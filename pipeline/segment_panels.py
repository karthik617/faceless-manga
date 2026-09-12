#!/usr/bin/env python3
"""segment_panels.py — STAGE 2: split pages/strips into ordered panels.

Classic OpenCV segmentation (no ML for v1):
  * manga   — bordered pages: threshold -> connected components -> filter by
              area -> order RIGHT-TO-LEFT, top-to-bottom (Japanese reading).
  * webtoon — long vertical strips: horizontal whitespace-projection cut at
              near-white gutters -> slices TOP-TO-BOTTOM.
  * auto    — guess per page by aspect ratio (tall -> webtoon, else manga).

Never crashes: if segmentation finds <2 sane panels, the whole page is emitted
as a single panel (mirrors make_video.py's graceful-fallback ethos).

Output: out_dir/p0001.png ... (globally renumbered, reading order) plus
panels_index.json ([{panel, page, bbox:[x,y,w,h], mode}]).

Usage:
    python3 segment_panels.py --raw output/<slug>/raw \
        --out output/<slug>/panels --mode auto
"""
import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
WEBTOON_ASPECT = 2.2   # height/width above this -> treat page as a webtoon strip

# ---- exp-004 split-guard (gap-004): bubble/text-aware forced cuts ----------
# ADOPTED 2026-09-09: manga.py passes --split-guard yolo by DEFAULT (opt-out
# --split-guard none). The module-level default stays "none" per the gap-002
# convention (module CLIs keep legacy defaults; the orchestrator flips them),
# so direct segment_panels.py invocations without the flag remain
# byte-identical to the pre-experiment splitter — "none" skips every guard
# code path below INCLUDING the auto-mode majority vote in segment().
# Env FM_SPLIT_GUARD overrides the module default.
# Design: mm14a (Pang et al., ACM MM 2014)
# Laplacian cut energy ranks candidate rows; detected text/bubble boxes veto
# rows so a forced cut can never land through a speech bubble.
GUARD_PAD = 16          # px margin added above/below each text box when
                        # marking rows illegal (a cut grazing a box edge still
                        # crops its tail)
CUT_LAMBDA = 4.0        # weight of the Laplacian boundary term vs raw spread:
                        # prefers the EDGE of a flat region (just past the art)
                        # over the middle of any flat texture, which can be the
                        # inside of a big bubble -- the exact bug behind the
                        # golden chapter's cropped-bubble faults
CUT_SOFT_CAP = 1.25     # allow a slice to overflow max_slice_px by 25% before
                        # we give up and place an illegal cut (research §5)
YOLO_REPO = "deepghs/manga109_yolo"       # MIT lib; Manga109-trained ONNX
# nano ~2.6M params, CPU-friendly. Smoke-tested on real ch3 webtoon panels:
# the 2023.12.07 checkpoints return ZERO `text` dets on colored webtoon
# bubbles (even at conf 0.1) while v2021.12.30_n finds them at 0.27-0.30 —
# hence the older model + a 0.2 floor (research §5 risk "Manga109-trained
# yolo misses colored webtoon text" hit for the newer weights only).
YOLO_MODEL = "v2021.12.30_n_yv11"
YOLO_CONF = 0.2
YOLO_WIN = 1280         # window height for detection over tall slices
YOLO_STRIDE = 1024      # 256 px overlap so a bubble on a window edge is still
                        # seen whole by the neighboring window
YOLO_MAX_W = 640        # downscale width before inference (yolo input budget)

