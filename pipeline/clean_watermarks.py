#!/usr/bin/env python3
"""clean_watermarks.py — STAGE 3.5 (optional): detect + remove the recurring
scanlation-site watermark (e.g. FLAMESCANS.ORG) from panels. Off by default;
enabled via manga.py --clean-watermarks.

Why this shape: the SAME lockup repeats near-identically across a chapter,
always corner-anchored in the top/bottom ~12% band. So we discover it ONCE per
chapter (vision-assisted, cached), then cheap cv2 template-matching finds every
instance, and removal is crop-when-safe else band-limited Telea inpaint —
the exact recipe clean_bubbles.py already ships for bubble text.

Three phases:
  DISCOVER — cross-panel recurrence scan (corner-anchored text runs that repeat
             on >=3 panels) picks candidate panels; gateway.llm_vision confirms
             the watermark text + region on up to 3 samples. Result cached to
             <panels_dir>/../watermark_template.{json,png}. "No watermark" is
             cached too (negative cache) so re-runs are a no-op. --no-vision
             (or a gateway failure) falls back to pure-CV recurrence consensus.
  MATCH    — two tiers. Tier 1: multi-scale cv2.matchTemplate restricted to
             the top/bottom bands (the usual stamp position). Tier 2 (v2): if
             the band tier finds nothing, scan the FULL panel — the scanlator
             occasionally stamps mid-panel (golden chapter p0066, y=422/593)
             — but demand a much higher score (0.85 vs 0.45-0.50) plus the
             same glyph-dice gates, because mid-panel art is riskier
             false-positive territory. The band restriction stays for
             DISCOVERY only.
  REMOVE   — if the band outside the match is a dead margin (near-uniform),
             crop the band (zero artifact risk); else reflect adjacent rows or
             Telea inpaint (radius 4), clamped to the matched bbox + dilation —
             we NEVER touch pixels outside it. v2: when a strong dark straight
             line (panel frame border) crosses the removal box, the reflect
             mirror is unsafe (a slanted border mirrors at the wrong angle —
             p0117 got a jagged white notch), so we run an edge-continuity
             self-check (Canny along each border line, before vs after):
             reflect that breaks a border falls back to Telea with the visible
             border pixels preserved + the line redrawn across the box; if
             even that breaks continuity, the panel is SKIPPED
             ('skipped_border_risk') — an unremoved watermark is better than a
             broken panel.

Writes panels_clean/<name> for cleaned panels only. panel_render.py /
make_short.py / make_thumbs.py all resolve panels_clean/<name> per-file and
fall through to panels/<name>, so a partial mirror is the convention — and it
composes with clean_bubbles.py: when panels_clean/<name> already exists (bubble
pass), we read THAT as the source and overwrite it, so both cleanups stack.

Usage:
    ./venv/bin/python3 pipeline/clean_watermarks.py \
        --panels output/<slug>/panels [--out <dir>] [--dry-run] [--no-vision]
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np

import gateway

LOG_VERSION = 2           # bump on schema/behavior change -> stale v1 logs redo
BAND_FRAC = 0.12          # top/bottom band where scanlator marks live (gap card)
# Match gates (tuned on the golden chapter: 8/8 known marks accepted, 0 false
# positives on 146 clean panels; best rejected impostor scored 0.52 but failed
# the glyph-overlap dice):
MATCH_THRESHOLD = 0.45    # TM_CCOEFF_NORMED intensity floor
STRONG_THRESHOLD = 0.50   # above this only the white-glyph dice is required
WHITE_DICE_MIN = 0.30     # white glyph pixels must overlap the template's
DARK_DICE_MIN = 0.60      # dark badge overlap, required in the 0.45-0.50 zone
# v2 tier-2 gate: the scanlator occasionally stamps MID-panel (round2 miss
# p0066: y=422 of 593, score 0.914 full-scan). The band restriction stays for
# discovery, but matching falls through to a full-panel scan when the bands
# come up empty. Mid-panel art is riskier false-positive territory, so
# out-of-band matches need a much higher score on top of the same dice gates
# (0.85 rejects the p0144 near-miss at 0.601 while catching p0066 at 0.914).
FULL_SCAN_THRESHOLD = 0.85
# 0.55-1.2: the scanlator stamps a smaller lockup on short filler panels
# (golden chapter: p0117 carries a 0.65x stamp), so the range reaches further
# down than research's 0.8-1.2. Sub-0.8 matches must ALSO pass the dark-badge
# dice unconditionally — small templates blur the gates' discrimination.
SCALES = (0.55, 0.65, 0.8, 0.9, 1.0, 1.1, 1.2)
SMALL_SCALE = 0.8
INPAINT_RADIUS = 4
MASK_DILATE_PX = 6
# v2 border-safety gates (round2 fix: p0117's reflect mirrored panel-exterior
# white over a slanted frame border -> jagged white notch, scene-20 fault
# cluster). A frame border is a LONG, DARK straight stroke that continues
# beyond the removal box; watermark badge/banner edges are straight too but
# live entirely inside the box, so the outside-extension gate rejects them.
BORDER_DARK_MAX = 80      # median gray along the stroke (frame ink is near-black)
BORDER_MIN_INSIDE = 12    # line must meaningfully cross the removal box (px)
BORDER_MIN_OUTSIDE = 8    # ...and continue outside it (kills badge self-edges)
BORDER_STRIP_PX = 3       # preserved strip half-width around a border line
CONTINUITY_MIN = 0.70     # after/before Canny presence ratio along the line
MIN_RECURRENCE = 3        # same corner-anchored run on >=3 panels = watermark
CROP_STD_MAX = 8.0        # "dead margin" uniformity gate (research §2 crop-vs-inpaint)
CROP_MIN_KEEP = 0.90      # never crop below 90% of original height

VISION_INSTRUCTION = (
    "These are manga/manhwa panels. Look ONLY for a scanlation/aggregator "
    "website watermark (a site name/logo like SOMESITE.ORG stamped near the "
    "top or bottom edge). Ignore dialogue, sound effects and chapter titles. "
    "Reply with STRICT JSON only, no prose: "
    '{"found": true/false, "text": "<watermark text or null>", '
    '"instances": [{"image": <1-based index>, "band": "top"|"bottom", '
    '"bbox": [x, y, w, h]}]} '
    "where bbox values are FRACTIONS (0-1) of that image's width/height.")


# ---------------------------------------------------------------- band helpers

def _bands(h, min_px=24):
    """(name, y0, y1) for the top/bottom 12% strips of a panel of height h.

    min_px lets the matcher widen the band just enough to hold the template on
    short panels (segmenter output varies 300-2000 px; the mark has a FIXED
    pixel size, so 12% of a short panel can be thinner than the mark itself).
    Capped at h/3 so the search never wanders into mid-panel art.
    """
    b = min(max(int(h * BAND_FRAC), min_px), max(h // 3, 1))
    return [("top", 0, min(b, h)), ("bottom", max(h - b, 0), h)]


def _band_slice(img, band_name):
    h = img.shape[0]
    for name, y0, y1 in _bands(h):
        if name == band_name:
            return img[y0:y1], y0
    raise ValueError(band_name)


# ---------------------------------------------------------------- discovery

def _glyph_runs(gray_band, thresh, invert):
    """Horizontal runs of letter-sized components at one intensity extreme.

    Site watermarks are stamped in a solid color (white-on-badge or plain
    dark), so thresholding at the extremes isolates their glyphs even on busy
    art, where gradient-based text detection merges everything into one blob
    (verified on golden-chapter bands). Components sharing a baseline within
    a few px and separated by letter-sized gaps are grouped into one run.
    """
    mode = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    _, bw = cv2.threshold(gray_band, thresh, 255, mode)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    glyphs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 5 <= h <= 36 and 1 <= w <= 44 and area >= 8 and w <= 4 * h:
            glyphs.append((int(x), int(y), int(w), int(h)))
    glyphs.sort()
    runs = []
    for g in glyphs:
        placed = False
        for r in runs:
            # same baseline (y within 40% of height) and a small horizontal gap
            if abs(g[1] - r["y"]) <= max(4, int(0.4 * r["h"])) and \
                    0 <= g[0] - (r["x"] + r["w"]) <= 24:
                r["w"] = g[0] + g[2] - r["x"]
                r["h"] = max(r["h"], g[3])
                r["y"] = min(r["y"], g[1])
                r["n"] += 1
                placed = True
                break
        if not placed:
            runs.append({"x": g[0], "y": g[1], "w": g[2], "h": g[3], "n": 1})
    return [r for r in runs if r["n"] >= 4 and 40 <= r["w"] <= 400]


def _text_runs(gray_band, band_w):
    """Corner-anchored, text-line-shaped glyph runs in a band strip.

    Watermarks are the only SMALL horizontal text runs that hug a corner on
    many panels: dialogue is bubble-centered, SFX are huge/irregular. We scan
    both intensity extremes (white lockup text AND dark stamped text).
    """
    runs = (_glyph_runs(gray_band, 210, invert=False) +
            _glyph_runs(gray_band, 60, invert=True))
    out = []
    for r in runs:
        cx = r["x"] + r["w"] / 2.0
        # corner-anchored: run center in the outer thirds of the band width.
        if 0.35 * band_w < cx < 0.65 * band_w:
            continue
        r["corner"] = "l" if cx <= band_w / 2 else "r"
        out.append(r)
    return out


def _prescan(panels):
    """Cross-panel recurrence: cluster corner runs by (band, corner, ~height).

    The watermark lockup has a fixed size, so its runs land in one tight
    cluster; incidental in-art text does not repeat with the same geometry.
    Returns clusters sorted by panel count, each with its member hits.
    """
    clusters = {}
    for p in panels:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        h = img.shape[0]
        for band_name, y0, y1 in _bands(h):
            band = img[y0:y1]
            for r in _text_runs(band, band.shape[1]):
                key = (band_name, r["corner"], round(r["h"] / 8),
                       round(r["w"] / 60))
                clusters.setdefault(key, []).append(
                    {"panel": p, "band": band_name, "run": r, "band_y0": y0})
    out = []
    for key, hits in clusters.items():
        n_panels = len({h["panel"] for h in hits})
        if n_panels >= MIN_RECURRENCE:
            out.append((n_panels, key, hits))
    out.sort(key=lambda t: -t[0])
    return out


def _parse_vision_json(reply):
    """Vision replies wrap JSON in prose/fences sometimes — extract the object."""
    m = re.search(r"\{.*\}", reply, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _lockup_bbox(gray, band_name, run):
    """Full lockup bbox (panel coords) from a tight glyph run in the band.

    Site lockups pair the text with a logo badge to its LEFT (verified on the
    golden chapter: hexagon flame badge + FLAMESCANS.ORG). We extend the run
    leftward to the badge's bright outline when one is present; otherwise a
    small fixed margin. Building the template from the run (not the loose
    vision bbox) is what keeps matching tight — vision boxes routinely include
    surrounding art, which varies per panel and kills the match score.
    """
    band, by0 = _band_slice(gray, band_name)
    bh, bw_ = band.shape[:2]
    x, y, w, h = run["x"], run["y"], run["w"], run["h"]
    # badge search window: up to 6 text-heights left of the run
    wx0 = max(0, x - 6 * h)
    wy0, wy1 = max(0, y - 2 * h), min(bh, y + 3 * h)
    win = band[wy0:wy1, wx0:x]
    left, top, bot = x, y, y + h
    if win.size:
        _, bright = cv2.threshold(win, 200, 255, cv2.THRESH_BINARY)
        n, _, stats, _ = cv2.connectedComponentsWithStats(bright, 8)
        for cx, cy, cw, ch, area in stats[1:]:
            if area >= 20:  # badge outline segments, not noise specks
                left = min(left, wx0 + int(cx))
                top = min(top, wy0 + int(cy))
                bot = max(bot, wy0 + int(cy) + int(ch))
    pad = max(2, h // 4)
    x0 = max(0, left - pad)
    y0 = max(0, top - pad)
    x1 = min(bw_, x + w + pad)
    y1 = min(bh, bot + pad)
    return x0, by0 + y0, x1 - x0, y1 - y0


def _save_template(cache_json, cache_png, meta, template_gray):
    cache_png.parent.mkdir(parents=True, exist_ok=True)
    if template_gray is not None:
        cv2.imwrite(str(cache_png), template_gray)
    cache_json.write_text(json.dumps(meta, indent=2))


def discover(panels, proj_dir, no_vision=False):
    """One-time-per-chapter watermark discovery. Returns meta dict or None.

    Cached to watermark_template.{json,png} next to panels/. A negative result
    ({"found": false}) is cached too, so clean chapters no-op forever after.
    """
    cache_json = proj_dir / "watermark_template.json"
    cache_png = proj_dir / "watermark_template.png"
    if cache_json.exists():
        meta = json.loads(cache_json.read_text())
        if not meta.get("found"):
            print("  [cached] no watermark in this chapter (negative cache)")
            return None
        tmpl = cv2.imread(str(cache_png), cv2.IMREAD_GRAYSCALE)
        if tmpl is not None:
            print(f"  [cached] watermark template '{meta.get('text')}' "
                  f"({meta.get('band')} band)")
            meta["_template"] = tmpl
            return meta
        # png missing/corrupt -> rediscover below

    print("  discovering watermark (once per chapter)...")
    clusters = _prescan(panels)
    if clusters:
        for n, key, _ in clusters[:3]:
            print(f"  recurrence: {key[0]} band, corner '{key[1]}', "
                  f"on {n} panels")
        # one sample per top cluster: the strongest recurrence can be a false
        # positive (repeating art texture), so let vision arbitrate BETWEEN
        # candidate clusters rather than confirm only the strongest one.
        samples, sample_hits = [], {}
        for n, key, hits in clusters[:3]:
            h0 = sorted(hits, key=lambda h: h["panel"].name)[len(hits) // 2]
            if h0["panel"] not in samples:
                samples.append(h0["panel"])
                sample_hits[h0["panel"]] = h0
    else:
        # nothing recurring — blind spread sample so vision can still catch a
        # mark our morphology missed (e.g. logo-only, no text run)
        samples = [panels[0], panels[len(panels) // 2], panels[-1]]
        sample_hits = {}
        print("  recurrence: no repeating corner run; blind vision sample")

    meta = None
    if not no_vision:
        try:
            reply = gateway.llm_vision([str(s) for s in samples],
                                       VISION_INSTRUCTION)
            v = _parse_vision_json(reply)
            if v is None:
                raise RuntimeError("unparseable vision reply")
            if not v.get("found") or not v.get("instances"):
                # vision is the authority when it answers: cache the negative
                # so we never burn calls (or nuke in-art SFX) on this chapter
                print("  vision: no watermark found -> negative cache")
                _save_template(cache_json, cache_png, {"found": False}, None)
                return None
            inst = v["instances"][0]
            src = samples[max(0, min(len(samples) - 1,
                                     int(inst.get("image", 1)) - 1))]
            gray = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
            H, W = gray.shape[:2]
            band = inst.get("band") or "bottom"
            hit = sample_hits.get(src)
            if hit and hit["band"] == band:
                # vision confirms the mark; the CV glyph run gives the TIGHT
                # box (vision bboxes routinely swallow surrounding art, which
                # varies per panel and tanks the template-match score)
                x, y, w, h = _lockup_bbox(gray, band, hit["run"])
            else:
                # no CV run to anchor on: clamp the vision bbox into the band
                fx, fy, fw, fh = inst["bbox"]
                x, y = int(fx * W), int(fy * H)
                w, h = max(8, int(fw * W)), max(8, int(fh * H))
                _, by0 = _band_slice(gray, band)
                y = max(by0, min(y, H - 8))
                h = min(h, H - y)
            tmpl = gray[y:y + h, x:x + w]
            meta = {"found": True, "text": v.get("text"), "band": band,
                    "bbox": [x, y, w, h], "source_panel": src.name,
                    "panel_size": [W, H], "method": "vision"}
        except Exception as e:  # noqa: BLE001 — gateway down must not kill the run
            print(f"  ! vision discovery unavailable ({e}); using CV fallback")

    if meta is None:
        # pure-CV fallback: recurrence consensus, VALIDATED. Recurring runs can
        # be repeating art texture (screentone specks read as "glyphs"), and a
        # texture template matches everywhere — verified on the golden chapter,
        # where the strongest cluster was a false one that matched 69/154
        # panels. A genuine site mark stamps a MINORITY of panels, so accept a
        # candidate only when its matches stay in [MIN_RECURRENCE, 30%].
        if not clusters:
            print("  no watermark found (CV) -> negative cache")
            _save_template(cache_json, cache_png, {"found": False}, None)
            return None
        probe = panels if len(panels) <= 40 else \
            panels[::max(1, len(panels) // 40)]
        for n, key, hits in clusters[:3]:
            hit = sorted(hits, key=lambda h: h["panel"].name)[len(hits) // 2]
            src = hit["panel"]
            gray = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
            x, y, w, h = _lockup_bbox(gray, hit["band"], hit["run"])
            cand_tmpl = gray[y:y + h, x:x + w]
            if cand_tmpl.size == 0 or min(cand_tmpl.shape) < 8:
                continue
            cand = {"band": key[0], "_template": cand_tmpl}
            n_match = sum(1 for p in probe
                          if (g := cv2.imread(str(p), cv2.IMREAD_GRAYSCALE))
                          is not None and match_panel(g, cand))
            frac = n_match / max(1, len(probe))
            print(f"  cv candidate {key[0]}/{key[1]}: matches "
                  f"{n_match}/{len(probe)} probed panels")
            if MIN_RECURRENCE <= n_match and frac <= 0.30:
                tmpl = cand_tmpl
                meta = {"found": True, "text": None, "band": key[0],
                        "bbox": [x, y, w, h], "source_panel": src.name,
                        "panel_size": [gray.shape[1], gray.shape[0]],
                        "method": "cv-recurrence"}
                break
        if meta is None:
            print("  no validated watermark (CV) -> negative cache")
            _save_template(cache_json, cache_png, {"found": False}, None)
            return None

    if tmpl.size == 0 or min(tmpl.shape) < 8:
        print("  ! degenerate template; treating as no watermark")
        _save_template(cache_json, cache_png, {"found": False}, None)
        return None
    _save_template(cache_json, cache_png, meta, tmpl)
    print(f"  template: '{meta.get('text')}' {meta['bbox']} "
          f"({meta['band']} band, from {meta['source_panel']}, "
          f"{meta['method']})")
    meta["_template"] = tmpl
    return meta


# ---------------------------------------------------------------- matching

_DICE_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def _white_mask(gray):
    return cv2.dilate((gray > 190).astype(np.uint8) * 255, _DICE_KERNEL,
                      iterations=2)


def _dark_mask(gray):
    return cv2.dilate((gray < 70).astype(np.uint8) * 255, _DICE_KERNEL,
                      iterations=2)


def _dice(a, b):
    inter = ((a > 0) & (b > 0)).sum()
    return 2.0 * inter / max(1, (a > 0).sum() + (b > 0).sum())


def _best_match(region, y_off, region_name, tmpl, t_white, t_dark):
    """Best multi-scale match inside one search region (band or full panel)."""
    best = None
    for s in SCALES:
        tw, th = int(tmpl.shape[1] * s), int(tmpl.shape[0] * s)
        if tw < 8 or th < 8 or th > region.shape[0] or tw > region.shape[1]:
            continue  # size gate: scaled template must fit inside the region
        t = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best["score"]:
            crop = region[loc[1]:loc[1] + th, loc[0]:loc[0] + tw]
            wd = _dice(_white_mask(crop), cv2.resize(
                t_white, (tw, th), interpolation=cv2.INTER_NEAREST))
            dd = _dice(_dark_mask(crop), cv2.resize(
                t_dark, (tw, th), interpolation=cv2.INTER_NEAREST))
            best = {"score": float(score), "scale": s, "band": region_name,
                    "white_dice": round(wd, 3), "dark_dice": round(dd, 3),
                    "bbox": [int(loc[0]), int(y_off + loc[1]), tw, th]}
    return best


def _gates_ok(best, score_floor, strong_floor):
    """Shared acceptance gates: score tier + glyph-dice discrimination."""
    ok = best["white_dice"] >= WHITE_DICE_MIN and (
        best["score"] >= strong_floor or
        (best["score"] >= score_floor and
         best["dark_dice"] >= DARK_DICE_MIN))
    if best["scale"] < SMALL_SCALE and best["dark_dice"] < DARK_DICE_MIN:
        ok = False  # small-scale matches lose discrimination; demand the badge
    return ok


def match_panel(gray, meta):
    """Best gated template match. None when gated out.

    Score alone can't separate the mark from dark corner art (both hover
    around 0.45-0.55 on busy panels — the mark is semi-transparent over
    varying backgrounds). What IS distinctive is the lockup's glyph pattern:
    the matched crop's bright pixels must overlap the template's bright pixels
    (white text/badge outline, dilated dice), and borderline scores also need
    the dark badge shape to line up.

    Tier 1 searches both top/bottom bands — the scanlator stamps whichever
    edge of the strip a panel got cut from. Tier 2 (v2, round2 fix): when the
    bands find nothing, scan the FULL panel for the occasional mid-panel stamp
    (p0066), gated at FULL_SCAN_THRESHOLD since a lower bar over arbitrary art
    would reopen the false-positive risk the band restriction was guarding.
    """
    tmpl = meta["_template"]
    t_white = _white_mask(tmpl)
    t_dark = _dark_mask(tmpl)
    # band must hold the template with slack: 2x template height. Exactly
    # template-height bands mislocalize marks sitting a few rows off the edge
    # (verified: p0101 matched 0.48@0.8 with a tight band, 0.75@1.0 with
    # slack). The h/3 cap in _bands still keeps the search out of mid-panel.
    min_px = int(tmpl.shape[0] * 2 * max(SCALES)) + 8
    best = None
    for band_name, y0, y1 in _bands(gray.shape[0], min_px=min_px):
        cand = _best_match(gray[y0:y1], y0, band_name, tmpl, t_white, t_dark)
        if cand and (best is None or cand["score"] > best["score"]):
            best = cand
    if best is not None and _gates_ok(best, MATCH_THRESHOLD, STRONG_THRESHOLD):
        return best
    # tier 2: full-panel scan, high bar (mid-panel stamps like p0066)
    full = _best_match(gray, 0, "full", tmpl, t_white, t_dark)
    if full is not None and full["score"] >= FULL_SCAN_THRESHOLD and \
            _gates_ok(full, FULL_SCAN_THRESHOLD, FULL_SCAN_THRESHOLD):
        return full
    return None


# ---------------------------------------------------------------- removal

def _line_points(x1, y1, x2, y2, step=2):
    """Evenly sampled integer points along a segment."""
    n = max(2, int(math.hypot(x2 - x1, y2 - y1) / step))
    return [(int(round(x1 + (x2 - x1) * i / n)),
             int(round(y1 + (y2 - y1) * i / n))) for i in range(n + 1)]


def _extend_to_rect(x1, y1, x2, y2, W, H):
    """Extrapolate a segment to the borders of a WxH rect (clipped)."""
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1:
        return x1, y1, x2, y2
    ux, uy = dx / L, dy / L
    reach = W + H  # long enough to cross any panel
    ax, ay = x1 - ux * reach, y1 - uy * reach
    bx, by = x2 + ux * reach, y2 + uy * reach
    # Liang-Barsky style clip of the infinite line to the rect
    t0, t1 = 0.0, 1.0
    ddx, ddy = bx - ax, by - ay
    for p, q in ((-ddx, ax - 0), (ddx, W - 1 - ax),
                 (-ddy, ay - 0), (ddy, H - 1 - ay)):
        if abs(p) < 1e-9:
            if q < 0:
                return x1, y1, x2, y2
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
    if t0 > t1:
        return x1, y1, x2, y2
    return (int(ax + ddx * t0), int(ay + ddy * t0),
            int(ax + ddx * t1), int(ay + ddy * t1))


def _border_lines(gray, box):
    """Detect panel-frame strokes crossing the removal box (v2, p0117 fix).

    A frame border is a LONG dark straight stroke that both crosses the box
    AND continues well beyond it — the watermark's own badge/banner edges are
    straight too, but they live entirely INSIDE the box, so the
    outside-extension gate rejects them. Returns [(x1, y1, x2, y2, thickness)]
    in panel coords, segments extended across the search window so a redraw
    spans the whole box.
    """
    H, W = gray.shape[:2]
    x0, y0, x1, y1 = box
    pad = 40  # enough surroundings to prove the stroke continues outside
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    sx1, sy1 = min(W, x1 + pad), min(H, y1 + pad)
    sub = gray[sy0:sy1, sx0:sx1]
    if sub.size == 0:
        return []
    edges = cv2.Canny(sub, 50, 150)
    segs = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                           minLineLength=30, maxLineGap=6)
    if segs is None:
        return []
    out = []
    # Hough is score-ordered; 40 is plenty here (reshape: cv2 versions differ
    # on returning (N,1,4) vs (1,N,4))
    for seg in np.asarray(segs).reshape(-1, 4)[:40]:
        a, b, c, d = (int(v) for v in seg)
        # gate on the RAW segment, not an extrapolation: the watermark's own
        # banner/badge edges are straight but their segments live entirely
        # inside the box; a frame stroke's segment physically continues past
        # it. (Extrapolated lines "continue" over any dark art and over-fire —
        # v2 first cut skipped all 8 known-good panels that way.)
        inside, outside, dark_out, contrast = 0, 0, [], 0
        for px, py in _line_points(a + sx0, b + sy0, c + sx0, d + sy0):
            if not (0 <= px < W and 0 <= py < H):
                continue
            if x0 <= px < x1 and y0 <= py < y1:
                inside += 1
            else:
                outside += 1
                # perpendicular window: the Hough segment rides the stroke's
                # EDGE, not its centerline
                win = gray[max(0, py - 9):py + 10, max(0, px - 9):px + 10]
                dark_out.append(int(win.min()))
                # flank contrast: frame INK sits against paper/gutter/art
                # that is clearly lighter. Dark streaks inside dark art (the
                # p0080 motion lines, p0094 drapes) have no bright flank and
                # must NOT count as borders — that over-fire skipped all 8
                # known-good panels in the first v2 cut.
                if int(win.max()) - int(win.min()) >= 90:
                    contrast += 1
        # gates: crosses the box, continues outside it, is frame-dark OUTSIDE
        # the box (inside may be covered by the watermark itself), and shows
        # ink-vs-flank contrast on most of its outside length
        if inside * 2 < BORDER_MIN_INSIDE or outside * 2 < BORDER_MIN_OUTSIDE:
            continue
        if not dark_out or float(np.median(dark_out)) > BORDER_DARK_MAX:
            continue
        if contrast < 0.6 * outside:
            continue
        # only now extend across the window so redraw/continuity span the box
        ex1, ey1, ex2, ey2 = _extend_to_rect(a, b, c, d,
                                             sx1 - sx0, sy1 - sy0)
        pts = _line_points(ex1 + sx0, ey1 + sy0, ex2 + sx0, ey2 + sy0)
        # stroke thickness: median dark run perpendicular to the line at
        # outside sample points (frame ink is a band, not a hairline)
        nx, ny = -(ey2 - ey1), (ex2 - ex1)
        nl = math.hypot(nx, ny) or 1.0
        nx, ny = nx / nl, ny / nl
        runs = []
        for px, py in pts[:: max(1, len(pts) // 12)]:
            if x0 <= px < x1 and y0 <= py < y1:
                continue
            run = 0
            for t in range(-15, 16):
                qx, qy = int(px + nx * t), int(py + ny * t)
                if 0 <= qx < W and 0 <= qy < H and gray[qy, qx] < BORDER_DARK_MAX:
                    run += 1
            if run:
                runs.append(run)
        thick = int(np.median(runs)) if runs else 3
        out.append((ex1 + sx0, ey1 + sy0, ex2 + sx0, ey2 + sy0,
                    max(2, min(thick, 30))))
    # dedupe near-identical strokes (Hough returns both edges of a thick band)
    dedup = []
    for ln in out:
        mx, my = (ln[0] + ln[2]) / 2, (ln[1] + ln[3]) / 2
        ang = math.atan2(ln[3] - ln[1], ln[2] - ln[0]) % math.pi
        dup = any(abs(mx - (o[0] + o[2]) / 2) < 12 and
                  abs(my - (o[1] + o[3]) / 2) < 12 and
                  min(abs(ang - oa), math.pi - abs(ang - oa)) < 0.12
                  for o, oa in dedup)
        if not dup:
            dedup.append((ln, ang))
    return [ln for ln, _ in dedup[:6]]


def _edge_presence(gray, lines, box):
    """Per-line fraction of in-box sample points with stroke evidence nearby.

    The self-check metric: a continuous frame stroke keeps a Canny edge AND
    dark ink along the whole crossing; a notch (p0117: white mirrored over
    the border) leaves a gap in both. The search window scales with the
    stroke thickness because the Hough segment rides the stroke's EDGE while
    a redraw centers on it — a fixed 2 px window misses legitimately-shifted
    edges and false-fails the repair.
    """
    H, W = gray.shape[:2]
    edges = cv2.Canny(gray, 50, 150)
    x0, y0, x1, y1 = box
    fracs = []
    for lx1, ly1, lx2, ly2, thick in lines:
        r = thick // 2 + 4
        pts = [(px, py) for px, py in _line_points(lx1, ly1, lx2, ly2)
               if x0 <= px < x1 and y0 <= py < y1 and
               0 <= px < W and 0 <= py < H]
        if not pts:
            fracs.append(1.0)
            continue
        hit = 0
        for px, py in pts:
            win_e = edges[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1]
            win_g = gray[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1]
            if win_e.any() and win_g.min() < BORDER_DARK_MAX + 40:
                hit += 1
        fracs.append(hit / len(pts))
    return fracs


def _continuity_ok(before_gray, after_gray, lines, box):
    """True iff every border line is at least as continuous after removal."""
    before = _edge_presence(before_gray, lines, box)
    after = _edge_presence(after_gray, lines, box)
    return all(a >= CONTINUITY_MIN * b for a, b in zip(after, before))


def _clone_border_crossing(result, img, box, line, wm_bbox):
    """Reconstruct one frame stroke across the box by profile cloning.

    A uniform cv2.line redraw is WRONG for real frame borders: they are a
    cross-SECTION (white gutter + black ink band + fill shading on p0133),
    not a flat stroke, and flattening them reads as damage. Instead, for
    every in-box point along the line we clone the perpendicular pixel
    profile from the nearest point OUTSIDE the box (where the original
    stroke is intact) — a clone-stamp of the border through the crossing.
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    lx1, ly1, lx2, ly2, thick = line
    dx, dy = lx2 - lx1, ly2 - ly1
    L = math.hypot(dx, dy)
    if L < 1:
        return
    nx, ny = -dy / L, dx / L                    # unit normal
    pts = _line_points(lx1, ly1, lx2, ly2, step=1)
    inside = [i for i, (px, py) in enumerate(pts)
              if x0 <= px < x1 and y0 <= py < y1]
    if not inside:
        return
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def _anchor_ok(i):
        # a valid source point must (a) be in-image, (b) actually contain
        # stroke ink. Without (b), p0117's bottom bar cloned from the
        # panel-EXTERIOR white left of its vertical border — recreating the
        # very notch this path exists to prevent.
        px, py = pts[i]
        if not (0 <= px < W and 0 <= py < H):
            return False
        win = gray[max(0, py - 4):py + 5, max(0, px - 4):px + 5]
        return win.size > 0 and int(win.min()) < BORDER_DARK_MAX

    # source anchors: first valid outside point on each side of the crossing
    # (3 px clearance keeps watermark-AA fringe out of the clone)
    lo = next((i for i in range(inside[0] - 3, -1, -1) if _anchor_ok(i)),
              None)
    hi = next((i for i in range(inside[-1] + 3, len(pts)) if _anchor_ok(i)),
              None)
    if lo is None and hi is None:
        return
    # only overwrite where the WATERMARK actually was (tight match bbox +
    # small pad): the rest of the removal box still holds original pixels the
    # inpaint mask spared, and cloning over them can only do harm (verified:
    # an unrestricted clone repainted p0117's intact bar left of the mark)
    mx, my, mw, mh = wm_bbox
    pad = 4
    half = thick // 2 + BORDER_STRIP_PX + 2
    for i in inside:
        j = lo if hi is None or (lo is not None and
                                 i - lo <= hi - i) else hi
        px, py = pts[i]
        if not (mx - pad <= px < mx + mw + pad and
                my - pad <= py < my + mh + pad):
            continue
        qx, qy = pts[j]
        for t in range(-half, half + 1):
            ox, oy = int(round(nx * t)), int(round(ny * t))
            sx_, sy_ = qx + ox, qy + oy
            tx_, ty_ = px + ox, py + oy
            if 0 <= sx_ < W and 0 <= sy_ < H and \
                    x0 <= tx_ < x1 and y0 <= ty_ < y1:
                result[ty_, tx_] = img[sy_, sx_]


