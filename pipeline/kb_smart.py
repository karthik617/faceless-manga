#!/usr/bin/env python3
"""kb_smart.py — content-aware Ken Burns framing (gap-014 + gap-006, exp-014).

The default zoompan in panel_render.py is CENTER-anchored and content-blind:
on extreme-aspect merged webtoon panels (h/w > 2.5) the visible window cuts
the composite's top/bottom by (1-1/zmax)/2 at peak zoom and grazes bubbles/
faces sitting near a panel edge (golden ch2 render-framing residuals, scenes
15/21/28; ch4 scenes 4/12/14/22). This module plans a content-aware frame per
panel cut, behind panel_render's --kenburns smart flag:

  * h/w > TALL_AR  -> "scroll": webtoon-native top->bottom vertical pan of a
    fit-to-width window (the ONLY sanctioned pan — sideways pans were removed
    as distracting). The scroll band is constrained so the window contains the
    padded union of ALL OCR text boxes at EVERY moment of the cut (stronger
    than the gap-014 per-beat gate, and deterministic). Speed is capped;
    smoothstep easing masks stepping; the render side runs the crop on the
    existing 2x supersampled composite so steps are half-pixel after
    downscale.
  * otherwise      -> "anchor": today's exact composite + zoom, but the
    zoompan y anchor shifts (vertically only) so the guaranteed-visible
    max-zoom window contains the text union (+ detected face boxes when they
    fit; faces are dropped first, text never — the gate measures text).

Detector decision (exp-014 smoke, 2026-09-11, recorded in IMPLEMENTATION.md):
the vendored manga109_yolo face/body classes return ZERO detections on the 8
colored-webtoon fault panels (research risk #1 confirmed), so faces come from
the research runner-up deepghs/anime_face_detection (face_detect_v1.4_s,
MIT-licensed weights, ~40 MB, auto-downloaded to the HF cache on first use —
same runtime-download pattern as the gap-004 manga109 weights) through the
same vendored yolo_detect._predict path. OCR text boxes come from the
existing exp-009 textboxes sidecar. Face boxes are cached in a
<slug>.faceboxes.json sidecar (name+mtime keyed, same pattern as text_boxes).

EVERY failure path returns None -> the caller keeps today's center anchor
(graceful-fallback rule: never worse than current behavior; flag off is
byte-identical because panel_render never calls into here).
"""
import json
from pathlib import Path

# aspect ratio (h/w) above which a panel gets the vertical-scroll treatment
# (gap-014 card threshold; p0041@3.91, p0059@3.00, p0101@3.64 all qualify,
# the normal-aspect fault panels stay on the anchor path)
TALL_AR = 2.5
# face detector: deepghs/anime_face_detection (MIT), chosen by the exp-014
# smoke over manga109's face class (0 recall on colored webtoons). 0.35 conf
# floor per the research risk table (false-positive faces inflate the union).
FACE_REPO = "deepghs/anime_face_detection"
FACE_MODEL = "face_detect_v1.4_s"
FACE_CONF = 0.35
# scroll speed cap in SOURCE panel px/s (research: ~12 px/frame at 30fps on
# a 3000px panel over 8s is visible stepping; 150 px/s keeps the sweep calm
# and readable). When the cap would truncate the sweep the range shrinks
# toward the text-union band; below MIN_SCROLL_PX we don't scroll at all
# (an imperceptible creep reads as judder, not motion).
SCROLL_SPEED_CAP = 150.0
MIN_SCROLL_PX = 40
# fit-to-width fill fraction floor: the scroll window may shrink the panel
# below full frame width (fill < 1) to GROW the window when the text union
# is taller than the full-width window. Below this floor the panel is back
# to sliver-land (text unreadable) and center fallback is less bad.
MIN_FILL = 0.55
# v2 scroll gate (exp-014 eval regression, scene 14 t=422.5): fit-to-width
# scrolling MAGNIFIES — on SFX/art tall panels with little or no text
# (p0075: one 76x76 box on a 690x2000 impact splash) the band shows giant
# cut-off glyph art where the baseline letterbox showed the piece whole
# (3/3 reviewers: cropped_content + irrelevant_panel). Scroll now fires only
# when OCR text-box AREA covers at least this fraction of the panel — the
# signature of genuine sequential reading content that benefits from a
# readable sweep. Chapter-wide v1 data separates the populations cleanly:
# scroll-worthy p0003/p0012/p0092/p0100 measure 1.59-2.86%, the regression
# class p0032/p0075/p0136 measure 0.04-0.42% (plus five zero-box panels);
# 1% sits ~2.4x from BOTH nearest neighbors. Gated-out panels fall through
# to the anchor path (today's letterbox fit + at most a vertical anchor
# shift), never a fit-to-width scroll.
MIN_SCROLL_TEXT_AREA = 0.01
# padding (panel px) beyond every must-see box edge — same guard value as
# panel_render.TEXT_PAD (never graze a drawn bubble outline).
PAD = 24
# anchor mode: keep boxes this many COMPOSITE px inside the max-zoom window
# (zoompan rounds x/y per frame; mirrors panel_render.MOTION_JITTER intent).
JITTER_SS = 8


