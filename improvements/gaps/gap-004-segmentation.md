# gap-004: Bubble/art-aware webtoon segmentation

**Stage:** segment | **Fault types:** `cropped_content`, `empty_screen`, `unreadable_text` | **Priority:** 4

## Problem
Webtoon splitter blind-splits gutterless stretches at the "flattest" row (`segment_panels.py:139`)
— cuts land through bubbles/art. Near-black dramatic beats are dropped entirely (:197).
Downstream: 6 MEDIUM cropped_content faults (bubbles cut top/bottom), fragments needing the
broken-panel guard in panel_render.py:531.

## Candidate directions
1. ML panel detectors: Magi (manga-whisperer), comic-panel YOLO models, DASS-Det — check
   license + CPU feasibility.
2. Cheaper: bubble-aware splits — run comic-text-detector / blob detect first, forbid split
   rows intersecting text boxes; choose next-flattest legal row.
3. Keep intentional black beats: emit them tagged `kind=beat` instead of skipping.

## Acceptance criteria
- cropped_content faults on golden chapter: 6 → ≤2.
- Zero splits through detected text boxes on the golden raw strip (deterministic check).
- Segment step wall-time ≤3x current.

## Quality guards
- Panel count sanity: within ±30% of current segmentation (no explosion into slivers).
- Reading order preserved; panels_index.json schema unchanged.
