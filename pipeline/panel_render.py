#!/usr/bin/env python3
"""panel_render.py — STAGE 5: render the extended scene JSON into an MP4 using
the REAL panels, delegating all heavy work to faceless-youtube's make_video.py.

Only the per-scene loop is reimplemented (to display real panel art with a
blurred-background Ken Burns instead of a generated image). Everything else —
edge-tts word timing, sidechain music, word-timed captions, and the single
loudnorm/H.264-CRF18/faststart final encode — is reused verbatim from
make_video (imported as `mv`). make_video.py is NOT modified.

Branding: no intro/outro videos. Instead an animated channel pop-up
(subscribe_popup.py) — logo + channel name + LIKE + SUBSCRIBE with YouTube-
style click animations — is overlaid after the hook and near the end.

Scene branch rule:
  * scene has "panels" (or "panel")  -> render real art (blurred-bg Ken Burns)
  * else                             -> fall back to mv.render_scene(image_prompt)
panels_clean/<name> is auto-substituted for panels/<name> when it exists.

Usage:
    python3 panel_render.py output/<slug>/<slug>.json [--karaoke] [--no-music]
        [--no-branding] [--res 1440p] [--workdir DIR] [--redo-scene N]
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# --- import faceless-youtube's make_video as the backbone (no copy, no edit) ---
FYT_PIPELINE = Path.home() / "faceless-youtube" / "pipeline"
sys.path.insert(0, str(FYT_PIPELINE))
try:
    import make_video as mv
except Exception as e:  # pragma: no cover
    sys.exit(f"could not import make_video from {FYT_PIPELINE}: {e}")

# NARRATION-DRIVEN editing cadence (Shonen-Flux style): the cut rate follows
# the scene's emotional register instead of a fixed clock. "hype" beats strobe,
# "quiet" beats dwell on one panel and let the art + slow Ken Burns carry it.
# A scene picks its pace via a "pace" field ("hype"/"normal"/"quiet"); scenes
# without one infer it: emphasis -> hype, focus -> quiet, else normal.
PACE_CUT_SEC = {"hype": 1.6, "normal": 3.5, "quiet": 8.0}
MAX_CUTS_PER_SCENE = 8
END_TAIL_SEC = 2.5   # hold the last scene this long after the final word
# uniform video timescale for copy-concat. Every per-scene clip is remuxed to
# this before concatenation so the concat demuxer builds a monotonic timeline
# (mixed timebases otherwise corrupt the muxed container duration). 15360 is a
# clean multiple of common frame rates (30/24/25).
CONCAT_TIMESCALE = 15360
# silence truncation: hard-cut only ABNORMALLY long internal pauses. The floor
# kept at each cut is high enough that natural breaths and dramatic beats
# survive — documentary-essay delivery needs air, not a relentless wall.
TRUNCATE_SILENCE_MS = 450
SILENCE_THRESH = "-30dB"

# ---- engagement SFX (Haikyuu-recap style) ----
# whoosh on each panel swap, impact "hit" on emphasis beats. These are ffmpeg-
# synthesized PLACEHOLDERS; a real pipeline/brand/sfx/<name>.wav overrides the
# synth if present, so authentic ball-hit / crowd-clap files can be dropped in
# later with no code change. Levels are pre-loudnorm so the -14 LUFS master holds.
SFX_WHOOSH_DB = -13.0     # transition swish (subtle)
SFX_IMPACT_DB = -7.0      # key-beat hit (punchy)
SFX_SUBBOOM_DB = -10.0    # sub-bass rumble layered under an impact for weight
SFX_MAX_CUES = 60         # cap adelay+amix inputs so the filtergraph stays sane


def _ensure_sfx(workdir, brand_dir):
    """Return {name: path} for whoosh/impact/riser. A real
    pipeline/brand/sfx/<name>.wav wins; otherwise synthesize a placeholder once
    per workdir with ffmpeg lavfi. All are short (<1s), 48k stereo."""
    sfx_dir = brand_dir / "sfx"
    out = {}
    specs = {
        # whoosh: white noise, rising bandpass sweep, quick in/out fade
        "whoosh": ("anoisesrc=color=white:amplitude=0.6:duration=0.45,"
                   "highpass=f=300,lowpass=f=6000,"
                   "afade=t=in:st=0:d=0.06,afade=t=out:st=0.18:d=0.27,"
                   "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000"),
        # impact: low sine thud + tiny noise transient, fast decay
        "impact": ("sine=frequency=72:duration=0.35,"
                   "afade=t=out:st=0.03:d=0.32,"
                   "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000"),
        # riser: short rising tone (build into a big beat)
        "riser": ("sine=frequency=220:duration=0.6,"
                  "afade=t=in:st=0:d=0.5,afade=t=out:st=0.5:d=0.1,"
                  "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000"),
        # subboom: deep sub-bass rumble layered UNDER an impact so the biggest
        # beats land with weight (Shonen-Flux tier-2 "sub-boom / impact rumble").
        # ~45Hz sine with a slow-ish decay for body.
        "subboom": ("sine=frequency=45:duration=0.55,"
                    "afade=t=in:st=0:d=0.02,afade=t=out:st=0.12:d=0.43,"
                    "aformat=sample_fmts=fltp:channel_layouts=stereo:sample_rates=48000"),
    }
    for name, filt in specs.items():
        real = sfx_dir / f"{name}.wav"
        if real.exists() and real.stat().st_size > 0:
            out[name] = real
            continue
        syn = workdir / f"sfx_{name}.wav"
        if not (syn.exists() and syn.stat().st_size > 0):
            # filt = "<lavfi source>,<filterchain>"; first token is the -i source
            src, _, chain = filt.partition(",")
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src]
            if chain:
                cmd += ["-af", chain]
            cmd += ["-c:a", "pcm_s16le", str(syn)]
            mv.run(cmd)
        out[name] = syn
    return out


def _norm_word(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _cue_relevant(cue, narration):
    """True only when the SFX cue clearly relates to the narration text: at
    least one meaningful cue word (>=4 chars) must appear in the narration as
    a prefix-stem match (slam/slams/slammed, laugh/laughing, cry/crying).
    Generic filler words in cues don't count. This keeps SFX tied to what the
    narrator actually says instead of decorating the scene at random."""
    stop = {"sound", "noise", "effect", "loud", "soft", "big", "small"}
    narr = {_norm_word(w) for w in narration.split()}
    narr.discard("")
    for cw in cue.split():
        cw = _norm_word(cw)
        if len(cw) < 3 or cw in stop:
            continue
        stem = cw[:4] if len(cw) >= 4 else cw
        if any(nw.startswith(stem) or cw.startswith(nw[:4]) for nw in narr
               if len(nw) >= 3):
            return True
    return False


def _plan_content_cues(scenes, durations, word_times, intro_dur, hook_n,
                       cue_paths, db_offset=0.0):
    """CONTENT-matched SFX: each scene's `sfx_events` places a sound at the
    exact word it describes. Returns (cues, name_map) where cues is
    [(t_seconds, slug, gain_db), ...] and name_map is {slug: Path}.

    Timing mirrors the caption model: scene start = sum of prior durations,
    +intro_dur once past the hook. An event's time is scene_start + the synced
    word's start (from word_times); if the word isn't found, scene start. An
    event whose cue had NO CC0 match (cue_paths[cue] is None) is SKIPPED.
    `db_offset` shifts every cue's gain (a global level trim; negative =
    quieter). A per-event "gain" is the base; the offset is added on top.
    """
    from fetch_sfx import slugify
    cues, name_map = [], {}
    t = 0.0
    for i, (scene, dur) in enumerate(zip(scenes, durations)):
        if i == hook_n:
            t += intro_dur
        start = t
        wt = word_times[i] if word_times and i < len(word_times) else None
        for ev in scene.get("sfx_events", []) or []:
            cue = ev.get("cue")
            path = cue_paths.get(cue)
            if not cue or not path:
                continue   # skip-if-missing
            # STRICT relevance gates — an SFX only lands when we're sure it
            # belongs: (1) the cue must relate to the narration text itself,
            # and (2) the synced word must actually be found in the word
            # timings (no fallback to scene start = no randomly-placed SFX).
            if not _cue_relevant(cue, scene.get("narration", "")):
                print(f"    skip SFX {cue!r} (scene {i+1}): cue does not "
                      f"match the narration")
                continue
            off = None
            target = _norm_word(ev.get("word", ""))
            if target and wt:
                for w in wt:
                    if _norm_word(w["word"]) == target:
                        off = float(w["start"])
                        break
            if off is None:
                print(f"    skip SFX {cue!r} (scene {i+1}): sync word "
                      f"{ev.get('word')!r} not found in word timings")
                continue
            slug = slugify(cue)
            name_map[slug] = path
            gain = float(ev.get("gain", -7.0)) + db_offset
            cues.append((start + off, slug, gain))
        t += dur
    cues.sort(key=lambda c: c[0])
    return cues[:SFX_MAX_CUES], name_map


def _mix_sfx(video_in, cues, sfx_paths, out_path):
    """Overlay SFX cues onto video_in's audio at their timestamps via
    adelay+amix, writing out_path. Video is stream-copied. Each cue is one
    extra input, delayed to its start time and level-set, then amixed with the
    base track (which is kept at unity so voice/music aren't attenuated)."""
    cmd = ["ffmpeg", "-y", "-i", str(video_in)]
    for _, name, _ in cues:
        cmd += ["-i", str(sfx_paths[name])]
    parts = []
    labels = ["[0:a]"]  # base track first, unmodified
    for k, (t, name, db) in enumerate(cues, start=1):
        ms = int(round(t * 1000))
        parts.append(f"[{k}:a]adelay={ms}|{ms},volume={db}dB[s{k}]")
        labels.append(f"[s{k}]")
    n = len(cues) + 1
    # amix normalizes by input count; multiply back up so the base track stays
    # at ~unity and SFX ride on top at their set dB.
    fc = (";".join(parts) + ";" + "".join(labels) +
          f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0,"
          f"aresample=48000[a]")
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", mv.AUDIO_BR, str(out_path)]
    mv.run(cmd)


MUSIC_XFADE = 2.0   # crossfade between music-plan sections


def _build_music_bed(plan, mood_paths, durations, total, workdir):
    """Stitch a narrative-scored bed from the script's music_plan: each
    section's mood bed is looped/trimmed to its section length, then the
    sections are joined with a short acrossfade. Returns the bed path, or
    None when the plan is unusable (caller falls back to the single bed).

    plan: [{"mood": str, "from_scene": int}, ...] (from_scene is 0-based,
    ascending). Section k spans from its first scene's start to the next
    section's start (last runs to `total`)."""
    valid = [s for s in plan
             if s.get("mood") in mood_paths
             and isinstance(s.get("from_scene"), int)
             and 0 <= s["from_scene"] < len(durations)]
    if not valid:
        return None
    valid.sort(key=lambda s: s["from_scene"])
    if valid[0]["from_scene"] != 0:   # bed must start at t=0
        valid[0] = dict(valid[0], from_scene=0)
    starts = [sum(durations[:s["from_scene"]]) for s in valid]
    ends = starts[1:] + [total]
    segs = []
    for k, (s, t0, t1) in enumerate(zip(valid, starts, ends)):
        seg_len = t1 - t0
        if seg_len <= 0.5:
            continue
        # sections after the first carry extra head for the crossfade overlap
        pad = MUSIC_XFADE if k > 0 else 0.0
        seg = workdir / f"bed_seg{k}.m4a"
        mv.run(["ffmpeg", "-y", "-stream_loop", "-1",
                "-i", str(mood_paths[s["mood"]]),
                "-t", f"{seg_len + pad:.3f}",
                "-af", "aformat=sample_fmts=fltp:channel_layouts=stereo:"
                       "sample_rates=48000",
                "-c:a", "aac", "-b:a", "192k", str(seg)])
        segs.append(seg)
    if not segs:
        return None
    if len(segs) == 1:
        return segs[0]
    out = workdir / "bed_planned.m4a"
    cmd = ["ffmpeg", "-y"]
    for s in segs:
        cmd += ["-i", str(s)]
    fc, prev = [], "0:a"
    for k in range(1, len(segs)):
        lbl = f"bx{k}"
        fc.append(f"[{prev}][{k}:a]acrossfade=d={MUSIC_XFADE}:c1=tri:c2=tri"
                  f"[{lbl}]")
        prev = lbl
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{prev}]",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    mv.run(cmd)
    return out


