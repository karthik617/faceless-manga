#!/usr/bin/env python3
"""make_thumbs.py — high-CTR thumbnail generator for manga recaps.

Reads the same script JSON as panel_render.py. Each entry in the top-level
"thumbnails" list becomes one 1280x720 thumbnail.

Style (modeled on top manga-recap channels):
  * ONE cinematic hero image per thumb: the chosen B/W panel is sent through
    gateway.image_edit (Gemini flash-image) to be COLORIZED and repainted as a
    borderless 16:9 keyframe — speech bubbles / SFX / panel gutters removed,
    tight crop on the focal character, dramatic lighting, embers, dark blurred
    background. Cached as thumbs/<name>_hero.png; if the AI edit fails we fall
    back to the raw panel. Each hero is identity-checked against the source
    panel via llm_vision (same hair/face/marks/outfit); on mismatch it is
    regenerated with a corrective character description (up to 2 retries).
  * ONE short event-based hook line anchored at the BOTTOM (~bottom fifth),
    white with a single RED highlight word, black stroke + soft drop shadow.

    "thumbnails": [
      {"name": "thumb1", "text": ["THEY COULDN'T", "STOP HIM"],
       "highlight": "STOP", "emotion": "shock", "panel": "panels/p0007.png"}
    ]

  Optional fields:
    "highlight" — which word to color red (default: last word).
    "emotion"   — picks a mood-matched display font (horror/fear -> Creepster,
                  menace/dark -> Pirata One, sad/somber -> Cinzel Decorative,
                  chaos/comedy -> Bangers; anything else -> Anton).
  Multi-entry "text" lists are joined into one line.

Usage:
    python3 make_thumbs.py output/<slug>/<slug>.json [--only thumb1] [--no-ai]
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from gateway import image_edit, llm_vision

W, H = 1280, 720

FONT_CANDIDATES = [
    Path.home() / ".local/share/fonts/Anton-Regular.ttf",
    Path("/usr/share/fonts/truetype/lato/Lato-Black.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

# mood-matched display fonts (like the reference channels: horror brush for
# collapses, elegant serif for somber beats, blackletter for dark/gothic,
# comic for chaos). Keyed by the spec's "emotion" value; Anton is the default.
_FONTS_DIR = Path(__file__).parent / "brand" / "fonts"
MOOD_FONTS = {
    "horror":  _FONTS_DIR / "Creepster-Regular.ttf",
    "fear":    _FONTS_DIR / "Creepster-Regular.ttf",
    "terror":  _FONTS_DIR / "Creepster-Regular.ttf",
    "dread":   _FONTS_DIR / "Creepster-Regular.ttf",
    "menace":  _FONTS_DIR / "PirataOne-Regular.ttf",
    "dark":    _FONTS_DIR / "PirataOne-Regular.ttf",
    "gothic":  _FONTS_DIR / "PirataOne-Regular.ttf",
    "sad":     _FONTS_DIR / "CinzelDecorative-Black.ttf",
    "somber":  _FONTS_DIR / "CinzelDecorative-Black.ttf",
    "grief":   _FONTS_DIR / "CinzelDecorative-Black.ttf",
    "emotional": _FONTS_DIR / "CinzelDecorative-Black.ttf",
    "chaos":   _FONTS_DIR / "Bangers-Regular.ttf",
    "comedy":  _FONTS_DIR / "Bangers-Regular.ttf",
    "manic":   _FONTS_DIR / "Bangers-Regular.ttf",
}

WHITE = (255, 255, 255)
RED = (230, 30, 30)

HERO_INSTRUCTION = (
    "Repaint this black-and-white manga panel as a FULL-COLOR cinematic anime "
    "keyframe for a YouTube thumbnail. Requirements: keep the characters' "
    "designs, poses and expressions faithful to the panel; make the main "
    "character LARGE in frame (tight crop, face and emotion clearly readable); "
    "completely REMOVE all speech bubbles, dialogue text, sound-effect "
    "lettering, panel borders and gutters — one single borderless full-bleed "
    "illustration; rich color grading with dramatic rim lighting, glowing "
    "embers or particles in the air, a dark moody blurred background and a "
    "subtle vignette. No text anywhere in the image. 16:9 landscape."
)

VERIFY_INSTRUCTION = (
    "Image 1 is a black-and-white manga panel. Image 2 is a colorized "
    "repaint of it. Check whether the MAIN character in image 2 is the SAME "
    "character as the most prominent character in image 1: compare hair "
    "style/color (accounting for plausible colorization of B/W), face shape "
    "and age, scars or marks, and clothing/outfit. Reply with EXACTLY one "
    "line:\n"
    "MATCH\n"
    "or\n"
    "MISMATCH: <one-sentence description of the panel character's hair, "
    "face, distinguishing marks and outfit, phrased as an instruction, e.g. "
    "'the man has slicked-back blond hair, a scar over his right eye, and "
    "wears a leopard-print shirt under a long dark coat'>"
)


def _hero_ok(hero_path):
    try:
        with Image.open(hero_path) as im:
            im.verify()
        return hero_path.stat().st_size > 10_000
    except Exception:  # noqa: BLE001
        return False


def _verify_identity(src_panel, hero_path):
    """Vision check: is the hero's main character the same as the panel's?
    Returns (True, None) on match, (False, corrective_description) on
    mismatch, and (True, None) if the check itself errors (best-effort)."""
    try:
        reply = llm_vision([src_panel, hero_path], VERIFY_INSTRUCTION,
                           max_tokens=300).strip()
    except Exception as e:  # noqa: BLE001 — verification is best-effort
        print(f"  ! identity check errored ({type(e).__name__}: {e}); "
              "keeping hero unverified")
        return True, None
    if reply.upper().startswith("MATCH"):
        return True, None
    desc = reply.split(":", 1)[1].strip() if ":" in reply else ""
    return False, desc


def _make_hero(src_panel, hero_path, identity_retries=2):
    """Colorize + cinematize the panel via the image gateway, then verify the
    main character matches the source panel; on mismatch regenerate with a
    corrective identity description (up to identity_retries times). Returns
    True on success (hero_path written), False on failure (caller falls back
    to the raw panel)."""
    instruction = HERO_INSTRUCTION
    for attempt in range(identity_retries + 1):
        try:
            image_edit(src_panel, instruction, hero_path, aspect="16:9")
        except Exception as e:  # noqa: BLE001 — any failure means fallback
            print(f"  ! hero generation failed ({type(e).__name__}: {e}); "
                  "falling back to raw panel")
            return False
        if not _hero_ok(hero_path):
            print("  ! hero output invalid; falling back to raw panel")
            return False
        ok, desc = _verify_identity(src_panel, hero_path)
        if ok:
            return True
        print(f"  ! hero character mismatch (attempt {attempt + 1}): {desc}")
        if attempt < identity_retries:
            instruction = (
                HERO_INSTRUCTION
                + " IMPORTANT character identity — reproduce EXACTLY this "
                  "person from the panel; do NOT invent a different face, "
                  "hair or outfit: "
                + (desc or "match the panel's main character precisely."))
    print("  ! identity retries exhausted; keeping last hero anyway")
    return True


def _font_path(emotion=None):
    if emotion:
        mood = MOOD_FONTS.get(emotion.strip().lower())
        if mood and mood.exists():
            return str(mood)
    for f in FONT_CANDIDATES:
        if f.exists():
            return str(f)
    sys.exit("no thumbnail font found (install Anton-Regular.ttf or DejaVu)")


def add_text(src, dst, words, highlight=None, emotion=None):
    """Bottom-anchored hook line: ALL-CAPS display font (mood-matched via
    "emotion", Anton default), white with ONE red word, black stroke + soft
    drop shadow, over a bottom darkening gradient. The art above stays
    unobstructed."""
    font_file = _font_path(emotion)
    im = Image.open(src).convert("RGB").resize((W, H), Image.LANCZOS)
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.06)

    # bottom darkening gradient (lower ~28%) for legibility
    grad = Image.new("L", (1, H), 0)
    for yy in range(H):
        frac = (yy - H * 0.72) / (H * 0.28)
        grad.putpixel((0, yy), max(0, min(190, int(190 * frac))))
    grad = grad.resize((W, H))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, grad)

    words = [w for w in words if w.strip()]
    if not words:
        im.save(dst, quality=95)
        return 0
    hl = (highlight or words[-1]).upper()
    line = " ".join(words)

    # auto-fit: single line within 94% width, capped so text stays a strip
    max_w = int(W * 0.94)
    size = 150
    d0 = ImageDraw.Draw(im)
    while size > 56:
        f = ImageFont.truetype(font_file, size)
        if d0.textlength(line, font=f) <= max_w:
            break
        size -= 4
    font = ImageFont.truetype(font_file, size)
    stroke = max(4, size // 18)
    space_w = d0.textlength(" ", font=font)
    line_w = d0.textlength(line, font=font)

    # render the line on its own layer for a soft shadow pass
    pad = stroke * 4
    layer = Image.new("RGBA", (int(line_w) + pad * 2, size + pad * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x = pad
    used_hl = False
    for w in words:
        color = RED if (w.upper() == hl and not used_hl) else WHITE
        if color == RED:
            used_hl = True
        ld.text((x, pad), w, font=font, fill=color + (255,),
                stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        x += d0.textlength(w, font=font) + space_w

    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    alpha = layer.split()[3].point(lambda a: min(a, 160))
    shadow.paste((0, 0, 0, 255), (0, 0), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(stroke * 1.5))

    px = int((W - line_w) / 2) - pad
    py = H - int(size * 1.55) - pad          # baseline strip near bottom edge
    im.paste(shadow, (px + stroke, py + stroke), shadow)
    im.paste(layer, (px, py), layer)
    im.save(dst, quality=95)
    return size


def make_thumbs(script_path, only=None, use_ai=True):
    script_path = Path(script_path)
    data = json.loads(script_path.read_text())
    specs = data.get("thumbnails") or []
    if not specs:
        print("script JSON has no \"thumbnails\" list — nothing to do")
        return []
    project_dir = script_path.parent
    out_dir = project_dir / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for spec in specs:
        name = spec.get("name", f"thumb{len(made)+1}")
        if only and name != only:
            continue
        raw = out_dir / f"{name}.jpg"
        hero = out_dir / f"{name}_hero.png"
        final = out_dir / f"{name}_final.jpg"
        if not raw.exists() or raw.stat().st_size == 0:
            panel_rel = spec.get("panel")
            src_panel = None
            if panel_rel:
                cand = project_dir / "panels_clean" / Path(panel_rel).name
                if not cand.exists():
                    cand = project_dir / panel_rel
                if not cand.exists():
                    cand = project_dir / "panels" / Path(panel_rel).name
                if cand.exists():
                    src_panel = cand
            if src_panel:
                Image.open(src_panel).convert("RGB").save(raw, quality=95)
            else:
                print(f"  ! {name}: no usable panel found "
                      f"({spec.get('panel')!r}); skipping")
                continue
        # cinematic colorized hero (cached); fall back to the raw panel
        bg = raw
        if use_ai:
            if hero.exists() and hero.stat().st_size > 10_000:
                bg = hero
            elif _make_hero(raw, hero):
                bg = hero
        words = " ".join(spec["text"]).upper().split()
        size = add_text(bg, final, words, spec.get("highlight"),
                        emotion=spec.get("emotion"))
        print(f"wrote {final.name} (bg {bg.name}, font {size}px)")
        made.append(final)
    return made


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script")
    ap.add_argument("--only")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the AI hero pass; overlay text on the raw panel")
    args = ap.parse_args()
    make_thumbs(args.script, args.only, use_ai=not args.no_ai)


if __name__ == "__main__":
    main()
