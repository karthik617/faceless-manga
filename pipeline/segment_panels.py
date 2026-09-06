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
import sys
from pathlib import Path

import cv2
import numpy as np

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
WEBTOON_ASPECT = 2.2   # height/width above this -> treat page as a webtoon strip


def _list_pages(raw_dir):
    files = [p for p in Path(raw_dir).iterdir()
             if p.suffix.lower() in IMG_EXTS]
    def key(p):
        import re
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", p.name)]
    return sorted(files, key=key)


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
                          min_slice_px=220):
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
    for b in boxes:
        x, y, bw, bh = b
        while bh > max_slice_px:
            # search a band for the flattest window of rows and cut there.
            # The band is capped at max_slice_px so every emitted piece
            # respects the cap regardless of where the flattest row is.
            lo = y + min(int(bh * 0.35), int(max_slice_px * 0.5))
            hi = y + min(int(bh * 0.75), max_slice_px)
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


def segment(raw_dir, out_dir, mode="auto"):
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
    pmode0 = (_guess_mode(imgs[0][1]) if mode == "auto" else mode) if imgs else mode
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
        boxes = segment_strip_webtoon(strip)
        if not boxes:
            boxes = [[0, 0, strip.shape[1], strip.shape[0]]]
        skipped = 0
        for (x, y, bw, bh) in boxes:
            crop = strip[y:y + bh, x:x + bw]
            if _content_frac(crop) < MIN_CONTENT_FRAC:
                skipped += 1
                continue
            pn += 1
            cv2.imwrite(str(out_dir / f"p{pn:04}.png"), crop)
            index.append({"panel": f"p{pn:04}.png", "page": "strip",
                          "bbox": [int(x), int(y), int(bw), int(bh)],
                          "mode": "webtoon"})
        print(f"  strip: {len(boxes)} slices, {skipped} empty skipped")
    else:
        for page_i, (page_path, img) in enumerate(imgs, 1):
            pmode = _guess_mode(img) if mode == "auto" else mode
            if pmode == "webtoon":
                boxes = segment_strip_webtoon(img)
            else:
                boxes = segment_page_manga(img)
            if len(boxes) < 1:
                h, w = img.shape[:2]
                boxes = [[0, 0, w, h]]
            for (x, y, bw, bh) in boxes:
                crop = img[y:y + bh, x:x + bw]
                if pmode == "webtoon" and _content_frac(crop) < MIN_CONTENT_FRAC:
                    continue
                pn += 1
                out_path = out_dir / f"p{pn:04}.png"
                cv2.imwrite(str(out_path), crop)
                index.append({"panel": out_path.name, "page": page_i,
                              "bbox": [int(x), int(y), int(bw), int(bh)],
                              "mode": pmode})
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
    args = ap.parse_args()
    segment(args.raw, args.out, args.mode)


if __name__ == "__main__":
    main()
