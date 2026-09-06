#!/usr/bin/env python3
"""kinetic.py — high-stimulation "kinetic typography" captions for the anime
recap format.

Style spec (from the framework):
  * heavy bold sans-serif, ALL text white with a THICK black stroke
  * 2 words per on-screen frame, POP-IN animation (scale up from small)
  * centered in the lower-middle third of the canvas
  * key impact words (per-scene "keywords") auto-highlight in bright YELLOW

Builds an ASS file. Reuses make_video's word-timing helpers (_scene_word_times,
_chunk_words are not needed — we chunk to exactly 2 words here) so timing stays
aligned to the real edge-tts boundaries.
"""


def _ass_ts(sec):
    cs = int(round(sec * 100))
    return f"{cs // 360000:d}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def _pairs(words):
    """Group a scene's word dicts into 2-word frames."""
    return [words[i:i + 2] for i in range(0, len(words), 2)]


def build_kinetic_ass(scenes, durations, ass_path, mv, word_times=None,
                      gap_dur=0.0, gap_after=1, vertical=False,
                      font="DejaVu Sans"):
    """Write a kinetic-typography ASS. `mv` is the imported make_video module
    (for _scene_word_times). keywords per scene come from scene["keywords"]."""
    fs = 110 if vertical else 76           # heavy, large
    # lower-middle third: alignment 2 (bottom-center) lifted up by MarginV
    marginv = 470 if vertical else 300
    outline = 6 if vertical else 5          # thick black stroke
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Kinetic,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,1,0,0,0,100,100,0,0,1,{outline},2,2,60,60,{marginv},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n")
    # ASS colors are &HAABBGGRR (BGR). Two highlight classes:
    YELLOW = "&H003BEBFF&"   # #FFEB3B hype/victory (WIN, GODLY, UNSTOPPABLE)
    ORANGE = "&H002257FF&"   # #FF5722 shock/conflict (BROKEN, RAGE, SHATTERS)
    WHITE = "&H00FFFFFF&"
    # built-in fallback vocab so plain `keywords` still gets a sensible class
    HYPE_WORDS = {
        "WIN", "WINNER", "GODLY", "INSANE", "EVOLUTION", "UNSTOPPABLE", "EPIC",
        "LEGENDARY", "POWER", "MIRACLE", "HAPPY", "LIGHT", "RADIANT", "ERUPTS",
        "UNBELIEVABLE", "GENIUS", "ULTIMATE", "VICTORY", "HERO", "SHARE",
    }
    SHOCK_WORDS = {
        "BROKEN", "MONSTER", "SAD", "DESTROYED", "DEFEAT", "SHATTERS", "SHATTER",
        "RAGE", "SEETHING", "EXPLODES", "EXPLODE", "SLAMS", "SLAM", "FREEZES",
        "FREEZE", "BOMB", "DETONATES", "SUFFOCATING", "SUFFOCATION", "CRUSHING",
        "DROWNING", "GUILLOTINE", "NUCLEAR", "FIRESTORM", "POISONOUS", "HURT",
        "DIVIDED", "NIGHTMARE", "SCREAMS", "SCREAM", "LAUNCHES",
    }

    def _class(bare, hype, shock):
        # explicit per-scene fields win; else the built-in vocab; else neutral
        if bare in hype:
            return YELLOW
        if bare in shock:
            return ORANGE
        if bare in HYPE_WORDS:
            return YELLOW
        if bare in SHOCK_WORDS:
            return ORANGE
        return None

    events = []
    t = 0.0
    for si, (scene, dur) in enumerate(zip(scenes, durations)):
        if si == gap_after:
            t += gap_dur
        wt = word_times[si] if word_times and si < len(word_times) else None
        words_list = mv._scene_word_times(scene, dur, wt)

        def _norm(s):
            return "".join(ch for ch in s.upper() if ch.isalnum())
        # per-scene keyword sets (uppercased, alnum-stripped). Explicit
        # keywords_hype / keywords_shock classify; a bare `keywords` entry is
        # classified by the built-in vocab (defaults to shock, since most hype
        # keywords in this format are dramatic/violent).
        hype = {_norm(k) for k in (scene.get("keywords_hype") or [])}
        shock = {_norm(k) for k in (scene.get("keywords_shock") or [])}
        for k in scene.get("keywords", []) or []:
            b = _norm(k)
            if b in HYPE_WORDS:
                hype.add(b)
            else:
                shock.add(b)   # default a generic keyword to the shock class
        for pair in _pairs(words_list):
            start = t + pair[0]["start"]
            end = t + pair[-1]["end"] + 0.10
            if end <= start:
                end = start + 0.35
            rel = int(max(end - start, 0.2) * 1000)
            # per-word coloring: yellow=hype, orange=shock, else white
            parts = []
            for w in pair:
                bare = _norm(w["word"])
                col = _class(bare, hype, shock) or WHITE
                parts.append("{\\c" + col + "}" +
                             w["word"].replace("{", "").replace("}", ""))
            text = " ".join(parts)
            # pop-in: fade + scale from 55% -> 108% -> settle 100%
            anim = ("{\\fad(50,40)}{\\fscx55\\fscy55"
                    "\\t(0,110,\\fscx108\\fscy108)"
                    f"\\t(110,190,\\fscx100\\fscy100)}}")
            events.append(
                f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Kinetic,,0,0,0,,"
                f"{anim}{text}")
        t += dur
    ass_path.write_text(header + "\n".join(events) + "\n")
    return True
