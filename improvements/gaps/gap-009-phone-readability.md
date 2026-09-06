# gap-009: Phone readability — min text height + auto punch-in

**Stage:** render | **Fault types:** `phone_readability`, `unreadable_text` | **Priority:** 9

## Problem
System/UI boxes and small text render too small for phones (review 2:23: "System description
text in the orange box is too small to read comfortably on a phone"). Renderer has no
awareness of text size in the fitted frame.

## Candidate directions
1. Estimate rendered text height: OCR text boxes (from gap-004/007 detectors) scaled by the
   blur_bg fit ratio; if median line height < ~28px at 1080p, auto punch-in (existing
   emphasis zoom machinery) or tight_crop that region.
2. For textonly panels quoted by narration: full-frame the text region directly.

## Acceptance criteria
- phone_readability + unreadable_text faults on golden chapter: 4 → ≤1.
- No new cropped_content faults from over-zealous punch-ins.

## Quality guards
- Punch-in must respect bubble boundaries (no half-cut bubbles).
