# exp-017-textonly-panel-filter — evaluation report

Evaluator run: 2026-09-11. Golden project `output/the-world-after-the-fall-ch2/`
**never touched** (verified: `find output/the-world-after-the-fall-ch2 -newermt
2026-09-11` returns empty). All arm work in `/tmp/opencode/exp017-eval/run/`
on copies of the golden script + ocr + textboxes sidecars, panels symlinked
read-only. Approach: **copy-inputs** (protocol option 1), with the baseline arm
reused from the pinned round2-eval artifacts (see "Baseline arm" below).

## Verdicts (binding, benchmark.py)

| scoring | verdict | score | vetoes |
|---|---|---|---|
| **global, default arithmetic** | **FAIL** | 126 → 132 (**+6**) | new type `static_scene`; worsened `cropped_content` 4→6, `missing_payoff` 5→6, `static_scene` 0→1 |
| **global, --matched-vetoes** (gap-015, hash videos passed) | **FAIL** | 126 → 132 (+6) | same three types; scene-3 cropped_content pair **frame-hash-exempted** (pixel-identical frame md5 `794bfdd2…` exists in baseline window) but net-after-exemptions still ≥1 on each type → not suppressed |
| **scoped `--scenes-changed 3,12,16,17,22`, default** | **FAIL** | 20 → 24 (+4) | new type `cropped_content` (in scope); worsened cropped_content 0→2, missing_payoff 2→3 |
| **scoped, --matched-vetoes** | **FAIL** | 20 → 24 (+4) | same; scene-3 pair hash-exempt, scene-17 + scene-16 pairs not |

**Interpretation.** The gate does exactly what it claims at the pick level —
6 genuinely art-free panels off-screen, 0 bad drops among ch2's 14 suspects,
scene-12's confirmed SFX-only HIGH (the "STAB" typography full-screen) is gone
and replaced by real art (see frames). But the two *causal* regressions are
real and both trace to one mechanism the prototype already knew about
(NEG p0047/p0084 on ch4): **tier-3 vision drops pure-text speech bubbles whose
text the narration quotes**, and on ch2 two of those bubbles were payoff
carriers:

