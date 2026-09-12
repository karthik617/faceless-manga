#!/usr/bin/env python3
"""check_containment.py — exp-014 deterministic containment gate (gap-014).

Binding acceptance criterion: for every panel with h/w > 2.5, the rendered
Ken Burns window at EVERY narration-beat timestamp contains 100% of the
panel's OCR text boxes. "Narration beat" = every word start/end from the
scene's edge-tts word timings (w###.json in the render workdir), mapped into
the cut that is on screen at that moment; cut boundaries and mid/end-points
are added so cuts with no words (SFX-only panels) are still checked.

The window math is the SAME code the renderer used (kb_smart.plan_frame
output interpreted by kb_smart.window_contains_boxes) — the gate checks the
plans the render actually baked in (<workdir>/kb_plans.json, written by
panel_render --kenburns smart), not a re-derivation. For a baseline
(flag-off) run there is no kb_plans.json; --baseline synthesizes
center-anchored plans from the script + panel dims, which is exactly what
the default render does, so the gate can quantify how many violations the
center anchor causes today.

Exit 0 = every tall-panel beat window contains every OCR text box.
Exit 1 = at least one violation (each is printed).

Usage:
  ./venv/bin/python3 check_containment.py <script.json> <workdir>
      [--res 1080p] [--baseline] [--ar 2.5]
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))

import kb_smart  # noqa: E402  (shared window math — single source of truth)

RES = {"1080p": (1920, 1080), "1440p": (2560, 1440)}


def _resolve_panel(project_dir, rel):
    rel = rel.replace("\\", "/")
    name = Path(rel).name
    for cand in (project_dir / "panels_clean" / name, project_dir / rel,
                 project_dir / "panels" / name):
        if cand.exists():
            return cand
    return project_dir / rel


def _beats(word_times, dur):
    """Timestamps (scene-relative s) to check: word starts+ends, plus a
    coarse grid so silent stretches / wordless cuts are still covered."""
    ts = {0.0, dur * 0.5, max(dur - 1e-3, 0.0)}
    for w in word_times or []:
        ts.add(float(w["start"]))
        ts.add(float(w["end"]))
    step = 0.5
    t = 0.0
    while t < dur:
        ts.add(t)
        t += step
    return sorted(t for t in ts if 0 <= t <= dur + 1e-6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("workdir")
    ap.add_argument("--res", default="1080p", choices=list(RES))
    ap.add_argument("--ar", type=float, default=2.5,
                    help="h/w threshold defining a 'tall' panel (gate scope)")
    ap.add_argument("--baseline", action="store_true",
                    help="no kb_plans.json: synthesize the default "
                         "center-anchored plans (what a flag-off render does)"
                         " to measure today's violations")
    args = ap.parse_args()

    script = Path(args.script)
    workdir = Path(args.workdir)
    project_dir = script.parent
    w, h = RES[args.res]
    data = json.loads(script.read_text())
    scenes = data["scenes"]

    # OCR text boxes: the same sidecar the renderer used
    tb_path = script.with_suffix(".textboxes.json")
    if not tb_path.exists():
        sys.exit(f"missing {tb_path} — run the render (or text_boxes.py) "
                 f"first; the gate refuses to invent boxes")
    tboxes = {k: v["boxes"] for k, v in
              json.loads(tb_path.read_text())["panels"].items()}

    from PIL import Image

    if args.baseline:
        # reconstruct the default render's cut schedule (mirror of
        # panel_render: PACE_CUT_SEC, forward-only, ping-pong tail) with
        # center plans. Durations come from the rendered per-scene clips.
        import subprocess
        PACE = {"hype": 1.6, "normal": 3.5, "quiet": 8.0}
        MAXC = 8
        plans = []
        for i, sc in enumerate(scenes):
            clip = workdir / f"c{i:03}.mp4"
            if not clip.exists():
                sys.exit(f"baseline: missing rendered clip {clip}")
            dur = float(subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(clip)],
                capture_output=True, text=True).stdout.strip())
            panels = [_resolve_panel(project_dir, p)
                      for p in (sc.get("panels") or
                                ([sc["panel"]] if sc.get("panel") else []))]
            panels = [p for p in panels if p.exists()]
            if not panels:
                continue
            pace = sc.get("pace")
            if pace not in PACE:
                pace = ("hype" if sc.get("emphasis")
                        else ("quiet" if sc.get("focus") else "normal"))
            n = max(len(panels), int(round(dur / PACE[pace])))
            n = max(1, min(n, max(MAXC, len(panels))))
            cuts = list(panels)
            tail = panels[-2:] if len(panels) >= 2 else panels
            k = 0
            while len(cuts) < n:
                cuts.append(tail[k % len(tail)])
                k += 1
            cuts = cuts[:n]
            sub = dur / n
            emph = bool(sc.get("emphasis"))
            for k, p in enumerate(cuts):
                pw, ph = Image.open(p).size
                plans.append({
                    "scene": i, "cut": k, "panel": p.name,
                    "panel_path": str(p), "cut_off": k * sub, "dur": sub,
                    "zmax": 1.16 if (emph and k == 0) else 1.08,
                    "frame": [w, h],
                    "plan": {"mode": "center", "zmax":
                             1.16 if (emph and k == 0) else 1.08,
                             "ss_w": w * 2, "ss_h": h * 2,
                             "pw": pw, "ph": ph}})
    else:
        plans_path = workdir / "kb_plans.json"
        if not plans_path.exists():
            sys.exit(f"missing {plans_path} — render with --kenburns smart "
                     f"(or use --baseline for a flag-off run)")
        plans = json.loads(plans_path.read_text())
        # a "center" fallback (plan None / skip-crop) still renders the
        # default window; gate it with the same center math
        for r in plans:
            if not r["plan"] or r["plan"].get("mode") == "skip-crop":
                pw, ph = Image.open(r["panel_path"]).size
                r["plan"] = {"mode": "center", "zmax": r["zmax"],
                             "ss_w": r["frame"][0] * 2,
                             "ss_h": r["frame"][1] * 2, "pw": pw, "ph": ph}

    # word timings per scene (workdir w###.json, written by the render)
    wt = {}
    for i in range(len(scenes)):
        p = workdir / f"w{i:03}.json"
        if p.exists():
            try:
                wt[i] = json.loads(p.read_text())
            except Exception:
                wt[i] = None

    checked = tall_cuts = violations = 0
    fails = []
    for rec in plans:
        plan = rec["plan"]
        pw, ph = plan["pw"], plan["ph"]
        if ph / pw <= args.ar:
            continue                       # gate scope: tall panels only
        tall_cuts += 1
        boxes = tboxes.get(rec["panel"]) or []
        if not boxes:
            continue                       # no OCR text: vacuously contained
        # clamp boxes like the planner does (DBNet can poke 1-2px past edges)
        boxes = [[max(b[0], 0), max(b[1], 0),
                  min(b[0] + b[2], pw) - max(b[0], 0),
                  min(b[1] + b[3], ph) - max(b[1], 0)] for b in boxes]
        boxes = [b for b in boxes if b[2] > 0 and b[3] > 0]
        dur = rec["dur"]
        for t_scene in _beats(wt.get(rec["scene"]), dur + rec["cut_off"]):
            t = t_scene - rec["cut_off"]   # beat time within THIS cut
            if t < 0 or t > dur + 1e-6:
                continue
            checked += 1
            if not kb_smart.window_contains_boxes(plan, boxes, t, dur):
                violations += 1
                fails.append((rec["scene"], rec["cut"], rec["panel"],
                              plan["mode"], round(t_scene, 2)))

    label = "baseline(center)" if args.baseline else "kb-smart"
    print(f"[{label}] tall cuts (h/w>{args.ar}): {tall_cuts}, "
          f"beat-window checks: {checked}, violations: {violations}")
    if fails:
        seen = set()
        for s, c, p, m, t in fails:
            key = (s, c)
            if key in seen:
                continue
            seen.add(key)
            n = sum(1 for f in fails if (f[0], f[1]) == key)
            print(f"  VIOLATION scene {s + 1} cut {c} panel {p} "
                  f"mode={m}: {n} beat(s), first at t={t}s")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
