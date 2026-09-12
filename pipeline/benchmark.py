#!/usr/bin/env python3
"""
benchmark.py — deterministic experiment scorer for the /improve-train loop.

Compares an experiment's review.json against the pinned baseline and applies
hard quality-non-degradation vetoes. LLM judgment happens once (inside
review_video.py); this file only does arithmetic on its output, so the trainer
never grades its own homework twice.

Usage:
  # after running the stage-scoped pipeline for an experiment:
  ./venv/bin/python3 pipeline/benchmark.py \
      --experiment improvements/experiments/exp-001 \
      --review output/the-world-after-the-fall-ch2/review.json \
      [--video output/.../slug.mp4]        # enables duration/loudness vetoes (full runs)
      [--config improvements/benchmark/config.json]

Exit codes: 0 = PASS (score improved, no vetoes), 1 = FAIL, 2 = setup error.
Writes <experiment>/metrics.json.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFPROBE = str(Path.home() / ".local/bin/ffprobe")
FFMPEG = str(Path.home() / ".local/bin/ffmpeg")


def load_faults(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):  # tolerate future schema {faults: [...]}
        data = data.get("faults", [])
    return [f for f in data if isinstance(f, dict) and f.get("type")]


def weighted_score(faults: list[dict], weights: dict) -> int:
    return sum(weights.get(f.get("severity", "low"), 1) for f in faults)


def sev_counts(faults: list[dict]) -> dict:
    c = Counter(f.get("severity", "low") for f in faults)
    return {"high": c["high"], "medium": c["medium"], "low": c["low"]}


def type_counts(faults: list[dict]) -> dict:
    return dict(Counter(f["type"] for f in faults))


def video_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# gap-015: matched-veto helpers (opt-in via --matched-vetoes; the default veto
# arithmetic below is untouched). WHY: per-type COUNT deltas between arms sit
# below the reviewer noise floor (gap-011) — marginal (scene,type) items churn
# on both sides and repeatedly veto experiments on relabels of pixel-identical
# frames (exp-004 addendum, exp-009 v3/v4). These helpers (a) re-threshold
# both arms to a common consensus fraction when pass counts differ, (b) key
# the veto on matched (scene,type) PAIRS so a fault that merely moves scenes
# does not read as a count increase, and (c) exempt count-increases whose
# evidence frame is pixel-identical to a baseline frame (decoded framemd5,
# NOT file md5 — container bytes differ on identical pixels, exp-004 sc28).
# Severity weights and score arithmetic are NOT touched — only which
# count-deltas are eligible to veto. Every suppression is annotated in
# metrics.json under "annotated_vetoes"; nothing is silent.
# ---------------------------------------------------------------------------

def parse_votes(f: dict) -> tuple[int, int]:
    """'3/5' -> (3,5); missing/unparseable -> (1,1) (single-pass review)."""
    v = f.get("votes")
    try:
        k, n = str(v).split("/")
        return int(k), int(n)
    except Exception:
        return 1, 1


def arm_pass_count(faults: list[dict]) -> int:
    return max((parse_votes(f)[1] for f in faults), default=1)


def consensus_filter(faults: list[dict], frac: float) -> tuple[list[dict], list[dict]]:
    """Split into (kept, dropped) at a fixed consensus fraction. WHY: 3/5 is
    more permissive than 2/3, so asymmetric pass counts alone flip verdicts
    (exp-004 addendum) — both arms must clear the SAME bar before comparing."""
    kept, dropped = [], []
    for f in faults:
        k, n = parse_votes(f)
        (kept if k / n >= frac - 1e-9 else dropped).append(f)
    return kept, dropped


def pair_map(faults: list[dict]) -> dict:
    """(scene,type) -> representative fault. Same identity key as
    review_stable.py's majority vote — detail text is reviewer-reworded."""
    m = {}
    for f in faults:
        m.setdefault((f.get("scene"), f.get("type")), f)
    return m


_FRAMEMD5_CACHE: dict = {}


def frame_md5_at(video: Path, t: float) -> str | None:
    """framemd5 of the single decoded frame at t (video stream 0)."""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
             "-map", "0:v:0", "-frames:v", "1", "-f", "framemd5", "-"],
            capture_output=True, text=True, timeout=120)
        for line in out.stdout.splitlines():
            if line and not line.startswith("#"):
                return line.split(",")[-1].strip()
    except Exception:
        pass
    return None


