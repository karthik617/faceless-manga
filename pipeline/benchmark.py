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
    args = ap.parse_args()

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
