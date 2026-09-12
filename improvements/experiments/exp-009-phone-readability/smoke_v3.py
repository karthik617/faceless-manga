#!/usr/bin/env python3
"""smoke_v3.py — exp-009 v3 iterate smoke test.

v2 passed scoped but FAILED global: with --text-aware, the size-aware
_panel_min_h made the smart-layout planner reject grid plans the binary
guard accepts (golden scenes 1/4/9/24 lost their composites -> watermark /
empty_screen vetoes). v3 (approved deciding change): the PLANNER always
receives text_boxes=None (binary guard) — layout plans are byte-identical
with --text-aware on/off; measured boxes drive only the crop decisions.

Checks:
  A. flag OFF byte-identity: same 12-filtergraph comparison as v2.
  B. source-level split: panel_render's planner calls pass text_boxes=None
     (both smart and smart2 branches); the crop path still uses tboxes.
  C. plan-set restoration: replaying the golden chapter's planning inputs
     (arm_t.json + baseline cached word timings/audio durations), the
     binary-guard plan set covers scenes {1,4,9,18,24} — identical to the
     flag-off path by construction (same None argument) — while v2's
     size-aware set was {18} only.
  D. crop decisions unchanged from v2: exact accepted/rejected/skip results
     on the v1 crop-log panels, plus the full-sidecar floor sweep.

Run: ./venv/bin/python3 improvements/experiments/exp-009-phone-readability/smoke_v3.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import panel_render as pr   # noqa: E402
import layout_smart as ls   # noqa: E402
import make_video as mv     # noqa: E402

PROJECT = ROOT / "output" / "the-world-after-the-fall-ch2"
PANELS = PROJECT / "panels"
RE_DIR = ROOT / "improvements/experiments/round2-eval"
SIDECAR = RE_DIR / "arm_t.textboxes.json"
W, H = 1920, 1080

# expected crop decisions on the v1 crop-log panels. Updated for the v4
# trigger gate: panels whose median line renders >= TEXT_TRIGGER_H_1080
# under default framing no longer trigger at all ("no-trigger") — that
# includes former v2/v3 crops p0003/p0060/p0126 (34-38px medians) and
# former explicit rejects whose text was fine anyway.
EXPECTED = {
    "p0024.png": ("crop", (220, 1316, 423, 379), 1.08),   # scene-5 win, 15px
    "p0043.png": ("crop", (235, 239, 417, 417), 1.08),    # 20px median
    "p0003.png": ("no-trigger", None, 1.08),   # 34px median (v4 gate)
    "p0126.png": ("no-trigger", None, 1.08),   # 36px — the round4 flag
    "p0060.png": ("no-trigger", None, 1.16),   # 38px
    "p0025.png": ("no-trigger", None, 1.08),   # 31px
    "p0122.png": ("reject", "containment-under-motion", 1.08),  # 29px:
                                               # triggers, then contained-out
    "p0017.png": ("wm-skip", None, 1.16),
    "p0040.png": ("wm-skip", None, 1.16),
    "p0031.png": ("wm-skip", None, 1.16),
    "p0117.png": ("wm-skip", None, 1.08),
    "p0032.png": ("reject", "text-cover", 1.16),
    "p0068.png": ("reject", "min-size", 1.08),
}


def main():
    boxes = {k: v["boxes"]
             for k, v in json.loads(SIDECAR.read_text())["panels"].items()}

    # ---- A. flag-off byte identity ----
    ref = json.loads((HERE / "samples/v2/flagoff_vf_v1.json").read_text())
    captured, real_run = [], mv.run

    def fake_run(cmd, *a, **k):
        for i, t in enumerate(cmd):
            if t == "-filter_complex":
                captured.append(cmd[i + 1])
        class R:
            stderr = stdout = ""
        return R()

    mv.run = fake_run
    try:
        for combo in ref:
            captured.clear()
            pr.render_panel_scene(PANELS / combo["panel"], "/dev/null",
                                  "/tmp/x.mp4", W, H, 4.0, motion_idx=1,
                                  blur_bg=True, crop_rect=None,
                                  **{k: True for k in combo["kw"]})
            assert captured[0] == combo["vf"], combo
    finally:
        mv.run = real_run
    print(f"A. flag-off byte-identity: {len(ref)} filtergraphs identical")

    # ---- B. source split: planner gets None, crop path keeps tboxes ----
    src = (ROOT / "pipeline/panel_render.py").read_text()
    for call in re.findall(r"plan_layout(?:_v2)?\((?:[^()]|\([^()]*\))*\)",
                           src):
        assert "text_boxes=None" in call, \
            f"planner call must pass text_boxes=None (exp-009 v3): {call}"
    assert "tboxes.get(name)" in src, "crop path must still use tboxes"
    print("B. source split: planner calls pass text_boxes=None; "
          "crop path uses tboxes")

    # ---- C. plan-set restoration on the golden chapter ----
    data = json.loads((RE_DIR / "arm_t.json").read_text())
    ocr = {r["panel"]: r
           for r in json.loads((RE_DIR / "arm_t.ocr.json").read_text())}
    tbx = boxes

    def probe_dur(p):
        return float(subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)],
            capture_output=True, text=True).stdout.strip())

    def plan_set(tb_arg):
        out = {}
        for i, sc in enumerate(data["scenes"]):
            plist = sc.get("panels") or []
            resolved = [pr._resolve_panel(PROJECT, p) for p in plist]
            resolved = [p for p in resolved if Path(p).exists()]
            fixed = []
            for p in resolved:
                if pr._panel_ok(p):
                    fixed.append(p)
                else:
                    sub = pr._nearest_usable_panel(PROJECT, p)
                    fixed.append(sub if sub else p)
            resolved = fixed or resolved
            if len(resolved) < 3:
                continue
            emph, foc = bool(sc.get("emphasis")), bool(sc.get("focus"))
            pace = sc.get("pace")
            if pace not in pr.PACE_CUT_SEC:
                pace = "hype" if emph else ("quiet" if foc else "normal")
            wtp = RE_DIR / "_work_base" / f"w{i:03}.json"
            wt = json.loads(wtp.read_text()) if wtp.exists() else None
            a = RE_DIR / "_work_base" / f"a{i:03}.mp3"
            d = (probe_dur(a) + 0.1) if a.exists() else 10.0
            plan = ls.plan_layout(sc, resolved, d, wt, frame_w=W, frame_h=H,
                                  pace=pace, ocr=ocr, text_boxes=tb_arg)
            if plan:
                out[i] = (plan.template,
                          tuple(Path(resolved[j]).name for j in plan.picked))
        return out

    p_binary = plan_set(None)      # what the v3 planner path (and flag-off) sees
    p_sizeaware = plan_set(tbx)    # what v2 fed the planner
    assert set(p_binary) == {1, 4, 9, 18, 24}, sorted(p_binary)
    assert set(p_sizeaware) == {18}, sorted(p_sizeaware)
    print(f"C. plan set restored: binary guard plans scenes "
          f"{sorted(p_binary)} (v2 size-aware planned only "
          f"{sorted(p_sizeaware)}); ON == OFF by construction "
          f"(planner arg is literally None)")

    # ---- D. crop decisions unchanged from v2 ----
    wm = pr._load_watermark_panels(PROJECT)
    for name, (kind, want, zmax) in EXPECTED.items():
        if kind == "wm-skip":
            assert name in wm and not (
                PROJECT / "panels_clean" / name).exists(), name
            continue
        msgs = []
        r = pr._text_aware_crop(PANELS / name, boxes[name], W, H,
                                zmax=zmax, reject_log=msgs.append)
        if kind == "crop":
            assert r == want, (name, r, want)
        elif kind == "no-trigger":
            # exp-009 v4: readable under default framing -> silent no-action
            assert r is None and not msgs, (name, r, msgs)
        else:
            assert r is None and msgs and want in msgs[0], (name, r, msgs)
    # full floor sweep (counts updated for the v4 trigger gate: only the
    # genuinely-unreadable p0024 + p0043 crop, at both zmax values)
    accepted = rejected = skipped = 0
    for name, bx in sorted(boxes.items()):
        if not bx or not (PANELS / name).exists():
            continue
        if name in wm and not (PROJECT / "panels_clean" / name).exists():
            skipped += 1
            continue
        for zmax in (1.08, 1.16):
            r = pr._text_aware_crop(PANELS / name, bx, W, H, zmax=zmax)
            if r is None:
                rejected += 1
            else:
                accepted += 1
                assert min(r[2], r[3]) >= pr.MIN_CROP_PX
                assert not pr._motion_violations(r, bx, zmax)
    assert (accepted, rejected, skipped) == (4, 136, 8), \
        (accepted, rejected, skipped)
    print(f"D. crop decisions: {len(EXPECTED)} spot-checks match; sweep "
          f"{accepted} accepted / {rejected} no-crop / {skipped} wm-skipped "
          f"(v4 trigger gate: accepted set = p0024 + p0043)")

    print("\nSMOKE V3 PASS")


if __name__ == "__main__":
    main()
