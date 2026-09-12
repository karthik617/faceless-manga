# exp-014 evaluation report — content-aware Ken Burns (gap-014 + gap-006)

**Evaluator run date:** 2026-09-12. **Stage scope (render):** render_review_cut → review.
**Verdict (binding, benchmark.py default arithmetic): FAIL — score 107 → 107 (Δ 0),
VETO `cropped_content` 4 → 5.**
**Verdict (--matched-vetoes, gap-015): FAIL — veto suppressed (net_after_exemptions 0,
one pair frame-hash-exempt on pixel-identical frames), but Δ 0 = no strict score
improvement, so the score gate itself fails.**

Recommendation: **ITERATE** (see §8). The prototype demonstrably fixes the fault
class it targeted (a 3/3 face-crop healed, anchor/scroll containment holds on every
panel it plans), but chapter-wide it (a) introduces one pixel-real MEDIUM+HIGH
regression on an SFX tall panel it chose to scroll, and (b) its own deterministic
gate still exits 1 on the golden chapter because two tall panels' text unions are
too large for any legal window and fall back to violating center plans.

## 0. Isolation statement

Approach: **copy, not in-place.** Full golden project copied to
`/tmp/opencode/exp014_eval/proj` (final mp4s excluded); all renders/reviews ran
there. `output/the-world-after-the-fall-ch2/` untouched — script md5
`6306cceaadad21277fd37947dddd5d44` verified identical pre/post. The integrator's
`/tmp/opencode/exp014_render/proj` mini-bench no longer existed (tmp cleaned);
everything was re-run fresh. No `--apply-fixes` anywhere.

Note: this is a **fresh same-script A/B**, not a comparison against the pinned
`baseline_review.json` (that review was taken on the 1440p final encode with
music/branding; comparing across encode/res/branding regimes would be
apples-to-oranges). Both arms here: 1080p, `--no-music --no-sfx --no-branding
--no-title-card --stop-after review-cut`, same TTS (arm ON reuses nothing — TTS
re-synth per arm workdir; durations matched to 0.009%).

## 1. Independent containment-gate re-check (task #1)

