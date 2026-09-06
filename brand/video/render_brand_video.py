#!/usr/bin/env python3
"""render_brand_video.py — render PanelBreak INTRO (6s) and OUTRO (20s) to MP4.

Pure PIL/numpy frame synthesis piped to ffmpeg. No external footage. Matches the
channel pipeline's delivery target: H.264, yuv420p, 30fps, +loudnorm audio.

Usage:
    python3 render_brand_video.py intro
    python3 render_brand_video.py outro
    python3 render_brand_video.py both
Outputs into ./out/ next to this script.
"""
import sys, os, math, subprocess, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

HERE = Path(__file__).resolve().parent
BRAND = HERE.parent
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
FPS, W, H = 30, 1920, 1080

# palette
INK   = (19, 17, 16)
PAPER = (247, 242, 233)
VERM  = (255, 59, 59)
CYAN  = (34, 199, 233)
MUTE  = (167, 158, 144)

# voiceover (edge-tts) — one voice, kept consistent = part of the brand
VO_VOICE = "en-US-ChristopherNeural"   # warm, confident, mature
VO_RATE  = "+6%"                        # a touch energetic
VO_LINE  = "That's the arc, broken down. Tap subscribe, and I'll see you in the next panel."

ANTON = str(Path.home() / ".local/share/fonts/Anton-Regular.ttf")
MONO  = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
ICON  = BRAND / "logo" / "panelbreak-icon-ink-1024.png"

def font(path, size): return ImageFont.truetype(path, size)
def ease_out(t): return 1 - (1 - t) ** 3
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))

# ---- reusable textures ---------------------------------------------------
def halftone(w, h, spacing=18, r=1.6, alpha=26):
    """Faint halftone dot layer (paper dots on transparent)."""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(0, h, spacing):
        for x in range(0, w, spacing):
            d.ellipse([x, y, x + r * 2, y + r * 2], fill=PAPER + (alpha,))
    return layer

HALF = halftone(W, H)

def base_frame():
    img = Image.new("RGB", (W, H), INK)
    img.paste(PAPER, (0, 0), None) if False else None
    img = Image.alpha_composite(img.convert("RGBA"), HALF)
    return img