# ---- face box sidecar (same cache pattern as text_boxes.ensure_boxes) ------

def ensure_face_boxes(sidecar_path, panel_paths, conf=FACE_CONF):
    """{panel_name: [[x, y, w, h], ...]} anime-face boxes for every path,
    detecting only panels missing from the sidecar or whose mtime changed.
    Raises on failure (model download, onnx) — the CALLER catches and treats
    faces as absent (text-only planning still fixes the bubble sites)."""
    sidecar_path = Path(sidecar_path)
    try:
        cache = json.loads(sidecar_path.read_text())
        assert cache.get("model") == f"{FACE_REPO}/{FACE_MODEL}"
    except Exception:
        cache = {"model": f"{FACE_REPO}/{FACE_MODEL}", "conf": conf,
                 "panels": {}}
    out, changed = {}, False
    for p in panel_paths:
        p = Path(p)
        if p.name in out:
            continue
        st = p.stat()
        rec = cache["panels"].get(p.name)
        if rec and abs(rec.get("mtime", -1) - st.st_mtime) < 1e-6:
            out[p.name] = rec["boxes"]
            continue
        boxes = _detect_faces(p, conf)
        cache["panels"][p.name] = {"mtime": st.st_mtime, "boxes": boxes}
        out[p.name] = boxes
        changed = True
        print(f"  kb_smart: {p.name}: {len(boxes)} face(s)")
    if changed:
        sidecar_path.write_text(json.dumps(cache))
    return out


def _detect_faces(panel_path, conf):
    """Windowed anime-face detection over one (possibly very tall) panel,
    [[x, y, w, h], ...] in panel px. Same windowing scheme as
    yolo_detect._detect_one (640px width, 1280px windows, 256px overlap) so
    a face on a window edge is seen whole by the neighbor window."""
    import yolo_detect as yd
    from PIL import Image
    img = Image.open(panel_path).convert("RGB")
    w, h = img.size
    max_w, win, stride = yd.DEFAULT_MAX_W, yd.DEFAULT_WIN, yd.DEFAULT_STRIDE
    scale = 1.0
    if w > max_w:
        scale = max_w / float(w)
        img = img.resize((max_w, max(1, int(round(h * scale)))),
                         Image.LANCZOS)
    sw, sh = img.size
    swin = max(1, int(round(win * scale)))
    sstride = max(1, int(round(stride * scale)))
    boxes, y = [], 0
    while True:
        y1 = min(sh, y + swin)
        crop = img.crop((0, y, sw, y1))
        for (bx0, by0, bx1, by1), label, c in yd._predict(
                crop, conf, repo=FACE_REPO, model=FACE_MODEL):
            if label != "face":
                continue
            boxes.append([bx0 / scale, (by0 + y) / scale,
                          bx1 / scale, (by1 + y) / scale])
        if y1 >= sh:
            break
        y += sstride
    # merge duplicates seen from two overlapping windows (union at IoU>0.3,
    # same rule as yolo_detect._detect_one)
    merged = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for m in merged:
            if yd._iou(b, m) > 0.3:
                m[0], m[1] = min(m[0], b[0]), min(m[1], b[1])
                m[2], m[3] = max(m[2], b[2]), max(m[3], b[3])
                break
        else:
            merged.append(list(b))
    # xyxy -> xywh to match the text-box convention
    return [[round(b[0], 1), round(b[1], 1),
             round(b[2] - b[0], 1), round(b[3] - b[1], 1)] for b in merged]


# ---- framing planner --------------------------------------------------------

def _clamp_boxes(boxes, pw, ph):
    out = []
    for b in boxes or []:
        x0, y0 = max(b[0], 0), max(b[1], 0)
        x1, y1 = min(b[0] + b[2], pw), min(b[1] + b[3], ph)
        if x1 > x0 and y1 > y0:
            out.append([x0, y0, x1 - x0, y1 - y0])
    return out