# ---- exp-004 v2 guard extensions --------------------------------------------
# v1 vetoed forced cuts through TEXT only; the ch2 downstream benchmark showed
# two residual mechanisms it can't see (report.md §downstream):
#   (a) forced cuts through text-free ART (scenes 15/21: cut rows had ink
#       density 0.90/0.93 — uniformly dark art reads as "flat" to the spread
#       measure, so the energy ranking happily cut there). The yolo model's
#       body/face classes fire at NEITHER fault site (only a `frame` box that
#       tiles the whole band), so the fix is an INK-DENSITY CEILING: rows with
#       more than INK_CEIL dark pixels are vetoed, and among legal rows an ink
#       term is added to the cut energy so low-ink rows win ties.
#   (b) gutter cuts clipping tall stylized SFX (scene 16): mostly-white rows
#       containing the SFX letter tips pass the mean>=245 gutter test, so the
#       slice top lands mid-letter. yolo does not detect stylized SFX, so the
#       fix is detector-free: SNAP the boundaries of over-tall slices outward
#       across any "gutter" rows that still carry real ink (spread >
#       SNAP_SPREAD), stopping at the first truly-empty row. Text is ink too,
#       so this is the same veto generalized to gutter cuts. Snapping only
#       GROWS a slice into its gutter, so it can never lose content or change
#       slice order, and segmentation can never deadlock.
INK_GRAY = 110          # gray level below which a pixel counts as ink
INK_CEIL = 0.55         # veto forced-cut rows with > this ink fraction; the
                        # ch2 fault bands keep 25%+ of their rows legal at
                        # this ceiling, and the widen/fallback chain still
                        # guarantees a cut on all-dark (dark-mode) strips
INK_W = 120.0           # weight of the ink term in cut energy (gray units at
                        # ink=1.0 ~ half the 0-255 spread scale)
CLEAN_INK = 0.20        # a cut row is "clean" below this ink fraction; when a
                        # slice fits under the soft cap but its best legal row
                        # is dirtier than this, SKIP the forced cut and emit
                        # the over-tall slice whole — ch2 scene 15 is a
                        # 2628-px full-page drawing (cap 2600) whose least-bad
                        # row still crosses SFX art; keeping it whole is
                        # strictly better than any cut
SNAP_SPREAD = 40        # a "gutter" row with min/max spread above this holds
                        # real marks (SFX/text tips), not jpeg noise
SNAP_GAP = 8            # allow this many empty rows inside an SFX before the
                        # outward snap stops (letter tips are not contiguous)
SNAP_PAD = 4            # margin kept above/below the snapped content
OVER_TALL = 1.5         # slices taller than this x median get their gutter
                        # boundaries audited (cheap scope per report.md rec.)


def _list_pages(raw_dir):
    files = [p for p in Path(raw_dir).iterdir()
             if p.suffix.lower() in IMG_EXTS]
    def key(p):
        import re
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", p.name)]
    return sorted(files, key=key)


