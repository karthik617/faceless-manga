#!/usr/bin/env python3
"""review_thumbs.py — STAGE 6d: human-like automated QC of the thumbnails.

Mirrors review_video.py for still thumbnails. Criteria distilled from
YouTube's thumbnail docs + vidIQ's 500-breakout study + Backlinko:

Deterministic tier (no LLM):
  * >=1280x720, 16:9, <=2MB, JPG/PNG
  * sharpness floor (catches blur, upscale mush, and the raw-B/W-panel
    fallback when hero generation failed)
  * dominant palette not red/white/black-heavy (blends into YouTube chrome)
  * contrast/brightness floors
  * writes thumbs/<name>_feedsize.png (120x68) — the mobile-feed render the
    vision pass and the human judge against

Vision-LLM tier (per thumb: full-size + the 120x68 render + title context):
  * mush_at_feed_size — subject/text/emotion unreadable at 120x68
  * no_focal_point — no single dominant subject / clutter
  * identity_or_artifact — AI-hero mangling (wrong character, garbled
    anatomy, leftover speech bubbles/SFX) or obvious raw manga-panel look
  * weak_emotion — face too small (<~25% of frame) or expression flat
  * text issues — >5 words, redundant with the title, colliding with the
    face, or sitting in the bottom-right timestamp corner
  * spoiler / policy_risk — advisory

Exit code 2 when HIGH faults remain, 0 otherwise (manga.py may then
regenerate the offending thumb).

Usage:
    python3 review_thumbs.py output/<slug>/<slug>.json [--only thumb1]
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import gateway
from review_common import parse_json, sharpness, write_report

MIN_W, MIN_H = 1280, 720
MAX_BYTES = 2 * 1024 * 1024
SHARP_MIN = 2.0          # Laplacian-mean floor (raw manga panels score low
                         # after the soft-blur bg treatment; heroes score high)
FEED_W, FEED_H = 120, 68

THUMB_INSTRUCTION = """You are a strict YouTube THUMBNAIL reviewer for a manga-recap channel. Image 1 is the full-size 1280x720 thumbnail. Image 2 is the SAME thumbnail at 120x68 — exactly how it appears in a phone feed.

VIDEO TITLE: "{title}"
INTENDED HOOK TEXT ON THE THUMB: "{hook_text}"

Judge like a viewer deciding whether to tap, and output ONE JSON object:
- "mush_at_feed_size": true/false — at 120x68 (image 2), do the subject, text and emotion still read instantly? true = it turns to mush.
- "no_focal_point": true/false — is there no single dominant subject (cluttered, multi-panel grid, competing elements, tiny distant figures)?
- "identity_or_artifact": true/false — AI-generation artifacts (mangled anatomy/hands/eyes, wrong or inconsistent character vs manga art style), leftover speech bubbles/SFX/panel gutters, or it looks like a raw uncropped black-and-white manga page instead of a designed thumbnail.
- "weak_emotion": true/false — no expressive face, or the face is under roughly a quarter of the frame, or the expression is flat/unreadable.
- "word_count": integer — number of overlay text words you can see.
- "text_title_redundant": true/false — the overlay text merely repeats the video title instead of adding a complementary hook.
- "text_collision": true/false — text overlaps the face or focal subject.
- "corner_text": true/false — text or key content in the bottom-right corner (YouTube stamps the duration there).
- "spoiler": true/false — the image looks like the story's final climax/payoff moment rather than a tease.
- "policy_risk": true/false — gore/shock level that risks age-restriction or ad limits.
- "note": one short sentence naming the biggest problem, or "".

