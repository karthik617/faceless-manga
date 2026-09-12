# exp-013 research — smart layout v2: magazine-style content-weighted composition

Gap: `improvements/gaps/gap-013-smart-layout-v2.md` (user-directed, 6 reference
samples in `reference_samples/`). Builds on adopted gap-002 (v1 `layout_smart.py`)
and exp-009 (text boxes + MIN_TEXT_H_1080=40 readability constant, already wired
into `_panel_min_h` at `layout_smart.py:192`).

Current v1 weaknesses this must fix:
- **Uniform cells** — `_grid_cells` (layout_smart.py:241) hard-splits into three
  equal columns; `_stack_cells` (269) into equal rows. Text-heavy panels get the
  same area as splash panels → 13 ch3 phone_readability faults, 3 MEDIUM on smart
  scenes.
- **Two templates only** (grid | stack) → repetitive look vs the reference samples.
- **No per-cell motion** — bright overlays are static; life comes only from
  whole-composite zoompan + punch-ins.

---

## 1. What the 6 reference samples encode (design targets)

| Sample | Structure observed | Template it seeds |
|---|---|---|
| smart_layout1.webp | Big top-left action panel + right full-height dominant column (~35-40% W); bottom-left strip of 4 small reaction/insert cells; varied sizes in one row | **hero-rail** (dominant column + weighted sub-grid) |
| smart_layout2.webp | 2 rows × 2-3 cells; row 1: wide dialogue cell (~65% W) + narrow establishing; row 2: narrow reaction + wide dialogue + medium; thin uniform gutters, white bg | **mag-grid** (rows with per-cell width weights) |
| smart_layout3.webp | 4 full-width horizontal strips stacked; strip height varies with content (dialogue strips taller) | **strips** (v1 stack, but weighted heights) |
| smart_layout4.jpeg | Dense bubble-heavy page, 4 rows; cells sized so bubbles stay readable; top→bottom flow | **strips/mag-grid with text-density weighting** (the sizing rule, not a new shape) |
| smart_layout5.jpeg | Full-bleed vertical hero (crowd action) + narrow left rail carrying the dialogue insets top and bottom | **inset** (hero fills frame, small panels overlap/rail) |
| smart_layout6.jpg | Spread: one large scene panel (~78% W) + narrow full-height right rail (payoff beat) | **hero-left + rail** (moment-emphasis column) |

Cross-cutting rules the samples share: (a) exactly ONE dominant element per
composition (55-80% of area); (b) cell area tracks text density — bubble-heavy
cells are never the smallest; (c) reading order strictly top→bottom / row-major;
(d) thin consistent gutters; (e) small cells are always *low-text* reaction beats.

Note: samples are portrait manga pages; our frame is 1920×1080 landscape. The
templates must be re-proportioned for 16:9 (hero columns work naturally; 4-strip
stacks at 1080 h give 255 px rows — only legal for text-free strips, so strips
caps at 3 rows unless all panels are text-free).

---

## 2. Candidate matrix