AMBIENCE_DB = -24.0   # atmosphere loops sit far under voice/music


def _plan_ambience(scenes, durations, intro_dur, hook_n):
    """Merge consecutive scenes sharing the same `ambience` value into spans:
    [(start, end, cue), ...] in absolute timeline seconds."""
    spans = []
    t = 0.0
    for i, (scene, dur) in enumerate(zip(scenes, durations)):
        if i == hook_n:
            t += intro_dur
        cue = (scene.get("ambience") or "").strip().lower()
        if cue:
            if spans and spans[-1][2] == cue and abs(spans[-1][1] - t) < 0.5:
                spans[-1] = (spans[-1][0], t + dur, cue)
            else:
                spans.append((t, t + dur, cue))
        t += dur
    return spans


def _mix_ambience(video_in, spans, cue_paths, out_path):
    """Loop each ambience span's sound under the existing mix: the loop is
    trimmed to the span length, faded in/out, delayed to the span start, and
    amixed at AMBIENCE_DB with the base track kept at unity."""
    from fetch_sfx import slugify
    usable = [(s, e, c) for s, e, c in spans if cue_paths.get(c)]
    if not usable:
        return False
    cmd = ["ffmpeg", "-y", "-i", str(video_in)]
    for s, e, c in usable:
        cmd += ["-stream_loop", "-1", "-i", str(cue_paths[c])]
    parts, labels = [], ["[0:a]"]
    for k, (s, e, c) in enumerate(usable, start=1):
        ln = e - s
        ms = int(round(s * 1000))
        fade = min(1.5, ln / 4)
        parts.append(
            f"[{k}:a]atrim=end={ln:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade:.2f},"
            f"afade=t=out:st={max(ln - fade, 0):.2f}:d={fade:.2f},"
            f"volume={AMBIENCE_DB}dB,adelay={ms}|{ms}[amb{k}]")
        labels.append(f"[amb{k}]")
    n = len(usable) + 1
    fc = (";".join(parts) + ";" + "".join(labels) +
          f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0,"
          f"aresample=48000[a]")
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", mv.AUDIO_BR, str(out_path)]
    mv.run(cmd)
    return True


def _detect_silences(audio, thresh=SILENCE_THRESH, min_dur=0.05):
    """Return [(start, end), ...] silence intervals in `audio` (seconds), via
    ffmpeg silencedetect. Parsed from stderr; empty list if none/parse fails."""
    res = mv.run(["ffmpeg", "-i", str(audio), "-af",
                  f"silencedetect=noise={thresh}:d={min_dur:.3f}",
                  "-f", "null", "-"])
    starts, sils = [], []
    for line in res.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m and starts:
            sils.append((starts.pop(), float(m.group(1))))
    return sils


def _overlaps(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def _truncate_silence(audio, word_times, workdir, name,
                      max_gap_ms=TRUNCATE_SILENCE_MS, protected=None):
    """Hard-cut pauses longer than max_gap_ms from `audio`, keeping a floor of
    max_gap_ms at each cut, and REMAP word_times so captions stay in sync.

    `protected` is a list of (start,end) windows that must NOT be cut (the
    [Pause] beat-drop silences). Any detected silence overlapping a protected
    window is skipped so the deliberate pause survives.

    Returns (new_audio_path, new_word_times). If nothing is removable the input
    audio and word_times are returned unchanged. We compute the removed
    intervals ourselves (not ffmpeg silenceremove) so the caption remap is
    exact: each word time is shifted left by the total removed duration that
    falls before it.
    """
    protected = protected or []
    floor = max_gap_ms / 1000.0
    sils = _detect_silences(audio)
    # removable slice of each over-long silence: trim to a `floor`-length pause.
    # Keep floor/2 of padding on each side so speech isn't clipped.
    removed = []  # (cut_start, cut_end) to delete
    for s, e in sils:
        if e - s <= floor:
            continue
        if any(_overlaps(s, e, p0, p1) for p0, p1 in protected):
            continue   # deliberate beat-drop pause -> keep it
        pad = floor / 2.0
        cut_s, cut_e = s + pad, e - pad
        if cut_e > cut_s:
            removed.append((cut_s, cut_e))
    if not removed:
        return audio, word_times, protected

    # build keep-segments (complement of removed) and atrim+concat them
    total = mv.ffprobe_duration(audio)
    keep, prev = [], 0.0
    for cs, ce in removed:
        if cs > prev:
            keep.append((prev, cs))
        prev = ce
    if prev < total:
        keep.append((prev, total))

    parts = []
    for k, (ks, ke) in enumerate(keep):
        parts.append(f"[0:a]atrim=start={ks:.4f}:end={ke:.4f},"
                     f"asetpts=PTS-STARTPTS[s{k}]")
    concat_in = "".join(f"[s{k}]" for k in range(len(keep)))
    fc = ";".join(parts) + f";{concat_in}concat=n={len(keep)}:v=0:a=1[a]"
    out = workdir / f"{name}_trunc.mp3"
    mv.run(["ffmpeg", "-y", "-i", str(audio), "-filter_complex", fc,
            "-map", "[a]", "-c:a", "libmp3lame", "-q:a", "2", str(out)])

    # remap word times: shift each left by removed duration occurring before it
    def shift(t):
        d = sum(min(ce, t) - cs for cs, ce in removed if cs < t)
        return max(t - d, 0.0)
    new_wt = None
    if word_times:
        new_wt = []
        for w in word_times:
            s = shift(w["start"])
            e = max(shift(w["end"]), s)
            new_wt.append({"word": w["word"], "start": s, "end": e})
    # remap protected windows the same way (they were never cut, only shifted)
    new_prot = [(shift(p0), shift(p1)) for p0, p1 in protected]
    return out, new_wt, new_prot


SLIDE_XF = 0.20   # panel slide-in duration (snappy)
_SLIDE_DIRS = ["slideleft", "slideright", "slideup", "slidedown"]
BEAT_DROP_SEC = 0.8   # held silence at a [Pause] marker for a music beat-drop


# Onomatopoeia the TTS mispronounces: uppercase "HA HA HA" is read as
# letters ("H-A-H-A"). Rewrite laughter/interjection runs to a lowercase,
# hyphenated form edge-tts speaks naturally. Case-insensitive on the run but
# only triggers on REPEATED tokens so normal words are untouched.
_LAUGH_TOKENS = r"(?:HA|HAH|HEH|HE|HO|FU|KU|HAHA|AHAHA|BWAHA|GYAHA|KEKE|KUKU)"
_LAUGH_RUN = re.compile(
    rf"\b({_LAUGH_TOKENS})((?:[\s\-—…]+{_LAUGH_TOKENS}){{1,}})\b",
    re.IGNORECASE)


def _speakable(text):
    """Make narration TTS-safe:
    1) collapse repeated laugh tokens ('HA HA HA HA' -> 'hahaha');
    2) de-shout ALL-CAPS words ('HAND-OUTS!!' -> 'hand-outs!!') — edge-tts
       treats uppercase words as acronyms and spells them letter by letter
       ('OUTS' -> 'O U T S'). Script caps are visual emphasis only; the
       spoken form must be normal case. Single letters (I, A) are kept."""
    def _fix(m):
        run = [t for t in re.split(r"[\s\-—…]+", m.group(0)) if t]
        # keep the tokens SEPARATE and lowercase: edge-tts voices spaced
        # 'ha ha ha' as distinct laugh syllables (fused 'hahaha' collapses
        # to a single short 'ha'). Preserve the run length (cap 5) so a
        # long panel laugh still sounds continuous.
        return " ".join(t.lower() for t in run[:5])
    text = _LAUGH_RUN.sub(_fix, text)
    # lowercase any all-caps word of 2+ letters (incl. hyphenated shouts).
    # Don't touch mixed-case words or single letters.
    text = re.sub(r"\b[A-Z][A-Z'\-]{1,}\b",
                  lambda m: m.group(0).lower(), text)
    return text


def _tts_with_pauses(narration, voice, out_audio, workdir, name,
                     rate="+0%", pitch="+0Hz"):
    narration = _speakable(narration)
    """TTS a narration that may contain literal [Pause] markers. Each [Pause] is
    stripped from the spoken text and replaced by a protected BEAT_DROP_SEC of
    silence that survives truncation. Returns (word_times, [(start,end),...])
    where the second list is the protected pause windows (absolute seconds in
    the output audio), for the music beat-drop spike.

    No [Pause] -> a plain mv.tts call (identical to before)."""
    segs = [s.strip() for s in narration.split("[Pause]")]
    if len(segs) == 1:
        wt = mv.tts(narration, voice, out_audio, rate=rate, pitch=pitch)
        return wt, []
    # synth each segment, measure, and stitch with a protected silence between
    seg_audio, seg_wt, seg_dur = [], [], []
    for j, seg in enumerate(segs):
        a = workdir / f"{name}_seg{j}.mp3"
        wt = mv.tts(seg or ".", voice, a, rate=rate, pitch=pitch)
        seg_audio.append(a)
        seg_wt.append(wt)
        seg_dur.append(mv.ffprobe_duration(a))
    sil = workdir / f"{name}_pausesil.mp3"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-t", f"{BEAT_DROP_SEC:.3f}",
            "-c:a", "libmp3lame", str(sil)])
    # concat: seg0, silence, seg1, silence, ... via the concat demuxer
    parts, order = [], []
    for j, a in enumerate(seg_audio):
        order.append(a)
        if j < len(seg_audio) - 1:
            order.append(sil)
    lst = workdir / f"{name}_pause.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in order))
    mv.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:a", "libmp3lame", "-q:a", "2", str(out_audio)])
    # rebuild absolute word_times + protected windows by accumulating offsets
    words, windows, t = [], [], 0.0
    for j, wt in enumerate(seg_wt):
        for wdt in (wt or []):
            words.append({"word": wdt["word"],
                          "start": t + wdt["start"],
                          "end": t + wdt["end"]})
        t += seg_dur[j]
        if j < len(seg_wt) - 1:
            windows.append((t, t + BEAT_DROP_SEC))   # protected pause
            t += BEAT_DROP_SEC
    return words, windows


def _slide_join(subclips, out_path, sub_dur):
    """Join equal-length sub-clips with a short directional xfade slide, so each
    new panel pushes the previous one off-canvas. Total length is
    N*sub_dur-(N-1)*SLIDE_XF; the caller re-times to the scene duration when it
    reattaches audio, so small drift is clamped there."""
    cmd = ["ffmpeg", "-y"]
    for c in subclips:
        cmd += ["-i", str(c)]
    fc = []
    prev = "0:v"
    offset = sub_dur - SLIDE_XF
    for k in range(1, len(subclips)):
        lbl = f"vx{k}"
        trans = _SLIDE_DIRS[(k - 1) % len(_SLIDE_DIRS)]
        fc.append(f"[{prev}][{k}:v]xfade=transition={trans}:"
                  f"duration={SLIDE_XF}:offset={offset:.3f}[{lbl}]")
        prev = lbl
        offset += sub_dur - SLIDE_XF
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{prev}]", "-an",
            *mv._INTER_V, str(out_path)]
    mv.run(cmd)


# ---- text-aware framing (gap-009 / exp-009, behind --text-aware) ----
# Minimum readable line height at 1080p on a phone: BBC subtitle sizing,
# Material Design 12sp->px, physical mm-at-arm's-length and broadcast minima
# all converge on a 40-59px band (research §2). 40 is the action trigger;
# scaled by frame_h/1080 for other resolutions. The emphasis punch-in peaks
# at 1.16x — a 20px line becomes 23px — so zoom can NEVER close this gap;
# the fix is framing: crop the blur_bg fit window to the text-bearing region.
MIN_TEXT_H_1080 = 40
# exp-009 v4: TRIGGER threshold, split from the 40px TARGET above. v3's
# round4 eval flagged the p0126 crop (median line already 36px rendered —
# squint-free on a phone) as an irrelevant-panel-adjacent reframe: cropping
# text that is merely below COMFORTABLE (40px) trades framing quality for a
# marginal readability gain. A crop now fires only when the default framing
# renders the median line below this GENUINELY-UNREADABLE floor; once it
# fires, the crop still aims for the 40px comfort target. 30px at 1080p ≈
# 1.9mm cap height on a 6" phone at arm's length — the strained-reading
# floor of research §2's sizing sources (the 40-59px band is "comfortable",
# ~0.75x its lower edge is where users stop being able to read without
# effort). Golden-chapter validation: the reviewer-confirmed unreadable
# info card (p0024) renders 14.7px, confirmed-fine p0126/p0060/p0003 render
# 34-38px — 30 separates every confirmed-bad from every confirmed-fine
# instance with margin on both sides.
TEXT_TRIGGER_H_1080 = 30
TEXT_PAD = 24            # panel-px padding beyond every box edge (never
                         # half-cut a bubble; gap-009 quality guard)
