# exp-003 evaluation report — round2-eval (Arm W)

**Verdict (binding, benchmark.py): FAIL — global AND scene-scoped, both on
veto + negative delta.**

- Global: score 126 → 118 (delta -8, i.e. improved) but **VETO: fault types
  worsened** — irrelevant_panel 12→13, unreadable_text 1→3.
- Scoped (`--scenes-changed 0,4,7,9,15,17,20,22`): score 54 → 47 (delta -7,
  improved) but **VETO** — cropped_content 2→3, irrelevant_panel 2→3,
  unreadable_text 1→2. (`metrics.json` = scoped, `metrics_global.json` = global.)

Interpretation: the core capability works — watermark HIGH faults dropped
4 → 1 and the weighted score improved in both scopes — but the removal step
introduced a visible artifact on p0117 that spawned a cluster of new faults in
scene 20 (the changed scene), and the detector misses at least one real
watermark instance (p0066, mid-panel band) that keeps the count from reaching
0. The FAIL is driven by real, experiment-caused damage, not just reviewer
noise: scene 20 gained irrelevant_panel HIGH + cropped_content MEDIUM +
unreadable_text LOW in the majority-of-3 vote, and the cleaned p0117 visibly
has a broken bottom-left panel border (jagged white notch where the badge
was). This is **FAIL-leaning-iterate**: fix the p0117-class reflect artifact
(the reflect source mirrored panel-exterior white into the border region) and
widen/band-free the match (p0066's mark sits at y=422 of a 593-px panel —
outside the "bottom 12%×2 template heights" search band), and the experiment
plausibly passes.

## Protection of golden project

Panels read from `output/the-world-after-the-fall-ch2/panels` (read-only via
symlink); cleaned outputs redirected with `--out` to
`round2-eval/panels_clean_w`. **Note:** clean_watermarks.py rewrote
`$GOLD/watermark_log.json` (cache/log file next to panels — allowed for cache
files per eval instructions); `watermark_template.json` untouched (md5
verified). No panel or script in the golden project was modified. Shared
baseline: fresh render into `round2-eval/_work_base` (the golden `_work/` had
been deleted), TTS mp3s reused across all arms so voices are identical.

## Score table (stable review, majority-of-3)

| fault type | baseline | Arm W | Δ |
|---|---|---|---|
| watermark (H) | 4 | 1 | **-3** |
| irrelevant_panel (H) | 12 | 13 | +1 ⚠ veto |
| missing_payoff (H) | 5 | 5 | 0 |
| weak_hook (H) | 1 | 1 | 0 |
| cropped_content (M) | 4 | 4 | 0 global (2→3 in scoped set) ⚠ veto |
| phone_readability (M) | 2 | 2 | 0 |
| empty_screen (M) | 1 | 1 | 0 |
| unreadable_text (L) | 1 | 3 | +2 ⚠ veto |
| panel_reuse (L) | 1 | 1 | 0 |
| **weighted score** | **126** | **118** | **-8 (better)** |

Fault-set diff (scene, type): fixed = watermark@9, watermark@15, watermark@20
(+ cropped_content@2, unchanged scene, likely noise). New = unreadable_text@14
(unchanged scene, likely noise), and in scene 20 (changed):
unreadable_text(L) + cropped_content(M) + irrelevant_panel(H) — consistent
with the p0117 border artifact below.

## Vetoes triggered

- `new_fault_types: false` — no brand-new types, but per-type counts worsened:
  scoped {cropped_content 2→3, irrelevant_panel 2→3, unreadable_text 1→2};
  global {irrelevant_panel 12→13, unreadable_text 1→3}. benchmark.py FAIL in
  both scopes. Scene-20's cluster is experiment-caused (see evidence);
  the scene-14 unreadable_text is on an untouched scene (reviewer noise), but
  the veto stands regardless.

## Acceptance criteria (gap-003)

