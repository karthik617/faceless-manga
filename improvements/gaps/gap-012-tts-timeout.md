# gap-012: edge-tts stream hang (no socket timeout)

**Stage:** render | **Fault types:** (reliability) | **Priority:** 11

## Problem
Round-1 eval: Arm A's render hung ~55 minutes mid-stream during scene 6 TTS;
resume logged 7 tts retries. `make_video.tts` (in ~/faceless-youtube — NOT
modifiable) has no socket timeout, so a stalled edge-tts websocket blocks the
whole pipeline indefinitely.

## Candidate directions
Since make_video.py must not be modified: wrap TTS calls from the manga side —
a watchdog subprocess/thread with a hard timeout (e.g. 120s per scene audio)
that kills and retries, or pre-generate all scene audio via a small wrapper
before invoking the render path. Check whether panel_render.py can pre-warm
`_work/a*.mp3` itself (audio reuse on resume already works — eval used it).

## Acceptance criteria
- A simulated stall (network blackhole) recovers in <5 min without human help.
- No change to audio output bytes on the happy path.

## Quality guards
- Retries must not truncate audio (verify duration vs word timings).