def _union_y(boxes, pad, ph):
    """(top, bottom) of the pad-expanded vertical union, clamped, or None."""
    if not boxes:
        return None
    y0 = max(min(b[1] for b in boxes) - pad, 0)
    y1 = min(max(b[1] + b[3] for b in boxes) + pad, ph)
    return (y0, y1)


def plan_frame(panel_path, text_boxes, face_boxes, frame_w, frame_h,
               duration, zmax, log=None):
    """Content-aware frame plan for ONE panel cut, or None (-> center anchor,
    byte-identical default path). Pure geometry — no I/O beyond reading the
    panel's dimensions. Returns a dict the renderer and the containment gate
    both interpret with the SAME formulas:

      {"mode": "scroll", "fill", "fw", "gh", "y0", "y1",
       "ss_w", "ss_h", "pw", "ph"}          y in supersampled-composite px
      {"mode": "anchor", "cy", "zmax", "ss_w", "ss_h", "pw", "ph",
       "fit", "ox", "oy"}                   cy = window-center fraction of ss_h
      {"mode": "contain", "ss_w", "ss_h", "pw", "ph"}
                                            static full-fit letterbox, no zoom
    """
    def _log(msg):
        if log:
            log(msg)
    try:
        from PIL import Image
        with Image.open(panel_path) as im:
            pw, ph = im.size
    except Exception:
        return None
    if duration <= 0:
        return None
    ss_w, ss_h = frame_w * 2, frame_h * 2
    tboxes = _clamp_boxes(text_boxes, pw, ph)
    fboxes = _clamp_boxes(face_boxes, pw, ph)
    tall = ph / pw > TALL_AR

    if tall:
        plan = _plan_scroll(pw, ph, tboxes, fboxes, ss_w, ss_h,
                            frame_w, frame_h, duration, _log)
        if plan:
            return plan
        # scroll gated/infeasible: fall through to the anchor plan on
        # today's fit — heals one-end grazes when full containment is
        # possible within the zoom window.

    plan = _plan_anchor(pw, ph, tboxes, fboxes, ss_w, ss_h, zmax, _log)
    if plan:
        return plan

    # v2 infeasible-geometry handling (exp-014 eval §1: p0002/p0024 carry
    # ~1800px-tall text unions on 690px-wide panels — NO legal scroll band
    # or 1/zmax anchor window can contain them, and v1's silent center
    # fallback kept violating the deterministic gate chapter-wide, 95
    # beats). _plan_anchor's None is ambiguous (no boxes / center already
    # legal / infeasible), so re-check the center fallback with the SAME
    # gate math: only when the default center window would provably cut a
    # text box do we emit an explicit static full-fit "contain" plan — the
    # whole panel letterboxed at z=1, which contains every box at every t
    # by construction (zero crop => zero violations). Scoped to tall
    # panels (the gate's scope and the observed fault class); normal-
    # aspect infeasible panels keep today's center behavior — changing
    # them would trade motion chapter-wide for a fault nobody measured.
    if tall and tboxes:
        center = {"mode": "center", "zmax": zmax,
                  "ss_w": ss_w, "ss_h": ss_h, "pw": pw, "ph": ph}
        if not window_contains_boxes(center, tboxes, 0.0, duration):
            _log("contain: text union infeasible for any legal window, "
                 "static full-fit letterbox (center would cut text)")
            return {"mode": "contain",
                    "ss_w": ss_w, "ss_h": ss_h, "pw": pw, "ph": ph}
    return None


