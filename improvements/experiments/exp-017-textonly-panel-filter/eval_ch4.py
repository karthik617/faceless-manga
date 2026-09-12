#!/usr/bin/env python3
"""eval_ch4.py — deterministic replay of the exp-017 tier-1.25 gate on ch4.

Labeled set (gap-017 acceptance criteria):
  POSITIVES: the 12 panel ids across 11 manual drop actions in
    output/the-world-after-the-fall-ch4/review.md's resolution log.
    "Caught" = the flagged verifier deterministically drops the panel OR
    queues it as a textonly suspect for forced tier-3 vision.
  NEGATIVES: every panel kept in the manually-fixed final script
    (the-world-after-the-fall-ch4.json — re-reviewed 0 faults).
    "False drop" = the gate deterministically drops one of these; a suspect
    verdict on a negative is acceptable (tier-3 decides) but is reported.

Inputs are copied into a temp dir first — the golden ch4 outputs are never
mutated. The verifier runs with --textonly-filter --no-vision (fully
deterministic; the ≥9/11 target must be met WITHOUT vision per research.md).

Run:
    ./venv/bin/python3 improvements/experiments/exp-017-textonly-panel-filter/eval_ch4.py
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipeline"))

CH4 = ROOT / "output/the-world-after-the-fall-ch4"
SLUG = "the-world-after-the-fall-ch4"

# 11 drop actions / 12 panel ids from review.md's resolution log. p0075 is
# the alignment-dependent cover card (may be excluded from the >=9 target).
POSITIVES = ["p0011.png", "p0021.png", "p0022.png", "p0027.png", "p0040.png",
             "p0042.png", "p0060.png", "p0062.png", "p0073.png", "p0075.png",
             "p0095.png", "p0096.png"]


def main():
    final = json.loads((CH4 / f"{SLUG}.json").read_text())
    negatives = sorted({Path(p).name for sc in final["scenes"]
                        for p in sc.get("panels", [])})
    pos = set(POSITIVES)
    assert not pos & set(negatives), "labeled sets overlap?!"

    tmp = Path(tempfile.mkdtemp(prefix="exp017-eval-", dir="/tmp/opencode"))
    script = tmp / f"{SLUG}.json"
    shutil.copy(CH4 / f"{SLUG}.json.pre_verify", script)
    shutil.copy(CH4 / f"{SLUG}.ocr.json", tmp / f"{SLUG}.ocr.json")
    # panels stay read-only in place; symlink the dir so panel refs resolve.
    (tmp / "panels").symlink_to(CH4 / "panels")
    # seed the detector sidecars if a previous eval run left them (idempotent
    # speed-up only; a cold run recomputes them in ~60s).
    for side in (f"{SLUG}.textboxes.json", f"{SLUG}.yolo109.json"):
        prev = Path("/tmp/opencode/exp017-sidecars") / side
        if prev.exists():
            shutil.copy(prev, tmp / side)

    import verify_panels
    data = json.loads(script.read_text())
    reads = json.loads((tmp / f"{SLUG}.ocr.json").read_text())
    t0 = time.time()
    v = verify_panels.Verifier(data, reads, script, use_vision=False,
                               use_payoff=False, dry_run=True,
                               use_textonly=True)
    v.run()
    wall = time.time() - t0

    # keep sidecars warm for the next run
    warm = Path("/tmp/opencode/exp017-sidecars")
    warm.mkdir(parents=True, exist_ok=True)
    for side in (f"{SLUG}.textboxes.json", f"{SLUG}.yolo109.json"):
        if (tmp / side).exists():
            shutil.copy(tmp / side, warm / side)

    dropped = {}    # name -> reason (last wins; a panel can appear in 2 scenes)
    suspect = {}
    for e in v.log:
        if not e.get("panel"):
            continue
        name = Path(e["panel"]).name
        if e["action"] == "drop":
            dropped[name] = e["reason"]
        elif e["action"] == "textonly_suspect":
            suspect[name] = e["reason"]

    print("\n================ per-panel verdicts ================")
    tp_drop = tp_susp = fn = 0
    for n in POSITIVES:
        if n in dropped:
            tp_drop += 1
            verdict = f"CAUGHT (drop): {dropped[n]}"
        elif n in suspect:
            tp_susp += 1
            verdict = f"CAUGHT (suspect->tier3): {suspect[n]}"
        else:
            fn += 1
            verdict = "MISSED"
        print(f"  POS {n}  {verdict}")

    fp_drop = fp_susp = 0
    for n in negatives:
        if n in dropped:
            fp_drop += 1
            print(f"  NEG {n}  FALSE DROP: {dropped[n]}")
        elif n in suspect:
            fp_susp += 1
            print(f"  NEG {n}  suspect (tier-3 decides; acceptable): "
                  f"{suspect[n]}")

    caught = tp_drop + tp_susp
    # the acceptance target counts 11 drop ACTIONS; p0075 may be excluded.
    print("\n================ confusion matrix ================")
    print(f"  positives caught : {caught}/{len(POSITIVES)} "
          f"({tp_drop} deterministic drops, {tp_susp} forced tier-3)")
    print(f"  positives missed : {fn} "
          f"({[n for n in POSITIVES if n not in dropped and n not in suspect]})")
    print(f"  negatives        : {len(negatives)} kept-panel set")
    print(f"  false drops      : {fp_drop}")
    print(f"  neg suspects     : {fp_susp} (forced tier-3, kept if vision "
          f"sees art)")
    print(f"  wall time        : {wall:.1f}s (verifier run incl. detectors)")
    ok = caught >= 9 and fp_drop == 0
    print(f"\n  ACCEPTANCE (>=9/11 caught, 0 false drops): "
          f"{'PASS' if ok else 'FAIL'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
