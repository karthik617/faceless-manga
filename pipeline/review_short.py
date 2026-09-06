#!/usr/bin/env python3
"""review_short.py — STAGE 6c: human-like automated QC of the 9:16 Short.

Mirrors review_video.py for the vertical format. Criteria distilled from
YouTube's Shorts guidance + creator-economy research (vidIQ/Backlinko/
safe-zone tooling consensus):

Deterministic tier (no LLM):
  * aspect exactly 9:16, >=1080x1920, no letterbox/pillarbox bars
  * duration in the 15-80s band (advisory over 60s)
  * loudness ~-14 LUFS / true peak <= -1 dBTP
  * first-frame quality (frame 0 doubles as the feed preview)
  * dead/flat ending (silence + static frames in the last 2s)
  * static visual spans >5s anywhere (retention killer at Shorts pace)

Vision-LLM tier (frames at 0/1/2s, spread samples, last 2s):
  * weak hook in the first 1-2 seconds (no pattern-interrupt)
  * text/captions intruding into the UI safe zones (bottom ~20% band where
    title/scrubber sit, right ~11% rail with like/subscribe buttons)
  * caption readability at phone size (<=2 lines, contrast)
  * payoff vs hook (does the clip deliver what its opening promises)
  * scanlation/third-party watermarks
  * spoiler risk (Short gives away the long-form's climax twist)
  * end card legibility, loopability (advisory)

Exit code 2 when HIGH faults remain, 0 otherwise (manga.py may then re-pick
the scene window and rebuild).

Usage:
    python3 review_short.py output/<slug>/<slug>.json \
        [--short output/<slug>/<slug>_short.mp4] [--out <report base>]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gateway
from review_common import (ffprobe_dur, ffprobe_res, grab, frame_stats,
                           sharpness, phash, hamming, parse_json,
                           write_report)

DUR_MIN, DUR_MAX = 15.0, 80.0
DUR_SWEET_MAX = 60.0
LUFS_TARGET, LUFS_TOL = -14.0, 1.5
TP_MAX = -1.0
FIRST_FRAME_LUMA_MIN = 12
SHARP_MIN = 1.0             # Laplacian-mean floor for the first frame
STATIC_SPAN_SEC = 5.0       # Shorts pace: static longer than this = fault
CARD_SECS = 3.0             # make_short's end-card length (excluded from
                            # the dead-ending scan)

FRAME_INSTRUCTION = """You are a strict YouTube SHORTS quality reviewer. I show you {n} frames from ONE vertical (9:16) Short for a manga-recap channel, sampled in order at these timestamps (seconds): {stamps}. The Short's narration (from its scenes) is:

NARRATION: "{narration}"

CHANNEL DESIGN (expected, do NOT flag): the branded header band at the top with the channel logo/name, the faint centered channel-logo watermark, the dark backdrop around the panel art, and a "FULL VIDEO" end card in the final ~3 seconds. "watermark" means THIRD-PARTY marks only: scanlation-site URLs stamped on the manga art (e.g. "...SCANS.ORG") or other platforms' logos (TikTok/CapCut).