| criterion | result |
|---|---|
| watermark HIGH 4 → 0 | **NOT MET** — 4 → 1. Remaining: scene 13 (p0066), whose FLAMESCANS.ORG lockup sits mid-panel (y=422/593), outside the bottom search band. Full-panel template scan scores it **0.914** — a detector band-restriction miss, not a matching failure. A second uncleaned mark exists on p0144 (score 0.601) but no scene uses p0144. |
| no visible inpaint smearing on sampled frames | **NOT MET** — no Telea smear (all 8 used reflect), but p0117's reflect broke the panel's bottom border: jagged white notch, see `samples/artifact_p0117_border_notch_zoom.png`. Reviewer flagged scene 20 accordingly. Other 7 panels look clean (`before_after_p*.png`). |
| clean step wall-time ≤ +60 s | **MET** — 6.4 s cold on 154 panels. |

## Wall times

- clean_watermarks (cold, cached template): ~6 s. Budget +60 s: **PASS**.
- Arm render (review cut, TTS reused): ~10 min. Stable review ×3: ~22 min.

## Frame evidence (`samples/`)

- `base_t241.6.png` vs `expW_t241.6.png` — scene 9 title-area mark removed cleanly.
- `base_t439.7.png` vs `expW_t439.7.png`, `base_t613.7.png` vs `expW_t613.7.png` — scenes 15/20 marks gone.
- `base_t368.6.png` vs `expW_t368.6.png` — scene 13 mark **still present in both** (p0066 miss).
- `missed_p0066_watermark.png`, `missed_p0144_watermark.png` — the two undetected lockups (crops from raw panels).
- `artifact_p0117_border_notch_zoom.png` — the removal artifact behind scene 20's new faults.
- `before_after_p*.png` (from prototyping) — per-panel before/after crops.

## Caveats — not measured in this stage-scoped run

- No final encode: loudness/duration vetoes not exercised (review-cut durations
  matched to ±0.1 s across arms; encode-must-succeed unverified for --full).
- No TTS regeneration (mp3s reused from baseline) — TTS interaction unmeasured.
- Vision-discovery path exercised via cached template; the negative-cache path
  and other chapters untested here.
- Reviewer noise floor: cropped_content@2 disappearing and unreadable_text@14
  appearing on untouched scenes shows residual noise even at 3 passes; the
  scene-20 cluster, however, has physical evidence and is not noise.
- Full pre-adoption run (mandatory per config) must recheck loudness, duration,
  encode, and re-audit all reflect outputs for border-adjacent matches.

## v2 re-evaluation (border-safety gates + tier-2 full-panel scan)

**Verdict (binding, benchmark.py): FAIL — global AND scene-scoped, veto on
fault-type counts despite improved score in both scopes.**

- Global: score 126 → 118 (delta -8, improved) but **VETO: fault types
  worsened** — cropped_content 4→5, phone_readability 2→3, unreadable_text 1→4
  (`metrics_global.json`).
- Scoped (`--scenes-changed 0,4,7,9,13,15,17,20,22` — recomputed from the v2
  watermark log × arm script; scene 13/p0066 now in-scope): score 64 → 58
  (delta -6, improved) but **VETO** — cropped_content 2→3, irrelevant_panel
  2→3, unreadable_text 1→3 (`metrics.json`).
  (Note: v1 scoped baseline was 54 on the 8-scene set; on the v2 9-scene set
  the same baseline scores 64 — scene 13 adds missing_payoff + watermark.)

### Are the two v1 defects fixed?

| v1 defect | v2 result |
|---|---|
| p0117 reflect broke the slanted panel border (jagged white notch → scene-20 fault cluster at t=613.7) | **FIXED.** Border self-check routed p0117 to `inpaint-border`; the frame border is intact and redrawn (`samples/v2_p0117_corner_zoom4x.png`, video frame `v2_expW2_t613.7.png`). The v1 613.7s cropped/irrelevant faults are gone from the v2 review. |
| p0066 mid-panel mark missed (band restriction) | **FIXED.** Tier-2 full-panel scan catches it at 0.914 (≥ FULL_SCAN_THRESHOLD 0.85) and rejects the p0144 near-miss (0.601, unused by any scene). p0066 cleaned via `inpaint-border`; scene-13 watermark fault gone (`samples/v2_before_after_p0066.png`, `v2_expW2_t368.6.png`). |