TEXT_CROP_FLOOR = 0.55   # crop window covers >= this fraction of the panel's
                         # smaller dimension (cropped_content guard) — unless
                         # even that can't reach MIN_TEXT_H, then escalate to
                         # the bare padded text-region union (research §3c)
TEXT_MIN_ONSCREEN = 1.2  # act only when the panel holds the screen this long
                         # (hysteresis: a 0.8s hype flash isn't worth a crop)

# ---- exp-009 v2 guards (v1 FAIL fixes; see experiments/exp-009/report.md) --
# (1) containment under MOTION: the Ken Burns zoompan is center-anchored, so
# the intersection of the visible windows across the whole pan/zoom path is
# simply the window at MAX zoom — a centered sub-rect of the crop with dims
# (cw/z_max, ch/z_max). (The true visible extent is ss/(z*fit) >= dim/z, so
# dim/z_max is a conservative lower bound.) v1 checked boxes against the
# STATIC crop only; a box sitting within dim*(1-1/z_max)/2 of a crop edge was
# progressively cut as the zoom ran (scene 20 t=628.5 half-cut bubble). v2:
# every box intersecting the crop must fit inside the max-zoom window or the
# crop is REJECTED (user-approved direction — reject, don't force).
MOTION_ZMAX = 1.08       # non-emphasis zoompan peak (see render_panel_scene z)
MOTION_ZMAX_EMPH = 1.16  # emphasis punch-in start zoom
MOTION_JITTER = 4        # panel px: zoompan rounds x/y per frame; keep boxes
                         # this far inside the max-zoom window
# (2) minimum crop size: v1's escalation rung (rect(0), the bare padded text
# region) had NO floor and accepted 65x62 (p0068) and 160x160 (p0117) crops —
# useless punch-ins that isolate fragments (irrelevant_panel fault class).
# Two floors, both must pass or the crop is REJECTED:
#   - MIN_CROP_PX absolute on each dim: 200px of a 690px-wide webtoon source
#     is already a 9.6x upscale at 1920; smaller is pixel mush AND a fragment.
#   - area >= MIN_CROP_AREA_FRAC * min(pw,ph)^2: panel-relative, measured
#     against the SQUARE of the shorter side (the reading-axis width) rather
#     than full panel area — 25% of full area on a 1:3 webtoon strip would
#     demand ~the whole panel and kill every legitimate info-card crop
#     (the scene-5 win is 12.6% of its 690x1840 panel but 135% of width^2/4).
#     Both v1 degenerates fail it; all legitimate v1 crops pass.
MIN_CROP_PX = 200
MIN_CROP_AREA_FRAC = 0.25
#   - MIN_TEXT_COVER: the target text must cover at least this fraction of
#     the crop area. A punch-in justified by one micro-word (p0032: a lone
#     24x23 box -> 0.4% of its 379^2 crop) frames mostly art/black space —
#     the v1 "partial SFX + black space" / irrelevant-fragment class. Real
#     dialogue crops measure 1.2-15% on the v1 log; 1% keeps them all.
MIN_TEXT_COVER = 0.01
# a candidate "container" blob spanning more than this fraction of the panel
# in BOTH dims is the page background, not a bubble
BUBBLE_MAX_PANEL_FRAC = 0.8
# (3) watermark-aware: crops re-exposed/magnified recorded watermark regions
# (v1 watermark faults scenes 0/1/22). Simplest safe rule (user-approved):
# if clean_watermarks recorded a watermark instance on the panel and we are
# NOT rendering a cleaned copy (panels_clean/<name>), skip the crop entirely.


def _load_watermark_panels(project_dir):
    """exp-009 v2 guard (3): {panel_name: bbox} for every panel where
    clean_watermarks.py recorded a watermark instance (method != None) in
    watermark_log.json. Missing/broken log -> {} (guard inert, crops allowed
    — same trust level as v1)."""
    try:
        log = json.loads((Path(project_dir) / "watermark_log.json").read_text())
        return {r["panel"]: r.get("bbox") for r in log.get("panels", [])
                if r.get("method")}
    except Exception:
        return {}


def _motion_violations(cand, rects, zmax):
    """exp-009 v2 guard (1): rects (text boxes and/or bubble extents,
    [x, y, w, h]) that intersect the crop but do NOT fit inside the max-zoom
    visible window. The zoompan is center-anchored, so the intersection of
    the visible windows over the whole motion path is the centered
    (cw/zmax, ch/zmax) sub-rect of the crop (inset by MOTION_JITTER for the
    per-frame x/y rounding). Anything intersecting the crop but poking out
    of that window WILL be cut on some frame. Rects fully outside the crop
    never render at all, so they are exempt (same drop rule as v1)."""
    cx, cy, cw, ch = cand
    ccx, ccy = cx + cw / 2.0, cy + ch / 2.0
    hx = cw / (2.0 * zmax) - MOTION_JITTER
    hy = ch / (2.0 * zmax) - MOTION_JITTER
    bad = []
    for b in rects:
        bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        if bx1 <= cx or bx0 >= cx + cw or by1 <= cy or by0 >= cy + ch:
            continue   # fully outside the crop: never on screen
        if (bx0 < ccx - hx or bx1 > ccx + hx
                or by0 < ccy - hy or by1 > ccy + hy):
            bad.append(b)
    return bad


def _bubble_extents(panel, clusters, pw, ph):
    """exp-009 v2 guard (1b): estimated FULL bubble/box outline rect per text
    cluster, [x, y, w, h] or None when no container blob is found. DBNet
    boxes cover only the text LINES; the drawn bubble around them extends
    further, and cutting THAT outline is the reviewer-visible "half-cut
    bubble" (v1 scene 20 t=628.5: all line boxes contained, bubble sliced).
    Estimate: threshold for bright(>200)/dark(<90) container blobs (the
    clean_bubbles.py split) and take the connected component under the
    cluster; keep it only when it plausibly IS a container (covers >=30% of
    the cluster rect, not a near-page-sized background blob). Any failure ->
    None for that cluster (falls back to text-box-only containment — v1
    behavior, never worse)."""
    try:
        import cv2
        import numpy as np
        gray = cv2.cvtColor(cv2.imread(str(panel)), cv2.COLOR_BGR2GRAY)
    except Exception:
        return [None] * len(clusters)
    out = []
    for cl in clusters:
        x0 = max(min(b[0] for b in cl), 0)
        y0 = max(min(b[1] for b in cl), 0)
        x1 = min(max(b[0] + b[2] for b in cl), pw)
        y1 = min(max(b[1] + b[3] for b in cl), ph)
        best = None
        try:
            for dark in (False, True):
                if dark:      # black caption box, white text
                    _, m = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
                else:         # white bubble, black text
                    _, m = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
                m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
                n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
                roi = lab[y0:y1, x0:x1]
                vals, counts = np.unique(roi[roi > 0], return_counts=True)
                if len(vals) == 0:
                    continue
                c = int(vals[np.argmax(counts)])
                if counts.max() / max(roi.size, 1) < 0.3:
                    continue   # blob doesn't really hold this text
                bx, by, bw, bh, area = stats[c]
                if (bw > BUBBLE_MAX_PANEL_FRAC * pw
                        and bh > BUBBLE_MAX_PANEL_FRAC * ph):
                    continue   # page background, not a bubble
                r = [int(bx), int(by), int(bw), int(bh)]
                if best is None or r[2] * r[3] > best[2] * best[3]:
                    best = r
        except Exception:
            best = None
        out.append(best)
    return out


