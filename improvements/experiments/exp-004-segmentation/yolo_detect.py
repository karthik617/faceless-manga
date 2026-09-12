#!/usr/bin/env python3
"""yolo_detect.py — quarantined manga109 text detector (exp-004).

Runs ONLY under the experiment venv (improvements/experiments/
exp-004-segmentation/venv) so dghs-imgutils + onnxruntime never enter the
pipeline venv or requirements.txt. pipeline/segment_panels.py shells out to
this script when --split-guard yolo is requested and reads JSON boxes back.

Input : an image path (a >max_slice slice region of the stitched strip).
Output: JSON list of [x0, y0, x1, y1] `text` boxes in the image's own
        coordinates, printed to stdout.

Windowing (research §3): tall regions are scanned in windows of --win rows
with --stride step (256 px overlap by default) so a bubble on a window edge
is still seen whole by the neighbor; width is downscaled to --max-w before
inference and boxes are scaled back. Duplicate boxes seen from two
overlapping windows are merged by IoU.

Accepts MULTIPLE image paths in one invocation (the model import dominates
wall time, so the pipeline batches every over-tall slice of a strip into one
subprocess call): output is then a JSON list of box lists, one per image, in
argument order. A single image still yields a single flat box list (back-
compat with early smoke tests).

Usage:
    venv/bin/python3 yolo_detect.py region.png [more.png ...] [--conf 0.2]
        [--win 1280] [--stride 1024] [--max-w 640]
"""
import argparse
import json
import sys


def _iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def _detect_one(path, args, Image, yolo_predict):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = 1.0
    if w > args.max_w:
        scale = args.max_w / float(w)
        img = img.resize((args.max_w, max(1, int(round(h * scale)))),
                         Image.LANCZOS)
    sw, sh = img.size
    win = max(1, int(round(args.win * scale)))
    stride = max(1, int(round(args.stride * scale)))

    boxes = []
    y = 0
    while True:
        y1 = min(sh, y + win)
        crop = img.crop((0, y, sw, y1))
        dets = yolo_predict(crop, args.repo, args.model,
                            conf_threshold=args.conf)
        for (bx0, by0, bx1, by1), label, _conf in dets:
            if label != "text":
                continue
            boxes.append([bx0 / scale, (by0 + y) / scale,
                          bx1 / scale, (by1 + y) / scale])
        if y1 >= sh:
            break
        y += stride

    # merge duplicates across overlapping windows (IoU > 0.3 -> union)
    merged = []
    for b in sorted(boxes, key=lambda b: b[1]):
        for m in merged:
            if _iou(b, m) > 0.3:
                m[0], m[1] = min(m[0], b[0]), min(m[1], b[1])
                m[2], m[3] = max(m[2], b[2]), max(m[3], b[3])
                break
        else:
            merged.append(list(b))
    return [[round(v, 1) for v in b] for b in merged]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--conf", type=float, default=0.2)
    ap.add_argument("--win", type=int, default=1280)
    ap.add_argument("--stride", type=int, default=1024)
    ap.add_argument("--max-w", type=int, default=640)
    ap.add_argument("--repo", default="deepghs/manga109_yolo")
    # 2021 nano: the only manga109_yolo checkpoint that detects colored
    # webtoon bubble text in smoke tests (2023 variants return zero)
    ap.add_argument("--model", default="v2021.12.30_n_yv11")
    args = ap.parse_args()

    from PIL import Image
    from imgutils.generic.yolo import yolo_predict

    results = [_detect_one(p, args, Image, yolo_predict)
               for p in args.images]
    json.dump(results[0] if len(args.images) == 1 else results, sys.stdout)


if __name__ == "__main__":
    main()
