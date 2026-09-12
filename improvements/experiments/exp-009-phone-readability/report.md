# exp-009 evaluation report — round2-eval (Arm T, `--text-aware`)

**Verdict (binding, benchmark.py): FAIL — global AND scene-scoped, on both
score regression and vetoes.**

- Global: score 126 → **149** (delta +19, worse). VETO: watermark 4→6,
  cropped_content 4→7, irrelevant_panel 12→15. (`metrics_global.json`)
- Scoped (`--scenes-changed 0,1,2,4,5,6,7,9,12,13,16,20,21,22,23,24` — the 16
  scenes where crops fired): score 86 → **105** (delta +19, worse). VETO:
  watermark 3→5, cropped_content 4→5, irrelevant_panel 6→9. (`metrics.json`)

Interpretation: the feature does what the gap card asked for on its target
metric — the scene-5 info card goes from squint-size to full-frame legible and
phone_readability+unreadable_text dropped 3 → 1 — but the crops cause more
damage than they repair. This is a clear FAIL, not a borderline one: the guard
metric (cropped_content) worsened in exactly the way gap-009's quality guard
prohibits ("no half-cut bubbles"), with multiple reviewer-confirmed half-cut
bubbles and decapitated faces on changed scenes, and the tighter framing also
re-exposed watermarks and turned context panels into "irrelevant" fragments.
The containment guard ("a crop can never cut through a bubble") demonstrably
did not hold at render time — e.g. scene 20 t=628.5 shows a bubble and face
cropped at the top of frame. Iterate before re-measuring: enforce the
containment guard on the RENDERED window (crop × Ken Burns zoom path), reject
degenerate tiny crops (p0068 → 65×62 px, p0117 → 160×160 were accepted), and
make crops watermark-aware (or stack on exp-003).

## What ran

- `panel_render.py arm_t.json --res 1080p --no-music --layout smart
  --text-aware --workdir _work_t --stop-after review-cut`; sidecar
  auto-generated (DBNet path), TTS mp3s reused from baseline.
- Crops fired on **31 panel-instances across 16 scenes** (render log 1-based
  scenes 1,2,3,5,6,7,8,10,13,14,17,21,22,23,24,25 → 0-based
  0,1,2,4,5,6,7,9,12,13,16,20,21,22,23,24). Log: `/tmp` copy summarized in
  round2-eval; notable accepted-but-degenerate crops: p0068 → (138,605,65,62),
  p0117 → (67,130,160,160).
- Stable review ×3 → `round2-eval/stable_t.json`.

## Score table (stable review, majority-of-3)

| fault type | baseline | Arm T | Δ |
|---|---|---|---|
| phone_readability (M) | 2 | 1 | **-1** (target) |
| unreadable_text (L) | 1 | 0 | **-1** (target) |
| cropped_content (M) | 4 | 7 | **+3 ⚠ guard veto** |
| irrelevant_panel (H) | 12 | 15 | +3 ⚠ veto |
| watermark (H) | 4 | 6 | +2 ⚠ veto |
| missing_payoff (H) | 5 | 4 | -1 (noise, untouched logic) |
| weak_hook / empty_screen / panel_reuse | 1/1/1 | 1/1/1 | 0 |
| **weighted global** | **126** | **149** | **+19 FAIL** |
| **weighted scoped** | **86** | **105** | **+19 FAIL** |

New-fault detail (changed scenes): scene 0 cropped bubbles at edges (H+M pair),
scene 2 cropped stylized text, scene 6-1-based/5-0-based face cropped at top
(chin+neck only), scene 7 partial SFX text + black space, scene 20 bubble+face
cropped at top with text cut, scenes 0/1/22 watermark re-exposed by tighter
framing, scenes 9/20/24 irrelevant_panel (crops isolate SFX/background
fragments).

## Acceptance criteria (gap-009)

