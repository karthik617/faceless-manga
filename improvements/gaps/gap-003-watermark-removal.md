# gap-003: Scanlation watermark detect + inpaint

**Stage:** clean | **Fault types:** `watermark` | **Priority:** 3

## Problem
Scanlation watermarks (e.g. FLAMESCANS.ORG bottom corners) survive to the final video.
4 HIGH faults on golden chapter (scenes 1, 13, 15, 20). Also a legal-optics issue
(README legal section: honor source, avoid aggregator branding).

## Evidence
- review.md lines: 0:39/0:44 title-card watermark, 6:08 / 7:19 / 10:13 FLAMESCANS.ORG.

## Candidate directions
1. Detect: OCR corner regions (existing vision reads already flag some) or template/text
   detect (comic-text-detector, EAST/PP-OCR) restricted to bottom/top 12% bands.
2. Remove: cv2 Telea inpaint (already used in clean_bubbles.py cv tier) for small marks;
   LaMa (`simple-lama-inpainting`, Apache-2.0) for larger regions.
3. Alternative: crop-safe reframe when the mark sits in dead margin space.

## Acceptance criteria
- watermark HIGH faults on golden chapter: 4 → 0.
- No visible inpaint smearing on sampled frames (evaluator attaches before/after crops).
- Clean step wall-time ≤ +60s per chapter (CPU).

## Quality guards
- Never inpaint regions overlapping detected art/bubbles (mask must be band-limited).
- Cache results in panels_clean/ like existing tiers; idempotent.
