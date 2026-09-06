#!/usr/bin/env python3
"""clean_bubbles.py — STAGE 4 (optional): blank/inpaint speech-bubble text so
panels read as clean art in the video. Off by default; enabled via --clean.

Two tiers:
  cv     — fast, free, deterministic. Find bright bubble blobs, mask the dark
           text strokes inside them, cv2.inpaint (Telea). No network.
  gemini — highest quality. gateway.image_edit() inpaints each panel ("remove
           all text inside speech bubbles, keep bubbles and art identical").
           Costs one gateway call per panel.

Writes panels_clean/<name>. panel_render.py auto-prefers panels_clean/ when it
exists, so this stage is transparent to the renderer.

Usage:
    python3 clean_bubbles.py --panels output/<slug>/panels \
        --out output/<slug>/panels_clean --tier cv
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

import gateway

GEMINI_INSTRUCTION = (
    "Edit this manga/manhwa panel: remove ALL text inside speech bubbles and "
    "narration boxes, leaving the bubbles/boxes empty. Keep the artwork, line "
    "art, characters, bubbles and composition EXACTLY the same. Do not add text.")


def clean_cv(panel_path, out_path):
    """Deterministic CV cleanup: inpaint dark text inside bright bubble regions."""
    img = cv2.imread(str(panel_path))
    if img is None:
        raise RuntimeError(f"cannot read {panel_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # bubble candidate = large bright regions
    _, bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    bubble = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=2)
    bubble = cv2.morphologyEx(bubble, cv2.MORPH_OPEN, kernel, iterations=1)
    # dark text = dark pixels that fall inside a bubble region
    _, dark = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
    text_mask = cv2.bitwise_and(dark, bubble)
    # dilate the text mask so inpaint covers stroke edges
    text_mask = cv2.dilate(text_mask,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                           iterations=2)
    if cv2.countNonZero(text_mask) == 0:
        # nothing detected -> copy through unchanged
        cv2.imwrite(str(out_path), img)
        return False
    result = cv2.inpaint(img, text_mask, 3, cv2.INPAINT_TELEA)
    cv2.imwrite(str(out_path), result)
    return True


def clean_gemini(panel_path, out_path):
    gateway.image_edit(panel_path, GEMINI_INSTRUCTION, out_path)
    return True


def clean(panels_dir, out_dir, tier="cv"):
    panels = sorted(Path(panels_dir).glob("p*.png"), key=lambda p: p.name)
    if not panels:
        sys.exit(f"no panels in {panels_dir}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n_changed = 0
    for i, p in enumerate(panels, 1):
        out = out_dir / p.name
        if out.exists() and out.stat().st_size > 0:
            continue
        try:
            if tier == "gemini":
                changed = clean_gemini(p, out)
            else:
                changed = clean_cv(p, out)
        except Exception as e:
            print(f"  ! {p.name} cleanup failed ({e}); copying original")
            import shutil
            shutil.copy(p, out)
            changed = False
        n_changed += int(changed)
        print(f"  [{i}/{len(panels)}] {p.name}{' (cleaned)' if changed else ''}")
    print(f"  -> {n_changed}/{len(panels)} panels modified -> {out_dir}")
    return n_changed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panels", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tier", choices=["cv", "gemini"], default="cv")
    args = ap.parse_args()
    clean(args.panels, args.out, args.tier)


if __name__ == "__main__":
    main()
