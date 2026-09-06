#!/usr/bin/env python3
"""subscribe_popup.py — animated channel pop-up overlay (replaces the old
branded intro/outro videos).

A YouTube-style card slides in from the bottom-left with the channel logo,
channel name, a LIKE button and a SUBSCRIBE button. An animated cursor clicks
LIKE (the button fills, particles burst, and the like counter starts ticking
up in real time) then clicks SUBSCRIBE (the button turns YouTube-red, the text
flips to SUBSCRIBED and a bell pops in with a ring burst). The card then
slides back out.

The card is rendered as a small RGBA PNG sequence with PIL (only the card-sized
canvas, not full video frames) and composited onto the finished video with a
single ffmpeg overlay pass, optionally with synthesized UI click sounds mixed
into the audio at the two click moments.

Public API (used by panel_render.py):
    channel_info()                          -> (channel_name, logo_path|None)
    plan_times(total_dur, hook_end)         -> [t0, ...] overlay start times
    build_frames(frames_dir, scale, name, logo) -> (n_frames, canvas_w, canvas_h)
    make_click_wav(workdir, run)            -> Path to a short click wav
    apply(video_in, video_out, frames_dir, times, x, y,
          run, inter_v, audio_br, click_wav=None)
"""
import json
import math
import random
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

FPS = 30
DUR = 6.5                 # total pop-up lifetime (seconds)
SLIDE_IN = 0.45           # entrance slide duration
LIKE_CLICK_T = 1.15       # cursor clicks LIKE at this offset
SUB_CLICK_T = 2.45        # cursor clicks SUBSCRIBE at this offset
SLIDE_OUT_T = 5.90        # exit begins here
LIKES_BASE = 1208         # starting like count shown on the button

# brand palette (matches brand/video/render_brand_video.py)
INK = (19, 17, 16)
PAPER = (247, 242, 233)
VERM = (255, 59, 59)
CYAN = (34, 199, 233)
MUTE = (167, 158, 144)
CARD = (28, 26, 25)
PILL = (54, 51, 48)

ANTON = str(Path.home() / ".local/share/fonts/Anton-Regular.ttf")
DEJA_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _ease_out(t):
    return 1 - (1 - t) ** 3


def _ease_in(t):
    return t ** 3


def _spring(t):
    """Overshoot/bounce easing for entrances (settles at 1.0)."""
    if t >= 1:
        return 1.0
    return 1 - math.exp(-6 * t) * math.cos(9 * t)


def _clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _font(path, size):
    return ImageFont.truetype(path, max(int(size), 8))


def channel_info():
    """(channel_name, logo_path|None) from channel_state.json + brand/logo/."""
    name = "PanelBreak"
    state = ROOT / "channel_state.json"
    if state.exists():
        try:
            name = json.loads(state.read_text()).get("channel", name)
        except Exception:
            pass
    # the "ink" (dark) icon reads on the paper-white disc the card draws
    logo = ROOT / "brand" / "logo" / "panelbreak-icon-ink-1024.png"
    return name, (logo if logo.exists() else None)


_CTA_RE = re.compile(r"\b(like button|subscribe|subscribing|smash that like)\b",
                     re.IGNORECASE)


def cta_times(scenes, durations, word_times=None):
    """Start times of narrated like/subscribe CTAs, so the pop-up appears
    exactly while the narrator asks for it. If word timings are available the
    time snaps to the first CTA keyword ('like'/'subscribe/smash') spoken in
    the scene; otherwise the scene start is used."""
    out, t = [], 0.0
    keys = ("like", "subscribe", "smash")
    for i, (scene, dur) in enumerate(zip(scenes, durations)):
        if _CTA_RE.search(scene.get("narration", "")):
            off = 0.0
            wt = word_times[i] if word_times and i < len(word_times) else None
            for w in (wt or []):
                lw = "".join(c for c in w["word"].lower() if c.isalnum())
                if lw in keys:
                    # lead the spoken word slightly so the card is on screen
                    off = max(float(w["start"]) - 0.8, 0.0)
                    break
            out.append(round(t + off, 3))
        t += dur
    return out


