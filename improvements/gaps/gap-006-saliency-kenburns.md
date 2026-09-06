# gap-006: Saliency-guided Ken Burns focal points

**Stage:** render | **Fault types:** `cropped_content`, `irrelevant_panel` | **Priority:** 6

## Problem
Zoom is center-anchored only (`panel_render.py:609`); the LLM's `motion` field is ignored
(comment :600 — pans removed as distracting). Push-ins can land on empty space or crop the
face/bubble that matters (review: "Face cropped at top and bottom, only chin/neck visible").

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
