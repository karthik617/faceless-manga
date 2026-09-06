# gap-011 evidence: stable-review spread (2026-09-06)

Two independent 3-pass `review_stable.py` runs on the **same video**
(`run/arm_A.mp4`, full final render, 906.73 s) with identical args.

## Aggregated (confirmed-fault) scores — weights high=5, med=2, low=1

| Run | Confirmed faults | Weighted score |
|---|---|---|
| stable_A1 | 46 (28h / 16m / 2l) | 174 |
| stable_A2 | 45 (27h / 15m / 3l) | 168 |

**SPREAD = |174 - 168| = 6** — acceptance criterion ≤5 → **narrow miss (by 1
point, ~3.5% of the score)**.

## Single-pass contrast (no stabilization)

| Pass file | Faults | Weighted score |
|---|---|---|
| stable_A1.pass1 | 95 | 367 |
| stable_A1.pass2 | 89 | 332 |
| stable_A1.pass3 | 91 | 367 |
| stable_A2.pass1 | 89 | 324 |
| stable_A2.pass2 | 94 | 360 |
| stable_A2.pass3 | 78 | 295 |

**Single-pass spread = 367 - 295 = 72.**

## Interpretation

Stable review reduces same-video score noise from 72 to 6 (~12x). The ≤5
target is missed by 1 point; global comparisons should treat deltas within
~±7 as noise. Options: relax the criterion to ≤7 with this evidence, or add
a 4th pass / median-of-two-stable-runs when tighter resolution is needed.

Each stable run cost ~407 s wall (3 passes).