Output ONLY the JSON object, no prose, no markdown fence."""

HUMAN_CHECKS = [
    "",
    "## Human spot-checks",
    "",
    "- [ ] Squint test: at arm's length does the thumb read in <1 second?",
    "- [ ] Feed test: place the 120x68 render next to competitors' thumbs — "
    "does it stand out on BOTH white and dark backgrounds?",
    "- [ ] Does thumb + title form one curiosity gap (neither spoils the "
    "other)?",
    "- [ ] Brand: consistent with the channel's colors/typography so repeat "
    "viewers recognize it?",
]


def deterministic_checks(final_path, feed_path):
    faults = []
    name = final_path.stem
    try:
        im = Image.open(final_path)
    except Exception as ex:
        return [{"t": 0.0, "item": name, "type": "unreadable_file",
                 "severity": "high", "detail": f"{type(ex).__name__}: {ex}"}]
    w, h = im.size
    if w < MIN_W or h < MIN_H:
        faults.append({"t": 0.0, "item": name, "type": "low_resolution",
                       "severity": "high",
                       "detail": f"{w}x{h} below {MIN_W}x{MIN_H}"})
    if abs(w / h - 16 / 9) > 0.02:
        faults.append({"t": 0.0, "item": name, "type": "bad_aspect",
                       "severity": "high", "detail": f"{w}x{h} is not 16:9"})
    size = final_path.stat().st_size
    if size > MAX_BYTES:
        faults.append({"t": 0.0, "item": name, "type": "file_too_big",
                       "severity": "high",
                       "detail": f"{size/1e6:.1f}MB > 2MB upload limit"})
    if im.format not in ("JPEG", "PNG"):
        faults.append({"t": 0.0, "item": name, "type": "bad_format",
                       "severity": "high", "detail": f"format {im.format}"})

    sh = sharpness(final_path)
    if sh < SHARP_MIN:
        faults.append({"t": 0.0, "item": name, "type": "soft_image",
                       "severity": "medium",
                       "detail": f"sharpness {sh:.1f} below floor — blur/"
                                 f"upscale mush or raw-panel fallback"})

    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    # dominant-palette check: fraction of pixels that are near-red, near-white
    # or near-black (the YouTube chrome colors a thumb must not blend into)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    white = (lum > 225).mean()
    black = (lum < 30).mean()
    red = ((r > 150) & (g < 90) & (b < 90)).mean()
    if white + black + red > 0.85:
        faults.append({"t": 0.0, "item": name, "type": "ui_palette",
                       "severity": "low",
                       "detail": f"{(white+black+red)*100:.0f}% of pixels are "
                                 f"white/black/red — blends into YouTube "
                                 f"chrome"})
    if lum.std() < 28:
        faults.append({"t": 0.0, "item": name, "type": "low_contrast",
                       "severity": "low",
                       "detail": f"global contrast (std {lum.std():.0f}) is "
                                 f"flat"})

    # the mobile-feed render (also consumed by the vision pass + human)
    im.convert("RGB").resize((FEED_W, FEED_H), Image.LANCZOS).save(feed_path)
    return faults


def vision_check(final_path, feed_path, title, hook_text):
    name = final_path.stem
    instr = THUMB_INSTRUCTION.format(title=title, hook_text=hook_text)
    try:
        raw = gateway.llm_vision([final_path, feed_path], instr)
        obj = parse_json(raw, "object")
    except Exception as ex:
        return [{"t": 0.0, "item": name, "type": "review_error",
                 "severity": "low", "detail": f"{type(ex).__name__}: {ex}"}]
    faults = []
    base = {"t": 0.0, "item": name}
    note = obj.get("note", "")

    def add(cond, typ, sev, default):
        if cond:
            faults.append({**base, "type": typ, "severity": sev,
                           "detail": note or default})

    add(obj.get("mush_at_feed_size"), "mush_at_feed_size", "high",
        "unreadable at mobile feed size (120x68)")
    add(obj.get("no_focal_point"), "no_focal_point", "high",
        "no single dominant subject")
    add(obj.get("identity_or_artifact"), "identity_or_artifact", "high",
        "AI artifacts / raw-panel look")
    add(obj.get("weak_emotion"), "weak_emotion", "medium",
        "face too small or expression flat")
    try:
        wc = int(obj.get("word_count") or 0)
    except (TypeError, ValueError):
        wc = 0
    add(wc > 5, "too_many_words", "medium",
        f"{wc} overlay words (max 5 for feed readability)")
    add(obj.get("text_title_redundant"), "text_title_redundant", "medium",
        "overlay text repeats the title instead of complementing it")
    add(obj.get("text_collision"), "text_collision", "medium",
        "text overlaps the focal subject")
    add(obj.get("corner_text"), "corner_text", "medium",
        "content in the bottom-right duration-stamp corner")
    add(obj.get("spoiler"), "spoiler", "medium",
        "thumbnail spends the story's climax image")
    add(obj.get("policy_risk"), "policy_risk", "low",
        "gore/shock may trigger restriction")
    return faults


def video_title(data, proj):
    """Best available title: upload package H1 > series+chapter > slug."""
    up = proj / "upload_package.md"
    if up.exists():
        for line in up.read_text().splitlines():
            if line.startswith("# "):
                return line[2:].strip()
            if line.lower().startswith("**title"):
                return line.split(":", 1)[-1].strip(" *")
    src = data.get("source") or {}
    if src.get("series"):
        return f"{src['series']} Chapter {src.get('chapter', '?')}"
    return data.get("title", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="output/<slug>/<slug>.json")
    ap.add_argument("--only", default=None, help="review just this thumb name")
    ap.add_argument("--out", default=None, help="report path base")
    ap.add_argument("--det-only", action="store_true",
                    help="deterministic checks only (no vision-LLM)")
    args = ap.parse_args()

    spath = Path(args.script)
    data = json.loads(spath.read_text())
    proj = spath.parent
    tdir = proj / "thumbs"
    specs = {s.get("name", f"thumb{i+1}"): s
             for i, s in enumerate(data.get("thumbnails") or [])}
    title = video_title(data, proj)

    finals = sorted(tdir.glob("thumb*_final.jpg")) if tdir.exists() else []
    if args.only:
        finals = [f for f in finals if f.stem.replace("_final", "") == args.only]
    if not finals:
        sys.exit(f"no thumbnails found in {tdir}")

    print(f"Reviewing {len(finals)} thumbnail(s) against title: {title!r}")
    faults = []
    for f in finals:
        name = f.stem.replace("_final", "")
        feed = tdir / f"{name}_feedsize.png"
        det = deterministic_checks(f, feed)
        faults.extend(det)
        if not args.det_only:
            spec = specs.get(name, {})
            hook = " ".join(spec.get("text") or [])
            vf = vision_check(f, feed, title, hook)
            faults.extend(vf)
            print(f"  {name}: {len(det)} deterministic + {len(vf)} vision "
                  f"fault(s)")
        else:
            print(f"  {name}: {len(det)} deterministic fault(s)")

    out_base = Path(args.out) if args.out else proj / "review_thumbs"
    extra = HUMAN_CHECKS if not args.det_only else ()
    nh, nm = write_report(out_base, faults, "Thumbnail review", extra)
    print(f"  report: {out_base}.md ({nh} high, {nm} medium)")
    sys.exit(2 if nh else 0)


if __name__ == "__main__":
    main()