- **scene 16**: p0086 ("WHAT'S THAT?" bubble) dropped → new 3/3
  `missing_payoff` HIGH ("quoted line never shown: 'What's that? That
  light...?'"). The quote-exception narrowing does not protect it because it
  is `kind=story` (mis-tagged), so it never hits the tier-1 textonly path —
  and `tier3_textonly`'s payoff-append guard only protects panels *appended by
  tier-1.5*, not pre-existing quote carriers. Confirmed causal, not noise.
- **scene 17**: p0101 dropped. The panel is NOT text-only — it carries the
  "LET'S GO, YOONHWAN!" bubble over molten-terrain art (see
  `samples/ch2_dropped/p0101.png`) — but the batched art-check judged
  `has_art=false` (position 3 in a 7-image batch). **Two single-image re-asks
  of the identical rubric both returned `has_art=true`** — a batching/attention
  miss, i.e. a vision false negative the fail-open design does not catch
  because the call *succeeded*. Downstream: scene 17 lost its widest panel,
  producing the new 3/3 `cropped_content` (t 526.1) and the new 3/3
  `static_scene` (12 s unchanged visuals, t 532.5) as the 4 remaining tall
  strips stretch over the same narration. Also note baseline scene 17 already
  had a missing_payoff for this exact quote — the verifier's payoff score for
  p0101 is 1.00, so this drop re-opens a wound the reviewer was already
  (wrongly per verifier, rightly per screen-time) flagging.
- scene-3 `cropped_content` (t 94.6): frame-hash-exempt — the flagged frame is
  pixel-identical to a baseline frame (dropping trailing p0014 doesn't change
  the sampled frame); reviewer relabel noise, formally counted.

So the FAIL is ~70% causal (scene 16/17), ~30% relabel noise (scene 3) — unlike
exp-001/005 where vetoes were pure noise, **this FAIL cannot be attributed
away**. It is a FAIL-iterate with a well-localized cause.

## Score table (stable review majority-of-3, baseline = round2-eval/stable_base.json)

| fault type | baseline | arm (textonly) | Δ | note |
|---|---|---|---|---|
| irrelevant_panel (H) | 12 | 11 | **-1** | scene 12 SFX-only HIGH fixed (treated); 2/7 unchanged (see criterion) |
| missing_payoff (H) | 5 | 6 | +1 ⚠ | scene 16, causal (p0086 quote-carrier dropped) |
| watermark (H) | 4 | 4 | 0 | |
| weak_hook (H) | 1 | 1 | 0 | (2/3 votes vs 3/3 base) |
| cropped_content (M) | 4 | 6 | +2 ⚠ | scene 17 causal; scene 3 hash-exempt noise |
| phone_readability (M) | 2 | 2 | 0 | |
| empty_screen (M) | 1 | 1 | 0 | |
| static_scene (M) | 0 | 1 | +1 ⚠ | new type; scene-17 pacing hole after p0101 drop |
| unreadable_text (L) | 1 | 1 | 0 | |
| panel_reuse (L) | 1 | 1 | 0 | |
| **weighted global** | **126** | **132** | **+6 FAIL** | |
| **weighted scoped (3,12,16,17,22)** | **20** | **24** | **+4 FAIL** | |

Metrics files: `metrics.json` (global default, binding),
`metrics_global_matched.json`, `metrics_scoped.json`, `metrics_scoped_matched.json`.

## What ran

1. **ch4 deterministic replay** (`eval_ch4.py`): **independently confirmed**
   — positives caught 11/12 (2 deterministic drops p0021/p0022, 9 forced
   tier-3), missed 1 (p0042, the documented C1 ceiling), false drops 0/73,
   neg suspects 3 (p0005/p0047/p0084), wall 1.8 s. `ACCEPTANCE PASS` printed.
   Matches the integrator's matrix exactly.
2. **Flag-off no-op check**: post-change `verify_panels.py --no-vision` on a
   golden-script copy → 0 drops/0 subs; scenes and narration byte-equal to
   golden (only the `_verify` audit key differs, which the golden final script
   doesn't carry — expected for a verify re-run, matches gap card's
   byte-identical-off intent at the decision level).
3. **Experiment arm**: `verify_panels.py arm_b.json --textonly-filter` (vision
   ON). 15 suspects raised (the 14 from the read-only report + p0123 via
   fresh sidecar), 2 art-check batches (8+7 images), **6 drops, 0
   substitutions, 5 scenes changed** (3, 12, 16, 17, 22 — 0-based), 0
   panel-less scenes (min panels/scene = 3). Dropped: p0014 ("DIE!!" bubble),
   p0056 ("SPIN" SFX strip), p0063 (red "STAB" SFX), p0086 ("WHAT'S THAT?"
   bubble), p0101 (**bad drop** — has art), p0132 ("JAEHWAN…" bubble on
   mostly-blank panel).
4. **Render**: `panel_render.py --res 1080p --no-music --layout smart
   --stop-after review-cut`, workdir pre-seeded with the baseline arm's cached
   clips for the 20 untouched scenes (byte-identity of all 20 reused clips
   verified via cmp — zero contamination); the 5 changed scenes re-rendered
   fresh. Review-cut duration 781.77 s vs baseline 781.51 s (+0.03%).
5. **Review**: `review_stable.py` majority-of-3 (first attempt lacked
   `--workdir` → script-level scan misfired; discarded and re-run correctly
   with the render workdir). 34 confirmed (22h/10m/2l), 3 unconfirmed-high,
   0 review_errors across all 3 passes.
6. **Baseline arm**: reused the pinned round2-eval baseline —
   `stable_base.json` (126 weighted) over `_work_base/ambed.mp4`, which is a
   majority-of-3 review of the identical golden script rendered with the
   identical flag set. Not re-reviewed this session (reviewer definitions
   unchanged since 09-07; re-review would only add noise). The pinned
   `improvements/benchmark/baseline_review.json` single-pass baseline was NOT
   used for scoring (single-pass vs stable mismatch), consistent with
   exp-005/009 practice.

## Acceptance criteria (gap-017)

| criterion | result | status |
|---|---|---|
| text-coverage metric per panel, scriptable | `textonly_gate.py --script` emits cov/ink/flat per panel (re-run on ch2: 120 panels, 14 suspects, deterministic) | **MET** |
| ch4 replay ≥9/11 caught | 10/11 acceptance-list (p0075 caught anyway via S4), 11/12 raw | **MET** (confirmed independently) |
| ch4 zero false drops (deterministic tier) | 0/73 | **MET** (but see vision-tier caveat: live vision drops kept-by-human p0047/p0084 on ch4 and p0101 on ch2) |
| **ch2 scenes 2/7/12 HIGH irrelevant_panel 4 → ≤1** | Stable-baseline reality: 3 (one per scene), not 4 (the 4 was single-pass). Arm: **2** (scene 12 fixed ✓; scenes 2 & 7 unchanged). Scenes 2/7 clips are **byte-identical between arms** — the gate flags nothing there because the flagged frames are Ken Burns crops *of art+SFX composite panels* (see `samples/s2_bothidentical_t50.5.png`, `s7_bothidentical_t190.1.png`: the p0007 "KUAHH!!" frame is a zoom into the top text band of an art panel; scene 7's frame is a render crop showing only the SFX tip of p0031 with black fill). These are **render-zoom faults, not pick errors** — exactly the integrator's caveat. From the pick side the reachable part was 1 of 3, and it was reached. | **NOT MET as written; MET for the pick-addressable subset (1/1); residual 2 belong to render framing (kenburns/layout gap, not this stage)** |
| no per-type veto regressions (incl. matched-veto arithmetic) | cropped_content, missing_payoff, static_scene all worsen; matched-vetoes exempts only the scene-3 pair | **NOT MET — causal, scenes 16/17** |
| no scene left panel-less | 0 panel-less; min 3 panels/scene | **MET** |
| script-step wall-time ≤1.5× | verify: 3.8 s (off) → 15.3 s (on, vision) — but the *script step* is OCR+draft (~160 s per exp-001); +11.5 s ≈ 1.07× of the step, and the DBNet sidecar was pre-paid (cold add ≈ +25 s ≈ 1.16×) | **MET** |
| fail-open / quote-exception / reading-order guards | fail-open paths present; quote-exception narrowing only hits no-dialogue textonly; **but the payoff-carrier protection has a hole: tier-3 protects only `payoff_appended` panels, not pre-existing quote carriers (p0086) — and with `--payoff` active in the same run, ordering does not save it (verified: a `--textonly-filter --payoff` run still drops p0086 AND additionally drops p0074)** | **PARTIALLY MET** |

## Regression analysis (the requested specifics)

- **ch2's 14 suspects → tier-3 outcomes**: 8 kept (p0002, p0008, p0074*,
  p0097×2, p0099, p0123, p0129, p0148), 6 dropped. (*p0074 kept in the plain
  arm; dropped in the `--payoff` combined probe — vision verdict on it is
  unstable run-to-run.) Of the 6 drops: 4 are clean wins (p0014, p0056,
  p0063, p0132 — pure lettering, two of them behind baseline HIGH flags),
  1 defensible-but-costly (p0086 — genuinely a bubble-only cutout, but it was
  the scene-16 payoff carrier), 1 outright wrong (p0101 — contains substantial
  art; single-image re-asks return has_art=true 2/2; batching artifact).
- **Gateway call delta**: +2 vision calls per chapter run (art-check batches,
  8 imgs/call) on top of the existing tier-3 batch; ch4 replay adds ~2 as
  well. Negligible cost; the risk is quality (batched per-image judgments are
  demonstrably less reliable than single-image).
- **Payoff-panel interaction is the systemic issue**: on ch2, 3 of the 15
  suspects (p0086, p0101, p0123) are payoff-scored 1.00 quote carriers;
  vision dropped 2 of them. p0123 (scene 20, the exp-005 payoff append)
  survived only because vision happened to see art — the `payoff_appended`
  guard would not have engaged either, since appends recorded in a *previous*
  run aren't in the current run's set.

## Wall times

| step | baseline | arm |
|---|---|---|
| verify_panels | 3.8 s (1 tier-3 batch) | 15.3 s (1 tier-3 + 2 art-check batches; DBNet sidecar warm) |
| render to review-cut | (cached, reused) | ~8 min (5 scenes re-rendered + SFX/ambience mux; 20/25 clips reused byte-identical) |
| stable review ×3 | (pinned) | ~13 min |

## Frame evidence (`samples/`)

- `s12_base_t353.7.png` vs `s12_arm_t354.2.png` — baseline: full-screen red
  "STAB" typography (the 3/3 HIGH); arm: monster-eye close-up art. **The
  headline fix.**
- `s12_base_t344.png` / `s12_arm_t358.png` — scene-12 flow after dropping
  p0056+p0063.
- `s16_base_t477.3.png` vs `s16_arm_t478.3.png` — baseline shows the molten
  rock + "THAT LIGHT…?" bubble (quote visibly paid off); arm at the same
  phase shows a mostly-white frame into the LEAP panel — the payoff bubble is
  gone, matching the new missing_payoff.
- `s17_arm_t526.1.png` (new cropped_content site), `s17_arm_t532.5.png` +
  `s17_arm_t540.png` (the 12 s static span) vs `s17_base_t525.png`.
- `s2_bothidentical_t50.5.png`, `s7_bothidentical_t190.1.png` — the residual
  scene-2/7 HIGHs: byte-identical clips in both arms; render-zoom crops of
  art+SFX composite panels (not pick errors).
- `s3_base_t93.png` vs `s3_arm_t93.png` — scene 3 visually unchanged at the
  flagged timestamp (p0014 was trailing); supports the noise attribution.
- `ch2_dropped/` — the six dropped panel originals; check p0101.png (the bad
  drop: molten terrain art + bubble) vs p0014/p0056/p0063/p0132 (pure text).
- `ch2_arm_verify.log`, `stable_b.json/.md`, `ch2_gate_report.txt`.

## Caveats — not measured in this stage-scoped run

- No final encode: loudness/duration vetoes inactive (review-cut drift
  +0.03%, trivially fine); branding/popup/title-card untested.
- TTS reuse for 20 unchanged scenes means TTS variance ≈ 0 between arms on
  those; the 5 re-rendered scenes carry fresh edge-TTS (nondeterministic
  timing, ±0.3 s total).
- The `--textonly-filter --payoff` interaction was probed only at the verify
  level (log in this report), not rendered/reviewed — but it already shows an
  extra drop (p0074) and the p0086 payoff-concat panel still dropped, so the
  combined-flag behavior in the real manga.py pipeline (where --payoff is
  default-on) would be at least as regressive as measured here.
- Baseline arm not re-reviewed this session (pinned 09-07 stable review);
  reviewer drift since then would affect both arms' comparability, though
  the causal regressions are frame-attributable and don't depend on it.
- Vision art-check reproducibility: the p0101 misjudgment is a per-batch
  coin-flip; a different run could pass cleanly and hide the defect. The
  defect is architectural (batched single-shot yes/no with no
  quote-carrier guard), not incidental.

## Recommendation: **ITERATE** (do not adopt, do not discard)

The deterministic layer is solid (11/12 ch4, 0 false drops, cheap) and the
ch2 win on scene 12 is visually unambiguous. Everything that failed sits in
the tier-3 art-check policy. Three targeted changes would likely flip the
verdict:

1. **Extend the payoff guard**: never vision-drop a panel whose text the
   narration quotes (payoff score ≥ threshold at drop time — the verifier
   already computes this; p0086 and p0101 both score 1.00), not just
   `payoff_appended` panels.
2. **De-batch or double-check drops**: require 2 consistent `has_art=false`
   verdicts (or a single-image re-ask) before dropping — p0101 fails 1/3
   batched but 0/2 single; the cost is a handful of extra calls on suspects
   only.
3. Optional: treat R2-only suspects with `dialogue` present (p0101's trigger
   path) as keep-by-default, matching the R1 dialogue carve-out.

Re-run the scoped benchmark after (1)+(2); scenes 16/17 regressions should
vanish while the scene-12 win stays, which the arithmetic would then reward
(expected scoped 20 → ~15). The scenes-2/7 residuals need a render-stage gap
card (SFX-band-aware Ken Burns), not iteration here.
