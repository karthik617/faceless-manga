#!/usr/bin/env python3
"""text_boxes.py — per-panel text LINE bounding boxes (gap-009, exp-009).

The OCR sidecar (<slug>.ocr.json) knows WHAT text a panel carries but not the
pixel HEIGHT of its lines, so no renderer guard can be size-aware. This module
emits detection-only line boxes [[x, y, w, h], ...] per panel so panel_render
(--text-aware) and layout_smart can compute *rendered* text height and act
before a phone viewer squints.

Detector chain (research §1/§5):
  1. PP-OCRv3 DBNet det-only ONNX (opencv_zoo, Apache-2.0) via the already-
     installed cv2.dnn TextDetectionModel_DB — zero new pip deps, ~0.2-0.4 s
     per panel on CPU. Model vendored at pipeline/models/ and sha256-checked
     so a corrupt/partial download can never silently mis-measure.
  2. cv2-classic fallback (bright/dark container threshold -> Otsu ink ->
     line-fuse morphology) when the model file is absent or fails to load.
     Weaker on screentone/low-contrast panels but zero-anything; each cache
     entry records which method produced it.

Tall webtoon panels (up to ~1:3) are TILED into ~square windows with 10%
overlap before detection — DBNet's square 736x736 input would otherwise
squash a 2000px-tall panel's lines below its own recall floor — and the tile
boxes are offset back to panel coordinates and de-duplicated.

Results cache to a <slug>.textboxes.json sidecar (keyed by panel filename +
mtime, same pattern as the OCR reads) so a chapter pays detection once.

CLI:
    ./venv/bin/python3 pipeline/text_boxes.py --panels <dir> --out <json> \
        [--debug-overlays <dir>] [--only p0024.png ...]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_NAME = "text_detection_en_ppocrv3_2023may.onnx"
# sha256 of the upstream file (opencv_zoo git-lfs pointer records the same
# oid): a mismatch means a truncated/tampered download -> classic fallback.
MODEL_SHA256 = "03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587"
DB_INPUT = 736                    # opencv_zoo ppocr_det.py input size
DB_MEAN = (122.67891434, 116.66876762, 104.00698793)
DB_BIN_THRESH = 0.3
DB_POLY_THRESH = 0.5
TILE_AR = 1.4                     # tile when long/short exceeds this AND the
                                  # long side would be downscaled at DB_INPUT
TILE_OVERLAP = 0.10               # 10% window overlap (tall-image OCR trick)
MERGE_IOM = 0.6                   # intersection/min-area above this = same
                                  # line seen from two overlapping tiles
MIN_LINE_H = 6                    # px: below this it's speckle, not a line
MAX_LINE_H_FRAC = 0.25            # a "line" taller than this * panel width
                                  # is SFX/merge junk, not dialogue (same
                                  # bound as the classic detector)
CACHE_VERSION = 1


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_detector():
    """DBNet detector or None (-> classic fallback). Never raises: any load
    problem is a printed warning, because the caller must keep rendering."""
    import cv2
    p = MODEL_DIR / MODEL_NAME
    if not p.exists():
        print(f"text_boxes: model {p} missing -> cv2-classic fallback")
        return None
    try:
        got = _sha256(p)
        if got != MODEL_SHA256:
            print(f"text_boxes: model sha mismatch ({got[:12]}... != "
                  f"{MODEL_SHA256[:12]}...) -> cv2-classic fallback")
            return None
        det = cv2.dnn.TextDetectionModel_DB(str(p))
        det.setBinaryThreshold(DB_BIN_THRESH)
        det.setPolygonThreshold(DB_POLY_THRESH)
        # scale/size/mean/swapRB per opencv_zoo text_detection_ppocr docs;
        # the high-level API maps detections back to source coordinates.
        det.setInputParams(1.0 / 255, (DB_INPUT, DB_INPUT), DB_MEAN, True)
        return det
    except Exception as e:
        print(f"text_boxes: model load failed ({type(e).__name__}: {e}) "
              f"-> cv2-classic fallback")
        return None


def _tiles(w, h):
    """~Square detection windows with TILE_OVERLAP, covering the long axis.
    Single window when the panel is near-square OR small enough that DBNet's
    square input doesn't squash it (long side <= DB_INPUT)."""
    long_dim, short_dim = max(w, h), min(w, h)
    if long_dim <= DB_INPUT or long_dim / short_dim <= TILE_AR:
        return [(0, 0, w, h)]
    win = min(long_dim, max(short_dim, DB_INPUT))
    step = max(int(win * (1.0 - TILE_OVERLAP)), 1)
    offs = list(range(0, long_dim - win, step)) + [long_dim - win]
    if h >= w:                       # tall: walk y
        return [(0, o, w, o + win) for o in offs]
    return [(o, 0, o + win, h)]      # wide: walk x


