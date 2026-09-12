# gap-014: Tall-panel Ken Burns framing crops

**Stage:** render | **Fault types:** `cropped_content` | **Priority:** 3

## Problem
Now that segmentation no longer cuts through art (gap-004 v2: seg-attributable
cropped_content 3→0, deterministic gate 0/0/0), the residual cropped_content
faults on the golden chapter are render-stage: the Ken Burns framing of very
tall merged panels (e.g. v2 p0015 at 3122px) crops faces/bubbles at the frame
edges. The zoompan window is center-anchored and content-blind, so on extreme
aspect ratios the visible window sweeps past the content that matters — the
segmenter cannot influence these and further guard iterations cannot move the
gap-004 "6 → ≤2" criterion; only the render path can.

## Evidence
- improvements/experiments/exp-004-segmentation/report.md §"downstream benchmark":
  scene 28 t692.9 (3/3) — "render framing — the bubble is complete in p0101
  (bbox verified); Ken Burns window grazes its top".
- Same report §"v2 re-benchmark": scene 15 t359.3 — v1 art cut healed by v2
  merge into p0041 (2697px, clean gutters 0.00 ink), yet the flag persists:
  "the ken-burns framing crop of a very tall panel — render stage, not a cut";
  scene 21 t513.4 — panels byte-identical to v1 with clean edges (top ink
  0.06), "the residual flag is render framing".
- Same report, tie-break addendum: v2 gained a 3/5 cropped_content at scene 4
  t106.6 on merged panel p0015 (3122px tall, top edge 0.00 ink) — "reviewers
  describe side/framing crops — render framing of a tall panel, not a new cut".
- Same report, evaluator conclusion: "residual crops are render-stage
  ken-burns framing on tall panels -> new gap card" (this card).
- improvements/experiments/exp-009-phone-readability/report.md: framing-class
  faults explicitly out of crop scope (scene-3 "fragment framing came from the
  layout path… NOT from a crop"); the crop guard operates on the RENDERED
  window (crop × Ken Burns zoom path) precisely because framing, not cutting,
  is where crops manifest.
- pipeline/panel_render.py: zoompan is center-anchored (comments at :564,
  :622, :868 — "The zoompan is center-anchored, so…").
- ch4 production run (2026-09-10, `output/the-world-after-the-fall-ch4/review.md`):
  4 MEDIUM cropped_content framing crops survive the manual panel fixes and
  are deferred to the systemic render gaps (review.md:50-54) — 1:55 scene 4
  (:80), 4:37 scene 12 face+bubble cut at top (:85), 5:26 scene 14 bubble cut
  at bottom mid-sentence (:87), 8:13 scene 22 forehead/hair cropped (:91).
  Scene 12's source p0041 is 2845px tall and scene 4's region includes
  1500px+ strips (panels_index.json) — same tall-panel center-anchor class.

## Overlap with gap-006
This is a sharper, evidence-backed instance of gap-006 (saliency-guided Ken
Burns focal points, status proposed): gap-006 targets *where the zoom lands*
in general; this gap targets the specific tall-panel (extreme aspect ratio)
case where center anchoring provably crops content on the golden bench.
**Recommend merging the work**: one experiment can serve both cards — a
saliency/box-aware start/end rect selector fixes gap-014's tall-panel sites
and satisfies gap-006's focal-point criteria. Whichever is prototyped first
should cite both.

## Candidate directions
1. **Box-aware start/end rect selection for tall panels**: when panel
   aspect ratio exceeds a threshold (e.g. h/w > 2.5), choose the zoompan
   start/end windows so their union covers the OCR text boxes (already
   available from the payoff/crop paths) and detected face/salient boxes,
   instead of the center-anchored sweep.
2. **Saliency anchor** (gap-006 direction 1): lbpcascade_animeface /
   cv2.saliency spectral residual to pick the anchor; fall back to center
   when confidence is low.
3. **Vertical scroll for extreme aspect ratios**: replace zoom with a
   top→bottom pan (webtoon-native reading motion) whose window never
   excludes a text box mid-narration-beat.

## Acceptance criteria
- ~~cropped_content on the golden chapter bench: the 3 render-framing
  residuals identified in exp-004 (scenes 15 t359.3, 21 t513.4, 28 t692.9)
  no longer confirmed by review_stable.py majority vote (scene-scoped
  benchmark.py run on those scenes: cropped_content 3 → 0).~~
  **AMENDED 2026-09-12 — see amendment note below.** Replaced by:
  - (a) Chapter-wide deterministic gate: the exp-014
    `check_containment.py` run on the smart arm of a full golden-chapter
    (25-scene project) render reports **0 violations** (exit 0) — every
    h/w > 2.5 panel cut's rendered window contains 100% of the panel's OCR
    text boxes at every narration-beat timestamp, with NO silently-violating
    fallback plans.
  - (b) Golden-chapter `cropped_content` count (review_stable.py majority,
    matched-vetoes arithmetic per gap-015) does **not increase** vs the
    same-script flag-off arm, AND the scene-6 t≈175.7 face-crop heal
    demonstrated in the v1 eval (3/3 baseline fault absent on the smart arm)
    **persists** — verified by frame evidence at that timestamp.
  - (c) No new fault types on scenes whose panels are all normal-aspect
    (h/w ≤ 2.5) — the anchor path fires there too and must stay
    containment-correct (benchmark.py type veto, review_stable.py per
    gap-011).
- Deterministic containment check: for every panel with h/w > 2.5, the
  rendered zoompan window at every narration-beat timestamp contains 100% of
  the panel's OCR text boxes (scriptable against the exp-009 containment
  logic — extend it from crop windows to Ken Burns start/end rects; this is
  a NEW deterministic check, to be added to the experiment's gate script).
- No new cropped_content on scenes with normal-aspect panels (benchmark.py
  type veto, measured with review_stable.py per gap-011).
- Render wall-time increase ≤1.2x (gap-006's bound).

### Amendment note (2026-09-12, exp-014 v1 evaluator finding)
The original first criterion referenced golden review scenes 15/21/28 at
t359.3/t513.4/t692.9 with a "cropped_content 3 → 0" target. The v1
evaluation found it **unmeasurable as written**: those scene numbers and
tall panels (p0041@2697px, p0059@2072px, p0101@2510px) belong to the
exp-004 v2 re-scripted 35-scene bench (`/tmp/opencode/exp004_bench_v2`),
which was deleted; the golden 25-scene project has different panels at
those numbers (golden p0041 is 690×1609, p0101 is 690×355) and neither arm
reproduces the fault surface at the mapped timestamps. Re-building that
bench (re-segmentation + re-script) is outside a training-round budget, so
the criterion is replaced with the three measurable golden-project criteria
(a)/(b)/(c) above. The p0101 bubble site itself remains covered
deterministically via the archived-plan replay (exp-014 report §1:
center 16 violations → smart 0).

## Quality guards
- Fallback to today's center anchor when no boxes/saliency found — never
  worse than current behavior.
- Keep the no-sideways-pan rule (pans were removed as distracting;
  vertical scroll on webtoon strips is the only sanctioned pan).
- Byte-identical output when the feature flag is off.
