# gap-013: Smart layout v2 — magazine-style composition (user-provided reference samples)

**Stage:** render | **Fault types:** `phone_readability`, `static_scene`, `irrelevant_panel` | **Priority:** 2

## Origin
User-directed (2026-09-07 round). Six reference images in
`improvements/experiments/exp-013-smart-layout-v2/reference_samples/` showing the target
aesthetic for multi-panel composition. This is the "iterate" follow-up recorded on adopted
gap-002 ("per-cell motion, text-aware sizing, 2-3 panel cap").

## What the samples demonstrate (vs our current uniform grid)
- **smart_layout1.webp** — asymmetric hero layout: one dominant full-height panel (right ~40%)
  next to a stacked column of smaller beats; varied cell sizes within a row.
- **smart_layout2.webp** — classic 2-row magazine grid, but cells sized by content: wide
  establishing panel + narrow reaction cells; consistent thin gutters, white background.
- **smart_layout3.webp** — full-width horizontal strips stacked vertically (4 rows) for
  dialogue pacing; tall-and-narrow phone-friendly aspect.
- **smart_layout4.jpeg** — dense bubble-heavy page: cells sized so text stays readable;
  reading-order flow preserved top→bottom.
- **smart_layout5.jpeg** — vertical hero panel with small inset/corner panels overlapping it
  (inset composition), strong action emphasis.
- **smart_layout6.jpg** — spread: large dominant scene panel + narrow full-height right rail
  panel (moment emphasis / payoff column).

## Candidate directions
1. **Content-driven cell weighting**: size cells by panel importance (hero beat gets 55-65%
   area) + text density (bubble-heavy panels get more area) instead of uniform grid split.
2. **Layout template library**: 6-10 magazine templates (hero-left, hero-right, rail,
   strips, inset) selected by panel count + aspect ratios + narration beat structure.
3. **Per-cell motion**: subtle Ken Burns inside the highlighted cell only, others static/dim.
4. **Text-aware sizing guard**: min bubble text height check per cell (ties into gap-009);
   fall back to fewer cells / seq when text would drop below readable size.

## Acceptance criteria
- ch3 evidence: 13 phone_readability faults (mostly smart-scene text cells too small) —
  smart-scene phone_readability MEDIUMs 3 → 0 on golden/ch3 scoped scenes.
- No regression on gap-002's win: smart-scene HIGH faults stay 0.
- Layouts visibly varied (≥3 distinct templates used on the golden chapter smart scenes).
- Render wall-time increase ≤1.3x vs current smart layout for affected scenes.

## Quality guards
- Reading order preserved; no cropping of bubbles at cell edges.
- Cells keeping text must render text ≥ readable threshold at 1080p phone view.
- Fallback chain: v2 template → v1 grid → seq cuts.
