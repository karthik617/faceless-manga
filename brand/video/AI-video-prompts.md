# PanelBreak — Text-to-Video Prompt Sheet
For Runway Gen-3 · Kling · Luma · Sora · Veo · Pika. Generate silent clips, then add
the wordmark/tagline and audio in your editor (AI generators render text unreliably —
overlay `logo/panelbreak-lockup-ink-1904.png` on top instead of asking the model for text).

**Global style tokens** (paste into every prompt):
> black-and-white manga aesthetic, halftone dot shading, bold ink linework, dramatic
> speed lines, high contrast, vermilion-red `#FF3B3B` single accent color, cinematic,
> 2D anime, 16:9, no on-screen text, no watermark, no letters.

---

## INTRO clips

**INTRO-A · Ignite + Rush (2s)**
> Extreme close-up on pure black. A single glowing red dot appears at center and pulses.
> Sharp white manga speed-lines streak inward from all four edges, converging on the dot
> with heavy motion blur, building energy. Black-and-white manga style, halftone texture,
> one red accent, cinematic, 16:9, no text.
> `--motion high` · end on the convergence flash.

**INTRO-B · Impact burst (2s)** — use behind the wordmark slam
> A violent white ink-splatter explodes outward from center against black, manga action
> panel, radial speed lines, halftone dots, one vermilion-red accent flashing through the
> splatter, camera shake, cinematic impact. 16:9, no text, no letters.

**INTRO-C · Settle (2s)**
> Ink splatter and speed lines rapidly collapse inward into a small clean rounded square
> panel outline at center, calming to stillness, black background, single red play-triangle
> glowing inside, manga style, halftone. 16:9, no text.

## OUTRO clips

**OUTRO-BG · Ambient loop (20s, loopable)**
> Slow drifting field of soft halftone dots and faint diagonal manga speed-lines over a
> deep near-black background, gentle parallax, occasional slow vermilion-red light sweep,
> calm and atmospheric, seamless loop, cinematic, 16:9, no text.
> `--camera slow drift` · keep it LOW energy so subscribe/thumbnails read on top.

---

## Thumbnail-style B-roll (optional, reusable transitions)
> Manga page turning, dramatic panel-to-panel zoom, ink and halftone, red accent, quick
> whip-pan transition, black and white, 16:9, no text.

### Tips
- Generate 3–4 takes per prompt; pick the one whose motion peaks match the beat timing.
- Keep the model OFF text — all lettering is your rendered PNGs, layered in post.
- Grade every clip toward the palette: crush blacks to `#131110`, push the one accent to `#FF3B3B`.
- Reuse the SAME clips across videos; only the middle content changes. That's the point of a kit.
