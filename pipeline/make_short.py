#!/usr/bin/env python3
"""make_short.py — STAGE 6b: build a native 9:16 Short from the project assets.

Shonen-Flux-style vertical Short, rebuilt from source (NOT cropped out of the
long video):

  * picks the strongest contiguous run of scenes (emotional peak), ending on a
    scene boundary — never a hard cut mid-sentence (45–80s window)
  * sub-segments each manga page into individual panels (XY-cut on white
    gutters) and fills the vertical frame with them — big, readable art on a
    clean dark backdrop (no blur-on-blur)
  * Ken Burns zoom per panel, cut rate driven by the scene's pace
  * channel header (logo band) + faint center watermark on every frame
  * word-timed kinetic captions: 2-word chunks, thick outline, keyword
    highlights, pop-in — sized/margined so they can never clip at the edges
  * per-scene narration audio + mood music bed, sidechain-ducked, mastered to
    -14 LUFS / -2 dBTP @ 48 kHz
  * "▶ FULL VIDEO" end card over the final seconds

Inputs it uses (all produced by earlier stages, sibling to <slug>.mp4):
  <slug>.json               scene script (narration, panels, pace, keywords)
  _work/a###_trunc.mp3      per-scene narration audio (silence-truncated)
  _work/w###.json           per-scene word timings (aligned to the trunc audio)
  panels/*.png              segmented pages
  music_<mood>.mp3          mood beds (optional)
  ../../brand/logo/*.png    channel lockup (optional)

Usage:
    ./venv/bin/python3 pipeline/make_short.py output/<slug>/<slug>.mp4
    ./venv/bin/python3 pipeline/make_short.py output/<slug>/<slug>.mp4 \
        --start-scene 3 --duration 60 --out output/<slug>/<slug>_short.mp4
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

W, H = 1080, 1920            # YouTube Shorts canvas
FPS = 30
PLATE_W, PLATE_H = 1350, 2400  # 1.25x plates so zoompan has room
TARGET_DUR = 60.0            # aim: complete micro-story, not a timer
MIN_DUR, MAX_DUR = 42.0, 80.0
SCENE_GAP = 0.30             # breathing room between scenes (s)
CARD_SECS = 3.0              # "full video" end card duration
HEADER_H = 210               # channel header band height (final px)
CAP_MARGIN_V = 430           # caption lift from bottom (final px)
PACE_CUT = {"hype": 1.9, "normal": 3.0, "quiet": 4.6}
MAX_CROPS_PER_SCENE = 6
BG_COLOR = (16, 15, 14)      # near-black backdrop
HEADER_COLOR = (24, 22, 20)

# ASS colors are &HAABBGGRR
YELLOW = r"\c&H003BEBFF&"    # #FFEB3B — hype keywords
RED = r"\c&H004040FF&"       # #FF4040 — shock words
WHITE = r"\c&H00FFFFFF&"
SHOCK_WORDS = {"kill", "kills", "killed", "killer", "die", "dies", "died",
               "dead", "death", "blood", "corpse", "monster", "murder",
               "murdered", "murderer"}


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"command failed:\n  {' '.join(map(str, cmd))}\n{r.stderr[-2000:]}")
    return r


def ffprobe_dur(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------
# scene selection — find the emotional peak window that ends on a boundary
# --------------------------------------------------------------------------
def _scene_score(s: dict) -> float:
    score = 2.0
    pace = s.get("pace", "normal")
    if pace == "hype":
        score += 1.5
    elif pace == "quiet":
        score -= 0.2
    if s.get("emphasis"):
        score += 1.0
    score += min(1.5, 0.5 * len(s.get("keywords") or []))
    narr = s.get("narration", "")
    score += min(1.0, 0.15 * (narr.count("!") + narr.count("?")))
    return score


def _is_meta_scene(s: dict, idx: int) -> bool:
    """Intro/outro scenes make weak Short material."""
    n = (s.get("narration") or "").lower()
    if idx == 0:
        return True                      # branded hook/intro
    bad = ("subscribe", "drop a like", "catch you in the next",
           "without further ado", "next chapter and if")
    return any(b in n for b in bad)


def pick_window(scenes, durs, target, start_scene=None):
    """Best contiguous scene run: duration within [MIN_DUR, MAX_DUR], highest
    mean scene score (small bonus for opening on a hype beat)."""
    n = len(scenes)
    usable = [i for i in range(n) if not _is_meta_scene(scenes[i], i)]
    best, best_key = None, None
    starts = [start_scene] if start_scene is not None else usable
    for s in starts:
        if s not in usable:
            continue
        dur, run_ = 0.0, []
        for e in range(s, n):
            if e not in usable:
                break
            dur += durs[e] + (SCENE_GAP if run_ else 0.0)
            run_.append(e)
            if dur < MIN_DUR:
                continue
            if dur > MAX_DUR:
                break
            score = sum(_scene_score(scenes[i]) for i in run_) / len(run_)
            if scenes[s].get("pace") == "hype":
                score += 0.6
            score -= abs(dur - target) / 100.0
            key = (score, -s)
            if best_key is None or key > best_key:
                best, best_key = list(run_), key
    if best is None:  # nothing fits — take the best single scene pair
        s = max(usable, key=lambda i: _scene_score(scenes[i]))
        best = [s]
        if s + 1 in usable:
            best.append(s + 1)
    return best


def rank_windows(scenes, durs, target):
    """All viable start scenes ranked best-first (for the reviewer's
    re-pick loop: retry with the next-best window on HIGH faults)."""
    n = len(scenes)
    usable = [i for i in range(n) if not _is_meta_scene(scenes[i], i)]
    ranked = []
    for s in usable:
        dur, run_ = 0.0, []
        best_key = None
        for e in range(s, n):
            if e not in usable:
                break
            dur += durs[e] + (SCENE_GAP if run_ else 0.0)
            run_.append(e)
            if dur < MIN_DUR:
                continue
            if dur > MAX_DUR:
                break
            score = sum(_scene_score(scenes[i]) for i in run_) / len(run_)
            if scenes[s].get("pace") == "hype":
                score += 0.6
            score -= abs(dur - target) / 100.0
            if best_key is None or score > best_key:
                best_key = score
        if best_key is not None:
            ranked.append((best_key, s))
    ranked.sort(reverse=True)
    return [s for _, s in ranked]


# --------------------------------------------------------------------------
# panel sub-segmentation — recursive XY-cut on white gutters
# --------------------------------------------------------------------------
def _gaps(profile, min_run):
    idx = profile > 0.92
    runs, s = [], None
    for i, v in enumerate(idx):
        if v and s is None:
            s = i
        if not v and s is not None:
            if i - s >= min_run:
                runs.append((s, i))
            s = None
    if s is not None and len(idx) - s >= min_run:
        runs.append((s, len(idx)))
    return [(a, b) for a, b in runs if a > min_run and b < len(profile) - min_run]


def xycut(img, rect=None, depth=0, out=None):
    if out is None:
        out = []
    H_, W_ = img.shape[:2]
    if rect is None:
        rect = (0, 0, W_, H_)
    x, y, w, h = rect
    if w < W_ * 0.18 or h < H_ * 0.10 or depth > 3:
        out.append(rect)
        return out
    g = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    rows = (g > 235).mean(axis=1)
    cols = (g > 235).mean(axis=0)
    hgaps, vgaps = _gaps(rows, 6), _gaps(cols, 6)
    if hgaps:
        cuts, axis = hgaps, "h"
    elif vgaps:
        cuts, axis = vgaps, "v"
    else:
        out.append(rect)
        return out
    limit = h if axis == "h" else w
    pts = [0] + [(a + b) // 2 for a, b in cuts] + [limit]
    spans = list(zip(pts[:-1], pts[1:]))
    if axis == "v":                       # manga reads right → left
        spans = spans[::-1]
    for a, b in spans:
        if b - a < 40:
            continue
        sub = (x, y + a, w, b - a) if axis == "h" else (x + a, y, b - a, h)
        xycut(img, sub, depth + 1, out)
    return out


def panel_crops(page_path: Path, workdir: Path, tag: str) -> list[Path]:
    """Cut one page into individual panel crops (reading order), filtered of
    slivers/bubble strips. Falls back to the whole page."""
    img = cv2.imread(str(page_path))
    if img is None:
        return []
    Hp, Wp = img.shape[:2]
    if min(Hp, Wp) < 300:                 # junk micro-panel
        return []
    boxes = xycut(img)
    good = [(x, y, w, h) for x, y, w, h in boxes
            if w >= 0.25 * Wp and h >= 0.12 * Hp and w * h >= 0.05 * Wp * Hp]
    if not good:
        good = [(0, 0, Wp, Hp)]
    paths = []
    for i, (x, y, w, h) in enumerate(good):
        pad = 6
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(Wp, x + w + pad), min(Hp, y + h + pad)
        p = workdir / f"crop_{tag}_{i:02d}.png"
        cv2.imwrite(str(p), img[y0:y1, x0:x1])
        paths.append(p)
    return paths


# --------------------------------------------------------------------------
# plates — pre-composed 1350x2400 frames (dark bg + panel), zoompanned later
# --------------------------------------------------------------------------
def build_plate(crop_path: Path, out_path: Path):
    """Panel on a clean dark canvas. Tall crops fill the art band; wide crops
    fit the width and sit centered. Art band leaves room for header+captions."""
    art_x0, art_y0 = 36, int(HEADER_H * 1.25) + 50       # plate coords
    art_x1, art_y1 = PLATE_W - 36, PLATE_H - int(CAP_MARGIN_V * 1.25) - 40
    bw, bh = art_x1 - art_x0, art_y1 - art_y0
    im = Image.open(crop_path).convert("RGB")
    w, h = im.size
    canvas = Image.new("RGB", (PLATE_W, PLATE_H), BG_COLOR)
    aspect = h / w
    band_aspect = bh / bw
    if aspect >= band_aspect:            # tall: fill band height, crop width
        s = bh / h
        nw = int(w * s)
        im = im.resize((nw, bh), Image.LANCZOS)
        if nw > bw:
            off = (nw - bw) // 2
            im = im.crop((off, 0, off + bw, bh))
        canvas.paste(im, (art_x0 + (bw - im.width) // 2, art_y0))
    else:                                # wide: fill band width
        s = bw / w
        nh = int(h * s)
        im = im.resize((bw, nh), Image.LANCZOS)
        canvas.paste(im, (art_x0, art_y0 + (bh - nh) // 2))
    canvas.save(out_path)


def _load_logo(path: Path) -> Image.Image:
    """Lockup cropped to its actual content (the canvas is mostly empty),
    dark strokes lifted to near-white so it reads on the dark header."""
    lg = Image.open(path).convert("RGBA")
    bbox = lg.getchannel("A").getbbox()
    if bbox:
        lg = lg.crop(bbox)
    px = np.array(lg)
    lum = (0.299 * px[..., 0] + 0.587 * px[..., 1] + 0.114 * px[..., 2])
    dark = (px[..., 3] > 0) & (lum < 100)   # black strokes -> off-white
    px[dark, 0:3] = (245, 242, 235)
    return Image.fromarray(px)


def build_overlay(out_path: Path, logo: Path | None):
    """Static full-canvas RGBA overlay: header band + logo + faint watermark."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)
    dr.rectangle([0, 0, W, HEADER_H], fill=HEADER_COLOR + (255,))
    dr.rectangle([0, HEADER_H, W, HEADER_H + 4], fill=(255, 46, 46, 255))
    if logo and logo.exists():
        lg = _load_logo(logo)
        lh = HEADER_H - 76
        lw = int(lg.width * lh / lg.height)
        if lw > W - 120:
            lw = W - 120
            lh = int(lg.height * lw / lg.width)
        lg = lg.resize((lw, lh), Image.LANCZOS)
        ov.alpha_composite(lg, ((W - lw) // 2, (HEADER_H - lh) // 2))
        # faint center watermark
        wm_h = 64
        wm_w = int(lg.width * wm_h / lg.height)
        wm = lg.resize((wm_w, wm_h), Image.LANCZOS)
        alpha = wm.getchannel("A").point(lambda a: int(a * 0.20))
        wm.putalpha(alpha)
        ov.alpha_composite(wm, ((W - wm_w) // 2, HEADER_H + 40))
    ov.save(out_path)


# --------------------------------------------------------------------------
# kinetic captions — word-timed 2-word chunks that cannot clip
# --------------------------------------------------------------------------
def _ass_ts(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs//360000:d}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}"


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def build_ass(scene_words, keywords_per_scene, offsets, total, out_ass: Path):
    """scene_words: list of word-dict lists; offsets: scene start times."""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n"
        "WrapStyle: 1\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Pop,DejaVu Sans,92,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,1,0,0,0,100,100,1,0,1,8,3,2,60,60,{CAP_MARGIN_V},1\n"
        "Style: CardTop,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&HA0000000,1,0,0,0,100,100,0,0,3,10,0,5,70,70,0,1\n"
        "Style: CardSub,DejaVu Sans,52,&H004040FF,&H00FFFFFF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,6,2,5,70,70,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n")
    events = []
    for words, kws, off in zip(scene_words, keywords_per_scene, offsets):
        kwset = {_norm(k) for k in kws}
        chunks = [words[i:i + 2] for i in range(0, len(words), 2)]
        for ci, ch in enumerate(chunks):
            a = off + ch[0]["start"]
            z = off + ch[-1]["end"] + 0.06
            nxt = chunks[ci + 1] if ci + 1 < len(chunks) else None
            if nxt:
                z = min(z, off + nxt[0]["start"])
            if z - a < 0.12:
                z = a + 0.12
            parts = []
            for wd in ch:
                text = wd["word"].upper().replace("{", "(").replace("}", ")")
                bare = _norm(wd["word"])
                if bare in kwset:
                    parts.append(f"{{{YELLOW}}}{text}{{{WHITE}}}")
                elif bare in SHOCK_WORDS:
                    parts.append(f"{{{RED}}}{text}{{{WHITE}}}")
                else:
                    parts.append(text)
            txt = (r"{\fscx72\fscy72\t(0,110,\fscx100\fscy100)}"
                   + " ".join(parts))
            events.append(f"Dialogue: 0,{_ass_ts(a)},{_ass_ts(min(z, total))},"
                          f"Pop,,0,0,0,,{txt}")
    cf = total - CARD_SECS
    events.append(
        f"Dialogue: 1,{_ass_ts(cf)},{_ass_ts(total)},CardTop,,0,0,0,,"
        r"{\an5\pos(540,900)\fscx80\fscy80\t(0,150,\fscx100\fscy100)}"
        r"▶ FULL VIDEO\Nlink in description")
    events.append(
        f"Dialogue: 1,{_ass_ts(cf)},{_ass_ts(total)},CardSub,,0,0,0,,"
        r"{\an5\pos(540,1055)}WATCH THE FULL RECAP")
    out_ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


# --------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------
def build_short(project: Path, slug: str, out: Path, target: float,
                start_scene: int | None) -> Path:
    script = project / f"{slug}.json"
    workroot = project / "_work"
    if not script.exists():
        sys.exit(f"missing script json: {script}")
    data = json.loads(script.read_text())
    scenes = data.get("scenes") or []
    if not scenes:
        sys.exit("script json has no scenes")

    # per-scene narration audio + word timings (from the render stage)
    audio, words, durs = [], [], []
    for i in range(len(scenes)):
        a = workroot / f"a{i:03d}_trunc.mp3"
        wj = workroot / f"w{i:03d}.json"
        if not a.exists() or not wj.exists():
            sys.exit(f"missing render artifacts for scene {i} "
                     f"({a.name}/{wj.name}) — run the render step first")
        audio.append(a)
        words.append(json.loads(wj.read_text()))
        durs.append(ffprobe_dur(a))

    win = pick_window(scenes, durs, target, start_scene)
    total = sum(durs[i] for i in win) + SCENE_GAP * (len(win) - 1)
    print(f"  scene window: {win[0]}–{win[-1]}  ({total:.1f}s) — "
          + " / ".join((scenes[i].get("caption") or f"scene {i}") for i in win))

    wd = out.parent / "_short_work"
    wd.mkdir(parents=True, exist_ok=True)
    for old in wd.glob("*"):
        old.unlink()
    # window metadata for the Short reviewer (review_short.py) and for the
    # orchestrator's retry loop (pick a different window on HIGH faults)
    (wd / "window.json").write_text(json.dumps(
        {"scenes": [win[0], win[-1]], "duration": round(total, 1)}))

    # ---- visuals: per-scene panel crops -> plates -> zoompan segments ----
    segs, seg_list = [], wd / "segs.txt"
    global_idx = 0
    for wi, si in enumerate(win):
        sc = scenes[si]
        pages = sc.get("panels") or ([sc["panel"]] if sc.get("panel") else [])
        crops = []
        for pg in pages:
            pgp = project / pg
            clean = project / pg.replace("panels/", "panels_clean/")
            crops += panel_crops(clean if clean.exists() else pgp, wd,
                                 f"s{si:03d}_{Path(pg).stem}")
        if not crops:                      # salvage: any neighbouring page
            for pg in pages:
                pgp = project / pg
                if pgp.exists():
                    crops = [pgp]
                    break
        if not crops:
            sys.exit(f"scene {si}: no usable panel art")
        dur = durs[si] + (SCENE_GAP if wi < len(win) - 1 else 0.0)
        cut = PACE_CUT.get(sc.get("pace", "normal"), 3.0)
        ncuts = max(1, min(MAX_CROPS_PER_SCENE, round(dur / cut)))
        if len(crops) >= ncuts:
            take = [crops[int(j * len(crops) / ncuts)] for j in range(ncuts)]
        else:                              # few panels: cycle them, the
            take = [crops[j % len(crops)]  # alternating zoom direction keeps
                    for j in range(ncuts)]  # each revisit visually distinct
        per = dur / ncuts
        for j, cp in enumerate(take):
            plate = wd / f"plate_{global_idx:03d}.png"
            build_plate(cp, plate)
            seg = wd / f"seg_{global_idx:03d}.mp4"
            frames = max(2, round(per * FPS))
            if global_idx % 2 == 0:
                z = f"min(1.0+{0.10/frames}*on,1.10)"
            else:
                z = f"max(1.10-{0.10/frames}*on,1.0)"
            run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS),
                 "-t", f"{per:.3f}", "-i", str(plate),
                 "-vf",
                 f"zoompan=z='{z}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
                 f"d={frames}:s={W}x{H}:fps={FPS},format=yuv420p",
                 "-frames:v", str(frames),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 str(seg)])
            segs.append(seg)
            global_idx += 1
    seg_list.write_text("".join(f"file '{s}'\n" for s in segs))
    print(f"  visuals: {len(segs)} panel segments")

    # ---- narration: concat scene audio with breathing gaps ----
    sil = wd / "gap.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi", "-t", f"{SCENE_GAP}",
         "-i", "anullsrc=r=24000:cl=mono", "-c:a", "libmp3lame",
         "-b:a", "64k", str(sil)])
    alist = wd / "audio.txt"
    lines = []
    for wi, si in enumerate(win):
        lines.append(f"file '{audio[si]}'\n")
        if wi < len(win) - 1:
            lines.append(f"file '{sil}'\n")
    alist.write_text("".join(lines))
    narr = wd / "narration.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(alist),
         "-ar", "48000", "-ac", "2", str(narr)])

    # ---- captions ----
    offsets, t = [], 0.0
    for wi, si in enumerate(win):
        offsets.append(t)
        t += durs[si] + (SCENE_GAP if wi < len(win) - 1 else 0.0)
    ass = wd / "short.ass"
    ncap = build_ass([words[i] for i in win],
                     [scenes[i].get("keywords") or [] for i in win],
                     offsets, total, ass)
    print(f"  captions: {ncap} kinetic cues")

    # ---- overlay (header + logo + watermark) ----
    logo = None
    for cand in [project.parent.parent / "brand/logo/panelbreak-lockup-1904.png",
                 Path(__file__).parent.parent / "brand/logo/panelbreak-lockup-1904.png"]:
        if cand.exists():
            logo = cand
            break
    ovl = wd / "overlay.png"
    build_overlay(ovl, logo)

    # ---- music bed for the window ----
    plan = data.get("music_plan") or []
    mood = None
    for entry in sorted(plan, key=lambda e: e.get("from_scene", 0)):
        if entry.get("from_scene", 0) <= win[0]:
            mood = entry.get("mood")
    music = None
    if mood:
        cand = project / f"music_{mood.lower()}.mp3"
        music = cand if cand.exists() else None
    if music is None:
        cand = project / "music.mp3"
        music = cand if cand.exists() else None

    # ---- final assembly: concat video + overlay + ass + mixed audio ----
    fade_st = max(0.0, total - 0.7)
    inputs = ["-f", "concat", "-safe", "0", "-i", str(seg_list),
              "-i", str(ovl), "-i", str(narr)]
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        agraph = (
            "[3:a]atrim=start=8,asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo,"
            "volume=0.38[mus];"
            "[mus][2:a]sidechaincompress=threshold=0.02:ratio=12:attack=15:"
            "release=420[duck];"
            "[2:a][duck]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={fade_st:.2f}:d=0.7,"
            "loudnorm=I=-14:TP=-2:LRA=11[aout]")
    else:
        agraph = (f"[2:a]afade=t=out:st={fade_st:.2f}:d=0.7,"
                  "loudnorm=I=-14:TP=-2:LRA=11[aout]")
    # NO fade-in: frame 0 is the Shorts feed preview — fading from black made
    # every short's preview a black frame (review_short bad_first_frame HIGH).
    # The short must punch in at full brightness on frame 0.
    vgraph = (
        "[0:v][1:v]overlay=0:0,"
        f"ass={ass},"
        f"fade=t=out:st={fade_st:.2f}:d=0.7[vout]")
    cmd = (["ffmpeg", "-y"] + inputs +
           ["-filter_complex", f"{vgraph};{agraph}",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(out)])
    print("  encoding vertical short …")
    run(cmd)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a 9:16 Short from project assets")
    ap.add_argument("mp4", help="rendered long-form <slug>.mp4 (used to locate the project)")
    ap.add_argument("--out", help="output short mp4 (default: <slug>_short.mp4)")
    ap.add_argument("--duration", type=float, default=TARGET_DUR,
                    help="target duration; actual length snaps to scene boundaries")
    ap.add_argument("--start-scene", type=int, default=None,
                    help="force the window to start at this scene index")
    ap.add_argument("--start", type=float, default=None,
                    help="(deprecated, ignored — scene-based selection is automatic)")
    ap.add_argument("--list-windows", action="store_true",
                    help="print viable start scenes ranked best-first (JSON "
                         "array) and exit — used by the review retry loop")
    args = ap.parse_args(argv)

    if args.start is not None:
        print("  ! --start is deprecated; using automatic scene selection")

    mp4 = Path(args.mp4).resolve()
    project = mp4.parent
    slug = mp4.stem
    out = Path(args.out) if args.out else project / f"{slug}_short.mp4"

    if args.list_windows:
        script = project / f"{slug}.json"
        data = json.loads(script.read_text())
        scenes = data.get("scenes") or []
        durs = []
        for i in range(len(scenes)):
            a = project / "_work" / f"a{i:03d}_trunc.mp3"
            durs.append(ffprobe_dur(a) if a.exists() else 0.0)
        target = max(MIN_DUR, min(MAX_DUR, args.duration))
        print(json.dumps(rank_windows(scenes, durs, target)))
        return 0

    build_short(project, slug, out,
                max(MIN_DUR, min(MAX_DUR, args.duration)), args.start_scene)
    dur = ffprobe_dur(out)
    print(f"  ✓ short -> {out}  ({dur:.1f}s, {W}x{H})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
