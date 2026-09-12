#!/usr/bin/env python3
"""review_video.py — STAGE 5.5: human-like automated QC of the rendered video.

Fans out vision-LLM reviewers over the video (one call per scene batch) plus
deterministic frame checks, and produces review.json + review.md listing every
fault with its scene, timestamp, severity, and a suggested fix. Optionally
applies the safe fixes automatically (drop/replace bad panels in the script
JSON) so a re-render addresses them — the self-refine loop.

What it checks (mirrors the faults a human reviewer flags):
  DETERMINISTIC (cheap, every ~2s of video):
    - blank/near-black or near-white frames
    - frames whose content fraction is tiny (empty screen)
  VISION-LLM (per scene, sampled frames + the narration text):
    - does the frame DEPICT what the narration says? (relevance)
    - is artwork/face/bubble visibly cut off at frame edges? (crop)
    - is on-screen text legible and uncropped? (legibility)
    - is it a cover/credits/end-card rather than story art? (non-story)
    - hook check on scene 0: is the visual dramatic enough to stop a skip?

Usage:
    python3 review_video.py output/<slug>/<slug>.json \
        [--video output/<slug>/<slug>.mp4] [--workdir output/<slug>/_work]
        [--apply-fixes] [--jobs 4]
Exit code: 0 = pass (no high-severity faults), 2 = faults found.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

import gateway
from review_common import (ffprobe_dur as _ffprobe_dur, grab as _grab,
                           frame_stats as _frame_stats,
                           parse_json as _parse_json,
                           phash as _phash, hamming as _ham)

FRAME_STEP = 2.0          # deterministic scan interval (s)
BLANK_LUMA_LO = 8         # mean luma below -> black frame
BLANK_LUMA_HI = 247       # mean luma above -> white frame
MIN_CONTENT_FRAC = 0.04   # fraction of pixels differing >30 from median

REVIEW_INSTRUCTION = """You are a strict YouTube video quality reviewer for a manga-recap channel. I show you {n} frames sampled from ONE scene of the video, in order. The narrator is saying:

NARRATION: "{narration}"

{hook_note}For EACH frame, judge it like a human viewer would and output one JSON object:
- "frame": the frame index I gave you (0-based)
- "relevant": true/false — does the imagery DEPICT what the narration describes (characters/action/place mentioned)? Title cards, covers, credits, footnotes, glossaries and unrelated art are NOT relevant.
- "cropped": true/false — is a face, speech bubble, text box, or system window visibly CUT OFF at the frame's top/bottom (sliced mid-content)?
- "empty": true/false — is the frame mostly empty/flat (a black/white screen, one tiny word, a sliver of art)?
- "text_issue": true/false — is there prominent on-screen text that is unreadable or truncated mid-sentence?
- "watermark": true/false — is a scanlation/aggregator site watermark or URL visible (e.g. "...SCANS.ORG", site logos stamped on the art)?
- "small_text": true/false — is there dialogue or system-window text that matters to the story but is too small to read comfortably on a PHONE screen?
- "note": one short sentence naming the biggest problem, or "" if fine.

{payoff_note}Be strict: a frame showing ONLY the word "AND", a half-visible popup, or chapter-title typography while the narration describes action = relevant:false or empty:true. Output ONLY a JSON object: {{"frames": [array of {n} frame objects]{payoff_field}}}, no prose, no markdown fence."""

PAYOFF_NOTE = """PAYOFF CHECK: the narration explicitly voices these quoted lines:
{quotes}
After judging the frames, also decide: does at least one of the frames show the manga panel that CARRIES each quote (the speech bubble / text or the exact moment it is spoken)? Set top-level "payoff_missing" to a list of the quotes whose panel is NOT visible in any frame (empty list if all are covered).

"""

HOOK_NOTE = """THIS IS THE OPENING HOOK (first seconds of the video — retention-critical). Additionally set "hook_weak": true on any frame that would NOT stop a scroller: covers/logos/title typography, calm establishing shots, or empty frames. The hook must show dramatic story art.

