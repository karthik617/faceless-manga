# exp-002 — multi-panel layout compositor (prototype)

**Gap:** gap-002-multipanel-layout | **Status:** v2 iteration built, smoke test PASS
**Date:** 2026-09-06 (v1), 2026-09-06 (v2)

## v2 iteration (round-1 eval fixes)

Round 1 FAILed on two real regressions (report.md): **static_scene 4→12**
(deterministic phash scan) and **phone_readability 2→8** (grid cells shrink
in-panel text). v2 fixes both behind the same `--layout smart` flag; the
`seq` path is untouched.

### What changed and why

**1. static_scene — guided-view punch-ins (primary) + continuous motion.**
Measured against review_video's actual detector (180px phash every 2s,
≤6/64 differing bits = "identical", 12s threshold): a 1.0→1.06 zoom moves
≤4 bits, brightness/breathe ≤5 bits, and even a full grid-cell REVEAL stays
inside the identical band — round 1's composite faulted straight through its
own reveals. Only a structural view change resets the scan. So v2:

- **Punch-ins** — the composite periodically punches INTO the narrated
  panel fullscreen (a complete plate: blurred/darkened cover + panel at 94%
  frame height, the sequential-path look) and back to the grid. Schedule:
  opening grid `PUNCH_AFTER=6s`, punches of `PUNCH_LEN=6s`, grid interludes
  `GRID_LEN=5s`, each reveal guaranteed `REVEAL_GRID=1.6s` on-grid, no shot
  under `MIN_SHOT=2.5s`, punches never cross a reveal. A safety pass
  force-inserts a punch into any same-view span reaching `SPAN_MAX=10.5s`.
  Verified over 3000 randomized duration/beat schedules through the real
  render code path: worst same-view span 10.5s < 12s threshold.
  Side benefit: each punch shows the panel's text at near-native fullscreen
  size (phone-readability assist).
- **Continuous zoom** — zoompan rate is now `(1.06-1)/total_frames` so z
  climbs for the WHOLE scene (round 1's fixed 0.0004/frame rate capped at
  1.06 ~5s in and froze), plus a slow sinusoidal x-drift riding the zoom's
  pan headroom (cannot leave the plate).
- **Breathe** — bright cells glow on a 5s luminance cycle
  (`eq=brightness=0.05*sin`), and the final panel's accent border breathes
  full/half alpha (1.7s of every 3.4s) after the last reveal. These are
  "alive frame" polish; the punches are what clear the detector.

**2. phone_readability — 3-panel cap + text-aware cell guards.**

- Grids capped at `MAX_GRID=3` cells (1x3). The round-1 2x2 template is
  gone — its ≤50%-height cells could never hold phone-legible text.
- Scenes with 4+ panels composite a 3-panel **selection in reading order**:
  first / middle / last (setup / turn / payoff), recorded in
  `plan.picked` and logged by panel_render.
- **Text-aware height guards** via the OCR sidecar (`<slug>.ocr.json`):
  panels with `dialogue` or `narration_text` must land ≥ `GUARD_H_TEXT=55%`
  of frame height in their cell; pure-action panels may go to `GUARD_H=45%`.
  Guard fails → `plan_layout` returns None → seq fallback.
- `plan_layout` gained an optional `ocr=` parameter (`{panel_name: read}`
  dict or the raw sidecar list). panel_render loads the sidecar lazily once
  per run; **missing/broken file → all panels treated as text-bearing**
  (conservative).

### New guard thresholds

| guard | v1 | v2 |
|---|---|---|
| grid cells | 3 or 4 (2x2) | 3 only (1x3); 4+ panels → pick first/middle/last |
| cell height, text panel | 45% | **55%** (OCR-driven) |
| cell height, action panel | 45% | 45% |
| max same-view span | unbounded (froze) | 10.5s scheduled ceiling |

### Evaluator command (unchanged flags)

Same as v1 — see "Exact evaluator commands" below. `--layout smart` on
`panel_render.py`, fresh workdir, `--stop-after review-cut`, then
`review_video.py` on the review cut.

### v2 smoke results

`smoke.py` (updated: OCR wired in, panel-cap check, freezedetect +
2s-interval frame-difference static checks) → `samples/smoke_grid_v2.mp4`:

- plan: grid, beats `[0.00, 2.93, 6.86]`, picked `[0,1,2]`
- 4+-panel test: 6-panel scene 5 → selected `p0015/p0018/p0020` (first/middle/last)
- ffprobe: 1920x1080, 12.000s (±0.2 ✓), 360/360 packets
- `freezedetect=n=0.001:d=10`: **0** freeze_start events
- all 2s-interval frames differ (PIL ImageChops)
- 40s long-scene repro (round 1 faulted 3 spans): review_video's exact
  static_scan logic → **0 faults**, freezedetect → 0 events
- golden chapter offline plan scan: **5/25 scenes** trigger smart (2, 5, 10,
  19, 25); 20 fall back (10 hype-pace, 10 text-height guard)

## Files changed

