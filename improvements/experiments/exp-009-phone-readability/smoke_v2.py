#!/usr/bin/env python3
"""smoke_v2.py — exp-009 v2 iterate smoke test.

v1 FAILED (report.md): +19 score, cropped_content/watermark/irrelevant_panel
vetoes. v2 adds three guards to _text_aware_crop (approved fix direction):
  (1) containment under MOTION — every box/bubble intersecting the crop must
      fit the max-zoom Ken Burns window (the guaranteed-visible intersection
      of the motion path), incl. an estimated FULL bubble outline per text
      cluster; else the crop is rejected.
  (2) minimum crop size — >=200px per dim AND area >= 25% of shorter-side^2
      AND text covers >=1% of the crop.
  (3) watermark-aware — panels with a recorded watermark instance
      (watermark_log.json) are only croppable via a cleaned panels_clean copy.

Checks:
  A. flag OFF byte-identity: render_panel_scene filtergraphs with
     crop_rect=None match the pre-v2 capture (flagoff_vf_v1.json) for 12
     panel/kwarg combos, and _tcrop-equivalent path is unreachable
     (tboxes None).
  B. the scene-5 win still fires: p0024 crop accepted, large, contains the
     info card.
  C. v1 failure panels now REJECT with the right reason:
     scene 0 (p0017/p0040 emphasis): containment-under-motion / watermark;
     scene 7 (p0025/p0031/p0032): containment / text-cover / watermark;
     scene 20 (p0117/p0122): min-size / containment.
  D. floor sweep: over ALL golden-chapter panels in the round2-eval sidecar,
     no accepted crop is smaller than the floors, and none violates motion
     containment of its own line boxes.
  E. watermark guard: _load_watermark_panels finds the recorded instances;
     p0017 (watermarked, no panels_clean) must be skipped by the render-side
     rule.

Run: ./venv/bin/python3 improvements/experiments/exp-009-phone-readability/smoke_v2.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import panel_render as pr  # noqa: E402
import make_video as mv    # noqa: E402

PROJECT = ROOT / "output" / "the-world-after-the-fall-ch2"
PANELS = PROJECT / "panels"
SIDECAR = ROOT / "improvements/experiments/round2-eval/arm_t.textboxes.json"
W, H = 1920, 1080

# v1 render log: which panels got crops on the v1 FAILURE scenes, and the
# zoom that cut of the scene actually ran (emphasis first-cut -> 1.16).
V1_FAILURE_PANELS = {
    # scene 0 (t=26.4 half-cut bubble): emphasis scene
    "p0017.png": 1.16, "p0040.png": 1.16,
    # scene 7 (t=190.1 partial SFX + black space)
    "p0025.png": 1.08, "p0031.png": 1.16, "p0032.png": 1.16,
    # scene 20 (t=628.5 bubble+face cut at top)
    "p0117.png": 1.08, "p0122.png": 1.08,
    # degenerate tiny crop (scene 14 in the log)
    "p0068.png": 1.08,
}


def main():
    boxes = {k: v["boxes"]
             for k, v in json.loads(SIDECAR.read_text())["panels"].items()}

    # ---- A. flag-off byte identity (filtergraph compare) ----
    ref = json.loads((HERE / "samples/v2/flagoff_vf_v1.json").read_text())
    captured = []
    real_run = mv.run

    def fake_run(cmd, *a, **k):
        for i, t in enumerate(cmd):
            if t == "-filter_complex":
                captured.append(cmd[i + 1])
        class R:
            stderr = ""
            stdout = ""
        return R()

    mv.run = fake_run
    try:
        for j, combo in enumerate(ref):
            captured.clear()
            kw = {k: True for k in combo["kw"]}
            pr.render_panel_scene(PANELS / combo["panel"], "/dev/null",
                                  "/tmp/x.mp4", W, H, 4.0, motion_idx=1,
                                  blur_bg=True, crop_rect=None, **kw)
            assert captured[0] == combo["vf"], \
                f"flag-off filtergraph changed for {combo}"
    finally:
        mv.run = real_run
    print(f"A. flag-off byte-identity: {len(ref)} filtergraphs identical")

    # ---- B. scene-5 win preserved ----
    r24 = pr._text_aware_crop(PANELS / "p0024.png", boxes["p0024.png"],
                              W, H, zmax=1.08)
    assert r24 is not None, "scene-5 info-card crop must still fire"
    assert min(r24[2], r24[3]) >= pr.MIN_CROP_PX, r24
    assert r24 == (220, 1316, 423, 379), \
        f"scene-5 crop drifted from the v1 win: {r24}"
    print(f"B. scene-5 win preserved: p0024 -> {r24}")

    # ---- C. v1 failure panels now reject ----
    wm = pr._load_watermark_panels(PROJECT)
    for name, zmax in V1_FAILURE_PANELS.items():
        # render-side watermark rule fires first for recorded-watermark panels
        if name in wm and not (PROJECT / "panels_clean" / name).exists():
            print(f"C. {name}: SKIP (watermark recorded, no cleaned panel)")
            continue
        msgs = []
        r = pr._text_aware_crop(PANELS / name, boxes[name], W, H,
                                zmax=zmax, reject_log=msgs.append)
        assert r is None, f"v1 failure panel {name} must reject, got {r}"
        # exp-009 v4: some v1 failures no longer even TRIGGER (their text
        # renders >= TEXT_TRIGGER_H under default framing) — silent
        # no-action is stricter than a logged reject, so it passes too.
        if not msgs:
            print(f"C. {name}: NO-TRIGGER (readable under default framing, "
                  f"v4 gate)")
            continue
        reason = msgs[0].split(" — ")[0]
        assert any(t in reason for t in
                   ("containment-under-motion", "min-size", "min-area",
                    "text-cover")), reason
        print(f"C. {name}: REJECT ({reason})")

    # ---- D. floor sweep over every panel in the sidecar ----
    accepted, rejected, skipped_wm = 0, 0, 0
    for name, bx in sorted(boxes.items()):
        if not bx:
            continue
        p = PANELS / name
        if not p.exists():
            continue
        if name in wm and not (PROJECT / "panels_clean" / name).exists():
            skipped_wm += 1
            continue
        for zmax in (1.08, 1.16):
            r = pr._text_aware_crop(p, bx, W, H, zmax=zmax)
            if r is None:
                rejected += 1
                continue
            accepted += 1
            cx, cy, cw, ch = r
            from PIL import Image
            with Image.open(p) as im:
                pw, ph = im.size
            assert min(cw, ch) >= pr.MIN_CROP_PX, (name, r)
            assert cw * ch >= pr.MIN_CROP_AREA_FRAC * min(pw, ph) ** 2, \
                (name, r)
            # no line box may exit the max-zoom window
            assert not pr._motion_violations(r, bx, zmax), \
                (name, r, zmax, "line box exits the rendered window")
    # v4 trigger gate shrinks the accepted set to the genuinely-unreadable
    # panels (p0024 + p0043 at both zmax values = 4 evals)
    assert accepted == 4, accepted
    print(f"D. floor sweep: {accepted} accepted crop-evals all >= floors + "
          f"motion-contained; {rejected} rejected; {skipped_wm} watermark-"
          f"skipped")

    # ---- E. watermark guard data ----
    assert "p0017.png" in wm and "p0117.png" in wm, sorted(wm)
    assert not (PROJECT / "panels_clean").exists() or \
        not (PROJECT / "panels_clean" / "p0017.png").exists(), \
        "test premise: p0017 has no cleaned copy in the golden project"
    print(f"E. watermark guard: {len(wm)} recorded instances "
          f"({', '.join(sorted(wm)[:4])}...)")

    print("\nSMOKE V2 PASS")


if __name__ == "__main__":
    main()