"""


def scene_spans(workdir, n_scenes):
    durs, t, spans = [], 0.0, []
    for i in range(n_scenes):
        c = Path(workdir) / f"c{i:03}.mp4"
        d = _ffprobe_dur(c) if c.exists() else 0.0
        spans.append((t, t + d))
        t += d
        durs.append(d)
    return spans, durs


def deterministic_scan(video, total, tmp):
    """Black/white/empty frame sweep every FRAME_STEP seconds."""
    faults = []
    t = 0.5
    while t < total - 0.5:
        f = tmp / f"det_{int(t*10):06d}.jpg"
        if _grab(video, t, f, h=270):
            luma, frac = _frame_stats(f)
            if luma <= BLANK_LUMA_LO or luma >= BLANK_LUMA_HI:
                faults.append({"t": round(t, 1), "type": "blank_frame",
                               "severity": "high",
                               "detail": f"mean luma {luma:.0f}"})
            elif frac < MIN_CONTENT_FRAC:
                faults.append({"t": round(t, 1), "type": "empty_frame",
                               "severity": "medium",
                               "detail": f"content fraction {frac:.03f}"})
        t += FRAME_STEP
    # collapse runs of consecutive hits into one fault span
    merged = []
    for f in faults:
        if merged and f["type"] == merged[-1]["type"] and \
                f["t"] - merged[-1]["t_end"] <= FRAME_STEP + 0.1:
            merged[-1]["t_end"] = f["t"]
        else:
            merged.append({**f, "t_end": f["t"]})
    return merged


# sample positions within a scene (fractions of its span). 0.95 exists
# because end-of-scene is where dropped payoff panels and loop-desync live —
# the old 25/50/75 sampling never saw the last 15% of any scene.
FRAME_FRACS = [0.20, 0.45, 0.70, 0.95]

# narration quotes only count as visual payoffs when anchored by a speech
# verb ("he screams: 'X'") — bare quotes are often stylistic, not promises.
_SPEECH_VERBS = (r"(?:says?|said|screams?|screamed|shouts?|shouted|yells?|"
                 r"yelled|asks?|asked|whispers?|whispered|cries|cried|"
                 r"replies|replied|answers?|answered|speaks?|spoke|"
                 r"echo(?:es|ed)?|mutters?|muttered|calls?|called)")
# quote must OPEN after a non-letter (so contractions like they're / Let's /
# doesn't never start or end a match) and CLOSE before a non-letter; internal
# apostrophes are allowed when letter-flanked.
_QUOTE_RE = re.compile(
    _SPEECH_VERBS + r".{0,40}?(?<![a-zA-Z])['\"]"
    r"((?:[^'\"]|(?<=[a-zA-Z])'(?=[a-zA-Z])){3,120}?)"
    r"['\"](?![a-zA-Z])", re.I)


def narration_quotes(narration):
    """Speech-verb-anchored quoted lines in a narration — each one promises
    that the panel carrying it appears on screen."""
    return [q.strip() for q in _QUOTE_RE.findall(narration or "")]


STATIC_SPAN_SEC = 12.0    # near-identical visuals longer than this = fault


def static_scan(video, total, tmp):
    """Flag spans where the picture stays near-identical >STATIC_SPAN_SEC
    while the video runs on (the 'narration moves, screen doesn't' fault a
    viewer feels immediately)."""
    faults, t = [], 0.5
    prev_hash, span_start = None, 0.5
    while t < total - 0.5:
        f = tmp / f"st_{int(t*10):06d}.jpg"
        if _grab(video, t, f, h=180):
            h = _phash(f)
            if prev_hash is not None and _ham(h, prev_hash) <= 6:
                if t - span_start >= STATIC_SPAN_SEC:
                    faults.append({"t": round(span_start, 1),
                                   "t_end": round(t, 1),
                                   "type": "static_scene",
                                   "severity": "medium",
                                   "detail": f"visuals unchanged for "
                                             f"{t - span_start:.0f}s"})
                    span_start = t   # don't re-report the same span
            else:
                span_start = t
            prev_hash = h
        t += FRAME_STEP
    return faults


def script_scans(data, workdir, total):
    """Deterministic script/timeline checks that need no pixels:
    panel over-reuse, abrupt ending, promised-but-absent SFX."""
    faults = []
    scenes = data.get("scenes", [])
    # -- panel reuse across scenes --
    seen = {}
    for i, sc in enumerate(scenes):
        for p in (sc.get("panels") or []):
            seen.setdefault(Path(p).name, []).append(i)
    for name, ss in sorted(seen.items()):
        if len(ss) >= 3:
            faults.append({"scene": ss[-1], "t": 0.0, "type": "panel_reuse",
                           "severity": "low",
                           "detail": f"{name} appears in {len(ss)} scenes "
                                     f"({ss}) — repetition reads as lazy"})
    # -- abrupt ending: video ends too soon after the last narrated word --
    wt_last = Path(workdir) / f"w{len(scenes)-1:03}.json"
    try:
        wt = json.loads(wt_last.read_text())
        if wt:
            spans, durs = scene_spans(workdir, len(scenes))
            last_word_abs = spans[-1][0] + max(w["end"] for w in wt)
            tail = total - last_word_abs
            if tail < 1.5:
                faults.append({"scene": len(scenes) - 1,
                               "t": round(last_word_abs, 1),
                               "type": "abrupt_end", "severity": "medium",
                               "detail": f"video ends {tail:.1f}s after the "
                                         f"final word (needs >=1.5s hold)"})
    except Exception:
        pass
    # -- SFX promised by the script but none rendered --
    tagged = sum(len(sc.get("sfx_events") or []) for sc in scenes)
    amb = sum(1 for sc in scenes if sc.get("ambience"))
    sfxed = (Path(workdir) / "sfxed.mp4").exists()
    ambed = (Path(workdir) / "ambed.mp4").exists()
    if tagged and not sfxed:
        faults.append({"t": 0.0, "type": "sfx_absent", "severity": "low",
                       "detail": f"script tags {tagged} sfx_events but no SFX "
                                 f"layer was rendered (missing Freesound key "
                                 f"or no CC0 matches)"})
    if amb and not ambed:
        faults.append({"t": 0.0, "type": "ambience_absent", "severity": "low",
                       "detail": f"{amb} scenes request ambience but no "
                                 f"ambience layer was rendered"})
    return faults


def human_checklist(data, durs, workdir):
    """The non-automatable spot checks, with computed timestamps, appended to
    review.md so a targeted human pass takes minutes not a full watch."""
    scenes = data.get("scenes", [])
    hook_n = int(data.get("hook_scenes", 1))
    lines = ["", "## Human spot-checks (timestamps computed for this cut)", ""]

    def ts(sec):
        return f"{int(sec // 60)}:{int(sec % 60):02d}"

    hook_end = sum(durs[:hook_n])
    lines.append(f"- [ ] HOOK 0:00-{ts(hook_end)}: does the panel cycle feel "
                 f"dynamic (not a cheap 3-panel loop)? Would it stop a "
                 f"scroller?")
    plan = data.get("music_plan") or []
    for a, b in zip(plan, plan[1:]):
        seam = sum(durs[:b.get("from_scene", 0)])
        lines.append(f"- [ ] MUSIC SEAM at {ts(seam)} "
                     f"({a.get('mood')} -> {b.get('mood')}, "
                     f"{a.get('track')} -> {b.get('track')}): hard cut or "
                     f"key clash?")
    # recurring proper nouns (TTS pronunciation)
    import collections
    _common = {"This", "That", "Then", "There", "They", "When", "Where",
               "What", "With", "From", "Above", "Below", "After", "Before",
               "Someone", "Something", "Chapter", "Because", "Instead"}
    words = collections.Counter(
        w for sc in scenes for w in re.findall(
            r"\b[A-Z][a-z]{3,}\b", sc.get("narration", ""))
        if w not in _common)
    names = [w for w, c in words.most_common(6) if c >= 5]
    if names:
        lines.append(f"- [ ] TTS: pronunciation of recurring names "
                     f"({', '.join(names)}) and whether quoted dialogue is "
                     f"distinguishable from essay narration")
    thumbs = [t.get("panel") for t in data.get("thumbnails", []) if
              t.get("panel")]
    hook_panels = scenes[0].get("panels", []) if scenes else []
    lines.append(f"- [ ] SPOILER: hook panels {hook_panels} / thumbnails "
                 f"{thumbs} — do any pre-burn the chapter's climax image?")
    lines.append("- [ ] BRANDING (final cut only): title card ~0:08 and "
                 "subscribe pop-up placement/size/taste")
    lines.append("- [ ] LEGAL: panel density vs commentary still within the "
                 "channel's fair-use framing; check source watermarks")
    return lines


def review_scene(video, idx, scene, span, tmp, n_frames=4):
    """One vision-LLM call reviewing sampled frames of one scene."""
    s, e = span
    if e - s < 0.5:
        return []
    fracs = FRAME_FRACS if n_frames == len(FRAME_FRACS) else \
        [(k + 1) / (n_frames + 1) for k in range(n_frames)]
    frames = []
    for k, fr in enumerate(fracs):
        f = tmp / f"sc{idx:03d}_{k}.jpg"
        if _grab(video, s + (e - s) * fr, f):
            frames.append((s + (e - s) * fr, f))
    if not frames:
        return []
    hook = HOOK_NOTE if idx == 0 else ""
    quotes = narration_quotes(scene.get("narration", ""))[:4]
    if quotes:
        pnote = PAYOFF_NOTE.format(
            quotes="\n".join(f'- "{q}"' for q in quotes))
        pfield = ', "payoff_missing": [...]'
    else:
        pnote, pfield = "", ""
    instr = REVIEW_INSTRUCTION.format(
        n=len(frames), narration=scene.get("narration", "")[:500],
        hook_note=hook, payoff_note=pnote, payoff_field=pfield)
    try:
        raw = gateway.llm_vision([f for _, f in frames], instr)
        obj = _parse_json(raw, "object")
        if isinstance(obj, list):    # tolerate old-style bare-array replies
            obj = {"frames": obj}
        arr = obj.get("frames") or []
    except Exception as ex:
        return [{"scene": idx, "t": round(s, 1), "type": "review_error",
                 "severity": "low", "detail": f"{type(ex).__name__}: {ex}"}]
    faults = []
    for k, item in enumerate(arr[:len(frames)]):
        if not isinstance(item, dict):
            continue
        t = frames[k][0]
        base = {"scene": idx, "t": round(t, 1)}
        if item.get("hook_weak"):
            faults.append({**base, "type": "weak_hook", "severity": "high",
                           "detail": item.get("note", "hook visual won't stop a skip")})
        if not item.get("relevant", True):
            faults.append({**base, "type": "irrelevant_panel",
                           "severity": "high",
                           "detail": item.get("note", "frame does not depict narration")})
        if item.get("cropped"):
            faults.append({**base, "type": "cropped_content",
                           "severity": "medium",
                           "detail": item.get("note", "content cut at frame edge")})
        if item.get("empty"):
            faults.append({**base, "type": "empty_screen",
                           "severity": "medium",
                           "detail": item.get("note", "mostly empty frame")})
        if item.get("text_issue"):
            faults.append({**base, "type": "unreadable_text",
                           "severity": "low",
                           "detail": item.get("note", "on-screen text unreadable/truncated")})
        if item.get("watermark"):
            faults.append({**base, "type": "watermark", "severity": "high",
                           "detail": item.get("note",
                                              "scanlation watermark visible")})
        if item.get("small_text"):
            faults.append({**base, "type": "phone_readability",
                           "severity": "medium",
                           "detail": item.get("note",
                                              "story text too small for phone")})
    for q in (obj.get("payoff_missing") or []):
        faults.append({"scene": idx, "t": round(s, 1),
                       "type": "missing_payoff", "severity": "high",
                       "detail": f'quoted line never shown: "{str(q)[:90]}"'})
    return faults


NARRATION_FIX_PROMPT = """A manga-recap scene's narration was written for a panel list that has since changed: some panels were REMOVED from the visuals. Rewrite the narration minimally so it no longer describes or quotes content that only existed on the removed panels. Keep everything else word-for-word identical — same voice, same length feel, same story beats that remain visible.

