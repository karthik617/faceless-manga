#!/usr/bin/env python3
"""smoke.py — exp-002 smoke test: plan_layout + render_smart_scene direct call.

Uses scene 2 of the golden chapter (3 panels, pace=quiet) with a synthetic
12s duration and evenly-spaced fake word timings, so the test runs without
TTS. Verifies the output with ffprobe (duration/resolution) and extracts
early/mid/late frames so a human can see the reveal progression.

Run: ./venv/bin/python3 improvements/experiments/exp-002-multipanel-layout/smoke.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipeline"))
import layout_smart as ls
import make_video as mv

SAMPLES = Path(__file__).resolve().parent / "samples"
SAMPLES.mkdir(exist_ok=True)
PROJECT = ROOT / "output" / "the-world-after-the-fall-ch2"
DUR = 12.0
W, H = 1920, 1080


def fake_word_times(narration, duration):
    """Evenly-spaced word timings across the duration (stands in for the
    edge-tts w{i:03}.json data the real pipeline provides)."""
    words = narration.split()
    step = duration / max(len(words), 1)
    return [{"word": w, "start": k * step, "end": (k + 1) * step}
            for k, w in enumerate(words)]


def resolve(rel):
    """Mirror panel_render._resolve_panel: prefer panels_clean/."""
    name = Path(rel).name
    clean = PROJECT / "panels_clean" / name
    return clean if clean.exists() else PROJECT / rel


def load_ocr():
    """Load the golden chapter's OCR sidecar keyed by panel name (the same
    shape panel_render passes to plan_layout)."""
    ocr_path = PROJECT / f"{PROJECT.name}.ocr.json"
    return {r["panel"]: r for r in json.loads(ocr_path.read_text())}


def main():
    data = json.loads((PROJECT / f"{PROJECT.name}.json").read_text())
    ocr = load_ocr()
    scene = data["scenes"][1]   # scene 2: 3 tall panels, pace=quiet
    panels = [resolve(p) for p in scene["panels"]]
    for p in panels:
        assert p.exists(), f"missing panel {p}"
    wt = fake_word_times(scene["narration"], DUR)

    plan = ls.plan_layout(scene, panels, DUR, wt, frame_w=W, frame_h=H,
                          ocr=ocr)
    assert plan is not None, "plan_layout returned None for a 3-panel scene"
    print(f"plan: template={plan.template} beats={[f'{b:.2f}' for b in plan.beats]}")
    assert plan.template == "grid", f"expected grid, got {plan.template}"
    assert len(plan.beats) == 3 and plan.beats[0] == 0.0
    assert plan.picked == [0, 1, 2]

    # negative guards: hype pace and too-short scenes must return None
    assert ls.plan_layout(dict(scene, pace="hype"), panels, DUR, wt,
                          frame_w=W, frame_h=H, ocr=ocr) is None, \
        "hype must fall back"
    assert ls.plan_layout(scene, panels, 2.0, wt,
                          frame_w=W, frame_h=H, ocr=ocr) is None, \
        "short must fall back"
    assert ls.plan_layout(scene, panels[:2], DUR, wt,
                          frame_w=W, frame_h=H, ocr=ocr) is None, \
        "2 panels must fall back"
    # no OCR sidecar -> conservative (all text-bearing) but tall webtoon
    # panels still clear the 55% guard, so the plan must not vanish
    assert ls.plan_layout(scene, panels, DUR, wt,
                          frame_w=W, frame_h=H) is not None, \
        "missing OCR must stay conservative, not fatal"

    # v2 panel cap: a 4+-panel non-hype scene must either select 3 panels in
    # reading order (first/middle/last) or return None (seq fallback)
    cap_scene = next(s for s in data["scenes"]
                     if len(s["panels"]) >= 4
                     and s.get("pace") in ("normal", "quiet"))
    cap_panels = [resolve(p) for p in cap_scene["panels"]]
    cap_wt = fake_word_times(cap_scene["narration"], DUR)
    cap_plan = ls.plan_layout(cap_scene, cap_panels, DUR, cap_wt,
                              frame_w=W, frame_h=H, ocr=ocr)
    if cap_plan is None:
        print(f"4+-panel scene ({len(cap_panels)} panels): plan=None "
              f"-> seq fallback (guard rejected)")
    else:
        assert len(cap_plan.cells) == 3, "cap must select exactly 3 panels"
        nmax = len(cap_panels)
        assert cap_plan.picked == [0, nmax // 2, nmax - 1], \
            f"selection must be first/middle/last, got {cap_plan.picked}"
        chosen = [Path(cap_panels[j]).name for j in cap_plan.picked]
        print(f"4+-panel scene ({nmax} panels): selected {chosen}")

    # synthetic silent narration track (the real path passes the scene mp3)
    audio = SAMPLES / "smoke_silence.mp3"
    mv.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{DUR:.3f}", "-c:a", "libmp3lame", str(audio)])

    out = SAMPLES / "smoke_grid_v2.mp4"
    ls.render_smart_scene(plan, out, audio, DUR, SAMPLES, name="smoke")

    # ---- ffprobe verification ----
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(out)], capture_output=True, text=True)
    info = json.loads(probe.stdout)
    got_w = info["streams"][0]["width"]
    got_h = info["streams"][0]["height"]
    got_d = float(info["format"]["duration"])
    print(f"ffprobe: {got_w}x{got_h}, {got_d:.3f}s")
    assert (got_w, got_h) == (W, H), f"bad resolution {got_w}x{got_h}"
    assert abs(got_d - DUR) <= 0.2, f"duration {got_d:.3f} off target {DUR}"

    # decode check: count frames (playability) — a broken graph truncates
    dec = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    n_frames = int(dec.stdout.strip())
    print(f"decoded packets: {n_frames} (expect ~{int(DUR * mv.FPS)})")
    assert n_frames >= int(DUR * mv.FPS) - 3, "clip truncated"

    # ---- static check (round-1 regression): freezedetect must fire ZERO
    # freeze_start events at a very tight noise floor — the v2 composite is
    # never static (continuous zoom + drift + breathe + border pulse) ----
    fz = subprocess.run(
        ["ffmpeg", "-i", str(out), "-vf", "freezedetect=n=0.001:d=10",
         "-f", "null", "-"], capture_output=True, text=True)
    freezes = [l for l in fz.stderr.splitlines() if "freeze_start" in l]
    print(f"freezedetect (n=0.001, d=10): {len(freezes)} freeze_start events")
    assert not freezes, f"static composite detected: {freezes}"

    # belt-and-braces: consecutive 2s-interval frames must actually differ
    # (same stride as review_video's static_scan)
    from PIL import Image, ImageChops
    prev = None
    for k, t in enumerate(x * 2.0 + 0.5 for x in range(int(DUR // 2))):
        png = SAMPLES / f"_static_{k}.png"
        mv.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(out),
                "-frames:v", "1", str(png)])
        im = Image.open(png).convert("L")
        if prev is not None:
            diff = ImageChops.difference(im, prev).getbbox()
            assert diff is not None, f"identical frames at t={t - 2:.1f}/{t:.1f}"
        prev = im
        png.unlink()
    print(f"2s-interval frames all differ (PIL ImageChops)")

    # ---- reveal-progression frames for human review ----
    for tag, t in [("early", plan.beats[0] + 0.6),
                   ("mid", plan.beats[1] + 0.6),
                   ("late", plan.beats[2] + 0.6)]:
        png = SAMPLES / f"smoke_grid_v2_{tag}.png"
        mv.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(out),
                "-frames:v", "1", str(png)])
        print(f"frame {tag} @ {t:.2f}s -> {png.name}")

    print("\nSMOKE PASS")


if __name__ == "__main__":
    main()
