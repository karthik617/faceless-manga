#!/usr/bin/env python3
"""script_from_panels.py — STAGE 3: OCR panels + write the recap script.

Two cached sub-steps:
  1. READ  — batch panels through gateway.llm_vision() -> per-panel JSON
             {panel, dialogue:[{speaker,text}], narration_text, sfx, scene_beat}.
             Cached to <slug>.ocr.json so re-runs are free.
  2. SCRIPT — feed the ordered reads to gateway.llm_text(RECAP_PROMPT) -> the
             extended scene JSON that panel_render.py consumes.

The output JSON is a superset of make_video.py's schema: each scene carries a
"panels" list (real art file paths) instead of an "image_prompt".

Usage:
    python3 script_from_panels.py --panels output/<slug>/panels \
        --out output/<slug>/<slug>.json --series "Series" --chapter 179 --mode webtoon
"""
import argparse
import json
import re
import sys
from pathlib import Path

import gateway

READ_BATCH = 6  # panels per vision call

READ_INSTRUCTION = """You are reading manga/manhwa panels for a recap. I will show you {n} panels IN READING ORDER; their filenames are, in order: {names}.

For EACH panel, output one JSON object with:
- "panel": the filename (use the names above, in order)
- "dialogue": list of {{"speaker": "<name or 'unknown'>", "text": "<spoken words>"}} read from speech bubbles (empty list if none)
- "narration_text": any narration/caption-box text (not in a bubble), or ""
- "sfx": notable sound effects shown, or ""
- "scene_beat": one short factual sentence describing what visibly happens in the panel
- "kind": classify the panel content — one of:
    "story"     (normal story art: characters, action, settings)
    "cover"     (chapter cover / series title art / logo card)
    "credits"   (scanlator or publisher credits, translator notes)
    "endmatter" (footnotes, glossary, novel excerpts, next-chapter teasers,
                 end cards — anything after the story ends)
    "textonly"  (a panel that is ONLY floating text/narration with no art)
- "quality": "ok", or "cut" if artwork/faces/speech bubbles are visibly
    truncated at the top or bottom edge of the image (a bubble sliced through,
    a face half missing, a system window cut off mid-box), or "sparse" if the
    panel is nearly empty (mostly flat background with a tiny element)

Attribute speakers by bubble tail/position and keep names consistent across panels. Do NOT invent dialogue that is not written. Output ONLY a JSON array of {n} objects, no prose, no markdown fence."""

