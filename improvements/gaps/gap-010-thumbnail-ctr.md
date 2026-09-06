# gap-010: Thumbnail CTR heuristics + variants

**Stage:** extras | **Fault types:** none (no review coverage today) | **Priority:** 10

## Problem
make_thumbs.py generates from script-specified panels with identity verification, but there
is no CTR-oriented scoring: face size/emotion, text contrast at small sizes, clutter, or
A/B variants. This stage has zero automated quality feedback — flying blind.

## Candidate directions
1. Research published CTR heuristics (face ≥20% frame, ≤4 words, high contrast, single
   focal point) and encode as a deterministic thumbnail linter.
2. Generate 2-3 variants per spec (different panel crops / text placement) and score with
   the linter + anime-face saliency (shares gap-006 detector).
3. Simulate phone scale: downscale to 168x94 and check text legibility (OCR round-trip).

## Acceptance criteria
- Thumbnail linter implemented with ≥5 measurable checks; benchmark.py reports scores.
- Variants generated behind flag; default output unchanged until adoption.

## Quality guards
- Identity verification (existing _verify_identity) still gates every variant.