The integrator's exact 47→0 run is **not reproducible bit-for-bit**: the
mini-fault bench (script + w###.json word timings) lived in tmp and is gone.
What I could do independently:

- **Replay of the archived plans**: re-scored the experiment's committed
  `kb_plans.json` (23 cuts, 4 fault scenes) + committed `smoke.textboxes.json`
  through `kb_smart.window_contains_boxes` on a 0.5 s beat grid (word-start/end
  beats not archived): **center 16 violations → smart 0**, and all 16 center
  violations sit on the scene-24/p0101 cut — exactly the site the integrator
  documented as carrying all 47 (their count is higher because word-boundary
  beats add ~3.6× more checks on that talky cut). Consistent, direction and
  site confirmed; headline number taken on trust with that caveat.
- **Fresh full-golden-chapter gate** (this eval's renders, real word timings):
  - baseline(center): tall cuts 27, beat checks 592, **95 violations** (exit 1)
    — all on scene 2/p0002 (40) and scene 6/p0024 (55).
  - kb-smart: **95 violations, exit 1 — identical.** Both panels' text unions
    (~1800 px tall on 690 px-wide panels) exceed what a fill ≥ 0.55 scroll or a
    1/1.08-zoom anchor window can cover; the planner logs the infeasibility and
    falls back to center (`text union exceeds the 1/1.08 window, center kept`).
    The fallback is "never worse than today" per the guard — but the card's
    deterministic criterion is *"for EVERY panel with h/w > 2.5 … contains 100%
    of the OCR text boxes"*, and that is **NOT met chapter-wide**. On the 4
    fault-scene subset the smart arm passes (0), so the integrator's gate claim
    is true on its stated scope but does not generalize.

## 2. A/B scores (task #2/#3)

review_stable.py, 3 passes each arm, majority ≥ 2/3. Weighted score
(high 5 / med 2 / low 1): **off 107, on 107, Δ 0.** Severity counts identical
(18 H / 7 M / 3 L both arms).

| type | off | on | Δ |
|---|---|---|---|
| irrelevant_panel (H) | 10 | 10 | 0 |
| cropped_content (M) | 4 | **5** | **+1 → default VETO** |
| watermark (H) | 4 | 4 | 0 |
| missing_payoff (H) | 4 | 4 | 0 |
| phone_readability (M) | 2 | 1 | −1 |
| empty_screen (M) | 1 | 1 | 0 |
| panel_reuse / ambience_absent / sfx_absent (L) | 1/1/1 | 1/1/1 | 0 |

**Matched-vetoes decomposition of the cropped_content +1** (metrics_ab_matched.json):
- pairs added: s3 t94.8 (2/3) — **frame-hash EXEMPT**, frames pixel-identical
  (frame_md5 507933…, verified independently: extracted PNGs md5-equal) → noise;
  s14 t422.5 (3/3) — **real, pixels differ** (see §4).
- pairs removed: s6 t175.7 (3/3) — **real improvement** (see §4).
- net after exemptions: 0 → veto suppressed, annotated. Verdict then rides on
  score alone: Δ 0 → still FAIL (no strict improvement).

**--scenes-changed:** framemd5 of all 25 scene clips: **25/25 differ** — the
smart planner touched at least one cut in every scene (112/167 cuts got a plan).
Scoped scoring over all 25 scenes = global minus the scene-less LOW items:
105 → 105, same veto. **Scene scoping cannot de-noise this experiment** because
nothing is untouched; that is itself a finding: the flag is chapter-wide, not
tall-panel-only (the anchor path fires on normal-aspect panels too, mean
|cy−0.5| = 0.035, max 0.069).

## 3. Gap criteria checklist (task #4)

| criterion | result |
|---|---|
| gap-014 #1: scenes 15/21/28 (golden review 0-based; script 16/22/29) cropped_content 3 → 0 | **NOT MEASURABLE as written.** Those scene numbers/timestamps belong to the exp-004 v2 re-scripted 35-scene bench (`/tmp/opencode/exp004_bench_v2` — deleted). Its tall merged panels (p0041@2697px, p0059@2072px, p0101@2510px) don't exist in the golden 25-scene project (golden p0041 is 690×1609, p0101 is 690×355). At the mapped timestamps t359.3/t513.4/t692.9 → this bench's scenes 12/17/22: **no cropped_content in either arm** (the fault surface itself isn't reproduced here). The archived-plan replay (§1) covers the p0101 bubble site deterministically: 16→0. Partially satisfied by proxy, not by the letter. |
| gap-014 #2: deterministic containment for every h/w>2.5 panel | **NOT MET chapter-wide** (95 violations both arms — infeasible-geometry center fallbacks on p0002/p0024). MET on every cut the planner actually plans. |
| gap-014 #3: no new cropped_content on normal-aspect scenes | **MET** — the one real new crop (s14) is on a tall panel (p0075, ar 2.9) in the new scroll mode; the other increase is pixel-identical noise. But note the spirit-of-the-guard issue: the new fault is on a panel the feature itself chose to handle. |
| gap-014 #4 / gap-006 #3: render wall ≤ 1.2x | **MET: 1.043x** (425.9 s → 444.1 s, full 25-scene render, cold face-detection included; text sidecar warm both arms). |
| gap-006 #1: golden cropped_content drop ≥ 50% | **NOT MET: 4 → 5 (+25%).** Even netting out the hash-exempt pair: 4 → 4 (0%). |
| gap-006 #2: sampled frames show zoom centers on faces/action | **Mixed.** s6 t175.7: yes — anchor keeps the face (evidence below). Anchor shifts are subtle (|cy−0.5| ≤ 0.069) and containment-correct. But s14 scroll picks a band of an SFX panel that reads worse than center letterboxing. |

## 4. The two pixel-real deltas (frame evidence, task #5)

All frames in `samples/eval/` (off = baseline arm, on = smart arm, same timestamps):

- **HEALED — s6 t175.7** (`off_t175.7.png` vs `on_t175.7.png`): baseline crops the
  character's face at the top (eyes gone; 3/3 "Face is cropped at top… shocked
  expression"); smart anchor on p0026 raises the window — eyes/expression visible,
  fault absent from all 3 ON passes. This is precisely the gap-006 fault class.
- **REGRESSED — s14 t422.5** (`off_t422.5.png` vs `on_t422.5.png`,
  `on_p0075scroll_t419.9/424.2.png` for sweep endpoints): p0075 (690×2000, SFX/impact
  art, one 76×76 text box) — baseline shows the letterboxed full panel (art whole,
  narrow); smart scrolls a fit-to-width band whose start frame is dominated by the
  giant SFX glyphs cut at frame edges. 3/3 reviewers flag cropped_content (M) +
  irrelevant_panel (H). The scroll is geometrically containment-correct (the text
  box stays inside) but *perceptually* worse: on text-free/SFX art panels,
  fit-to-width magnification crops art the letterbox showed whole. Mode-attributable
  regression, not noise.
- **Pixel-identical churn — s3 t94.8**: extracted frames md5-equal across arms;
  the 2/3 crop flag on the ON side is pure reviewer relabeling (gap-011/015 class).
- s23 t716.7 irrelevant_panel appears ON-only and s8/s19 items OFF-only — 2/3-vote
  churn on visually equivalent content (t716.7 frames differ only by a small
  Ken-Burns phase offset of the same panel); balanced ±HIGH churn, nets 0 in score.

## 5. Scroll audit (task #5)

20 scroll cuts. Speeds 25–150 src-px/s, all at/below the 150 cap; sweeps 117–735
src px over 3.0–5.7 s. No sub-perceptual creep (min sweep 117 px > the 40 px drop
threshold) and no sprint. Smoothstep easing; sweep endpoints sampled for p0075
show no judder. Speed-vs-narration mismatch: none egregious; the four cuts pinned
at exactly 150 px/s (s4, s8×2, s14, s16) are band-clamped — they cover the
band the duration allows, acceptable. The issue with scrolls is *content choice*
(SFX panels), not motion quality.

## 6. Wall time (task #6)

| arm | wall | notes |
|---|---|---|
| off | 425.91 s | 25 scenes, TTS synth + render |
| on | 444.09 s | + face detection (111 panels, model cached in ~/.cache/huggingface) + planning + scroll branches |
| **ratio** | **1.043x** | bound ≤ 1.2x → **MET** with margin (integrator's 1.09x claim conservative) |

Duration drift off→on: 781.13 s → 781.20 s (+0.009%). Scene count 25/25 both arms.

## 7. Honest caveats

- **Stage-scoped**: no music/SFX/branding/title-card, 1080p, review cut only —
  TTS regression, final loudness, encode, 1440p behavior (the production res)
  unmeasured. The mandatory pre-adoption full run must cover them. In particular
  the scroll branch's supersample math at 1440p/2160p was never exercised here.
- The pinned `baseline_review.json` was not usable for a like-for-like A/B
  (different res/branding/encode); the fresh off-arm stands in as baseline. The
  fresh off-arm's fault profile (28) is materially different from the pinned 45 —
  earlier adoptions (gap-001/002/004/005) already moved the chapter.
- 3-pass majority still leaves 2/3 churn (±2 items per arm observed here);
  the matched-vetoes arithmetic handled it as designed.
- gap-014's binding scene-triplet criterion is anchored to a deleted bench;
  it cannot be re-certified without re-building the exp-004 v2 bench project
  (re-segmentation + re-script), which is outside this round's budget.
- The integrator's 47→0 headline was verified in direction and site but not in
  exact count (word timings not archived).

## 8. Why ITERATE (not reject)

The mechanism works where it has text to anchor on: the healed s6 face crop, the
p0101 bubble site (deterministic 16→0), 92 containment-correct anchors, wall cost
trivial, off-path byte-identical (25/25 framemd5-diff is the ON arm; a flag-off
re-render reproduced the off clips identically per integrator unit tests). Two
targeted fixes would plausibly flip the verdict:

1. **Don't scroll low-text tall panels.** p0075's only text is a 76×76 box; SFX/art
   panels read better letterboxed. Gate the scroll mode on text coverage (e.g.
   require text-box area or count above a floor, or require the text union to
   actually constrain the band) — p0072/p0075/p0136-class panels then keep today's
   behavior, killing the s14 regression while keeping the s6-class anchor wins.
2. **Handle the infeasible-geometry fallback** (p0002/p0024): when the text union
   exceeds any legal window, either drop fill below 0.55 with a floor on readability,
   or split the sweep into two beats — otherwise the card's deterministic gate can
   never pass chapter-wide and every future eval re-fails criterion #2.

## Artifacts

- `metrics.json` (default verdict + evaluator block), `metrics_ab_default.json`,
  `metrics_ab_matched.json`, `metrics_ab_scoped_allscenes.json`
- `eval_reviews/`: review_{off,on}.json/.md + all 6 pass files,
  `containment_fullbench.txt`, `kb_plans_fullbench.json`,
  `render_{off,on}_fullbench.log`, `time_{off,on}.log`
- `samples/eval/`: off/on frame pairs at t70.9, t94.8 (md5-equal), t175.7 (heal),
  t422.5 (regression), t636.0, t716.7; scroll sweep endpoints
  `on_p0075scroll_t419.9/424.2.png`
