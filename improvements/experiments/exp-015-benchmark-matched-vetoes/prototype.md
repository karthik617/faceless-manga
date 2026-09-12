# exp-015 prototype — matched (scene,type) veto arithmetic

**Gap:** gap-015 (benchmark per-type count vetoes fire on unmatched reviewer
noise). Build-ourselves gap; the card's candidate directions 1–3 are all
implemented.

## What changed

- `pipeline/benchmark.py` only (+270/-14). Opt-in flag **`--matched-vetoes`**
  plus `--hash-video`, `--hash-baseline-video`, `--hash-window` (default
  3.0 s). Default path byte-identical with the flag off (diff-verified on two
  archived pairs). Score arithmetic, severity weights, media vetoes,
  `--scenes-changed`, and review_video.py untouched.
- `improvements/experiments/exp-015-benchmark-matched-vetoes/replay_acceptance.py`
  — deterministic replay of all five gap-card acceptance criteria over
  archived stable reviews. **15/15 checks pass** (~21 s).

## Mechanism (details in IMPLEMENTATION.md)

1. Fixed-consensus normalization: both arms re-thresholded at the stricter
   native majority fraction when pass counts differ (3v5 → common 2/3 bar).
2. Matched (scene,type) pairs: per-type count increases net against removed
   pairs of the same type (scene churn cancels out).
3. Frame-hash exemption: an added pair whose evidence frame (ffmpeg framemd5
   of the DECODED frame at fault `t`, video stream only) exists in the
   baseline video within ±window is exempt — pixel-identical relabels can't
   veto. File md5 never used (container-byte guard).
4. Everything suppressed is annotated in `metrics.json → matched_vetoes.
   annotated_vetoes` with per-pair hash proofs; consensus drops listed too.
   Never silent.

## Fallback behavior

- Missing/invalid hash videos → hashing disabled (flagged in output),
  matched-pair netting still applies.
- Any exception inside the matched path → stderr warning + fall back to the
  DEFAULT (stricter) veto arithmetic; the scorer cannot crash or under-gate.
- Single-pass reviews (no `votes` field) → treated as 1/1, consensus 1.0.

## How to run the stage-scoped experiment

```bash
./venv/bin/python3 pipeline/benchmark.py \
    --experiment improvements/experiments/<exp-id> \
    --baseline <stable_baseline.json> --review <stable_arm.json> \
    --matched-vetoes \
    --hash-video <arm review-cut mp4> --hash-baseline-video <base review-cut mp4> \
    [--scenes-changed ...]

# acceptance replay:
./venv/bin/python3 improvements/experiments/exp-015-benchmark-matched-vetoes/replay_acceptance.py
```

## Dep installs performed

None — stdlib + the ffmpeg binary benchmark.py already uses.

## Deviations

See IMPLEMENTATION.md §Deviations: (1) C5 control substituted (gap-011 run/
inputs deleted; used exp-004 3-pass vs 5-pass reviews of the identical
sfxed.mp4), (2) asymmetric pass counts renormalize rather than refuse,
(3) frame hashing = single-frame framemd5 + ±3 s window rather than
whole-clip md5, (4) exp-004 hash videos live in /tmp only, (5) pre-existing
unrelated dirty diff on review_video.py predates this session.