def _cluster_boxes(boxes, pad):
    """Group line boxes into reading units (bubble/info-box blocks): two
    boxes whose pad-expanded rects overlap belong to the same cluster —
    the same block-merge heuristic manga text tools use (research §3a).
    Returns a list of clusters (lists of boxes)."""
    clusters = [[b] for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                hit = False
                for a in clusters[i]:
                    for b in clusters[j]:
                        if (a[0] - pad < b[0] + b[2] + pad
                                and b[0] - pad < a[0] + a[2] + pad
                                and a[1] - pad < b[1] + b[3] + pad
                                and b[1] - pad < a[1] + a[3] + pad):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    clusters[i] += clusters.pop(j)
                    changed = True
                    break
            if changed:
                break
    return clusters


def _text_aware_crop(panel, boxes, w, h, zmax=MOTION_ZMAX_EMPH,
                     reject_log=None):
    """(cx, cy, cw, ch) crop rect in panel px, or None when no action is
    needed/possible.

    exp-009 v2: `zmax` is the peak Ken Burns zoom the render call will apply
    (1.08 plain / 1.16 emphasis; default = the conservative max). A candidate
    crop must keep every intersecting box inside the max-zoom visible window
    — the guaranteed-visible intersection of the whole motion path — else it
    is repaired (grown by zmax, one attempt) or REJECTED. Crops below the
    minimum-size floor are REJECTED. `reject_log(reason)` (optional callable)
    receives a human-readable reason when a wanted crop is rejected, so the
    render log shows WHY a small-text panel stayed uncropped.

    Framing rule (research §3a): boxes cluster into reading units; the crop
    frames the cluster with the most sub-threshold lines (the text that needs
    help), padded TEXT_PAD beyond every edge and floored at TEXT_CROP_FLOOR
    of the panel's smaller dimension. Never crops THROUGH a box: any box the
    rect partially intersects is absorbed by growing the rect (gap-009
    guard: no half-cut bubbles); boxes fully outside simply drop, which is
    legal because the framed cluster keeps text on screen. If the floored
    crop still can't reach MIN_TEXT_H, escalate to the bare padded cluster
    (full-frame the text region, research §3c)."""
    if not boxes:
        return None
    try:
        from PIL import Image
        with Image.open(panel) as im:
            pw, ph = im.size
    except Exception:
        return None
    # clamp: DBNet occasionally reports a box 1-2px past the panel edge; an
    # unclamped box could never be contained by any legal crop
    boxes = [[max(b[0], 0), max(b[1], 0),
              min(b[0] + b[2], pw) - max(b[0], 0),
              min(b[1] + b[3], ph) - max(b[1], 0)] for b in boxes]
    boxes = [b for b in boxes if b[2] > 0 and b[3] > 0]
    if not boxes:
        return None
    hs = sorted(b[3] for b in boxes)
    med_all = hs[len(hs) // 2]
    min_px = MIN_TEXT_H_1080 * h / 1080.0
    # exp-009 v4: TRIGGER gate. v1-v3 triggered below the 40px COMFORT
    # target, which cropped panels whose text was already legible (p0126:
    # 36px median -> flagged as a pointless reframe in the round4 eval).
    # A crop now fires only when the default framing renders the median
    # line genuinely UNREADABLE (< TEXT_TRIGGER_H_1080); once triggered,
    # the crop still sizes toward the 40px comfort target below.
    trig_px = TEXT_TRIGGER_H_1080 * h / 1080.0
    fit0 = min(w / pw, h / ph)
    if med_all * fit0 >= trig_px:
        return None   # already readable under the plain blur_bg fit
    clusters = _cluster_boxes(boxes, TEXT_PAD)
    # target = the cluster with the most lines that render sub-TRIGGER
    # today (the text that genuinely needs help); ties break toward more
    # lines then larger area (a 5-line info card beats a single loose word)
    def _need(cl):
        return sum(1 for b in cl if b[3] * fit0 < trig_px)
    target = max(clusters, key=lambda cl: (
        _need(cl), len(cl), sum(b[2] * b[3] for b in cl)))
    if _need(target) == 0:
        return None   # every small line is noise-scale; nothing to frame
    t_hs = sorted(b[3] for b in target)
    med_t = t_hs[len(t_hs) // 2]

    def rect(floor_px):
        cx0 = min(b[0] for b in target) - TEXT_PAD
        cy0 = min(b[1] for b in target) - TEXT_PAD
        cx1 = max(b[0] + b[2] for b in target) + TEXT_PAD
        cy1 = max(b[1] + b[3] for b in target) + TEXT_PAD
        if cx1 - cx0 < floor_px:            # grow centered to the floor
            cc = (cx0 + cx1) / 2.0
            cx0, cx1 = cc - floor_px / 2.0, cc + floor_px / 2.0
        if cy1 - cy0 < floor_px:
            cc = (cy0 + cy1) / 2.0
            cy0, cy1 = cc - floor_px / 2.0, cc + floor_px / 2.0
        # shift inside the panel bounds
        if cx0 < 0:
            cx1 -= cx0; cx0 = 0
        if cy0 < 0:
            cy1 -= cy0; cy0 = 0
        if cx1 > pw:
            cx0 -= (cx1 - pw); cx1 = pw
        if cy1 > ph:
            cy0 -= (cy1 - ph); cy1 = ph
        cx0, cy0 = max(cx0, 0), max(cy0, 0)
        cx1, cy1 = min(cx1, pw), min(cy1, ph)
        # containment repair: absorb any box the rect PARTIALLY intersects
        # (grow, never shrink), until stable — a half-visible bubble is the
        # exact fault this feature exists to prevent
        for _ in range(len(boxes) + 1):
            grew = False
            for b in boxes:
                bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
                if bx1 <= cx0 or bx0 >= cx1 or by1 <= cy0 or by0 >= cy1:
                    continue          # fully outside: allowed to drop
                nx0 = min(cx0, max(bx0 - TEXT_PAD, 0))
                ny0 = min(cy0, max(by0 - TEXT_PAD, 0))
                nx1 = max(cx1, min(bx1 + TEXT_PAD, pw))
                ny1 = max(cy1, min(by1 + TEXT_PAD, ph))
                if (nx0, ny0, nx1, ny1) != (cx0, cy0, cx1, cy1):
                    cx0, cy0, cx1, cy1 = nx0, ny0, nx1, ny1
                    grew = True
            if not grew:
                break
        return (int(cx0), int(cy0), int(cx1 - cx0), int(cy1 - cy0))

    cand = rect(TEXT_CROP_FLOOR * min(pw, ph))
    if med_t * min(w / cand[2], h / cand[3]) < min_px:
        # the floored crop still renders the text too small -> full-frame the
        # text region itself (best effort: dense tiny text may stay short of
        # MIN_TEXT_H even here; that's the research's accepted residual)
        cand = rect(0)
    if cand[2] >= 0.98 * pw and cand[3] >= 0.98 * ph:
        return None   # crop ~ whole panel: no point re-encoding the same fit

    def _reject(reason):
        if reject_log:
            reject_log(f"{reason} — crop rejected, default framing kept")
        return None

    # exp-009 v2 guard (2): minimum crop size. Absolute floor kills pixel-mush
    # fragment punch-ins (v1 accepted 65x62 / 160x160); the panel-relative
    # floor (vs the shorter side squared — the reading-axis width, see the
    # constant comment) kills "large enough in px but a sliver of the art"
    # crops on small panels.
    if min(cand[2], cand[3]) < MIN_CROP_PX:
        return _reject(f"min-size {cand[2]}x{cand[3]} < {MIN_CROP_PX}px")
    if cand[2] * cand[3] < MIN_CROP_AREA_FRAC * min(pw, ph) ** 2:
        return _reject(f"min-area {cand[2]}x{cand[3]} < "
                       f"{MIN_CROP_AREA_FRAC:.0%} of {min(pw, ph)}^2")
    t_area = sum(b[2] * b[3] for b in target)
    if t_area < MIN_TEXT_COVER * cand[2] * cand[3]:
        return _reject(f"text-cover {t_area / (cand[2] * cand[3]):.1%} < "
                       f"{MIN_TEXT_COVER:.0%} (punch-in would frame mostly "
                       f"non-text)")

    # exp-009 v2 guard (1): containment under MOTION. v1 verified boxes
    # against the static crop; the Ken Burns zoom then shrank the visible
    # window past the boxes and cut bubbles mid-motion (report: scene 20
    # t=628.5 et al). The zoompan is center-anchored, so the guaranteed-
    # visible region across the whole motion is the centered (cw/zmax,
    # ch/zmax) window; anything intersecting the crop but outside it WILL be
    # cut on some frame -> reject (approved direction: never render a
    # maybe-cut). Checked rects = the text LINE boxes plus the estimated
    # FULL bubble outline per cluster (v1's frame evidence shows the cut
    # happens on the drawn bubble, which extends past the line boxes).
    contain = list(boxes)
    for ext in _bubble_extents(panel, clusters, pw, ph):
        if ext:
            contain.append(ext)
    bad = _motion_violations(cand, contain, zmax)
    if bad:
        # one deterministic repair: scale the rect about its center by zmax
        # (plus the jitter margin) so the max-zoom window equals the old
        # static rect, then clamp to the panel. If the panel edge clips the
        # growth (center shifts, margin lost) the re-check below rejects —
        # we never render a maybe-cut bubble.
        gw = cand[2] * zmax + 2 * MOTION_JITTER
        gh = cand[3] * zmax + 2 * MOTION_JITTER
        gx = cand[0] + cand[2] / 2.0 - gw / 2.0
        gy = cand[1] + cand[3] / 2.0 - gh / 2.0
        if gx < 0:
            gx = 0
        if gy < 0:
            gy = 0
        if gx + gw > pw:
            gx = pw - gw
        if gy + gh > ph:
            gy = ph - gh
        if gx < 0 or gy < 0:
            return _reject(f"containment-under-motion (zmax {zmax}): "
                           f"crop + zoom margin exceeds the panel")
        cand = (int(gx), int(gy), int(gw), int(gh))
        if cand[2] >= 0.98 * pw and cand[3] >= 0.98 * ph:
            return None   # grew to ~whole panel: default framing already does this
        bad = _motion_violations(cand, contain, zmax)
        if bad:
            return _reject(f"containment-under-motion (zmax {zmax}): "
                           f"{len(bad)} box(es) exit the rendered window "
                           f"even after zoom-margin growth, e.g. {bad[0]}")
    return cand


def _ensure_text_boxes(script_path, project_dir, scenes):
    """Load (generating if stale/missing) the <slug>.textboxes.json sidecar
    for every panel the script references. Returns {panel_name: boxes} or
    None on ANY failure — the caller treats None as 'flag off' so a detector
    problem can never take the render down (graceful-fallback rule)."""
    try:
        import text_boxes as tb
        seen, paths = set(), []
        for sc in scenes:
            for rel in sc.get("panels") or ([sc["panel"]] if sc.get("panel")
                                            else []):
                p = _resolve_panel(project_dir, rel)
                if p.name not in seen and Path(p).exists():
                    seen.add(p.name)
                    paths.append(p)
        if not paths:
            return None
        sidecar = script_path.with_suffix(".textboxes.json")
        return tb.ensure_boxes(sidecar, paths)
    except Exception as e:
        print(f"WARNING: text-aware framing disabled — text box detection "
              f"failed ({type(e).__name__}: {e})")
        return None


# broken-panel guard: segmentation sometimes emits fragments (a 145x187 crop
# of a full page, a sliver of floating text). A panel is unusable when its
# short side is tiny OR its total area is too small to survive delivery
# resolution. Wide-but-short webtoon banner slices (e.g. 690x240) stay valid.
MIN_PANEL_DIM = 180
MIN_PANEL_AREA = 120_000   # px^2 (~690x175)


def _panel_ok(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            return min(w, h) >= MIN_PANEL_DIM and w * h >= MIN_PANEL_AREA
    except Exception:
        return False


def _nearest_usable_panel(project_dir, bad_path):
    """The healthy panel numerically closest to a broken one (p0006 -> p0005
    or p0007), searching panels/ by |number difference|. None if nothing."""
    m = re.search(r"p(\d+)", Path(bad_path).name)
    if not m:
        return None
    n = int(m.group(1))
    cands = []
    for p in (project_dir / "panels").glob("p*.png"):
        pm = re.search(r"p(\d+)", p.name)
        if pm and int(pm.group(1)) != n:
            cands.append((abs(int(pm.group(1)) - n), p))
    for _, p in sorted(cands):
        q = _resolve_panel(project_dir, str(p))
        if _panel_ok(q):
            return q
    return None


def _resolve_panel(project_dir, rel):
    """Map a scene panel path to disk, preferring a cleaned version."""
    rel = rel.replace("\\", "/")
    name = Path(rel).name
    clean = project_dir / "panels_clean" / name
    if clean.exists():
        return clean
    p = (project_dir / rel)
    if p.exists():
        return p
    # try panels/<name> if the stored path was bare
    alt = project_dir / "panels" / name
    return alt if alt.exists() else p


def render_panel_scene(panel, audio, out_path, w, h, duration, motion_idx=0,
                       blur_bg=True, emphasis=False, tight_crop=False,
                       focus=False, glitch=False, crop_rect=None,
                       frame_plan=None):
    """Single real panel + narration -> clip with blurred-bg letterbox + Ken Burns.

    mv.render_scene crops-to-fill, which clips tall webtoon panels. Instead we
    fit the FULL panel over a blurred, darkened copy of itself (the technique
    from faceless-studio/make_shorts.py), then apply a zoompan Ken Burns move on
    the composite. Reuses mv.FPS / mv.PAD / mv._INTER_V / mv._INTER_A / mv.run.

    emphasis=True gives the panel a stronger, faster punch-in (it visibly
    "hits") for important beats. tight_crop=True fits the panel to a larger
    focal window (fills more of the frame) while keeping a safe margin so faces
    are not lost — a conservative middle ground vs the old crop-to-fill.

    crop_rect=(x, y, w, h) in PANEL pixels (exp-009 --text-aware): a data-
    driven crop of the fit window framing the panel's text region, applied
    before the decrease scale so small in-panel text renders phone-legible.
    Takes precedence over tight_crop (both are crops of the same window; the
    data-driven one knows where the text actually is). None = unchanged.

    frame_plan (exp-014 --kenburns smart): a kb_smart.plan_frame dict —
    "anchor" keeps today's composite/zoom but shifts the zoompan y anchor so
    the max-zoom window contains the panel's text/face boxes; "scroll"
    replaces the zoom with a webtoon-native top->bottom pan of a
    fit-to-width window on the supersampled composite (the only sanctioned
    pan; sideways pans stay removed); "contain" (v2) holds the full
    letterboxed panel static (z=1) when the text union is provably
    infeasible for any legal moving window. None = today's path,
    byte-identical — the flag-off guarantee lives on this default.
    """
    frames = max(int(round(duration * mv.FPS)), 1)
    ss_w, ss_h = w * 2, h * 2
    # Motion: gentle center-anchored zoom only. Horizontal pan modes were
    # removed (the sideways slide reads as distracting on manga panels). x/y stay
    # locked to the panel center so nothing drifts out of frame.
    # emphasis -> a punchier move: start already zoomed and ease back, so the
    # panel lands with impact instead of drifting in.
    if emphasis:
        # quick punch: start ~1.16 and settle toward 1.02 over the clip
        z = "if(eq(on,0),1.16,max(zoom-0.0022,1.02))"
    else:
        kind = motion_idx % 2
        if kind == 0:      # slow zoom in
            z = "min(zoom+0.0005,1.08)"
        else:              # slow zoom out
            z = "if(eq(on,0),1.08,max(zoom-0.0005,1.0))"
    x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if frame_plan and frame_plan.get("mode") == "contain" and blur_bg \
            and not crop_rect:
        # exp-014 v2 "contain": infeasible-geometry tall panel (text union
        # too large for ANY legal zoom/scroll window — kb_smart proved the
        # default center zoom would cut text). Same composite, zoompan as a
        # pure frame duplicator (z=1): the full letterboxed panel is static
        # on screen, so every text box is visible at every t by
        # construction. Trading the gentle zoom for guaranteed-complete
        # text on these panels is the v2 fix for the chapter-wide gate.
        z = "1"
    elif frame_plan and frame_plan.get("mode") == "anchor" and blur_bg \
            and not crop_rect:
        # exp-014 "anchor": same composite, same zoom envelope, but the
        # window centers on cy (fraction of composite height) instead of
        # 0.5, clamped inside the frame. At z=1 the clamp forces center
        # (the window IS the frame); as z grows the window releases toward
        # cy — every intermediate window still contains the max-zoom
        # window at cy (proof: window shrinks monotonically around a
        # clamped path), so kb_smart's containment check covers all t.
        cy = frame_plan["cy"]
        # commas are safe unescaped here: the whole expression is single-
        # quoted inside the filtergraph (same as the z expressions above)
        y = f"max(0,min(ih*{cy:.6f}-(ih/zoom/2),ih-ih/zoom))"

    # post-zoompan effect suffix:
    #  - glitch (emphasis hit): 2-frame RGB split + white additive flash on the
    #    first ~2 frames, synced to the impact SFX that lands there.
    #  - focus: soft black radial vignette so the panel edges darken (draws the
    #    eye to the center for a quiet monologue/shock beat).
    two_frames = 2.0 / mv.FPS
    fx = ""
    if glitch:
        # keep the RGB split but a much gentler flash: +0.25 blew out
        # mostly-white manga pages into a full-white frame at scene starts.
        fx += (f",rgbashift=rh=4:bh=-4:enable='lt(t,{two_frames:.3f})',"
               f"eq=brightness='if(lt(t,{two_frames:.3f}),0.08,0)'")
    if focus:
        fx += ",vignette=angle=PI/4"

    if blur_bg and frame_plan and frame_plan.get("mode") == "scroll" \
            and not crop_rect:
        # exp-014 "scroll": webtoon-native top->bottom pan for h/w > 2.5
        # panels. The fg is scaled to the plan's supersampled width and a
        # frame-height window crops it with a smoothstep-eased y sweep from
        # y0 to y1 (kb_smart guarantees every text box stays inside the
        # window at every t; speed is capped there). The crop runs at 2x
        # supersample and the composite downscales to the frame, so the
        # integer crop-y steps land at half-pixel after downscale — the
        # same anti-judder trick the zoompan paths use. No zoom rides on
        # top: the scroll IS the motion (a zoom would shrink the window and
        # break the containment guarantee).
        fw, y0, y1 = frame_plan["fw"], frame_plan["y0"], frame_plan["y1"]
        pw, ph = frame_plan["pw"], frame_plan["ph"]
        d = max(duration, 1e-3)
        # wall-time guard (gap-006 <=1.2x bound), two tricks:
        # (a) the scroll only ever shows the [y0, y1+ss_h] band of the
        #     scaled fg, so pre-crop the SOURCE panel to that band (+4px
        #     slack for rounding) before the expensive supersample scale —
        #     a 690x2697 panel would otherwise scale to 3840x15010;
        # (b) the input is read WITHOUT -loop and each branch is expanded
        #     to `frames` by zoompan z=1 (a pure frame duplicator), so the
        #     scale/gblur chains run ONCE instead of per frame — the same
        #     reason the default zoompan path is cheap. Only the y-sweep
        #     crop and the overlay run per frame.
        g = fw / float(pw)
        py0 = max(int(y0 / g) - 4, 0)
        py1 = min(int((y1 + ss_h) / g + 1) + 4, ph)
        band_h = int(round((py1 - py0) * g / 2.0)) * 2   # even, exact scale
        off = py0 * g
        sy0, sy1 = y0 - off, y1 - off
        # clamp the sweep inside the scaled band (rounding slack)
        sy1 = min(sy1, band_h - ss_h)
        sy0 = max(min(sy0, sy1), 0)
        st = f"min(t/{d:.4f},1)"
        y_expr = f"{sy0:.1f}+({sy1 - sy0:.1f})*pow({st},2)*(3-2*{st})"
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,"
            f"crop={ss_w}:{ss_h},gblur=sigma=24,eq=brightness=-0.14,"
            f"zoompan=z='1':d={frames}:s={ss_w}x{ss_h}:fps={mv.FPS}[bgb];"
            f"[fg]crop={pw}:{py1 - py0}:0:{py0},scale={fw}:{band_h},"
            f"zoompan=z='1':d={frames}:s={fw}x{band_h}:fps={mv.FPS},"
            f"crop={fw}:{ss_h}:0:'{y_expr}'[fgc];"
            f"[bgb][fgc]overlay=(W-w)/2:(H-h)/2,"
            f"scale={w}:{h}"
            f"{fx},format=yuv420p[v]"
        )
        cmd = ["ffmpeg", "-y", "-i", str(panel), "-i", str(audio),
               "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
               "-af", "apad", "-t", f"{duration:.3f}",
               *mv._INTER_V, *mv._INTER_A, str(out_path)]
        mv.run(cmd)
        return

    if blur_bg:
        # blurred/darkened fill + full panel fitted centered, supersampled for
        # smooth sub-pixel zoompan, then Ken Burns on the composite.
        # tight_crop: scale the foreground with force_original_aspect_ratio=
        # increase to a slightly-smaller focal box (0.9x the frame) then
        # center-crop it, so tall panels fill more of the frame. The 0.9 factor
        # keeps a safe margin vs the old edge-to-edge crop.
        if crop_rect:
            # text-aware framing: crop the panel to the computed text window
            # (panel px, pre-scale) so the same decrease fit now spends the
            # frame on the region that must stay legible.
            cx, cy, cw, ch = crop_rect
            fg = (f"[fg]crop={cw}:{ch}:{cx}:{cy},"
                  f"scale={ss_w}:{ss_h}:force_original_aspect_ratio=decrease[fgc]")
        elif tight_crop:
            fg = (f"[fg]scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,"
                  f"crop=iw*0.9:ih*0.9:(iw-iw*0.9)/2:(ih-ih*0.9)*0.35,"
                  f"scale={ss_w}:{ss_h}:force_original_aspect_ratio=decrease[fgc]")
        else:
            fg = (f"[fg]scale={ss_w}:{ss_h}:"
                  f"force_original_aspect_ratio=decrease[fgc]")
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,"
            f"crop={ss_w}:{ss_h},gblur=sigma=24,eq=brightness=-0.14[bgb];"
            f"{fg};"
            f"[bgb][fgc]overlay=(W-w)/2:(H-h)/2,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={mv.FPS}"
            f"{fx},format=yuv420p[v]"
        )
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(panel), "-i", str(audio),
               "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
               "-af", "apad", "-t", f"{duration:.3f}",
               *mv._INTER_V, *mv._INTER_A, str(out_path)]
        mv.run(cmd)
    else:
        # crop-to-fill Ken Burns (same as mv.render_scene)
        mv.render_scene(panel, audio, out_path, w, h, duration, motion_idx)


def render_parallax_scene(panel, fg_png, audio, out_path, w, h, duration,
                          motion_idx=0):
    """2.5D parallax: original panel as background, the alpha foreground cutout
    on top. CENTERED and calm — no horizontal/vertical pans (those read as
    shaking on busy manga art). Depth comes only from a gentle differential
    zoom: the foreground eases in slightly faster than the background, so the
    character subtly separates from the plate. Everything stays anchored to
    center. Caller falls back to render_panel_scene when there is no cutout."""
    frames = max(int(round(duration * mv.FPS)), 1)
    ss_w, ss_h = w * 2, h * 2   # supersample for smooth sub-pixel zoom
    cxy = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"   # locked to center
    # background: blurred/darkened fill, very slow centered zoom (1.0 -> ~1.04)
    bg = (f"[0:v]scale={ss_w}:{ss_h}:force_original_aspect_ratio=increase,"
          f"crop={ss_w}:{ss_h},gblur=sigma=20,eq=brightness=-0.12,"
          f"zoompan=z='min(1.0+0.00035*on,1.04)':{cxy}"
          f":d={frames}:s={w}x{h}:fps={mv.FPS}[bg]")
    # foreground cutout: fit, gentle centered zoom a touch faster (1.0 -> ~1.07)
    # so it lifts off the background for depth — no lateral/vertical drift.
    fg = (f"[1:v]scale={ss_w}:{ss_h}:force_original_aspect_ratio=decrease,"
          f"zoompan=z='min(1.0+0.0006*on,1.07)':{cxy}"
          f":d={frames}:s={w}x{h}:fps={mv.FPS}[fg]")
    vf = f"{bg};{fg};[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(panel),
           "-loop", "1", "-i", str(fg_png), "-i", str(audio),
           "-filter_complex", vf, "-map", "[v]", "-map", "2:a",
           "-af", "apad", "-t", f"{duration:.3f}",
           *mv._INTER_V, *mv._INTER_A, str(out_path)]
    mv.run(cmd)


