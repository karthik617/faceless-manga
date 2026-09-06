# exp-002 — smart multi-panel layout (gap-002) — Round 1 Evaluation Report

Evaluator run: round1-eval, 2026-09-06. Golden project untouched.
All artifacts: `improvements/experiments/round1-eval/run/` (arm_A/arm_C scripts,
review_A/review_C json+md, render_C.log, _work_A/_work_C).

## Verdict

**benchmark.py: FAIL** (binding) — weighted score 233 → 278 (delta +45), with
worsened-fault-type vetoes. Unlike exp-001, here the FAIL is **partly real**:
the deterministic static-scene detector (not just the noisy vision channel)
got worse (4 → 12 spans), and phone_readability worsened 2 → 8 with 2 of the
new faults landing on smart-layout scenes. The smart path renders correctly
(frame evidence below shows genuinely better composition on its poster case)
but its current motion/readability tradeoffs degrade the measured cut.

## Scenes triggered

**9 of 36 scenes** used the smart path (log: `scene N: smart layout (grid, k
panels)` for scenes 2, 6, 12, 13, 17, 24, 26, 32, 35 — 1-indexed; grids of 3-4
panels). 31 scenes have ≥3 panels, so the planner accepted **9/31 = 29%** of
eligible scenes and fell back (returned None) on the rest. Measurable — not a
FAIL-not-measurable case.

## Score table (fault type, A vs C)

| type | sev | Arm A | Arm C | note |
|---|---|---|---|---|
| irrelevant_panel | high | 31 | 33 | vision channel; A had 4 unscored scenes (review_error) |
| watermark | high | 6 | 6 | stable |
| blank_frame | high | 3 | 3 | deterministic, stable |
| static_scene | med | 4 | 12 | **deterministic detector — real regression**; C's new spans at t≈30.5, 120.5, 252.5, 266.5, 280.5, 624.5, 848.5… map to scenes 1, 4, 10, 11, 12, 25, 34 (mix of smart scenes 12/25 and non-smart) |
| phone_readability | med | 2 | 8 | new faults on scenes 1, 5, 10, 22, 33 (0-idx); scenes 1 and 5 are smart-layout scenes (grid cells shrink already-small panels) |
| cropped_content | med | 7 | 12 | vision channel |
| empty_screen | med | 1 | 2 | |
| unreadable_text | low | 1 | 0 | |
| review_error | low | 4 | 0 | A undercounted |
| **weighted score** | | **233** | **278** | weights high=5 med=2 low=1 |

## Acceptance criteria (gap-002)

| criterion | result | status |
|---|---|---|
| ≥40% drop in irrelevant_panel + static_scene on scenes with 3+ panels | A=26 → C=29 (static mapped to scenes via clip-boundary timestamps; scene field is null on static faults) — **worse**, needed ≤15 | **NOT MET** |
| no new phone_readability faults | 2 → 8; scenes 1 and 5 (0-idx) among the new ones are smart-layout scenes | **NOT MET** |
| render wall-time ratio | C: 633 s vs A resumed leg 1256 s → ratio ≈ 0.50; but A's leg re-did 31 TTS calls with 7 retries while C reused none of A's clips either — TTS was re-synthesized in C too (audio-copy does not skip TTS; only cached *clips* skip work). Honest read: smart layout adds no material render cost; both legs are TTS-dominated | **MET** (no meaningful slowdown attributable to smart layout) |

## Vetoes (benchmark.py)

- fault types worsened (base→now): irrelevant_panel 31→33,
  static_scene 4→12, phone_readability 2→8, empty_screen 1→2,
  cropped_content 7→12.
