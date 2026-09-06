# gap-001: Panel-narration relevance verification

**Stage:** script | **Fault types:** `irrelevant_panel` (biggest cluster), `weak_hook` | **Priority:** 1

## Problem
Panel selection is LLM-prompt-only (`script_from_panels.py` RECAP_PROMPT) with no verification.
Result on golden chapter: 20+ HIGH `irrelevant_panel` faults — title cards during narration,
sound-effect-only frames while narration describes combat, dog-tag close-up during action beats.
Faults are only caught *after* a full render, in `review_video.py`.

## Evidence
- `output/the-world-after-the-fall-ch2/review.md`: scenes 1,2,3,7,8,10,11,12,19,23 all irrelevant_panel HIGH.
- `pipeline/script_from_panels.py:72` — panel rules live only inside the prompt.

## Candidate directions
1. CLIP / SigLIP image-text similarity: score each (panel, narration) pair at script time;
   reject below threshold, re-pick from OCR-tagged story panels.
2. Cheaper: reuse existing OCR reads (`scene_beat`, `kind`, `quality`) as a deterministic
   gate — never allow kind∈{cover,credits,endmatter,textonly} unless narration quotes that text.
3. Vision-LLM pairwise verify pass (gateway) — costlier per call but no new deps.

## Acceptance criteria
- HIGH `irrelevant_panel` count on golden chapter drops ≥50% vs baseline (20 → ≤10).
- Zero new fault types; no scene left panel-less (fallback substitution must fire).
- Script step wall-time increase ≤2x.

## Quality guards
- Verify substituted panels still follow reading order (no future-chapter spoiler jumps).
- weak_hook: hook scene must score above relevance threshold too.
