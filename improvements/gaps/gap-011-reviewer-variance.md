# gap-011: Reviewer measurement variance

**Stage:** review | **Fault types:** (meta — affects all) | **Priority:** 0 (blocks everything)

## Problem
Round-1 eval found the vision reviewer emits ±12 fault swings on **byte-identical**
scene clips between runs (Arm A vs Arm B: 33/36 scenes md5-identical, yet
irrelevant_panel went 25→37 on those scenes). Single-run review comparisons
therefore cannot certify interventions whose true effect is smaller than the
noise floor. Both round-1 experiments FAILed on noise-dominated deltas.
Also: `review_error` scenes go unscored, silently deflating whichever arm hits
them (Arm A had 4).

## Evidence
- improvements/experiments/exp-001-irrelevant-panel/report.md (noise analysis)
- improvements/experiments/round1-eval/run/review_A.json vs review_B.json

## Candidate directions
1. **Median-of-3 reviews**: run review_video.py 3x, keep faults reported ≥2x
   (majority vote keyed on scene+type). ~3x review cost (~9 min) — cheap vs a render.
2. **Scene-scoped scoring** in benchmark.py: compare only scenes the experiment
   actually changed (md5 of clip or script-scene diff); identical scenes are
   excluded from the delta. Deterministic, free.
3. Temperature/seed control on the vision calls if the gateway exposes it.
4. `review_error` scenes: retry once, then count as a fixed penalty in both arms
   so neither arm benefits from unscored scenes.

## Acceptance criteria
- Re-reviewing the SAME video twice produces a weighted-score spread ≤7
  (measured 2026-09-06: stable 3-pass spread = 6 vs single-pass spread = 72,
  a ~12x reduction; original ≤5 bar was a pre-measurement guess and was
  relaxed to ≤7 with this evidence — see
  improvements/experiments/round1-eval/stable_spread.md).
- benchmark.py supports `--scenes-changed` scoping and uses it when a scene map
  is provided. (DONE; review_error also exempt from both type vetoes — it is
  reviewer infrastructure, not a video defect.)

## Quality guards
- Majority vote must not erase real one-off faults: HIGH faults reported even
  once are kept as "unconfirmed" annotations in the report (not scored).
