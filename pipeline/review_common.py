#!/usr/bin/env python3
"""review_common.py — shared plumbing for the human-like QC reviewers
(review_video.py, review_short.py, review_thumbs.py).

Frame extraction, frame statistics, perceptual hashing, LLM-JSON parsing and
the severity-sorted report writer. Exit-code convention across reviewers:
2 when HIGH faults remain, 0 otherwise.
"""
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

SEV_RANK = {"high": 0, "medium": 1, "low": 2}

_PHASH_SIDE = 16


def ffprobe_dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def ffprobe_res(p):
    """(width, height) of the first video stream, or (0, 0)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return 0, 0


def grab(video, t, out, h=540):
    """Extract one frame at t seconds, scaled to height h. True on success."""
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                    "-frames:v", "1", "-vf", f"scale=-2:{h}", str(out)],
                   capture_output=True)
    return out.exists() and out.stat().st_size > 0


def frame_stats(path):
    """(mean_luma, content_fraction) — fraction of pixels >30 from median."""
    g = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    bg = float(np.median(g))
    frac = float((np.abs(g - bg) > 30).mean())
    return float(g.mean()), frac


def sharpness(path):
    """Variance of a Laplacian — low values = blurry/mushy image."""
    g = np.asarray(Image.open(path).convert("L"), dtype=np.float32)
    lap = (np.abs(np.diff(g, 2, axis=0)).mean()
           + np.abs(np.diff(g, 2, axis=1)).mean())
    return float(lap)


def phash(path):
    """Tiny average-hash of an image (64-bit int) for near-duplicate checks."""
    g = np.asarray(Image.open(path).convert("L")
                   .resize((_PHASH_SIDE, _PHASH_SIDE)), dtype=np.float32)
    bits = (g > g.mean()).flatten()
    return int("".join("1" if b else "0" for b in bits[:64]), 2)


def hamming(a, b):
    return bin(a ^ b).count("1")


def strip_fence(t):
    t = t.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    return re.sub(r"\s*```$", "", t).strip()


def parse_json(text, expect="array"):
    t = strip_fence(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        oc, cc = ("[", "]") if expect == "array" else ("{", "}")
        i, j = t.find(oc), t.rfind(cc)
        if i != -1 and j > i:
            return json.loads(t[i:j + 1])
    raise ValueError(f"unparseable reviewer output: {text[:200]}")


def write_report(out_base, faults, title, extra_lines=()):
    """Severity-sorted <base>.json + <base>.md. Returns (n_high, n_medium)."""
    faults = sorted(faults, key=lambda f: (SEV_RANK.get(f.get("severity"), 3),
                                           f.get("t", 0)))
    high = [f for f in faults if f.get("severity") == "high"]
    med = [f for f in faults if f.get("severity") == "medium"]
    Path(f"{out_base}.json").write_text(json.dumps(faults, indent=2))
    lines = [f"# {title}",
             f"{len(faults)} fault(s): {len(high)} high, {len(med)} medium\n"]
    for f in faults:
        t = f.get("t", 0)
        mm, ss = int(t // 60), int(t % 60)
        loc = f" scene {f['scene']}" if f.get("scene") is not None else ""
        loc += f" [{f['item']}]" if f.get("item") else ""
        lines.append(f"- [{f['severity'].upper()}] {mm}:{ss:02d}{loc} "
                     f"{f['type']}: {f.get('detail', '')}")
    lines.extend(extra_lines)
    Path(f"{out_base}.md").write_text("\n".join(lines) + "\n")
    return len(high), len(med)