def speed_lines(strength, inward=True):
    """Radial speed lines converging on center. strength 0..1."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if strength <= 0: return layer
    d = ImageDraw.Draw(layer)
    cx, cy = W / 2, H / 2
    n = 60
    a = int(200 * strength)
    for i in range(n):
        ang = (i / n) * 2 * math.pi
        r0 = 200 + (1 - strength) * 700          # lines start far, rush in
        r1 = 1600
        x0, y0 = cx + math.cos(ang) * r0, cy + math.sin(ang) * r0
        x1, y1 = cx + math.cos(ang) * r1, cy + math.sin(ang) * r1
        d.line([x0, y0, x1, y1], fill=PAPER + (a,), width=3)
    return layer

def draw_wordmark(img, cx, cy, size, misreg=1.0, ink_on=True):
    """PANELBREAK in Anton with CMYK-misregister shadow layers."""
    f = font(ANTON, size)
    txt = "PANELBREAK"
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), txt, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]
    ox = int(size * 0.05 * misreg)
    # vermilion + cyan offset ghosts
    d.text((x - ox, y - ox * 0.9), txt, font=f, fill=VERM)
    d.text((x + ox, y + ox), txt, font=f, fill=CYAN)
    if ink_on:
        d.text((x, y), txt, font=f, fill=PAPER)   # on ink bg the top layer is paper-white
    return tw, th

def paste_icon(img, cx, cy, px):
    ic = Image.open(ICON).convert("RGBA").resize((px, px), Image.LANCZOS)
    img.alpha_composite(ic, (int(cx - px / 2), int(cy - px / 2)))

def encode(frames_dir, audio_path, out_path, nframes):
    """ffmpeg: PNG frames + audio -> H.264 yuv420p + loudnorm, faststart."""
    cmd = ["ffmpeg", "-y", "-framerate", str(FPS),
           "-i", str(frames_dir / "f%05d.png")]
    if audio_path:
        cmd += ["-i", str(audio_path)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-movflags", "+faststart"]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "192k",
                "-af", "loudnorm=I=-14:TP=-1:LRA=11",
                "-shortest"]
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ========================================================================
# INTRO — 6s / 180 frames
# ========================================================================
def render_intro():
    fdir = OUT / "intro_frames"
    if fdir.exists(): shutil.rmtree(fdir)
    fdir.mkdir()
    N = FPS * 6
    for i in range(N):
        t = i / FPS
        img = base_frame()
        cx, cy = W / 2, H / 2

        # global slow 2% zoom handled at encode-time is overkill; keep static.
        if t < 1.0:                                   # BEAT 1 ignite
            p = ease_out(clamp(t / 0.8))
            r = 26 * (1.1 * p if p < 1 else 1.0)      # overshoot then settle
            ImageDraw.Draw(img).ellipse(
                [cx - r, cy - r, cx + r, cy + r], fill=VERM)
        elif t < 2.0:                                 # BEAT 2 rush
            s = clamp((t - 1.0) / 1.0)
            img.alpha_composite(speed_lines(ease_out(s)))
            ImageDraw.Draw(img).ellipse([cx-26, cy-26, cx+26, cy+26], fill=VERM)
            if t > 1.93:                              # flash frame
                img = Image.new("RGBA", (W, H), PAPER + (255,))
        elif t < 3.8:                                 # BEAT 3 impact
            k = clamp((t - 2.0) / 0.13)               # slam-in 4 frames
            scale = 1.30 - 0.30 * ease_out(k)
            misreg = 2.0 - 1.0 * ease_out(k) if k < 1 else 1.0
            # decaying shake
            shk = max(0, 1 - (t - 2.0) / 0.3)
            dx = int(math.sin(i * 2.3) * 8 * shk)
            dy = int(math.cos(i * 1.9) * 8 * shk)
            size = int(150 * scale)
            draw_wordmark(img, cx + dx, cy + dy, size, misreg=misreg)
        elif t < 4.9:                                 # BEAT 4 tagline types on
            draw_wordmark(img, cx, cy - 40, 150)
            full = "every arc, explained_"
            chars = int(clamp((t - 3.8) / 0.8) * len(full))
            sub = full[:chars]
            fm = font(MONO, 40)
            d = ImageDraw.Draw(img)
            bb = d.textbbox((0, 0), sub, font=fm)
            d.text((cx - (bb[2]-bb[0]) / 2, cy + 90), sub, font=fm, fill=MUTE)
        else:                                         # BEAT 5 resolve -> end card
            # The wordmark + tagline HOLD (recognizable brand card), and a
            # vermilion underline sweeps across beneath the tagline. No shrink
            # to an invisible ink icon; ends on a clean, stable hold.
            s = clamp((t - 4.9) / 0.5)                 # underline sweep 0.5s
            draw_wordmark(img, cx, cy - 40, 150)
            fm = font(MONO, 40)
            tag = "every arc, explained"
            d = ImageDraw.Draw(img)
            bb = d.textbbox((0, 0), tag, font=fm)
            tagw = bb[2] - bb[0]
            d.text((cx - tagw / 2, cy + 90), tag, font=fm, fill=PAPER)
            # underline wipes L->R under the tagline
            uy = cy + 150
            ux0 = cx - tagw / 2
            uw = tagw * ease_out(s)
            d.rectangle([ux0, uy, ux0 + uw, uy + 7], fill=VERM)
            # handle fades in after the sweep completes
            if t > 5.4:
                fh = font(MONO, 30)
                ht = "@PanelBreak"
                hb = d.textbbox((0, 0), ht, font=fh)
                d.text((cx - (hb[2]-hb[0]) / 2, cy + 185), ht, font=fh, fill=MUTE)

        img.convert("RGB").save(fdir / f"f{i:05d}.png")

    # --- synth audio: cinematic bed + noise whoosh riser + layered impact ---
    # Uses filtered noise (not raw sines) for the whoosh and splat, a pitched
    # sub-drop for the hit, and reverb tail. Everything is timed to the visuals:
    #   0.0  low sub bed fades in
    #   1.0-2.0  whoosh riser (rising-pitch filtered noise) rushes in
    #   2.0  IMPACT: sub boom + white-noise splat burst + reverb tail
    #   3.8-4.9  soft mechanical ticks under the typing tagline
    #   5.4  gentle resolve swell under the end card
    a = OUT / "intro_audio.wav"
    filt = (
        # --- deep sub bed, present throughout, swells slightly toward impact ---
        "sine=frequency=42:duration=6[sub];"
        "[sub]volume=0.35,afade=t=in:st=0:d=0.6[bed];"
        # --- whoosh riser 1.0-2.0s: pink noise, band-pass sweeps up. Capped so
        #     it stays UNDER the impact (impact is the single loudest moment). ---
        "anoisesrc=d=6:c=pink:a=0.9[wn];"
        "[wn]highpass=f=300,lowpass=f=6000,"
        "volume='if(between(t,1.0,2.0), (t-1.0)*(t-1.0)*0.85, 0)':eval=frame[whoosh];"
        # --- IMPACT sub-drop: strong 120Hz boom, gated to a short hit ---
        "sine=frequency=120:duration=6[imp];"
        "[imp]volume='if(between(t,2.0,2.7), 1.6*exp(-(t-2.0)*5), 0)':eval=frame[boom];"
        # --- splat: short white-noise burst at impact (the ink hit) ---
        "anoisesrc=d=6:c=white:a=1.0[sn];"
        "[sn]highpass=f=800,"
        "volume='if(between(t,2.0,2.30), 1.2*exp(-(t-2.0)*26), 0)':eval=frame[splat];"
        # --- soft typewriter ticks 3.8-4.9 (clicky filtered noise pulses) ---
        "anoisesrc=d=6:c=white:a=1.0[tn];"
        "[tn]highpass=f=2000,"
        "volume='if(between(t,3.8,4.9)*gt(sin(t*180),0.7), 0.15, 0)':eval=frame[ticks];"
        # --- resolve swell under end card 5.2-6.0 ---
        "sine=frequency=84:duration=6[res];"
        "[res]volume='if(gt(t,5.2), (t-5.2)*0.4, 0)':eval=frame,afade=t=out:st=5.8:d=0.2[swell];"
        # --- mix, add hall reverb tail, gentle limiter ---
        "[bed][whoosh]amix=inputs=2:normalize=0[m1];"
        "[m1][boom]amix=inputs=2:normalize=0[m2];"
        "[m2][splat]amix=inputs=2:normalize=0[m3];"
        "[m3][ticks]amix=inputs=2:normalize=0[m4];"
        "[m4][swell]amix=inputs=2:normalize=0[dry];"
        "[dry]aecho=0.8:0.6:60:0.35,alimiter=limit=0.95[out]"
    )
    subprocess.run(["ffmpeg","-y","-filter_complex",filt,"-map","[out]",
                    "-t","6","-ar","48000",str(a)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    out = OUT / "panelbreak-intro-6s.mp4"
    encode(fdir, a, out, N)
    print("INTRO ->", out)

# ========================================================================
# OUTRO — 20s / 600 frames · end-screen layout
# ========================================================================
def render_outro():
    fdir = OUT / "outro_frames"
    if fdir.exists(): shutil.rmtree(fdir)
    fdir.mkdir()
    N = FPS * 20
    lock = Image.open(BRAND/"logo"/"panelbreak-lockup-ink-1904.png").convert("RGBA")
    lw = 760; lh = int(lock.height * lw / lock.width)
    lock = lock.resize((lw, lh), Image.LANCZOS)
    fmono = font(MONO, 30); fmono_s = font(MONO, 26); fcta = font(ANTON, 44)

    for i in range(N):
        t = i / FPS
        img = base_frame()
        # left brand group slides in 0-2s
        sx = ease_out(clamp(t / 2.0))
        lx = int(-lw + (170 + lw) * sx) - 60
        img.alpha_composite(lock, (max(120, 150), 300))
        d = ImageDraw.Draw(img)

        # subscribe chip w/ pulse
        if t > 1.6:
            pulse = 1 + 0.04 * math.sin((t - 1.6) * 4) if t > 2 else 1
            cw, ch = int(300 * pulse), int(84 * pulse)
            bx, by = 150, 430
            d.rounded_rectangle([bx, by, bx + cw, by + ch], radius=6, fill=VERM)
            ct = "▶ SUBSCRIBE"
            bb = d.textbbox((0, 0), ct, font=fcta)
            d.text((bx + (cw-(bb[2]-bb[0]))/2, by + (ch-(bb[3]-bb[1]))/2 - bb[1]),
                   ct, font=fcta, fill=INK)
            d.text((150, 540), "@PanelBreak · new breakdowns weekly",
                   font=fmono_s, fill=MUTE)

        # right: two video slots draw on 6-14s (dashed cyan placeholders)
        def dashed(x, y, w, h, col):
            step = 26
            for xx in range(x, x + w, step):
                d.line([xx, y, min(xx+14, x+w), y], fill=col, width=3)
                d.line([xx, y+h, min(xx+14, x+w), y+h], fill=col, width=3)
            for yy in range(y, y + h, step):
                d.line([x, yy, x, min(yy+14, y+h)], fill=col, width=3)
                d.line([x+w, yy, x+w, min(yy+14, y+h)], fill=col, width=3)
        slotw, sloth = 620, 300
        sxr = 1150
        if t > 6.0:
            dashed(sxr, 210, slotw, sloth, CYAN)
            d.text((sxr+20, 220), "WATCH NEXT", font=fmono, fill=CYAN)
        if t > 6.5:
            dashed(sxr, 560, slotw, sloth, CYAN)
            d.text((sxr+20, 570), "MORE ARCS", font=fmono, fill=CYAN)

        img.convert("RGB").save(fdir / f"f{i:05d}.png")

    # --- 1) TTS voiceover via edge-tts (channel pipeline already uses it) ---
    vo_mp3 = OUT / "outro_vo.mp3"
    vo_start = 2.2                                  # VO lands after slide-in
    try:
        subprocess.run(["edge-tts", "--voice", VO_VOICE, "--rate", VO_RATE,
                        "--text", VO_LINE, "--write-media", str(vo_mp3)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        have_vo = True
    except Exception as e:
        print("  (edge-tts unavailable, rendering without VO:", e, ")")
        have_vo = False

    # --- 2) music bed + SFX, with the bed ducking under the voice ---
    a = OUT / "outro_audio.wav"
    # SFX design:
    #   0.0  low whoosh as the brand slides in
    #   1.7  subscribe chip "pop" (short pitched blip + click)
    #   6.0  slot 1 "click"     6.5  slot 2 "click"
    #   19.x music resolves out
    bed = (
        # warm two-note pad bed
        "sine=frequency=110:duration=20[p1];"
        "sine=frequency=164.8:duration=20[p2];"
        "sine=frequency=220:duration=20[p3];"
        "[p1]volume=0.16[b1];[p2]volume=0.10[b2];[p3]volume=0.05[b3];"
        "[b1][b2]amix=inputs=2:normalize=0[bm1];"
        "[bm1][b3]amix=inputs=2:normalize=0,"
        "afade=t=in:st=0:d=1.2,afade=t=out:st=18.6:d=1.4[music];"
        # slide-in whoosh 0.0-1.6
        "anoisesrc=d=20:c=pink:a=0.8[wn];"
        "[wn]highpass=f=250,lowpass=f=5000,"
        "volume='if(between(t,0.0,1.6), 0.5*sin(3.14159*(t/1.6)), 0)':eval=frame[whoosh];"
        # subscribe pop at 1.7 (pitched blip)
        "sine=frequency=660:duration=20[pop];"
        "[pop]volume='if(between(t,1.7,1.9), 0.5*exp(-(t-1.7)*22), 0)':eval=frame[pop2];"
        # two slot clicks (filtered noise ticks)
        "anoisesrc=d=20:c=white:a=1.0[cn];"
        "[cn]highpass=f=1500,"
        "volume='if(between(t,6.0,6.09)+between(t,6.5,6.59), 0.4, 0)':eval=frame[clicks];"
        # mix all non-voice elements
        "[music][whoosh]amix=inputs=2:normalize=0[mx1];"
        "[mx1][pop2]amix=inputs=2:normalize=0[mx2];"
        "[mx2][clicks]amix=inputs=2:normalize=0[sfxbed]"
    )

    if have_vo:
        # delay VO to vo_start, high-pass + compress it, then SIDECHAIN-duck the
        # sfx/music bed under it so the words stay clear.
        # VO is input 1 (input 0 = silent placeholder so [1:a] = the voice).
        vo_delay_ms = int(vo_start * 1000)
        filt = (
            bed + ";"
            f"[1:a]adelay={vo_delay_ms}|{vo_delay_ms},"
            "highpass=f=90,acompressor=threshold=-18dB:ratio=3:attack=5:release=120,"
            "volume=1.25,apad,atrim=0:20,asplit=2[vo_duck][vo_mix];"
            "[sfxbed][vo_duck]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=300[ducked];"
            "[ducked][vo_mix]amix=inputs=2:normalize=0:duration=longest,"
            "alimiter=limit=0.95,atrim=0:20[out]"
        )
        cmd = ["ffmpeg","-y",
               "-f","lavfi","-i","anullsrc=r=48000:cl=stereo",
               "-i",str(vo_mp3),
               "-filter_complex",filt,
               "-map","[out]","-t","20","-ar","48000",str(a)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        filt = bed + ";[sfxbed]alimiter=limit=0.95[out]"
        subprocess.run(["ffmpeg","-y","-filter_complex",filt,"-map","[out]",
                        "-t","20","-ar","48000",str(a)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    out = OUT / "panelbreak-outro-20s.mp4"
    encode(fdir, a, out, N)
    print("OUTRO ->", out)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("intro", "both"): render_intro()
    if which in ("outro", "both"): render_outro()