REMOVED PANEL CONTENT (no longer on screen):
{removed}

CURRENT NARRATION:
{narration}

Output ONLY the rewritten narration text, no quotes around it, no commentary."""


def _fix_narration(scene, dropped, reads_by_panel):
    """When panels are dropped from a scene, touch up its narration so it
    stops describing/quoting content that is no longer on screen (the
    narration otherwise promises visuals the video can't show). Best-effort:
    on any LLM failure the narration is left unchanged."""
    removed_desc = []
    for p in dropped:
        r = reads_by_panel.get(Path(p).name, {})
        bits = []
        for d in (r.get("dialogue") or []):
            if d.get("text"):
                bits.append(f'dialogue "{d["text"]}"')
        if r.get("narration_text"):
            bits.append(f'text "{r["narration_text"]}"')
        if r.get("sfx"):
            bits.append(f'sfx "{r["sfx"]}"')
        if r.get("scene_beat"):
            bits.append(r["scene_beat"])
        removed_desc.append(f"- {Path(p).name}: " + ("; ".join(bits) or
                                                     "(no OCR data)"))
    try:
        new = gateway.llm_text(NARRATION_FIX_PROMPT.format(
            removed="\n".join(removed_desc),
            narration=scene.get("narration", ""))).strip()
        if new and len(new) > 40:
            scene["narration"] = new
            return True
    except Exception as ex:
        print(f"    narration touch-up failed ({type(ex).__name__}); "
              f"leaving narration as-is")
    return False


def apply_fixes(script_path, data, faults, reads_by_panel):
    """Safe auto-fixes on the script JSON: scenes whose panels drew
    irrelevant/empty faults get those panels swapped for the scene's other
    panels, or for the nearest story-quality panel. When panels are dropped,
    the scene narration is LLM-touched-up so it stops referencing content
    that is no longer on screen. Returns list of changes."""
    changes = []
    by_scene = {}
    for f in faults:
        if f.get("scene") is not None and f["type"] in (
                "irrelevant_panel", "empty_screen", "weak_hook"):
            by_scene.setdefault(f["scene"], []).append(f)
    for si, fs in by_scene.items():
        if si >= len(data["scenes"]):
            continue
        scene = data["scenes"][si]
        panels = scene.get("panels") or []
        bad_kinds = {"cover", "credits", "endmatter", "textonly"}
        keep, dropped = [], []
        for p in panels:
            r = reads_by_panel.get(Path(p).name, {})
            if r.get("kind") in bad_kinds or r.get("quality") == "sparse":
                dropped.append(p)
                changes.append(f"scene {si}: dropped {p} "
                               f"(kind={r.get('kind')}, q={r.get('quality')})")
            else:
                keep.append(p)
        if keep and keep != panels:
            scene["panels"] = keep
            if dropped and _fix_narration(scene, dropped, reads_by_panel):
                changes.append(f"scene {si}: narration touched up for "
                               f"{len(dropped)} dropped panel(s)")
        elif not keep and panels:
            # nothing usable in-scene: borrow the nearest story panel
            all_story = [n for n, r in sorted(reads_by_panel.items())
                         if r.get("kind", "story") == "story"
                         and r.get("quality", "ok") == "ok"]
            if all_story:
                anchor = Path(panels[0]).name
                nearest = min(all_story,
                              key=lambda n: abs(int(re.sub(r"\D", "", n) or 0)
                                                - int(re.sub(r"\D", "", anchor) or 0)))
                scene["panels"] = [f"panels/{nearest}"]
                changes.append(f"scene {si}: replaced all panels with "
                               f"panels/{nearest} (nearest healthy story art)")
    if changes:
        Path(script_path).write_text(json.dumps(data, ensure_ascii=False,
                                                indent=2))
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="output/<slug>/<slug>.json")
    ap.add_argument("--video", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--jobs", type=int, default=4,
                    help="parallel vision-LLM reviewers (default 4)")
    ap.add_argument("--frames-per-scene", type=int, default=4)
    ap.add_argument("--apply-fixes", action="store_true",
                    help="rewrite the script JSON dropping/replacing panels "
                         "that drew faults (re-render afterwards)")
    ap.add_argument("--out", default=None, help="review report path base")
    ap.add_argument("--det-only", action="store_true",
                    help="deterministic checks only (blank/empty frame sweep, "
                         "no vision-LLM calls) — cheap post-encode guard")
    args = ap.parse_args()

    spath = Path(args.script)
    data = json.loads(spath.read_text())
    proj = spath.parent
    video = Path(args.video) if args.video else proj / (spath.stem + ".mp4")
    workdir = Path(args.workdir) if args.workdir else proj / "_work"
    if not video.exists():
        sys.exit(f"video not found: {video}")

    scenes = data["scenes"]
    spans, durs = scene_spans(workdir, len(scenes))
    total = _ffprobe_dur(video)
    print(f"Reviewing {video.name}: {total:.0f}s, {len(scenes)} scenes "
          f"({args.jobs} parallel reviewers)")

    tmp = Path(tempfile.mkdtemp(prefix="review_"))
    faults = deterministic_scan(video, total, tmp)
    print(f"  deterministic scan: {len(faults)} fault span(s)")

    if not args.det_only:
        st = static_scan(video, total, tmp)
        print(f"  static-visual scan: {len(st)} span(s)")
        faults.extend(st)
        sf = script_scans(data, workdir, total)
        print(f"  script/timeline scan: {len(sf)} fault(s)")
        faults.extend(sf)
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(review_scene, video, i, sc, spans[i], tmp,
                              args.frames_per_scene)
                    for i, sc in enumerate(scenes)]
            for fu in futs:
                faults.extend(fu.result())

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    faults.sort(key=lambda f: (sev_rank.get(f.get("severity"), 3),
                               f.get("t", 0)))
    high = [f for f in faults if f.get("severity") == "high"]
    med = [f for f in faults if f.get("severity") == "medium"]
    print(f"  vision review: {len(faults)} total fault(s) "
          f"({len(high)} high, {len(med)} medium)")

    out_base = Path(args.out) if args.out else proj / "review"
    Path(f"{out_base}.json").write_text(json.dumps(faults, indent=2))
    lines = [f"# Video review — {video.name}",
             f"{len(faults)} fault(s): {len(high)} high, {len(med)} medium\n"]
    for f in faults:
        t = f.get("t", 0)
        mm, ss = int(t // 60), int(t % 60)
        sc = f" scene {f['scene']}" if f.get("scene") is not None else ""
        lines.append(f"- [{f['severity'].upper()}] {mm}:{ss:02d}{sc} "
                     f"{f['type']}: {f.get('detail','')}")
    if not args.det_only:
        lines.extend(human_checklist(data, durs, workdir))
    Path(f"{out_base}.md").write_text("\n".join(lines) + "\n")
    print(f"  report: {out_base}.md")

    if args.apply_fixes and faults:
        ocr = proj / (spath.stem + ".ocr.json")
        reads_by_panel = {}
        if ocr.exists():
            for r in json.loads(ocr.read_text()):
                reads_by_panel[r["panel"]] = r
        changes = apply_fixes(spath, data, faults, reads_by_panel)
        if changes:
            print(f"  applied {len(changes)} fix(es):")
            for c in changes:
                print(f"    {c}")
            print("  -> re-render affected scenes "
                  "(panel_render.py --redo-scene N) and re-review.")
            # record the fixes in the report itself so the md is a complete
            # audit trail (what was flagged AND what was done about it)
            with open(f"{out_base}.md", "a") as fh:
                fh.write("\n## Fixes auto-applied this round\n\n")
                for c in changes:
                    fh.write(f"- {c}\n")
                fh.write("\nAffected scenes will be re-rendered and "
                         "re-reviewed to confirm the fixes hold.\n")

    sys.exit(2 if high else 0)


if __name__ == "__main__":
    main()