def _merge_boxes(boxes):
    """Fuse duplicates (same line detected in two overlapping tiles): greedy
    union of pairs whose intersection covers most of the smaller box."""
    boxes = [list(map(int, b)) for b in boxes]
    changed = True
    while changed:
        changed = False
        out = []
        while boxes:
            a = boxes.pop()
            ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
            merged = False
            for o in out:
                ox0, oy0, ox1, oy1 = o[0], o[1], o[0] + o[2], o[1] + o[3]
                iw = min(ax1, ox1) - max(ax0, ox0)
                ih = min(ay1, oy1) - max(ay0, oy0)
                if iw <= 0 or ih <= 0:
                    continue
                if iw * ih >= MERGE_IOM * min(a[2] * a[3], o[2] * o[3]):
                    nx0, ny0 = min(ax0, ox0), min(ay0, oy0)
                    nx1, ny1 = max(ax1, ox1), max(ay1, oy1)
                    o[:] = [nx0, ny0, nx1 - nx0, ny1 - ny0]
                    merged = changed = True
                    break
            if not merged:
                out.append(a)
        boxes = out
    return sorted(boxes, key=lambda b: (b[1], b[0]))


def detect_dbnet(img, det):
    """DBNet over tiles; quads -> axis-aligned line boxes in panel coords.
    Post-filter: a LINE taller than MAX_LINE_H_FRAC of panel width isn't
    dialogue — it's stylized vertical SFX lettering (or several SFX quads
    fused across tile overlaps). Dropping those keeps the height median on
    the reading text AND stops a giant pseudo-box from blocking legal crops
    (crops must fully contain every kept box)."""
    import numpy as np
    h, w = img.shape[:2]
    boxes = []
    for (x0, y0, x1, y1) in _tiles(w, h):
        tile = img[y0:y1, x0:x1]
        quads, _conf = det.detect(tile)
        for q in quads:
            q = np.asarray(q)
            bx0, by0 = int(q[:, 0].min()), int(q[:, 1].min())
            bx1, by1 = int(q[:, 0].max()), int(q[:, 1].max())
            if by1 - by0 < MIN_LINE_H:
                continue
            boxes.append([bx0 + x0, by0 + y0, bx1 - bx0, by1 - by0])
    merged = _merge_boxes(boxes)
    max_h = MAX_LINE_H_FRAC * w
    return [b for b in merged if b[3] <= max_h]