| File | Change |
|---|---|
| `pipeline/layout_smart.py` | NEW — planner (`plan_layout`) + renderer (`render_smart_scene`). PIL pre-composites a dimmed base plate over the blurred-bg of the first panel; one ffmpeg pass does per-beat bright-panel overlays (`enable='gte(t,T)'` / `between`), a 0.35s slide-in x-expression, a vermilion `drawbox` accent on the active cell, and a gentle `zoompan` (d=1, `in`-driven) on the composite. Encodes with `mv._INTER_V`/`mv._INTER_A`, `apad` + `-t {dur:.3f}`, `-video_track_timescale 15360`. |
| `pipeline/panel_render.py` | `--layout {seq,smart}` argparse flag (default `seq`); smart branch in the scene loop after parallax, before the `n_cuts` paths. Guarded by `args.layout == "smart"` so `seq` never touches the new code. Any exception in the smart path falls through to the sequential render. |
| `manga.py` | `--layout {seq,smart}` flag (default `seq`), passed through in `_render_cmd` only when non-default — default invocations are byte-identical. |
| `improvements/experiments/exp-002-multipanel-layout/smoke.py` | NEW — test harness (see below). |

## Flag

`--layout smart` on `pipeline/panel_render.py` (or `manga.py`, forwarded).
Default `seq` = today's behavior bit-for-bit (the smart module is only
imported inside the `smart` branch).

## Trigger / fallback conditions (plan_layout returns None → existing path)

*(v1 documentation — superseded where the "v2 iteration" section above
differs: 3-cell cap, first/middle/last selection, 55%/45% text-aware guards.)*

Smart layout is attempted only when ALL hold; otherwise the scene renders
through the untouched sequential-cuts path:

- `len(panels) >= 3`
- `pace != "hype"` (explicit `pace` field, else emphasis→hype/focus→quiet
  inference — same rule as the cut-cadence block)
- `duration >= len(panels) * 1.2s` (each beat readable)
- a template fits the readability guard (every panel ≥45% frame height at
  its highlight moment):
  - **grid** — 3 panels → 1x3 columns, 4 panels → 2x2; panels fit-inside
    their cell (never cropped)
  - **stack** (accordion) — all panels wide strips (w/h ≥ 1.4); active panel
    pops to ~58% frame height, resting rows share the remainder
- panel images openable by PIL
- `--parallax` keeps priority (smart is skipped when parallax rendered)
- any runtime error in plan/render → logged, sequential fallback

Beat times snap to sentence boundaries from the scene's edge-tts word
timings (0.12s pre-roll, ≥1.2s per beat, merged/split to the panel count);
fallback to equal splits when timings are missing or the schedule doesn't fit.

## Exact evaluator commands

Re-render the golden chapter's review cut with the smart layout (fresh
workdir so cached seq clips don't mask the new path), then review it:

```bash
cd /home/kmyadav/faceless-manga
./venv/bin/python3 pipeline/panel_render.py \
    output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.json \
    --res 1080p --layout smart \
    --workdir output/the-world-after-the-fall-ch2/_work_smart \
    --stop-after review-cut
./venv/bin/python3 pipeline/review_video.py \
    "$(cat output/the-world-after-the-fall-ch2/_work_smart/review_src.txt)"
```

Baseline comparison (unchanged default) uses the same commands without
`--layout smart` and a separate workdir. Acceptance per the gap card:
irrelevant_panel + static_scene faults on 3+-panel non-hype scenes drop
≥40%, no new phone_readability faults, wall-time ≤1.5x on affected scenes.

## Smoke test

`./venv/bin/python3 improvements/experiments/exp-002-multipanel-layout/smoke.py`

Scene 2 of the golden chapter (3 panels, pace=quiet), synthetic 12s duration,
evenly-spaced fake word timings. Results:

- plan: `grid`, beats `[0.00, 2.93, 6.86]` (sentence-snapped)
- negative guards verified: hype pace / 2s duration / 2 panels all → None
- ffprobe: **1920x1080, 12.000s** (target 12.0 ±0.2 ✓), 360/360 frames decoded
- reveal progression frames extracted for human review

## Sample files (samples/)

- `smoke_grid.mp4` — 12s 1080p grid-reveal clip
- `smoke_grid_early.png` — t=0.6s: panel 1 bright + accent border, others dimmed
- `smoke_grid_mid.png` — t=3.5s: panel 2 revealed, accent moved
- `smoke_grid_late.png` — t=7.5s: all revealed, accent on panel 3
- `smoke_silence.mp3` — synthetic silent narration track (test input)

## Deviations from research.md

- Grid template ships first (research led with stack); the golden chapter's
  panels are tall webtoon crops (w/h ≈ 0.35), so the accordion stack's wide-
  strip precondition rarely fires there. Stack is implemented and guarded.
- Grid highlight uses accumulate-reveal (`gte`) + dimmed base plate instead of
  per-window `drawbox` dims over a lit plate — one plate variant, fewer
  filters, same visual (§2.3 noted both).
- The grid's final punch-in tail (xfade to fullscreen last panel) deferred:
  the settled composite + zoompan already carries tails; add if the reviewer
  flags monotony.
- Wipe template (research §5.3 C) not built, as planned ("v1 optional").
