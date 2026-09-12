#!/usr/bin/env python3
"""
review_triage.py — STAGE 5.6: automated adjudication of surviving HIGH faults.

After review_video.py's --apply-fixes loop hits its ceiling ("faults remain
but no auto-fix applies"), a human used to triage each remaining HIGH fault
by hand: open the faulted panel image + a frame grab from the review cut at
the fault timestamp, compare against the narration, and decide
  DROP    — the panel is a bubble-only / SFX-text / title-card / near-blank
            cutout that doesn't carry the narration; remove it from the scene
            (with narration touch-up) and re-render, or
  DISMISS — the reviewer misread the frame (single-pass vision variance);
            the panel genuinely depicts the narration; keep it.

This module automates exactly that pass. For every HIGH fault carrying a
scene index it builds an evidence pack (frame grab at t + every panel image
of the scene + narration + fault text) and asks the vision LLM to adjudicate
with a deliberately conservative rubric (DROP only for objectively contentless
panels; when in doubt DISMISS — a wrong DROP loses story art, a wrong DISMISS
just leaves one weak shot). Decisions are applied to the script JSON via the
same drop + narration-touch-up machinery as review_video.apply_fixes, and
dismissed faults are re-labelled in review.json (severity "dismissed") so the
encode gate no longer counts them. Every decision is appended to review.md.

Usage:
    python3 review_triage.py output/<slug>/<slug>.json \
        [--video <review-cut>] [--workdir output/<slug>/_work] [--dry-run]

Exit code: 0 = all HIGH faults resolved (drops applied and/or dismissed),
           2 = some HIGH faults could not be adjudicated (evidence missing
               or LLM undecided) — these stay HIGH and the gate still stops.
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway
import review_common
from review_video import _fix_narration, scene_spans as _scene_spans

# fault types a triage decision makes sense for (frame/panel content calls);
# blank_frame carries no scene but is mapped onto one via the span table
TRIAGEABLE = {"irrelevant_panel", "empty_screen", "weak_hook", "watermark",
              "missing_payoff", "blank_frame"}

TRIAGE_INSTRUCTION = """\
You are adjudicating ONE automated review fault on a manga-recap video, the
way a careful human editor would. You are given:
  - image 1: a frame grabbed from the video at the fault timestamp
  - images 2..N: the source panel images used by this scene, in order
  - the scene's narration and the fault description below.

The automated reviewer flagged this scene HIGH with:
  type: {ftype}
  detail: {detail}

Scene narration:
\"\"\"{narration}\"\"\"

Panel filenames in order (image 2 onward): {panel_names}

Decide ONE of:
  DROP    — one or more panels are objectively content-free for this
            narration: a speech-bubble-only cutout on empty background, an
            SFX-text-only fragment, a chapter title card / cover / credits
            page, or a near-black/near-white blank. Dropping them loses no
            story art.
  DISMISS — the flagged frame/panels DO depict the narration (dark art,
            abstract action, system-UI windows the narration explicitly
            describes, mid-pan slices of a tall panel, close-ups). The
            reviewer misread it.

Be conservative: a wrong DROP deletes story art forever; a wrong DISMISS just
leaves one weak shot. System-notification / status-window panels are VALID
story content whenever the narration mentions notifications, titles, items,
or system messages. Do not drop a scene's only panel unless it is truly
blank or a title card.

Reply with ONLY a JSON object:
{{"decision": "DROP"|"DISMISS",
  "panels": ["p0011.png", ...],   // only for DROP: which of the listed panel
                                  // filenames to remove (subset, never all
                                  // unless every one is contentless)
  "reason": "<one short sentence>"}}