def _blob_text_boxes(region):
    """Pure-OpenCV bubble/caption text candidates -> [(x0,y0,x1,y1)] in
    region coords. No ML, no new deps (guard=='blob').

    Heuristic (research §4, tuned on real ch3 panels): manga lettering is
    DARK strokes sitting inside a FLAT BRIGHT field (the bubble/caption).
    A pure flat-field CCL fails here — text strokes carve holes through the
    bubble so its fill-ratio collapses — so instead: keep ink pixels whose
    neighborhood is bright (text-on-bubble, not line art on busy tone),
    fuse letters into blocks morphologically, then verify each block's bbox
    is mostly bright with a text-like ink fraction. Over-wide boxes on snowy
    art are acceptable: for a cut VETO a false positive only shrinks the
    legal-row pool, and the widen-then-fallback path still guarantees a cut."""
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    ink = (gray < 90).astype(np.uint8)
    bright = (gray > 195).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    txt = ink & cv2.dilate(bright, k)      # ink with bright surroundings
    fuse = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 13))
    blocks = cv2.morphologyEx(txt, cv2.MORPH_CLOSE, fuse)
    blocks = cv2.dilate(blocks,
                        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(blocks, 8)
    h, w = gray.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 400 or bh < 14 or bw < 20:
            continue                       # speckle, stray stroke
        if bh > 0.9 * h and bw > 0.9 * w:
            continue                       # whole-region blob = art, not text
        roi = gray[y:y + bh, x:x + bw]
        bfrac = float((roi > 195).mean())
        ifrac = float((roi < 90).mean())
        # bubbles read as mostly-bright boxes with a modest ink share; busy
        # art fails the bright floor, empty fields fail the ink floor
        if bfrac < 0.35 or ifrac < 0.04 or ifrac > 0.6:
            continue
        out.append((int(x), int(y), int(x + bw), int(y + bh)))
    return out


def _yolo_text_boxes_batch(regions):
    """deepghs/manga109_yolo `text` boxes for MULTIPLE regions, batched so
    the ~5 s one-time ONNX model load is paid once per run (keeps the segment
    step inside the 3x wall-time budget).

    ADOPTED 2026-09-09 (IMPLEMENTATION.md deviation 3 discharge): at
    prototype stage this shelled out to a quarantined subprocess in the
    experiment venv; the detector now lives in-process at
    pipeline/yolo_detect.py with its runtime deps (onnxruntime,
    huggingface-hub) in pipeline/requirements.txt. Same model, same
    windowing, same boxes (diff-verified against the subprocess detector).
    Any failure (deps missing, model download offline, bad ONNX) raises so
    the caller can fall back to the blob guard — the pipeline never dies
    here. First run downloads the ~10 MB ONNX to ~/.cache/huggingface."""
    import yolo_detect
    out = yolo_detect.detect_text_boxes(
        regions, conf=YOLO_CONF, win=YOLO_WIN, stride=YOLO_STRIDE,
        max_w=YOLO_MAX_W, repo=YOLO_REPO, model=YOLO_MODEL)
    return [[tuple(int(round(v)) for v in b) for b in boxes]
            for boxes in out]


def _guard_text_boxes(region, guard):
    """Text/bubble boxes for ONE strip region under the chosen guard. yolo
    degrades to blob on any error (graceful fallback, logged)."""
    if guard == "yolo":
        try:
            return _yolo_text_boxes_batch([region])[0]
        except Exception as e:   # noqa: BLE001 — any detector failure
            print(f"  ! yolo guard unavailable ({e}); falling back to blob")
            return _blob_text_boxes(region)
    return _blob_text_boxes(region)


def _forbidden_rows_multi(img, spans, guard):
    """Bool array over the WHOLE strip height; True where a cut row would
    intersect a detected text box (± GUARD_PAD) inside any [y0,y1) span.
    Detection runs ONLY inside the given spans (the over-tall slices that
    need forced cuts), never over the full strip — the wall-time mitigation
    from research §3. yolo spans are batched into one subprocess."""
    h = img.shape[0]
    forb = np.zeros(h, dtype=bool)
    if not spans:
        return forb
    if guard == "yolo":
        try:
            per_span = _yolo_text_boxes_batch([img[a:b] for a, b in spans])
        except Exception as e:   # noqa: BLE001 — any detector failure
            print(f"  ! yolo guard unavailable ({e}); falling back to blob")
            per_span = [_blob_text_boxes(img[a:b]) for a, b in spans]
    else:
        per_span = [_blob_text_boxes(img[a:b]) for a, b in spans]
    for (y0, _), boxes in zip(spans, per_span):
        for (_, by0, _, by1) in boxes:
            a = max(0, y0 + int(by0) - GUARD_PAD)
            b = min(h, y0 + int(by1) + GUARD_PAD)
            forb[a:b] = True
    return forb


def _best_legal_cut(row_spread, row_mean, forbidden, lo, hi, y, bh,
                    max_slice_px, row_ink=None):
    """mm14a-style cut placement: rank rows by
        E(c) = mean(spread[c-4:c+5]) - CUT_LAMBDA * (2g(c) - g(c-1) - g(c+1))
              [+ INK_W * ink(c) under the v2 art guard]
    (low spread = flat row; high Laplacian = boundary between content and
    spacing, so the cut lands just PAST the art instead of in the middle of
    any flat texture — e.g. the inside of a big bubble). Only rows NOT vetoed
    by `forbidden` are eligible; if the band has no legal row, widen it (soft
    cap +25% over max_slice_px per research §5), else return None so the
    caller falls back to the legacy flattest-row cut.

    exp-004 v2: `row_ink` (per-row dark-pixel fraction) adds an ART veto —
    rows with ink > INK_CEIL are ineligible, because on the ch2 benchmark the
    two remaining forced-cut faults went through text-FREE art whose rows are
    uniformly dark (ink 0.90/0.93): a flat dark row has LOW spread so the v1
    energy actively preferred it. The ink ceiling is a SOFTER constraint than
    the text veto: if no row passes both, the ceiling is dropped first (text
    boxes stay vetoed) — cutting art beats cutting a bubble, and an all-dark
    dark-mode strip must still get a cut (never deadlock)."""
    win = 9
    g = row_mean
    lap = np.zeros_like(g)
    lap[1:-1] = 2 * g[1:-1] - g[:-2] - g[2:]

    def pick(a, b, use_ink):
        a = max(a, y + win)
        b = min(b, y + bh - win)
        if b - a < win:
            return None
        best_i, best_e = None, None
        for i in range(a, b - win):
            c = i + win // 2
            if forbidden[c]:
                continue
            if use_ink and row_ink[c] > INK_CEIL:
                continue                   # v2: don't cut through dense art
            e = float(row_spread[i:i + win].mean()) - CUT_LAMBDA * float(lap[c])
            if use_ink:
                e += INK_W * float(row_ink[c])   # among legal rows, prefer
            if best_e is None or e < best_e:     # the least-inked one
                best_e, best_i = e, c
        return best_i

    # two passes: first honor the art ceiling, then (still honoring text
    # boxes) without it — so the fallback ladder is
    # band+ink -> widened+ink -> band -> widened -> None(legacy)
    for use_ink in ([True, False] if row_ink is not None else [False]):
        cut = pick(lo, hi, use_ink)
        if cut is None:                   # widen downward (soft cap overflow)
            cut = pick(lo, y + min(bh, int(max_slice_px * CUT_SOFT_CAP)),
                       use_ink)
        if cut is None:                   # widen upward toward the slice top
            cut = pick(y + int(max_slice_px * 0.25),
                       y + min(bh, int(max_slice_px * CUT_SOFT_CAP)), use_ink)
        if cut is not None:
            return cut
    return None


def _snap_gutter_edges(out, row_is_gutter, row_spread, strip_h):
    """exp-004 v2 (guard on only): grow over-tall slices outward across
    'gutter' rows that still carry real marks.

    Mechanism it fixes (ch2 scene 16): tall stylized SFX typography sits on a
    near-white field, so the rows holding the letter TIPS pass the
    mean>=245 gutter test and the slice edge lands mid-letter — a gutter cut
    the forced-cut guard never inspects. yolo does not detect stylized SFX
    (verified on the ch2 site), so the audit is detector-free: from each edge
    of an over-tall slice (> OVER_TALL x median — the report's cheap scope),
    walk into the adjacent gutter and pull the edge over any rows whose
    min/max spread exceeds SNAP_SPREAD (ink exists there; text tips qualify
    too, so this is the text/SFX veto generalized to gutter cuts). The walk
    stops at the neighboring slice, so snapping only ever GROWS a slice into
    empty gutter: content is never lost, slice order never changes, and no
    fallback is needed — segmentation cannot deadlock here."""
    if len(out) < 2:
        return out
    med = float(np.median([b[3] for b in out]))
    tall = med * OVER_TALL
    for k, b in enumerate(out):
        x, y, bw, bh = b
        if bh <= tall:
            continue
        floor = (out[k - 1][1] + out[k - 1][3]) if k > 0 else 0
        ceil_ = out[k + 1][1] if k < len(out) - 1 else strip_h
        # top edge: walk upward through the gutter, tracking the farthest
        # inky row; SNAP_GAP empty rows are allowed inside an SFX (letter
        # tips are not contiguous)
        r, new_top, gap = y - 1, y, 0
        while r >= floor and row_is_gutter[r] and gap <= SNAP_GAP:
            if row_spread[r] > SNAP_SPREAD:
                new_top, gap = r, 0
            else:
                gap += 1
            r -= 1
        if new_top < y:
            ny = max(floor, new_top - SNAP_PAD)
            print(f"  guard: slice top y={y} snapped -{y - ny}px "
                  f"(inky gutter rows: SFX/text tips)")
            b[1], b[3] = ny, y + bh - ny
            x, y, bw, bh = b
        # bottom edge: same walk downward
        bot = y + bh
        r, new_bot, gap = bot, bot, 0
        while r < ceil_ and row_is_gutter[r] and gap <= SNAP_GAP:
            if row_spread[r] > SNAP_SPREAD:
                new_bot, gap = r + 1, 0
            else:
                gap += 1
            r += 1
        if new_bot > bot:
            nb = min(ceil_, new_bot + SNAP_PAD)
            print(f"  guard: slice bottom y={bot} snapped +{nb - bot}px "
                  f"(inky gutter rows: SFX/text tips)")
            b[3] = nb - y
    return out


def _guess_mode(img):
    h, w = img.shape[:2]
    return "webtoon" if (h / max(w, 1)) >= WEBTOON_ASPECT else "manga"


def segment_page_manga(img, min_area_frac=0.02):
    """Return bordered-panel bboxes [x,y,w,h] ordered right-to-left, top-down."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # panels are usually dark gutters on light page (or vice-versa); binarize
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # close small gaps so a panel's interior becomes one blob
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    min_area = min_area_frac * w * h
    boxes = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw * bh < min_area:
            continue
        # reject near-full-page blobs (whole page as one contour) unless it's
        # genuinely the only thing; and reject slivers
        if bw < 0.05 * w or bh < 0.05 * h:
            continue
        boxes.append([x, y, bw, bh])
    return _order_reading(boxes, "manga", h)


def segment_strip_webtoon(img, min_gap_px=None, white_thresh=245,
                          min_slice_px=220, guard="none"):
    """Return slice bboxes for a long strip, cut at UNIFORM horizontal gutters
    (white OR black OR any flat background color — dark-mode webtoons use
    black), ordered top-to-bottom.

    Slices shorter than min_slice_px (floating text lines, SFX fragments,
    scene-break ornaments) are merged into their nearest neighbor so they
    never become standalone 'panels' that render as near-empty frames."""
    h, w = img.shape[:2]
    if min_gap_px is None:
        # proportional on single pages, but CAPPED: on a stitched multi-page
        # strip h*0.006 would demand gutters hundreds of px tall and never
        # fire (real webtoon gutters are ~20-60px).
        min_gap_px = max(12, min(int(h * 0.006), 28))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    row_min = gray.min(axis=1)
    row_max = gray.max(axis=1)
    row_mean = gray.mean(axis=1)
    # a row is "gutter" when it is visually flat: near-white, near-black, or
    # any uniform color (tiny min/max spread = no artwork on that row)
    row_is_gutter = ((row_min >= white_thresh) |
                     (row_mean >= white_thresh) |
                     (row_max <= 12) |
                     ((row_max - row_min) <= 8))
    boxes = []
    in_content = False
    start = 0
    gap = 0
    for i in range(h):
        if row_is_gutter[i]:
            gap += 1
            if in_content and gap >= min_gap_px:
                end = i - gap + 1
                if end - start > 4:
                    boxes.append([0, start, w, end - start])
                in_content = False
        else:
            if not in_content:
                start = i
                in_content = True
            gap = 0
    if in_content and h - start > 4:
        boxes.append([0, start, w, h - start])

    # merge short slices into a neighbor (prefer the closer one) so text
    # fragments ride with the art they belong to. Repeat until stable.
    def _merge_pass(bs):
        for k, b in enumerate(bs):
            if b[3] >= min_slice_px or len(bs) == 1:
                continue
            prev_gap = b[1] - (bs[k-1][1] + bs[k-1][3]) if k > 0 else 1e9
            next_gap = bs[k+1][1] - (b[1] + b[3]) if k < len(bs) - 1 else 1e9
            j = k - 1 if prev_gap <= next_gap else k + 1
            lo = min(bs[j][1], b[1])
            hi = max(bs[j][1] + bs[j][3], b[1] + b[3])
            bs[j] = [0, lo, w, hi - lo]
            del bs[k]
            return True
        return False
    while _merge_pass(boxes):
        pass
    # drop anything still microscopic (lone ornaments on an otherwise empty page)
    boxes = [b for b in boxes if b[3] >= max(48, min_slice_px // 4)]

    # split overly tall slices (gutterless stretches of a stitched strip) at
    # their flattest interior rows, so cuts land where the least art is.
    max_slice_px = 2600
    row_spread = (row_max - row_min)   # per-row content measure
    out = []
    # exp-004: with a guard on, detect text boxes ONCE over exactly the
    # over-tall slices that will need forced cuts (only those slices pay
    # detector cost — research §3 wall-time mitigation; yolo runs them all
    # in a single subprocess) and veto their rows for every cut below.
    forbidden = None
    row_ink = None
    if guard != "none":
        spans = [(b[1], b[1] + b[3]) for b in boxes if b[3] > max_slice_px]
        if spans:
            forbidden = _forbidden_rows_multi(img, spans, guard)
            # exp-004 v2: per-row ink density for the ART veto (text boxes
            # can't cover text-free drawings; dense-dark rows are art)
            row_ink = (gray < INK_GRAY).mean(axis=1)
    for b in boxes:
        x, y, bw, bh = b
        while bh > max_slice_px:
            # search a band for the flattest window of rows and cut there.
            # The band is capped at max_slice_px so every emitted piece
            # respects the cap regardless of where the flattest row is.
            lo = y + min(int(bh * 0.35), int(max_slice_px * 0.5))
            hi = y + min(int(bh * 0.75), max_slice_px)
            best_i = None
            if forbidden is not None:
                best_i = _best_legal_cut(row_spread, row_mean, forbidden,
                                         lo, hi, y, bh, max_slice_px,
                                         row_ink=row_ink)
                # exp-004 v2: soft-cap escape — if the slice would FIT under
                # the +25% overflow allowance and even the best legal row
                # still crosses art (ink > CLEAN_INK), emit it whole instead
                # of forcing a dirty cut. ch2 scene 15 is a 2628-px full-page
                # drawing (cap 2600) with no clean row anywhere; any cut
                # through it is a cropped_content fault. Only fires under the
                # soft cap so pathological 10k-px gutterless stretches still
                # get cut (never deadlocks, never unbounded slices).
                if (best_i is not None
                        and bh <= int(max_slice_px * CUT_SOFT_CAP)
                        and row_ink is not None
                        and row_ink[best_i] > CLEAN_INK):
                    print(f"  guard: slice y={y} h={bh} kept whole "
                          f"(soft cap; best cut row ink="
                          f"{row_ink[best_i]:.2f} > {CLEAN_INK})")
                    break
                if best_i is None:
                    print(f"  ! no legal cut row near y={y}; "
                          f"forced cut may cross content")
            if best_i is None:
                # legacy behavior (guard off, or veto left no legal row)
                win = 9
                best_i, best_v = lo, 1e9
                spread = row_spread[lo:hi]
                for i in range(0, len(spread) - win):
                    v = float(spread[i:i + win].mean())
                    if v < best_v:
                        best_v, best_i = v, lo + i + win // 2
            out.append([x, y, bw, best_i - y])
            bh = y + bh - best_i
            y = best_i
        out.append([x, y, bw, bh])
    # exp-004 v2 (guard on only): audit the GUTTER edges of over-tall slices
    # for clipped SFX/text tips and grow them over any inky "gutter" rows.
    if guard != "none":
        out = _snap_gutter_edges(out, row_is_gutter, row_spread, h)
    return out  # already top-to-bottom


def _order_reading(boxes, mode, page_h):
    """Order bboxes into reading order. manga: right-to-left within horizontal
    bands, bands top-to-bottom. webtoon: pure top-to-bottom."""
    if not boxes:
        return []
    if mode == "webtoon":
        return sorted(boxes, key=lambda b: b[1])
    # band by y (row grouping): sort by y, cluster into bands of ~similar top
    boxes = sorted(boxes, key=lambda b: b[1])
    band_tol = 0.08 * page_h
    bands, cur = [], [boxes[0]]
    for b in boxes[1:]:
        if abs(b[1] - cur[0][1]) <= band_tol:
            cur.append(b)
        else:
            bands.append(cur)
            cur = [b]
    bands.append(cur)
    ordered = []
    for band in bands:
        # right-to-left: largest x first
        ordered.extend(sorted(band, key=lambda b: b[0], reverse=True))
    return ordered


def _content_frac(crop):
    """Fraction of pixels differing >30 from the median background luma."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    bg = float(np.median(g))
    return float((np.abs(g.astype(np.float32) - bg) > 30).mean())


# slices with almost no content (pure-black dramatic beats, empty spacers)
# render as blank frames at delivery — skip them (the narration carries the
# beat; a black screen just reads as an encoding error).
MIN_CONTENT_FRAC = 0.05


def segment(raw_dir, out_dir, mode="auto", guard="none"):
    pages = _list_pages(raw_dir)
    if not pages:
        sys.exit(f"no page images in {raw_dir}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    pn = 0  # global panel counter

    imgs = []
    for page_path in pages:
        img = cv2.imread(str(page_path))
        if img is None:
            print(f"  ! could not read {page_path.name}; skipping")
            continue
        imgs.append((page_path, img))

    # WEBTOON TILE STITCHING: aggregator downloads deliver one continuous
    # strip pre-chopped into fixed-height tiles (e.g. 690x2000). Segmenting
    # tiles independently turns every tile edge into a hard cut straight
    # through bubbles/art. When all pages share a width and the mode is
    # webtoon, vertically stitch them into ONE virtual strip and segment
    # that, so cuts only ever land on real gutters.
    widths = {im.shape[1] for _, im in imgs}
    if mode == "auto" and imgs:
        if guard != "none":
            # exp-004 adoption (report §3 / recommendation option b): vote the
            # chapter mode by MAJORITY over all pages instead of trusting
            # page 1. The golden ch2 faults happened because page 1 is a
            # 1778x1000 cover guessed "manga", which disabled tile stitching
            # for 97 webtoon tiles and turned every tile edge into a hard cut
            # through bubbles/art. Guard-gated so --split-guard none keeps
            # the legacy first-page guess (byte-identical opt-out).
            from collections import Counter
            votes = Counter(_guess_mode(im) for _, im in imgs)
            pmode0 = votes.most_common(1)[0][0]
            if pmode0 != _guess_mode(imgs[0][1]):
                print(f"  auto mode: majority vote -> {pmode0} "
                      f"(page 1 alone would say {_guess_mode(imgs[0][1])})")
        else:
            pmode0 = _guess_mode(imgs[0][1])   # legacy: trust page 1
    else:
        pmode0 = mode
    stitched = (pmode0 == "webtoon" and len(widths) == 1 and len(imgs) > 1)
    # a lone odd-width page (cover art) breaks the stitch; segment it alone
    if pmode0 == "webtoon" and len(widths) == 2 and len(imgs) > 2:
        from collections import Counter
        common_w = Counter(im.shape[1] for _, im in imgs).most_common(1)[0][0]
        odd = [(p, im) for p, im in imgs if im.shape[1] != common_w]
        if len(odd) <= 2:
            for p, im in odd:
                pn += 1
                cv2.imwrite(str(out_dir / f"p{pn:04}.png"), im)
                index.append({"panel": f"p{pn:04}.png", "page": p.name,
                              "bbox": [0, 0, im.shape[1], im.shape[0]],
                              "mode": "webtoon", "note": "odd-width page"})
            imgs = [(p, im) for p, im in imgs if im.shape[1] == common_w]
            stitched = True

    if stitched:
        strip = np.vstack([im for _, im in imgs])
        print(f"  stitched {len(imgs)} tiles -> one strip "
              f"{strip.shape[1]}x{strip.shape[0]}")
        boxes = segment_strip_webtoon(strip, guard=guard)
        if not boxes:
            boxes = [[0, 0, strip.shape[1], strip.shape[0]]]
        skipped = 0
        beats = 0
        for (x, y, bw, bh) in boxes:
            crop = strip[y:y + bh, x:x + bw]
            entry = {"panel": None, "page": "strip",
                     "bbox": [int(x), int(y), int(bw), int(bh)],
                     "mode": "webtoon"}
            if _content_frac(crop) < MIN_CONTENT_FRAC:
                if guard == "none":
                    skipped += 1
                    continue
                # gap-004 direction 3: near-black dramatic beats are real
                # story moments — keep them, tagged via the EXISTING optional
                # `note` key so the index schema stays unchanged; the renderer
                # can later hold on them with narration instead of Ken Burns.
                entry["note"] = "beat"
                beats += 1
            pn += 1
            entry["panel"] = f"p{pn:04}.png"
            cv2.imwrite(str(out_dir / f"p{pn:04}.png"), crop)
            index.append(entry)
        print(f"  strip: {len(boxes)} slices, {skipped} empty skipped"
              + (f", {beats} beat(s) kept" if beats else ""))
    else:
        for page_i, (page_path, img) in enumerate(imgs, 1):
            pmode = _guess_mode(img) if mode == "auto" else mode
            if pmode == "webtoon":
                boxes = segment_strip_webtoon(img, guard=guard)
            else:
                boxes = segment_page_manga(img)
            if len(boxes) < 1:
                h, w = img.shape[:2]
                boxes = [[0, 0, w, h]]
            for (x, y, bw, bh) in boxes:
                crop = img[y:y + bh, x:x + bw]
                entry = {"panel": None, "page": page_i,
                         "bbox": [int(x), int(y), int(bw), int(bh)],
                         "mode": pmode}
                if pmode == "webtoon" and _content_frac(crop) < MIN_CONTENT_FRAC:
                    if guard == "none":
                        continue
                    entry["note"] = "beat"   # keep tagged (see stitched path)
                pn += 1
                out_path = out_dir / f"p{pn:04}.png"
                cv2.imwrite(str(out_path), crop)
                entry["panel"] = out_path.name
                index.append(entry)
            print(f"  [{page_i}/{len(imgs)}] {page_path.name} ({pmode}): "
                  f"{len(boxes)} panel(s)")
    (out_dir / "panels_index.json").write_text(json.dumps(index, indent=2))
    print(f"  -> {pn} panels total -> {out_dir}")
    return pn


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", required=True, help="raw/ dir of page images")
    ap.add_argument("--out", required=True, help="panels/ output dir")
    ap.add_argument("--mode", choices=["auto", "manga", "webtoon"], default="auto")
    ap.add_argument("--split-guard", choices=["none", "blob", "yolo"],
                    default=os.environ.get("FM_SPLIT_GUARD", "none"),
                    help="gap-004 guard (adopted 2026-09-09; manga.py passes "
                         "yolo by default, this module CLI keeps none per "
                         "the gap-002 convention). none=legacy behavior "
                         "(byte-identical, incl. first-page auto-mode "
                         "guess); blob=pure-opencv heuristic; yolo=manga109 "
                         "text detector (pipeline/yolo_detect.py, ~10MB ONNX "
                         "cached on first use, falls back to blob on error). "
                         "Any guard also: art-ink cut ceiling, gutter-edge "
                         "SFX snapping, keeps near-black beat panels tagged "
                         "note='beat', and majority-votes the auto mode.")
    args = ap.parse_args()
    segment(args.raw, args.out, args.mode, guard=args.split_guard)


if __name__ == "__main__":
    main()
