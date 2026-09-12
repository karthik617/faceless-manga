#!/usr/bin/env python3
"""smoke.py — exp-009 smoke test: text-aware framing on the golden chapter.

1. Detects text boxes for scene 5's panels (incl. p0024, the "orange info
   card" panel that drew the ch2 phone_readability MEDIUM) via the sidecar
   cache and asserts the info-card lines are found.
2. Computes the text-aware crop for p0024 and asserts: median rendered line
   height crosses MIN_TEXT_H, the crop fully contains every kept box, and
   the crop respects the panel bounds.
3. Renders the p0024 shot BOTH ways via render_panel_scene (crop_rect=None
   vs computed) and extracts before/after frames into samples/ so a human
   can see the text grow.
4. Classic-fallback check: detect_classic on p0024 must find the card lines.
5. layout_smart inertness: plan_layout(text_boxes=None) must equal the plan
   without the param (adopted behavior preserved); with measured boxes a
   tiny-text panel's required cell height must rise.

Run: ./venv/bin/python3 improvements/experiments/exp-009-phone-readability/smoke.py
"""
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipeline"))
import text_boxes as tb
import panel_render as pr
import layout_smart as ls
import make_video as mv

SAMPLES = Path(__file__).resolve().parent / "samples"
SAMPLES.mkdir(exist_ok=True)
PROJECT = ROOT / "output" / "the-world-after-the-fall-ch2"
W, H = 1920, 1080
DUR = 4.0


def main():
    data = json.loads((PROJECT / f"{PROJECT.name}.json").read_text())
    scene5 = data["scenes"][5]          # reviewer's scene 5 (0-based)
    assert "panels/p0024.png" in scene5["panels"], scene5["panels"]
    panel = PROJECT / "panels" / "p0024.png"

    # ---- 1. detection through the sidecar cache ----
    sidecar = SAMPLES / "smoke.textboxes.json"
    sidecar.unlink(missing_ok=True)
    paths = [PROJECT / p for p in scene5["panels"]]
    boxes = tb.ensure_boxes(sidecar, paths)
    b24 = boxes["p0024.png"]
    assert len(b24) >= 5, f"expected the info-card lines on p0024, got {b24}"
    cache = json.loads(sidecar.read_text())
    assert cache["panels"]["p0024.png"]["method"] == "dbnet"
    # cache hit: second call must not re-detect (method/mtime key)
    again = tb.ensure_boxes(sidecar, paths)
    assert again["p0024.png"] == b24
    print(f"1. detection: p0024 {len(b24)} lines, heights "
          f"{sorted(b[3] for b in b24)}")

    # ---- 2. crop math + containment guards ----
    from PIL import Image
    with Image.open(panel) as im:
        pw, ph = im.size
    fit = min(W / pw, H / ph)
    med0 = statistics.median(b[3] for b in b24)
    crop = pr._text_aware_crop(panel, b24, W, H)
    assert crop is not None, "small-text panel must trigger a crop"
    cx, cy, cw, ch = crop
    assert 0 <= cx and 0 <= cy and cx + cw <= pw and cy + ch <= ph, crop
    # never crop THROUGH a box: each box is fully inside or fully outside;
    # fully-outside drops are legal only because another box stays visible
    n_in = 0
    for b in b24:
        bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        inside = (cx <= bx0 and cy <= by0 and bx1 <= cx + cw
                  and by1 <= cy + ch)
        outside = (bx1 <= cx or bx0 >= cx + cw or by1 <= cy or by0 >= cy + ch)
        assert inside or outside, f"crop {crop} cuts through box {b}"
        n_in += inside
    assert n_in >= 1, "crop kept no text box visible"
    fit2 = min(W / cw, H / ch)
    print(f"2. crop {crop}: median line {med0 * fit:.0f}px -> "
          f"{med0 * fit2:.0f}px rendered (min {pr.MIN_TEXT_H_1080})")
    assert med0 * fit < pr.MIN_TEXT_H_1080, "trigger precondition"
    assert med0 * fit2 > med0 * fit * 1.3, "crop must meaningfully enlarge"
    # a comfortably-readable synthetic panel must NOT trigger
    big = [[100, 100, 400, 80], [100, 200, 400, 80]]
    assert pr._text_aware_crop(panel, big, W, H) is None, \
        "readable text must not be cropped"
    assert pr._text_aware_crop(panel, [], W, H) is None
    assert pr._text_aware_crop(panel, None, W, H) is None

    # ---- 3. before/after render of the actual shot ----
    audio = SAMPLES / "smoke_sil.mp3"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{DUR:.3f}", "-c:a", "libmp3lame", str(audio)])
    for tag, cr in [("before", None), ("after", crop)]:
        clip = SAMPLES / f"smoke_scene5_{tag}.mp4"
        pr.render_panel_scene(panel, audio, clip, W, H, DUR,
                              motion_idx=0, blur_bg=True, crop_rect=cr)
        png = SAMPLES / f"smoke_scene5_{tag}.png"
        mv.run(["ffmpeg", "-y", "-ss", "2.0", "-i", str(clip),
                "-frames:v", "1", str(png)])
        print(f"3. rendered {tag}: {png.name}")

    # ---- 4. classic fallback finds the card lines too ----
    import cv2
    img = cv2.imread(str(panel))
    cb = tb.detect_classic(img)
    print(f"4. cv2-classic fallback: {len(cb)} lines on p0024")
    assert len(cb) >= 3, "classic fallback must find the info-card lines"

    # ---- 5. layout_smart guard: inert without boxes, stricter with ----
    scene2 = data["scenes"][1]
    panels2 = [PROJECT / p for p in scene2["panels"]]
    wt = [{"word": w, "start": k, "end": k + 1}
          for k, w in enumerate(scene2["narration"].split())]
    p_none = ls.plan_layout(scene2, panels2, 12.0, wt, frame_w=W, frame_h=H)
    p_default = ls.plan_layout(scene2, panels2, 12.0, wt, frame_w=W,
                               frame_h=H, text_boxes=None)
    assert (p_none is None) == (p_default is None)
    if p_none:
        assert [c.rest for c in p_none.cells] == \
               [c.rest for c in p_default.cells], "text_boxes=None changed the plan"
    # measured tiny text must demand a taller cell than the binary 55% guard
    tiny = {"p0024.png": [[10, 10, 100, 12]]}   # 12px lines in a 1840px panel
    req = ls._panel_min_h(panel, None, tiny, H, ph)
    assert req == 1.0, f"12px lines must be uncellable, got {req}"
    ok = {"p0024.png": [[10, 10, 400, 220]]}    # huge display text: needs
    # only 40*1840/(220*1080) = 0.31 of frame height -> clamps to GUARD_H
    req2 = ls._panel_min_h(panel, None, ok, H, ph)
    assert req2 < ls.GUARD_H_TEXT, f"big text should relax below 0.55, got {req2}"
    print(f"5. layout guard: None inert; tiny-text req={req}, "
          f"big-text req={req2:.2f}")

    print("\nSMOKE PASS")


if __name__ == "__main__":
    main()
