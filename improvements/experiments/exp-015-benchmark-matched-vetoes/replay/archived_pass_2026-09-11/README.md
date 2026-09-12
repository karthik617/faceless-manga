# Archived original 15/15 replay artifacts (recovered 2026-09-12)

These are NOT outputs of a current run. They were recovered from the opencode
session snapshot store
(`~/.local/share/opencode/snapshot/6f7f8f4d…/9b0f3a22…`, git blobs
3e9d6967 / fc7013d5 / c886892 / d91c0466 / 0328b001) and correspond to the
original `replay_acceptance.py` 15/15 PASS executed 2026-09-11 17:48:28
(opencode DB part `prt_09067adb4001kZ0KLvsBrex21R`, wall 21.3 s), when the
/tmp exp-004 bench videos still existed. They document that C2b/C2c/C2d and
C5b passed against the real fixtures, with per-pair frame-hash proofs
(e.g. scene-0 t6.5 md5 173af57514a963705dbde731db4f3f3a exempt; scene-4
t106.6 NOT exempt — the render-framing crop — netted against removed
scene-3 pair). The live suite cannot reproduce these because the bench
script/TTS were never archived (see IMPLEMENTATION.md fixture-restoration
note).
