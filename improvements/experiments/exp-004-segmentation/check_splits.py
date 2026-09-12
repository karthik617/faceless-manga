#!/usr/bin/env python3
"""check_splits.py — deterministic acceptance-criterion gate for exp-004.

Given the raw strip pages + a panels_index.json produced by segment_panels.py,
recomputes which panel boundaries are FORCED cuts (interior cuts not sitting
on a real gutter row) and counts how many land inside a detected text box.
Acceptance criterion (gap-004): zero with --split-guard on.

exp-004 v2 adds two more audits (both on by default; the combined acceptance
is zero violations across all three):
  * ART audit — forced cuts whose row ink density exceeds the splitter's
    INK_CEIL (cut through a dense text-free drawing; the scene-15/21
    residual class from the downstream benchmark).
  * GUTTER audit — over-tall slices (> OVER_TALL x median) whose gutter
    edges sit within SNAP_PAD+1 of "gutter" rows carrying real marks
    (spread > SNAP_SPREAD): clipped SFX/text tips (the scene-16 residual).
All thresholds are imported from segment_panels so the gate can never drift
from the guard.

Text boxes come from the same detectors the guard uses (--detector blob|yolo);
CPU ONNX inference and the blob heuristic are both deterministic, so repeated
runs give identical counts.

Usage:
    ./venv/bin/python3 improvements/experiments/exp-004-segmentation/check_splits.py \
        --raw <raw dir> --index <panels_index.json> [--detector blob|yolo]

Exit 0 when no violation in any audit; exit 1 otherwise, listing offenders.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# import the pipeline's own machinery so the checker can never drift from the
# splitter's definition of "gutter" or from the guard's detectors
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipeline"))
import segment_panels as sp  # noqa: E402


def _stitched_strip(raw_dir):
    """Rebuild the same virtual strip segment() stitches (common-width tiles,
    odd-width covers excluded) so index bboxes line up with strip rows."""
    pages = sp._list_pages(raw_dir)
    imgs = []
    for p in pages:
        im = cv2.imread(str(p))
        if im is not None:
            imgs.append(im)
    if not imgs:
        sys.exit(f"no readable pages in {raw_dir}")
    from collections import Counter
    common_w = Counter(im.shape[1] for im in imgs).most_common(1)[0][0]
    return np.vstack([im for im in imgs if im.shape[1] == common_w])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--detector", choices=["blob", "yolo"], default="blob")
    ap.add_argument("--pad", type=int, default=0,
                    help="extra margin (px) around boxes when testing "
                         "intersection; 0 = box edges themselves")
    ap.add_argument("--no-art", action="store_true",
                    help="skip the v2 ink-ceiling art audit (v1 gate only)")
    ap.add_argument("--no-gutter", action="store_true",
                    help="skip the v2 gutter-edge SFX audit (v1 gate only)")
    args = ap.parse_args()

    strip = _stitched_strip(args.raw)
    h, w = strip.shape[:2]
    index = json.loads(Path(args.index).read_text())
    entries = [e for e in index if e.get("page") == "strip"]
    if not entries:
        sys.exit("index has no stitched-strip panels; nothing to check")

    # classify each interior cut: a cut coinciding with a real gutter row was
    # a natural split; anything else was a FORCED cut placed by the splitter
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32)
    row_min, row_max = gray.min(axis=1), gray.max(axis=1)
    row_mean = gray.mean(axis=1)
    row_is_gutter = ((row_min >= 245) | (row_mean >= 245) |
                     (row_max <= 12) | ((row_max - row_min) <= 8))

    cuts = []
    for e in sorted(entries, key=lambda e: e["bbox"][1])[1:]:
        y = e["bbox"][1]
        band = row_is_gutter[max(0, y - 2):min(h, y + 3)]
        if not band.any():                    # not on/next to a gutter row
            cuts.append((e["panel"], y))
    print(f"strip {w}x{h}: {len(entries)} strip panels, "
          f"{len(cuts)} forced (non-gutter) cuts")

    boxes = sp._guard_text_boxes(strip, args.detector)
    print(f"{args.detector} detector: {len(boxes)} text boxes")

    bad = []
    for panel, y in cuts:
        for (x0, y0, x1, y1) in boxes:
            if y0 - args.pad < y < y1 + args.pad:
                bad.append((panel, y, (x0, y0, x1, y1)))
    print(f"forced cuts intersecting a text box: {len(bad)}")
    for panel, y, box in bad:
        print(f"  BAD {panel}: cut y={y} inside box {box}")

    # ---- v2 ART audit: forced cuts through high-ink (dense drawing) rows ----
    bad_art = []
    if not args.no_art:
        row_ink = (gray < sp.INK_GRAY).mean(axis=1)
        for panel, y in cuts:
            if row_ink[y] > sp.INK_CEIL:
                bad_art.append((panel, y, float(row_ink[y])))
        print(f"forced cuts through high-ink art rows "
              f"(ink > {sp.INK_CEIL}): {len(bad_art)}")
        for panel, y, ink in bad_art:
            print(f"  BAD-ART {panel}: cut y={y} row ink={ink:.2f}")

    # ---- v2 GUTTER audit: over-tall slices whose gutter edge clips marks ----
    # mirrors _snap_gutter_edges: an edge is bad when inky "gutter" rows
    # (spread > SNAP_SPREAD) sit within SNAP_PAD+1 px just OUTSIDE it, i.e.
    # the splitter left SFX/text tips in the gutter it cut along.
    bad_gut = []
    if not args.no_gutter:
        row_spread = row_max - row_min
        ordered = sorted(entries, key=lambda e: e["bbox"][1])
        med = float(np.median([e["bbox"][3] for e in ordered]))
        for e in ordered:
            _, y, _, bh = e["bbox"]
            if bh <= med * sp.OVER_TALL:
                continue
            for edge, rng in (("top", range(y - 1, max(0, y - sp.SNAP_PAD - 2),
                                            -1)),
                              ("bottom", range(y + bh, min(h, y + bh +
                                                           sp.SNAP_PAD + 2)))):
                for r in rng:
                    if row_is_gutter[r] and row_spread[r] > sp.SNAP_SPREAD:
                        bad_gut.append((e["panel"], edge, r,
                                        float(row_spread[r])))
                        break
        print(f"over-tall-slice gutter edges clipping marks: {len(bad_gut)}")
        for panel, edge, r, spr in bad_gut:
            print(f"  BAD-GUTTER {panel}: {edge} edge, inky gutter row y={r} "
                  f"(spread={spr:.0f})")

    sys.exit(1 if (bad or bad_art or bad_gut) else 0)


if __name__ == "__main__":
    main()
