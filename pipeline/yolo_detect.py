#!/usr/bin/env python3
"""yolo_detect.py — manga109 text detector for the webtoon split guard (gap-004).

ADOPTED 2026-09-09 from improvements/experiments/exp-004-segmentation/
yolo_detect.py. At prototype stage the detector ran as a quarantined
subprocess in the experiment venv because its library (dghs-imgutils) was not
allowed into the pipeline venv. At adoption the FULL library still cannot
move in-process — it hard-requires numpy<2 (faster-whisper needs numpy>=2)
and opencv-contrib-python (whose `cv2` package collides with our
opencv-python-headless) — so instead the small slice of its yolo inference
glue we actually use is VENDORED below (dghs-imgutils is MIT;
_yolo_xywh2xyxy/_yolo_nms/pre/postprocess adapted from
imgutils.generic.yolo). Runtime deps are only onnxruntime (MIT) +
huggingface-hub (Apache-2.0) + numpy + Pillow, all in
pipeline/requirements.txt.

NOTE on weights: the ONNX checkpoint (deepghs/manga109_yolo,
v2021.12.30_n_yv11, ~10 MB, auto-downloaded to ~/.cache/huggingface on first
use) is an Ultralytics-trained YOLO11n whose embedded metadata declares the
Ultralytics AGPL-3.0 license — recorded in README.md and the ledger. The
2021 nano is used, NOT the newer 2023 checkpoints, because the 2023 weights
return ZERO text detections on colored webtoon bubbles (research §5 risk,
confirmed empirically in exp-004 smoke).

Windowing (research §3): tall regions are scanned in windows of `win` rows
with `stride` step (256 px overlap by default) so a bubble on a window edge
is still seen whole by the neighbor; width is downscaled to `max_w` before
inference and boxes are scaled back. Duplicate boxes seen from two
overlapping windows are merged by IoU.

Library API (used by segment_panels.py):
    detect_text_boxes(regions) -> [[x0, y0, x1, y1], ...] per region.
    Regions may be numpy BGR arrays (cv2 convention), PIL images, or paths.
    Raises on any failure (missing deps, offline model download, bad ONNX)
    so the caller can fall back to the blob guard — never swallow here.

CLI (kept for parity with the experiment script / debugging):
    python3 yolo_detect.py region.png [more.png ...] [--conf 0.2]
        [--win 1280] [--stride 1024] [--max-w 640]
"""
import argparse
import ast
import json
import sys

DEFAULT_REPO = "deepghs/manga109_yolo"
DEFAULT_MODEL = "v2021.12.30_n_yv11"
DEFAULT_CONF = 0.2
DEFAULT_IOU = 0.7        # imgutils yolo_predict default; keep for parity
DEFAULT_WIN = 1280
DEFAULT_STRIDE = 1024
DEFAULT_MAX_W = 640

# one ONNX session per (repo, model) per process — the model load dominates
# wall time, so segment_panels' batched call pays it exactly once.
_SESSIONS = {}


def _get_session(repo, model):
    """Lazy-load the ONNX model from the HF cache (downloads on first use).
    Returns (InferenceSession, (in_w, in_h), labels)."""
    key = (repo, model)
    if key not in _SESSIONS:
        import onnxruntime
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=repo, repo_type="model",
                               filename=f"{model}/model.onnx")
        sess = onnxruntime.InferenceSession(
            path, providers=["CPUExecutionProvider"])
        meta = sess.get_modelmeta().custom_metadata_map
        # imgsz like "[640, 640]"; names like "{0: 'body', ..., 3: 'text'}"
        imgsz = tuple(json.loads(meta["imgsz"])) if "imgsz" in meta \
            else (640, 640)
        names = ast.literal_eval(meta["names"])
        labels = [names[i] for i in range(len(names))]
        _SESSIONS[key] = (sess, imgsz, labels)
    return _SESSIONS[key]