def _inpaint_preserve_borders(img, gray, box, lines, wm_bbox):
    """Band-limited Telea that keeps frame strokes intact (v2, p0117 fix).

    The mask excludes a BORDER_STRIP_PX strip around each detected stroke
    wherever the strip is still frame-dark (preserving real border ink with
    its anti-aliasing, and anchoring Telea's boundary condition); where the
    watermark covers the stroke the strip IS inpainted, then the stroke is
    reconstructed by cloning its cross-profile from outside the box — so the
    frame line always survives the crossing.
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    mask = np.zeros((H, W), np.uint8)
    mask[y0:y1, x0:x1] = 255
    keep = np.zeros((H, W), np.uint8)
    for lx1, ly1, lx2, ly2, thick in lines:
        cv2.line(keep, (lx1, ly1), (lx2, ly2), 255,
                 thickness=thick + 2 * BORDER_STRIP_PX)
    # preserve only where the strip still shows frame ink (dark); watermark
    # pixels sitting ON the line must still be inpainted + reconstructed
    dark = (gray < BORDER_DARK_MAX + 40).astype(np.uint8) * 255
    mask[(keep > 0) & (dark > 0)] = 0
    result = cv2.inpaint(img, mask, INPAINT_RADIUS, cv2.INPAINT_TELEA)
    for line in lines:
        _clone_border_crossing(result, img, box, line, wm_bbox)
    return result


def _try_crop(img, bbox, band):
    """Trim the band instead of inpainting when it is a dead margin.

    Safe iff the band rows around the mark (excluding the mark's columns) are
    near-uniform (gutter/letterbox) AND >=90% of the height survives — then
    cropping is strictly better: zero artifacts, zero cost (research §2).
    """
    H = img.shape[0]
    x, y, w, h = bbox
    if band == "bottom":
        cut = y - MASK_DILATE_PX
        strip = img[max(0, cut):]
        keep = cut
    else:
        cut = y + h + MASK_DILATE_PX
        strip = img[:min(H, cut)]
        keep = H - cut
    if keep < CROP_MIN_KEEP * H or keep < 200:
        return None
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    outside = np.ones(gray.shape, bool)
    sx0, sx1 = max(0, x - MASK_DILATE_PX), min(gray.shape[1], x + w + MASK_DILATE_PX)
    outside[:, sx0:sx1] = False
    vals = gray[outside]
    if vals.size == 0 or vals.std() > CROP_STD_MAX:
        return None
    if not (vals.mean() < 30 or vals.mean() > 225):
        return None
    return img[:cut] if band == "bottom" else img[cut:]


def _removal_box(img, bbox):
    """Matched bbox + MASK_DILATE_PX, snapped to the panel edge when close.

    The lockup is OPAQUE (banner + badge), so removal must cover the whole
    matched rect — a glyph-only stroke mask leaves the banner behind (verified
    on golden-chapter crops). Snapping to a nearby edge avoids leaving a thin
    sliver of banner between the box and the panel border.
    """
    H, W = img.shape[:2]
    x, y, w, h = bbox
    # proportional dilation: the badge's pointy outline extends a little past
    # the discovered template crop (its dark tip evades the bright-outline
    # search), and a fixed 6 px left a residue sliver on golden-chapter crops
    dx = int(0.15 * w) + MASK_DILATE_PX
    dy = int(0.20 * h) + MASK_DILATE_PX
    x0, y0 = max(0, x - dx), max(0, y - dy)
    x1, y1 = min(W, x + w + dx), min(H, y + h + dy)
    if y0 <= 24:
        y0 = 0
    if H - y1 <= 24:
        y1 = H
    return x0, y0, x1, y1


def _reflect(img, box):
    """Mirror adjacent rows into the box; None when no clean mirror source."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    bh = y1 - y0
    src = None
    if y0 == 0 and y1 + bh <= H:
        src = img[y1:y1 + bh][::-1]      # top-edge mark: mirror rows below
    elif y1 == H and y0 - bh >= 0:
        src = img[y0 - bh:y0][::-1]      # bottom-edge mark: mirror rows above
    if src is None:
        return None
    result = img.copy()
    result[y0:y1, x0:x1] = src[:, x0:x1]
    # feather the box borders so the mirrored patch blends into the
    # surrounding art (2 px outside is within the dilation allowance)
    seams = np.zeros((H, W), np.uint8)
    seams[y0:y1, max(0, x0 - 2):min(W, x0 + 3)] = 255
    seams[y0:y1, max(0, x1 - 3):min(W, x1 + 2)] = 255
    if y0 > 0:
        seams[max(0, y0 - 2):y0 + 3, x0:x1] = 255
    if y1 < H:
        seams[y1 - 3:min(H, y1 + 2), x0:x1] = 255
    return cv2.inpaint(result, seams, INPAINT_RADIUS, cv2.INPAINT_TELEA)


def remove_watermark(img, bbox, band, meta):
    """Remove the matched instance; returns (result_img_or_None, method).

    Cheapest-safe-first ladder (research §2 stage 3):
      crop     — band is a dead margin: trim it, zero artifact risk.
      reflect  — mark touches the panel edge and enough rows exist on the
                 art side: mirror them into the box. Continues the local
                 texture where Telea produces a visible directional smear
                 on this box size (~130x50 px is far past Telea's thin-stroke
                 comfort zone). Seams get a thin feathering inpaint.
      inpaint  — fallback full-rect Telea (radius 4).
    All methods modify ONLY the matched bbox + dilation.

    v2 border safety (round2 fix, p0117): when a panel-frame stroke crosses
    the removal box, a mirror at the wrong angle chops the frame (jagged
    white notch -> scene-20 fault cluster). So with border lines present the
    ladder becomes: reflect -> Canny edge-continuity self-check along each
    line -> on break, border-preserving Telea (mask spares a 3 px strip of
    frame ink, stroke redrawn across the box) -> re-check -> on break again,
    return (None, "skipped_border_risk"): an unremoved watermark is better
    than a broken panel.
    """
    cropped = _try_crop(img, bbox, band)
    if cropped is not None:
        return cropped, "crop"
    H, W = img.shape[:2]
    box = _removal_box(img, bbox)
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lines = _border_lines(gray, box)

    reflected = _reflect(img, box)
    if reflected is not None:
        if not lines:
            return reflected, "reflect"
        after = cv2.cvtColor(reflected, cv2.COLOR_BGR2GRAY)
        if _continuity_ok(gray, after, lines, box):
            return reflected, "reflect"
        # reflect broke a frame stroke -> fall through to the safe path

    if lines:
        result = _inpaint_preserve_borders(img, gray, box, lines, bbox)
        after = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        if _continuity_ok(gray, after, lines, box):
            return result, "inpaint-border"
        return None, "skipped_border_risk"

    mask = np.zeros((H, W), np.uint8)
    mask[y0:y1, x0:x1] = 255
    result = cv2.inpaint(img, mask, INPAINT_RADIUS, cv2.INPAINT_TELEA)
    return result, "inpaint"


# ---------------------------------------------------------------- main loop

def clean(panels_dir, out_dir=None, dry_run=False, no_vision=False):
    panels_dir = Path(panels_dir)
    panels = sorted(panels_dir.glob("p*.png"), key=lambda p: p.name)
    if not panels:
        sys.exit(f"no panels in {panels_dir}")
    proj = panels_dir.parent
    out_dir = Path(out_dir) if out_dir else proj / "panels_clean"
    log_path = proj / "watermark_log.json"

    # idempotency: a previous run's log + intact outputs = nothing to do.
    # (The template cache alone already makes re-discovery free; this skips
    # the per-panel matching pass too.)
    if not dry_run and log_path.exists():
        try:
            log = json.loads(log_path.read_text())
            # cleaned panels are the ones with files on disk; skipped panels
            # ("skipped_border_risk") intentionally have no output to verify
            done = [e for e in log.get("panels", [])
                    if e.get("method") and
                    not e["method"].startswith("skipped")]
            # version gate: v1 logs (no "version" key) predate the border
            # self-check and full-panel tier -> redo the pass so their
            # outputs get re-audited under the new rules
            if log.get("version") == LOG_VERSION and \
                    log.get("out_dir") == str(out_dir) and \
                    len(log.get("panels", [])) == len(panels) and \
                    all((out_dir / e["panel"]).exists() for e in done):
                print(f"  [cached] watermark pass already done "
                      f"({len(done)} cleaned) -> {out_dir}")
                return 0
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # stale/corrupt log -> redo the pass

    meta = discover(panels, proj, no_vision=no_vision)
    if meta is None:
        if not dry_run:
            log_path.write_text(json.dumps(
                {"version": LOG_VERSION, "out_dir": str(out_dir),
                 "template": None,
                 "panels": [{"panel": p.name, "method": None} for p in panels]},
                indent=2))
        return 0

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    n_cleaned = 0
    for i, p in enumerate(panels, 1):
        entry = {"panel": p.name, "method": None}
        try:
            # compose with clean_bubbles: if a bubble-cleaned copy exists,
            # THAT is the source (and the destination) — the cleanups stack.
            src = out_dir / p.name if (out_dir / p.name).exists() else p
            gray = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise RuntimeError("unreadable")
            m = match_panel(gray, meta)
            if m:
                entry.update(score=round(m["score"], 3), scale=m["scale"],
                             band=m["band"], white_dice=m["white_dice"],
                             dark_dice=m["dark_dice"], bbox=m["bbox"])
                if dry_run:
                    entry["method"] = "dry-run"
                    print(f"  [{i}/{len(panels)}] {p.name} MATCH "
                          f"score={m['score']:.3f} scale={m['scale']} "
                          f"bbox={m['bbox']} (dry-run, not written)")
                    n_cleaned += 1
                else:
                    img = cv2.imread(str(src))
                    result, method = remove_watermark(img, m["bbox"],
                                                      m["band"], meta)
                    entry["method"] = method
                    if result is None:
                        # border self-check failed on every removal path:
                        # keep the original — an unremoved watermark is
                        # better than a broken panel frame (round2 p0117)
                        print(f"  [{i}/{len(panels)}] {p.name} MATCH "
                              f"score={m['score']:.3f} bbox={m['bbox']} "
                              f"-> {method} (original kept)")
                    else:
                        cv2.imwrite(str(out_dir / p.name), result)
                        print(f"  [{i}/{len(panels)}] {p.name} MATCH "
                              f"score={m['score']:.3f} bbox={m['bbox']} "
                              f"-> {method}")
                        n_cleaned += 1
            # no match -> write nothing: panel_render falls through to
            # panels/<name> per-file, so a partial panels_clean/ is correct
        except Exception as e:  # noqa: BLE001 — one bad panel must not kill the run
            print(f"  ! {p.name} watermark pass failed ({e}); original kept")
            entry["error"] = str(e)
        entries.append(entry)

    if not dry_run:
        log_path.write_text(json.dumps(
            {"version": LOG_VERSION, "out_dir": str(out_dir),
             "template": {k: v for k, v in meta.items() if k != "_template"},
             "panels": entries}, indent=2))
    print(f"  -> {n_cleaned}/{len(panels)} panels "
          f"{'would be ' if dry_run else ''}cleaned -> {out_dir}")
    return n_cleaned


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panels", required=True)
    ap.add_argument("--out", default=None,
                    help="output dir (default: <panels>/../panels_clean)")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover + match + log decisions; write no panels")
    ap.add_argument("--no-vision", action="store_true",
                    help="skip gateway vision; pure-CV recurrence discovery")
    args = ap.parse_args()
    try:
        clean(args.panels, args.out, dry_run=args.dry_run,
              no_vision=args.no_vision)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # graceful fallback: an optional cleanup stage must never fail the
        # pipeline — originals stay in use and the run continues.
        print(f"  ! clean_watermarks failed ({e}); panels left untouched")
        sys.exit(0)


if __name__ == "__main__":
    main()