def render(script_path, args):
    script_path = Path(script_path).expanduser()
    data = json.loads(script_path.read_text())
    scenes = data["scenes"]
    voice = data.get("voice", "en-US-AndrewMultilingualNeural")
    style = data.get("style_suffix", "")
    tts_rate = data.get("tts_rate", "+0%")
    tts_pitch = data.get("tts_pitch", "+0Hz")
    vertical = data.get("vertical", False)  # v1 = horizontal only
    w, h = (mv.VERT_PRESETS if vertical else mv.RES_PRESETS)[args.res]
    redo = set(args.redo_scene or [])

    project_dir = script_path.parent
    out_path = project_dir / (script_path.stem + ".mp4")
    if args.workdir:
        workdir = Path(args.workdir).resolve()
    else:
        workdir = Path(tempfile.mkdtemp(prefix="fmanga_"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"Rendering {len(scenes)} scenes -> {out_path}  (workdir {workdir})")

    # exp-009 text-aware framing: measured text-line boxes per panel, from
    # the <slug>.textboxes.json sidecar (generated here on first run). OFF by
    # default; None (flag off or detection failed) leaves every code path
    # below byte-identical to today.
    # exp-014 content-aware Ken Burns (--kenburns smart / MANGA_KB_SMART=1):
    # per-panel frame plans from kb_smart (OCR text boxes + anime-face boxes,
    # both sidecar-cached). OFF by default; any failure below downgrades to
    # None -> every render call keeps the center-anchored path byte-identical
    # (same graceful-fallback rule as --text-aware).
    kb_on = (getattr(args, "kenburns", "center") == "smart"
             or os.environ.get("MANGA_KB_SMART") == "1")
    kb_tboxes, kb_faces, kbs = None, {}, None
    kb_log = []   # every framing decision, dumped to <workdir>/kb_plans.json
                  # for the exp-014 deterministic containment gate
    if kb_on:
        print("Content-aware Ken Burns (--kenburns smart): loading boxes...")
        kb_tboxes = _ensure_text_boxes(script_path, project_dir, scenes)
        if kb_tboxes is None:
            print("WARNING: --kenburns smart disabled (text boxes "
                  "unavailable); center-anchored framing kept")
            kb_on = False
        else:
            try:
                import kb_smart as kbs
            except Exception as e:
                print(f"WARNING: --kenburns smart disabled "
                      f"({type(e).__name__}: {e})")
                kb_on, kbs = False, None
        if kb_on:
            # anime-face boxes (deepghs/anime_face_detection, MIT — chosen
            # over manga109's face class by the exp-014 smoke: 0 recall on
            # colored webtoons). Failure -> text-only planning, which still
            # fixes the bubble-graze sites.
            try:
                seen, fpaths = set(), []
                for sc in scenes:
                    for rel in sc.get("panels") or (
                            [sc["panel"]] if sc.get("panel") else []):
                        p = _resolve_panel(project_dir, rel)
                        if p.name not in seen and Path(p).exists():
                            seen.add(p.name)
                            fpaths.append(p)
                kb_faces = kbs.ensure_face_boxes(
                    script_path.with_suffix(".faceboxes.json"), fpaths)
            except Exception as e:
                print(f"WARNING: face detection failed ({type(e).__name__}: "
                      f"{e}); --kenburns smart continues text-only")
                kb_faces = {}

    tboxes = None
    wm_panels = {}
    if getattr(args, "text_aware", False):
        print("Text-aware framing: loading/detecting panel text boxes...")
        tboxes = _ensure_text_boxes(script_path, project_dir, scenes)
        if tboxes is not None:
            n = sum(1 for b in tboxes.values() if b)
            print(f"  text boxes ready: {n}/{len(tboxes)} panels carry text")
        # exp-009 v2 guard (3): panels with a recorded watermark instance.
        # A text-aware crop must never re-frame onto a watermark the default
        # framing kept small/off-center (v1 watermark 4->6 veto).
        wm_panels = _load_watermark_panels(project_dir)
        if wm_panels:
            print(f"  watermark guard: {len(wm_panels)} panel(s) carry a "
                  f"recorded watermark (crop skipped unless cleaned)")

    clips, durations, word_times = [], [], []
    beat_windows = []   # absolute [Pause] beat-drop windows (pre-intro-offset)
    for i, scene in enumerate(scenes):
        audio = workdir / f"a{i:03}.mp3"
        clip = workdir / f"c{i:03}.mp4"
        wt_path = workdir / f"w{i:03}.json"
        force = (i + 1) in redo
        if force:
            for p in [clip, audio, wt_path]:
                Path(p).unlink(missing_ok=True)
        if clip.exists() and clip.stat().st_size > 0 and not force:
            print(f"[{i + 1}/{len(scenes)}] (cached)")
            clips.append(clip)
            durations.append(mv.ffprobe_duration(clip))
            word_times.append(json.loads(wt_path.read_text()) if wt_path.exists() else None)
            continue
        print(f"[{i + 1}/{len(scenes)}] {scene['narration'][:60]}...")
        # [Pause] markers (or scene beat_drop) create a protected held silence
        # for a music beat-drop; otherwise this is a plain TTS.
        wt, pause_windows = _tts_with_pauses(
            scene["narration"], voice, audio, workdir, f"a{i:03}",
            rate=tts_rate, pitch=tts_pitch)
        if not args.no_truncate_silence:
            # hard-cut internal pauses >150ms for a relentless delivery, and
            # remap word timings so captions stay aligned. Protected [Pause]
            # windows are preserved.
            new_audio, wt, pause_windows = _truncate_silence(
                audio, wt, workdir, f"a{i:03}", protected=pause_windows)
            if new_audio != audio:
                shutil.copy(new_audio, audio)
        wt_path.write_text(json.dumps(wt))
        # record beat-drop windows in ABSOLUTE timeline seconds (scene start +
        # window offset); scene start is the running sum of prior durations.
        scene_start = sum(durations)
        for p0, p1 in pause_windows:
            beat_windows.append((scene_start + p0, scene_start + p1))
        dur = mv.ffprobe_duration(audio) + mv.PAD
        # ENDING TAIL: hold the final scene ~2.5s past the last narrated word
        # (music resolves, end-screen room) instead of cutting to black 0.1s
        # after the closing line.
        if i == len(scenes) - 1:
            dur += END_TAIL_SEC

        panel_list = scene.get("panels") or (
            [scene["panel"]] if scene.get("panel") else [])
        # Default: ALWAYS fit the full panel over a blurred background so tall
        # manga panels are never cropped (faces stay in frame). Per-scene
        # blur_bg=false (crop-to-fill) is only honored with --allow-crop.
        blur = True if not args.allow_crop else scene.get("blur_bg", True)
        # emphasis beat -> punch-in (+ impact SFX later); tight_crop is a global
        # flag but a scene may opt out with "tight": false.
        emph = bool(scene.get("emphasis"))
        tight = args.tight_crop and scene.get("tight", True)
        foc = bool(scene.get("focus"))
        # glitch (RGB split + flash) rides on emphasis beats unless disabled
        gl = emph and not args.no_glitch
        if panel_list:
            resolved = [_resolve_panel(project_dir, p) for p in panel_list]
            resolved = [p for p in resolved if Path(p).exists()]
            if not resolved:
                sys.exit(f"scene {i+1}: none of its panels exist: {panel_list}")
            # replace broken/tiny segmentation fragments with the nearest
            # healthy panel so the visuals stay on the narrated moment
            fixed = []
            for p in resolved:
                if _panel_ok(p):
                    fixed.append(p)
                    continue
                sub = _nearest_usable_panel(project_dir, p)
                if sub:
                    print(f"  scene {i+1}: panel {Path(p).name} too small, "
                          f"using {Path(sub).name} instead")
                    fixed.append(sub)
                else:
                    print(f"  scene {i+1}: panel {Path(p).name} too small, "
                          f"dropped")
            resolved = fixed or resolved
            # NARRATION-DRIVEN CADENCE: the cut clock follows the scene's
            # emotional register. Explicit "pace" wins; otherwise emphasis
            # scenes read as hype, focus scenes as quiet, the rest normal.
            pace = scene.get("pace")
            if pace not in PACE_CUT_SEC:
                pace = "hype" if emph else ("quiet" if foc else "normal")
            cut_sec = PACE_CUT_SEC[pace]
            # EVERY scripted panel renders — the script's panel list is a
            # narrative sequence and its tail is usually the payoff (a quiet
            # scene that dropped its last panels once cut the chapter's
            # emotional climax). If narration is shorter than panels*cut_sec,
            # per-cut time just compresses; if longer, extra cuts are added.
            n_cuts = max(len(resolved), int(round(dur / cut_sec)))
            n_cuts = max(1, min(n_cuts, max(MAX_CUTS_PER_SCENE,
                                            len(resolved))))
            # FORWARD-ONLY SCHEDULE: panels play once, in story order —
            # never wrap back to panel 0 (late narration must land on late
            # panels: the old modulo cycle put "DIE!!" over the setup shot).
            # Extra cuts beyond the list ping-pong between the LAST TWO
            # panels so long narration keeps motion without rewinding story.
            cuts = list(resolved)
            tail = resolved[-2:] if len(resolved) >= 2 else resolved
            k = 0
            while len(cuts) < n_cuts:
                cuts.append(tail[k % len(tail)])
                k += 1
            cuts = cuts[:n_cuts]
            # parallax: opt-in, applied to the scene's first panel as a single
            # depth shot (overrides the multi-cut cadence for this scene). Falls
            # back to the flat path if segmentation is unavailable/poor.
            para_done = False
            if args.parallax:
                try:
                    import cutout as _cut
                    fg = _cut.cutout(resolved[0], project_dir / "cutouts")
                    cov = _cut.alpha_coverage(fg)
                    # need a substantial, but not full-frame, foreground. 0.15
                    # floor rejects near-empty cutouts (a thin sliver looks
                    # broken); 0.9 ceiling rejects "removed nothing".
                    if 0.15 < cov < 0.90:
                        render_parallax_scene(resolved[0], fg, audio, clip,
                                              w, h, dur, motion_idx=i)
                        para_done = True
                    else:
                        print(f"  scene {i+1}: cutout coverage {cov:.2f} "
                              f"unusable, flat render")
                except Exception as e:
                    print(f"  scene {i+1}: parallax failed ({type(e).__name__}), "
                          f"flat render")
            # smart multi-panel layout (gap-002): opt-in via --layout smart.
            # The planner returns None whenever its guards fail (few panels,
            # hype pace, short scene, cells too small) and we fall through to
            # the existing sequential path — the default --layout seq never
            # reaches this block at all, so seq output stays bit-identical.
            # smart2 (exp-013): magazine-style content-weighted templates.
            # Accepts 2-panel scenes (hero/rail templates); its planner falls
            # back to the v1 grid/stack plan internally, and any error falls
            # through to the sequential path below — --layout smart behavior
            # and the default seq path are untouched.
            smart_done = False
            min_smart = 2 if args.layout == "smart2" else 3
            if (args.layout in ("smart", "smart2") and not para_done
                    and len(resolved) >= min_smart):
                try:
                    import layout_smart as ls
                    # OCR sidecar (<slug>.ocr.json, written by the script
                    # step) tells the planner which panels carry text so it
                    # can require taller cells for them. Loaded lazily ONCE;
                    # missing/broken file -> None (planner treats every panel
                    # as text-bearing, the conservative default).
                    if "_smart_ocr" not in dir():
                        _smart_ocr = None
                        _ocr_path = script_path.with_suffix(".ocr.json")
                        if _ocr_path.exists():
                            try:
                                _smart_ocr = {r["panel"]: r for r in
                                              json.loads(_ocr_path.read_text())}
                            except Exception:
                                _smart_ocr = None
                    # exp-009 v3: the PLANNER always sees text_boxes=None —
                    # the adopted binary 55/45 guard — so the smart-layout
                    # plan set is byte-identical with --text-aware on or off.
                    # v2 fed measured boxes here and the size-aware
                    # _panel_min_h rejected grids the binary guard accepts
                    # (golden scenes 1/4/9/24 lost their composites ->
                    # watermark magnified on scene 1, empty_screen on the
                    # solo pendant panel; round3 eval root cause). Measured
                    # boxes now drive ONLY the crop/punch-in decisions
                    # (_tcrop below keeps tboxes).
                    if args.layout == "smart2":
                        # exp-013 v2: content-weighted magazine templates;
                        # plan_layout_v2 already chains to v1's planner when
                        # no v2 template fits, so a v1-shaped plan can come
                        # back here and renders via the v1 renderer below.
                        import layout_smart2 as ls2
                        plan = ls2.plan_layout_v2(
                            scene, resolved, dur, wt, frame_w=w, frame_h=h,
                            pace=pace, ocr=_smart_ocr, text_boxes=None)
                    else:
                        plan = ls.plan_layout(scene, resolved, dur, wt,
                                              frame_w=w, frame_h=h, pace=pace,
                                              ocr=_smart_ocr,
                                              text_boxes=None)
                    if plan:
                        chosen = [Path(resolved[j]).name for j in plan.picked]
                        print(f"  scene {i+1}: smart layout "
                              f"({plan.template}, {len(plan.cells)} of "
                              f"{len(resolved)} panels: {', '.join(chosen)})")
                        if (args.layout == "smart2"
                                and isinstance(plan, ls2.LayoutPlanV2)):
                            ls2.render_smart_scene_v2(plan, clip, audio, dur,
                                                      workdir,
                                                      name=f"c{i:03}")
                        else:
                            ls.render_smart_scene(plan, clip, audio, dur,
                                                  workdir, name=f"c{i:03}")
                        smart_done = True
                except Exception as e:
                    print(f"  scene {i+1}: smart layout failed "
                          f"({type(e).__name__}: {e}), sequential render")
            # text-aware framing (exp-009 §3a/c): per-panel data-driven crop
            # when the blur_bg fit would render its median text line below
            # MIN_TEXT_H. Only for panels holding the screen long enough to
            # read (hysteresis) and only under --text-aware (tboxes=None
            # otherwise -> _tcrop is always None -> paths unchanged).
            def _tcrop(p, on_screen, emph_cut=False):
                if tboxes is None or blur is not True:
                    return None
                if on_screen < TEXT_MIN_ONSCREEN:
                    return None
                name = Path(p).name
                # exp-009 v2 guard (3): a panel with a recorded watermark is
                # only croppable when we render the CLEANED copy
                # (panels_clean/<name>, already preferred by _resolve_panel);
                # otherwise a tighter frame magnifies/re-exposes the mark.
                if name in wm_panels and Path(p).parent.name != "panels_clean":
                    print(f"  scene {i+1}: text-aware crop on {name} skipped "
                          f"(recorded watermark, no cleaned panel)")
                    return None
                # exp-009 v2 guard (1): containment is checked against the
                # zoom THIS cut will actually run (emphasis punches to 1.16,
                # plain zoompan peaks at 1.08).
                zmax = MOTION_ZMAX_EMPH if emph_cut else MOTION_ZMAX
                r = _text_aware_crop(
                    p, tboxes.get(name), w, h, zmax=zmax,
                    reject_log=lambda msg: print(
                        f"  scene {i+1}: text-aware crop on {name}: {msg}"))
                if r:
                    print(f"  scene {i+1}: text-aware crop on "
                          f"{name} -> {r}")
                return r

            # exp-014 content-aware Ken Burns plan for one cut. Returns None
            # (center anchor, byte-identical path) unless --kenburns smart is
            # on AND the planner finds a strictly-better legal frame. A
            # text-aware crop_rect wins when both fire (it already carries
            # its own containment guard); every decision is appended to
            # kb_log for the exp-014 deterministic containment gate.
            def _kb(p, cut_idx, cut_off, cut_dur, emph_cut=False,
                    have_crop=False):
                if not kb_on or blur is not True:
                    return None
                name = Path(p).name
                # cut_off = offset of this cut WITHIN the scene (seconds) —
                # word timings are scene-relative, so the gate maps beats to
                # cuts without re-deriving the scene timeline.
                rec = {"scene": i, "cut": cut_idx, "panel": name,
                       "panel_path": str(p),
                       "cut_off": round(cut_off, 3),
                       "dur": round(cut_dur, 3),
                       "zmax": MOTION_ZMAX_EMPH if emph_cut else MOTION_ZMAX,
                       "frame": [w, h], "plan": None}
                if have_crop:
                    rec["plan"] = {"mode": "skip-crop"}
                    kb_log.append(rec)
                    return None
                try:
                    zmax = MOTION_ZMAX_EMPH if emph_cut else MOTION_ZMAX
                    plan = kbs.plan_frame(
                        p, kb_tboxes.get(name), kb_faces.get(name), w, h,
                        cut_dur, zmax,
                        log=lambda m: print(f"  scene {i+1}: kb {name}: {m}"))
                except Exception as e:
                    print(f"  scene {i+1}: kb plan on {name} failed "
                          f"({type(e).__name__}: {e}), center anchor kept")
                    plan = None
                rec["plan"] = plan
                kb_log.append(rec)
                if plan:
                    if plan["mode"] == "scroll":
                        print(f"  scene {i+1}: kb scroll on {name} "
                              f"(fill {plan['fill']}, y {plan['y0']:.0f}->"
                              f"{plan['y1']:.0f} over {cut_dur:.1f}s)")
                    elif plan["mode"] == "contain":
                        print(f"  scene {i+1}: kb contain on {name} "
                              f"(static full fit; text union infeasible "
                              f"for any legal moving window)")
                    else:
                        print(f"  scene {i+1}: kb anchor on {name} "
                              f"(cy {plan['cy']:.3f})")
                return plan

            if para_done or smart_done:
                pass
            elif n_cuts == 1:
                cr = _tcrop(cuts[0], dur, emph_cut=emph)
                render_panel_scene(cuts[0], audio, clip, w, h, dur,
                                   motion_idx=i, blur_bg=blur,
                                   emphasis=emph, tight_crop=tight,
                                   focus=foc, glitch=gl,
                                   crop_rect=cr,
                                   frame_plan=_kb(cuts[0], 0, 0.0, dur,
                                                  emph_cut=emph,
                                                  have_crop=bool(cr)))
            else:
                sub_dur = dur / n_cuts
                _silent = workdir / f"sil{i:03}.mp3"
                mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", f"{sub_dur:.3f}",
                        "-c:a", "libmp3lame", str(_silent)])
                subclips = []
                for k, panel in enumerate(cuts):
                    sc = workdir / f"c{i:03}_p{k}.mp4"
                    # only the FIRST cut of an emphasis scene gets the punch-in,
                    # so the "hit" lands on the scene entrance, not every cut.
                    cr = _tcrop(panel, sub_dur, emph_cut=(emph and k == 0))
                    render_panel_scene(panel, _silent, sc, w, h, sub_dur,
                                       motion_idx=i + k, blur_bg=blur,
                                       emphasis=(emph and k == 0),
                                       tight_crop=tight, focus=foc,
                                       glitch=(gl and k == 0),
                                       crop_rect=cr,
                                       frame_plan=_kb(
                                           panel, k, k * sub_dur, sub_dur,
                                           emph_cut=(emph and k == 0),
                                           have_crop=bool(cr)))
                    subclips.append(sc)
                # join the cut slices (video). With --slide (default), panels
                # push in from alternating directions via a short xfade slide;
                # otherwise a hard-cut concat. Then attach the real audio.
                vonly = workdir / f"c{i:03}_v.mp4"
                if not args.no_slide and len(subclips) > 1:
                    _slide_join(subclips, vonly, sub_dur)
                else:
                    lst = workdir / f"c{i:03}_list.txt"
                    lst.write_text("".join(f"file '{c}'\n" for c in subclips))
                    mv.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i",
                            str(lst), "-an", "-c", "copy", str(vonly)])
                mv.run(["ffmpeg", "-y", "-i", str(vonly), "-i", str(audio),
                        "-af", "apad", "-t", f"{dur:.3f}",
                        "-map", "0:v", "-map", "1:a",
                        *mv._INTER_V, *mv._INTER_A, str(clip)])
        else:
            # no panels -> fall back to make_video's generated-image path
            img = workdir / f"i{i:03}.jpg"
            mv.fetch_image_gemini(scene.get("image_prompt", "") + style, img, w, h)
            mv.render_scene(img, audio, clip, w, h, dur, motion_idx=i)

        clips.append(clip)
        durations.append(dur)
        word_times.append(wt)

    # exp-014: persist the framing decisions so check_containment.py can
    # verify text-box containment deterministically against the SAME plans
    # the render used (cached scenes log nothing — their plans are already
    # baked into the cached clips from the run that produced them).
    if kb_on:
        (workdir / "kb_plans.json").write_text(json.dumps(kb_log, indent=1))
        n_planned = sum(1 for r in kb_log
                        if r["plan"] and r["plan"].get("mode") in
                        ("scroll", "anchor"))
        print(f"kb plans: {n_planned}/{len(kb_log)} cuts content-aware "
              f"-> {workdir / 'kb_plans.json'}")

    # ---- join (no intro/outro videos; branding is a pop-up overlay later) ----
    brand_dir = Path(__file__).parent / "brand"
    use_branding = not args.no_branding and not vertical
    intro_dur = 0.0   # no intro clip anymore; kept for caption/SFX timing math
    hook_n = int(data.get("hook_scenes", 1))

    def concat_copy(seg_clips, name):
        # Per-scene clips come out of several encode paths with INCONSISTENT
        # video timebases (e.g. 1/30000, 1/15360, 1/737280). The concat demuxer
        # with -c copy cannot build a monotonic timeline across mixed timebases:
        # it injects a huge phantom PTS jump at the first boundary, so the muxed
        # container reports a bogus multi-thousand-second duration (the frames
        # themselves are fine). join_with_transitions then trusts that duration
        # for the outro xfade offset and ffmpeg stalls forever. Fix: remux every
        # clip to a single uniform timescale first (still a stream copy), then
        # copy-concat the normalized clips.
        norm = []
        for i, c in enumerate(seg_clips):
            n = workdir / f"{name}_n{i:03}.mp4"
            mv.run(["ffmpeg", "-y", "-i", str(c), "-c", "copy",
                    "-video_track_timescale", str(CONCAT_TIMESCALE), str(n)])
            norm.append(n)
        lst = workdir / f"{name}.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in norm))
        out = workdir / f"{name}.mp4"
        mv.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", str(out)])
        # guard: a healthy body/hook is on the order of the summed clip lengths;
        # if the muxed duration still looks wildly off, fail loud instead of
        # handing a poisoned offset to the transition join.
        got = mv.ffprobe_duration(out)
        approx = sum(mv.ffprobe_duration(c) for c in seg_clips)
        if got > approx * 1.5 + 5:
            raise RuntimeError(
                f"concat_copy({name}) produced corrupt duration {got:.1f}s "
                f"(clips sum ~{approx:.1f}s); timescale normalization failed")
        return out

    joined = concat_copy(list(clips), "all")
    current = joined

    # ---- background music (sidechain-ducked) — reuse make_video's mix ----
    # Bed resolution order: a script music_plan (multi-mood, per-section beds
    # stitched into one narrative-scored track) > --music arg > the project's
    # own mood-matched output/<slug>/music.mp3 > the shared
    # pipeline/brand/music.mp3. Missing music when the script asked for it is
    # a loud warning, not a silent skip.
    music = None
    plan = data.get("music_plan") or []
    if plan and not args.no_music:
        from fetch_music import mood_slug
        mood_paths = {}
        for s in plan:
            m = s.get("mood")
            if not m:
                continue
            p = project_dir / f"music_{mood_slug(m)}.mp3"
            if p.exists() and p.stat().st_size > 0:
                mood_paths[m] = p
        if mood_paths:
            total_v = mv.ffprobe_duration(current)
            bed = _build_music_bed(plan, mood_paths, durations, total_v,
                                   workdir)
            if bed:
                n_moods = len(set(s.get('mood') for s in plan))
                print(f"Music plan: {len(plan)} sections, "
                      f"{n_moods} mood(s) -> stitched bed")
                music = bed
    if music is None:
        if args.music:
            music = Path(args.music)
        elif (project_dir / "music.mp3").exists():
            music = project_dir / "music.mp3"
        else:
            music = brand_dir / "music.mp3"
    if data.get("music") and not args.no_music and not music.exists():
        print(f"WARNING: script wants music but no bed found "
              f"(looked for --music, {project_dir / 'music.mp3'}, {music}); "
              f"rendering WITHOUT background music.")
    if data.get("music") and not args.no_music and music.exists():
        bed_db = args.music_db
        print(f"Mixing background music (bed {bed_db}dB, ducked under voice)...")
        mixed = workdir / "mixed.mp4"
        # intro-shifted absolute windows for beat-drops and focus low-pass
        def _shift_intro(win):
            out = []
            for s, e in win:
                if s >= sum(durations[:hook_n]) - 1e-6:
                    s += intro_dur; e += intro_dur
                out.append((s, e))
            return out
        bwins = _shift_intro(beat_windows) if not args.no_beat_drop else []
        # Shonen-Flux audio matrix tier 1: voice 0dB; music at bed_db, ducked
        # under narration (sidechain ratio 16). Beat-drop automation on the bed:
        # during a [Pause] window boost music toward -4dB (no voice there, so it
        # isolates the drop). (Focus low-pass on music was dropped as unreliable
        # via runtime commands; the focus vignette on video still lands.)
        beat_gain = "".join(
            f"+({(-4.0)-bed_db})*between(t,{s:.3f},{e:.3f})" for s, e in bwins)
        vol_expr = f"pow(10,(0{beat_gain})/20)" if bwins else "1"
        fc = (
            f"[1:a]volume={bed_db}dB,volume='{vol_expr}':eval=frame,"
            f"aresample=48000[mraw];"
            f"[0:a]aresample=48000,asplit=2[sc][mix];"
            f"[mraw][sc]sidechaincompress=threshold=0.02:ratio=16:attack=5:"
            f"release=300:makeup=1[duck];"
            f"[mix][duck]amix=inputs=2:duration=first:dropout_transition=0,"
            f"aresample=48000[a]"
        )
        mv.run(["ffmpeg", "-y", "-i", str(current), "-stream_loop", "-1", "-i",
                str(music), "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", mv.AUDIO_BR, str(mixed)])
        current = mixed

    # ---- CONTENT-matched SFX overlay ----
    # Each scene's sfx_events place a real CC0 sound at the word it describes
    # (door SLAMS -> slam at that word). Placed AFTER music/voice mix and BEFORE
    # captions + final loudnorm so SFX sit inside the -14 LUFS master.
    if not args.no_sfx and len(durations) == len(scenes):
        # collect distinct cue phrases the script tags
        all_cues = [ev.get("cue") for sc in scenes
                    for ev in (sc.get("sfx_events") or []) if ev.get("cue")]
        cue_paths = {}
        if all_cues:
            import fetch_sfx
            key = fetch_sfx.resolve_key(args.freesound_key)
            if key:
                print(f"Fetching {len(set(all_cues))} content SFX from Freesound...")
                cue_paths = fetch_sfx.fetch_cues(key, all_cues)
            else:
                # no key: reuse only already-cached cue files, skip the rest
                for c in set(all_cues):
                    p = brand_dir / "sfx" / f"cue_{fetch_sfx.slugify(c)}.wav"
                    cue_paths[c] = p if p.exists() else None
                if not any(cue_paths.values()):
                    print("No Freesound key and no cached cues; skipping SFX.")
        cues, name_map = _plan_content_cues(
            scenes, durations, word_times, intro_dur, hook_n, cue_paths,
            db_offset=args.sfx_db)
        if cues:
            print(f"Adding {len(cues)} content SFX:")
            for t_s, slug, g in cues:
                print(f"    {t_s:6.2f}s  {slug}  ({g:+.0f}dB)")
            sfxed = workdir / "sfxed.mp4"
            _mix_sfx(current, cues, name_map, sfxed)
            current = sfxed
        else:
            print("No content SFX placed (no tagged events matched a CC0 sound).")

    # ---- scene ambience layer ----
    # A scene's `ambience` ("crowd murmur", "rain", ...) loops a soft
    # atmosphere bed under that scene's span — the setting's room tone.
    # Consecutive scenes sharing a value are merged into one span.
    if not args.no_sfx and len(durations) == len(scenes):
        spans = _plan_ambience(scenes, durations, intro_dur, hook_n)
        if spans:
            import fetch_sfx
            amb_cues = sorted({c for _, _, c in spans})
            key = fetch_sfx.resolve_key(args.freesound_key)
            if key:
                print(f"Fetching {len(amb_cues)} ambience loop(s)...")
                amb_paths = fetch_sfx.fetch_cues(key, amb_cues, max_dur=30.0)
            else:
                amb_paths = {}
                for c in amb_cues:
                    p = brand_dir / "sfx" / f"cue_{fetch_sfx.slugify(c)}.wav"
                    amb_paths[c] = p if p.exists() else None
            usable = [(s, e, c) for s, e, c in spans if amb_paths.get(c)]
            if usable:
                print(f"Adding {len(usable)} ambience span(s):")
                for s, e, c in usable:
                    print(f"    {s:6.1f}s-{e:6.1f}s  {c}")
                ambed = workdir / "ambed.mp4"
                if _mix_ambience(current, spans, amb_paths, ambed):
                    current = ambed

    # ---- captions ----
    # Long-form default: NO burned-in captions (documentary style — voice +
    # art carry it; YouTube gets the SRT sidecar for closed captions).
    # --captions opts back into burned kinetic typography; --karaoke and
    # --plain-srt keep the make_video styles. The plain .srt sidecar is
    # always emitted for upload regardless.
    srt = workdir / "subs.srt"
    ass = workdir / "subs.ass"
    have_wt = len(word_times) == len(scenes) and any(w for w in word_times)
    if len(durations) == len(scenes):
        # always emit the uploadable plain SRT sidecar
        mv.build_srt(scenes, durations, srt,
                     word_times=word_times if have_wt else None,
                     gap_dur=intro_dur, gap_after=hook_n)
    burn = (args.captions or args.karaoke or args.plain_srt) \
        and not args.no_captions
    if burn and len(durations) == len(scenes):
        if args.plain_srt:
            print("Generating captions (plain SRT)...")
            style_str = ("FontName=DejaVu Sans,FontSize=15,Bold=1,"
                         "PrimaryColour=&H00FFFFFF,BorderStyle=3,"
                         "OutlineColour=&H66000000,Outline=1,Shadow=0,MarginV=45")
            subfilter = f"subtitles={srt}:force_style='{style_str}'"
        elif args.karaoke:
            print("Generating captions (karaoke)...")
            mv.build_karaoke_ass(scenes, durations, ass,
                                 word_times=word_times if have_wt else None,
                                 gap_dur=intro_dur, gap_after=hook_n,
                                 vertical=vertical)
            subfilter = f"ass={ass}"
        else:
            print("Generating captions (kinetic)...")
            import kinetic
            kinetic.build_kinetic_ass(scenes, durations, ass, mv,
                                      word_times=word_times if have_wt else None,
                                      gap_dur=intro_dur, gap_after=hook_n,
                                      vertical=vertical)
            subfilter = f"ass={ass}"
        captioned = workdir / "captioned.mp4"
        mv.run(["ffmpeg", "-y", "-i", str(current), "-vf", subfilter,
                *mv._INTER_V, "-c:a", "copy", str(captioned)])
        current = captioned

    # ---- review cut-point ----
    # --stop-after review-cut ends the render here: full story visuals + voice
    # + music + SFX/ambience + captions, but NO branding overlays (popup, title
    # card) and NO final delivery encode. The QC reviewer inspects this
    # intermediate so (a) fix rounds skip the 3 expensive full-video encodes
    # and (b) intentional branding never triggers false faults. A later full
    # run reuses the cached scene clips and finalizes in one pass.
    if args.stop_after == "review-cut":
        (workdir / "review_src.txt").write_text(str(current))
        print(f"\nReview cut ready: {current}")
        print("  (stopped before popup/title-card/final encode; "
              "re-run without --stop-after to finalize)")
        return current

    # ---- channel pop-up overlay (replaces the old intro/outro videos) ----
    # A YouTube-style card (logo + channel name + LIKE + SUBSCRIBE) slides in
    # right after the hook and again near the end. An animated cursor clicks
    # LIKE (count ticks up in real time) then SUBSCRIBE (button turns red).
    if use_branding:
        import subscribe_popup as sp
        ch_name, ch_logo = sp.channel_info()
        total = mv.ffprobe_duration(current)
        hook_end = sum(durations[:hook_n])
        # sync to the narrated CTA ("smash that like button and subscribe")
        # when the script has one, so the card is on screen as it's spoken
        cta = sp.cta_times(scenes, durations,
                           word_times if len(word_times) == len(scenes)
                           else None)
        times = sp.plan_times(total, hook_end, cta=cta)
        if times:
            print(f"Adding channel pop-up ({ch_name}) at "
                  + ", ".join(f"{t:.1f}s" for t in times) + "...")
            frames_dir = workdir / "popup_frames"
            _, pw, ph = sp.build_frames(frames_dir, h / 1080.0, ch_name, ch_logo)
            pad = int(90 * (h / 1080.0))
            px = int(56 * (h / 1080.0)) - pad
            py = h - ph + pad - int(64 * (h / 1080.0))
            click = sp.make_click_wav(workdir, mv.run)
            popped = workdir / "popped.mp4"
            sp.apply(current, popped, frames_dir, times, px, py,
                     mv.run, mv._INTER_V, mv.AUDIO_BR, click_wav=click)
            current = popped

    # ---- series + chapter identity card ----
    # A lower-third banner with the series name and "CHAPTER N" slides in a
    # beat after the hook opens (never frame 0 — the hook visual grabs first)
    # so viewers always know what they're watching.
    src = data.get("source") or {}
    series_name = src.get("series") or data.get("series")
    chap = src.get("chapter") or data.get("chapter")
    if series_name and chap and not args.no_title_card:
        import title_overlay as tover
        card = workdir / "title_card.png"
        cs = h / 1080.0
        cw_, ch_ = tover.build_card(card, str(series_name), str(chap), cs)
        tx_ = int(48 * cs)
        ty_ = int(48 * cs)
        print(f"Adding title card ({series_name} — Chapter {chap}) at "
              f"{tover.SHOW_AT:.1f}s for {tover.SHOW_FOR:.0f}s...")
        titled = workdir / "titled.mp4"
        tover.apply(current, titled, card, tx_, ty_, mv.run, mv._INTER_V)
        current = titled

    # ---- single delivery encode (loudnorm + H.264 CRF18 + faststart) ----
    print(f"Final encode (CRF {mv.FINAL_CRF} {mv.FINAL_PRESET}, "
          f"loudnorm {mv.LUFS} LUFS)...")
    final_tmp = workdir / "final.mp4"
    mv.run(["ffmpeg", "-y", "-i", str(current),
            "-af", f"loudnorm=I={mv.LUFS}:TP={mv.TRUE_PEAK}:LRA=11,aresample=48000",
            "-c:v", "libx264", "-preset", mv.FINAL_PRESET, "-crf", mv.FINAL_CRF,
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-x264-params", "keyint=60:min-keyint=60:scenecut=0",
            "-c:a", "aac", "-b:a", mv.FINAL_AUDIO_BR, "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(final_tmp)])

    if srt.exists():
        shutil.copy(srt, out_path.with_suffix(".srt"))
    shutil.copy(final_tmp, out_path)
    print(f"\nDone: {out_path}")
    info = mv._probe_quality(out_path)
    print(f"  {info['duration']:.1f}s, {info['res']}, "
          f"video {info['vbr']:.1f} Mbps, {info['lufs']}")
    if not args.workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="path to extended scene JSON")
    ap.add_argument("--res", choices=list(mv.RES_PRESETS), default="1440p")
    ap.add_argument("--karaoke", action="store_true",
                    help="word-highlight karaoke captions instead of kinetic")
    ap.add_argument("--plain-srt", action="store_true",
                    help="plain SRT captions instead of kinetic")
    ap.add_argument("--no-captions", action="store_true",
                    help="force captions off even if --captions/--karaoke set")
    ap.add_argument("--captions", action="store_true",
                    help="burn kinetic captions (off by default for long-form; "
                         "an .srt sidecar is always written)")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--music", default=None,
                    help="path to the background-music bed to use instead of "
                         "the shared pipeline/brand/music.mp3 (e.g. a per-video "
                         "mood-matched track from fetch_music.py).")
    ap.add_argument("--music-db", type=float, default=-10.0,
                    help="background-music bed level in dB (default -10; more "
                         "negative = quieter). It ducks further under voice.")
    ap.add_argument("--no-branding", action="store_true")
    ap.add_argument("--no-title-card", action="store_true",
                    help="skip the series/chapter identity banner shown "
                         "during the opening seconds")
    ap.add_argument("--no-truncate-silence", action="store_true",
                    help="disable hard-cutting of narration pauses >150ms "
                         "(on by default, for a relentless delivery)")
    ap.add_argument("--allow-crop", action="store_true",
                    help="honor per-scene blur_bg=false (crop-to-fill). By "
                         "default every scene fits the FULL panel over a "
                         "blurred background so faces are never cropped.")
    ap.add_argument("--no-sfx", action="store_true",
                    help="disable content SFX entirely. On by default.")
    ap.add_argument("--freesound-key", default=None,
                    help="Freesound API token for fetching content SFX cues "
                         "(or set FREESOUND_API_KEY). Without it, only "
                         "already-cached cue files are used.")
    ap.add_argument("--sfx-db", type=float, default=-6.0,
                    help="global SFX level trim in dB added to every content "
                         "sound (default -6; more negative = quieter).")
    ap.add_argument("--tight-crop", action="store_true",
                    help="fit panels to a larger focal window (fills more of "
                         "the frame) while keeping a safe margin. Off by "
                         "default (full-panel-over-blur keeps faces safe).")
    ap.add_argument("--text-aware", action="store_true",
                    help="phone-readability framing (exp-009): detect per-"
                         "panel text-line boxes (<slug>.textboxes.json "
                         "sidecar, generated on first run) and (a) crop the "
                         "blur_bg fit to the text region when its lines "
                         "would render below 40px@1080p, (b) size smart-"
                         "layout cells by measured text height. Off by "
                         "default = today's behavior byte-identical.")
    ap.add_argument("--kenburns", choices=["center", "smart"],
                    default="center",
                    help="'smart' (exp-014, gap-014/gap-006): content-aware "
                         "Ken Burns framing. Tall panels (h/w > 2.5) get a "
                         "webtoon-native top->bottom vertical scroll whose "
                         "window keeps every OCR text box on screen for the "
                         "whole cut; normal panels get a box-aware vertical "
                         "anchor shift (OCR text + anime-face boxes, "
                         "sidecar-cached) instead of the blind center. "
                         "Center fallback whenever no boxes/low confidence/"
                         "planner rejects. MANGA_KB_SMART=1 equivalent. "
                         "Default 'center' = today's behavior "
                         "byte-identical.")
    ap.add_argument("--layout", choices=["seq", "smart", "smart2"],
                    default="seq",
                    help="'smart': scenes with 3+ panels (pace != hype) get a "
                         "narration-synced multi-panel composite (grid "
                         "highlight / accordion stack) instead of sequential "
                         "cuts; falls back to 'seq' per scene when layout "
                         "guards fail. 'smart2' (exp-013, experimental): "
                         "magazine-style content-weighted templates "
                         "(hero/rail/mag_grid/strips) for 2+ panel scenes "
                         "with per-cell Ken Burns on the active cell; falls "
                         "back to smart-v1 plans, then seq. Default 'seq' = "
                         "today's behavior.")
    ap.add_argument("--no-slide", action="store_true",
                    help="disable directional panel slide-in transitions "
                         "(hard-cut between intra-scene panels instead).")
    ap.add_argument("--no-glitch", action="store_true",
                    help="disable the 2-frame RGB-split + flash on emphasis "
                         "beats.")
    ap.add_argument("--no-beat-drop", action="store_true",
                    help="disable the music beat-drop spike at [Pause] markers.")
    ap.add_argument("--parallax", action="store_true",
                    help="2.5D parallax: segment foreground characters (rembg) "
                         "and move fg/bg on separate axes. Off by default "
                         "(experimental; segmentation quality varies on manga).")
    ap.add_argument("--workdir", help="reuse a working dir (resumes finished scenes)")
    ap.add_argument("--redo-scene", type=int, action="append", default=[])
    ap.add_argument("--stop-after", choices=["review-cut"], default=None,
                    help="'review-cut': stop after story visuals + audio mix + "
                         "captions, BEFORE branding overlays and the final "
                         "delivery encode. Writes the intermediate's path to "
                         "<workdir>/review_src.txt for the QC reviewer.")
    args = ap.parse_args()
    render(args.script, args)


if __name__ == "__main__":
    main()
