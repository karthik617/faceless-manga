#!/usr/bin/env python3
"""layout_smart.py — creative multi-panel layout compositor (gap-002).

When a scene lists 3+ panels and the pace allows it, the sequential-cuts path
loses the "several moments at once" structure of the page: narration describing
panel 3 plays over panel 1, and long narration ping-pongs between the last two
panels. This module composites the scene's panels into ONE plate (grid or
accordion stack) and reveals/highlights each panel exactly when its narration
beat lands, so the narrated panel is always the visibly-active one.

Design (see improvements/experiments/exp-002-multipanel-layout/research.md):
  * All layout math happens in Python/PIL (testable, no filtergraph escaping
    for geometry) — the same "pre-composited plate" trick as make_short.py's
    build_plate. ffmpeg then does a single pass: timed overlays of the bright
    panel PNGs onto a dimmed base plate, an accent border on the active cell,
    and one gentle zoompan on the composite.
  * plan_layout() is a pure planner: it returns None whenever its readability
    guards fail, and the caller MUST fall back to the existing sequential path
    (zero behavior change). Nothing here runs unless --layout smart is set.

Only used behind panel_render.py's --layout smart flag; the default pipeline
never imports this module's render path.
"""
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# reuse make_video's encode constants exactly like panel_render does, so the
# smart clip is byte-compatible with the per-scene clips it gets concatenated
# with (same fps / pix_fmt / codec args). Idempotent when panel_render already
# inserted the path.
FYT_PIPELINE = Path.home() / "faceless-youtube" / "pipeline"
if str(FYT_PIPELINE) not in sys.path:
    sys.path.insert(0, str(FYT_PIPELINE))
import make_video as mv

# Opt-in memory guard (Sep 10 ch4 OOM incident): the single-pass smart-layout
# ffmpeg holds n+1 looped supersampled PNG inputs plus a large overlay/zoompan
# filtergraph, and its buffer footprint scales with filter threads. Setting
# FM_FFMPEG_THREADS=N caps -threads / -filter_threads / -filter_complex_threads
# for THIS encode only (slower, much flatter RAM peak). Unset = byte-identical
# behavior to before.
def _thread_cap_args():
    v = os.environ.get("FM_FFMPEG_THREADS", "").strip()
    if not v:
        return []
    try:
        n = max(1, int(v))
    except ValueError:
        return []
    n = str(n)
    return ["-threads", n, "-filter_threads", n, "-filter_complex_threads", n]

# ---- trigger / guard constants (from the gap card + research §5) ----
MIN_BEAT_SEC = 1.2     # a panel needs this long on screen to be readable
PRE_ROLL = 0.12        # panel lands just before its line starts (motion-comic
                       # convention: the eye arrives before the word)
GUARD_H = 0.45         # text-FREE (pure action) panel must show at >=45% of
                       # frame height (phone readability; gap card criterion)
GUARD_H_TEXT = 0.55    # text-BEARING panel (OCR found dialogue/narration in
                       # it) needs >=55% of frame height or the in-panel text
                       # is illegible on phones (round-1 eval: phone_
                       # readability 2->8 on grid scenes)
MAX_GRID = 3           # round-1 eval: 4-cell 2x2 grids shrink panels below
                       # phone legibility — cap composites at 3 cells and
                       # pick 3 panels in reading order from longer scenes
WIDE_AR = 1.4          # w/h at or above this = "wide strip" -> stack template
ACCORDION_H = 0.58     # active accordion panel takes ~58% of frame height
GUTTER = 12            # px between cells at 1080p-class frames
DIM = 0.55             # resting panels sit at 55% brightness on the base plate
SLIDE_SEC = 0.35       # bright panel slide-in duration
SLIDE_PX = 60          # slide-in travel (frame px; subtle, not a swipe)
BORDER_T = 6           # accent border thickness (frame px)
ACCENT = "0xFF3B3B"    # brand vermilion (title_overlay.VERM / brand kit)
SS = 2                 # supersample factor — matches render_panel_scene's
                       # ss_w/ss_h so the zoompan stays sub-pixel smooth
