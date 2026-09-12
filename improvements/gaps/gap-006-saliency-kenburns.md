# gap-006: Saliency-guided Ken Burns focal points

**Stage:** render | **Fault types:** `cropped_content`, `irrelevant_panel` | **Priority:** 6

## Problem
Zoom is center-anchored only (`panel_render.py:609`); the LLM's `motion` field is ignored
(comment :600 — pans removed as distracting). Push-ins can land on empty space or crop the
face/bubble that matters (review: "Face cropped at top and bottom, only chin/neck visible").

## Evidence (additions)
- ch4 (2026-09-10, `output/the-world-after-the-fall-ch4/review.md:50-54`):
  manual pass explicitly deferred the MEDIUM cropped_content framing items to
  this gap ("framing and layout engine issues … not per-panel choices").
  Concrete sites: 1:55 scene 4 (bubbles cut off at top, review.md:80), 4:37
  scene 12 (face + bubble cut off at top, :85), 5:26 scene 14 (bubble text cut
  mid-sentence at bottom, :87), 8:13 scene 22 (forehead/hair cropped at top,
  :91) — recurs across ch2/ch3/ch4.

## Candidate directions
1. Saliency: anime-face detectors (lbpcascade_animeface — BSD, yolov8-animeface),
   cv2.saliency spectral residual as fallback; anchor zoom on the salient box.
2. Reuse OCR text boxes: never let the zoom window exclude the panel's dialogue bubble
   when narration quotes it.
3. Honor `motion` semantically: "push-in on X" → saliency box; keep the no-sideways-pan rule.

## Acceptance criteria
- cropped_content faults on golden chapter drop ≥50%.
- Sampled frames show zoom centers on faces/action (evaluator attaches grid).
- Render wall-time increase ≤1.2x.

## Quality guards
- Fallback to center anchor when saliency confidence low — never worse than today.
