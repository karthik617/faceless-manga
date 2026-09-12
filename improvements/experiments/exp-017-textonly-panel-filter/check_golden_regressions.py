#!/usr/bin/env python3
"""check_golden_regressions.py — exp-017 v2 regression check for the two
golden-ch2 failure cases the v1 evaluation surfaced (report.md):

  p0086 (scene 16, "WHAT'S THAT?" bubble): v1's batched art-check dropped it
    and scene 16 grew a 3/3 missing_payoff — the narration quotes the bubble
    verbatim. v2 fix 1 must protect it via the narration-anchor guard
    (anchored_run_len >= ANCHOR_QUOTE_MIN).
  p0101 (scene 17, "LET'S GO, YOONHWAN!" over molten-terrain art): v1's
    batched art-check returned a false has_art=false (batch-position
    artifact) and scene 17 grew cropped_content + static_scene. v2 fix 2
    must catch it via the single-image confirmation — AND fix 1 also covers
    it (final panel of its scene), so we assert both layers independently.

Golden output/the-world-after-the-fall-ch2/ is NEVER mutated: script + OCR
are copied to /tmp/opencode, panels symlinked read-only. Two modes:

  default        — deterministic only: asserts _drop_protection() protects
                   both panels (no network). Always safe to run.
  --live         — additionally runs the full flagged verify (vision ON,
                   real gateway calls, ~3-8 vision requests) and asserts
                   both panels survive end-to-end; then bypasses fix 1 and
                   calls _confirm_no_art() directly on p0101 to prove the
                   single-image confirmation dissents from the v1 batch
                   verdict (has_art must come back true).

Run:
    ./venv/bin/python3 improvements/experiments/exp-017-textonly-panel-filter/check_golden_regressions.py [--live]
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipeline"))

CH2 = ROOT / "output/the-world-after-the-fall-ch2"
SLUG = "the-world-after-the-fall-ch2"
# 0-based scene indices in the golden final script (report.md numbering).
CASES = {"p0086.png": 16, "p0101.png": 17}


def stage_copy():
    """Copy the golden ch2 inputs into a scratch dir (never mutate golden)."""
    tmp = Path(tempfile.mkdtemp(prefix="exp017-golden-", dir="/tmp/opencode"))
    shutil.copy(CH2 / f"{SLUG}.json", tmp / f"{SLUG}.json")
    shutil.copy(CH2 / f"{SLUG}.ocr.json", tmp / f"{SLUG}.ocr.json")
    shutil.copy(CH2 / f"{SLUG}.textboxes.json", tmp / f"{SLUG}.textboxes.json")
    (tmp / "panels").symlink_to(CH2 / "panels")
    return tmp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="also run the full flagged verify with vision + a "
                         "direct single-image confirmation on p0101")
    args = ap.parse_args()

    import verify_panels as vp
    tmp = stage_copy()
    script = tmp / f"{SLUG}.json"
    data = json.loads(script.read_text())
    reads = json.loads((tmp / f"{SLUG}.ocr.json").read_text())
    failures = []

    # -- layer 1 (deterministic): fix-1 protection covers both panels --------
    v = vp.Verifier(data, reads, script, use_vision=False,
                    use_textonly=True, dry_run=True)
    scenes_kept = [list(sc.get("panels", [])) for sc in data["scenes"]]
    print("== deterministic protection (fix 1) ==")
    for name, si in CASES.items():
        ref = f"panels/{name}"
        prot = v._drop_protection(si, ref, scenes_kept)
        status = "PROTECTED" if prot else "NOT PROTECTED"
        print(f"  scene {si} {name}: {status} ({prot or '-'})")
        if not prot:
            failures.append(f"{name} not covered by _drop_protection")
    # p0086 must be protected specifically by the narration anchor (its v1
    # drop was NOT a vision error — the panel really is a bubble cutout; only
    # the quote makes it undroppable), so assert the reason class too.
    prot86 = v._drop_protection(16, "panels/p0086.png", scenes_kept)
    if prot86 and "quotes" not in prot86 and "quote" not in prot86:
        failures.append(f"p0086 protected but not by the narration anchor: "
                        f"{prot86}")

    if args.live:
        # -- layer 2 (live): end-to-end flagged verify keeps both ------------
        print("\n== live flagged verify (vision ON) ==")
        data2 = json.loads(script.read_text())
        v2 = vp.Verifier(data2, reads, script, use_vision=True,
                         use_textonly=True, dry_run=True)
        v2.run()
        dropped = {Path(e["panel"]).name for e in v2.log
                   if e["action"] == "drop" and e.get("panel")}
        for name in CASES:
            ok = name not in dropped
            print(f"  {name}: {'KEPT' if ok else 'DROPPED (regression!)'}")
            if not ok:
                failures.append(f"{name} dropped in live flagged verify")

        # -- layer 3 (live): fix 2 in isolation on p0101 ----------------------
        # Bypass fix 1 and ask the single-image confirmation directly: the
        # panel HAS art (molten terrain + character), so the confirmation
        # must dissent from v1's batched false negative (return False =
        # "do not drop"). This is the p0101 failure mode reproduced without
        # depending on the batch proposing a drop this run.
        print("\n== single-image confirmation on p0101 (fix 2, isolated) ==")
        would_drop = v2._confirm_no_art("panels/p0101.png")
        print(f"  _confirm_no_art(p0101) -> {would_drop} "
              f"(False = confirmation sees art = panel kept)")
        if would_drop:
            failures.append("single-image confirmation judged p0101 art-free")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'PASS' if not failures else 'FAIL'}: "
          f"{len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
