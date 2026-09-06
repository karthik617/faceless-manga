#!/usr/bin/env python3
"""cutout.py — background removal for 2.5D parallax.

Given a panel image, produce a foreground-only RGBA PNG (alpha where the
background was) using rembg's u2net model. The original panel serves as the
background layer; the cutout is the foreground layer. Results are cached so a
panel is only segmented once.

rembg must be importable (pip install rembg filetype) and downloads the u2net
model on first use (~170MB). Segmentation quality on manga line-art is variable
— use panel_render.py --parallax-test to eyeball one before a full render.
"""
from pathlib import Path

_session = None


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session
        _session = new_session("u2net")
    return _session


def cutout(panel_path, out_dir):
    """Return path to a cached RGBA cutout PNG of `panel_path`'s foreground.
    Raises if rembg is unavailable so the caller can fall back to flat render."""
    panel_path = Path(panel_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (panel_path.stem + "_fg.png")
    if out.exists() and out.stat().st_size > 0:
        return out
    from rembg import remove
    from PIL import Image
    img = Image.open(panel_path).convert("RGBA")
    result = remove(img, session=_get_session())
    result.save(out)
    return out


def alpha_coverage(png_path):
    """Fraction of pixels that are at least partly opaque (0..1). Used to sanity
    -check a cutout: ~0 means the model removed everything (bad), ~1 means it
    removed nothing (also unhelpful for parallax)."""
    from PIL import Image
    import numpy as np
    a = np.asarray(Image.open(png_path).convert("RGBA"))[:, :, 3]
    return float((a > 16).mean())