Judge like a phone viewer scrolling the Shorts feed. For EACH frame output one JSON object:
- "frame": index (0-based)
- "hook_weak": true/false — ONLY meaningful for frames in the first 2 seconds: would this frame fail to stop a scroller (calm art, logos, setup shots, mostly text)?
- "safe_zone_text": true/false — do burned-in captions or important text sit in the BOTTOM 20% of the frame (i.e. the text's baseline clearly below the 80%-height line — where YouTube overlays title/channel/scrubber) or in the RIGHT ~11% edge column (like/comment/subscribe rail)? Captions positioned in the 70-80% band are FINE — only flag text genuinely inside the bottom fifth. For END-CARD frames (last ~3s) do not use this flag; judge the card via "endcard_ok" instead.
- "caption_issue": true/false — are captions more than 2 lines, low-contrast, tiny, or clipped at an edge?
- "watermark": true/false — scanlation/aggregator watermark or URL visible (e.g. "...SCANS.ORG"), or third-party platform logos.
- "empty": true/false — mostly black/white/flat frame with no compelling art.
- "note": one short sentence naming the biggest problem, or "".

After the frames, also output these TOP-LEVEL keys:
- "payoff_ok": true/false — across the frames, does the visual story deliver the drama the narration promises (not just repeated filler art)?
- "spoiler_risk": true/false — do the frames reveal what looks like the story's final climax/twist image (a Short should tease, not spend the payoff)?
- "endcard_ok": true/false — if a "FULL VIDEO" style end card appears in the last frames, is it legible and not covering a story beat mid-action?
- "loopable": true/false — would the last frame cut back to the first frame without feeling jarring?

Output ONLY a JSON object: {{"frames": [...], "payoff_ok": ..., "spoiler_risk": ..., "endcard_ok": ..., "loopable": ...}} — no prose, no markdown fence."""


def _ebur128(path):
    """(integrated LUFS, true peak dBTP) via ffmpeg ebur128, or (None, None)."""
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-i", str(path), "-filter_complex",
         "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True)
    lufs = tp = None
    tail = r.stderr[-3000:]
    import re
    m = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", tail)
    if m:
        lufs = float(m.group(1))
    m = re.search(r"Peak:\s*(-?[\d.]+)\s*dBFS", tail)
    if m:
        tp = float(m.group(1))
    return lufs, tp


def _tail_silent(path, dur, span=2.0):
    """True if the last `span` seconds (before any end card) are near-silent."""
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-ss", f"{max(dur - span, 0):.2f}",
         "-i", str(path), "-af", "astats=metadata=1", "-f", "null", "-"],
        capture_output=True, text=True)
    import re
    m = re.search(r"RMS level dB:\s*(-?[\d.]+|-inf)", r.stderr)
    if not m:
        return False
    v = m.group(1)
    return v == "-inf" or float(v) < -50.0


def deterministic_checks(video, tmp):
    faults = []
    dur = ffprobe_dur(video)
    w, h = ffprobe_res(video)

    # aspect + resolution
    if w == 0 or h == 0:
        return [{"t": 0.0, "type": "unreadable_file", "severity": "high",
                 "detail": "could not probe video stream"}], dur
    if abs(w / h - 9 / 16) > 0.01:
        faults.append({"t": 0.0, "type": "bad_aspect", "severity": "high",
                       "detail": f"{w}x{h} is not 9:16 — Shorts must be "
                                 f"full-frame vertical"})
    if h < 1920 or w < 1080:
        faults.append({"t": 0.0, "type": "low_resolution", "severity": "high",
                       "detail": f"{w}x{h} below 1080x1920"})

    # duration band
    if dur < DUR_MIN:
        faults.append({"t": 0.0, "type": "too_short", "severity": "medium",
                       "detail": f"{dur:.0f}s < {DUR_MIN:.0f}s — hard to "
                                 f"land a story"})
    elif dur > DUR_MAX:
        faults.append({"t": 0.0, "type": "too_long", "severity": "medium",
                       "detail": f"{dur:.0f}s > {DUR_MAX:.0f}s"})
    elif dur > DUR_SWEET_MAX:
        faults.append({"t": 0.0, "type": "over_sweet_spot", "severity": "low",
                       "detail": f"{dur:.0f}s — 15-60s retains best; fine if "
                                 f"the story needs it"})

    # loudness
    lufs, tp = _ebur128(video)
    if lufs is not None and abs(lufs - LUFS_TARGET) > LUFS_TOL:
        faults.append({"t": 0.0, "type": "loudness", "severity": "medium",
                       "detail": f"integrated {lufs:.1f} LUFS (target "
                                 f"{LUFS_TARGET:+.0f} ±{LUFS_TOL})"})
    if tp is not None and tp > TP_MAX:
        faults.append({"t": 0.0, "type": "true_peak", "severity": "medium",
                       "detail": f"true peak {tp:.1f} dBTP > {TP_MAX}"})

    # TRUE letterbox: uniform black bars covering >=12% of the height at BOTH
    # top and bottom (a 16:9 video dropped into a 9:16 canvas). The channel's
    # intentional dark side-backdrop and header band must NOT trigger this.
    bar_hits = 0
    samples = [dur * k / 5 for k in range(1, 5)]
    for k, t in enumerate(samples):
        f = tmp / f"bars_{k}.png"
        if not grab(video, t, f, h=480):
            continue
        import numpy as np
        from PIL import Image
        g = np.asarray(Image.open(f).convert("L"), dtype=np.float32)
        hh = g.shape[0]
        band = max(int(hh * 0.12), 1)
        top, bottom = g[:band], g[-band:]
        if all(s.std() < 2.0 and s.mean() < 16 for s in (top, bottom)):
            bar_hits += 1
    if bar_hits >= 3:
        faults.append({"t": 0.0, "type": "letterbox", "severity": "high",
                       "detail": f"uniform black bars top+bottom on "
                                 f"{bar_hits}/4 sampled frames — a 16:9 "
                                 f"video letterboxed into 9:16"})

    # first frame = feed preview
    f0 = tmp / "first.png"
    if grab(video, 0.05, f0, h=640):
        luma, frac = frame_stats(f0)
        if luma < FIRST_FRAME_LUMA_MIN or frac < 0.04:
            faults.append({"t": 0.0, "type": "bad_first_frame",
                           "severity": "high",
                           "detail": f"frame 0 is near-black/empty (luma "
                                     f"{luma:.0f}) — it is the feed preview"})
        elif sharpness(f0) < SHARP_MIN:
            faults.append({"t": 0.0, "type": "blurry_first_frame",
                           "severity": "medium",
                           "detail": "frame 0 looks blurry/mid-transition"})

    # dead ending: silence + static frames just before the end card
    probe_end = max(dur - CARD_SECS, 0)
    if _tail_silent(video, probe_end, span=2.0):
        faults.append({"t": round(probe_end - 2, 1), "type": "dead_ending",
                       "severity": "medium",
                       "detail": "near-silence in the last seconds before "
                                 "the end card"})

    # static spans > STATIC_SPAN_SEC. Strict identity (hamming <=2) so the
    # intentional slow Ken Burns zoom does NOT read as static — only a truly
    # frozen image trips this. Consecutive hits merge into one fault.
    t, prev, span_start = 0.5, None, 0.5
    static_spans = []
    while t < dur - 0.5:
        f = tmp / f"st_{int(t*10):05d}.png"
        if grab(video, t, f, h=160):
            hsh = phash(f)
            if prev is not None and hamming(hsh, prev) <= 2:
                if t - span_start >= STATIC_SPAN_SEC:
                    if static_spans and span_start <= static_spans[-1][1] + 1.1:
                        static_spans[-1][1] = t
                    else:
                        static_spans.append([span_start, t])
            else:
                span_start = t
            prev = hsh
        t += 1.0
    for s0, s1 in static_spans:
        faults.append({"t": round(s0, 1), "type": "static_scene",
                       "severity": "medium",
                       "detail": f"visuals frozen for {s1 - s0:.0f}s (Shorts "
                                 f"pace: change every ~2-3s)"})
    return faults, dur


def vision_review(video, dur, narration, tmp, jobs=1):
    """One batched vision call over hook frames + spread + tail."""
    stamps = [0.2, 1.0, 2.0]
    stamps += [dur * f for f in (0.3, 0.5, 0.7, 0.85)]
    stamps += [max(dur - 1.5, 0), max(dur - 0.3, 0)]
    stamps = sorted({round(min(s, max(dur - 0.05, 0)), 2) for s in stamps})
    frames = []
    for k, t in enumerate(stamps):
        f = tmp / f"vf_{k:02d}.jpg"
        if grab(video, t, f, h=960):
            frames.append((t, f))
    if not frames:
        return [{"t": 0.0, "type": "review_error", "severity": "low",
                 "detail": "no frames could be extracted"}]
    instr = FRAME_INSTRUCTION.format(
        n=len(frames),
        stamps=", ".join(f"{t:.1f}" for t, _ in frames),
        narration=narration[:900])
    try:
        raw = gateway.llm_vision([f for _, f in frames], instr)
        obj = parse_json(raw, "object")
    except Exception as ex:
        return [{"t": 0.0, "type": "review_error", "severity": "low",
                 "detail": f"{type(ex).__name__}: {ex}"}]
    faults = []
    for k, item in enumerate((obj.get("frames") or [])[:len(frames)]):
        if not isinstance(item, dict):
            continue
        t = frames[k][0]
        base = {"t": round(t, 1)}
        if item.get("hook_weak") and t <= 2.2:
            faults.append({**base, "type": "weak_hook", "severity": "high",
                           "detail": item.get("note",
                                              "first seconds won't stop "
                                              "a scroller")})
        if item.get("safe_zone_text") and t < dur - CARD_SECS:
            # end-card frames are judged via endcard_ok, not safe zones
            faults.append({**base, "type": "safe_zone_text",
                           "severity": "high",
                           "detail": item.get("note",
                                              "text in the YouTube UI zone "
                                              "(bottom band / right rail)")})
        if item.get("caption_issue"):
            faults.append({**base, "type": "caption_readability",
                           "severity": "medium",
                           "detail": item.get("note", "captions hard to "
                                                      "read on a phone")})
        if item.get("watermark"):
            faults.append({**base, "type": "watermark", "severity": "high",
                           "detail": item.get("note",
                                              "third-party watermark")})
        if item.get("empty"):
            faults.append({**base, "type": "empty_screen",
                           "severity": "medium",
                           "detail": item.get("note", "flat/empty frame")})
    if obj.get("payoff_ok") is False:
        faults.append({"t": 0.0, "type": "payoff_vs_hook", "severity": "high",
                       "detail": "visuals never deliver the drama the "
                                 "narration promises"})
    if obj.get("spoiler_risk") is True:
        faults.append({"t": 0.0, "type": "spoiler", "severity": "medium",
                       "detail": "Short appears to spend the long-form's "
                                 "climax image"})
    if obj.get("endcard_ok") is False:
        faults.append({"t": round(max(dur - CARD_SECS, 0), 1),
                       "type": "end_card", "severity": "low",
                       "detail": "end card illegible or covers a story beat"})
    if obj.get("loopable") is False:
        faults.append({"t": round(dur, 1), "type": "loopability",
                       "severity": "low",
                       "detail": "ending does not loop back into the "
                                 "opening (advisory)"})
    return faults


HUMAN_CHECKS = [
    "",
    "## Human spot-checks",
    "",
    "- [ ] Watch MUTED on a phone: is the story followable from art + "
    "captions alone?",
    "- [ ] Watch with sound: do caption pops land on the narration beats?",
    "- [ ] Does the music drop/lift where the story peaks?",
    "- [ ] Would YOU rewatch it? (loop feel is the strongest Shorts signal)",
    "- [ ] Does the end card tease the long-form without begging?",
]


def short_narration(data, short_meta=None):
    """Concatenated narration of the scenes the Short window covers (falls
    back to all scenes when the window is unknown)."""
    scenes = data.get("scenes", [])
    if short_meta and "scenes" in short_meta:
        lo, hi = short_meta["scenes"]
        scenes = scenes[lo:hi + 1]
    return " ".join(sc.get("narration", "") for sc in scenes)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="output/<slug>/<slug>.json")
    ap.add_argument("--short", default=None,
                    help="path to the Short (default <slug>_short.mp4)")
    ap.add_argument("--out", default=None, help="report path base")
    ap.add_argument("--det-only", action="store_true",
                    help="deterministic checks only (no vision-LLM)")
    args = ap.parse_args()

    spath = Path(args.script)
    data = json.loads(spath.read_text())
    proj = spath.parent
    video = Path(args.short) if args.short else \
        proj / (spath.stem + "_short.mp4")
    if not video.exists():
        sys.exit(f"short not found: {video}")

    meta_p = proj / "_short_work" / "window.json"
    short_meta = None
    if meta_p.exists():
        try:
            short_meta = json.loads(meta_p.read_text())
        except Exception:
            pass

    tmp = Path(tempfile.mkdtemp(prefix="review_short_"))
    print(f"Reviewing Short {video.name}...")
    faults, dur = deterministic_checks(video, tmp)
    print(f"  deterministic: {len(faults)} fault(s) ({dur:.0f}s)")
    if not args.det_only:
        vf = vision_review(video, dur, short_narration(data, short_meta), tmp)
        print(f"  vision review: {len(vf)} fault(s)")
        faults.extend(vf)

    out_base = Path(args.out) if args.out else proj / "review_short"
    extra = HUMAN_CHECKS if not args.det_only else ()
    nh, nm = write_report(out_base, faults,
                          f"Short review — {video.name}", extra)
    print(f"  report: {out_base}.md ({nh} high, {nm} medium)")
    sys.exit(2 if nh else 0)


if __name__ == "__main__":
    main()