# ---- continuous-motion constants (round-1 static_scene fix) ----
ZOOM_END = 1.06        # whole-composite zoom target reached AT scene end —
                       # the zoom rate is duration-scaled so motion never
                       # stops (round 1 capped at 1.06 after ~5s and froze)
BREATHE_AMP = 0.05     # bright-panel luminance breathe (+/- eq brightness);
                       # ~13/255 luma — a gentle projector-glow "the frame is
                       # alive" cue (too small to move the phash scan alone)
BREATHE_SEC = 5.0      # breathe period (odd phase vs the 2s scan stride)
PULSE_ON = 1.7         # final-panel accent border breathe: full-alpha for
PULSE_SEC = 3.4        # PULSE_ON s of every PULSE_SEC s, half-alpha the rest
                       # (the border never blinks off — it breathes)
# guided-view punch-ins: the PRIMARY static_scene fix. review_video's
# static_scan phashes a 180px frame every 2s and calls <=6/64 differing bits
# "identical" — measured on the real plates, a 1.0->1.06 zoom moves <=4
# bits, brightness/breathe <=5, and even a full grid-cell REVEAL stays
# inside the band (the round-1 40s repro faulted straight through its
# reveals). Only a structural view change resets the detector. So the
# composite periodically punches INTO the narrated panel fullscreen and
# back — the guided-view convention — which guarantees a full-frame
# structural change before any span reaches the 12s threshold, and shows
# each panel's in-panel text at near-native size (phone readability assist).
PUNCH_AFTER = 6.0      # opening grid runs this long before the first punch
PUNCH_LEN = 6.0        # punch-in shot length (quiet-pace cadence)
GRID_LEN = 5.0         # grid time between punches. Worst-case contiguous
                       # grid = GRID_LEN + 2 reveal-lead pushes (5 + 2*1.6 =
                       # 8.2s) — comfortably under the 12s static threshold
REVEAL_GRID = 1.6      # grid time guaranteed after each reveal so the
                       # slide-in + accent handoff stay visible before a
                       # punch may start
MIN_SHOT = 2.5         # never schedule a shot shorter than this
SPAN_MAX = 10.5        # hard ceiling on any same-view span (static_scan
                       # threshold is 12s; reveals alone do NOT reset its
                       # phash, so the safety pass force-inserts a punch
                       # into any span that would reach this)
PUNCH_FILL = 0.94      # punched panel fills 94% of frame height over a
                       # blurred cover plate (sequential-path look)

_SENT_SPLIT = re.compile(r"[^.!?…;]+[.!?…;]*\s*")


@dataclass
class Cell:
    """One panel's placement: `rest` = dimmed resting rect on the base plate,
    `hi` = bright highlighted rect during its beat. Rects are (x, y, w, h) of
    the DISPLAYED image (fit-inside, never cropped) in frame coordinates."""
    panel: Path
    rest: tuple
    hi: tuple


@dataclass
class LayoutPlan:
    template: str        # "grid" | "stack"
    frame_w: int
    frame_h: int
    beats: list          # reveal start time per cell (seconds, ascending)
    cells: list          # [Cell, ...] in reading order
    picked: list = None  # original panel indices composited (reading order);
                         # differs from range(n) when the scene had >MAX_GRID
                         # panels and the planner selected first/middle/last


def _fit(pw, ph, box_w, box_h):
    """Fit-inside scale (never crop — bubbles must survive; gap card guard)."""
    s = min(box_w / pw, box_h / ph)
    return max(int(pw * s), 1), max(int(ph * s), 1)