# ---- vendored from dghs-imgutils (MIT), imgutils/generic/yolo.py ----------
def _yolo_xywh2xyxy(x):
    """(cx, cy, w, h) -> (x1, y1, x2, y2), YOLOv8 convention."""
    import numpy as np
    y = np.copy(x)
    y[..., 0] = x[..., 0] - x[..., 2] / 2
    y[..., 1] = x[..., 1] - x[..., 3] / 2
    y[..., 2] = x[..., 0] + x[..., 2] / 2
    y[..., 3] = x[..., 1] + x[..., 3] / 2
    return y


def _yolo_nms(boxes, scores, iou_threshold=0.7):
    """Greedy NMS, identical math to imgutils so adopted boxes match the
    experiment's subprocess detector bit-for-bit."""
    import numpy as np
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(iou <= iou_threshold)[0] + 1]
    return keep


def _xy_postprocess(x, y, old_size, new_size):
    import numpy as np
    ow, oh = old_size
    nw, nh = new_size
    x, y = x / nw * ow, y / nh * oh
    return (int(np.clip(x, 0, ow).round()), int(np.clip(y, 0, oh).round()))
# ---- end vendored section --------------------------------------------------


def _predict(pil_img, conf, repo=DEFAULT_REPO, model=DEFAULT_MODEL,
             iou=DEFAULT_IOU):
    """Run the detector on ONE PIL image; return [(box, label, conf)].
    Mirrors imgutils yolo_predict for the nms-based manga109 checkpoints:
    fixed resize to the model's imgsz (no letterboxing — same as the
    experiment path), CHW float32 /255, then conf filter + NMS."""
    import numpy as np
    sess, (in_w, in_h), labels = _get_session(repo, model)
    old_size = (pil_img.width, pil_img.height)
    data = np.asarray(pil_img.resize((in_w, in_h)))
    data = (np.transpose(data, (2, 0, 1)) / 255.0).astype(np.float32)[None]
    output, = sess.run(["output0"], {"images": data})
    output = output[0]                       # [4+cls, boxes]
    max_scores = output[4:, :].max(axis=0)
    output = output[:, max_scores > conf].transpose(1, 0)
    boxes, scores = output[:, :4], output[:, 4:]
    if not boxes.size:
        return []
    boxes = _yolo_xywh2xyxy(boxes)
    idx = _yolo_nms(boxes, scores.max(axis=1), iou_threshold=iou)
    boxes, scores = boxes[idx], scores[idx]
    dets = []
    for box, score in zip(boxes, scores):
        x0, y0 = _xy_postprocess(box[0], box[1], old_size, (in_w, in_h))
        x1, y1 = _xy_postprocess(box[2], box[3], old_size, (in_w, in_h))
        k = score.argmax()
        dets.append(((x0, y0, x1, y1), labels[k], float(score[k])))
    return dets


def _iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def _to_pil(region):
    """numpy BGR array (cv2 convention) / PIL image / path -> PIL RGB."""
    from PIL import Image
    if isinstance(region, Image.Image):
        return region.convert("RGB")
    try:
        import numpy as np
        if isinstance(region, np.ndarray):
            # BGR -> RGB without importing cv2 (keep this module cv2-free)
            return Image.fromarray(region[:, :, ::-1])
    except ImportError:
        pass
    return Image.open(region).convert("RGB")


def _detect_one(region, conf, win, stride, max_w, repo, model):
    """Windowed detection over one (possibly very tall) region; returns
    merged `text` boxes in the region's own coordinates. Logic identical to
    the experiment script's _detect_one."""
    from PIL import Image
    img = _to_pil(region)
    w, h = img.size
    scale = 1.0
    if w > max_w:
        scale = max_w / float(w)
        img = img.resize((max_w, max(1, int(round(h * scale)))),
                         Image.LANCZOS)
    sw, sh = img.size
    swin = max(1, int(round(win * scale)))
    sstride = max(1, int(round(stride * scale)))

    boxes = []
    y = 0
    while True:
        y1 = min(sh, y + swin)
        crop = img.crop((0, y, sw, y1))
        for (bx0, by0, bx1, by1), label, _c in _predict(crop, conf,
                                                        repo, model):
            if label != "text":
                continue
            boxes.append([bx0 / scale, (by0 + y) / scale,
                          bx1 / scale, (by1 + y) / scale])
        if y1 >= sh:
            break
        y += sstride

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