def frame_md5_window(video: Path, t0: float, t1: float) -> set[str]:
    """Set of decoded-frame md5s in [t0,t1]. Cached per (video, window)."""
    key = (str(video), round(t0, 2), round(t1, 2))
    if key in _FRAMEMD5_CACHE:
        return _FRAMEMD5_CACHE[key]
    md5s: set[str] = set()
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{t0:.3f}", "-i", str(video),
             "-map", "0:v:0", "-t", f"{t1 - t0:.3f}", "-f", "framemd5", "-"],
            capture_output=True, text=True, timeout=300)
        for line in out.stdout.splitlines():
            if line and not line.startswith("#"):
                md5s.add(line.split(",")[-1].strip())
    except Exception:
        pass
    _FRAMEMD5_CACHE[key] = md5s
    return md5s


# -- hash sidecars (exp-015 fixture hardening) ------------------------------
# WHY: the 0.5 GB bench videos were never the real dependency of the frame-
# hash exemption — only (a) the single decoded-frame md5 at each fault t in
# the experiment arm and (b) the set of decoded-frame md5s in t±window of the
# baseline arm. A few-KB JSON sidecar carrying exactly those values makes
# exemption decisions bit-for-bit reproducible after the videos are evicted
# (the exp-004 /tmp bench renders were lost 2026-09-12 and are unrecoverable:
# LLM script + TTS + word timings were never archived). Sidecars only feed
# the --matched-vetoes hash-exemption leg; nothing else reads them.

SIDECAR_SCHEMA = "benchmark-hash-sidecar/v1"


def load_hash_sidecar(path: str, window: float) -> dict | None:
    """Validate + load a hash sidecar. Returns None (with a loud stderr note)
    on any problem — a bad sidecar must degrade to hashing-off, exactly like
    a missing video, never crash or silently alter decisions."""
    try:
        sc = json.loads(Path(path).read_text())
        if sc.get("schema") != SIDECAR_SCHEMA:
            raise ValueError(f"unexpected schema {sc.get('schema')!r}")
        # The baseline window md5 sets are baked at export time; using them at
        # a different --hash-window would change decisions invisibly.
        if abs(float(sc["hash_window_s"]) - window) > 1e-9:
            raise ValueError(f"sidecar window {sc['hash_window_s']} != "
                             f"--hash-window {window}")
        assert isinstance(sc.get("frames"), dict)
        assert isinstance(sc.get("baseline_windows"), dict)
        return sc
    except Exception as e:
        print(f"[benchmark] hash sidecar {path} unusable ({e!r}); "
              f"frame-hash exemption disabled for sidecar path", file=sys.stderr)
        return None


def sidecar_key(t) -> str:
    """Canonical timestamp key. Fault t values are floats (review.json);
    fixed %.3f formatting makes export and lookup collision-proof."""
    return f"{float(t):.3f}"