def detect_classic(img):
    """cv2-only fallback (research §5): scanlation dialogue is near-black
    type in near-white bubbles/boxes (system captions: the mirrored dark-box
    pass). Stage 1 finds bright/dark container blobs (thresholds match
    clean_bubbles.py's proven 200/90 split), Stage 2 Otsu-splits the ink
    inside each and fuses glyphs into lines with a wide flat dilation.
    Misses SFX-over-art by design (no container; SFX aren't the fault
    source) and degrades on gray-on-gray scans — that's why DBNet is the
    primary and this only fills in when the model file is gone."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.medianBlur(gray, 3)          # kills most screentone dots
    ph, pw = g.shape
    k7 = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    k5 = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    out = []
    for dark_container in (False, True):
        if dark_container:               # black caption boxes, white text
            _, cont = cv2.threshold(g, 90, 255, cv2.THRESH_BINARY_INV)
        else:                            # white bubbles/boxes, black text
            _, cont = cv2.threshold(g, 200, 255, cv2.THRESH_BINARY)
        cont = cv2.morphologyEx(cont, cv2.MORPH_CLOSE, k7)
        cont = cv2.morphologyEx(cont, cv2.MORPH_OPEN, k5)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cont)
        for k in range(1, n):
            x, y, w, h, a = stats[k]
            if a < 0.002 * pw * ph or w * h == 0:
                continue
            if a / (w * h) < 0.45:       # containers are solid-ish blobs
                continue
            if not (0.2 <= w / h <= 8.0):
                continue
            roi = g[y:y + h, x:x + w]
            comp = (lab[y:y + h, x:x + w] == k).astype(np.uint8) * 255
            # erode the container 2px so the bubble outline stroke doesn't
            # fuse with the text and drag the line boxes to the border
            interior = cv2.erode(comp, k5)
            flag = (cv2.THRESH_BINARY if dark_container
                    else cv2.THRESH_BINARY_INV)
            _, ink = cv2.threshold(roi, 0, 255, flag + cv2.THRESH_OTSU)
            ink = cv2.bitwise_and(ink, interior)
            # wide flat dilation fuses glyphs into LINES (kh=1: never fuses
            # adjacent lines vertically). Kernel width tracks container size.
            kw = max(7, (w // 20) | 1)
            fused = cv2.dilate(ink, cv2.getStructuringElement(
                cv2.MORPH_RECT, (kw, 1)))
            cnts, _ = cv2.findContours(fused, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                bx, by, bw, bh = cv2.boundingRect(c)
                if bh < MIN_LINE_H or bh > 0.25 * pw:
                    continue
                if bw / bh < 1.2:        # a line is wider than tall
                    continue
                dens = cv2.countNonZero(ink[by:by + bh, bx:bx + bw]) / (bw * bh)
                if not (0.15 <= dens <= 0.85):   # solid shapes / speckle
                    continue
                out.append([x + bx, y + by, bw, bh])
    return _merge_boxes(out)


def detect_text_boxes(png_path, det=None):
    """(boxes, method) for one panel. det: a loaded DBNet model, or None for
    the classic fallback. A per-panel detect error returns ([], method) —
    an empty read must never take the pipeline down."""
    import cv2
    img = cv2.imread(str(png_path))
    if img is None:
        return [], "unreadable"
    try:
        if det is not None:
            return detect_dbnet(img, det), "dbnet"
        return detect_classic(img), "cv2-classic"
    except Exception as e:
        print(f"text_boxes: detect failed on {Path(png_path).name} "
              f"({type(e).__name__}: {e})")
        return [], "error"


# ---- sidecar cache -------------------------------------------------------

def _load_cache(path):
    try:
        d = json.loads(Path(path).read_text())
        if isinstance(d, dict) and "panels" in d:
            return d
    except Exception:
        pass
    return {"_meta": {"version": CACHE_VERSION, "model": MODEL_NAME},
            "panels": {}}


def ensure_boxes(sidecar_path, panel_paths, debug_dir=None):
    """{panel_name: [[x,y,w,h],...]} for every path, detecting only panels
    missing from the sidecar or whose file mtime changed (cleaned panels
    replace originals under the same name; mtime catches the swap). The
    detector loads lazily — a fully-cached chapter never touches the model.
    Each entry records which method produced it, so a chapter detected under
    the classic fallback re-detects once the model file lands."""
    sidecar_path = Path(sidecar_path)
    cache = _load_cache(sidecar_path)
    det = "unloaded"
    changed = False
    out = {}
    for p in panel_paths:
        p = Path(p)
        if p.name in out:
            continue
        st = p.stat()
        rec = cache["panels"].get(p.name)
        want = "dbnet" if (MODEL_DIR / MODEL_NAME).exists() else "cv2-classic"
        if (rec and abs(rec.get("mtime", -1) - st.st_mtime) < 1e-6
                and rec.get("method") == want):
            out[p.name] = rec["boxes"]
            continue
        if det == "unloaded":
            det = load_detector()
        boxes, method = detect_text_boxes(p, det)
        cache["panels"][p.name] = {"mtime": st.st_mtime, "method": method,
                                   "boxes": boxes}
        out[p.name] = boxes
        changed = True
        print(f"  text_boxes: {p.name}: {len(boxes)} line(s) [{method}]")
        if debug_dir:
            _write_overlay(p, boxes, Path(debug_dir), method)
    if changed:
        sidecar_path.write_text(json.dumps(cache))
    return out


def _write_overlay(panel, boxes, debug_dir, method):
    """Debug PNG: green line boxes + heights on the panel, so a human can
    eyeball tile-offset correctness before the render side trusts a number."""
    import cv2
    img = cv2.imread(str(panel))
    if img is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    for x, y, w, h in boxes:
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 220, 0), 2)
        cv2.putText(img, f"h{h}", (x, max(y - 4, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    cv2.putText(img, method, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (255, 0, 0), 2)
    cv2.imwrite(str(debug_dir / panel.name), img)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panels", required=True,
                    help="directory of panel PNGs (panels/ or panels_clean/)")
    ap.add_argument("--out", required=True,
                    help="sidecar JSON to write/update "
                         "(<slug>.textboxes.json)")
    ap.add_argument("--debug-overlays", default=None,
                    help="also write per-panel overlay PNGs (green line "
                         "boxes) into this directory")
    ap.add_argument("--only", action="append", default=[],
                    help="restrict to these panel filenames (repeatable)")
    args = ap.parse_args()
    panels_dir = Path(args.panels)
    paths = sorted(panels_dir.glob("p*.png")) or sorted(panels_dir.glob("*.png"))
    if args.only:
        keep = set(args.only)
        paths = [p for p in paths if p.name in keep]
    if not paths:
        sys.exit(f"no panels found in {panels_dir}")
    print(f"Detecting text boxes on {len(paths)} panel(s) -> {args.out}")
    out = ensure_boxes(args.out, paths, debug_dir=args.debug_overlays)
    n_text = sum(1 for b in out.values() if b)
    print(f"Done: {n_text}/{len(out)} panels carry detected text lines.")


if __name__ == "__main__":
    main()
