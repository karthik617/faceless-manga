# PanelBreak — Brand Assets

Faceless manga-recap YouTube channel. Everything here is reusable across every upload.

## logo/
| File | Use |
|------|-----|
| `panelbreak-icon.svg` / `-1024.png` / `-512.png` | Master icon (light bg), scalable + raster |
| `panelbreak-icon-ink.svg` / `-ink-1024.png` | Icon on dark/ink background |
| `panelbreak-favicon-32.png` | Favicon / tiny UI |
| `panelbreak-lockup.svg` / `-1904.png` | Horizontal logo + wordmark (light) |
| `panelbreak-lockup-ink.svg` / `-ink-1904.png` | Horizontal logo (ink) |

## social/
| File | Use | Exact size |
|------|-----|-----------|
| `panelbreak-avatar-800.png` | YouTube channel profile picture | 800×800 |
| `panelbreak-banner-2560x1440.png` | YouTube channel banner (art inside 1546×423 safe zone) | 2560×1440 |

## video/
| File | Use |
|------|-----|
| **`out/panelbreak-intro-6s.mp4`** | **Rendered intro** — 1920×1080, 30fps, H.264+AAC, ready to cut in |
| **`out/panelbreak-outro-20s.mp4`** | **Rendered outro / end screen** — 1920×1080, 30fps |
| `render_brand_video.py` | Regenerates both MP4s locally (PIL + ffmpeg). `python3 render_brand_video.py both` |
| `INTRO-6s_editor-script.md` | Shot-by-shot intro spec the renderer implements |
| `OUTRO-20s_editor-script.md` | End-screen layout + timing with YT subscribe & video slots |
| `AI-video-prompts.md` | Text-to-video prompts (Runway/Kling/Sora/Veo) if you'd rather AI-generate the motion |
| `AUDIO-cue-sheet.md` | SFX timeline, music, and voice spec |

### Regenerating the videos
```
cd brand/video && python3 render_brand_video.py both
```
Requires ffmpeg + Pillow + numpy + edge-tts and the Anton font installed locally.

**Outro voiceover** is generated with `edge-tts` (same tool the pipeline uses). Voice,
speaking rate, and the spoken line are constants at the top of `render_brand_video.py`:
```
VO_VOICE = "en-US-ChristopherNeural"   # warm, confident, mature
VO_RATE  = "+6%"
VO_LINE  = "That's the arc, broken down. Tap subscribe, and I'll see you in the next panel."
```
Change any of them and re-run. The VO is high-passed + compressed, and the music/SFX
bed **sidechain-ducks** under it so the words stay clear. During the VO the mix sits at
~-14 LUFS (YouTube target); the quiet music tail after is intentional so viewers can
read/click the end-screen cards.

**SFX:** intro = sub-bed + noise whoosh riser + layered impact (boom + ink-splat +
reverb tail) + typing ticks; outro = slide-in whoosh + subscribe "pop" + two slot
"clicks" + a warm 3-note pad bed. All synth-generated — swap in licensed tracks per
`AUDIO-cue-sheet.md` if you prefer, but these are clean enough to publish.

The right-side outro boxes are dashed placeholders for YouTube's clickable end-screen
video elements — you add the real videos in the YT upload flow.

## Brand quick-reference
- **Name:** PanelBreak · **Handle:** @PanelBreak · **Tagline:** Every arc, explained.
- **Colors:** Ink `#16130F` · Paper `#FAF7F0` · Vermilion `#FF2E2E` · Cyan `#0FB5D6`
- **Fonts:** Anton (display) · Hanken Grotesk (body) · Space Mono (specs)
- **Full visual brand board:** `../panelbreak-brand-kit.html`

## Regenerating PNGs
Edit any `.svg`, then run `bash render.sh` (uses headless Chrome). Anton font must be
installed locally for the wordmark to render.
