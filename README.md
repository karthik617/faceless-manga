# faceless-manga

Turn downloaded manga/manhwa chapters into narrated **recap** YouTube videos.
Sibling to `faceless-studio` and `faceless-youtube` — same Python-CLI shape,
same Athena/edge-tts/ffmpeg stack — but instead of AI-generating every visual,
it **displays the real chapter panels** (Ken Burns over the actual art) with an
AI-written recap voiceover.

## Pipeline

```
discover -> (ranked list)   AniList trends scored for recap suitability [optional]
download -> raw/            gallery-dl | mangadex-downloader | manual folder
segment  -> panels/         OpenCV: page/strip -> panels in reading order
clean    -> panels_clean/   (optional) blank speech-bubble text
script   -> <slug>.json     multimodal OCR + recap LLM (extended scene JSON)
render   -> <slug>.mp4/.srt real panels + blurred-bg Ken Burns, TTS, captions
extras   -> thumbs/         high-CTR thumbnails from real panels
short    -> <slug>_short.mp4  9:16 Short cut from the long video's best ~40s hook
package  -> upload_package.md  asset manifest + titles/description/tags + checklist
```

The renderer **imports** `~/faceless-youtube/pipeline/make_video.py` and reuses
its edge-tts word timing, branded intro/outro J-cuts, sidechain music, word-timed
captions, and the single loudnorm/H.264-CRF18/faststart final encode. That file
is never modified.

## Setup

```bash
cd ~/faceless-manga
python3 -m venv venv
./venv/bin/pip install -r pipeline/requirements.txt
# ffmpeg: uses the static build in ~/.local/bin (same as faceless-youtube)
```

Gateway token: set `ATHENA_TOKEN` in the env, or place it in
`pipeline/athena_token.txt` (gitignored). **Never commit the token.**

## Usage

```bash
# CHANNEL CONTINUITY — a real channel commits to ONE series and recaps it in
# order. Do this ONCE, then --auto always makes the next chapter (no topic-hop).
./venv/bin/python3 pipeline/channel.py set --title "Absolute Regression"
./venv/bin/python3 pipeline/channel.py status          # what's current + progress
./venv/bin/python3 pipeline/channel.py next            # what the next video is
#   ...channel auto-advances chapter→chapter, and only promotes a NEW series
#   once the current one is FINISHED and fully recapped.

# STAGE 0 — RAW trend discovery (advisory only; use to pick a series to commit).
# NOTE: trends reorder every run — don't drive uploads straight off this.
./venv/bin/python3 manga.py --discover                       # top 10, then exits
./venv/bin/python3 pipeline/research_trends.py discover --top 15 --out trends.json

# ...but the HOTTEST manga is usually LICENSED — MangaDex can only host its
# newest chapter, so you can't recap it in order. Add --fetchable to probe
# real availability and rank series you can actually download FROM CHAPTER 1:
./venv/bin/python3 pipeline/research_trends.py discover --fetchable --top 10
#   ✅ ch1+  = binge-able in order (channel-ready)
#   🟡 partial = only later/newest chapters fetchable
#   ❌ locked = not legally downloadable
# channel.py auto-promotion already uses this — it only commits to ✅ series.

# manual folder of page images (no scraping — start here)
./venv/bin/python3 manga.py --folder ~/panels --series "My Series" --chapter 1 --mode auto

# legal MangaDex source
./venv/bin/python3 manga.py --url https://mangadex.org/chapter/<id> \
    --series "My Series" --chapter 179 --mode webtoon --karaoke

# fully hands-off, CHANNEL-DRIVEN: makes the current series' NEXT chapter,
# then records it as done so the next --auto run advances one chapter.
./venv/bin/python3 manga.py --folder ~/panels --auto     # series/chapter from channel state

# hands-off but explicit series (bypasses channel continuity)
./venv/bin/python3 manga.py --folder ~/panels --series "My Series" --chapter 1 --auto

# just (re)cut the vertical Short from an already-rendered long video
./venv/bin/python3 pipeline/make_short.py output/my-series-ch1/my-series-ch1.mp4

# resume a project from a given step (after editing the script JSON)
./venv/bin/python3 manga.py --project output/my-series-ch179 --from render

# optional speech-bubble cleanup
./venv/bin/python3 manga.py --folder ~/panels --series S --chapter 1 --clean-bubbles         # cv tier
./venv/bin/python3 manga.py --folder ~/panels --series S --chapter 1 --clean-bubbles gemini  # AI inpaint
```