def _plan_scroll(pw, ph, tboxes, fboxes, ss_w, ss_h, frame_w, frame_h,
                 duration, _log):
    """Vertical top->bottom scroll of a fit-to-width window. The window must
    contain the padded union of ALL text boxes for the WHOLE cut (the gate's
    per-beat requirement, satisfied at every t). Face boxes shrink the band
    further only when that stays feasible (drop-faces-first rule)."""
    # v2 gate: only genuinely text-dense tall panels earn the fit-to-width
    # magnification. Low/zero-text tall panels are SFX/impact art that reads
    # best whole (v1 eval: the s14/p0075 scroll band showed giant cut-off
    # glyphs the baseline letterbox showed complete — the one pixel-real
    # regression). Returning None here falls through to the anchor path,
    # which keeps today's full-panel letterbox fit.
    t_area = sum(b[2] * b[3] for b in tboxes) / float(pw * ph)
    if t_area < MIN_SCROLL_TEXT_AREA:
        _log(f"scroll: text area {t_area:.2%} < "
             f"{MIN_SCROLL_TEXT_AREA:.0%} gate (SFX/art tall panel), "
             f"letterbox fit kept")
        return None
    # pad is a nicety (don't graze the drawn bubble outline); the binding
    # gate measures the OCR boxes themselves. When the padded union makes
    # the plan infeasible, retry unpadded before giving up (exp-014: p0015's
    # bottom-corner text sits 9px too low for the padded band).
    for pad in (PAD, 0):
        tu = _union_y(tboxes, pad, ph)
        # window height in PANEL px at fill f: win_h = (frame_h/frame_w)*pw/f
        win_full = (frame_h / frame_w) * pw      # fill = 1.0
        fill = 1.0
        if tu:
            need = tu[1] - tu[0]
            if need > win_full:
                fill = win_full / need           # grow window by shrinking fg
                if fill < MIN_FILL:
                    if pad == 0:
                        _log(f"scroll: text union {need:.0f}px needs fill "
                             f"{fill:.2f} < {MIN_FILL} floor")
                        return None
                    continue
        win_h = win_full / fill
        if win_h >= ph:                          # whole panel fits one window
            return None                          # -> anchor path handles it
        # allowed y band (panel px, window top) keeping the text union covered
        lo, hi = 0.0, ph - win_h
        if tu:
            lo = max(lo, tu[1] - win_h)
            hi = min(hi, tu[0])
        if hi >= lo - 1e-6:
            break                                # feasible band found
    else:
        return None                              # infeasible even unpadded
    hi = max(hi, lo)
    # shrink toward face coverage when feasible; faces drop first otherwise
    fu = _union_y(fboxes, PAD, ph)
    if fu:
        flo, fhi = max(lo, fu[1] - win_h), min(hi, fu[0])
        if fhi >= flo:
            lo, hi = flo, fhi
        else:
            _log("scroll: face union does not fit the text-covering band, "
                 "faces dropped (text containment kept)")
    # speed cap: reduce the sweep centered in the band
    dist = hi - lo
    max_dist = SCROLL_SPEED_CAP * duration
    if dist > max_dist:
        mid = (lo + hi) / 2.0
        lo, hi = mid - max_dist / 2.0, mid + max_dist / 2.0
        dist = max_dist
    if dist < MIN_SCROLL_PX:
        return None                              # imperceptible creep
    # panel px -> supersampled fg px
    fw = int(round(fill * ss_w / 2)) * 2         # even for yuv420p
    g = fw / pw
    # exact height ffmpeg's scale=fw:-2 will produce (round to even), so the
    # y1 clamp below can never ask crop for rows past the scaled fg's end
    gh = int(round(ph * g / 2.0)) * 2
    y0 = lo * g
    y1 = min(hi * g, gh - ss_h - 2)              # never crop past the fg end
    if y1 <= y0:
        return None
    return {"mode": "scroll", "fill": round(fill, 4), "fw": fw, "gh": gh,
            "y0": round(y0, 1), "y1": round(y1, 1),
            "ss_w": ss_w, "ss_h": ss_h, "pw": pw, "ph": ph}


def _plan_anchor(pw, ph, tboxes, fboxes, ss_w, ss_h, zmax, _log):
    """Vertically shifted zoompan anchor on today's exact composite. The
    guaranteed-visible window across the whole zoom path is the (1/zmax)
    window centered at cy; every must-see box must fit inside it (inset by
    JITTER_SS). Horizontal position stays locked to center (no sideways
    pans) — boxes wider than the central window mean center is already the
    best legal frame, so we return None (never worse than today)."""
    if not tboxes and not fboxes:
        return None
    fit = min(ss_w / pw, ss_h / ph)              # decrease-fit scale
    ox = (ss_w - pw * fit) / 2.0
    oy = (ss_h - ph * fit) / 2.0
    half_h = ss_h / (2.0 * zmax) - JITTER_SS
    half_w = ss_w / (2.0 * zmax) - JITTER_SS

    def comp(boxes):                             # panel px -> composite px
        return [[ox + b[0] * fit, oy + b[1] * fit,
                 b[2] * fit, b[3] * fit] for b in boxes]

    def fits(boxes):
        """cy (fraction of ss_h) containing all boxes at max zoom, or None.
        The pad is a nicety (don't graze the drawn bubble outline); the
        binding gate measures the raw OCR boxes, so when the padded union
        pokes past a composite edge (box at the very panel edge — ch2 p0015's
        bottom-corner credit text) we retry unpadded before giving up."""
        if not boxes:
            return 0.5
        for pad in (PAD * fit, 0.0):
            y0 = min(b[1] for b in boxes) - pad
            y1 = max(b[1] + b[3] for b in boxes) + pad
            x0 = min(b[0] for b in boxes) - pad
            x1 = max(b[0] + b[2] for b in boxes) + pad
            if x0 < ss_w / 2.0 - half_w or x1 > ss_w / 2.0 + half_w:
                if pad == 0.0:
                    return None                  # can't move x: center = best
                continue
            if y1 - y0 > 2 * half_h:
                if pad == 0.0:
                    return None                  # taller than the window
                continue
            cy = (y0 + y1) / 2.0 / ss_h
            # clamp so the max-zoom window stays inside the composite AND
            # still contains the union (the union fits, so a clamped cy that
            # keeps [cy-half, cy+half] over it exists within these bounds)
            cy_min = max(1.0 / (2 * zmax), (y1 - half_h) / ss_h)
            cy_max = min(1 - 1.0 / (2 * zmax), (y0 + half_h) / ss_h)
            if cy_max < cy_min:
                if pad == 0.0:
                    return None
                continue
            return min(max(cy, cy_min), cy_max)
        return None

    ct, cf = comp(tboxes), comp(fboxes)
    cy = fits(ct + cf)
    if cy is None and cf:
        _log("anchor: text+faces union too tall/wide, faces dropped")
        cy = fits(ct)                            # faces drop first, text never
    if cy is None:
        _log(f"anchor: text union exceeds the 1/{zmax} window, center kept")
        return None
    if abs(cy - 0.5) < 1e-3:
        return None                              # center already contains it
    return {"mode": "anchor", "cy": round(cy, 6), "zmax": zmax,
            "ss_w": ss_w, "ss_h": ss_h, "pw": pw, "ph": ph,
            "fit": round(fit, 6), "ox": round(ox, 2), "oy": round(oy, 2)}