All **four original watermark HIGH faults fixed** (scenes 9, 13, 15, 20:
4 → 0 on the original set). 9/154 panels cleaned (5 reflect, 4 inpaint-border,
0 skipped_border_risk), 11.5 s cold.

### Score table (stable review, majority-of-3, baseline = stable_base.json reused)

| fault type | baseline | v1 (W) | v2 (W2) | Δ base→v2 |
|---|---|---|---|---|
| watermark (H) | 4 | 1 | 1 | **-3** (all 4 original fixed; 1 NEW on scene 0, see below) |
| irrelevant_panel (H) | 12 | 13 | 12 | 0 global (2→3 scoped ⚠ veto) |
| missing_payoff (H) | 5 | 5 | 5 | 0 |
| weak_hook (H) | 1 | 1 | 1 | 0 |
| cropped_content (M) | 4 | 4 | 5 | +1 ⚠ veto (both scopes) |
| phone_readability (M) | 2 | 2 | 3 | +1 ⚠ veto (global) |
| empty_screen (M) | 1 | 1 | 1 | 0 |
| unreadable_text (L) | 1 | 3 | 4 | +3 ⚠ veto (both scopes) |
| panel_reuse (L) | 1 | 1 | 1 | 0 |
| **weighted score** | **126** | **118** | **118** | **-8 (better)** |

### New faults: real vs noise (per-pass vote audit)

Fault-set diff vs baseline, with per-pass votes (base / v2):

| new fault | scene changed? | votes base→v2 | judgment |
|---|---|---|---|
| watermark(H)@0 + unreadable_text(L)@0 | yes (p0017, p0040) | [0,0,0]→[1,1,0] / [1,0,0]→[0,1,1] | **REAL-leaning (residual artifact).** p0040 is pristine, but p0017's `inpaint-border` leaves a conspicuous grey box + white bar where the badge was (`samples/v2_p0017_zoom.png`, visible in-video at t≈26, `v2_expW2_t26.4_scene0fault.png`). 2/3 reviewers call the residual a watermark. Ironically baseline reviewers never flagged scene-0's actual legible FLAMESCANS.ORG (3× missed); the residual box now draws the eye. |
| cropped_content(M)@20 + irrelevant_panel(H)@20 + unreadable_text(L)@20 | yes (p0117) | [0,1,0]→[1,1,1] etc. | **NOISE on unchanged content.** The v2 cluster sits at t=635.2 — a dialogue panel ("THIS STONE… LOOKS KIND OF DIRTY") byte-identical to the baseline frame (`/tmp` cmp attached logic; base vs w2 frames match) and NOT the p0117 frame (613.7, now clean). Baseline reviewers saw the same weakness 1/3; the −0.7 s cut-length shift moved frame sampling so it now confirms 3/3. Pre-existing content fault surfacing, not experiment damage — but scene 20 is a changed scene, so the scoped veto counts it. |
| cropped_content(M)@1 + unreadable_text(L)@1 | **no** (untouched scene) | [1,0,0]→[0,1,1] | **NOISE** per gap-011 (byte-identical scene). |
| phone_readability(M)@21 | **no** (untouched) | [0,0,0]→[1,0,1] | **NOISE** per gap-011. |
| fixed on untouched scenes: cropped_content@2, irrelevant_panel@14 | no | — | noise in our favor; ignored. |

Net: the veto is driven by (a) one real experiment-caused artifact class —
`inpaint-border` residuals (p0017's is the worst; p0066/p0117/p0133 leave
fainter dark smudges, see `v2_p0066_zoom.png`, `v2_p0117_border_zoom.png`) —
and (b) frame-sampling noise on unchanged content. benchmark.py's FAIL is
binding regardless.

