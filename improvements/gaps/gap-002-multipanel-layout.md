# gap-002: Creative multi-panel layout compositor

**Stage:** render | **Fault types:** `irrelevant_panel`, `static_scene`, `weak_hook` | **Priority:** 2

## Problem
Renderer is single-panel-only (`panel_render.py:583`): multi-panel scenes become sequential
single-panel cuts, ping-ponging between the last two panels when narration outlasts panels
(`panel_render.py:807`). When narration describes several moments at once, no single panel
matches → reviewer flags irrelevant_panel; long quiet scenes read static.

## User's example (build-ourselves seed)
"If the narration describes multiple panels, try a creative layout of those panels which
would make the narration and view in best sync for the viewer."

## Candidate directions (build ourselves — no off-the-shelf tool expected)
1. **Stack reveal**: panels slide/fade in one-by-one into a vertical/horizontal stack,
   timed to narration beats (edge-tts word timings already available).
2. **Comic grid**: 2-4 panel grid over blurred bg; each cell highlights (scale+brightness)
   as its beat is narrated, others dim.
3. **Sequential wipe**: split-screen wipe between panels synced to sentence boundaries.
Trigger: scene lists 3+ panels AND pace != hype. Behind flag `--layout smart`.

## Acceptance criteria
- On golden chapter, scenes with 3+ panels: irrelevant_panel + static_scene faults drop ≥40%.
- No new fault types (esp. phone_readability — cells must keep panels ≥45% frame height,
  else fall back to sequential cuts).
- Render wall-time increase ≤1.5x for affected scenes.

## Quality guards
- Reading order preserved left→right / top→bottom of reveal.
- blur_bg fitting rules respected per cell; no cropping of bubbles.
