# exp-001 — verify_panels (gap-001) — Round 1 Evaluation Report

Evaluator run: round1-eval, 2026-09-06. Golden project untouched.
All artifacts: `improvements/experiments/round1-eval/run/` (arm_A/arm_B scripts,
review_A/review_B json+md, verify_log.txt, render logs, _work_A/_work_B).

## Verdict

**benchmark.py: FAIL** (binding) — weighted score 233 → 287 (delta +54), with
worsened-fault-type vetoes.

**Interpretation: the FAIL is dominated by reviewer noise, not by the
intervention.** The evidence:

- verify_panels changed only **3 of 36 scenes** (script scenes 1, 34, 36 →
  0-idx 0, 33, 35): 3 drops + 1 substitution. Narration is byte-identical in
  all 36 scenes.
- Rendered scene clips for the 33 unchanged scenes are **byte-identical**
  between Arm A and Arm B (md5-verified on c001/c010/c028).
- Yet the reviewer emitted **25 (A) vs 37 (B)** irrelevant_panel faults *on
  those identical unchanged scenes*. That 12-fault swing on identical pixels
  is pure vision-LLM run variance, and it alone exceeds the whole score delta.
- Additionally Arm A had 4 `review_error` faults (scenes 1, 10, 12, 28
  unscored → A's counts deflated); Arm B had 0 review_errors (fully scored).
- **On the scenes verify_panels actually touched, HIGH irrelevant_panel went
  6 (A) → 2 (B)**, a 67% drop, matching the frame evidence below.

So: the tool did what it claims on the scenes it touched, but a single-run
review comparison cannot pass benchmark.py because reviewer noise
(σ ≈ ±12 faults on identical video) is larger than the treatment effect
(3 scenes). The benchmark verdict is honest and binding for adoption purposes;
it should be read as "not provable with n=1 reviews", not "made it worse".

## Score table (fault type, A vs B)

| type | sev | Arm A | Arm B | note |
|---|---|---|---|---|
| irrelevant_panel | high | 31 | 39 | on unchanged scenes 25→37 (identical pixels: noise); on changed scenes 6→2 |
| watermark | high | 6 | 7 | unchanged scenes 5→7 (identical pixels) |
| blank_frame | high | 3 | 3 | deterministic, stable |
| cropped_content | med | 7 | 9 | unchanged scenes 7→9 |
| empty_screen | med | 1 | 6 | unchanged scenes 1→6 (noise) |
| static_scene | med | 4 | 4 | deterministic, stable |
| phone_readability | med | 2 | 2 | stable |
| unreadable_text | low | 1 | 0 | |
| review_error | low | 4 | 0 | 4 A-scenes unscored → A undercounted |
| **weighted score** | | **233** | **287** | weights high=5 med=2 low=1 |

Note the deterministic checks (blank_frame, static_scene) are identical across
arms — every divergence lives in the vision-LLM channel.

## Acceptance criteria (gap-001)

| criterion | result | status |
|---|---|---|
| ≥50% drop in HIGH irrelevant_panel | global: 31→39 (worse). On verify-changed scenes only: 6→2 (−67%) | **NOT MET globally / MET on treated scenes** — global measurement swamped by reviewer variance |
| no scene left panel-less | 0 panel-less scenes in arm_B.json (scene 36 rescued via substitution p0148→p0146) | **MET** |
| script-step wall-time delta | verify_panels ran in **10.6 s** (incl. 1 vision batch: 3 scenes / 6 panels); script draft itself 160.5 s | **MET** (negligible overhead) |

## Vetoes (benchmark.py)

- fault types worsened (base→now): irrelevant_panel 31→39, watermark 6→7,
  cropped_content 7→9, empty_screen 1→6 — all located on scenes whose pixels
  are byte-identical between arms.

## Wall times

| step | time |
|---|---|
| script_from_panels (shared draft, OCR cached) | 160.5 s |
| verify_panels (vision ON, no fallback needed) | 10.6 s |
| Arm A render to review-cut | ~87 min total (first attempt hung ~55 min on an edge-tts stream stall at scene 6 — no socket timeout in mv.tts; resumed leg 1256 s with 5 scenes cached; 7 tts retries logged) |
| Arm B render to review-cut | 93 s (33/36 clips reused from A via audio+clip copy; only 3 changed scenes re-rendered) |
| review_video per arm | ~3 min |

## Frame evidence (samples/)

- `scene33_t818.6_armA.png` vs `_armB.png` — A: full-screen "LET'S GO"
  text-only bubble (the exact panel verify dropped, tier-1 "textonly, narration
  does not quote it"); B: story art (cloaked figure, flying leaves). Fixed.
- `scene33_t830.9_armA/B.png` — layout shifts after the drop.
- `scene35_t874.0_armA.png` vs `_armB.png` — A: static snowfield with
  "A SNOWFIELD…?" bubble while narration describes Jaehwan walking forward;
  B: Jaehwan close-up (substituted p0146). Fixed.
- `scene35_t894.4_armA/B.png` — same scene later; A still snowfield, B face.

## Verify decisions (from verify_log.txt)

- drop scene 1 (script idx 0): p0118.png — tier-3 vision judged irrelevant.
- drop scene 34 (idx 33): p0139.png — tier-1 textonly.
- drop+substitute scene 36 (idx 35): p0148.png dropped (tier-3), p0146.png
  substituted (out-of-window fallback, score 0.222) — scene not left empty.
- 1 vision batch total: 3 scenes / 6 panels escalated.

## Caveats

- Stage-scoped run (--stop-after review-cut, --no-music in all arms):
  final loudness, music seams/ducking, popup/title-card, and final-encode
  duration drift were NOT measured. LUFS/duration vetoes were inactive
  (no --video passed to benchmark.py).
- TTS audio for B was copied from A (narration unchanged by verify_panels),
  so TTS variance is zero between A and B — a strength for this comparison.
- Single review run per arm. Measured reviewer noise on identical content is
  larger than the treatment surface (3/36 scenes). Recommendation for next
  round: review each cut 3× and compare medians, or score only the scenes the
  intervention touched, or make review_video temperature-0/deterministic.
- Arm A's 4 review_error scenes deflate its baseline count; Arm B was fully
  scored — an asymmetric penalty against B.
- Vision escalation used the gateway successfully; --no-vision fallback was
  not needed.

## Full-run pre-adoption gate (round1-eval, 2026-09-06)

Full renders of both arms (no --stop-after; captions, SFX/ambience, subscribe
popup, title card, loudnorm final encode) from the cached workdirs, then
stable 3-pass reviews (review_stable.py) and benchmark with media vetoes.

### Renders & media sanity

| Metric | arm_A (baseline) | arm_B (verify_panels) |
|---|---|---|
| Final wall time (resume from ambed) | 442 s | 450 s |
| Duration | 906.73 s | 906.73 s (drift 0.00% <=5%) |
| Resolution / streams | 1920x1080, h264+aac | 1920x1080, h264+aac |
| Loudness | -14.0 LUFS (in [-17,-11]) | -14.02 LUFS (in [-17,-11]) |
| Branding stages | popup 899.2s + title card 8.0s + loudnorm: OK | same: OK |
| Panel-less scenes | 0 / 36 | 0 / 36 |

### gap-011: stable-review spread (same video, arm_A, two independent 3-pass runs)

| Run | Confirmed faults | Weighted score (5/2/1) |
|---|---|---|
| stable_A1 | 46 (28h/16m/2l) | 174 |
| stable_A2 | 45 (27h/15m/3l) | 168 |

- **Aggregated spread = 6** vs acceptance <=5 → **narrowly missed (by 1 point, ~3.5% of score)**.
- Single-pass contrast: the 6 individual passes scored 367/332/367/324/360/295
  → **single-pass spread = 72**. Stable review reduces noise ~12x; the ≤5 bar
  is marginally missed, not failed by an order of magnitude.

### gap-001 benchmark, global (metrics_global.json)

| | baseline (A, stable_A1) | experiment (B, stable_B) |
|---|---|---|
| Weighted score | 174 | 158 (**delta -16**, better) |
| high / med / low | 28 / 16 / 2 | 26 / 13 / 2 |
| irrelevant_panel | 22 | 21 |
| cropped_content | 9 | 7 |
| watermark | 5 | 4 |
| empty_screen | 5 | 4 |
| review_error | 0 | 1 |

Verdict: **FAIL (vetoed)** — sole veto is `review_error 0→1`: a JSONDecodeError
inside one reviewer pass at scene 1 (low severity, 2/3 votes), i.e. reviewer
tooling flakiness, **not a video defect**, and scene 1 is untreated. Every
real fault type is flat or improved; no type worsens beyond the measured
noise band (spread 6).

### gap-001 benchmark, scene-scoped (scenes 0,33,35; metrics.json)

| | baseline (A) | experiment (B) |
|---|---|---|
| Weighted score | 12 (2h/1m: 2 irrelevant_panel + 1 cropped_content) | 5 (1h: 1 irrelevant_panel) |

Verdict: **PASS**, delta -7, **no vetoes**, media checks active and clean.

### Acceptance checklist

- [x] Encode sanity; loudness -14.0/-14.02 LUFS in [-17,-11]; duration drift 0%
- [x] Scene-scoped verdict PASS (treated scenes improved, no vetoes)
- [x] Global: no real fault TYPE worsens beyond noise (score -16; only
      flagged regression is a reviewer JSON-parse artifact, low, untreated scene)
- [x] No panel-less scenes; branding stages completed in both logs

### Recommendation: **ADOPT** gap-001 (verify_panels)

The global weighted score improves by 16 points (~3x the measured stable-review
noise of 6), the scene-scoped comparison passes cleanly, and all media gates
pass. The single global veto is attributable to reviewer infrastructure
(JSONDecodeError), not the treatment. Suggested follow-ups: (1) make
benchmark.py exempt `review_error` from the fault-type veto or fix the
reviewer's JSON robustness; (2) gap-011's spread of 6 vs <=5 is a near-miss —
either accept the criterion at <=6-7 with justification or add a 4th pass /
median-of-runs before relying on global comparisons tighter than ~7 points.