def detect_text_boxes(regions, conf=DEFAULT_CONF, win=DEFAULT_WIN,
                      stride=DEFAULT_STRIDE, max_w=DEFAULT_MAX_W,
                      repo=DEFAULT_REPO, model=DEFAULT_MODEL):
    """Detect `text` boxes in each region; returns one box list per region,
    in order. Raises on any failure so callers can fall back gracefully."""
    return [_detect_one(r, conf, win, stride, max_w, repo, model)
            for r in regions]


def _detect_all_one(region, conf, win, stride, max_w, repo, model):
    """Windowed detection keeping ALL manga109 classes (body/face/frame/text)
    with labels + confidences. Same windowing/scaling math as _detect_one;
    duplicates across overlapping windows are merged per-label by IoU, the
    surviving box keeping the max confidence (exp-017: verify_panels'
    text-only gate needs body/face as an 'is there drawn art?' veto)."""
    from PIL import Image
    img = _to_pil(region)
    w, h = img.size
    scale = 1.0
    if w > max_w:
        scale = max_w / float(w)
        img = img.resize((max_w, max(1, int(round(h * scale)))),
                         Image.LANCZOS)
    sw, sh = img.size
    swin = max(1, int(round(win * scale)))
    sstride = max(1, int(round(stride * scale)))

    dets = []
    y = 0
    while True:
        y1 = min(sh, y + swin)
        crop = img.crop((0, y, sw, y1))
        for (bx0, by0, bx1, by1), label, c in _predict(crop, conf,
                                                       repo, model):
            dets.append([label, float(c),
                         bx0 / scale, (by0 + y) / scale,
                         bx1 / scale, (by1 + y) / scale])
        if y1 >= sh:
            break
        y += sstride

    merged = []
    for d in sorted(dets, key=lambda d: d[3]):
        for m in merged:
            if m[0] == d[0] and _iou(d[2:], m[2:]) > 0.3:
                m[1] = max(m[1], d[1])
                m[2], m[3] = min(m[2], d[2]), min(m[3], d[3])
                m[4], m[5] = max(m[4], d[4]), max(m[5], d[5])
                break
        else:
            merged.append(list(d))
    return [[d[0], round(d[1], 3)] + [round(v, 1) for v in d[2:]]
            for d in merged]


def detect_objects(regions, conf=DEFAULT_CONF, win=DEFAULT_WIN,
                   stride=DEFAULT_STRIDE, max_w=DEFAULT_MAX_W,
                   repo=DEFAULT_REPO, model=DEFAULT_MODEL):
    """All-classes variant of detect_text_boxes: one
    [[label, conf, x0, y0, x1, y1], ...] list per region, in order. The
    model always emitted body/face/frame/text; detect_text_boxes filters to
    text for the segmentation guard — this returns everything so callers can
    ask 'does this panel contain drawn characters?'. Raises on any failure
    so callers can fail open, same contract as detect_text_boxes."""
    return [_detect_all_one(r, conf, win, stride, max_w, repo, model)
            for r in regions]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--win", type=int, default=DEFAULT_WIN)
    ap.add_argument("--stride", type=int, default=DEFAULT_STRIDE)
    ap.add_argument("--max-w", type=int, default=DEFAULT_MAX_W)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    results = detect_text_boxes(args.images, conf=args.conf, win=args.win,
                                stride=args.stride, max_w=args.max_w,
                                repo=args.repo, model=args.model)
    # single image -> flat list (back-compat with the experiment script)
    json.dump(results[0] if len(args.images) == 1 else results, sys.stdout)


if __name__ == "__main__":
    main()
