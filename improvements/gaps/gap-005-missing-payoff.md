# gap-005: Quote-to-panel OCR cross-check at script time

**Stage:** script | **Fault types:** `missing_payoff` | **Priority:** 5

## Problem
Narration quotes dialogue ("Yoonhwan!!!", "Jaehwan, you punk...") but the scene's panels
don't contain that line — 4 HIGH missing_payoff faults. Detected only post-render by
review_video.py `narration_quotes` (:171). Also evidence of quote truncation at apostrophes
("Let", "re all thinking:") suggesting a quote-extraction bug worth fixing while here.

## Candidate directions
1. Deterministic: at script time, for every speech-verb-anchored quote, fuzzy-match
   (rapidfuzz, MIT) against OCR `dialogue` text of the scene's panels; if no panel contains
   it, swap in the panel that does (OCR index gives us the mapping for free).
2. If no panel anywhere contains the quote → rewrite narration to paraphrase (existing
   `_fix_narration` pattern in review_video.py:401).
3. Fix quote regex to handle apostrophes inside quotes.

## Acceptance criteria
- missing_payoff on golden chapter: 4 → 0.
- No new irrelevant_panel faults introduced by swaps (relevance gate from gap-001 applies).

## Quality guards
- Fuzzy threshold tuned against OCR noise (translated/stylized text); log all swaps.
