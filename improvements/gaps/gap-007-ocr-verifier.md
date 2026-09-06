# gap-007: Local manga-ocr verifier/fallback

**Stage:** script | **Fault types:** `missing_payoff`, `irrelevant_panel` (indirect) | **Priority:** 7

## Problem
Vision-LLM OCR (`script_from_panels.py:141`, batches of 6) degrades silently to empty reads
on parse failure; filename alignment is positional and can drift. Bad reads poison panel
selection, quotes, and payoff checks downstream. No independent verification exists.

## Candidate directions
1. `manga-ocr` (Apache-2.0, HF model, CPU-viable) as second reader on panels where the
   vision read returned empty/sparse — note: JP-trained; verify usefulness on EN scanlations.
2. For EN text: PaddleOCR / EasyOCR / Tesseract as the cross-checker instead.
3. Agreement metric: fuzzy dialogue overlap between readers → flag panels below threshold
   for a single retry with a stricter prompt.

## Acceptance criteria
- Empty/sparse read rate on golden chapter panels drops ≥50%.
- Dialogue-recovery agreement metric implemented and reported by benchmark.py.
- OCR step wall-time ≤2x (cache both reads).

## Quality guards
- Verifier is advisory: it triggers retries, never silently overwrites the vision read.