RECAP_PROMPT = """Act as an ELITE anime YouTube essayist-scriptwriter for the channel "{channel}" (the style of top analysis channels like Shonen Flux). Write a story-driven, documentary-style narrated ESSAY covering ONE chapter of {series} (chapter {chapter}). Not a breathless recap — a told story with a thesis, rising tension, a payoff, and room to breathe.

Below are the ordered panels of the chapter, already read as JSON (reading order). Each item has {{panel, dialogue, narration_text, sfx, scene_beat}}.

PANEL READS:
{reads}

STRICT FRAMEWORK — follow every rule:
1. THE HOOK (scene 0): open on the dramatic THESIS of the chapter — name the characters and the stakes in the first sentence, then state what "nobody saw coming" as an open loop (do NOT spoil the payoff). End the hook with a clean transition line like "And without further ado, let's get into it." NEVER "welcome back", never "in this chapter".
   HOOK VISUALS ARE CRITICAL FOR RETENTION: the hook scene's "panels" MUST be dramatic STORY panels (extreme facial expressions, big action beats — pulled from anywhere in the chapter as a flash-forward). NEVER use covers, title/logo art, calm establishing shots, or text-only panels in the hook. A viewer must see something shocking within the first seconds. SPOILER GUARD: do NOT use the panel that carries the chapter's single biggest emotional payoff/reveal (the exact image the essay builds toward) — tease with adjacent action/reaction panels instead, so the climax image still lands fresh when its scene arrives.
2. ESSAY STRUCTURE: after the hook, tell the chapter as a STORY with acts:
   - PRESENT ACTION: present-tense play-by-play of what is happening now.
   - DEEP DIVE / BACKSTORY: when the chapter gives history, motivation or a flashback, SLOW DOWN and tell it fully — this is the emotional heart. Use past tense here; let sentences lengthen.
   - RETURN + PAYOFF: come back to the present and land the chapter's biggest beat.
   - REFLECTION: 1-2 closing scenes on what it means / what's next, ending on an open question or forward tease.
3. NARRATIVE TEXTURE: name EVERY character involved and narrate their reactions ("You can see it on his face..."). Weave quoted dialogue as delivered lines. Address the viewer directly sometimes ("Trust me, you do NOT want to miss this"), and ask the viewer questions ("Will it hold?"). Vary rhythm: punchy lines in action, longer flowing lines in backstory.
4. LENGTH: this is a LONG-FORM essay. Write 18-30 scenes, each 4-8 sentences (~15-25 seconds spoken). Total spoken length should target 6-10 minutes. Cover the panels in order but LINGER where the story is deepest, and compress connective tissue.
5. CTA: exactly ONE like/subscribe line, in the FINAL scene only, woven into the closing reflection (e.g. "And if this series has been hitting for you, drop a like, subscribe, and I'll catch you in the next one."). Set that final scene's "cta" to true. NO mid-video CTA.
6. Never invent plot the panels don't show.

Per-scene fields:
- "narration": the spoken lines for this beat.
- "panels": the panel filenames this scene shows, in order (e.g. "panels/p0001.png"). Cover panels roughly in order across scenes. PANEL SELECTION RULES: every panel shown must DEPICT what the narration is saying at that moment (a viewer glancing at the screen must see the thing being described). Prefer panels whose read has kind="story" and quality="ok". A panel tagged "textonly" may be used ONLY when the narration is quoting that exact text (e.g. a system message being read aloud). Skip panels whose read is tagged quality="cut" unless nothing else depicts the beat.
- "pace": one of "hype" (action peaks: rapid cuts), "normal" (standard storytelling), "quiet" (backstory, monologue, emotional dwell: the camera sits on one panel). Use "quiet" generously in the deep-dive act — the contrast is what makes hype hit.
- "keywords": 1-3 UPPERCASE impact words drawn from THIS scene's narration to highlight on screen (empty list on quiet scenes).
- "motion": a short Ken Burns hint ("slow push-in", "fast punch-in", "pan left", "zoom out").
- "caption": a 3-6 word on-screen line for the scene.
- "cta": true ONLY on the final scene.
- "emphasis": true on the MOST dramatic beats (big reveal, a hit, a scream, a twist) — at most 1 in 4 scenes, always on the hook. FALSE on quiet scenes.
- "sfx_events": OPTIONAL list of sound effects tied to VISIBLE PHYSICAL ACTION in this scene's panels (a door slamming, a scream, a punch landing, a crowd roaring, glass breaking). The action must ALSO be described by a word in the narration. 0-2 per scene; MOST scenes should have none — silence is better than a wrong sound. Each entry: {{"cue": "<short search phrase, e.g. 'door slam'>", "word": "<the exact narration word to sync to>", "gain": <optional dB, default -7>}}.
- "ambience": OPTIONAL scene-level background atmosphere loop when the SETTING clearly calls for one ("crowd murmur", "rain", "night crickets", "city traffic", "wind"). Omit on most scenes; set it only when a location strongly implies it and keep the SAME value across consecutive scenes in the same location.

Top-level "music_plan": divide the video into 2-4 MUSIC SECTIONS that track the essay's acts, each {{"mood": <one of Epic, Cinematic, Dramatic, Emotional, Sad, Inspiring, Uplifting, Powerful, Hype, Energetic, Action, Dark, Suspense, Mysterious, Calm>, "from_scene": <0-based scene index where this section starts>}}. The first section MUST start at 0. Example: tense opening (Suspense) -> backstory (Emotional) -> comeback payoff (Epic).

Do NOT write "image_prompt" — we display the real art.

For the two thumbnails, pick panels showing ONE clear dramatic MOMENT with the MOST extreme facial emotion (shock, rage, hyper-focus) and a single large focal character — avoid multi-panel spreads, tiny distant figures, panels that are mostly speech bubbles, and (same spoiler guard as the hook) the chapter's climax-payoff panel. "text" is a short EVENT-based hook in CAPS, 3-5 words, describing what HAPPENS — a narrative beat, not a label (good: "THEY COULDN'T STOP HIM", "IT ALL FALLS APART"; bad: "INVINCIBLE MONSTER"). "highlight" is the single most emotional word from the text to render in red. "emotion" names the mood and also picks the caption font — use one of: shock, rage, horror, fear, menace, dark, sad, somber, chaos.

Output EXACTLY one JSON object (no markdown fence):
{{
  "title": "{slug}",
  "voice": "en-US-AndrewMultilingualNeural",
  "tts_rate": "+4%",
  "tts_pitch": "-4Hz",
  "music": true,
  "hook_scenes": 1,
  "music_plan": [
    {{"mood": "Suspense", "from_scene": 0}},
    {{"mood": "Emotional", "from_scene": 6}},
    {{"mood": "Epic", "from_scene": 14}}
  ],
  "source": {{"series": "{series}", "chapter": "{chapter}", "mode": "{mode}"}},
  "thumbnails": [
    {{"name": "thumb1", "text": ["THEY COULDN'T STOP HIM"], "highlight": "STOP", "panel": "panels/pXXXX.png", "emotion": "shock"}},
    {{"name": "thumb2", "text": ["THE WAR BEGINS"], "highlight": "WAR", "panel": "panels/pXXXX.png", "emotion": "rage"}}
  ],
  "scenes": [
    {{"narration": "...", "panels": ["panels/p0001.png"], "pace": "hype", "keywords": ["SHATTERS"], "motion": "fast punch-in", "caption": "It All Breaks", "cta": false, "emphasis": true, "sfx_events": [{{"cue": "door slam", "word": "SLAMS"}}], "ambience": "crowd murmur", "blur_bg": true}}
  ]
}}
"""