def export_hash_sidecar(out: Path, faults: list[dict], video: Path,
                        baseline_video: Path, window: float) -> dict:
    """Export a sidecar covering every fault timestamp in `faults` (the FULL
    experiment review, pre --scenes-changed scoping, so one sidecar serves
    any scoping variant). Values are computed by the very same frame_md5_at /
    frame_md5_window helpers the live path uses — bit-for-bit equivalence by
    construction."""
    frames: dict[str, str] = {}
    windows: dict[str, list[str]] = {}
    for f in faults:
        t = f.get("t")
        if t is None:
            continue
        key = sidecar_key(t)
        if key in windows:
            continue
        md5 = frame_md5_at(video, float(t))
        if md5:
            frames[key] = md5
        t0 = max(0.0, float(t) - window)
        windows[key] = sorted(frame_md5_window(baseline_video, t0,
                                               float(t) + window))
    payload = {
        "schema": SIDECAR_SCHEMA,
        "hash_window_s": window,
        "video": str(video),
        "baseline_video": str(baseline_video),
        "provenance": "exported from live videos by benchmark.py "
                      "--write-hash-sidecar",
        "frames": frames,
        "baseline_windows": windows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def matched_vetoes(base: list[dict], cur: list[dict], vetoes_cfg: dict,
                   video: str | None, baseline_video: str | None,
                   window: float, sidecar: dict | None = None
                   ) -> tuple[list[str], dict]:
    """Matched (scene,type) veto arithmetic. Returns (vetoes, annotations).

    A type-count increase vetoes only if, after (1) fixed-consensus
    normalization, (2) matched-pair netting against removed pairs of the same
    type, and (3) frame-hash exemption of added pairs whose evidence frame is
    pixel-identical to baseline, the net increase is still > 0. A genuinely
    new fault on changed pixels always counts fully — the veto layer stays a
    HARD gate for real regressions (quality guard)."""
    n_base, n_cur = arm_pass_count(base), arm_pass_count(cur)
    # Fixed consensus = the STRICTER of the two arms' native majority
    # fractions (ceil(N/2)/N). Symmetric arms are unchanged; a 3-pass vs
    # 5-pass comparison renormalizes the 5-pass side to >=4/5 instead of
    # refusing outright (gap-015 candidate direction 3).
    frac = max(math.ceil(n_base / 2) / n_base, math.ceil(n_cur / 2) / n_cur)
    base_kept, base_dropped = consensus_filter(base, frac)
    cur_kept, cur_dropped = consensus_filter(cur, frac)

    bmap, cmap = pair_map(base_kept), pair_map(cur_kept)
    # Precedence: live videos win over a sidecar (exp-015). WHY: the videos
    # are ground truth — a sidecar is an archived projection of them, so when
    # both are supplied the fresher source decides and any stale sidecar can
    # never mask a real pixel change. Sidecar is the fallback for evicted
    # fixtures; neither present -> hashing off, netting-only (unchanged).
    videos_on = bool(video and baseline_video
                     and Path(video).exists() and Path(baseline_video).exists())
    sidecar_on = (not videos_on) and sidecar is not None
    hashes_on = videos_on or sidecar_on

    def hash_exempt(f: dict) -> dict | None:
        """Frame-hash proof that the fault's evidence frame is pixel-identical
        to a baseline frame near the same timestamp (window absorbs concat /
        Ken Burns sub-second phase offsets, exp-009 v3)."""
        if not hashes_on:
            return None
        t = f.get("t")
        if t is None:
            return None
        if videos_on:
            md5 = frame_md5_at(Path(video), float(t))
            if not md5:
                return None
            t0 = max(0.0, float(t) - window)
            base_md5s = frame_md5_window(Path(baseline_video), t0,
                                         float(t) + window)
        else:
            # Sidecar lookup mirrors the video path exactly: same md5 values
            # (exported by the same helpers), same window membership test, so
            # the exemption decision is bit-for-bit identical.
            key = sidecar_key(t)
            md5 = sidecar["frames"].get(key)
            if not md5:
                return None
            t0 = max(0.0, float(t) - window)
            base_md5s = set(sidecar["baseline_windows"].get(key, []))
        if md5 in base_md5s:
            return {"frame_md5": md5, "t": t,
                    "baseline_window": [round(t0, 2), round(float(t) + window, 2)]}
        return None

    by_type_added: dict[str, list] = {}
    by_type_removed: dict[str, list] = {}
    for key in cmap:
        if key not in bmap:
            by_type_added.setdefault(key[1], []).append(key)
    for key in bmap:
        if key not in cmap:
            by_type_removed.setdefault(key[1], []).append(key)

    base_counts = Counter(k[1] for k in bmap)
    cur_counts = Counter(k[1] for k in cmap)

    vetoes: list[str] = []
    annotated: list[dict] = []
    worsened: dict[str, tuple] = {}
    surviving_new_types: list[str] = []

    types = sorted(set(base_counts) | set(cur_counts))
    for typ in types:
        if typ == "review_error":  # reviewer infrastructure, same exemption
            continue               # as the default vetoes
        b, c = base_counts.get(typ, 0), cur_counts.get(typ, 0)
        added = by_type_added.get(typ, [])
        removed = by_type_removed.get(typ, [])
        if c <= b:
            continue
        pairs_added, n_exempt = [], 0
        for (scene, _t) in sorted(added, key=str):
            f = cmap[(scene, typ)]
            proof = hash_exempt(f)
            if proof:
                n_exempt += 1
            pairs_added.append({"scene": scene, "t": f.get("t"),
                                "votes": f.get("votes"),
                                "severity": f.get("severity"),
                                "frame_hash_exempt": bool(proof),
                                "frame_hash_proof": proof})
        net = c - b - n_exempt
        record = {
            "type": typ,
            "count": {"baseline": b, "experiment": c},
            "pairs_added": pairs_added,
            "pairs_removed": [{"scene": s, "t": bmap[(s, typ)].get("t"),
                               "votes": bmap[(s, typ)].get("votes")}
                              for (s, _t2) in sorted(removed, key=str)],
            "net_after_exemptions": net,
        }
        if net > 0:
            worsened[typ] = (b, c)
            if typ not in base_counts and vetoes_cfg.get("new_fault_types"):
                surviving_new_types.append(typ)
            record["suppressed"] = False
            annotated.append(record)
        else:
            record["suppressed"] = True
            record["reason"] = ("frame-hash exemption + matched-pair netting"
                                if n_exempt else "matched-pair netting "
                                "(scene churn absorbed by removed pairs)")
            annotated.append(record)
        # New-type veto suppression is annotated through the same record: a
        # type absent from baseline whose every added pair is exempt never
        # reaches surviving_new_types.

    if surviving_new_types:
        vetoes.append(f"new fault types introduced: {sorted(surviving_new_types)}")
    if worsened:
        vetoes.append(f"fault types worsened (base→now, matched-pair net): {worsened}")

    meta = {
        "pass_counts": {"baseline": n_base, "experiment": n_cur},
        "consensus_fraction": round(frac, 4),
        "consensus_dropped": {
            "baseline": [{"scene": f.get("scene"), "type": f.get("type"),
                          "votes": f.get("votes")} for f in base_dropped],
            "experiment": [{"scene": f.get("scene"), "type": f.get("type"),
                            "votes": f.get("votes")} for f in cur_dropped],
        },
        "frame_hash": {"enabled": hashes_on, "window_s": window,
                       "video": video, "baseline_video": baseline_video},
        "annotated_vetoes": annotated,
    }
    if sidecar_on:
        # Only present on the sidecar path — video-fed metrics.json stays
        # byte-identical to pre-sidecar output.
        meta["frame_hash"]["source"] = "sidecar"
        meta["frame_hash"]["sidecar_provenance"] = sidecar.get("provenance")
        meta["frame_hash"]["sidecar_original_video"] = sidecar.get("video")
        meta["frame_hash"]["sidecar_original_baseline_video"] = \
            sidecar.get("baseline_video")
    return vetoes, meta


def video_loudness(path: Path) -> float | None:
    """Integrated LUFS via ffmpeg loudnorm print_format=json (analysis pass)."""
    try:
        out = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, timeout=600)
        tail = out.stderr[out.stderr.rfind("{"):out.stderr.rfind("}") + 1]
        return float(json.loads(tail)["input_i"])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment", required=True,
                    help="improvements/experiments/<exp-id> dir (metrics.json written here)")
    ap.add_argument("--review", required=True,
                    help="experiment's review.json (from review_video.py)")
    ap.add_argument("--baseline", default=None,
                    help="override baseline review.json (default: from config)")
    ap.add_argument("--config", default=str(ROOT / "improvements/benchmark/config.json"))
    ap.add_argument("--video", default=None,
                    help="final mp4 — enables duration/loudness vetoes (use on --full runs)")
    ap.add_argument("--baseline-video", default=None,
                    help="baseline mp4 for duration-drift comparison")
    ap.add_argument("--extra-metrics", default=None,
                    help="JSON file of stage-specific metrics to merge into the report "
                         "(e.g. OCR agreement, seam flux) — informational, not veto")
    ap.add_argument("--scenes-changed", default=None,
                    help="comma-separated scene indices the experiment actually "
                         "touched (e.g. '2,6,12'). Scoring and vetoes are then "
                         "restricted to those scenes — reviewer noise on "
                         "untouched, byte-identical scenes must not decide the "
                         "verdict (gap-011).")
    ap.add_argument("--matched-vetoes", action="store_true",
                    help="gap-015 (experimental, opt-in): compute the two "
                         "type-count vetoes over matched (scene,type) pairs at "
                         "a fixed consensus level, with frame-hash exemption "
                         "for count-increases whose evidence frame is pixel-"
                         "identical to baseline. Score arithmetic unchanged; "
                         "suppressions annotated under 'annotated_vetoes'.")
    ap.add_argument("--hash-video", default=None,
                    help="experiment video used ONLY for --matched-vetoes "
                         "frame hashing (does not enable media vetoes like "
                         "--video does). Defaults to --video when set.")
    ap.add_argument("--hash-baseline-video", default=None,
                    help="baseline video for --matched-vetoes frame hashing. "
                         "Defaults to --baseline-video when set.")
    ap.add_argument("--hash-window", type=float, default=3.0,
                    help="+/- seconds around a fault timestamp to search the "
                         "baseline for a pixel-identical frame (absorbs concat"
                         "/Ken Burns sub-second phase offsets). Default 3.0.")
    ap.add_argument("--hash-sidecar", default=None,
                    help="exp-015: framemd5 sidecar JSON (schema "
                         "benchmark-hash-sidecar/v1) standing in for BOTH "
                         "hash videos in the --matched-vetoes frame-hash "
                         "exemption. Used only when the live hash videos are "
                         "absent (live videos take precedence). Affects "
                         "nothing outside the hash-exemption leg.")
    ap.add_argument("--write-hash-sidecar", default=None, metavar="OUT",
                    help="generator mode: export a hash sidecar for the "
                         "given --review/--hash-video/--hash-baseline-video/"
                         "--hash-window and exit (no scoring). Archive the "
                         "sidecar next to the review pair so the hash "
                         "exemption stays replayable after video eviction.")
    args = ap.parse_args()

    if args.write_hash_sidecar:
        # Generator mode (exp-015): no scoring, just export the framemd5
        # sidecar for this review pair while the videos still exist.
        hv = args.hash_video or args.video
        hb = args.hash_baseline_video or args.baseline_video
        if not (hv and hb and Path(hv).exists() and Path(hb).exists()):
            print("--write-hash-sidecar needs existing --hash-video and "
                  "--hash-baseline-video (or --video/--baseline-video)",
                  file=sys.stderr)
            return 2
        rp = Path(args.review)
        if not rp.exists():
            print(f"missing input: {rp}", file=sys.stderr)
            return 2
        faults = load_faults(rp)
        out = Path(args.write_hash_sidecar)
        payload = export_hash_sidecar(out, faults, Path(hv), Path(hb),
                                      args.hash_window)
        print(f"[benchmark] wrote hash sidecar {out} "
              f"({len(payload['frames'])} frame hashes, "
              f"{len(payload['baseline_windows'])} baseline windows)")
        return 0

    cfg = json.loads(Path(args.config).read_text())
    weights = cfg["weights"]
    vetoes_cfg = cfg["hard_vetoes"]

    exp_dir = Path(args.experiment)
    exp_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = Path(args.baseline or ROOT / cfg["baseline_review"])
    review_path = Path(args.review)
    if not baseline_path.exists() or not review_path.exists():
        print(f"missing input: {baseline_path if not baseline_path.exists() else review_path}",
              file=sys.stderr)
        return 2

    base = load_faults(baseline_path)
    cur = load_faults(review_path)

    scenes_changed = None
    if args.scenes_changed:
        scenes_changed = {int(s) for s in args.scenes_changed.split(",") if s.strip()}
        base = [f for f in base if f.get("scene") in scenes_changed]
        cur = [f for f in cur if f.get("scene") in scenes_changed]

    base_score = weighted_score(base, weights)
    cur_score = weighted_score(cur, weights)
    base_types = type_counts(base)
    cur_types = type_counts(cur)

    vetoes: list[str] = []
    matched_meta = None

    use_default_vetoes = not args.matched_vetoes
    if args.matched_vetoes:
        # gap-015: matched (scene,type) pair veto arithmetic replaces vetoes
        # 1-2 only. Score, severity weights, and media vetoes are untouched.
        try:
            hash_video = args.hash_video or args.video
            hash_base_video = args.hash_baseline_video or args.baseline_video
            sidecar = (load_hash_sidecar(args.hash_sidecar, args.hash_window)
                       if args.hash_sidecar else None)
            mv, matched_meta = matched_vetoes(base, cur, vetoes_cfg,
                                              hash_video, hash_base_video,
                                              args.hash_window, sidecar)
            vetoes.extend(mv)
        except Exception as e:  # never crash the scorer: fall back to the
            # default (stricter) veto arithmetic and say so loudly.
            print(f"[benchmark] matched-vetoes failed ({e!r}); falling back "
                  f"to default veto arithmetic", file=sys.stderr)
            matched_meta = {"error": repr(e), "fallback": "default_vetoes"}
            use_default_vetoes = True
    if use_default_vetoes:
        # Veto 1: new fault types (quality must not degrade in NEW ways)
        if vetoes_cfg.get("new_fault_types"):
            new_types = sorted(set(cur_types) - set(base_types) - {"review_error"})
            if new_types:
                vetoes.append(f"new fault types introduced: {new_types}")

        # Veto 2: any fault type got worse. review_error is exempt here for the
        # same reason it is exempt from Veto 1: it marks a reviewer-infrastructure
        # failure (e.g. a JSON parse flake), not a defect in the video under test.
        worsened = {t: (base_types.get(t, 0), n) for t, n in cur_types.items()
                    if n > base_types.get(t, 0) and t != "review_error"}
        if worsened:
            vetoes.append(f"fault types worsened (base→now): {worsened}")

    # Veto 3/4: media sanity (only when a video is provided — i.e. --full runs)
    dur = lufs = base_dur = None
    if args.video:
        v = Path(args.video)
        dur = video_duration(v)
        if vetoes_cfg.get("encode_must_succeed") and (dur is None or dur < 30):
            vetoes.append(f"encode sanity failed: duration={dur}")
        if args.baseline_video:
            base_dur = video_duration(Path(args.baseline_video))
            if dur and base_dur:
                drift = abs(dur - base_dur) / base_dur * 100
                if drift > vetoes_cfg.get("max_duration_drift_pct", 5):
                    vetoes.append(f"duration drift {drift:.1f}% > "
                                  f"{vetoes_cfg['max_duration_drift_pct']}%")
        lufs = video_loudness(v)
        lo, hi = vetoes_cfg.get("loudness_lufs_range", [-17, -11])
        if lufs is not None and not (lo <= lufs <= hi):
            vetoes.append(f"loudness {lufs:.1f} LUFS outside [{lo},{hi}]")

    improved = cur_score < base_score
    passed = improved and not vetoes

    extra = {}
    if args.extra_metrics and Path(args.extra_metrics).exists():
        extra = json.loads(Path(args.extra_metrics).read_text())

    report = {
        "verdict": "PASS" if passed else "FAIL",
        "scenes_changed": sorted(scenes_changed) if scenes_changed else None,
        "score": {"baseline": base_score, "experiment": cur_score,
                  "delta": cur_score - base_score,
                  "weights": weights},
        "severity_counts": {"baseline": sev_counts(base), "experiment": sev_counts(cur)},
        "fault_types": {"baseline": base_types, "experiment": cur_types},
        "vetoes": vetoes,
        "media": {"duration_s": dur, "baseline_duration_s": base_dur,
                  "loudness_lufs": lufs} if args.video else None,
        "extra_metrics": extra,
        "inputs": {"baseline": str(baseline_path), "review": str(review_path),
                   "video": args.video},
    }
    if matched_meta is not None:
        # Only present when --matched-vetoes is on: keeps the default
        # metrics.json byte-identical to pre-gap-015 output.
        report["matched_vetoes"] = matched_meta
    out = exp_dir / "metrics.json"
    out.write_text(json.dumps(report, indent=2))

    print(f"[benchmark] {report['verdict']}  score {base_score} -> {cur_score} "
          f"(delta {cur_score - base_score:+d})")
    for v in vetoes:
        print(f"[benchmark] VETO: {v}")
    print(f"[benchmark] wrote {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