def _center(box_x, box_y, box_w, box_h, dw, dh):
    return (box_x + (box_w - dw) // 2, box_y + (box_h - dh) // 2, dw, dh)


def _sentence_starts(narration, word_times):
    """Candidate beat boundaries = start time of each sentence's first word.
    Sentences map to word_times positionally (cumulative word counts): the
    TTS speaks the words in order, so index arithmetic is enough — no fuzzy
    matching needed. Returns None when the mapping is unusable."""
    if not narration or not word_times:
        return None
    sents = [s for s in _SENT_SPLIT.findall(narration) if s.strip()]
    if len(sents) < 2:
        return None
    starts, idx = [], 0
    for s in sents:
        if idx >= len(word_times):
            break
        starts.append(float(word_times[idx]["start"]))
        idx += len(s.split())
    return starts if len(starts) >= 2 else None


def _beat_times(narration, word_times, n, duration):
    """N ascending reveal times in [0, duration), snapped to sentence
    boundaries when word timings allow, else equal splits. Beat 0 is always
    t=0 (the scene never opens empty)."""
    equal = [k * duration / n for k in range(n)]
    cand = _sentence_starts(narration, word_times)
    if not cand:
        return equal
    # pre-roll: land the panel a breath before its line begins
    cand = sorted({0.0} | {max(t - PRE_ROLL, 0.0) for t in cand[1:]})
    # drop boundaries too close to the end for a readable final beat
    cand = [t for t in cand if t <= duration - MIN_BEAT_SEC]
    if not cand or cand[0] != 0.0:
        cand = [0.0] + cand
    # fit the sentence count to the panel count: merge the shortest beat
    # (drop its start boundary) when over, split the longest when under
    while len(cand) > n:
        gaps = [(cand[j] - cand[j - 1], j) for j in range(1, len(cand))]
        _, j = min(gaps)
        cand.pop(j)
    while len(cand) < n:
        ends = cand[1:] + [duration]
        spans = [(e - s, j) for j, (s, e) in enumerate(zip(cand, ends))]
        _, j = max(spans)
        cand.insert(j + 1, (cand[j] + ends[j]) / 2.0)
    # enforce the minimum beat length forward; if the schedule no longer fits
    # the scene, the safe equal grid wins (trigger guarantees it fits)
    for k in range(1, n):
        cand[k] = max(cand[k], cand[k - 1] + MIN_BEAT_SEC)
    if cand[-1] > duration - MIN_BEAT_SEC + 1e-6:
        return equal
    return cand


# minimum readable line height at 1080p on a phone (exp-009 research §2:
# BBC/Material/broadcast anchors converge on a 40-59px comfort band; 40 is
# the action trigger). Scaled by frame_h/1080 for other resolutions.
MIN_TEXT_H_1080 = 40


def _panel_min_h(panel, ocr, text_boxes, frame_h, panel_h):
    """Per-panel required cell height as a fraction of frame height (exp-009
    §3b): with MEASURED line boxes, a text-bearing panel qualifies for a grid
    cell only if its projected in-cell text height reaches MIN_TEXT_H — the
    binary has-text guard let panels whose text was already tiny at native
    scale pass 55% and still render unreadable (ch3 right-column MEDIUMs).

    text_boxes is {panel_name: [[x,y,w,h],...]} or None. None -> the adopted
    binary behavior EXACTLY (this function must stay inert without data).
    Text known present (OCR) but zero measured boxes -> conservative binary
    guard, never looser than today.

    exp-009 v3: panel_render no longer passes measured boxes here — the
    size-aware requirement rejected grid plans the binary guard accepts
    (round3 eval: lost composites caused watermark/empty_screen vetoes), so
    layout PLANNING uses the binary guard regardless of --text-aware and
    measured boxes drive only the crop/punch-in path. The size-aware branch
    below stays for callers that opt in explicitly (layout_smart2 CLI)."""
    has_text = _panel_has_text(panel, ocr)
    if text_boxes is None:
        return GUARD_H_TEXT if has_text else GUARD_H
    boxes = text_boxes.get(Path(panel).name)
    if not boxes:
        return GUARD_H_TEXT if has_text else GUARD_H
    hs = sorted(b[3] for b in boxes)
    med = hs[len(hs) // 2]
    min_px = MIN_TEXT_H_1080 * frame_h / 1080.0
    # displayed text height = med * dh / panel_h; need >= min_px
    # -> dh/frame_h >= min_px * panel_h / (med * frame_h)
    req = min_px * panel_h / (med * frame_h)
    # req > 1.0 survives the clamp ceiling at 1.0 and can never be met by a
    # grid cell (cells are always < frame height) -> the template rejects the
    # panel and the planner returns None -> sequential fallback, where the
    # text-aware crop path takes over.
    return min(max(req, GUARD_H), 1.0)


def _panel_has_text(panel, ocr):
    """True when the OCR sidecar says this panel carries dialogue or embedded
    narration text (its in-panel text must stay phone-legible). Conservative:
    no OCR data / no entry for the panel -> treat as text-bearing, so the
    stricter GUARD_H_TEXT applies whenever we don't KNOW a panel is pure
    action. `ocr` accepts a {panel_name: read} dict or the raw sidecar list."""
    if not ocr:
        return True
    name = Path(panel).name
    if isinstance(ocr, dict):
        r = ocr.get(name)
    else:
        r = next((x for x in ocr if x.get("panel") == name), None)
    if r is None:
        return True
    return bool(r.get("dialogue")) or bool((r.get("narration_text")
                                            or "").strip())


def _grid_cells(panels, sizes, w, h, min_hs):
    """3 panels -> 1x3 columns, reading order left->right (the script's panel
    list is already in story order). min_hs[k] is that panel's per-panel
    height guard (0.55 text-bearing / 0.45 text-free) as a fraction of frame
    height. Returns None when any panel's fitted height misses its guard.

    4+ panel grids (the round-1 2x2) are gone: cells at <=half frame height
    could never satisfy the text guard, and round-1 phone_readability faults
    landed exactly there. Wide-strip panels that would need a 3x1 row stack
    are served by the accordion template (rows at h/3 can't pass either)."""
    if len(panels) != 3:
        return None
    boxes = [((w - 4 * GUTTER) // 3, h - 2 * GUTTER,
              GUTTER + k * ((w - 4 * GUTTER) // 3 + GUTTER), GUTTER)
             for k in range(3)]
    cells = []
    for p, (pw, ph), (cw, ch, cx, cy), g in zip(panels, sizes, boxes, min_hs):
        dw, dh = _fit(pw, ph, cw, ch)
        if dh < g * h:
            return None    # cell too small for this panel -> template rejected
        rect = _center(cx, cy, cw, ch, dw, dh)
        # grid highlight = the cell itself: reveal is brightness + slide-in,
        # the panel never moves off its cell (Guided-View reading order stays
        # visually stable)
        cells.append(Cell(panel=p, rest=rect, hi=rect))
    return cells


def _stack_cells(panels, sizes, w, h, min_hs):
    """Accordion for wide strips: resting rows share the height equally
    (dimmed); the active panel's bright copy pops to ~58% of frame height
    centered on its band. Returns None when any highlighted panel misses its
    per-panel guard (extremely wide strips get width-limited)."""
    n = len(panels)
    if any(pw / ph < WIDE_AR for pw, ph in sizes):
        return None
    row_h = (h - (n + 1) * GUTTER) // n
    cells = []
    for k, (p, (pw, ph)) in enumerate(zip(panels, sizes)):
        band_y = GUTTER + k * (row_h + GUTTER)
        dw, dh = _fit(pw, ph, w - 2 * GUTTER, row_h)
        rest = _center(GUTTER, band_y, w - 2 * GUTTER, row_h, dw, dh)
        hw, hh = _fit(pw, ph, w - 2 * GUTTER, int(ACCORDION_H * h))
        if hh < min_hs[k] * h:
            return None
        # highlighted copy centers on its band but stays fully in frame
        hx = (w - hw) // 2
        hy = min(max(band_y + row_h // 2 - hh // 2, GUTTER),
                 h - GUTTER - hh)
        cells.append(Cell(panel=p, rest=rest, hi=(hx, hy, hw, hh)))
    return cells


def plan_layout(scene, panels, duration, word_times,
                frame_w=1920, frame_h=1080, pace=None, ocr=None,
                text_boxes=None):
    """Decide whether this scene gets a smart layout and, if so, which one.

    `ocr` (optional): the <slug>.ocr.json reads — a {panel_name: read} dict
    or the raw list — used to size cells text-aware: panels the OCR says
    carry dialogue/narration must land >=GUARD_H_TEXT (55%) of frame height,
    pure-action panels may go down to GUARD_H (45%). No OCR = every panel is
    treated as text-bearing (conservative).

    `text_boxes` (optional, exp-009): {panel_name: [[x,y,w,h],...]} measured
    line boxes from the <slug>.textboxes.json sidecar. When present, the
    binary text guard upgrades to a size-aware one: a text-bearing panel
    qualifies for a cell only if its projected in-cell median line height
    reaches MIN_TEXT_H_1080 (scaled by frame_h). None -> binary guards,
    byte-identical to the adopted behavior.

    Scenes with more than MAX_GRID panels composite a 3-panel selection in
    reading order — first, middle, last (setup / turn / payoff) — recorded in
    plan.picked so the caller can log the choice.

    Returns a LayoutPlan or None. None means the caller MUST use the existing
    sequential path — every guard here exists so that fallback keeps today's
    output bit-identical for scenes the compositor can't serve well.
    """
    panels = [Path(p) for p in panels]
    n = len(panels)
    if n < 3:
        return None
    # pace: same inference as panel_render's cadence block so both agree on
    # what "hype" means (explicit field wins, else emphasis->hype/focus->quiet)
    if pace is None:
        pace = scene.get("pace")
        if pace not in ("hype", "normal", "quiet"):
            pace = ("hype" if scene.get("emphasis")
                    else ("quiet" if scene.get("focus") else "normal"))
    if pace == "hype":
        return None    # hype beats strobe via the existing 1.6s cut cadence
    # panel-cap selection: keep reading order, keep the emotional shape
    # (opening shot, mid-scene turn, final payoff)
    if n > MAX_GRID:
        picked = [0, n // 2, n - 1]
    else:
        picked = list(range(n))
    panels = [panels[k] for k in picked]
    k = len(panels)
    if duration < k * MIN_BEAT_SEC:
        return None    # beats would be too short to read
    try:
        from PIL import Image
        sizes = []
        for p in panels:
            with Image.open(p) as im:
                sizes.append(im.size)
    except Exception:
        return None
    # text-aware per-panel height guards (round-1 phone_readability fix).
    # With measured boxes (exp-009 --text-aware) the guard is size-aware;
    # without, it's the adopted binary 55/45 split.
    if text_boxes is None:
        min_hs = [GUARD_H_TEXT if _panel_has_text(p, ocr) else GUARD_H
                  for p in panels]
    else:
        min_hs = [_panel_min_h(p, ocr, text_boxes, frame_h, ph)
                  for p, (pw, ph) in zip(panels, sizes)]
    # template selection: grid first (3 panels whose aspects survive the
    # cells), then accordion stack for wide strips, else give up
    cells = _grid_cells(panels, sizes, frame_w, frame_h, min_hs)
    template = "grid"
    if cells is None:
        cells = _stack_cells(panels, sizes, frame_w, frame_h, min_hs)
        template = "stack"
    if cells is None:
        return None
    beats = _beat_times(scene.get("narration", ""), word_times, k, duration)
    return LayoutPlan(template=template, frame_w=frame_w, frame_h=frame_h,
                      beats=beats, cells=cells, picked=picked)


def _build_plates(plan, workdir, name):
    """PIL pre-composition: base plate = blurred/darkened fill of the FIRST
    panel (the scene's establishing art — same gblur+darken look as
    render_panel_scene's blur_bg) with every panel pasted dimmed at its
    resting rect; plus one bright PNG per panel at its highlighted size.
    Everything is built at SS supersample so the final zoompan stays smooth.
    Returns (base_png, [bright_pngs])."""
    from PIL import Image, ImageEnhance, ImageFilter
    w, h = plan.frame_w * SS, plan.frame_h * SS
    # background: scale-to-cover + center-crop the first panel, blur, darken.
    # gblur sigma=24 / eq brightness=-0.14 equivalents: blur radius scales with
    # the supersample; -0.14 is additive in ffmpeg's eq -> subtract ~36/255.
    with Image.open(plan.cells[0].panel) as im:
        src = im.convert("RGB")
        s = max(w / src.width, h / src.height)
        big = src.resize((int(src.width * s) + 1, int(src.height * s) + 1),
                         Image.LANCZOS)
    ox, oy = (big.width - w) // 2, (big.height - h) // 2
    base = big.crop((ox, oy, ox + w, oy + h))
    base = base.filter(ImageFilter.GaussianBlur(24 * SS // 2))
    base = base.point(lambda v: max(v - 36, 0))
    # dimmed resting panels
    for c in plan.cells:
        x, y, dw, dh = (v * SS for v in c.rest)
        with Image.open(c.panel) as im:
            pn = im.convert("RGB").resize((dw, dh), Image.LANCZOS)
        base.paste(ImageEnhance.Brightness(pn).enhance(DIM), (x, y))
    base_png = workdir / f"{name}_smart_base.png"
    base.save(base_png)
    # bright highlighted copies + fullscreen punch-in PLATES. A punch plate
    # is a complete frame — blurred/darkened cover of the panel + the panel
    # fit-inside PUNCH_FILL, centered — exactly the look of the sequential
    # path's single-panel shots. Overlaying a bare tall panel onto the grid
    # is NOT enough: webtoon panels are narrow columns, the grid stays
    # visible around them and the static_scan's 180px phash barely moves.
    # A full plate makes each punch a true cut (structural frame change).
    brights, punches = [], []
    for k, c in enumerate(plan.cells):
        x, y, dw, dh = (v * SS for v in c.hi)
        with Image.open(c.panel) as im:
            src = im.convert("RGB")
            pn = src.resize((dw, dh), Image.LANCZOS)
            s = max(w / src.width, h / src.height)
            pb = src.resize((int(src.width * s) + 1, int(src.height * s) + 1),
                            Image.LANCZOS)
            fw, fh = _fit(src.width, src.height,
                          int(w * PUNCH_FILL), int(h * PUNCH_FILL))
            pu = src.resize((fw, fh), Image.LANCZOS)
        bp = workdir / f"{name}_smart_p{k}.png"
        pn.save(bp)
        brights.append(bp)
        ox, oy = (pb.width - w) // 2, (pb.height - h) // 2
        plate = pb.crop((ox, oy, ox + w, oy + h))
        plate = plate.filter(ImageFilter.GaussianBlur(24 * SS // 2))
        plate = plate.point(lambda v: max(v - 36, 0))
        plate.paste(pu, ((w - fw) // 2, (h - fh) // 2))
        pp = workdir / f"{name}_smart_f{k}.png"
        plate.save(pp)
        punches.append(pp)
    return base_png, brights, punches


def render_smart_scene(plan, out_path, audio, duration, workdir, name="smart"):
    """One ffmpeg pass: dimmed base plate -> per-beat bright-panel overlays
    (slide-in over SLIDE_SEC) -> accent border on the active cell (the final
    panel's border breathes on a mod(t) pulse) -> continuous duration-scaled
    zoompan + sinusoidal drift on the composite -> yuv420p. Encoded with the
    same mv._INTER_V / mv._INTER_A args and apad+-t clamp as
    render_panel_scene so the clip joins the existing 15360-timescale
    copy-concat unchanged.

    v2 motion contract (round-1 static_scene fix), three layers:
      1. STRUCTURAL (the one the phash detector actually sees): guided-view
         punch-ins — every GRID_LEN..PUNCH_LEN alternation the composite
         punches into the narrated panel fullscreen and back, so no same-view
         stretch can reach review_video's 12s near-duplicate threshold.
         (Measured: zoom to 1.06, dim/bright reveals, even a full cell reveal
         all stay within the detector's <=6/64-bit "identical" band — only a
         structural view change resets it.)
      2. Continuous zoom: rate is (ZOOM_END-1)/total_frames so z grows for
         the ENTIRE scene instead of capping after ~5s like round 1.
      3. Breathe: bright cells glow on a BREATHE_SEC luminance cycle and the
         final panel's accent border breathes full/half alpha, so even
         within one shot the frame is visibly alive."""
    base_png, brights, punches = _build_plates(plan, Path(workdir), name)
    w, h = plan.frame_w, plan.frame_h
    ss_w, ss_h = w * SS, h * SS
    n = len(plan.cells)
    beats = plan.beats
    ends = beats[1:] + [duration + 1.0]   # last window runs past the end

    # ---- guided-view punch schedule (grid / punch alternation) ----
    # Every reveal happens ON the grid (with >=REVEAL_GRID of grid time after
    # it so the slide-in + accent handoff read), then the view punches into
    # the active panel fullscreen. A punch never crosses a reveal — it cuts
    # back to the grid at the beat. Worst contiguous grid span stays under
    # ~GRID_LEN + 2*REVEAL_GRID < the 12s static_scan threshold.
    punch_shots = []                       # [(t0, t1, panel_idx), ...]
    t = PUNCH_AFTER
    while t < duration - MIN_SHOT:
        last_b = max(b for b in beats if b <= t + 1e-6)
        if t - last_b < REVEAL_GRID - 1e-6:
            t = last_b + REVEAL_GRID       # let the reveal settle on-grid
            continue
        p_end = min(t + PUNCH_LEN, duration)
        nb = min((b for b in beats if b > t + 1e-6), default=None)
        if nb is not None and nb < p_end:
            p_end = nb                     # cut back to grid at the reveal
        if p_end - t < MIN_SHOT:
            if nb is None:
                break
            t = nb + REVEAL_GRID           # no room before the reveal — skip
            continue
        if duration - p_end < MIN_SHOT:
            p_end = duration               # tail punch absorbs the remainder
        k_act = max(j for j in range(n) if beats[j] <= t + 1e-6)
        punch_shots.append((t, p_end, k_act))
        t = p_end + GRID_LEN
    # SAFETY PASS: the greedy walk above can leave a same-view span >= the
    # static_scan threshold on awkward beat schedules (e.g. a ~15s scene
    # whose reveals crowd the tail leaves no legal punch slot, so the whole
    # scene is one grid view). Force a punch into the middle of any span
    # that reaches SPAN_MAX — reveals don't count as span breaks because
    # they don't move the detector's phash (measured; see constants above).
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
        if p1 - p0 < 1.0:                  # degenerate — give up quietly;
            break                          # zoom/breathe still cover it
        k_act = max(j for j in range(n) if beats[j] <= p0 + 1e-6)
        punch_shots.append((p0, p1, k_act))
        punch_shots.sort()

    # only the punch PNGs the schedule actually uses become inputs (an
    # unconsumed filtergraph branch is a hard ffmpeg error)
    punch_used = sorted({k for _, _, k in punch_shots})
    punch_in = {k: 1 + n + j for j, k in enumerate(punch_used)}

    cmd = ["ffmpeg", "-y", *_thread_cap_args(),
           "-loop", "1", "-framerate", str(mv.FPS),
           "-t", f"{duration:.3f}", "-i", str(base_png)]
    for bp in brights + [punches[k] for k in punch_used]:
        cmd += ["-loop", "1", "-framerate", str(mv.FPS),
                "-t", f"{duration:.3f}", "-i", str(bp)]
    cmd += ["-i", str(audio)]
    a_idx = 1 + n + len(punch_used)        # audio input index

    fc = []
    # anchor every image loop's timestamps at 0 so overlay enable= windows
    # land on real wall-time (overlay docs: inputs with unknown/offset PTS
    # make t NAN)
    fc.append("[0:v]setpts=PTS-STARTPTS[b]")
    for k in range(n):
        # luminance "breathe" on every bright panel: eq re-evaluates its
        # expression per frame, so the lit cells glow on a BREATHE_SEC cycle
        # against the static dim plate — a constant "the frame is alive" cue
        # between punches (round-1 static_scene fix, layer 3)
        fc.append(f"[{k + 1}:v]setpts=PTS-STARTPTS,"
                  f"eq=brightness='{BREATHE_AMP}*"
                  f"sin(2*PI*t/{BREATHE_SEC})'[q{k}]")
    # punch branches: one label per SHOT (a filter pad is single-consumer, so
    # a panel punched twice gets its input split)
    shot_lbl = []
    uses = {k: [j for j, (_, _, kk) in enumerate(punch_shots) if kk == k]
            for k in punch_used}
    for k in punch_used:
        js = uses[k]
        outs = "".join(f"[f{k}_{j}]" for j in js)
        sp = f",split={len(js)}{outs}" if len(js) > 1 else f"[f{k}_{js[0]}]"
        fc.append(f"[{punch_in[k]}:v]setpts=PTS-STARTPTS{sp}")
    prev = "b"
    for k, c in enumerate(plan.cells):
        x, y, dw, dh = (v * SS for v in c.hi)
        t0 = beats[k]
        # grid accumulates (a revealed panel stays lit — the plate builds up);
        # the accordion spotlight is transient (the previous panel drops back
        # to its dimmed resting copy when the highlight moves on). Last panel
        # always holds so the tail narration plays over a settled composite.
        if plan.template == "grid" or k == n - 1:
            en = f"gte(t,{t0:.3f})"
        else:
            en = f"between(t,{t0:.3f},{ends[k]:.3f})"
        # subtle slide-in: from +/-SLIDE_PX horizontally, easing to rest over
        # SLIDE_SEC (alternating direction like _SLIDE_DIRS keeps it varied)
        dx = SLIDE_PX * SS * (1 if k % 2 == 0 else -1)
        t1 = t0 + SLIDE_SEC
        xexpr = (f"if(lt(t,{t1:.3f}),{x}+({t1:.3f}-t)/{SLIDE_SEC}*{dx},{x})")
        fc.append(f"[{prev}][q{k}]overlay=x='{xexpr}':y={y}:"
                  f"enable='{en}'[v{k}]")
        prev = f"v{k}"
    # accent border marks the ACTIVE cell during its beat window (brand
    # vermilion, same family as the title-card spine). The FINAL cell's
    # border breathes once revealed — full alpha PULSE_ON s of every
    # PULSE_SEC cycle, half alpha the rest — so the settled tail keeps a
    # visible heartbeat between punches.
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
    # guided-view punch overlays: fullscreen active panel during each punch
    # window (structural view change = the static_scan reset; also shows the
    # panel's text at near-native size)
    prev = "gb"
    for j, (t0, t1, k_act) in enumerate(punch_shots):
        fc.append(f"[{prev}][f{k_act}_{j}]overlay=x=0:y=0:"
                  f"enable='between(t,{t0:.3f},{t1:.3f})'[pv{j}]")
        prev = f"pv{j}"
    # continuous zoompan LAST (research §2.4: putting it after the timed
    # filters keeps enable= windows on wall-time; d=1 passes video frames
    # through 1:1). v2: the zoom rate is duration-scaled so z climbs for the
    # WHOLE scene (1.0 -> ZOOM_END at the final frame) — round 1's fixed
    # 0.0004/frame rate hit its 1.06 cap ~5s in and the composite froze.
    # A slow sinusoidal x-drift rides on the zoom's pan headroom (iw-iw/zoom
    # grows from 0 with z, so the crop window always stays inside the plate).
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
            # concat_copy remuxes to this anyway; setting it at encode keeps
            # the clip drop-in safe even for callers that skip the remux
            "-video_track_timescale", "15360",
            str(out_path)]
    mv.run(cmd)