# ---- shared window math (renderer + containment gate use the SAME code) ----

def scroll_y_at(plan, t, duration):
    """Window top (supersampled fg px) at time t — the smoothstep the render
    expression implements: s = min(t/D, 1); y = y0 + (y1-y0)*s^2*(3-2s)."""
    s = min(max(t / duration, 0.0), 1.0)
    return plan["y0"] + (plan["y1"] - plan["y0"]) * s * s * (3 - 2 * s)


def window_contains_boxes(plan, boxes, t, duration):
    """True when the rendered window at time t contains every [x,y,w,h]
    panel-px box. Modes: scroll (time-parameterized crop window), anchor
    (guaranteed-visible max-zoom window — conservative for any t), center
    (same math at cy=0.5), contain (static full fit — always True), crop
    (max-zoom window of the text-aware crop)."""
    mode = plan["mode"]
    if mode == "contain":
        # static full-fit letterbox at z=1: the whole panel is visible at
        # every t, so every in-panel box is contained by construction.
        return True
    if mode == "scroll":
        g = plan["fw"] / plan["pw"]
        y = scroll_y_at(plan, t, duration)
        for b in boxes:
            if b[1] * g < y or (b[1] + b[3]) * g > y + plan["ss_h"]:
                return False
        return True
    if mode in ("anchor", "center"):
        ss_w, ss_h = plan["ss_w"], plan["ss_h"]
        pw, ph = plan["pw"], plan["ph"]
        fit = min(ss_w / pw, ss_h / ph)
        ox, oy = (ss_w - pw * fit) / 2.0, (ss_h - ph * fit) / 2.0
        cy = plan.get("cy", 0.5)
        zmax = plan["zmax"]
        half_h, half_w = ss_h / (2.0 * zmax), ss_w / (2.0 * zmax)
        wy0, wy1 = cy * ss_h - half_h, cy * ss_h + half_h
        wx0, wx1 = ss_w / 2.0 - half_w, ss_w / 2.0 + half_w
        for b in boxes:
            bx0, by0 = ox + b[0] * fit, oy + b[1] * fit
            bx1, by1 = bx0 + b[2] * fit, by0 + b[3] * fit
            if bx0 < wx0 or bx1 > wx1 or by0 < wy0 or by1 > wy1:
                return False
        return True
    if mode == "crop":
        cx, cy_, cw, ch = plan["rect"]
        zmax = plan["zmax"]
        ccx, ccy = cx + cw / 2.0, cy_ + ch / 2.0
        hx, hy = cw / (2.0 * zmax), ch / (2.0 * zmax)
        for b in boxes:
            bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
            if bx1 <= cx or bx0 >= cx + cw or by1 <= cy_ or by0 >= cy_ + ch:
                continue      # fully outside the crop: never rendered
            if (bx0 < ccx - hx or bx1 > ccx + hx
                    or by0 < ccy - hy or by1 > ccy + hy):
                return False
        return True
    raise ValueError(f"unknown plan mode {mode!r}")