def plan_times(total_dur, hook_end, cta=None):
    """Overlay start times — ONE appearance per video (Shonen-Flux style: the
    story is never interrupted). It syncs to the narrated CTA when the script
    has one (the card is on screen while the narrator says "drop a like,
    subscribe"); with no CTA it plays near the end where the outro used to
    sit. `hook_end` is accepted for API stability but no longer used."""
    if cta:
        t = max(float(sorted(cta)[0]), 0.5)
        if t + DUR + 1.0 > total_dur:
            t = max(total_dur - DUR - 1.0, 0.5)
        return [round(t, 3)]
    t = total_dur - DUR - 1.5
    return [round(t, 3)] if t >= 4.0 else []


# ---------------------------------------------------------------- drawing --
def _circle_logo(logo_path, px):
    """Logo cropped to a circle on a paper disc (RGBA tile of px x px)."""
    tile = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.ellipse([0, 0, px - 1, px - 1], fill=PAPER + (255,))
    if logo_path:
        try:
            ic = Image.open(logo_path).convert("RGBA")
            # some exports leave the art in a corner of a mostly-transparent
            # canvas; crop to the alpha bbox so the logo fills the disc
            bbox = ic.split()[3].getbbox()
            if bbox:
                ic = ic.crop(bbox)
                side = max(ic.size)
                sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                sq.alpha_composite(ic, ((side - ic.width) // 2,
                                        (side - ic.height) // 2))
                ic = sq
            inner = int(px * 0.86)
            ic = ic.resize((inner, inner), Image.LANCZOS)
            # constrain the icon's own alpha to the disc's inner circle
            mask = Image.new("L", (inner, inner), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
            ic.putalpha(ImageChops.multiply(ic.split()[3], mask))
            off = (px - inner) // 2
            tile.alpha_composite(ic, (off, off))
        except Exception:
            pass
    return tile


# The YouTube/Material "thumb_up" glyph, traced from its 24x24 SVG path.
# One polygon for the hand+thumb, one rounded rect for the cuff.
_THUMB_HAND = [
    (7.00, 9.00), (7.59, 7.59), (14.17, 1.00), (14.75, 1.35),
    (15.23, 2.05), (15.67, 3.11), (15.64, 3.43), (14.69, 8.00),
    (21.00, 8.00), (22.40, 8.60), (23.00, 10.00), (23.00, 12.01),
    (22.86, 12.73), (19.84, 19.78), (19.20, 20.60), (18.00, 21.00),
    (9.00, 21.00), (7.60, 20.40), (7.00, 19.00),
]
_THUMB_CUFF = (1.0, 9.0, 5.0, 21.0)


_thumb_cache = {}


def _thumb_tile(s, color, angle=0.0):
    """Anti-aliased YouTube thumb_up as an RGBA tile (s x s box drawn 4x
    supersampled then downscaled). angle: counter-clockwise tilt degrees for
    the click 'pop'. Cached per (size, color, rounded angle)."""
    key = (int(s), color, round(angle, 1))
    if key in _thumb_cache:
        return _thumb_cache[key]
    ss = 4
    big = int(s) * ss
    pad = big // 3        # headroom so rotation doesn't clip
    tile = Image.new("RGBA", (big + 2 * pad, big + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    k = big / 24.0
    d.polygon([(pad + px * k, pad + py * k) for px, py in _THUMB_HAND],
              fill=color)
    cx0, cy0, cx1, cy1 = _THUMB_CUFF
    d.rounded_rectangle([pad + cx0 * k, pad + cy0 * k,
                         pad + cx1 * k, pad + cy1 * k],
                        radius=1.2 * k, fill=color)
    if angle:
        tile = tile.rotate(angle, resample=Image.BICUBIC,
                           center=(pad + 7 * k, pad + 21 * k))
    out = tile.resize((tile.width // ss, tile.height // ss), Image.LANCZOS)
    _thumb_cache[key] = (out, pad // ss)
    return _thumb_cache[key]


def _draw_thumb(img, x, y, s, color, angle=0.0):
    """Composite the anti-aliased thumb tile with its box corner at (x, y)."""
    tile, pad = _thumb_tile(s, color, angle)
    img.alpha_composite(tile, (int(x - pad), int(y - pad)))


def _draw_bell(d, cx, cy, s, color):
    """Simple notification bell centered at (cx, cy), s = height."""
    r = s * 0.45
    d.pieslice([cx - r, cy - s * 0.52, cx + r, cy + s * 0.28], 180, 360,
               fill=color)
    d.rectangle([cx - r, cy - s * 0.12, cx + r, cy + s * 0.22], fill=color)
    d.rounded_rectangle([cx - r * 1.25, cy + s * 0.18, cx + r * 1.25,
                         cy + s * 0.30], radius=s * 0.05, fill=color)
    d.ellipse([cx - s * 0.10, cy + s * 0.30, cx + s * 0.10, cy + s * 0.50],
              fill=color)


def _draw_cursor(d, x, y, s, alpha=255):
    """Standard pointer arrow with the tip at (x, y)."""
    pts = [(0, 0), (0, 16.5), (4.6, 12.6), (7.0, 18.2), (9.6, 17.1),
           (7.2, 11.6), (11.6, 11.6)]
    pts = [(x + px * s, y + py * s) for px, py in pts]
    d.polygon(pts, fill=(255, 255, 255, alpha), outline=(0, 0, 0, alpha),
              width=max(int(1.4 * s), 1))


def _lerp_path(keys, t):
    """Piecewise-linear (eased) interpolation over [(t, x, y), ...]."""
    if t <= keys[0][0]:
        return keys[0][1], keys[0][2]
    for (t0, x0, y0), (t1, x1, y1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            k = _ease_out(_clamp((t - t0) / max(t1 - t0, 1e-6)))
            return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
    return keys[-1][1], keys[-1][2]


def build_frames(frames_dir, scale, channel_name, logo_path, fps=FPS):
    """Render the pop-up as an RGBA PNG sequence into frames_dir.

    scale is video_height / 1080 so the card keeps the same on-screen size at
    every resolution. Returns (n_frames, canvas_w, canvas_h)."""
    frames_dir = Path(frames_dir)
    if frames_dir.exists():
        for p in frames_dir.glob("f*.png"):
            p.unlink()
    frames_dir.mkdir(parents=True, exist_ok=True)

    s = float(scale)
    n_frames = int(round(DUR * fps))
    rng = random.Random(7)

    pad = int(90 * s)                      # room for particles/cursor/slide
    card_w, card_h = int(680 * s), int(196 * s)
    cw, ch = card_w + 2 * pad, card_h + 2 * pad

    name_f = _font(ANTON, 46 * s)
    btn_f = _font(DEJA_BOLD, 26 * s)
    plus_f = _font(DEJA_BOLD, 22 * s)

    logo_px = int(122 * s)
    logo_tile = _circle_logo(logo_path, logo_px)

    # like counter per frame: starts ticking "live" at the click
    counts, c = [], LIKES_BASE
    for f in range(n_frames):
        if f / fps >= LIKE_CLICK_T and f % 3 == 0:
            c += rng.choice([1, 1, 1, 2, 2, 3])
        counts.append(c)

    # particle burst at the like click (deterministic)
    parts = []
    for _ in range(14):
        a = rng.uniform(0, 2 * math.pi)
        v = rng.uniform(90, 260) * s
        parts.append((a, v, rng.choice([CYAN, VERM, PAPER]),
                      rng.uniform(2.2, 4.6) * s))

    # static layout (card-local, before slide offset)
    x0 = pad
    lg_cx = x0 + int(38 * s) + logo_px // 2
    tx = x0 + int(38 * s) + logo_px + int(30 * s)
    y_btn_h = int(60 * s)
    # like pill sized for the largest count it can reach
    max_cnt = f"{counts[-1]:,}"
    _m = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    cnt_w = _m.textbbox((0, 0), max_cnt, font=btn_f)[2]
    like_w = int(34 * s) + int(12 * s) + cnt_w + int(44 * s)
    sub_txt_w = _m.textbbox((0, 0), "SUBSCRIBED", font=btn_f)[2]
    sub_w = sub_txt_w + int(50 * s) + int(34 * s)   # room for the bell

    for f in range(n_frames):
        t = f / fps
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # entrance: spring-bounce slide-in (professional lower-third feel);
        # exit: ease-in slide + fade
        k_in = _clamp(t / SLIDE_IN)
        slide = (1 - _spring(k_in)) * 110 * s
        fade = 1.0
        if t >= SLIDE_OUT_T:
            k = _clamp((t - SLIDE_OUT_T) / (DUR - SLIDE_OUT_T))
            slide += _ease_in(k) * 70 * s
            fade = 1.0 - k
        y0 = pad + slide

        # soft drop shadow under the card (grounds it on any art)
        sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [x0 + 6 * s, y0 + 10 * s, x0 + card_w + 6 * s,
             y0 + card_h + 10 * s],
            radius=int(30 * s), fill=(0, 0, 0, 130))
        img = Image.alpha_composite(
            img, sh.filter(ImageFilter.GaussianBlur(int(10 * s))))
        d = ImageDraw.Draw(img)

        # card with subtle vertical gradient (lighter top edge)
        d.rounded_rectangle([x0, y0, x0 + card_w, y0 + card_h],
                            radius=int(30 * s), fill=CARD + (242,),
                            outline=PAPER + (56,), width=max(int(2 * s), 1))
        d.rounded_rectangle([x0 + 2 * s, y0 + 2 * s, x0 + card_w - 2 * s,
                             y0 + int(card_h * 0.45)],
                            radius=int(26 * s), fill=(255, 255, 255, 10))
        # thin vermilion accent bar down the left edge (lower-third style)
        d.rounded_rectangle([x0, y0 + int(20 * s), x0 + int(7 * s),
                             y0 + card_h - int(20 * s)],
                            radius=int(3 * s), fill=VERM + (255,))

        # logo + channel name; avatar ring pulses softly until subscribed
        img.alpha_composite(logo_tile, (int(lg_cx - logo_px / 2),
                                        int(y0 + card_h / 2 - logo_px / 2)))
        pulse = 1.0 + 0.06 * math.sin(t * 4.4) if t < SUB_CLICK_T else 1.0
        rr_ = logo_px / 2 * pulse
        ring_col = VERM if t < SUB_CLICK_T else CYAN
        d.ellipse([lg_cx - rr_, y0 + card_h / 2 - rr_,
                   lg_cx + rr_, y0 + card_h / 2 + rr_],
                  outline=ring_col + (255,), width=max(int(3 * s), 1))
        d.text((tx, y0 + int(24 * s)), channel_name, font=name_f,
               fill=PAPER + (255,))

        y_btn = y0 + int(108 * s)
        liked = t >= LIKE_CLICK_T
        subbed = t >= SUB_CLICK_T

        # ---- LIKE button ----
        press = _clamp((t - LIKE_CLICK_T) / 0.16) if liked else 1.0
        shrink = (1 - abs(press * 2 - 1)) * 4 * s if liked and press < 1 else 0
        lb = [tx + shrink, y_btn + shrink,
              tx + like_w - shrink, y_btn + y_btn_h - shrink]
        if liked:
            d.rounded_rectangle(lb, radius=y_btn_h // 2, fill=CYAN + (255,))
            ic_col, txt_col = INK + (255,), INK + (255,)
        else:
            d.rounded_rectangle(lb, radius=y_btn_h // 2, fill=PILL + (255,),
                                outline=PAPER + (90,),
                                width=max(int(2 * s), 1))
            ic_col, txt_col = PAPER + (255,), PAPER + (255,)
        # thumb glyph with the YouTube click 'pop': on the like click it
        # tilts back ~20 deg and swells ~25%, then springs back to rest.
        th_s = int(30 * s)
        tilt, swell = 0.0, 1.0
        if liked:
            pk = _clamp((t - LIKE_CLICK_T) / 0.45)
            bump = math.sin(pk * math.pi)          # 0 -> 1 -> 0
            tilt = -22.0 * bump
            swell = 1.0 + 0.25 * bump
        th_draw = int(th_s * swell)
        _draw_thumb(img, tx + int(20 * s) - (th_draw - th_s) / 2,
                    y_btn + (y_btn_h - th_draw) / 2 - 2 * s,
                    th_draw, ic_col, angle=tilt)
        d = ImageDraw.Draw(img)
        cnt = f"{counts[f]:,}"
        d.text((tx + int(20 * s) + th_s + int(12 * s),
                y_btn + y_btn_h / 2 - btn_f.size / 2 - 2 * s),
               cnt, font=btn_f, fill=txt_col)

        # "+1" floats rising off the like count (real-time feel)
        if liked:
            for burst_t in [LIKE_CLICK_T + i * 0.8 for i in range(5)]:
                life = (t - burst_t) / 0.6
                if 0 <= life <= 1:
                    a = int(255 * (1 - life))
                    d.text((tx + like_w - int(4 * s),
                            y_btn - int(6 * s) - life * 42 * s),
                           "+1", font=plus_f, fill=CYAN + (a,))

        # like-click particle burst
        life = (t - LIKE_CLICK_T) / 0.55
        if 0 <= life <= 1:
            pcx, pcy = tx + like_w / 2, y_btn + y_btn_h / 2
            for a, v, col, r in parts:
                px = pcx + math.cos(a) * v * life
                py = (pcy + math.sin(a) * v * life
                      + 60 * s * life * life)          # slight gravity
                al = int(255 * (1 - life))
                d.ellipse([px - r, py - r, px + r, py + r], fill=col + (al,))

        # ---- SUBSCRIBE button ----
        sx = tx + like_w + int(22 * s)
        press = _clamp((t - SUB_CLICK_T) / 0.16) if subbed else 1.0
        shrink = (1 - abs(press * 2 - 1)) * 4 * s if subbed and press < 1 else 0
        sb = [sx + shrink, y_btn + shrink,
              sx + sub_w - shrink, y_btn + y_btn_h - shrink]
        if subbed:
            # icon/button turns RED once subscribed (YouTube style)
            d.rounded_rectangle(sb, radius=y_btn_h // 2, fill=VERM + (255,))
            label, lab_col = "SUBSCRIBED", PAPER + (255,)
            # bell pops in
            bk = _ease_out(_clamp((t - SUB_CLICK_T) / 0.35))
            bell_s = int(30 * s * bk)
            if bell_s > 2:
                _draw_bell(d, sx + int(28 * s), y_btn + y_btn_h / 2,
                           bell_s, PAPER + (255,))
            lx = sx + int(50 * s)
        else:
            d.rounded_rectangle(sb, radius=y_btn_h // 2, fill=PAPER + (255,))
            label, lab_col = "SUBSCRIBE", INK + (255,)
            lx = sx + int(30 * s)
        d.text((lx, y_btn + y_btn_h / 2 - btn_f.size / 2 - 2 * s),
               label, font=btn_f, fill=lab_col)

        # ring burst when the subscribe lands
        life = (t - SUB_CLICK_T) / 0.5
        if 0 <= life <= 1:
            rcx, rcy = sx + sub_w / 2, y_btn + y_btn_h / 2
            rr = 30 * s + _ease_out(life) * 95 * s
            al = int(220 * (1 - life))
            d.ellipse([rcx - rr, rcy - rr, rcx + rr, rcy + rr],
                      outline=VERM + (al,), width=max(int(4 * s), 1))

        # ---- cursor ----
        like_c = (tx + like_w * 0.55, y_btn + y_btn_h * 0.55)
        sub_c = (sx + sub_w * 0.5, y_btn + y_btn_h * 0.55)
        path = [(0.55, cw - 30 * s, ch - 26 * s),
                (LIKE_CLICK_T - 0.03, like_c[0], like_c[1]),
                (1.75, like_c[0], like_c[1]),
                (SUB_CLICK_T - 0.03, sub_c[0], sub_c[1]),
                (2.95, sub_c[0], sub_c[1]),
                (3.60, cw + 40 * s, ch + 20 * s)]
        if 0.55 <= t <= 3.60:
            a = int(255 * _clamp((t - 0.55) / 0.15)
                    * _clamp((3.60 - t) / 0.25))
            cx_, cy_ = _lerp_path(path, t)
            _draw_cursor(d, cx_, cy_, 2.1 * s, a)
            # click ripple at the tip
            for ct in (LIKE_CLICK_T, SUB_CLICK_T):
                rl = (t - ct) / 0.35
                if 0 <= rl <= 1:
                    rr = 6 * s + rl * 34 * s
                    al = int(200 * (1 - rl))
                    d.ellipse([cx_ - rr, cy_ - rr, cx_ + rr, cy_ + rr],
                              outline=(255, 255, 255, al),
                              width=max(int(3 * s), 1))

        # global fade on exit
        if fade < 1.0:
            arr = np.array(img)
            arr[..., 3] = (arr[..., 3] * fade).astype(np.uint8)
            img = Image.fromarray(arr)

        img.save(frames_dir / f"f{f:05d}.png")

    return n_frames, cw, ch


# ----------------------------------------------------------------- ffmpeg --
def make_click_wav(workdir, run):
    """Short synthesized UI click (mouse-click 'tick'), 48k stereo wav."""
    out = Path(workdir) / "popup_click.wav"
    if not (out.exists() and out.stat().st_size > 0):
        run(["ffmpeg", "-y", "-f", "lavfi", "-i",
             "sine=frequency=1350:duration=0.05",
             "-af", ("afade=t=in:st=0:d=0.004,afade=t=out:st=0.012:d=0.038,"
                     "aformat=sample_fmts=fltp:channel_layouts=stereo:"
                     "sample_rates=48000"),
             "-c:a", "pcm_s16le", str(out)])
    return out


def apply(video_in, video_out, frames_dir, times, x, y, run, inter_v,
          audio_br, click_wav=None, fps=FPS):
    """Overlay the pop-up PNG sequence onto video_in at each start time in
    `times`, optionally mixing a click sound at the two click moments of every
    appearance. One ffmpeg pass; video re-encoded with inter_v settings."""
    cmd = ["ffmpeg", "-y", "-i", str(video_in)]
    for _ in times:
        cmd += ["-framerate", str(fps), "-i", str(Path(frames_dir) / "f%05d.png")]
    click_offs = []
    if click_wav:
        cmd += ["-i", str(click_wav)]
        for t0 in times:
            click_offs += [t0 + LIKE_CLICK_T, t0 + SUB_CLICK_T]

    fc, prev = [], "0:v"
    for k, t0 in enumerate(times, start=1):
        end = t0 + DUR + 0.5
        fc.append(f"[{k}:v]format=rgba,setpts=PTS-STARTPTS+{t0:.3f}/TB[p{k}]")
        fc.append(f"[{prev}][p{k}]overlay={x}:{y}:eof_action=pass:"
                  f"enable='between(t,{t0:.3f},{end:.3f})'[v{k}]")
        prev = f"v{k}"

    maps = ["-map", f"[{prev}]"]
    if click_offs:
        ai = len(times) + 1
        n = len(click_offs)
        fc.append(f"[{ai}:a]asplit={n}" + "".join(f"[k{j}]" for j in range(n)))
        labs = ""
        for j, tc in enumerate(click_offs):
            ms = int(round(tc * 1000))
            fc.append(f"[k{j}]adelay={ms}|{ms},volume=-16dB[d{j}]")
            labs += f"[d{j}]"
        fc.append(f"[0:a]{labs}amix=inputs={n + 1}:duration=first:"
                  f"dropout_transition=0:normalize=0,aresample=48000[a]")
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", audio_br]
    else:
        maps += ["-map", "0:a?", "-c:a", "copy"]

    cmd += ["-filter_complex", ";".join(fc)] + maps + [*inter_v, str(video_out)]
    run(cmd)
