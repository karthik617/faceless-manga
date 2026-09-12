#!/usr/bin/env python3
"""layout_smart2.py — smart layout v2: magazine-style content-weighted
composition (gap-013 / exp-013-smart-layout-v2). OPT-IN via --layout smart2.

v1 (layout_smart.py, adopted gap-002) splits scenes into UNIFORM cells: three
equal columns or equal accordion rows. Text-heavy panels get the same area as
splash panels, only two template shapes exist, and the only motion is on the
whole composite. v2 fixes all three per the six user reference samples
(improvements/experiments/exp-013-smart-layout-v2/reference_samples/):

  * TEMPLATE LIBRARY — hero_right / hero_left / mag_grid / strips / rail,
    selected by panel count, aspect ratios and narration beat structure, with
    a round-robin recency penalty so consecutive smart scenes vary (>=3
    distinct templates per chapter is an acceptance criterion).
  * CONTENT-WEIGHTED SIZING — the key inversion vs v1: v1 sized cells
    uniformly then REJECTED templates when the readability guard failed; v2
    solves each cell's minimum height (exp-009's MIN_TEXT_H_1080=40px floor
    via layout_smart._panel_min_h) as a HARD constraint first, then
    distributes the remaining area by importance
        W = 0.40*beat_share + 0.35*text_density + 0.25*position
    so a text cell is either big enough or the template is infeasible —
    never a small text cell (ch3's 3 MEDIUM phone_readability faults).
  * PER-CELL KEN BURNS — the ACTIVE (highlighted) cell drifts across a 10%
    overscan during its beat; resting cells stay static/dimmed exactly like
    v1. Drift only engages when we KNOW no bubble sits in the overscan
    margin (OCR says text-free, or measured text boxes clear the margin) —
    stricter than the research draft, because "no bubble cropping at cell
    edges" is a hard quality guard.

Fallback chain (research §3.4): v2 templates -> v1 plan_layout (grid/stack,
adopted behavior) -> None (caller uses sequential cuts). plan_layout_v2 is a
pure planner like v1's: nothing here runs unless --layout smart2 is set, so
the default pipeline never imports this module's render path.

The mag_grid row math is a Python port of the row-partition idea in Flickr's
justified-layout (https://github.com/flickr/justified-layout, MIT License,
Copyright (c) Yahoo/Flickr) — rows are "justified" so aspect-scaled cell
widths fill the row width exactly; we extend it with per-cell importance
weights. Attribution kept per MIT terms; no code was copied verbatim.

CLI (smoke tests / sample generation — renders with synthetic silence):
    ./venv/bin/python3 pipeline/layout_smart2.py --panels p1.png p2.png ... \
        --out sample.mp4 [--duration 12] [--narration "..."] [--plan-only]
"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# v1 is the substrate: constants, guards and the beat mapper are REUSED so
# the two planners can never drift apart on what "readable" means.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout_smart as ls
from layout_smart import (
    Cell, LayoutPlan, GUTTER, DIM, SS, MIN_BEAT_SEC, MAX_GRID, WIDE_AR,
    ACCORDION_H, SLIDE_SEC, SLIDE_PX, BORDER_T, ACCENT, ZOOM_END,
    BREATHE_AMP, BREATHE_SEC, PULSE_ON, PULSE_SEC, PUNCH_AFTER, PUNCH_LEN,
    GRID_LEN, REVEAL_GRID, MIN_SHOT, SPAN_MAX, PUNCH_FILL,
    _fit, _center, _beat_times, _panel_has_text, _panel_min_h,
)
import make_video as mv   # already on sys.path via layout_smart's insert

# ---- v2 constants (research §3) ----
W_BEAT, W_TEXT, W_POS = 0.40, 0.35, 0.25   # importance weight mix
HERO_MIN = 0.5          # hero score threshold to trigger hero_* templates
HERO_AR_MAX = 1.2       # hero panel must be portrait/square (fills a column)
HERO_W_LO, HERO_W_HI = 0.40, 0.62   # hero column width band (sample ratios)
RAIL_SCENE_W = 0.74     # rail: scene panel width fraction (samples: 70-78%)
RAIL_WIDE_AR = 1.3      # rail scene panel must be at least this wide
RAIL_TALL_AR = 0.9      # rail column panel must be at most this wide
SPLASH_DENSITY = 0.02   # text density below this + big art = splash panel
DRIFT_OVERSCAN = 1.10   # active-cell bright PNG renders at 1.10x its rect
DRIFT_MIN_BEAT = 2.0    # no drift on beats shorter than this (wobble guard)
DRIFT_MARGIN = 0.10     # measured text boxes must clear this fraction of
                        # every panel edge or the cell renders static — a
                        # drifting crop must never clip a bubble (hard guard)
STRIP_W_LO, STRIP_W_HI = 0.6, 1.6   # strips: weighted row heights clamped to
                                    # this band around the equal share so no
                                    # resting row degenerates into a sliver

# round-robin tiebreak state: template names in selection order, one process
# = one chapter render (panel_render imports once), so consecutive smart
# scenes see each other's picks. Reset via reset_rotation() for tests.
_RECENT = []


def reset_rotation():
    _RECENT.clear()


def _rotation_penalty(tname):
    """Recency penalty for the round-robin tiebreak: the template used by
    the PREVIOUS smart scene is discounted hardest, anything in the last
    three picks mildly — enough to break area ties toward variety without
    ever overriding a feasibility difference."""
    if _RECENT and _RECENT[-1] == tname:
        return 0.75
    if tname in _RECENT[-3:]:
        return 0.90
    return 1.0


# ---------------------------------------------------------------------------
# importance weights (research §3.2) — all inputs already exist, no new ML
# ---------------------------------------------------------------------------

def _text_density(panel, size, ocr, text_boxes):
    """Fraction of panel area covered by measured text-line boxes. Without
    measured boxes we fall back to a coarse binary prior from the OCR sidecar
    (text-bearing ~ a typical dialogue page's ~10% coverage, text-free 0) so
    the weight formula still separates talky panels from splash art."""
    pw, ph = size
    boxes = (text_boxes or {}).get(Path(panel).name)
    if boxes:
        return min(sum(b[2] * b[3] for b in boxes) / float(pw * ph), 1.0)
    return 0.10 if _panel_has_text(panel, ocr) else 0.0


def _weights(panels, sizes, beats, duration, ocr, text_boxes):
    """Per-panel (W, hero) scores. W drives slack distribution AFTER min
    heights are satisfied; hero picks the dominant-column templates."""
    n = len(panels)
    starts = list(beats)
    ends = starts[1:] + [duration]
    beat_share = [max(e - s, 0.0) / max(duration, 1e-6)
                  for s, e in zip(starts, ends)]
    dens = [_text_density(p, s, ocr, text_boxes)
            for p, s in zip(panels, sizes)]
    dmax = max(dens) if max(dens) > 0 else 1.0
    dens_n = [d / dmax for d in dens]
    pos = [1.0 if k == n - 1 else (0.6 if k == 0 else 0.3) for k in range(n)]
    areas = sorted(pw * ph for pw, ph in sizes)
    med_area = areas[len(areas) // 2]
    W, hero = [], []
    for k in range(n):
        pw, ph = sizes[k]
        splash = 1.0 if (dens[k] < SPLASH_DENSITY
                         and pw * ph >= med_area) else 0.0
        W.append(W_BEAT * beat_share[k] + W_TEXT * dens_n[k] + W_POS * pos[k])
        hero.append(0.6 * splash + 0.4 * pos[k])
    return W, hero


# ---------------------------------------------------------------------------
# constrained solvers: min heights are HARD, slack is weight-proportional
# ---------------------------------------------------------------------------

def _stack_in_column(panels, sizes, req_px, weights, x0, y0, col_w, col_h):
    """Stack panels vertically inside a column, giving every panel its
    required minimum displayed height FIRST and splitting the remaining
    height by weight. Returns [(x,y,dw,dh) display rects] or None when the
    mins alone don't fit (template infeasible -> fallback chain)."""
    m = len(panels)
    avail = col_h - (m - 1) * GUTTER
    if sum(req_px) > avail:
        return None
    for (pw, ph), r in zip(sizes, req_px):
        # width-limited ceiling: a very wide panel in a narrow column can
        # never reach its min height no matter how tall its cell is
        if col_w * ph / pw < r:
            return None
    slack = avail - sum(req_px)
    sw = sum(weights) or 1.0
    rects, y = [], y0
    for (pw, ph), r, wt in zip(sizes, req_px, weights):
        cell_h = int(r + slack * wt / sw)
        dw, dh = _fit(pw, ph, col_w, cell_h)
        if dh < r - 1:      # -1: integer rounding tolerance
            return None
        rects.append(_center(x0, y, col_w, cell_h, dw, dh))
        y += cell_h + GUTTER
    return rects


def _justify_row(sizes, weights, req_px, row_w, row_h, x0, y0):
    """One justified row: cells fill the row width exactly with no dead
    space — the row-partition idea ported from Flickr's justified-layout
    (MIT, see module docstring), extended with (a) importance weights and
    (b) the same mins-first inversion as the column solver: every cell is
    floored at the WIDTH its min height demands (dh = cw/ar must reach
    req), the remaining width splits by aspect*weight. Panels fit-inside
    their cell (never cropped). Returns [(x,y,dw,dh)] or None when the
    min widths alone overflow the row (row infeasible)."""
    m = len(sizes)
    inner_w = row_w - (m - 1) * GUTTER
    ars = [pw / ph for pw, ph in sizes]
    # width each cell needs for its panel to reach req at row height
    min_ws = [min(r, row_h) * a for r, a in zip(req_px, ars)]
    if sum(min_ws) > inner_w:
        return None
    slack = inner_w - sum(min_ws)
    keys = [a * max(w, 1e-6) for a, w in zip(ars, weights)]
    ks = sum(keys)
    rects, x = [], x0
    for j, ((pw, ph), key, mw) in enumerate(zip(sizes, keys, min_ws)):
        cw = int(mw + slack * key / ks) if j < m - 1 else (x0 + row_w - x)
        dw, dh = _fit(pw, ph, cw, row_h)
        rects.append(_center(x, y0, cw, row_h, dw, dh))
        x += cw + GUTTER
    return rects


# ---------------------------------------------------------------------------
# templates — each returns [Cell] in reading order, or None when infeasible
# ---------------------------------------------------------------------------

def _hero(panels, sizes, w, h, req_px, weights, hero_scores, side):
    """hero_right (samples 1/6): dominant full-height column on the right,
    the OTHER panels stacked weight-proportionally on the left. The hero is
    the LAST panel (payoff) so left->right reading order holds. hero_left is
    the mirror for an ESTABLISHING splash (hero = first panel)."""
    n = len(panels)
    if n < 2 or n > 3:
        return None
    hk = n - 1 if side == "right" else 0
    pw, ph = sizes[hk]
    if hero_scores[hk] < HERO_MIN or pw / ph > HERO_AR_MAX:
        return None
    share = weights[hk] / (sum(weights) or 1.0)
    frac = min(max(HERO_W_LO + 0.45 * (share - 0.33), HERO_W_LO), HERO_W_HI)
    hero_w = int(frac * w)
    col_w = w - 3 * GUTTER - hero_w
    hx = w - GUTTER - hero_w if side == "right" else GUTTER
    cx = GUTTER if side == "right" else 2 * GUTTER + hero_w
    hdw, hdh = _fit(pw, ph, hero_w, h - 2 * GUTTER)
    if hdh < req_px[hk] - 1:
        return None
    hero_rect = _center(hx, GUTTER, hero_w, h - 2 * GUTTER, hdw, hdh)
    rest_idx = [k for k in range(n) if k != hk]
    rects = _stack_in_column([panels[k] for k in rest_idx],
                             [sizes[k] for k in rest_idx],
                             [req_px[k] for k in rest_idx],
                             [weights[k] for k in rest_idx],
                             cx, GUTTER, col_w, h - 2 * GUTTER)
    if rects is None:
        return None
    out, ri = [], 0
    for k in range(n):
        r = hero_rect if k == hk else rects[ri]
        if k != hk:
            ri += 1
        out.append(Cell(panel=panels[k], rest=r, hi=r))
    return out


def _rail(panels, sizes, w, h, req_px, weights):
    """rail (sample 6): one large wide scene panel (~74% width) + a narrow
    full-height column panel as the payoff rail. n=2 only; a degenerate
    hero_right kept as its own name for the variety metric."""
    if len(panels) != 2:
        return None
    (aw, ah), (bw, bh) = sizes
    if aw / ah < RAIL_WIDE_AR or bw / bh > RAIL_TALL_AR:
        return None
    scene_w = int(RAIL_SCENE_W * w) - 2 * GUTTER
    rail_w = w - 3 * GUTTER - scene_w
    sdw, sdh = _fit(aw, ah, scene_w, h - 2 * GUTTER)
    rdw, rdh = _fit(bw, bh, rail_w, h - 2 * GUTTER)
    if sdh < req_px[0] - 1 or rdh < req_px[1] - 1:
        return None
    s_rect = _center(GUTTER, GUTTER, scene_w, h - 2 * GUTTER, sdw, sdh)
    r_rect = _center(2 * GUTTER + scene_w, GUTTER, rail_w, h - 2 * GUTTER,
                     rdw, rdh)
    return [Cell(panel=panels[0], rest=s_rect, hi=s_rect),
            Cell(panel=panels[1], rest=r_rect, hi=r_rect)]


def _mag_grid(panels, sizes, w, h, req_px, weights):
    """mag_grid (samples 2/4): 3 panels in 2 justified rows (2+1 or 1+2).
    Row heights get their members' max required height FIRST, the remainder
    splits by summed row weight; within a row the ported justified split
    sizes widths by aspect*weight. Tries both groupings, keeps the feasible
    one with the larger displayed area."""
    if len(panels) != 3:
        return None
    best, best_area = None, -1
    for split in ((2, 1), (1, 2)):
        g1 = list(range(split[0]))
        g2 = list(range(split[0], 3))
        avail = h - 3 * GUTTER
        reqs = [max(req_px[k] for k in g) for g in (g1, g2)]
        if sum(reqs) > avail:
            continue
        rw = [sum(weights[k] for k in g) for g in (g1, g2)]
        slack = avail - sum(reqs)
        sw = sum(rw) or 1.0
        rhs = [int(r + slack * x / sw) for r, x in zip(reqs, rw)]
        rows, ok, y = [], True, GUTTER
        for g, rh in zip((g1, g2), rhs):
            rects = _justify_row([sizes[k] for k in g],
                                 [weights[k] for k in g],
                                 [req_px[k] for k in g],
                                 w - 2 * GUTTER, rh, GUTTER, y)
            if rects is None:
                ok = False
                break
            for k, (_, _, dw, dh) in zip(g, rects):
                if dh < req_px[k] - 1:
                    ok = False
            rows.append(rects)
            y += rh + GUTTER
        if not ok:
            continue
        rects = rows[0] + rows[1]
        area = sum(r[2] * r[3] for r in rects)
        if area > best_area:
            best_area = area
            best = [Cell(panel=p, rest=r, hi=r)
                    for p, r in zip(panels, rects)]
    return best


def _strips(panels, sizes, w, h, req_px, weights, min_hs):
    """strips (samples 3/4): v1's accordion stack with WEIGHT-PROPORTIONAL
    resting row heights (clamped to a band around the equal share so no row
    degenerates). The highlighted pop keeps v1's exact guard: the bright
    copy at ACCORDION_H must meet each panel's min height."""
    n = len(panels)
    if any(pw / ph < WIDE_AR for pw, ph in sizes):
        return None
    avail = h - (n + 1) * GUTTER
    equal = avail / n
    sw = sum(weights) or 1.0
    raw = [avail * wt / sw for wt in weights]
    row_hs = [min(max(r, STRIP_W_LO * equal), STRIP_W_HI * equal)
              for r in raw]
    scale = avail / sum(row_hs)
    row_hs = [int(r * scale) for r in row_hs]
    cells, y = [], GUTTER
    for k, (p, (pw, ph)) in enumerate(zip(panels, sizes)):
        dw, dh = _fit(pw, ph, w - 2 * GUTTER, row_hs[k])
        rest = _center(GUTTER, y, w - 2 * GUTTER, row_hs[k], dw, dh)
        hw, hh = _fit(pw, ph, w - 2 * GUTTER, int(ACCORDION_H * h))
        if hh < min_hs[k] * h:
            return None
        hx = (w - hw) // 2
        hy = min(max(y + row_hs[k] // 2 - hh // 2, GUTTER), h - GUTTER - hh)
        cells.append(Cell(panel=p, rest=rest, hi=(hx, hy, hw, hh)))
        y += row_hs[k] + GUTTER
    return cells


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------

@dataclass
class LayoutPlanV2(LayoutPlan):
    drift: list = None   # per-cell bool: active-cell Ken Burns drift enabled


def _drift_safe(panel, size, ocr, text_boxes):
    """A cell may drift only when we KNOW the 10% overscan crop can't clip a
    bubble: OCR says the panel is text-free, or every measured text box
    clears DRIFT_MARGIN of each panel edge. Unknown -> static (v1 look).
    Stricter than the research draft on purpose — 'no bubble cropping at
    cell edges' is a hard quality guard, drift is only polish."""
    if not _panel_has_text(panel, ocr):
        return True
    boxes = (text_boxes or {}).get(Path(panel).name)
    if not boxes:
        return False
    pw, ph = size
    mx, my = DRIFT_MARGIN * pw, DRIFT_MARGIN * ph
    return all(b[0] >= mx and b[1] >= my
               and b[0] + b[2] <= pw - mx and b[1] + b[3] <= ph - my
               for b in boxes)


def plan_layout_v2(scene, panels, duration, word_times,
                   frame_w=1920, frame_h=1080, pace=None, ocr=None,
                   text_boxes=None):
    """v2 planner. Same contract as layout_smart.plan_layout, plus:
      * accepts n=2 scenes (hero/rail templates only — research §3.1's
        relaxation of v1's n<3 rejection);
      * returns a LayoutPlanV2 (template in hero_right/hero_left/mag_grid/
        strips/rail, per-cell drift flags) when a v2 template is feasible;
      * else falls back to v1's plan_layout (grid/stack, adopted behavior);
      * else None -> the caller MUST use the sequential path.
    """
    panels = [Path(p) for p in panels]
    n = len(panels)
    if n < 2:
        return None
    if pace is None:
        pace = scene.get("pace")
        if pace not in ("hype", "normal", "quiet"):
            pace = ("hype" if scene.get("emphasis")
                    else ("quiet" if scene.get("focus") else "normal"))
    if pace == "hype":
        return None    # hype beats strobe via the existing 1.6s cut cadence
    if n > MAX_GRID:
        picked = [0, n // 2, n - 1]
    else:
        picked = list(range(n))
    sel = [panels[k] for k in picked]
    k = len(sel)
    if duration < k * MIN_BEAT_SEC:
        return None
    try:
        from PIL import Image
        sizes = []
        for p in sel:
            with Image.open(p) as im:
                sizes.append(im.size)
    except Exception:
        return None
    # per-panel min heights: EXACTLY v1's guard function (binary 55/45 or
    # exp-009 size-aware with measured boxes) — the measuring stick is shared
    min_hs = [_panel_min_h(p, ocr, text_boxes, frame_h, ph)
              for p, (pw, ph) in zip(sel, sizes)]
    req_px = [g * frame_h for g in min_hs]
    beats = _beat_times(scene.get("narration", ""), word_times, k, duration)
    W, hero = _weights(sel, sizes, beats, duration, ocr, text_boxes)

    cands = []
    for tname, cells in (
            ("hero_right", _hero(sel, sizes, frame_w, frame_h, req_px, W,
                                 hero, "right")),
            ("hero_left", _hero(sel, sizes, frame_w, frame_h, req_px, W,
                                hero, "left")),
            ("rail", _rail(sel, sizes, frame_w, frame_h, req_px, W)),
            ("mag_grid", _mag_grid(sel, sizes, frame_w, frame_h, req_px, W)),
            ("strips", _strips(sel, sizes, frame_w, frame_h, req_px, W,
                               min_hs))):
        if cells is None:
            continue
        area = sum(c.rest[2] * c.rest[3] for c in cells)
        cands.append((area * _rotation_penalty(tname), tname, cells))
    if not cands:
        # v1 fallback: the adopted grid/stack planner, bit-safe (n>=3 only —
        # v1 rejects 2-panel scenes itself, which lands us at seq unchanged)
        return ls.plan_layout(scene, [str(p) for p in panels], duration,
                              word_times, frame_w=frame_w, frame_h=frame_h,
                              pace=pace, ocr=ocr, text_boxes=text_boxes)
    _, tname, cells = max(cands, key=lambda c: c[0])
    _RECENT.append(tname)
    ends = beats[1:] + [duration]
    drift = [(_drift_safe(c.panel, s, ocr, text_boxes)
              and (e - b) >= DRIFT_MIN_BEAT)
             for c, s, b, e in zip(cells, sizes, beats, ends)]
    return LayoutPlanV2(template=tname, frame_w=frame_w, frame_h=frame_h,
                        beats=beats, cells=cells, picked=picked, drift=drift)


# ---------------------------------------------------------------------------
# renderer — v1's render_smart_scene with two v2 deltas:
#   (a) per-cell drift: drifting cells' bright PNGs render at DRIFT_OVERSCAN
#       of their rect and an animated crop pans across the overscan during
#       the cell's beat (active-cell Ken Burns; resting copies stay static);
#   (b) accumulate semantics: every v2 template except strips keeps revealed
#       panels lit (like v1 grid); strips keeps v1's transient accordion.
# The punch-in scheduler, breathe, accent border, zoompan and encode args
# are copied from v1 UNCHANGED — they are the static_scene safety net and
# must not move (research §3.3: drift is additive polish, not a punch-in
# replacement).
# ---------------------------------------------------------------------------

def _build_plates_v2(plan, workdir, name):
    """Same plates as v1's _build_plates, except drift cells' bright PNGs
    carry DRIFT_OVERSCAN of extra resolution for the animated crop."""
    from PIL import Image, ImageEnhance, ImageFilter
    w, h = plan.frame_w * SS, plan.frame_h * SS
    drift = plan.drift or [False] * len(plan.cells)
    with Image.open(plan.cells[0].panel) as im:
        src = im.convert("RGB")
        s = max(w / src.width, h / src.height)
        big = src.resize((int(src.width * s) + 1, int(src.height * s) + 1),
                         Image.LANCZOS)
    ox, oy = (big.width - w) // 2, (big.height - h) // 2
    base = big.crop((ox, oy, ox + w, oy + h))
    base = base.filter(ImageFilter.GaussianBlur(24 * SS // 2))
    base = base.point(lambda v: max(v - 36, 0))
    for c in plan.cells:
        x, y, dw, dh = (v * SS for v in c.rest)
        with Image.open(c.panel) as im:
            pn = im.convert("RGB").resize((dw, dh), Image.LANCZOS)
        base.paste(ImageEnhance.Brightness(pn).enhance(DIM), (x, y))
    base_png = workdir / f"{name}_smart2_base.png"
    base.save(base_png)
    brights, punches = [], []
    for k, c in enumerate(plan.cells):
        x, y, dw, dh = (v * SS for v in c.hi)
        bw = int(dw * DRIFT_OVERSCAN) if drift[k] else dw
        bh = int(dh * DRIFT_OVERSCAN) if drift[k] else dh
        with Image.open(c.panel) as im:
            src = im.convert("RGB")
            pn = src.resize((bw, bh), Image.LANCZOS)
            s = max(w / src.width, h / src.height)
            pb = src.resize((int(src.width * s) + 1, int(src.height * s) + 1),
                            Image.LANCZOS)
            fw, fh = _fit(src.width, src.height,
                          int(w * PUNCH_FILL), int(h * PUNCH_FILL))
            pu = src.resize((fw, fh), Image.LANCZOS)
        bp = workdir / f"{name}_smart2_p{k}.png"
        pn.save(bp)
        brights.append(bp)
        ox, oy = (pb.width - w) // 2, (pb.height - h) // 2
        plate = pb.crop((ox, oy, ox + w, oy + h))
        plate = plate.filter(ImageFilter.GaussianBlur(24 * SS // 2))
        plate = plate.point(lambda v: max(v - 36, 0))
        plate.paste(pu, ((w - fw) // 2, (h - fh) // 2))
        pp = workdir / f"{name}_smart2_f{k}.png"
        plate.save(pp)
        punches.append(pp)
    return base_png, brights, punches


def render_smart_scene_v2(plan, out_path, audio, duration, workdir,
                          name="smart2"):
    base_png, brights, punches = _build_plates_v2(plan, Path(workdir), name)
    w, h = plan.frame_w, plan.frame_h
    n = len(plan.cells)
    beats = plan.beats
    ends = beats[1:] + [duration + 1.0]
    drift = plan.drift or [False] * n
    accumulate = plan.template != "strips"

    # ---- guided-view punch schedule: verbatim v1 logic ----
    punch_shots = []
    t = PUNCH_AFTER
    while t < duration - MIN_SHOT:
        last_b = max(b for b in beats if b <= t + 1e-6)
        if t - last_b < REVEAL_GRID - 1e-6:
            t = last_b + REVEAL_GRID
            continue
        p_end = min(t + PUNCH_LEN, duration)
        nb = min((b for b in beats if b > t + 1e-6), default=None)
        if nb is not None and nb < p_end:
            p_end = nb
        if p_end - t < MIN_SHOT:
            if nb is None:
                break
            t = nb + REVEAL_GRID
            continue
        if duration - p_end < MIN_SHOT:
            p_end = duration
        k_act = max(j for j in range(n) if beats[j] <= t + 1e-6)
        punch_shots.append((t, p_end, k_act))
        t = p_end + GRID_LEN

    def _spans():
        marks = [0.0]
        for a, b, _ in punch_shots:
            marks += [a, b]
        marks.append(duration)
        return [(marks[j], marks[j + 1]) for j in range(0, len(marks) - 1, 2)]
    guard = 0
    while guard < 8:
        guard += 1
        bad = next(((s, e) for s, e in _spans() if e - s >= SPAN_MAX), None)
        if bad is None:
            break
        s, e = bad
        mid = (s + e) / 2.0
        p0 = max(s + MIN_SHOT / 2, mid - PUNCH_LEN / 2)
        p1 = min(e - MIN_SHOT / 2, p0 + PUNCH_LEN)
        if p1 - p0 < 1.0:
            break
        k_act = max(j for j in range(n) if beats[j] <= p0 + 1e-6)
        punch_shots.append((p0, p1, k_act))
        punch_shots.sort()

    punch_used = sorted({k for _, _, k in punch_shots})
    punch_in = {k: 1 + n + j for j, k in enumerate(punch_used)}

    cmd = ["ffmpeg", "-y", *ls._thread_cap_args(),
           "-loop", "1", "-framerate", str(mv.FPS),
           "-t", f"{duration:.3f}", "-i", str(base_png)]
    for bp in brights + [punches[k] for k in punch_used]:
        cmd += ["-loop", "1", "-framerate", str(mv.FPS),
                "-t", f"{duration:.3f}", "-i", str(bp)]
    cmd += ["-i", str(audio)]
    a_idx = 1 + n + len(punch_used)

    fc = []
    fc.append("[0:v]setpts=PTS-STARTPTS[b]")
    for k, c in enumerate(plan.cells):
        chain = f"[{k + 1}:v]setpts=PTS-STARTPTS," \
                f"eq=brightness='{BREATHE_AMP}*sin(2*PI*t/{BREATHE_SEC})'"
        if drift[k]:
            # v2 per-cell Ken Burns: the bright copy is DRIFT_OVERSCAN
            # oversized; a crop with an animated offset pans the visible
            # window across the overscan during the cell's beat (progress
            # clamps at 1 so accumulated cells settle, never jitter).
            # Even cells drift horizontally, odd vertically — the same
            # alternation as v1's slide directions. ~0.5px/frame, far
            # below anything that could compound with the composite zoom.
            x, y, dw, dh = (v * SS for v in c.hi)
            t0 = beats[k]
            blen = max(ends[k] if k < n - 1 else duration, t0 + 0.1) - t0
            prog = f"min(max((t-{t0:.3f})/{blen:.3f},0),1)"
            if k % 2 == 0:
                cx, cy = f"(iw-ow)*{prog}", "(ih-oh)/2"
            else:
                cx, cy = "(iw-ow)/2", f"(ih-oh)*{prog}"
            chain += f",crop=w={dw}:h={dh}:x='{cx}':y='{cy}'"
        fc.append(chain + f"[q{k}]")
    shot_lbl_uses = {k: [j for j, (_, _, kk) in enumerate(punch_shots)
                         if kk == k] for k in punch_used}
    for k in punch_used:
        js = shot_lbl_uses[k]
        outs = "".join(f"[f{k}_{j}]" for j in js)
        sp = f",split={len(js)}{outs}" if len(js) > 1 else f"[f{k}_{js[0]}]"
        fc.append(f"[{punch_in[k]}:v]setpts=PTS-STARTPTS{sp}")
    prev = "b"
    for k, c in enumerate(plan.cells):
        x, y, dw, dh = (v * SS for v in c.hi)
        t0 = beats[k]
        if accumulate or k == n - 1:
            en = f"gte(t,{t0:.3f})"
        else:
            en = f"between(t,{t0:.3f},{ends[k]:.3f})"
        dx = SLIDE_PX * SS * (1 if k % 2 == 0 else -1)
        t1 = t0 + SLIDE_SEC
        xexpr = (f"if(lt(t,{t1:.3f}),{x}+({t1:.3f}-t)/{SLIDE_SEC}*{dx},{x})")
        fc.append(f"[{prev}][q{k}]overlay=x='{xexpr}':y={y}:"
                  f"enable='{en}'[v{k}]")
        prev = f"v{k}"
    boxes = []
    for k, c in enumerate(plan.cells):
        x, y, dw, dh = (v * SS for v in c.hi)
        if k == n - 1:
            base_en = f"gte(t,{beats[k]:.3f})"
            hi_ph = f"lt(mod(t,{PULSE_SEC}),{PULSE_ON})"
            boxes.append(f"drawbox=x={x}:y={y}:w={dw}:h={dh}:"
                         f"color={ACCENT}@0.9:t={BORDER_T * SS}:"
                         f"enable='{base_en}*{hi_ph}'")
            boxes.append(f"drawbox=x={x}:y={y}:w={dw}:h={dh}:"
                         f"color={ACCENT}@0.45:t={BORDER_T * SS}:"
                         f"enable='{base_en}*not({hi_ph})'")
        else:
            en = f"between(t,{beats[k]:.3f},{ends[k]:.3f})"
            boxes.append(f"drawbox=x={x}:y={y}:w={dw}:h={dh}:"
                         f"color={ACCENT}@0.9:t={BORDER_T * SS}:"
                         f"enable='{en}'")
    fc.append(f"[{prev}]" + ",".join(boxes) + "[gb]")
    prev = "gb"
    for j, (t0, t1, k_act) in enumerate(punch_shots):
        fc.append(f"[{prev}][f{k_act}_{j}]overlay=x=0:y=0:"
                  f"enable='between(t,{t0:.3f},{t1:.3f})'[pv{j}]")
        prev = f"pv{j}"
    total_frames = max(int(round(duration * mv.FPS)), 1)
    rate = (ZOOM_END - 1.0) / total_frames
    zx = (f"iw/2-(iw/zoom/2)+(iw-iw/zoom)/2*0.6*"
          f"sin(2*PI*in/{total_frames}*1.5)")
    zp = (f"zoompan=z='1.0+{rate:.8f}*in':x='{zx}':"
          f"y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={mv.FPS}")
    fc.append(f"[{prev}]" + ",".join([zp, "format=yuv420p"]) + "[v]")

    cmd += ["-filter_complex", ";".join(fc),
            "-map", "[v]", "-map", f"{a_idx}:a",
            "-af", "apad", "-t", f"{duration:.3f}",
            *mv._INTER_V, *mv._INTER_A,
            "-video_track_timescale", "15360",
            str(out_path)]
    mv.run(cmd)


# ---------------------------------------------------------------------------
# CLI — smoke tests / sample renders with synthetic silence (no TTS needed)
# ---------------------------------------------------------------------------

def main():
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panels", nargs="+", required=True)
    ap.add_argument("--out", default=None, help="output mp4 (omit with "
                    "--plan-only)")
    ap.add_argument("--duration", type=float, default=12.0)
    ap.add_argument("--narration", default="")
    ap.add_argument("--pace", default="normal",
                    choices=["normal", "quiet", "hype"])
    ap.add_argument("--frame", default="1920x1080")
    ap.add_argument("--ocr", default=None, help="<slug>.ocr.json sidecar")
    ap.add_argument("--text-boxes", default=None,
                    help="<slug>.textboxes.json sidecar")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the plan as JSON and exit (no render)")
    args = ap.parse_args()
    w, h = (int(v) for v in args.frame.split("x"))
    ocr = None
    if args.ocr and Path(args.ocr).exists():
        ocr = {r["panel"]: r for r in json.loads(Path(args.ocr).read_text())}
    tboxes = None
    if args.text_boxes and Path(args.text_boxes).exists():
        raw = json.loads(Path(args.text_boxes).read_text())
        tboxes = {k: v.get("boxes", v) if isinstance(v, dict) else v
                  for k, v in raw.get("panels", raw).items()}
    scene = {"narration": args.narration, "pace": args.pace}
    plan = plan_layout_v2(scene, args.panels, args.duration, None,
                          frame_w=w, frame_h=h, ocr=ocr, text_boxes=tboxes)
    if plan is None:
        print(json.dumps({"template": None,
                          "note": "no feasible layout -> seq fallback"}))
        return
    info = {"template": plan.template, "picked": plan.picked,
            "beats": [round(b, 2) for b in plan.beats],
            "drift": getattr(plan, "drift", None),
            "cells": [{"panel": Path(c.panel).name, "rest": c.rest,
                       "hi": c.hi} for c in plan.cells]}
    print(json.dumps(info, indent=2))
    if args.plan_only or not args.out:
        return
    with tempfile.TemporaryDirectory(prefix="smart2_") as td:
        td = Path(td)
        sil = td / "sil.mp3"
        mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                "anullsrc=r=48000:cl=stereo", "-t", f"{args.duration:.3f}",
                "-c:a", "libmp3lame", str(sil)])
        if isinstance(plan, LayoutPlanV2):
            render_smart_scene_v2(plan, args.out, sil, args.duration, td)
        else:
            ls.render_smart_scene(plan, args.out, sil, args.duration, td)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
