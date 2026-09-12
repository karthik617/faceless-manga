# gap-016: v1 smart-layout grid ffmpeg render fails at 1440p (silent sequential fallback)

**Stage:** render | **Fault types:** `static_scene`, `irrelevant_panel` (indirect — fallback loses the composite) | **Priority:** 12

## Problem
The adopted v1 smart layout (gap-002, default since 2026-09-07) builds its
grid composite canvas as a multiple of the output frame size. At 1440p output
(2560×1440) the scene-19 grid produced a 5088×2880 canvas; the ffmpeg filter
graph (overlays + drawbox highlights + zoompan → s=2560x1440, libx264) fails
on it, and panel_render.py's error fallback silently degrades the scene to
sequential cuts. The one golden-chapter smart-layout firing at 1440p is
therefore lost: the intended narration-synced composite (the gap-002 win
against static_scene/irrelevant_panel) never renders, and nobody notices
unless they read the render log. Pre-existing v1 bug — reproduced identically
in both arms of exp-013 (framemd5-identical output), so it is not a v2 issue.

## Evidence
- improvements/experiments/exp-013-smart-layout-v2/report.md: "v1 `grid`
  fallback attempted: 1 (scene 19) — its ffmpeg render then failed and fell
  to sequential, **identically in both arms** (pre-existing v1 bug, filter
  graph builds 5088×2880 canvas > x264 limits at 1440p)"; caveats: "v1's
  adopted smart layout silently loses its one golden-chapter firing at 1440p"
  — with the explicit request for a separate gap card.
- improvements/experiments/exp-013-smart-layout-v2/run/render_A.log:186:
  "scene 19: smart layout failed (RuntimeError: Command failed: ffmpeg …
  c018_smart_base.png … zoompan=…:s=2560x1440 … -c:v libx264 …), sequential
  render". work_A/c018_smart_base.png confirmed 5120×2880 on disk (the log's
  5088×2880 is the drawbox/overlay working extent). render_B.log shows the
  same single failure.
- pipeline/panel_render.py:1287 — the v2 planner "falls back to the v1
  grid/stack plan internally, and any error falls" through to sequential;
  the error path is the silent-degrade site.
- Frame evidence: run/samples s19_t2_A/B, s19_t15_*, s19_t28_* — "the only
  smart-layout attempt (v1 grid plan → ffmpeg failure → sequential in both
  arms)".

## Candidate directions
1. **Downscale the composite canvas** before the filter graph: cap the base
   canvas at a safe encoder-friendly size (e.g. ≤4096 on the long edge, even
   dimensions), scaling panel cells and overlay/drawbox coordinates
   proportionally — the final zoompan output is 2560×1440 anyway, so the
   oversized intermediate buys nothing above ~2x supersampling.
2. Split the pipeline: pre-compose the grid + highlight frames to PNG at the
   capped size (PIL side), feeding ffmpeg only frame-sized inputs.
3. At minimum, make the fallback LOUD: a failed smart render should raise the
   log level / write a marker the review or benchmark harness can surface,
   so silent composite loss cannot recur undetected.

## Reproduce
`improvements/experiments/exp-013-smart-layout-v2/run/render_A.log` line 186
has the full failing ffmpeg command (inputs still on disk under
`run/work_A/c018_smart_*.png`); re-running that exact command reproduces the
failure. Any 1440p render of the golden chapter that plans a grid on scene 19
hits it.

## Acceptance criteria
- The exp-013 repro case renders: re-running the golden-chapter scene 19 at
  1440p with `--layout smart` produces a grid composite clip (log line
  "smart layout (grid, …)" with NO subsequent "smart layout failed";
  c018.mp4 differs from the sequential-fallback clip by framemd5).
- Deterministic sweep: a test script drives the grid render path at 1080p,
  1440p, and 2160p with 2/3/4-panel synthetic inputs (including one very
  tall panel) — 0 ffmpeg failures, all outputs pass `ffprobe` decode.
- 1080p output on the golden chapter is byte-identical (framemd5) to today's
  — the fix must not perturb the resolution where the adopted layout already
  works.
- Scene-scoped benchmark (benchmark.py --scenes-changed on scene 19, stable
  review per gap-011): no new fault types on the restored composite scene.
- Render wall-time for the fixed scene ≤1.3x its sequential-fallback time.

## Quality guards
- Fallback chain stays intact (template → grid → seq): a genuinely
  un-renderable plan must still degrade gracefully, just not silently —
  failures logged at warning level with the ffmpeg stderr tail.
- Highlight boxes / reveal timings must stay pixel-aligned with cells after
  any canvas rescale (drawbox coordinates scale with the same factor).
- No text below readable size introduced by downscaling (ties into gap-009's
  min text-height floors).
