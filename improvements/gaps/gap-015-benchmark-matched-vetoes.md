# gap-015: Benchmark per-type count vetoes fire on unmatched reviewer noise

**Stage:** review | **Fault types:** (meta — benchmark arithmetic, affects all) | **Priority:** 1

## Problem
Follow-up to gap-011 (adopted). review_stable.py fixed *within-arm* variance
(spread 72 → 6), but benchmark.py's veto layer still compares raw per-type
COUNTS between arms (`type_counts` at pipeline/benchmark.py:51, vetoes 1–2 at
:134-146). Marginal faults (3/5 consensus) surface on both sides of an A/B and
churn the per-type totals; count-increases repeatedly trace to reviewer
relabels of pixel-identical / md5-identical frames. Any experiment whose true
effect size is smaller than this churn vetoes on noise regardless of merit —
this is now the binding blocker on certifying small improvements (exp-004
lean-adopt blocked; exp-009 v3/v4 blocked 3 consecutive rounds).

**Scope constraint: fault DEFINITIONS in pipeline/review_video.py must NOT
change. This gap is about benchmark.py's veto arithmetic only.**

## Evidence
- improvements/experiments/exp-004-segmentation/report.md, tie-break addendum:
  the original veto item confirmed noise at 1/5 passes, yet every recomputed
  A/B still FAILs — "5 passes with a ceil(N/2)=3 threshold is *more
  permissive* than 3-pass (60% vs 67% consensus), so marginal 3/5 items
  surface on both sides and churn the per-type counts that the veto layer
  compares"; "Every count-increase behind the vetoes traces to reviewer churn
  on marginal items or render-stage framing, none to a v2 cut through
  content"; explicit filing request: "per-type vetoes should compare matched
  (scene,type) pairs at a fixed consensus level, otherwise any experiment of
  this effect size will veto on noise regardless of merit."
- Concrete churn examples (same addendum): v1's scene-21 crop dropped below
  majority at 5 passes while v2's held at 3/5; v2 "gained" a 4/5 weak_hook+crop
  on scene 0 whose earlier flagged frame was byte-identical to v1's.
- improvements/experiments/exp-009-phone-readability/report.md, v3: global
  veto items on frames **byte-identical to baseline** (md5-equal, e.g. scene 3
  t94.8 md5 327e2c45… both arms). v4: "Global: cropped_content 4→5 —
  PERSISTS — but 100% reviewer relabeling on baseline-identical pixels…
  This is the same churn documented in v3 and gap-011"; verdict line: "3rd
  consecutive relabel round… Global veto sits on baseline-identical pixels -
  no code iterate can fix" (also quoted in ledger gap-009 last_verdict).
- ch4 production run (2026-09-10 manual pass,
  `output/the-world-after-the-fall-ch4/review.md:31-43`): 7 of 18 HIGH
  `irrelevant_panel` flags dismissed as reviewer false positives after frame
  verification (scenes 6, 11, 15, 20, 22, 23 — e.g. 7:34/7:39 "normal room"
  misread of an outdoor dragon claw strike). ~39% HIGH false-positive rate on
  a single production review — the same noise class the matched-veto fix must
  keep out of A/B arithmetic.

## Candidate directions
1. **Matched (scene,type) pair comparison**: compute vetoes over pairs keyed
   on (scene, type) at a fixed consensus level (same pass count and same
   majority threshold on BOTH arms), so a fault that merely moves scenes or
   flips type on identical pixels does not read as a count increase.
2. **Frame-hash exemption**: before a type-count increase can veto, hash the
   fault's evidence frame (ffmpeg framemd5 at the fault timestamp, or the
   scene clip md5 already computed for --scenes-changed); if the frame is
   pixel-identical to the baseline arm's frame, the increase is annotated
   (like review_error) instead of vetoing.
3. Fixed-consensus normalization: when arms were reviewed with different pass
   counts (3 vs 5), benchmark.py refuses or renormalizes to a common
   confirmed set before comparing — the exp-004 addendum showed threshold
   asymmetry alone flips verdicts.

## Acceptance criteria
- Replay test (deterministic, no new reviews): running the modified
  benchmark.py over the archived review pairs must flip the known-noise
  vetoes and preserve the known-real ones:
  - exp-009 v4 global (stable_base vs T4): cropped_content 4→5 veto no
    longer fires (all deltas on pixel-identical frames) → verdict driven by
    score only.
  - exp-004 scoped 5-pass symmetric (stable_seg_v1_5pass vs
    stable_seg_v2_5pass): unmatched-churn vetoes suppressed; report annotates
    them instead.
  - exp-009 v1 (stable_base vs T1): the REAL regression (cropped_content
    4→7, crop-consistent details on changed scenes) still vetoes/FAILs.
  - gap-003 v2 (watermark residual ghost p0017, real new fault on a changed
    frame) still vetoes/FAILs.
- Re-running the same-video-twice control (gap-011's spread test inputs):
  zero vetoes between two stable reviews of the identical video.
- pipeline/review_video.py untouched (git diff empty for that file).
- Exemptions are never silent: every suppressed count-increase appears in
  metrics.json under an `annotated_vetoes` (or equivalent) key with the
  frame-hash proof.

## Quality guards
- The veto layer must remain a HARD gate for genuine regressions: any fault
  on a frame that differs from baseline pixels still counts fully.
- No relaxation of severity weights or score arithmetic — only which
  count-deltas are eligible to veto.
- Frame-hash comparison must tolerate container-level byte diffs on identical
  pixels (use framemd5 of decoded frames, not file md5 — exp-004 scene 28
  showed a 2-byte container diff on near-identical frames).