| criterion | result |
|---|---|
| phone_readability + unreadable_text 4 → ≤1 | **MET on this baseline** — stable baseline had 3 (2 PR + 1 UT; the gap card's 4 was single-pass), Arm T has 1 (PR@0, itself caused by a bad crop). Raw counts reported since ch3 is the real target: PR 2→1, UT 1→0. |
| no new cropped_content from over-zealous punch-ins | **NOT MET** — 4 → 7 global (+3), all three new ones on crop-changed scenes with crop-consistent details (half-cut bubbles, decapitated faces, partial SFX). Hard veto. |
| quality guard: punch-in respects bubble boundaries | **NOT MET** — reviewer-confirmed half-cut bubbles at scene 0 t=26.4 and scene 20 t=628.5 (see samples). |

## Vetoes triggered

- Worsened fault types (both scopes): cropped_content (the gap's own guard),
  irrelevant_panel, watermark. All plausibly experiment-caused: crops both cut
  content and re-frame onto watermark corners / SFX fragments. Score also
  regressed +19, so FAIL holds even without vetoes.

## Wall times

Sidecar detection: ~16 s (154 panels, DBNet). Render: ~8 min (slightly faster
than baseline's ~10). Stable review ×3: ~23 min. No budget concerns.

## Frame evidence (`samples/`)

- `scene5_base_t143.png` vs `scene5_textaware_t143.png` — the win: True Dragon
  Sword info card, tiny → full-frame legible. Also t150/t157 pairs (t150 shows
  a side-effect: group shot reframed to a partial view).
- `guard_base_t26.4.png` vs `guard_textaware_t26.4.png` — scene 0 cropped-bubble fault.
- `guard_base_t94.3.png` vs `guard_textaware_t94.3.png` — scene 3 fragment framing.
- `guard_base_t190.1.png` vs `guard_textaware_t190.1.png` — scene 7 partial SFX + black space.
- `guard_base_t628.5.png` vs `guard_textaware_t628.5.png` — scene 20: baseline shows face + bubble + stone hand; text-aware shows bubble cut at top, face gone — the exact "half-cut bubble" the guard prohibits.

## Caveats — not measured in this stage-scoped run

- Golden ch2 has few readability faults by construction; ch3 (where the gap
  lives) was not rendered — the +19 regression here is disqualifying anyway,
  but a fixed version must be measured on ch3 too.
- The smart-layout `_panel_min_h` interaction (planner rejections → sequential
  fallback) wasn't isolated from the crop path; some "irrelevant_panel" faults
  may come from layout fallbacks rather than crops per se.
- TTS reuse, no final encode: loudness/duration/encode vetoes unexercised
  (review-cut duration drift -0.03 s, fine).
- Sidecar was generated fresh in the run dir (`arm_t.textboxes.json`); the
  pre-existing golden sidecar was not used or modified.

## v2 evaluation — round2-eval (Arm T2, `--text-aware` + v2 guards)

**Verdict (binding, benchmark.py): global FAIL (veto) / scene-scoped PASS.
Net read: FAIL-leaning-iterate — the v1 crop regressions are all fixed; the
remaining global vetoes come from the untouched smart-layout `_panel_min_h`
interaction (v1's known, explicitly-unaddressed caveat) plus one
reviewer-noise fault, not from the v2 crop path.**

- Global: score 126 → **117** (delta **-9**, improved) but VETO:
  watermark 4→5, empty_screen 1→2. (`metrics_global.json`)
- Scoped (`--scenes-changed 1,5,9,12,21` — the 5 scenes, 0-based, where a
  crop actually fired): score 22 → **20** (delta -2), **no vetoes → PASS**.
  (`metrics.json`)
- Extra probes (not the binding files): scoped over crop+noise-removed
  scenes `1,4,5,9,12,21,24` → PASS 28→21 (-7); scoped over every scene any
  fault moved on (`+3`) → FAIL only on the scene-3 empty_screen (see below).

### What ran

- Arm T2: `panel_render.py arm_t2.json --res 1080p --no-music --layout smart
  --text-aware --workdir _work_t2 --stop-after review-cut`
  (mirrors the v1 arm_t invocation exactly). Log:
  `round2-eval/arm_t2_render.log`. Sidecar reused
  (`arm_t2.textboxes.json` = copy of `arm_t.textboxes.json`);
  `watermark_log.json` copied from the golden project into round2-eval so
  the v2 watermark guard could see it (guard reported: 9 recorded panels).
- Golden project protected by the copy approach: all inputs copied/symlinked
  into `round2-eval/`, no file under `output/the-world-after-the-fall-ch2/`
  read-modified (verified: zero files in the golden dir modified today).
- **TTS reuse caveat**: `_work_t`'s mp3s were pre-seeded into `_work_t2`,
  but panel_render's cache key is the scene *clip*, not the mp3 — with no
  `c*.mp4` present it re-synthesized all 25 narrations (same voice/rate/
  pitch, so voicing matches; exact waveforms differ — same behavior as
  every prior round2 arm, incl. v1). Review-cut duration 781.10 s vs
  baseline 781.51 s (-0.05%, fine).
- Stable review ×3 → `round2-eval/stable_t2.(json|md)`: 28 confirmed
  (21H/5M/2L), 1 unconfirmed-high, vs baseline 31.

### Crop decisions (from arm_t2_render.log; every non-crop has a logged reason)

| outcome | panel-instances | distinct panels | detail |
|---|---|---|---|
| **accepted** | 5 | 5 | p0003 (379²), p0024 (423×379 — the scene-5 win, identical rect to v1), p0043 (417² — grown from v1's 379² by the zoom-margin repair), p0060 (379²), p0126 (379²). All ≥ every floor. |
| rejected: containment-under-motion | 13 | 6 | p0006, p0007, p0025 (zmax 1.16), p0122, p0128, p0136 — incl. the exact v1 scene-20 half-cut-bubble panel p0122 |
| rejected: min-size | 3 | 1 | p0068 65×62 — v1's degenerate crop, now killed |
| rejected: text-cover < 1% | 11 | 6 | p0022, p0032 (v1's micro-word fragment), p0063, p0069, p0090, p0129 |
| skipped: recorded watermark | 16 | 8 | p0017, p0031, p0040, p0066, p0080, p0101, p0117, p0133 — all 9 log panels minus p0094 (never crop-eligible) |

v1 accepted 31 panel-instances across 16 scenes; v2 accepts 5 instances
across 5 scenes (1-based 2/6/10/13/22 → 0-based 1/5/9/12/21). Feature now
fires only where it can win, exactly as smoke_v2 predicted.

### Score table (stable review, majority-of-3)

| fault type | baseline | Arm T (v1) | Arm T2 (v2) | Δ v2 vs base |
|---|---|---|---|---|
| phone_readability (M) | 2 | 1 | **0** | **-2 (target)** |
| unreadable_text (L) | 1 | 0 | 1 | 0 (scene 7, mirrors a baseline fault) |
| cropped_content (M) | 4 | 7 ⚠ | **3** | **-1 — v1 veto GONE** |
| irrelevant_panel (H) | 12 | 15 ⚠ | 11 | -1 — v1 veto GONE |
| watermark (H) | 4 | 6 ⚠ | 5 | +1 ⚠ global veto (see below) |
| empty_screen (M) | 1 | 1 | 2 | +1 ⚠ global veto (see below) |
| missing_payoff (H) | 5 | 4 | 5 | 0 |
| weak_hook (H) | 1 | 1 | 0 | -1 (noise) |
| panel_reuse (L) | 1 | 1 | 1 | 0 |
| **weighted global** | **126** | 149 | **117** | **-9** |
| **weighted scoped (crop scenes)** | **22** | — | **20** | **-2 PASS** |

### Are the v1 regressions gone?

| v1 regression | v2 status |
|---|---|
| cropped_content 4→7 (half-cut bubbles/faces) | **GONE** — 4→3. The 3 remaining (scenes 2, 6, 7) all mirror baseline faults on scenes where NO v2 crop fired (scene-6 face crop and scene-7 SFX crop exist identically in the baseline). Scene-20 t=628.5 frame is now visually identical to baseline: bubble + face + stone hand fully in frame (`samples/v2/eval/scene20_*`). Scene-0 t=26.4: crop skipped (watermark guard), frame ≈ baseline, and the baseline's own scene-0 cropped_content/phone_readability/weak_hook trio is gone from T2's review. |
| degenerate 65×62 / 160×160 crops accepted | **GONE** — p0068 rejected min-size (logged 3×); p0117 skipped (watermark). No accepted crop below 379 px min-dim. |
| watermark re-exposure BY CROPS (v1 scenes 0/1/22) | **GONE as a crop effect** — all 9 recorded-watermark panels skipped. The global watermark 4→5 is NOT the crop path: T2's two new watermark faults are scene 0 t=27.1 (frame near-identical to baseline — the FLAMESCANS badge is equally visible in both; baseline review flagged scene 9 instead → reviewer relabeling noise) and scene 1 t=40.5 (real, but caused by the smart-layout `_panel_min_h` fallback: baseline renders scene 1 as a 3-panel grid, T2 falls back to sequential and shows the p0002 banner full-frame, magnifying its corner watermark — the layout interaction v1's report flagged and v2 explicitly did not address). |

New empty_screen (scene 3 t=94.8, pendant silhouette solo on white): also
not a crop — no crop fired or was even evaluated on scene 3; it's the same
layout/sequential-framing class the v1 report called out on this exact scene
("scene-3 fragment framing came from the layout path… NOT from a crop").
Baseline shows the same pendant panel inside a highlight composite; T2's
sequential path holds it full-frame.

Smart-layout diff confirming the mechanism: baseline plans grids on 0-based
scenes 1, 4, 9, 18, 24; both v1 and v2 arms keep only scene 18 — measured
text boxes make `_panel_min_h` reject the other four plans → sequential
fallback. That fallback, not the crops, is where both residual vetoes live.

### Does the scene-5 readability win survive?

**YES.** p0024 crop fires with the identical rect (220,1316,423,379);
baseline's phone_readability fault at scene 5 t=143.1 ("System description
text in orange box is too small") is absent from the T2 review; frames
`samples/v2/eval/scene5win_base_t143.png` vs `scene5win_t2_t143.png` show
the True Dragon Sword card going squint-size → full-frame legible.
phone_readability + unreadable_text: baseline 3 → T2 1 (and that 1 mirrors
a baseline scene-7 fault untouched by any crop).

### Acceptance criteria (gap-009)

| criterion | result |
|---|---|
| phone_readability + unreadable_text 4 → ≤1 | **MET on this baseline** (3 → 1; PR 2→0, UT 1→1 with the residual UT pre-existing in baseline). ch3 — the gap's origin — still unmeasured. |
| no new cropped_content from punch-ins | **MET** — 4→3; zero cropped_content on any crop-fired scene. |
| quality guard: punch-in respects bubble boundaries | **MET** — containment-under-motion rejected 13 crop evaluations incl. the exact v1 offenders (p0122 scene 20/25, p0025 scene 7); no reviewer-confirmed half-cut bubble on any crop-fired scene. |

### Vetoes triggered

Global run: watermark 4→5, empty_screen 1→2 → benchmark FAIL (binding for
the global scope; I do not soften it). Attribution honest-read: 1 fault
reviewer noise (scene-0 watermark, frames near-identical), 2 faults
experiment-adjacent via the smart-layout `_panel_min_h` fallback (scene-1
watermark, scene-3 empty_screen) — a v1-inherited interaction the v2 iterate
deliberately left out of scope. Scene-scoped run (crop scenes only, the
scoring the eval plan designates for subset-touching experiments): PASS, no
vetoes. **One change would decide the global verdict: make `_panel_min_h`
degrade to the adopted binary guard instead of forcing planner rejection
(keeping the grids on scenes 1/4/9/24), then re-measure — the crop path
itself is no longer producing regressions.**

### Wall times

Render (TTS re-synth + 25 scenes): ~15.5 min. Stable review ×3: ~10.5 min.
Benchmarks + frames: <1 min. No budget concerns.

### Frame evidence (`samples/v2/eval/`)

- `scene5win_base_t143.png` vs `scene5win_t2_t143.png` — the win, preserved:
  info card tiny → full-frame legible.
- `scene20_base_t628.5.png` vs `scene20_t2_t628.5.png` — v1's worst fault
  frame: T2 now identical to baseline, "JAEHWAN, YOU PUNK…" bubble + face +
  bloodied stone all fully in frame.
- `scene0_base_t26.4.png` vs `scene0_t2_t26.4.png` — v1 cropped-bubble scene:
  T2 ≈ baseline full panel (watermark-skip); note FLAMESCANS badge equally
  visible in BOTH — the T2-only watermark flag here is reviewer noise.
- `scene7_base_t190.1.png` vs `scene7_t2_t190.1.png` — near-identical SFX
  frames; the v1 "partial SFX + black space" crop is gone, residual fault is
  baseline-inherited.
- `scene3_base_t94.3.png` vs `scene3_t2_t94.3.png` — the layout-fallback
  divergence behind the scene-3 empty_screen (multi-panel composite vs
  sequential full-frame pendant); no crop involved.

### Caveats — not measured in this stage-scoped run

- **ch3 measurement still outstanding.** Golden ch2 has few readability
  faults by construction (baseline 3); the gap card's motivating faults live
  in ch3's system-box-heavy scenes and the ch3 right-column MEDIUM layout
  fix — neither rendered here. Mandatory before adoption.
- The smart-layout `_panel_min_h` interaction is now the dominant residual
  effect (4 lost grids → both global vetoes). It was out of scope for the
  v2 iterate; it must either be fixed or measured in isolation before any
  adoption call.
- Stacking with exp-003 (panels_clean present) unmeasured: 8 watermark-
  skipped panels would become crop-eligible — that combination needs its own
  arm.
- TTS re-synthesized (same voice; clip cache key defeats mp3 pre-seeding),
  no final encode: loudness/final-duration/encode vetoes unexercised
  (review-cut drift -0.4 s / -0.05%, fine).
- v1's metrics.json/metrics_global.json overwritten with v2 numbers per the
  eval plan; v1 numbers preserved in the tables above.

## v3 evaluation — round2-eval (Arm T3, `--text-aware` + planner/crop split)

**Verdict (binding, benchmark.py): FAIL — both scopes veto, though every
veto fault sits on a frame that is byte-identical or visually identical to
baseline. Global: score 126 → 113 (delta -13, best of any arm) but VETO
cropped_content 4→5. Scoped (`--scenes-changed 5,12,21`, the crop scenes):
12 → 15 (+3) with VETO irrelevant_panel 1→2. Net read:
FAIL-leaning-adopt-after-noise-control — the v3 mechanism did exactly what
it promised (v2's layout vetoes reverted, plans byte-identical to baseline,
crop decisions unchanged), and the surviving vetoes are reviewer relabeling
on unchanged pixels, but the binding verdict is FAIL and I do not soften
it.** (`metrics_global.json` / `metrics.json`)

### Plan/crop log verification — the v3 contract, checked first

- **Smart-layout lines: IDENTICAL to baseline.** `arm_t3_render.log` plans
  grids on 1-based scenes 2, 5, 10, 19, 25 (0-based 1, 4, 9, 18, 24) with
  the exact same panel picks as the baseline (`arm_w2_render.log`); `diff`
  of the "smart layout" lines is empty. v2 had kept only scene 19. ✔
- **Crop accept lines: IDENTICAL to v2.** Same 5 accepted crops with the
  same rects — p0003 (44,524,379,379), p0024 (220,1316,423,379 — the
  scene-5 win), p0043 (235,239,417,417), p0060 (56,0,379,379), p0126
  (281,728,379,379). Same reject reasons (containment-under-motion,
  min-size 65×62 on p0068, text-cover) and same 9-panel watermark skips. ✔
- **Expected non-bug divergence:** crop-eval lines on scenes 2/5/10/25
  (0-based 1/4/9/24) drop out vs v2 — those panels now render inside the
  restored grid composites, so the sequential crop path never evaluates
  them. p0003/p0043 still crop because grid templates pick 3 of the scene's
  panels and these render in the remaining sequential cuts. Every missing
  line is on a restored-grid scene; no other divergence. **VERIFIED, not a
  bug.**
- Note: 3 of the 5 accepted crops land on grid scenes' sequential cuts, so
  only scenes 5, 12, 21 (0-based) show crop-attributable pixel changes —
  the scoped benchmark uses those; a 1,5,9,12,21 scoping was also run
  (FAIL, same +irrelevant_panel veto, score -2) before narrowing.

### Score table (stable review, majority-of-3; baseline = stable_base.json)

| fault type | baseline | v2 (T2) | **v3 (T3)** | Δ v3 vs base |
|---|---|---|---|---|
| phone_readability (M) | 2 | 0 | **0** | **-2 (target)** |
| unreadable_text (L) | 1 | 1 | **0** | **-1 (target)** |
| cropped_content (M) | 4 | 3 | 5 | +1 ⚠ global veto — but see below |
| irrelevant_panel (H) | 12 | 11 | 11 | -1 global; +1 on scene 21 ⚠ scoped veto |
| watermark (H) | 4 | 5 ⚠ | **4** | **0 — v2 veto GONE** |
| empty_screen (M) | 1 | 2 ⚠ | **1** | **0 — v2 veto GONE** |
| missing_payoff (H) | 5 | 5 | 5 | 0 |
| weak_hook (H) | 1 | 0 | 0 | -1 |
| panel_reuse (L) | 1 | 1 | 1 | 0 |
| **weighted global** | **126** | 117 | **113** | **-13 (best arm yet) but FAIL on veto** |
| **weighted scoped (5,12,21)** | **12** | — | **15** | +3 FAIL |

### Did v2's vetoes revert? YES — both.

- **watermark 4→5 (v2) → 4→4 (v3).** The scene-1 (0-based) t=40.5 frame
  now renders the baseline's 3-panel grid instead of v2's full-frame p0002
  banner; the magnified corner watermark fault is gone
  (`samples/v3/eval/scene1wm_base_t40.5.png` vs `scene1wm_t3_t40.5.png` —
  same composite).
- **empty_screen 1→2 (v2) → 1→1 (v3).** The scene-3 t=94.8 frame is
  **byte-identical** to baseline (md5 327e2c45… both) — the solo-pendant
  sequential framing is gone with the restored composite.

### Veto anatomy (why FAIL is noise, stated for the record — not softened)

- **Global veto, cropped_content 4→5.** T3's two new cropped_content faults:
  scene 3 t=94.8 — the frame is **byte-identical to baseline** (md5-equal);
  the reviewer relabeled baseline's own irrelevant_panel complaint on this
  scene into an irrelevant_panel + cropped_content pair. Scene 20 t=636.0 —
  frames visually identical (`scene20new_*_t636.0.png`, same bubble filling
  frame, sub-second Ken Burns phase offset only); no crop fired on either
  scene, and no pixel T3 controls differs. Meanwhile 2 of baseline's 4
  cropped_content faults (scenes 0, 7) disappeared — net movement is
  reviewer churn on unchanged content.
- **Scoped veto, irrelevant_panel 1→2 (scene 21, 2/3 votes).** Scene 21
  t=657.3: baseline shows the full staircase panel; T3 (AND v2 — frames
  `scene21crop_t2_t657.3.png` / `scene21crop_t3_t657.3.png` are visually
  identical) shows the p0126 crop framing the "THANKS TO YOU, I'M SURE"
  bubble. This IS crop-attributable — same crop, same rect as v2 — but
  v2's review did not flag it and v3's did (2/3 votes, sub-noise-floor).
  The p0126 crop is the one accepted crop that trades art context for text
  size on a frame with no sub-threshold readability fault; it is the
  weakest of the 5.
- Score deltas on the two identical-video reviews (t2: 28 faults, t3: 27)
  differ by exactly this churn — consistent with the ±12 single-pass /
  residual majority-vote noise documented in gap-011.

### Scene-5 readability win: PRESERVED

p0024 crop fires with the identical rect; the True Dragon Sword card is
full-frame legible (`scene5win_t3_t143.1.png` vs squint-size baseline);
baseline's scene-5 phone_readability fault is absent from T3's review.
phone_readability + unreadable_text: baseline 3 → **0** (v3 is the first
arm to clear ALL target faults).

### Acceptance criteria (gap-009)

| criterion | result |
|---|---|
| phone_readability + unreadable_text 4 → ≤1 | **MET** — 3 → 0 on this baseline (best of all arms). ch3 unmeasured. |
| no new cropped_content from punch-ins | **MET on evidence / NOT MET by the binding count** — global 4→5, but neither new fault is on a crop scene and one sits on a byte-identical frame; zero cropped_content on any crop-fired scene. |
| quality guard: punch-in respects bubble boundaries | **MET** — crop decisions unchanged from v2; scene-20 guard frame still identical to baseline (`scene20guard_*`, md5s differ only by motion phase); no half-cut bubble anywhere. |

### Wall times

Render: ~9.5 min (grids restored, fewer sequential cuts than v2's 15.5).
Stable review ×3: ~13 min (one re-run needed: first review invocation
omitted `--workdir _work_t3`, review_video found no c*.mp4 spans and
vision-reviewed zero scenes — discarded, re-run with the correct workdir).
Benchmarks + frames: <2 min. Within budget.

### Frame evidence (`samples/v3/eval/`)

- `scene5win_{base,t3}_t143.1.png` — the win, preserved: info card
  full-frame legible.
- `scene1grid_{base,t3}_t44.0.png` + `scene1wm_{base,t3}_t40.5.png` —
  scene-1 grid restored; v2's watermark-magnifying banner gone.
- `scene3empty_{base,t3}_t94.8.png` — byte-identical (md5-equal): v2's
  empty_screen site reverted AND the site of T3's "new" cropped_content —
  the clearest single frame showing the global veto is relabeling noise.
- `scene20new_{base,t3}_t636.0.png` — the other new cropped_content:
  visually identical bubble close-up in both arms.
- `scene21crop_{base,t2,t3}_t657.3.png` — the scoped veto: p0126 crop
  (identical in v2 and v3); baseline full staircase panel vs cropped
  bubble. The one genuinely crop-attributable judgment call.
- `scene20guard_{base,t3}_t628.5.png` — v1's worst fault frame, still
  clean.

### Caveats — not measured in this stage-scoped run

- **ch3 measurement still outstanding — mandatory before adoption.** The
  gap's motivating faults (system-box-heavy scenes, right-column MEDIUM
  layout) live in ch3; golden ch2 had only 3 target faults, all now
  cleared. The FULL end-to-end run required by benchmark config before any
  adoption also remains outstanding.
- The two binding vetoes could flip on a re-review: both global veto
  faults sit on identical/near-identical pixels, and the scoped veto is a
  2/3-vote fault on a frame v2 rendered identically without being flagged.
  If the orchestrator wants a decision harder than reviewer noise, either
  (a) re-run stable review on BOTH base and T3 cuts with more passes, or
  (b) drop the p0126 crop (raise MIN_TEXT_COVER or require a
  sub-threshold readability trigger on the scene) and re-measure — that
  one change would likely clear the scoped veto legitimately.
- Stacking with exp-003 (panels_clean) still unmeasured (8 watermark-
  skipped panels would become crop-eligible).
- TTS re-synthesized (clip-cache key defeats mp3 pre-seeding), no final
  encode: loudness/final-duration/encode vetoes unexercised (review-cut
  781.15 s vs baseline 781.51 s, -0.05%, fine).
- v2's metrics.json/metrics_global.json overwritten with v3 numbers per
  the eval plan; v2 numbers preserved in the tables above.
- Golden project untouched: all inputs copied into `round2-eval/`
  (arm_t3.json/.ocr.json/.textboxes.json cloned from the arm_t2 set),
  no file under `output/the-world-after-the-fall-ch2/` read-modified.

## v4 evaluation — round2-eval (Arm T4, `--text-aware` + readability trigger gate)

**Verdict (binding, benchmark.py): global FAIL (veto) / scene-scoped PASS.
Global: score 126 → 114 (delta -12, second-best after v3's 113) but VETO
cropped_content 4→5 — again on scenes where no crop fired and the frames are
pixel-identical to baseline. Scoped (`--scenes-changed 5` — the ONLY scene
with crop-attributable pixel change, derivation below): 2 → 0 (delta -2),
no vetoes → PASS. A wider scoped probe over both crop-log scenes
(`--scenes-changed 5,9`): PASS 7→5 (-2). Net read: FAIL-leaning-adopt — v4
did exactly what it promised (v3's scoped veto crop p0126 is gone, scene 21
renders baseline-identical), every remaining veto fault sits on
baseline-identical pixels, and the target metric is fully cleared — but the
binding global verdict is FAIL and I do not soften it.**
(`metrics_global.json` = global; `metrics.json` = binding scoped run.)

### Crop log verification — the v4 contract

- **Plans match baseline: VERIFIED.** `arm_t4_render.log` "smart layout"
  lines are diff-identical to `arm_t3_render.log` (= baseline): grids on
  1-based scenes 2, 5, 10, 19, 25 (0-based 1, 4, 9, 18, 24), same panel
  picks.
- **Accepted crops: 1 logged line, not the expected 2 — explained, and the
  expectation was subtly wrong.** The log shows exactly one accept:
  `scene 6: text-aware crop on p0024.png -> (220, 1316, 423, 379)` (the
  scene-5 0-based win, identical rect to v1/v2/v3). **p0043 cannot log an
  accept line in ANY planner-split arm**: scene 10 (1-based) renders as a
  grid composite (`grid, 3 of 3 panels: p0040, p0042, p0043`), and grid
  panels never reach the sequential crop path — v3's log has no p0043
  accept line either (v2's did only because v2's arm lost that grid). The
  smoke_v3/v4 "accepted set {p0024, p0043}" is the CROP-ELIGIBILITY sweep
  over all panels at both zmax values (4 accepted crop-evals / 136 no-crop /
  8 watermark-skips), not a render-log prediction. Render-log ground truth
  v4: 1 accept (p0024), matching the sweep ∩ sequential-path panels.
- **No p0126 crop: VERIFIED** — no accept line on scene 22 (1-based);
  v3 had `scene 22: text-aware crop on p0126.png -> (281,728,379,379)`.
- **v3→v4 log diff exactly matches the trigger gate**: dropped lines are
  the p0060 + p0126 accepts and the p0006/p0022/p0025/p0090/p0129
  reject-log lines (now silent no-triggers, all 31-38px median — nothing
  was wrong, so nothing logs). No other divergence.
- **Pixel-level confirmation of crop-scene set**: per-panel sub-clips
  compared t4-vs-t3-vs-base — scene 12 slot p2 (p0060): t4-vs-t3 diff
  58.7, t4-vs-base **0.0** (crop removed, baseline framing restored);
  scene 21 clip: t4-vs-base 0.02 (identical, p0126 crop gone); scene 9
  grid clip: t4-vs-base ≤0.9 (motion-phase only, no layout/crop change).
  Therefore the only scene with crop-attributable pixels is **scene 5
  (0-based)** → that is the binding `--scenes-changed` set; 5,9 run as a
  probe because scene 9's grid contains p0043 (PASS there too).

### Score table (stable review ×3, majority-of-3; baseline = stable_base.json)

| fault type | baseline | v3 (T3) | **v4 (T4)** | Δ v4 vs base |
|---|---|---|---|---|
| phone_readability (M) | 2 | 0 | **0** | **-2 (target)** |
| unreadable_text (L) | 1 | 0 | 1 | 0 (scene 7, mirrors baseline's own fault) |
| cropped_content (M) | 4 | 5 ⚠ | 5 | +1 ⚠ global veto — same two relabel sites as v3, see below |
| irrelevant_panel (H) | 12 | 11 | 11 | -1; **scene-21 scoped veto from v3 GONE** |
| watermark (H) | 4 | 4 | 4 | 0 |
| empty_screen (M) | 1 | 1 | 1 | 0 |
| missing_payoff (H) | 5 | 5 | 5 | 0 |
| weak_hook (H) | 1 | 0 | 0 | -1 (noise) |
| panel_reuse (L) | 1 | 1 | 1 | 0 |
| **weighted global** | **126** | 113 | **114** | **-12, FAIL on veto** |
| **weighted scoped (scene 5)** | **2** | — | **0** | **-2 PASS, no vetoes** |

### Veto status vs v3

| v3 veto | v4 status |
|---|---|
| Scoped: irrelevant_panel 1→2 on scene 21 (the p0126 crop — v3's one genuinely crop-attributable fault) | **GONE.** p0126 no longer triggers (36.1px ≥ 30px gate); scene-21 clip is pixel-identical to baseline (mean abs diff 0.02); T4's scene-21 review shows only the baseline-inherited missing_payoff. The scoped run has zero vetoes. |
| Global: cropped_content 4→5 | **PERSISTS — but 100% reviewer relabeling on baseline-identical pixels.** T4's five cropped_content: scenes 2, 6, 7 mirror baseline's own faults verbatim; the two "new" ones are scene 3 (frame content: aftermath-dialogue mismatch — a narration-relevance complaint double-labeled irrelevant_panel + cropped_content on a scene where no crop ever fired in any arm; T3's review did the same relabel) and scene 20 ("speech bubble cropped top/bottom" — the baseline's own full-bleed bubble panel, clips differ by ≤1.4 mean abs diff = motion phase; baseline review labels this same frame class watermark-only). Meanwhile baseline's scene-0 cropped_content dropped out. This is the same churn documented in v3 and gap-011. |

Crucially: v4 controls strictly fewer pixels than v3 (1 crop vs 5), and
every fault that moved sits outside those pixels. The global FAIL is
noise-floor arithmetic, not a quality regression — but it is the binding
number.

### Scene-5 readability win: PRESERVED

p0024 crop fires with the identical rect (220,1316,423,379); the True
Dragon Sword card is full-frame legible
(`samples/v4/eval/scene5win_t4_t143.3.png`); baseline's scene-5
phone_readability fault is absent from T4's review; the scoped benchmark
over scene 5 is a clean 2→0. phone_readability + unreadable_text: baseline
3 → 1, and the residual UT (scene 7) mirrors a baseline fault on a scene
whose frames are baseline-identical. On crop-touched pixels: 3 → 0.

### Acceptance criteria (gap-009)

| criterion | result |
|---|---|
| phone_readability + unreadable_text 4 → ≤1 | **MET** — 3 → 1 on this baseline (v3's 0 vs v4's 1 differs only by the scene-7 UT relabel on identical pixels). ch3 unmeasured. |
| no new cropped_content from punch-ins | **MET on evidence / NOT MET by the binding global count** — 4→5, but neither new fault is on the one crop scene; both sit on frames measured pixel-identical to baseline (diffs ≤1.4 motion phase). Zero cropped_content on the crop-fired scene. |
| quality guard: punch-in respects bubble boundaries | **MET** — the single accepted crop (p0024, an info card) contains all 8 text boxes; no reviewer-confirmed half-cut bubble anywhere; the v1 fault frames remain clean. |

### Wall times

Render: started 22:56, c-files complete ~14:06 next day (host was
suspended/contended overnight — process etime 15h, but per-file mtimes show
TTS ~19 min and scene renders in normal ~10 min bands; not a pipeline
regression). Stable review ×3: ~12 min. Benchmarks + frames: <2 min. No
budget concerns attributable to v4 (the trigger gate only removes work).

### Frame evidence (`samples/v4/eval/`)

- `scene5win_{base_t143.1,t4_t143.3}.png` — the win, preserved: info card
  full-frame legible vs squint-size baseline.
- `scene21_{base_t657.3,t4_t657.5}.png` — v3's scoped-veto site: T4 now
  shows the same full staircase panel as baseline (p0126 crop gone; clip
  diff 0.02).
- `scene12notrig_{base_t343.3,t3_t343.8,t4_t343.8}.png` — the trigger gate
  in one triptych: t3 shows the p0060 bubble punch-in, t4 shows baseline's
  full panel (sub-clip t4-vs-base diff 0.0).
- `scene9grid_{base,t4}_t250.png` — scene 9 (0-based) grid intact in both
  arms; p0043 lives here, hence no sequential crop.
- `scene3veto_{base,t4}_t88.png` — "new" cropped_content site #1:
  visually identical frames.
- `scene20veto_{base,t4}_t636.png` — "new" cropped_content site #2:
  same full-bleed bubble in both arms.

### Caveats — not measured in this stage-scoped run

- **ch3 measurement still outstanding — mandatory before adoption.** The
  gap's motivating faults (system-box-heavy scenes, right-column MEDIUM
  layout) live in ch3. The 30px trigger was validated only against golden
  ch2's separation (14.7/20 vs 34-38px); ch3 must confirm genuinely-tiny
  boxes still trigger and already-legible ones don't.
- **The full end-to-end run required by benchmark config before adoption
  also remains outstanding** (TTS/loudness/final-encode vetoes
  unexercised; review-cut duration 781.27 s vs baseline 781.51 s, -0.03%,
  fine).
- The global cropped_content veto has now appeared in v3 AND v4 on
  baseline-identical frames — the binding global verdict is pinned to the
  reviewer noise floor for any experiment this small. If the orchestrator
  wants a global PASS harder than noise, re-review base+T4 with more
  passes; on current evidence one more code iterate cannot fix it because
  no controlled pixel is at fault.
- Stacking with exp-003 (panels_clean) unmeasured (8 watermark-skipped
  panels would become crop-eligible under the trigger gate).
- v3's metrics.json/metrics_global.json overwritten with v4 numbers per
  the eval plan; v3 numbers preserved in the tables above.
- Golden project untouched: all inputs cloned inside `round2-eval/`
  (arm_t4.json/.ocr.json/.textboxes.json copied from the arm_t3 set); no
  file under `output/the-world-after-the-fall-ch2/` read-modified.

## ch3 measurement + full-run gate (resumed)

**Verdicts (binding, benchmark.py):**

- **ch3 global: FAIL** — score 77 → **78** (delta +1). VETO: irrelevant_panel
  6→7. (`ch3_eval/metrics_ch3_global.json`)
- **ch3 scene-scoped (`--scenes-changed 0,3,8,18,19,25` — the crop-fired
  scenes, derivation below): FAIL** — score 30 → **22** (delta **-8**,
  improved) but VETO: new fault type phone_readability (0→1, scene 18).
  (`ch3_eval/metrics_ch3_scoped.json`)
- **Full end-to-end run (golden ch2, round2-eval Arm T4 finalized): PASS** —
  score 127 → **108** (delta **-19**), **zero vetoes**, media vetoes
  exercised and clean. (`ch3_eval/metrics_full_global.json`; scoped scene-5
  probe also PASS 2→0, `ch3_eval/metrics_full_scoped5.json`.)

Net read: the v4 trigger gate does exactly what the gap card asked on ch3 —
5 genuinely-tiny text sites punch in to full-frame legibility, cropped_content
IMPROVES 4→2, the scoped weighted score improves 30→22, and the mandatory
full e2e gate passes outright — but the two binding ch3 verdicts are FAIL and
are reported as such. Attribution below shows the global veto sits entirely
on byte-identical pixels (gap-015 pattern, md5 evidence) and the scoped veto
is a single 3/3 fault on a crop that renders the flagged text ~4× LARGER
than baseline (a prominence complaint, not a size regression) — but neither
verdict is softened.

### How this session resumed

Previous evaluator session was killed mid-base-render. `_work_base` had
scenes 0-7 complete plus a truncated `c007.mp4` (moov atom missing —
killed mid-write); the truncated file was deleted and the render resumed via
`--workdir` (scenes 0-7 reused from cache, log `ch3_eval/base_resume.log`).
Invocation reconstructed from prototype.md §"Exact evaluator commands" +
existing clip properties (c000.mp4 probed 2560×1440 → `--res 1440p`, grid
composites in workdir → `--layout smart`; music_plan resolves moods from the
ch3_eval dir copies, no --music/--no-music flag needed):

- base: `panel_render.py ch3_base.json --res 1440p --layout smart
  --workdir _work_base --stop-after review-cut`
- arm: same + `--text-aware`, `--workdir _work_arm` (fresh; TTS
  re-synthesized — clip-cache key defeats mp3 pre-seeding, same caveat as
  every prior arm; review-cut durations 720.67 s base vs 720.59 s arm,
  -0.011%).

Golden projects protected: everything ran inside `ch3_eval/` on the
pre-copied script/ocr/textboxes/panels/music inputs; `find output/… -newermt
today` returns empty for BOTH ch2 and ch3 project dirs.

### Trigger-fire audit (the ch3 acceptance question: do genuinely-tiny boxes trigger and legible ones not?)

Threshold at 1440p: trigger = 40.0 px rendered median (30px@1080p scaled),
target = 53.3 px. Audit = full sidecar sweep of every referenced panel's
median line height under the default blur_bg fit vs the render log.

**Accepted crops (6 instances, 5 distinct panels, 0-based scenes
{0, 3, 8, 18, 19, 25}):**

| panel | median @default | scenes (0-based) | outcome |
|---|---|---|---|
| p0086 | 27.4 px | 0, 25 | crop (120,1008,379,379) — hook + reprise |
| p0010 | 29.7 px | 3 | crop (172,0,391,379) |
| p0026 | 35.8 px | 8 | crop (19,0,379,379) |
| p0062 | 33.3 px | 18 | crop (302,308,379,379) |
| p0064 | 20.6 px | 19 | crop (81,0,426,379) |

**Rejected/guarded (all logged, no silent failures):** p0091 (15.9 px,
containment-under-motion ×4 scenes), p0042 (20.6 px, containment), p0076
(29.7 px, containment), p0065 (32.9 px, containment), p0011 (37.1 px,
min-size 196×196), p0032/p0016 et al. inside grid scenes (never reach the
seq crop path). **Nearest non-triggers: p0053 40.7 px, p0025 40.9 px, p0049
42.3 px, p0081 54.4 px — all correctly left alone.** Every panel below the
trigger either cropped or was rejected by a v2 guard with a logged reason;
every panel above it was untouched. **Criterion MET: the gate separates
genuinely-tiny from already-legible with no misfires on ch3.**

Layout parity (v3 contract) verified: base and arm plan identical grids
(1-based scenes 6* — see below — 10, 14, 16, 18; arm log additionally shows
grids on 6 and 8 in both arms — the "smart layout" line sets are identical
between base_resume.log and arm_render.log for the scenes both rendered
fresh). Per-scene pixel probe: only scenes 0, 3, 8, 18, 19, 25 diverge
(MAD 57-68 on crop cuts); every other scene ≤2.5 MAD (motion phase) and
scenes 2/4/6/7/10/11/12/15/17/23/24/26 are 0.00-identical.

### Score table — ch3 (stable review ×3, majority-of-3)

| fault type | ch3 base | ch3 arm (v4) | Δ |
|---|---|---|---|
| phone_readability (M) | 2 (s7, s24) | 2 (s7, s18) | 0 — s24 relabeled away, s18 NEW on crop scene ⚠ scoped veto |
| unreadable_text (L) | 2 (s0, s23) | 2 (s23, s24) | 0 — **s0 fixed by p0086 crop**, s24 gained a relabel |
| cropped_content (M) | 4 | **2** | **-2 (the gap's own guard IMPROVES)** |
| irrelevant_panel (H) | 6 | 7 | +1 ⚠ global veto — see attribution |
| watermark (H) | 4 | 4 | 0 |
| missing_payoff (H) | 1 | 1 | 0 |
| static_scene (M) | 1 | 1 | 0 |
| weak_hook (H) | 1 | 1 | 0 |
| panel_reuse (L) | 1 | 1 | 0 |
| **weighted global** | **77** | **78** | +1 FAIL (veto) |
| **weighted scoped (crop scenes)** | **30** | **22** | **-8, but FAIL on new-PR veto** |

### Veto attribution (stated for the record — verdicts NOT softened)

- **Global veto, irrelevant_panel 6→7.** Base set: scenes 0,1,5,8,21,22.
  Arm set: 0,1,5,**6**,21,22,**24**; base's s8 ip is GONE (the p0026 crop
  fixed the cropped-eyes framing the base reviewer flagged — a crop win).
  The two arm additions are on scenes whose clips are **byte-identical**
  between arms: scene 6 (grid) — frame at the flagged t: base/arm clip-frame
  md5 a632ed8c67 == a632ed8c67, MAD 0.000; scene 24 — clip-frame md5
  e81dbcf19a == e81dbcf19a, MAD 0.000 (c024 full-clip PSNR ∞; the small
  full-cut frame diffs at those absolute timestamps are sub-second concat
  offset, not content). This is the gap-015 pattern — per-type vetoes firing
  on reviewer relabeling of baseline-identical frames — 4th consecutive
  occurrence for this experiment (ch2 v3, v4, now ch3 global).
- **Scoped veto, phone_readability 0→1 (scene 18, 3/3 votes).** This one IS
  on crop-controlled pixels: the p0062 punch-in fires (33.3 px median →
  129 px in-crop) and both flagged timestamps land on p0062 windows.
  BUT the flagged text ("What does 'tower within tower…' mean?" handwritten
  side-note) renders **~3.9× larger in the arm than in the base** (fit 0.98
  → 3.80; the note's boxes go 15.7-33.3 px → 60.8-129.2 px) — see
  `samples/ch3/s18reg_{base,arm}_t481.7.png`: the base shows the note at
  squint size, unflagged; the arm shows it large and legible, flagged 3/3.
  The complaint is prominence-induced (newly-readable scrawly handwriting
  attracts a size complaint), not a rendering regression. Formally binding
  as a new fault type in scope; factually the pixels got strictly more
  readable.

### Full end-to-end gate (benchmark config `full_run_required_before_adoption`)

Target: golden-ch2 round2-eval Arm T4 (the v4 arm the stage-scoped verdict
was measured on), finalized from its cached `_work_t4` clips without
`--stop-after` (mirrors the exp-005 full-run recipe: same flags
`--res 1080p --no-music --layout smart --text-aware`, log
`round2-eval/final_t4.log`) → `round2-eval/arm_t4.mp4`. Stable review ×3 →
`round2-eval/stable_t4_full.json` (27 confirmed, 4 unconfirmed-high) vs the
existing full-run baseline `stable_base_full.json` (32 confirmed).

| fault type | base full | T4 full | Δ |
|---|---|---|---|
| irrelevant_panel (H) | 12 | 10 | -2 |
| missing_payoff (H) | 5 | 4 | -1 |
| phone_readability (M) | 1 | **0** | **-1 (target)** |
| static_scene (M) | 1 | 0 | -1 |
| cropped_content (M) | 4 | 4 | 0 |
| watermark (H) | 5 | 5 | 0 |
| unreadable_text (L) | 2 | 2 | 0 |
| empty_screen (M) | 1 | 1 | 0 |
| panel_reuse (L) | 1 | 1 | 0 |
| **weighted** | **127** | **108** | **-19 PASS, zero vetoes** |

**Media vetoes (first time exercised for exp-009):**

| veto | base | T4 | limit | status |
|---|---|---|---|---|
| final duration | 781.517 s | 781.300 s | drift ≤5% | **-0.028% OK** |
| loudness | -14.0 LUFS | **-13.99 LUFS** | [-17, -11] | **OK** |
| encode | ok | **ok** (3.2 Mbps, faststart, title card + popup applied) | must succeed | **OK** |
| scene count | 25 | 25 | match script | **OK** |

TTS/loudness/final-encode — the outstanding caveats from every stage-scoped
round — are now all measured and clean. Scoped probe on the one
crop-changed scene (5): 2→0 PASS.

### Acceptance criteria (gap-009) — ch3 verdict

| criterion | result |
|---|---|
| phone_readability + unreadable_text → ≤1 | **NOT MET on ch3 numerically** (4 → 4). Decomposition: of base's 4, two sit on pixels the feature cannot control (s7 grid right-column PR — the layout interaction, explicitly out of v4 scope; s23 UT — crop correctly rejected on containment). Of the two it could touch: s0 UT **fixed** (p0086 crop), s24 PR relabeled (p0081 at 54.4 px correctly no-trigger — complaint above threshold). Arm added s18 PR on crop pixels (prominence complaint on 3.9×-enlarged text). On crop-touched pixels: 1 → 1. |
| genuinely-tiny boxes still trigger; already-legible don't | **MET** — clean separation, zero misfires (audit table above). |
| no new cropped_content from punch-ins | **MET** — 4→2 on ch3 (improves); 4→4 on the full run; zero cropped_content on any crop-fired scene in either. |
| punch-in respects bubble boundaries | **MET** — 6 containment-under-motion rejections logged incl. p0091 (4 scenes); no reviewer-confirmed half-cut bubble on any crop scene in ch3 or the full run. |

### Wall times

ch3 base resume: ~26 min (scenes 8-26 + mix; 0-7 cached). ch3 arm: ~36 min
(fresh TTS + detection + 27 scenes). Stable reviews ×3, both arms
concurrent: ~43 min. Full T4 finalize (cached clips + SFX/ambience + popup +
title + CRF18-slow encode): ~60 min. T4 full stable review ×3: ~17 min.
Benchmarks + frames: <5 min. No budget concerns; the trigger gate only
removes render work.

### Frame evidence (`samples/ch3/`)

- `s0win_{base,arm}_t31.5.png` — hook scene: base shows the "IT'S SLOWLY
  CHIPPING AWAY" strip with the bottom dialogue cut off mid-sentence
  (base's s0 UT+cropped_content faults); arm punches into the bubble,
  full-frame legible — both base faults gone from the arm review.
- `s3_o12.0_{base,arm}.png` — p0010 win: "YOU RAT-LIKE BASTARD!" bubble
  squint-size → full-frame.
- `s19_o3.0_{base,arm}.png` — p0064 win: "One does not age in the Tower…"
  rules text tiny-over-blur → full-frame legible (the exact system-box
  class the gap card was opened for).
- `s18reg_{base,arm}_t481.7.png` — the scoped-veto site: arm text is
  LARGER than base at the flagged timestamp; judge for yourself.
- `s6clip_o4.3_{base,arm}.png` + `s24clip_o23.5_{base,arm}.png` — the two
  global-veto sites: **md5-identical frame pairs** (a632ed8c67 /
  e81dbcf19a) — the veto faults sit on pixels no arm controls.
- `s8win_{base,arm}_t216.8.png` — p0026 crop removing base's
  cropped-eyes/irrelevant_panel complaint (MAD 1.8, reframed).
- `s7grid_{base,arm}_t182.6.png` — the residual s7 grid PR fault:
  byte-identical frames (md5 c6eaabac85 both) — layout-owned, not
  crop-owned.

### Honest caveats

- The ch3 PR+UT headline (4→4) hides that half the residual faults are
  grid-cell text (s7) — the `_panel_min_h`/layout interaction remains
  unaddressed by design since v3 split it out; it needs its own gap if the
  right-column MEDIUM class is to be fixed.
- The s18 scoped veto: formally crop-attributable, evidentially a
  prominence complaint on enlarged text. A 5-pass re-review of ch3 or a
  human look at `s18reg_*` would decide it harder than 3 passes can.
- ch3 base/arm TTS waveforms differ (clip-cache re-synth) — same caveat as
  all prior arms; durations within 0.011%.
- Full-run PASS is on golden ch2 where v4 fires exactly 1 crop; ch3 (6
  instances) exercises the mechanism far harder and is where both FAILs
  live.
- ch2 v4's metrics.json/metrics_global.json were overwritten by this
  session's benchmark invocations per the standing eval-plan convention;
  all four new metric files are preserved under `ch3_eval/metrics_*.json`
  and v4's ch2 numbers remain in the tables above.

## gap-015 re-score (matched-vetoes + frame-hash) — 2026-09-12

Payoff step for gap-015: the ARCHIVED ch3 stable reviews
(`ch3_eval/stable_ch3_base.json` / `stable_ch3_arm.json`, frozen per user
decision 2026-09-09 — no new reviews, no code iteration) re-scored under
`--matched-vetoes` WITH the frame-hash exemption. The exemption needs both
review-cut videos; the originals were deleted, so both arms were
re-rendered deterministically from the archived scene plans in
`/tmp/opencode/exp009_rescore/` (golden `output/the-world-after-the-fall-ch3`
untouched — `find -newermt` empty for both ch2 and ch3 project dirs; all
inputs were the ch3_eval copies, verified md5-identical scene plans and
textboxes sidecar identical modulo mtime fields).

### Re-render provenance / determinism

Resumed the interrupted attempt's workdirs: `_work_base` had 17 complete
clips + truncated c017 (moov missing, deleted), `_work_arm` had 9 + truncated
c009 (deleted); both resumed via `--workdir` with the same invocations as the
original eval (base: `--res 1440p --layout smart`; arm: `+ --text-aware`;
`--stop-after review-cut`; logs `{base,arm}_rerender.log`).

- **Arm crop/layout decisions: IDENTICAL to the original `arm_render.log`**
  (diff of all `text-aware crop` / `smart layout` lines is empty modulo
  scenes served from cache and one transient). Same 6 accepted crops
  (p0086 ×2, p0010, p0026, p0062, p0064), same rejections with same reasons.
- **Transient:** first arm resume hit a one-off ffmpeg RuntimeError on the
  scene-10 grid encode (fell back to seq for that attempt). c009 was deleted
  and the arm re-resumed; second attempt rendered the **same grid plan**
  (p0029/p0033/p0034) cleanly. Final workdir contains the grid clip.
- Durations: review cuts 720.73 s base / 720.71 s arm (original run: 720.67 /
  720.59 — ±0.06 s drift from TTS re-synthesis; clip-count and per-scene
  durations match to ≤0.03 s). 5 scene clips byte-identical across arms
  (c007/c012/c014/c026 + c... as before); TTS waveforms differ run-to-run
  (known clip-cache re-synth caveat), which is why frame-level md5s vs the
  archived PNG samples don't byte-match, but re-extracted evidence frames
  agree with the archived samples at MAD 0.0–2.6 (same content, sub-second
  encode phase).

### Verdict table (old default arithmetic vs matched-vetoes vs mv + frame-hash)

| scope | default (binding, 09-09) | mv, no hash | **mv + frame-hash (this run)** |
|---|---|---|---|
| ch3 global | FAIL 77→78, veto irrelevant_panel (6,7) | FAIL 77→78, veto irrelevant_panel matched-net (6,7) | **FAIL 77→78 — vetoes: NONE (both added ip pairs hash-exempt); fails purely on score +1** |
| ch3 scoped (0,3,8,18,19,25) | FAIL 30→22, vetoes new-type PR + worsened PR | FAIL 30→22, same vetoes | **FAIL 30→22 (-8) — s18 phone_readability veto SURVIVES (not pixel-identical)** |

Files: `rescore-mv/metrics_ch3_global_mv_hash.json`,
`rescore-mv/metrics_ch3_scoped_mv_hash.json`; evidence frames in
`rescore-mv/samples/`.

### Exempted vetoes (with hash evidence)

The global irrelevant_panel 6→7 veto is **fully suppressed**: the arm's two
added (scene,type) pairs both sit on frames pixel-identical to baseline, and
matched-pair netting additionally credits the removed s8 pair
(net_after_exemptions = **-1**):

- **scene 6, irrelevant_panel, t=151.4 (2/3)** — arm frame md5
  `819e05e59dedbe086006878d7a301b69` found in baseline window
  [148.4, 154.4]. Independent check: same-timestamp PNG extraction gives
  **identical file md5 in both arms** (`e5405f6e…` == `e5405f6e…`, MAD 0.000)
  — `samples/s6_exempt_{base,arm}_t151.4.png`.
- **scene 24, irrelevant_panel, t=642.1 (3/3)** — arm frame md5
  `83397888dc3de0e22adb3eff7f1925df` found in baseline window
  [639.1, 645.1]. Same-t extraction differs slightly (MAD 1.44) — that is
  the sub-second concat offset the ±3 s hash window exists to absorb; the
  exact frame exists in the baseline within the window —
  `samples/s24_exempt_{base,arm}_t642.1.png`.
- Netted removal: baseline s8 irrelevant_panel (t=216.8) absent from the arm
  (fixed by the p0026 crop).

(Note: the report's earlier attribution quoted the flagged content by scene;
the actual added consensus pairs are s6 t=151.4 and s24 t=642.1 as recorded
in the archived review JSONs — those are what the exemption cleared.)

This is direct confirmation of the gap-015 diagnosis: the ch3 global veto
was 100% reviewer relabeling on baseline-identical pixels.

### Surviving veto (real, not noise)

- **scene 18, phone_readability, t=481.7, 3/3, scoped run.** NOT exempt:
  arm frame is genuinely different (MAD 66.4 vs baseline — it's the p0062
  crop punch-in). Frames `rescore-mv/samples/s18_surviving_{base,arm}_t481.7.png`:
  the base shows the full page with the handwritten "What does 'tower within
  tower…' mean?" side-note at squint size (unflagged); the arm shows the
  cropped window with the note ~3.9× larger and fully legible, flagged 3/3.
  **Real in the formal sense** (crop-owned pixels, correctly NOT exempted —
  the exemption is doing its job by refusing to clear it) but evidentially a
  prominence complaint: the pixels got strictly more readable and the
  reviewer objects to newly-prominent scrawl. Binding as recorded.

### Gate call for gap-009

**ch3 does NOT flip to PASS under mv+hash.**

- Global: the frame-hash exemption removes the only veto (validating
  gap-015's mechanism), but the verdict stays FAIL on score arithmetic —
  77→78 (+1). By design `--matched-vetoes` changes only veto arithmetic;
  the two noise faults on byte-identical frames still count +10 weighted in
  the score, which cancels the crop wins (cropped_content 4→2, s0
  unreadable_text fixed). A score-side counterpart to the hash exemption
  would flip this to PASS (-9), but that is a benchmark-design question for
  a future gap, not something this re-score may apply.
- Scoped: -8 improvement but the s18 veto survives on genuinely crop-changed
  pixels. Formally a hard FAIL.

**Recommendation: gap-009 is NOT adoption-eligible on this re-score.** The
previously-recorded full-e2e PASS on golden ch2 (127→108, zero vetoes)
remains valid and unweakened, and the mv+hash result materially strengthens
the case that ch3's global FAIL is noise-dominated — but the acceptance bar
requires the stage-scoped verdict to pass, and both ch3 verdicts still read
FAIL: one on score arithmetic that still counts hash-exempt noise, one on a
real (if debatable) 3/3 crop-attributable fault. Two honest paths forward,
both requiring decisions outside this evaluator's remit: (a) extend gap-015
with a score-side frame-hash exemption and re-gate (would clear the global
FAIL mechanically; the scoped s18 veto would still stand), or (b) a human
adjudication of the s18 prominence flag (`s18_surviving_*` frames) plus an
explicit orchestrator ruling on whether ch2-full-PASS + ch3-noise-attributed
FAIL meets the adoption bar. Until then: **FAIL-leaning-iterate, do not
adopt.**

### Caveats of this re-score

- Reviews are frozen (by user decision); everything here re-interprets the
  same 3-pass consensus — no new reviewer evidence.
- Re-rendered videos are decision-identical but not byte-identical to the
  deleted originals (TTS re-synth); frame-hash exemption compares the two
  NEW arms against each other, which is the correct pairing (both reviews
  were taken on arms rendered from these exact plans, and cross-arm pixel
  identity is a plan property, not a run property — confirmed by the s6
  same-md5 extraction and the 5 byte-identical clips).
- Nothing here measures TTS quality, final loudness, popup/title branding,
  or the finalize encode — the mandatory pre-adoption full run still owns
  those.
