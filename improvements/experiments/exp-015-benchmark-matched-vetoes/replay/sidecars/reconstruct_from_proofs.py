#!/usr/bin/env python3
"""
reconstruct_from_proofs.py — one-off (auditable) builder of the C2/C5 hash
sidecars from the archived original 15/15 metrics
(replay/archived_pass_2026-09-11/), because the /tmp exp-004 bench videos
were evicted and are unrecoverable (LLM script + TTS + word timings lost —
see IMPLEMENTATION.md "Fixture restoration 2026-09-12").

WHY this is faithful: the sidecar consumer (benchmark.py hash_exempt) only
asks two questions per added pair — "what is the experiment frame md5 at t?"
and "is that md5 in the baseline t±window set?". The archived metrics record
exactly those answers per pair as frame_hash_proof (md5 + window) for exempt
pairs; for non-exempt pairs the md5 was never recorded, so the sidecar simply
carries NO frames[t] entry — benchmark.py then returns not-exempt, the same
decision the live video produced (and the conservative direction: absence of
proof can never manufacture an exemption). The fault lists are frozen JSON,
so the set of (t, decision) queries is fixed forever — decision-for-decision
identical replay.

Deterministic: re-running overwrites with identical bytes.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCH = HERE.parent / "archived_pass_2026-09-11"
PROVENANCE = ("reconstructed from archived frame_hash_proof entries in "
              "replay/archived_pass_2026-09-11 (original 15/15 run of "
              "2026-09-11 17:48); source /tmp exp-004 bench videos evicted "
              "and unreproducible. Exempt pairs carry their recorded md5s; "
              "non-exempt pairs intentionally have no frames[] entry "
              "(=> not exempt, same decision as the live run).")


def build(tag: str, archived_metrics: Path, out: Path):
    m = json.loads(archived_metrics.read_text())
    fh = m["matched_vetoes"]["frame_hash"]
    window = fh["window_s"]
    frames: dict[str, str] = {}
    windows: dict[str, list[str]] = {}
    for rec in m["matched_vetoes"]["annotated_vetoes"]:
        for p in rec["pairs_added"]:
            t = p.get("t")
            if t is None:
                continue
            key = f"{float(t):.3f}"
            proof = p.get("frame_hash_proof")
            if p.get("frame_hash_exempt") and proof:
                frames[key] = proof["frame_md5"]
                # Membership is the only thing tested; the singleton set
                # {md5} reproduces the archived True decision exactly.
                windows.setdefault(key, [])
                if proof["frame_md5"] not in windows[key]:
                    windows[key].append(proof["frame_md5"])
            else:
                # Non-exempt: no frames entry -> hash_exempt returns None,
                # identical to the archived False decision.
                windows.setdefault(key, [])
    payload = {
        "schema": "benchmark-hash-sidecar/v1",
        "hash_window_s": window,
        "video": fh["video"],
        "baseline_video": fh["baseline_video"],
        "provenance": PROVENANCE,
        "frames": frames,
        "baseline_windows": {k: sorted(v) for k, v in windows.items()},
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"wrote {out} ({len(frames)} exempt frames, "
          f"{len(windows)} timestamps)")


if __name__ == "__main__":
    build("c2", ARCH / "c2_exp004_5pass/metrics.json",
          HERE / "c2_seg_v2_vs_v1_5pass.hash_sidecar.json")
    build("c5", ARCH / "c5_same_video/metrics.json",
          HERE / "c5_seg_v2_selfpair.hash_sidecar.json")