def _list_panels(panels_dir):
    files = [p for p in Path(panels_dir).glob("p*.png")]
    return sorted(files, key=lambda p: p.name)


def _strip_fence(text):
    """Remove ```json ... ``` fences the model may add."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _extract_json(text, expect="object"):
    """Best-effort parse: strip fence, else grab the first {...} / [...] span."""
    t = _strip_fence(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    open_c, close_c = ("[", "]") if expect == "array" else ("{", "}")
    i = t.find(open_c)
    j = t.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        return json.loads(t[i:j + 1])
    raise ValueError(f"could not parse JSON {expect} from model output:\n{text[:400]}")


def read_panels(panels, batch=READ_BATCH):
    """Vision-read panels in batches; return a flat list of per-panel dicts."""
    reads = []
    for start in range(0, len(panels), batch):
        chunk = panels[start:start + batch]
        names = ", ".join(p.name for p in chunk)
        instr = READ_INSTRUCTION.format(n=len(chunk), names=names)
        raw = gateway.llm_vision(chunk, instr)
        try:
            arr = _extract_json(raw, expect="array")
        except ValueError as e:
            print(f"  ! read parse failed for batch {start}: {e}")
            # degrade: emit empty reads for this batch so pipeline continues
            arr = [{"panel": p.name, "dialogue": [], "narration_text": "",
                    "sfx": "", "scene_beat": ""} for p in chunk]
        # normalize + align filenames positionally if model drifted
        for k, p in enumerate(chunk):
            item = arr[k] if k < len(arr) and isinstance(arr[k], dict) else {}
            reads.append({
                "panel": p.name,
                "dialogue": item.get("dialogue", []),
                "narration_text": item.get("narration_text", ""),
                "sfx": item.get("sfx", ""),
                "scene_beat": item.get("scene_beat", ""),
                "kind": item.get("kind", "story"),
                "quality": item.get("quality", "ok"),
            })
        print(f"  read {min(start + batch, len(panels))}/{len(panels)} panels")
    return reads


def usable_reads(reads):
    """Drop panels the scriptwriter must never show: covers/logo cards,
    scanlator credits, and post-story end-matter (footnotes, glossaries,
    teasers). 'textonly' and 'cut' panels stay available (the story may need
    them) but carry their tags so the prompt can steer around them."""
    out = []
    for r in reads:
        if r.get("kind") in ("cover", "credits", "endmatter"):
            continue
        out.append(r)
    return out or reads


def draft_script(reads, series, chapter, slug, mode, channel="Manga Recap"):
    n_all = len(reads)
    reads = usable_reads(reads)
    if len(reads) < n_all:
        print(f"  script: excluded {n_all - len(reads)} non-story panel(s) "
              f"(covers/credits/end-matter)")
    prompt = RECAP_PROMPT.format(
        channel=channel, series=series, chapter=chapter, slug=slug, mode=mode,
        reads=json.dumps(reads, indent=1))
    raw = gateway.llm_text(prompt)
    data = _extract_json(raw, expect="object")
    # guarantee required fields / slug
    data.setdefault("title", slug)
    # Voice/delivery profile: documentary-essay cadence — near-natural rate
    # with a slightly deeper pitch. (The old +12% "trailer" rush reads as
    # synthetic; analysis channels talk TO you, not at you.)
    data.setdefault("voice", "en-US-AndrewMultilingualNeural")  # deep male
    data.setdefault("tts_rate", "+4%")
    data.setdefault("tts_pitch", "-4Hz")
    data.setdefault("hook_scenes", 1)
    if "scenes" not in data or not data["scenes"]:
        sys.exit("model returned no scenes")
    return data


def build(panels_dir, out_json, series, chapter, mode, channel="Manga Recap",
          ocr_cache=None):
    panels = _list_panels(panels_dir)
    if not panels:
        sys.exit(f"no panels in {panels_dir}")
    slug = Path(out_json).stem
    ocr_cache = Path(ocr_cache) if ocr_cache else Path(out_json).with_suffix(".ocr.json")

    if ocr_cache.exists() and ocr_cache.stat().st_size > 0:
        print(f"  [cached] reads <- {ocr_cache.name}")
        reads = json.loads(ocr_cache.read_text())
    else:
        print(f"  reading {len(panels)} panels via vision...")
        reads = read_panels(panels)
        ocr_cache.write_text(json.dumps(reads, indent=2))
        print(f"  [ocr] {ocr_cache}")

    print("  drafting recap script via LLM...")
    data = draft_script(reads, series, chapter, slug, mode, channel)
    Path(out_json).write_text(json.dumps(data, indent=2))
    print(f"  [script] {out_json} ({len(data['scenes'])} scenes)")
    return out_json


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panels", required=True, help="panels/ dir")
    ap.add_argument("--out", required=True, help="output <slug>.json path")
    ap.add_argument("--series", required=True)
    ap.add_argument("--chapter", required=True)
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--channel", default="Manga Recap")
    args = ap.parse_args()
    build(args.panels, args.out, args.series, args.chapter, args.mode,
          args.channel)


if __name__ == "__main__":
    main()