### Acceptance criteria (gap-003)

| criterion | result |
|---|---|
| watermark HIGH 4 → 0 | **NOT MET (close).** Original 4 all fixed; 1 NEW watermark flag (2/3) on scene 0 attributable to the p0017 inpaint-border residual → net 4 → 1. |
| no visible inpaint smearing on sampled frames | **NOT MET.** No Telea smear and no broken borders, but the 4 `inpaint-border` panels carry visible grey/white bar residuals where the opaque banner was (crops in samples/). Reviewers react to the p0017 one. |
| clean step wall-time ≤ +60 s | **MET** — 11.5 s cold on 154 panels (v1: 6.4 s; tier-2 full scans add ~5 s). |

### Wall times

- clean_watermarks v2 (cold, cached template): 11.5 s. Budget +60 s: PASS.
- Arm W2 render (review cut, TTS mp3s reused from _work_w): ~19 min.
- Stable review ×3: ~10 min. (First review attempt discarded: run without
  `--workdir` silently produced a 0-fault vision pass — scene spans came up
  empty. Re-run with `--workdir _work_w2`; only the corrected run is scored.)

### Protection of golden project

Same approach as v1: golden panels read via symlink, outputs redirected with
`--out` to `round2-eval/panels_clean_w2`; `watermark_log.json` (cache, allowed)
rewritten — v1 copy backed up to /tmp; `watermark_template.{json,png}` md5
verified unchanged before/after (b238dc53…, aed6e221…). No golden panel,
script, or render touched.

### Frame evidence (`samples/`, v2_ prefix)

- `v2_before_after_p0117.png`, `v2_p0117_border_zoom.png`, `v2_p0117_corner_zoom4x.png` — v1's broken border now intact (fix #1).
- `v2_before_after_p0066.png`, `v2_p0066_zoom.png`, `v2_full_p0066.png` — the v1 miss now detected + removed (fix #2); note residual smudge.
- `v2_p0017_zoom.png`, `v2_p0040_zoom.png` — p0040 pristine vs p0017's residual box (the new scene-0 complaint).
- `v2_expW2_t368.6.png` / `v2_expW2_t439.7.png` / `v2_expW2_t613.7.png` / `v2_expW2_t241.6.png` — in-video: scenes 13/15/20/9 marks gone (compare v1 `base_t*.png`).
- `v2_expW2_t26.4_scene0fault.png` vs `v2_base_t26_scene0.png` — scene-0: baseline's legible logo vs v2's residual box.
- `v2_expW2_t635.2_scene20fault.png` — the scene-20 veto frame: unchanged dialogue panel, not the cleaned p0117.

### Caveats — not measured in this stage-scoped run

- Same as v1: no final encode (loudness/duration/encode vetoes unexercised —
  review-cut duration drifted −0.7 s / 0.09%, well under the 5% guard but the
  cause of the drift with reused TTS is unexplained and should be checked on
  the full run), no TTS regeneration, negative-cache/other-chapter paths
  untested.
- Reviewer sampling sensitivity: a 0.7 s length change was enough to flip a
  marginal 1/3 fault to 3/3 on unchanged content — the majority-of-3 noise
  floor does not fully protect changed scenes whose timing shifts.

### FAIL-leaning-iterate: the one change that would decide it

The capability is now complete (all 4 original marks removed, borders safe,
mid-panel tier works, 0 false positives). What keeps failing the gate is the
**`inpaint-border` residual**: Telea inside the preserved-border box does not
erase the opaque banner ghost — p0017's residual is prominent enough that
reviewers flag it as a new watermark. v3 should add a residual self-check
(post-removal: no dark box-shaped blob inside the bbox; else escalate to
gateway `image_edit` — the research's unimplemented tier — or widen the fill
source), then re-run this exact eval. The scene-20/1/21 noise items would not
by themselves veto a run where scene-0 is clean.
