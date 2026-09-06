# PanelBreak — Outro / End Screen (20s) · Editor Script

**Canvas** 1920×1080 · 30fps
**Runs over** the final 5–20s of every video. YouTube end-screen elements can only appear in this window — hold a beat of real content or a static plate behind it.
**Assets** `logo/panelbreak-icon-ink-1024.png` · `logo/panelbreak-lockup-ink-1904.png`
**Spoken line:** "That's the arc — broken down. Tap subscribe, and I'll see you in the next panel."

---

## Layout (YouTube end-screen safe zones)
```
┌─────────────────────────────────────────────┐
│                                              │
│   [icon] PANELBREAK        ┌──────────────┐  │
│   ▶ SUBSCRIBE              │ WATCH NEXT   │  │  ← video slot 1
│   @PanelBreak · Tue & Fri  └──────────────┘  │
│                            ┌──────────────┐  │
│                            │ MORE ARCS    │  │  ← video slot 2
│                            └──────────────┘  │
│                                              │
└─────────────────────────────────────────────┘
        LEFT = brand + sub          RIGHT = 2 video slots
```
- Keep every element inside the **inner 90% title-safe** margin.
- Left column: subscribe element snaps beside the channel bug.
- Right column: top = "best for viewer" (YT auto), bottom = a specific next arc.

---

### Beat 01 · SLIDE-IN — 00:00–00:02
- Background: ink `#131110` + halftone, or the dimmed/blurred last frame of content.
- Left group (icon + wordmark) slides in from left, hard ease-out; small CMYK settle on the wordmark.
- **SFX:** outro music loop starts (mid-energy, on-brand).

### Beat 02 · SUBSCRIBE PULSE — 00:02–00:06
- `▶ SUBSCRIBE` chip (vermilion fill, ink text) scales in, then a gentle 2-frame pulse every ~2s to draw the eye.
- Handle + schedule line fades up under it.
- VO spoken line lands here.

### Beat 03 · VIDEO SLOTS — 00:06–00:14
- Two right-side slots draw on with a 2px dashed-cyan border → then fill with YT thumbnails.
- Label each: "WATCH NEXT" / "MORE ARCS" (Space Mono, small).
- Stagger their entrance by ~6 frames.

### Beat 04 · HOLD & RESOLVE — 00:14–00:20
- Everything holds steady (viewers need time to click — don't animate under their cursor).
- Music resolves on the final downbeat at ~00:19.5; last frame is clean and static.
- **No fade-under-talk**; let the loop land.

---

### Checklist
- [ ] All interactive elements inside title-safe 90%.
- [ ] Subscribe pulse is subtle — never covers a clickable thumbnail.
- [ ] Hold the last 5s nearly static so viewers can actually click.
- [ ] Music ends resolved, not faded.
- [ ] Reuse identical timing across all uploads → viewers learn the rhythm.
