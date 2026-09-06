# PanelBreak — Intro (6s) · Editor Script

**Canvas** 1920×1080 · 30fps · safe-title inside 90%
**Palette** Ink `#131110` · Paper `#F7F2E9` · Vermilion `#FF3B3B` · Cyan `#22C7E9`
**Fonts** Anton (wordmark) · Space Mono (tagline)
**Assets** `logo/panelbreak-icon-ink-1024.png` · `logo/panelbreak-lockup-ink-1904.png`
**Two cuts:** full **6s** = channel trailer / first-video. Regular uploads → use the **2.4s SLAM cut** (start at Beat 03, end Beat 05).

---

### Beat 01 · IGNITE — 00:00–00:01.0
- Full-frame ink `#131110` with a faint halftone dot texture (opacity 6%).
- One vermilion dot at exact center: scale 0 → 1 over 8 frames, ease-out (overshoot 110% then settle).
- Whole frame: subtle 2% zoom-in (Ken Burns) throughout the intro.
- **SFX:** deep sub-rumble builds.

### Beat 02 · RUSH — 00:01.0–00:02.0
- Speed lines (thin paper-white streaks) sweep INWARD from all four edges toward the center dot.
- Motion blur high; converge and "collapse" into the dot on the last 3 frames.
- Optional 1-frame white flash-frame on impact point at 00:01.9.
- **SFX:** rising whoosh / riser, pitch climbs.

### Beat 03 · IMPACT — 00:02.0–00:03.8  ← *SLAM cut starts here*
- Wordmark **PANELBREAK** (Anton) slams from 130% → 100% scale in 4 frames, hard ease-out.
- **CMYK misregister:** duplicate the wordmark ×3 layers — vermilion offset (−6,−5)px, cyan offset (+5,+5)px, ink on top. On impact frame, offsets are exaggerated (×2), then settle to rest over 6 frames.
- Camera shake: 3-frame decaying shake (±8px) on impact only.
- Ink-splat mask reveal behind the mark (paper-white splat, quick).
- **SFX:** bass hit + ink-splat + short sub-drop. **This is the loudest moment.**

### Beat 04 · LINE — 00:03.8–00:05.0
- Tagline `every arc, explained_` (Space Mono) types on beneath the wordmark, 1 char ~1.5 frames; blinking cursor `_`.
- Tiny page-flip wipe (paper corner) transitions the tagline in.
- **SFX:** paper flip + soft mechanical key-clicks per character.

### Beat 05 · SETTLE — 00:05.0–00:06.0
- Wordmark + tagline collapse/scale-down into the **icon bug** (`panelbreak-icon-ink-1024.png`) at center, 10 frames.
- Single vermilion flash (2 frames) → **HARD CUT** to content (no fade).
- **SFX:** snap + short tail reverb, then silence for the cut.

---

### Motion checklist
- [ ] Everything eases OUT (fast in, settle) — nothing linear.
- [ ] Only ONE loud beat (Impact). Don't let every beat peak.
- [ ] End on a hard cut, never a dissolve — energy stays high.
- [ ] Loudness target ≈ −14 LUFS integrated; true-peak ≤ −1 dBTP.
- [ ] Respect motion economy: if it feels busy, cut Beat 02's flash-frame.
