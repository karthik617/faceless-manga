#!/usr/bin/env python3
"""
improve_nudge.py — post-run bridge from the video pipeline to the
improve-train loop.

WHY: the per-video fix loop (review_video.py --apply-fixes) repairs ONE video,
but faults that recur chapter after chapter are pipeline defects — those are
what /improve-train exists for. This hook runs at the end of every manga.py
run: it appends the chapter's confirmed fault summary to the ledger's evidence
pool (so gap-analyst has trend data across chapters), cross-references open
gaps by fault type, and prints a nudge when a training round looks worthwhile.

It NEVER fails the pipeline — any error degrades to a one-line warning — and
it never modifies gap statuses; it only appends evidence.

Usage (also callable standalone to re-scan a project):
  ./venv/bin/python3 pipeline/improve_nudge.py output/<slug>
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "improvements" / "ledger.json"

# a fault type is "recurring" (pipeline-defect signal) when it shows up in at
# least this many distinct chapters in the evidence pool
RECUR_CHAPTERS = 2
# nudge when this many high-severity faults survive the fix loop in one run
HIGH_NOW = 3


def _load_faults(proj: Path) -> list[dict]:
    """Prefer the post-fix-loop review (what actually shipped); the raw
    pre-fix review would double-count faults the existing loop already
    handles fine."""
    for name in ("review.json",):
        p = proj / name
        if p.exists():
            try:
                data = json.loads(p.read_text())
                faults = data if isinstance(data, list) else data.get("faults", [])
                return [f for f in faults if isinstance(f, dict) and f.get("type")]
            except Exception:
                pass
    return []


def summarize(faults: list[dict]) -> dict:
    sev = Counter(f.get("severity", "low") for f in faults)
    return {
        "total": len(faults),
        "high": sev["high"], "medium": sev["medium"], "low": sev["low"],
        "by_type": dict(Counter(f["type"] for f in faults)),
    }


def run(project_dir: str) -> int:
    proj = Path(project_dir)
    slug = proj.name
    faults = _load_faults(proj)
    summary = summarize(faults)

    try:
        ledger = json.loads(LEDGER.read_text())
    except Exception as e:
        print(f"  [improve] ledger unreadable ({e}) — skipping nudge")
        return 0

    # ---- append evidence (idempotent per slug: re-runs overwrite) ----
    evidence = ledger.setdefault("evidence", {})
    evidence[slug] = {"date": str(date.today()), **summary}

    # ---- recurrence analysis across all recorded chapters ----
    chapters_with_type: Counter = Counter()
    for ch in evidence.values():
        for t in ch.get("by_type", {}):
            chapters_with_type[t] += 1
    recurring = {t: n for t, n in chapters_with_type.items()
                 if n >= RECUR_CHAPTERS and summary["by_type"].get(t)}

    # ---- map fault types -> open gaps ----
    open_gaps = [g for g in ledger.get("gaps", [])
                 if g.get("status") not in ("adopted", "rejected")]
    hits = []
    for g in open_gaps:
        overlap = set(g.get("fault_types", [])) & set(summary["by_type"])
        if overlap:
            mass = sum(summary["by_type"][t] for t in overlap)
            hits.append((mass, g["id"], sorted(overlap)))
    hits.sort(reverse=True)

    try:
        LEDGER.write_text(json.dumps(ledger, indent=2))
    except Exception as e:
        print(f"  [improve] could not write ledger: {e}")

    # ---- the nudge ----
    print(f"  [improve] {slug}: {summary['total']} fault(s) survived the fix "
          f"loop ({summary['high']} high, {summary['medium']} medium)")
    for t, n in recurring.items():
        print(f"  [improve] recurring across {chapters_with_type[t]} chapters: {t}")
    if hits:
        top = ", ".join(f"{gid} ({'/'.join(ts)})" for _, gid, ts in hits[:3])
        print(f"  [improve] open gaps matching this run: {top}")

    worth_it = summary["high"] >= HIGH_NOW or recurring
    if worth_it:
        print(f"  [improve] >>> worth a training round: open opencode and run "
              f"/improve-train"
              + (f"  (top gap: {hits[0][1]})" if hits else ""))
    else:
        print("  [improve] no training round needed for this run")
    return 0


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    return run(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())
