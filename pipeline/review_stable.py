#!/usr/bin/env python3
"""
review_stable.py — variance-controlled wrapper around review_video.py.

WHY: the vision reviewer's fault list swings by ±12 on byte-identical video
(round-1 eval, gap-011), so a single pass cannot certify improvements whose
true effect is smaller than that noise floor. This wrapper runs N independent
review passes and majority-votes: a fault is SCORED only if the same
(scene, type) pair appears in >= ceil(N/2) passes. review_video.py itself is
never modified — the measuring stick's definitions stay put; we just read it
more than once.

HIGH-severity faults seen only once are NOT silently dropped: they are kept in
the .md as "unconfirmed" annotations (quality guard from the gap card — a real
one-off catch must stay visible to humans), they just don't enter the scored
.json that benchmark.py consumes.

Usage (same core args as review_video.py):
  ./venv/bin/python3 pipeline/review_stable.py <script.json> --video <mp4> \
      [--workdir DIR] [--passes 3] [--jobs 4] [--out output/<slug>/review]

Writes <out>.json (majority faults) and <out>.md; per-pass raw results are
kept next to them as <out>.pass{k}.json for auditing.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

PIPE = Path(__file__).resolve().parent
PY = sys.executable


def run_pass(args, k: int, out_base: str) -> list[dict]:
    cmd = [PY, str(PIPE / "review_video.py"), args.script,
           "--out", f"{out_base}.pass{k}",
           "--jobs", str(args.jobs),
           "--frames-per-scene", str(args.frames_per_scene)]
    if args.video:
        cmd += ["--video", args.video]
    if args.workdir:
        cmd += ["--workdir", args.workdir]
    # review_video exits 2 when high faults exist — that's data, not an error.
    subprocess.run(cmd, check=False)
    p = Path(f"{out_base}.pass{k}.json")
    if not p.exists():
        sys.exit(f"pass {k} produced no {p}")
    data = json.loads(p.read_text())
    return data if isinstance(data, list) else data.get("faults", [])


def majority_vote(passes: list[list[dict]]) -> tuple[list[dict], list[dict]]:
    """Return (scored, unconfirmed_high). Key = (scene, type): the reviewer
    rewords `detail` freely between runs, so text can't be part of identity."""
    need = math.ceil(len(passes) / 2)
    seen: dict[tuple, list[dict]] = defaultdict(list)
    for faults in passes:
        # de-dupe within a pass: same (scene,type) counted once per pass
        per_pass = {}
        for f in faults:
            key = (f.get("scene"), f.get("type"))
            per_pass.setdefault(key, f)
        for key, f in per_pass.items():
            seen[key].append(f)

    scored, unconfirmed = [], []
    for key, hits in sorted(seen.items(), key=lambda kv: (kv[1][0].get("t", 0),
                                                          str(kv[0]))):
        rep = dict(hits[0])
        rep["votes"] = f"{len(hits)}/{len(passes)}"
        if len(hits) >= need:
            scored.append(rep)
        elif rep.get("severity") == "high":
            unconfirmed.append(rep)
    return scored, unconfirmed


def write_md(out_base: str, scored: list[dict], unconfirmed: list[dict],
             n_passes: int, video: str | None):
    def line(f):
        m, s = divmod(int(f.get("t", 0)), 60)
        return (f"- [{f.get('severity','?').upper()}] {m}:{s:02d} "
                f"scene {f.get('scene')} {f.get('type')} "
                f"(votes {f.get('votes')}): {f.get('detail','')}")
    counts = defaultdict(int)
    for f in scored:
        counts[f.get("severity", "low")] += 1
    lines = [f"# Video review (stable, {n_passes} passes) — "
             f"{Path(video).name if video else '?'}",
             f"{len(scored)} confirmed fault(s): {counts['high']} high, "
             f"{counts['medium']} medium", ""]
    lines += [line(f) for f in scored]
    if unconfirmed:
        lines += ["", "## Unconfirmed HIGH (single-pass sightings — human eyes "
                      "recommended, not scored)", ""]
        lines += [line(f) for f in unconfirmed]
    Path(f"{out_base}.md").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script")
    ap.add_argument("--video", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--frames-per-scene", type=int, default=4)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_base = args.out or str(Path(args.script).parent / "review")
    passes = [run_pass(args, k, out_base) for k in range(1, args.passes + 1)]
    scored, unconfirmed = majority_vote(passes)

    Path(f"{out_base}.json").write_text(json.dumps(scored, indent=2))
    write_md(out_base, scored, unconfirmed, args.passes, args.video)
    counts = defaultdict(int)
    for f in scored:
        counts[f.get("severity", "low")] += 1
    print(f"[review_stable] {len(scored)} confirmed "
          f"({counts['high']}h/{counts['medium']}m/{counts['low']}l), "
          f"{len(unconfirmed)} unconfirmed-high -> {out_base}.json/.md")
    return 2 if counts["high"] else 0


if __name__ == "__main__":
    sys.exit(main())
