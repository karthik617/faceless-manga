# gap-008: Music seam compatibility

**Stage:** render | **Fault types:** `music_seam` (human-checklist item, not auto-detected yet) | **Priority:** 8

## Problem
Music sections crossfade with no key/energy matching (`panel_render.py:_build_music_bed:222`).
Human checklist on golden chapter flags 3 seams (Suspense→Emotional 3:26, →Dramatic 7:11,
→Epic 10:07) for hard cut / key clash. Currently only checkable by a human.

## Candidate directions
1. librosa (ISC) key + tempo estimation on candidate beds at fetch time
   (`fetch_music.py:fetch_plan:263`); prefer adjacent-section tracks with compatible keys
   (circle of fifths distance ≤2) and tempo delta ≤15%.
2. Beat-aligned crossfade points (snap seam to nearest downbeat).
3. Add a deterministic `music_seam` metric to benchmark.py: spectral-flux discontinuity
   across the seam window — makes this measurable without a human.

## Acceptance criteria
- Seam spectral-flux discontinuity metric drops ≥30% vs baseline on golden chapter.
- Music fetch still succeeds when catalog offers no compatible pair (graceful fallback).

## Quality guards
- Mood correctness beats key compatibility — never swap mood category to win the metric.