"""


def scene_spans(workdir, n_scenes):
    spans, _durs = _scene_spans(workdir, n_scenes)
    return spans


def _scene_for_t(spans, t):
    for i, (a, b) in enumerate(spans):
        if a <= t < b:
            return i
    return None


def _load_faults(path):
    d = json.loads(Path(path).read_text())
    return d if isinstance(d, list) else d.get("faults", [])


def triage(script_path, video, workdir, dry_run=False):
    script_path = Path(script_path)
    proj = script_path.parent
    data = json.loads(script_path.read_text())
    scenes = data["scenes"]

    rj = proj / "review.json"
    if not rj.exists():
        print("triage: no review.json — nothing to do")
        return 0
    faults = _load_faults(rj)
    high = [f for f in faults if f.get("severity") == "high"]
    if not high:
        print("triage: no HIGH faults — nothing to do")
        return 0

    spans = scene_spans(Path(workdir), len(scenes))
    tmp = Path(tempfile.mkdtemp(prefix="triage_"))

    ocr = proj / (script_path.stem + ".ocr.json")
    reads_by_panel = {}
    if ocr.exists():
        for r in json.loads(ocr.read_text()):
            reads_by_panel[r["panel"]] = r

    # group HIGH faults by scene (resolve scene from t for scene-less faults)
    by_scene, unresolved = {}, []
    for f in high:
        si = f.get("scene")
        if si is None and f.get("type") in TRIAGEABLE:
            si = _scene_for_t(spans, float(f.get("t", -1)))
        if si is None or si >= len(scenes) or f.get("type") not in TRIAGEABLE:
            unresolved.append(f)
            continue
        by_scene.setdefault(si, []).append(f)

    log, changed_scenes, dismissed = [], set(), []
    for si, fs in sorted(by_scene.items()):
        scene = scenes[si]
        panels = [p for p in (scene.get("panels") or [])]
        pnames = [Path(p).name for p in panels]
        t = float(fs[0].get("t", spans[si][0] if si < len(spans) else 0))

        # evidence pack: frame at fault time + all scene panels
        frame = tmp / f"tri_s{si:03d}.jpg"
        images = []
        if video and review_common.grab(video, t, frame, h=540):
            images.append(frame)
        for p in panels:
            pp = proj / p
            if pp.exists():
                images.append(pp)
        if len(images) < 2:      # need at least a frame or panels to judge
            unresolved.extend(fs)
            continue

        detail = " | ".join(f.get("detail", "") for f in fs)
        ftype = ",".join(sorted({f.get("type", "?") for f in fs}))
        instr = TRIAGE_INSTRUCTION.format(
            ftype=ftype, detail=detail,
            narration=scene.get("narration", "")[:900],
            panel_names=", ".join(pnames))
        try:
            raw = gateway.llm_vision(images, instr)
            obj = review_common.parse_json(raw, "object")
        except Exception as ex:
            print(f"  triage scene {si}: LLM failed ({type(ex).__name__}) — "
                  f"left HIGH")
            unresolved.extend(fs)
            continue

        decision = str(obj.get("decision", "")).upper()
        reason = str(obj.get("reason", ""))[:200]
        if decision == "DROP":
            want = {n for n in (obj.get("panels") or []) if n in pnames}
            keep = [p for p in panels if Path(p).name not in want]
            if not want:
                # DROP with no valid panel named — treat as undecided
                print(f"  triage scene {si}: DROP without valid panels — "
                      f"left HIGH")
                unresolved.extend(fs)
                continue
            if not keep:
                # never empty a scene from triage; require at least 1 survivor
                print(f"  triage scene {si}: DROP would empty the scene — "
                      f"left HIGH for manual pass")
                unresolved.extend(fs)
                continue
            print(f"  triage scene {si}: DROP {sorted(want)} — {reason}")
            log.append(f"- scene {si}: DROP {', '.join(sorted(want))} — "
                       f"{reason}")
            if not dry_run:
                dropped = [p for p in panels if Path(p).name in want]
                scene["panels"] = keep
                _fix_narration(scene, dropped, reads_by_panel)
                changed_scenes.add(si)
            for f in fs:
                f["severity"] = "resolved_drop"
                f["triage"] = reason
        elif decision == "DISMISS":
            print(f"  triage scene {si}: DISMISS — {reason}")
            log.append(f"- scene {si}: DISMISS ({ftype}) — {reason}")
            for f in fs:
                f["severity"] = "dismissed"
                f["triage"] = reason
            dismissed.extend(fs)
        else:
            print(f"  triage scene {si}: undecided ({decision!r}) — left HIGH")
            unresolved.extend(fs)

    if dry_run:
        print(f"triage (dry run): {len(log)} decision(s), "
              f"{len(unresolved)} unresolved")
        return 0 if not unresolved else 2

    # persist: script JSON (drops), review.json (re-labelled severities)
    if changed_scenes:
        script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    rj.write_text(json.dumps(faults, indent=2))

    # append the audit trail to review.md
    rmd = proj / "review.md"
    if rmd.exists() and (log or unresolved):
        with open(rmd, "a") as fh:
            fh.write("\n## Automated triage (stage 5.6)\n\n")
            fh.write("Each surviving HIGH fault was adjudicated by a vision "
                     "LLM shown the fault-time video frame, the scene's "
                     "panel images, and the narration (conservative rubric: "
                     "DROP only objectively contentless panels; when in "
                     "doubt DISMISS).\n\n")
            for line in log:
                fh.write(line + "\n")
            if unresolved:
                fh.write(f"\n{len(unresolved)} fault(s) could not be "
                         f"adjudicated and remain HIGH (manual pass "
                         f"needed).\n")
            if changed_scenes:
                fh.write(f"\nScenes re-rendered after drops: "
                         f"{sorted(s + 1 for s in changed_scenes)} "
                         f"(1-based).\n")

    print(f"triage: {len(log)} decision(s) "
          f"({len(changed_scenes)} scene(s) changed, "
          f"{len(dismissed)} fault(s) dismissed, "
          f"{len(unresolved)} unresolved)")
    # tell the caller which scenes need re-rendering (1-based, like
    # panel_render --redo-scene)
    if changed_scenes:
        print("TRIAGE_REDO_SCENES=" +
              ",".join(str(s + 1) for s in sorted(changed_scenes)))
    return 0 if not unresolved else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("script")
    ap.add_argument("--video", default=None,
                    help="review cut to grab evidence frames from "
                         "(default: _work/review_src.txt)")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    spath = Path(args.script)
    proj = spath.parent
    workdir = Path(args.workdir) if args.workdir else proj / "_work"
    video = args.video
    if not video:
        marker = workdir / "review_src.txt"
        if marker.exists():
            v = Path(marker.read_text().strip())
            if v.exists():
                video = v
    sys.exit(triage(spath, video, workdir, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