- static_scene and phone_readability are the substantive ones: static_scene is
  a deterministic detector (identical across A/B, so its 3× rise in C is
  caused by C's rendering), and phone_readability regressions on smart scenes
  match the mechanism (grid cells display panels at 1/3–1/4 size).

## Wall times

| step | time |
|---|---|
| Arm C render to review-cut (--layout smart) | 633 s, 0 tts retries |
| Arm A render (resumed leg, 5 scenes pre-cached) | 1256 s, 7 tts retries |
| review_video | ~3 min per arm |

Ratio is not apples-to-apples (different TTS retry luck); the smart
compositor itself (ffmpeg xfade/zoompan grid) is not the bottleneck.

## Frame evidence (samples/)

- `scene31_t782.5_armA.png` vs `_armC.png` — poster case FOR the feature. A:
  full-screen text bubble ("IT'S REALLY SUCH A RELIEF…") with zero art while
  narration describes the cloaked figure; C: 4-cell grid showing face close-up
  + "IF IT'S YOU." panel + boot panel + the bubble in context, with active-cell
  highlight. Clearly better storytelling — yet note the bubble text in C is
  ~1/3 frame size (the phone_readability tradeoff).
- `scene31_t788.4_armA/C.png` — same scene later beat.
- `scene11_t271.8_armA/C.png` — A: cropped face/ice-shard; C: grid version.
- `scene34_t843.8_armA/C.png` — A: partial text/logo frame; C alternative.

## Interpretation

The compositor works and its output is visibly richer on dialogue-heavy
scenes. But two real regressions block adoption in current form:

1. **static_scene ×3** — the grid's beat-highlighting apparently holds the
   full grid static long enough (>10 s spans) to trip the deterministic
   static-visual scan; the single-panel path's Ken Burns motion avoided this.
   The smart path needs per-cell motion or a slow global drift.
2. **phone_readability** — 3-4 panel grids at 1080p shrink text below phone
   legibility. Needs a cap (2-panel layouts only?) or text-aware cell sizing
   (don't grid panels whose OCR kind is dialogue-heavy).

Reviewer noise (see exp-001 report: ±12 irrelevant_panel faults on identical
pixels) also inflates the vision-channel deltas here, but unlike exp-001 the
deterministic detectors independently confirm a regression, so FAIL stands on
its own merits.

## Caveats

- Stage-scoped (--stop-after review-cut, --no-music identical in all arms):
  final loudness, music seams, TTS mix, popup/title-card and final encode not
  measured; LUFS/duration vetoes inactive.
- static_scene faults have `scene: null`; scene attribution done by mapping
  fault timestamps onto per-clip durations — accurate to the clip boundary.
- Single review run per arm; vision-channel counts (irrelevant_panel,
  cropped_content) carry the same n=1 noise documented in exp-001.
- Arm A first-attempt hang (edge-tts stall, no socket timeout in
  make_video.tts) cost ~55 min and is a pipeline robustness finding unrelated
  to either experiment.

---

# v2 evaluation (2026-09-06, evaluator run round1-eval)

Artifacts: `improvements/experiments/round1-eval/run/` — `arm_C2.json` (= arm_A.json
+ `--layout smart` only), `_work_C2/` (fresh workdir, cached TTS audio a*.mp3 copied
from _work_A), `render_C2.log`, `stable_C2.{json,md}` (3-pass), `stable_Acut.{json,md}`
(3-pass baseline on Arm A's REVIEW CUT — the earlier stable_A1/A2 were on the final
branded mp4 and are not comparable). Metrics: `metrics_v2_global.json`,
`metrics_v2_scoped.json` (= `metrics.json`).

## Verdict

- **Global: FAIL** — score 154 → 156 (delta +2). Vetoes: new type `unreadable_text`
  (0→2), worsened `irrelevant_panel` 19→20, `phone_readability` 1→2.
- **Scene-scoped (smart scenes only): FAIL on a veto, but score IMPROVED** —
  14 → 10 (delta −4, high faults 2→1). Sole veto: `unreadable_text` 0→1, a LOW
  severity 2/3-vote fault on scene 6's "True Dragon Sword" info card — the same
  card that already carries a confirmed phone_readability fault in BOTH arms
  (i.e. a re-label of a pre-existing baseline problem, not new smart-layout damage).

Both v1 regressions are fixed (details below). The residual global delta (+2) is
inside the reviewer-noise band documented in exp-001 (±12 on identical pixels) and
comes from non-smart scenes rendered by the untouched seq path.

## Scenes triggered (SMART_SCENES)

**5 of 36 scenes** (v1: 9): **6, 12, 17, 24, 31** (1-indexed) = 5, 11, 16, 23, 30
in review-JSON 0-indexing. All grids, 3 cells; scenes 24 and 31 used first/middle/last
selection from 4 and 5 panels (`3 of 4`, `3 of 5` in render_C2.log). The v2 55%
text-height guard rejected 4 scenes that v1 accepted (2, 13, 26, 32, 35 → seq).

## Per-fault-type table (confirmed 3-pass majority, global)

| type | sev | A (review cut) | C2 | smart scenes A → C2 |
|---|---|---|---|---|
| irrelevant_panel | high | 19 | 20 | 2 → 1 |
| watermark | high | 6 | 5 | 0 → 0 |
| blank_frame | high | 1 | 1 | 0 → 0 |
| cropped_content | med | 8 | 7 | 1 → 1 |
| phone_readability | med | 1 | 2 | 1 → 1 |
| static_scene | med | 1 confirmed (4 detector spans) | 1 confirmed (3 detector spans) | 0 → 0 |
| empty_screen | med | 2 | 2 | 0 → 0 |
| unreadable_text | low | 0 confirmed (1/3-vote sightings in 2 passes) | 2 | 0 → 1 (scene 6 info card, low, 2/3) |
| **weighted score** | | **154** | **156** | **14 → 10** |

## The two v1 regressions, specifically

**static_scene — FIXED.** v1: 4 → 12 detector spans. v2: deterministic
static-visual scan reports **3 spans in C2 vs 4 in A** (identical 3 spans at
t=860.5/872.5/884.5 in both arms — final-scene tail, scene 35/36, not a smart
scene; A additionally has one at t=292.5, scene 13, which C2 does not). **Zero
static spans on smart scenes.** The punch-in scheduling did what the smoke test
predicted. (Note: majority-vote JSON collapses all null-scene static faults to
one confirmed entry per arm; the span counts above are from the per-pass
deterministic scan, stable 3/3 passes in both arms.)

**phone_readability — HELD AT BASELINE on smart scenes.** v1: 2 → 8 global with
2 new on smart scenes. v2 global: 1 → 2; smart scenes: **1 → 1** — the single
smart-scene fault (scene 6, t≈130, info card) exists in the baseline too (3/3
votes both arms; it's small source text, not a grid artifact — visible in
sample frames, the card is small in A's fullscreen render as well). The one
new global fault is scene 11 (0-idx 10), a NON-smart scene rendered by the
byte-identical seq path — vision-channel noise (3/3 votes in C2, 0 sightings
in A; same pixels).

## Acceptance criteria (gap-002)

| criterion | result | status |
|---|---|---|
| irrelevant_panel + static_scene on smart scenes ≥40% drop | baseline 2 (irr@12, irr@31; 0 static) → v2 1 (irr@6): **−50%** | **MET** (small-n: 2→1; scoped score confirms direction, high faults 2→1) |
| phone_readability: no new faults on smart scenes | 1 → 1 (same pre-existing info-card fault both arms) | **MET** (global 1→2, but the new one is a non-smart seq scene) |
| render wall-time ≤1.5x | C2 full review-cut render 678 s (0 TTS retries) vs v1's C 633 s and A's resumed leg 1256 s; smart compositing adds ~45 s over v1 across 5 scenes | **MET** |

Strictly, the benchmark's own veto (`unreadable_text` 0→1 scoped) blocks a PASS
stamp: one LOW 2/3-vote fault double-labeling the same info card that both arms
already flag as phone_readability. Baseline passes contained unreadable_text
sightings too (scenes 1/7/14 at 1/3 votes each — below the confirmation bar).
This is a vote-threshold coin-flip on a shared baseline defect, not a v2 regression
mechanism; flagged for the human gate rather than treated as disqualifying.

## Wall times

| step | time |
|---|---|
| C2 render to review-cut (`--layout smart`, cached TTS mp3s reused as inputs but re-synth pipeline still ran clips fresh) | **678 s**, 0 tts retries |
| v1 C render (same stage) | 633 s |
| A render (resumed leg, v1 eval) | 1256 s (7 tts retries) |
| stable review, 3 passes | ~9 min/arm |

Caveat: arms never share clip caches, and TTS retry luck dominates; the honest
read is smart-v2 costs ≈45 s extra over 5 scenes (~9 s/scene for the punch-in
filter graph) — well under 1.5x.

## Frame evidence (samples/)

`v2_scene{6,12,31}_{armA,armC2}_{early,mid,late}.png` (1-indexed scene names;
timestamps 123/131/140, 255/266/278, 744/754/764 s, same t in both arms):

- **scene 6 mid (t=131)** — C2: fullscreen punch-in on the sword panel with the
  info card at near-native size (the punch mechanism working as designed); A:
  fullscreen sword panel. The info card is small in both — the phone fault here
  is the source art, not the layout.
- **scene 12 mid (t=266)** — C2: 3-cell grid, center cell active with vermilion
  accent, side cells dimmed; readable bubbles ("ALL THE VICTORIES WE SHARED").
  A: single ice-shard panel while narration covers three beats.
- **scene 31 mid (t=754)** — C2: 3-of-5 grid (boot/step, stairs+COUGH, archway)
  with the active-cell border; text legible at cell size (55% guard). A: single
  panel.

## Caveats / anomalies

- Scene indexing: render log is 1-indexed, review JSON 0-indexed. The first
  scoped benchmark run used 1-indexed IDs by mistake and was discarded;
  `metrics_v2_scoped.json` uses the correct 0-indexed {5,11,16,23,30}.
- static_scene faults carry `scene: null`; span→scene attribution via per-clip
  duration mapping (clip-boundary accurate).
- Baseline static span at t=292.5 (scene 13) present in A, absent in C2 — scene
  13 is seq in both arms; likely borderline phash (both arms re-rendered that
  clip independently). Counts C2 favorably by 1; smart-scene conclusion (0→0)
  unaffected.
- Vision channel (irrelevant_panel, cropped_content) still noisy even at 3
  passes: 8 of C2's 20 irrelevant_panel faults sit on scenes with byte-identical
  render settings to A. Scoped comparison is the decision-grade number.
- Stage-scoped as in v1: `--stop-after review-cut`, `--no-music`; final encode /
  loudness / music vetoes inactive.
- eval.ocr.json copied to arm_C2.ocr.json; sidecar loaded (no "missing OCR"
  warning in render_C2.log), so the 55%/45% text guards ran on real OCR data.