Steps are cached: re-running skips finished work. After `script`, the pipeline
pauses so you can edit `output/<slug>/<slug>.json` (narration, which panels each
scene shows) before rendering — pass `--yes` to skip the pause.

## Scene JSON (extended)

Superset of make_video's schema. Each scene points at real panel files:

```jsonc
{
  "title": "my-series-ch179",
  "voice": "en-US-AndrewMultilingualNeural",
  "tts_rate": "-4%",
  "music": true,
  "hook_scenes": 1,
  "source": { "series": "My Series", "chapter": "179", "mode": "webtoon" },
  "thumbnails": [ { "name": "thumb1", "text": ["HE FINALLY","SNAPS"], "panel": "panels/p0007.png" } ],
  "scenes": [
    { "narration": "...", "panels": ["panels/p0001.png"], "motion": "slow push-in",
      "caption": "The Throne Room", "blur_bg": true }
  ]
}
```

- `panels` present ⇒ real art is shown (never AI-generated). `panels_clean/` is
  auto-substituted when it exists.
- `blur_bg: true` fits a tall/portrait panel into 16:9 over a blurred, darkened
  copy of itself (no cropping) — the technique from `make_shorts.py`.

## Modules

| File | Stage | Notes |
|---|---|---|
| `pipeline/gateway.py` | — | Athena helpers: `llm_text`, `llm_vision` (OCR), `image_edit` |
| `pipeline/research_trends.py` | discover | AniList trend ranking (+Jikan fallback); advisory only — use to pick a series to commit |
| `pipeline/channel.py` | continuity | channel_state.json roster: single-series, in-order chapters, auto-advance when a series ends. Drives `--auto` |
| `pipeline/download_chapter.py` | download | gallery-dl / mangadex / folder / requests |
| `pipeline/segment_panels.py` | segment | OpenCV manga (R→L) + webtoon (projection) |
| `pipeline/script_from_panels.py` | script | vision read cache + recap prompt → JSON |
| `pipeline/verify_panels.py` | script | panel↔narration relevance gate (OCR rules + lexical + vision tiers); on by default, `--no-verify-panels` to skip |
| `pipeline/clean_bubbles.py` | clean | CV inpaint (default) or Gemini inpaint |
| `pipeline/panel_render.py` | render | imports make_video; `render_panel_scene` |
| `pipeline/layout_smart.py` | render | opt-in `--layout smart`: 3-panel grid composites with beat-synced reveals + guided-view punch-ins for multi-panel scenes (default `seq` = one panel at a time) |
| `pipeline/make_thumbs.py` | extras | real-panel background + text overlay |
| `pipeline/make_short.py` | short | cuts a 9:16 Short (best hook clip) from the long mp4; blur-fill + ASS captions + "full video" card |
| `manga.py` | all | resumable orchestrator (`STEPS`, `--from`) |

## ⚠️ Legal / copyright / monetization

Manga panels are **copyrighted**. A recap may need to qualify as **transformative
fair use / commentary**, which is jurisdiction- and fact-dependent and **not
guaranteed** — full 1:1 chapter dumps are the weakest position.

- Prefer **legal sources** (MangaDex permitted titles, official publisher apps).
  Scraping aggregator/pirate sites violates their ToS and often serves
  infringing copies.
- Expect YouTube **Content ID claims / strikes**; the "reused content" policy
  limits monetization of low-transformation compilations.
- AI narration/edits ⇒ tick YouTube's **synthetic-media** disclosure.
- Mitigate: heavy original commentary, avoid full dumps, credit the source, link
  the official release, honor takedowns.

**This is not legal advice.** Confirm you have the rights for a given series
before publishing.
