#!/usr/bin/env python3
"""title_overlay.py — series + chapter identity card overlaid on the video.

Renders a lower-third style banner (PIL, brand palette) with the series name
big in Anton and "CHAPTER N" in a small accent chip, then overlays it on the
video with a slide+fade in/out via one ffmpeg pass. Shown during the opening
seconds (after the hook has already grabbed the eye — never frame 0) so the
viewer knows exactly what they're watching, the way analysis channels keep a
chapter label on screen.

Public API (used by panel_render.py):
    build_card(path, series, chapter, scale)  -> (w, h) of the written PNG
    apply(video_in, video_out, card_png, x, y, t0, dur, run, inter_v)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# brand palette (matches subscribe_popup.py / render_brand_video.py)
INK = (19, 17, 16)
PAPER = (247, 242, 233)
VERM = (255, 59, 59)
CARD = (28, 26, 25)

ANTON = str(Path.home() / ".local/share/fonts/Anton-Regular.ttf")
DEJA_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SHOW_AT = 8.0      # card enters AFTER the hook window (the opening seconds
                   # must be uncovered story art — retention-critical)
SHOW_FOR = 4.0     # on-screen duration
FADE = 0.45


def _font(path, size):
    return ImageFont.truetype(path, max(int(size), 8))


def build_card(out_path, series, chapter, scale=1.0):
    """Write the title banner PNG. scale = video_height / 1080."""
    s = float(scale)
    title_f = _font(ANTON, 52 * s)
    chip_f = _font(DEJA_BOLD, 24 * s)

    title = series.upper()
    chip = f"CHAPTER {chapter}"

    m = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tb = m.textbbox((0, 0), title, font=title_f)
    cb = m.textbbox((0, 0), chip, font=chip_f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    cw, chh = cb[2] - cb[0], cb[3] - cb[1]

    pad_x, pad_y = int(34 * s), int(24 * s)
    bar_w = int(7 * s)
    chip_pad = int(12 * s)
    gap = int(14 * s)
    w = bar_w + pad_x + max(tw, cw + 2 * chip_pad) + pad_x
    h = pad_y + th + gap + chh + 2 * chip_pad + pad_y
    shadow_pad = int(14 * s)

    img = Image.new("RGBA", (w + 2 * shadow_pad, h + 2 * shadow_pad),
                    (0, 0, 0, 0))
    # soft shadow
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [shadow_pad + 5 * s, shadow_pad + 8 * s,
         shadow_pad + w + 5 * s, shadow_pad + h + 8 * s],
        radius=int(16 * s), fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img, sh.filter(
        ImageFilter.GaussianBlur(int(8 * s))))
    d = ImageDraw.Draw(img)
    x0, y0 = shadow_pad, shadow_pad
    # card
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=int(16 * s),
                        fill=CARD + (235,), outline=PAPER + (48,),
                        width=max(int(2 * s), 1))
    # vermilion spine
    d.rounded_rectangle([x0, y0 + int(12 * s), x0 + bar_w,
                         y0 + h - int(12 * s)],
                        radius=int(3 * s), fill=VERM + (255,))
    # series title
    tx = x0 + bar_w + pad_x
    ty = y0 + pad_y - tb[1]
    d.text((tx, ty), title, font=title_f, fill=PAPER + (255,))
    # chapter chip
    cy0 = y0 + pad_y + th + gap
    d.rounded_rectangle(
        [tx, cy0, tx + cw + 2 * chip_pad, cy0 + chh + 2 * chip_pad],
        radius=(chh + 2 * chip_pad) // 2, fill=VERM + (255,))
    d.text((tx + chip_pad, cy0 + chip_pad - cb[1]), chip, font=chip_f,
           fill=PAPER + (255,))

    img.save(out_path)
    return img.size


def apply(video_in, video_out, card_png, x, y, run, inter_v,
          t0=SHOW_AT, dur=SHOW_FOR):
    """Overlay the card with a slide-in from the left + alpha fade in/out."""
    t1 = t0 + dur
    slide = 40  # px slide distance
    fc = (
        f"[1:v]format=rgba,"
        f"fade=t=in:st={t0:.2f}:d={FADE:.2f}:alpha=1,"
        f"fade=t=out:st={t1 - FADE:.2f}:d={FADE:.2f}:alpha=1[card];"
        f"[0:v][card]overlay="
        f"x='{x}-{slide}*max(0,1-(t-{t0:.2f})/{FADE:.2f})':y={y}:"
        f"enable='between(t,{t0:.2f},{t1:.2f})'[v]"
    )
    run(["ffmpeg", "-y", "-i", str(video_in), "-loop", "1", "-i",
         str(card_png), "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
         "-c:a", "copy", "-shortest", *inter_v, str(video_out)])