| # | Candidate | What it gives us | Deps / license | CPU | Integration effort into layout_smart.py | Fit (1-5) | Risk |
|---|-----------|------------------|----------------|-----|------------------------------------------|-----------|------|
| A | **Build-ourselves template library v2** — 5-6 named magazine templates (from §1) + content-weighted row/column splits + per-cell Ken Burns; pure extension of `plan_layout` | Exactly the reference aesthetic; every guard (`_panel_min_h`, no-crop, reading order) reused as-is | none (PIL/numpy/ffmpeg already in venv) | trivial (ms; layout math is closed-form) | Medium: new `_template_*` cell functions + a template scorer + one ffmpeg crop-drift filter per active cell; `render_smart_scene` mostly unchanged | **5** | design risk only — bad weight formula → ugly layouts; mitigated by clamps + fallback chain |
| B | **Flickr justified-layout algorithm** (github.com/flickr/justified-layout, MIT, 1.6k★) — port the row-partition math (~100 lines) to Python; feeds aspect ratios, returns aspect-preserving justified rows | Principled aspect-aware row layout: rows of varying height, cells keep native AR exactly (no letterbox gaps inside cells) — ideal engine for **mag-grid**/**strips** | none if ported (MIT permits); it's JS-only, no pip package | trivial | Low-medium: one pure function `justify(aspects, frame_w, target_row_h) -> rects`; slot under template A's mag-grid | **4** (as a sub-algorithm of A, not standalone — it has no importance weighting or hero concept) | port bugs; rows are height-uniform per row unless we run it per-row with weighted target heights |
| C | **squarify** (pypi `squarify` 0.4.4, Apache-2.0, pure Python, laserson/squarify 334★) — squarified treemap: areas ∝ importance weights | Direct "area = importance" solver, pip-installable, tiny | `pip install squarify`; Apache-2.0; last release 2024-era, low-churn but stable (algorithm is finished math) | trivial | Low to call, HIGH to make usable: treemap rects have arbitrary aspect ratios and layout order ≠ reading order → panels letterboxed inside cells, reading flow broken. Would need heavy post-constraints | 2 | breaks quality guards (reading order) out of the box; wrong tool shape |
| D | **rectpack** (pypi 0.2.2, Apache-2.0) — 2D bin packing heuristics | Rect packing into a canvas | `pip install rectpack`; Apache-2.0; **stale: last release Nov 2021, status Alpha** | trivial | High: solves "fit rects into min bins", not "fill fixed canvas with weighted areas in reading order"; no aspect scaling, no order control | 1 | wrong problem; unmaintained |
| E | **Academic: Cao/Chan/Lau SIGGRAPH Asia 2012 "Automatic Stylistic Manga Layout"** + AutoCollage (Rother 2006) + picture-collage optimization papers | Concepts: importance-driven panel areas over a template tree; saliency-guided placement | no code released (both); AutoCollage is MSR-patented and *crops* images (violates our no-crop guard) | n/a | Idea-mining only: confirms template-tree + importance-weight approach (what A does); implementing the full generative model is weeks of work for marginal gain over templates | 2 as implementation, **valuable as design validation for A** | none if used as reference |
| F | **delimitry/collage_maker** (MIT, 189★) / similar PIL collage scripts | Linear-partition row collage in PIL | MIT; unmaintained (13 commits, ~2014) | trivial | Nothing A+B don't already cover; no weighting, no importance | 1 | dead code, would be vendored then rewritten anyway |
| G | **Saliency model for panel importance** (cv2.saliency `StaticSaliencySpectralResidual` / `FineGrained`, in opencv-contrib; BASNet/U2-Net for deep saliency) | Per-panel visual-importance signal beyond OCR/beat data | spectral residual: opencv-contrib-python (BSD-ish Apache 2.0), ~5-15 ms/panel CPU; U2-Net: +170 MB model + torch — **reject weight** | cheap (classic) | Low: one function producing `saliency_var` per panel, mixed into the importance formula | 3 (optional garnish) | classic saliency on manga line art is noisy (screentones light up); NOT needed for v2 acceptance — beat duration + text density + position already separate hero from filler. Defer. |

**Rejected outright:** paid/hosted layout APIs (needless), GPL comic-layout repos
(none relevant found; the 5 GitHub hits for "comic panel layout generation" are
student text-to-comic projects with naive fixed grids), AutoCollage-style
optimizers (crop-based, patent-encumbered, no code).

**RECOMMENDED: A, with B's justified-row math ported inside it** (MIT-attributed
comment). No new pip dependencies. This is consistent with how v1 was won.
**Runner-up:** C (squarify) only if we later want organic/free-form mosaic pages —
not for v2.

Install + smoke for the top-2 external pieces (integrator reference; NOT run here):

```bash
# B — no install; port the algorithm. Grab reference impl for the port:
git clone --depth 1 https://github.com/flickr/justified-layout /tmp/opencode/jl
node -e "console.log(require('/tmp/opencode/jl')( [1.4,0.7,1.0] ).boxes)"  # sanity ref output

# C — runner-up only:
pip install squarify           # Apache-2.0, pure python
python -c "import squarify; print(squarify.squarify(squarify.normalize_sizes([6,3,1],192,108),0,0,192,108))"
```

---

## 3. Recommended design (build-ourselves, detailed for the integrator)

### 3.1 Template set (names, structure, trigger)

All templates emit the existing `Cell(panel, rest, hi)` list in reading order, so
`_build_plates` / `render_smart_scene` keep working. Frame = 1920×1080. GUTTER
stays 12. `W = importance weight` from §3.2. Panel count n after the existing
MAX_GRID pick (extend pick to allow n=2 for hero templates — v1's `n < 3 → None`
at layout_smart.py:322 relaxes to `n < 2` ONLY when a hero template triggers).

1. **`hero_right`** (samples 1, 6 mirrored) — trigger: n=2-3 AND one panel has
   hero score ≥ 0.5 AND hero panel is portrait/square (ar ≤ 1.2). Hero column on
   the right at width `hero_w = clamp(0.40..0.62, W_hero) * frame_w`, full height.
   Remaining panels stack in the left column, heights split by their weights.
   Hero = LAST panel in reading order (payoff) → right placement preserves
   left→right flow.
2. **`hero_left`** — mirror; trigger: hero is the FIRST panel (establishing
   splash). Same math, hero column on the left.
3. **`mag_grid`** (samples 2, 4) — trigger: n=3 (2 rows: 2+1 or 1+2) with mixed
   aspect ratios and no dominant hero. Rows built with the ported justified-row
   partition: row heights ∝ sum of member weights; within a row, cell widths ∝
   panel aspect × weight (justified so cells fill the row width exactly at
   fitted height — no dead space, the sample-2 look).
4. **`strips`** (samples 3, 4) — v1 `_stack_cells` upgraded: trigger unchanged
   (all wide, ar ≥ 1.4) but row heights become weight-proportional:
   `row_h_k = (h - (n+1)*GUTTER) * W_k / ΣW`, clamped so every text row meets
   its `_panel_min_h` **in the highlighted state** (accordion pop retained).
5. **`rail`** (sample 6) — trigger: n=2, one very wide/large scene panel + one
   tall panel. Scene panel gets 70-78% width, tall panel is a full-height right
   rail. Degenerate hero_right; kept as its own name for the variety metric.
6. **`inset`** (sample 5) — **PHASE 2, not v2.0**: hero full-bleed + small inset
   panels overlapping. Deferred because overlap risks covering the hero's
   bubbles; needs bubble-position data (exp-009 boxes give it, but the occlusion
   test is extra work). Ship v2 with 1-5; add inset behind a sub-flag.

Template selection: score every template that triggers, prefer (a) all guards
pass, (b) highest total displayed panel area, (c) round-robin tiebreak on a
per-chapter counter so consecutive smart scenes don't reuse the same template
(the "≥3 distinct templates on golden chapter" acceptance criterion).

### 3.2 Importance / weight formula

All inputs already exist — no new ML:

```
text_density_k = sum(box areas from <slug>.textboxes.json) / panel_area   # 0..~0.35
beat_share_k   = beat_dur_k / scene_duration                              # from _beat_times
pos_k          = 1.0 if k == n-1 (payoff) else 0.6 if k == 0 else 0.3    # story position
splash_k       = 1.0 if text_density_k < 0.02 and panel_area_px >= median else 0.0

W_k    = 0.40*beat_share_k + 0.35*text_density_norm_k + 0.25*pos_k
hero_k = 0.6*splash_k + 0.4*pos_k          # hero score: big art + payoff position
W_k    = clamp(W_k, W_min)  where W_min ensures cell height ≥ _panel_min_h(panel_k)
```

Key inversion vs v1: v1 sized cells uniformly then *rejected* the template when
`_panel_min_h` failed; v2 *solves* the split so each text cell gets at least its
required height first, then distributes the remainder by W. If the min-height
constraints alone exceed the frame (Σ required > available), the template is
infeasible → next template → v1 grid → seq (fallback chain, §3.4). This directly
converts ch3's 3 MEDIUM "right-column text too small" faults into either a
bigger cell or a clean fallback — never a small text cell.

Readability constant: keep exp-009's `MIN_TEXT_H_1080 = 40` px median line
height (anchors: BBC subtitle sizing ≈86 px comfort ceiling, Material 12sp ≈56 px,
broadcast 1/25-1/32 pict height ≈34-43 px floor; 40 is the agreed action floor —
do NOT relitigate, gap-009 adopted it).

### 3.3 Per-cell Ken Burns (active cell only)

Cheapest correct mechanism, one filter per cell, no extra ffmpeg pass:

- `_build_plates` renders each bright panel PNG at **1.10× its `hi` rect** (still
  fit-inside from the source, so no resample-up beyond SS headroom).
- In `render_smart_scene`, before each bright overlay, add a `crop` with animated
  offset: `crop=w=DW:h=DH:x='(iw-ow)*min((t-T0)/BEAT,1)*0.5':y='(ih-oh)*0.5'`
  (linear drift across the 10% overscan during the panel's beat; alternate
  drift axis per cell like the existing slide direction). Dimmed resting copies
  stay static — motion marks the ACTIVE cell, matching the gap goal.
- Whole-composite zoompan, breathe, and guided-view punch-ins are **retained
  unchanged** — they are the static_scan safety net; per-cell drift is additive
  polish and must NOT replace punch-ins (measured in round 1: sub-cell motion
  doesn't move the phash detector).

Wall-time: adds 1 crop filter + ~21% larger bright PNGs per cell. v1's cost is
dominated by the zoompan at SS resolution, unchanged → expected ≤1.1×, well
inside the ≤1.3× criterion. Benchmark anyway.

### 3.4 Fallback chain & guards (unchanged contract)

```
plan_layout_v2:  scored templates (hero_right/hero_left/mag_grid/strips/rail)
   └─ none feasible → v1 _grid_cells / _stack_cells   (adopted behavior, bit-safe)
        └─ None → sequential cuts                      (pre-gap-002 behavior)
```

- Every template computes cells with fit-inside only (`_fit`) — no cropping of
  panel art, bubbles survive (guard).
- Reading order = template's cell order = script panel order (guard).
- Beat mapping unchanged: `_beat_times` output indexes cells 1:1; hero templates
  with n=2 just get 2 beats. Punch-in scheduler works on any n ≥ 2 as-is.
- Flag: `--layout smart2` (or `smart --v2`); v1 remains the default until the
  benchmark gate passes.

### 3.5 What to measure in the prototype

1. Golden-chapter + ch3 scoped scenes: smart-scene phone_readability MEDIUM
   count (target 3 → 0), HIGH stays 0.
2. Template distribution log (need ≥3 distinct on golden chapter).
3. Wall-time ratio on the same scenes (≤1.3×).
4. Visual spot-check vs reference samples (human gate).

---

## 4. Open risks → quality-guard mapping

| Risk | Guard hit | Mitigation |
|---|---|---|
| Weight formula produces a near-degenerate sliver cell | phone_readability | W_min floor from `_panel_min_h` solved FIRST; infeasible → fallback |
| Hero template puts payoff on-screen from t=0 (spoiler) | weak_hook | hero cell stays DIMMED until its beat like every cell today (reveal semantics unchanged) |
| Per-cell drift + composite zoompan compound into wobble | new fault type | drift amplitude capped at 10% overscan, ~0.5 px/frame; disable drift when beat < 2 s |
| Landscape re-proportioning of portrait-page templates looks unlike samples | user acceptance | keep hero/rail column ratios from samples (0.35-0.45 rail), let strips cap at 3 rows |
| 2-panel scenes newly entering smart path regress seq scenes that were fine | no-regression criterion | n=2 only via hero/rail with BOTH panels passing guards; else seq unchanged |
| Squarify/rectpack shortcut temptation | reading order | documented as rejected (§2 C/D) — do not adopt |

License summary: everything shipped is our code; the justified-row port derives
from Flickr's MIT reference (attribute in a comment). Optional squarify is
Apache-2.0. No GPL anywhere. No model downloads, no new pip deps required.
