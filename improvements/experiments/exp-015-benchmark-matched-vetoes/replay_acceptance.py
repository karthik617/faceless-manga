#!/usr/bin/env python3
"""
replay_acceptance.py — deterministic replay of the gap-015 acceptance criteria
against archived stable-review pairs. No new reviews are run; benchmark.py is
invoked on frozen JSON (plus archived work-dir videos for frame hashing).

Criteria (from improvements/gaps/gap-015-benchmark-matched-vetoes.md):
  C1  exp-009 v4 global (stable_base vs stable_t4): cropped_content 4->5 veto
      no longer fires under --matched-vetoes; verdict driven by score only
      (score improved -12 => PASS), suppression annotated with frame-hash
      proof.
  C2  exp-004 scoped 5-pass symmetric (stable_seg_v1_5pass vs
      stable_seg_v2_5pass, --scenes-changed as in the addendum): the
      unmatched-churn vetoes (new weak_hook/phone_readability; worsened
      cropped_content/irrelevant_panel/missing_payoff) are all suppressed and
      annotated. Verdict may still be FAIL — but only on the score delta,
      never on a veto.
  C3  exp-009 v1 (stable_base vs stable_t): the REAL regression
      (cropped_content 4->7, crop-consistent faults on changed scenes) still
      vetoes -> FAIL.
  C4  gap-003 v2 (stable_base vs stable_w2, scoped to the 9 cleaned scenes):
      the p0017 residual-ghost fault cluster on changed frames still
      vetoes -> FAIL.
  C5  same-video-twice control: two stable reviews of the IDENTICAL video
      produce zero vetoes. gap-011's original run/ inputs are gone
      (round1-eval keeps only stable_spread.md); substitute = exp-004
      stable_seg_v2.json (3-pass) vs stable_seg_v2_5pass.json (5-pass), both
      reviews of the same sfxed.mp4 — this also exercises the
      fixed-consensus normalization (3 vs 5 passes).

Usage: ./venv/bin/python3 improvements/experiments/exp-015-benchmark-matched-vetoes/replay_acceptance.py
Exit 0 = all criteria pass, 1 = at least one fails.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY = sys.executable
BENCH = ROOT / "pipeline/benchmark.py"
EXP = Path(__file__).resolve().parent
R2 = ROOT / "improvements/experiments/round2-eval"
E4 = ROOT / "improvements/experiments/exp-004-segmentation"

# Archived videos for frame hashing. The exp-004 bench videos lived in /tmp
# (never copied into the repo — 0.5 GB each) and WERE evicted 2026-09-12;
# every pair now also has an archived framemd5 sidecar under replay/sidecars/
# (exp-015 fixture hardening). Preference order per check: live video if
# present, else sidecar — the sidecar reproduces the exemption decisions
# bit-for-bit (see sidecars/reconstruct_from_proofs.py for C2/C5 provenance),
# so no assertion is weakened when a video is gone.
V_BASE = R2 / "_work_base/ambed.mp4"
V_T4 = R2 / "_work_t4/ambed.mp4"
V_T = R2 / "_work_t/ambed.mp4"
V_W2 = R2 / "_work_w2/ambed.mp4"
V_SEG_V1 = Path("/tmp/opencode/exp004_bench/proj/_work/sfxed.mp4")
V_SEG_V2 = Path("/tmp/opencode/exp004_bench_v2/proj/_work/sfxed.mp4")

SIDECARS = EXP / "replay/sidecars"
SC_C1 = SIDECARS / "c1_stable_t4_vs_base.hash_sidecar.json"
SC_C2 = SIDECARS / "c2_seg_v2_vs_v1_5pass.hash_sidecar.json"
SC_C3 = SIDECARS / "c3_stable_t_vs_base.hash_sidecar.json"
SC_C4 = SIDECARS / "c4_stable_w2_vs_base.hash_sidecar.json"
SC_C5 = SIDECARS / "c5_seg_v2_selfpair.hash_sidecar.json"

RESULTS: list[tuple[str, bool, str]] = []


def run_bench(tag: str, baseline: Path, review: Path, *, matched: bool,
              hash_video: Path | None = None, hash_base: Path | None = None,
              sidecar: Path | None = None, scenes: str | None = None) -> dict:
    out_dir = EXP / "replay" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [PY, str(BENCH), "--experiment", str(out_dir),
           "--baseline", str(baseline), "--review", str(review)]
    if scenes:
        cmd += ["--scenes-changed", scenes]
    if matched:
        cmd += ["--matched-vetoes"]
        if hash_video and hash_video.exists() and hash_base and hash_base.exists():
            cmd += ["--hash-video", str(hash_video),
                    "--hash-baseline-video", str(hash_base)]
        elif sidecar and sidecar.exists():
            # Video evicted: replay hashing from the archived sidecar.
            # benchmark.py gives live videos precedence, so passing both
            # would be harmless — but we only pass the one actually used to
            # keep metrics.json inputs honest.
            cmd += ["--hash-sidecar", str(sidecar)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    (out_dir / "stdout.txt").write_text(r.stdout + r.stderr)
    return json.loads((out_dir / "metrics.json").read_text())


def check(name: str, ok: bool, detail: str):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def suppressed_types(m: dict) -> set[str]:
    return {a["type"] for a in m.get("matched_vetoes", {}).get("annotated_vetoes", [])
            if a.get("suppressed")}


def veto_text(m: dict) -> str:
    return " | ".join(m["vetoes"]) if m["vetoes"] else "(none)"


# --------------------------------------------------------------------------
print("== C1: exp-009 v4 global — noise veto suppressed, score decides ==")
m = run_bench("c1_exp009_v4", R2 / "stable_base.json", R2 / "stable_t4.json",
              matched=True, hash_video=V_T4, hash_base=V_BASE, sidecar=SC_C1)
ann = m.get("matched_vetoes", {}).get("annotated_vetoes", [])
cc = next((a for a in ann if a["type"] == "cropped_content"), None)
check("C1a cropped_content 4->5 veto gone",
      not any("cropped_content" in v for v in m["vetoes"]),
      veto_text(m))
check("C1b verdict driven by score only (delta -12 => PASS, zero vetoes)",
      m["verdict"] == "PASS" and not m["vetoes"] and m["score"]["delta"] == -12,
      f"verdict={m['verdict']} delta={m['score']['delta']}")
has_proof = bool(cc and cc.get("suppressed") and
                 any(p.get("frame_hash_exempt") for p in cc["pairs_added"]))
check("C1c suppression annotated with frame-hash proof (never silent)",
      has_proof,
      json.dumps(cc["pairs_added"], default=str)[:200] if cc else "no annotation")

# --------------------------------------------------------------------------
print("== C2: exp-004 scoped 5-pass symmetric — churn vetoes suppressed ==")
SCOPE = "0,1,3,4,10,13,14,15,16,18,19,20,21,23,24,26"  # metrics_ab_5pass.json
m_old = run_bench("c2_default", E4 / "stable_seg_v1_5pass.json",
                  E4 / "stable_seg_v2_5pass.json", matched=False, scenes=SCOPE)
m = run_bench("c2_exp004_5pass", E4 / "stable_seg_v1_5pass.json",
              E4 / "stable_seg_v2_5pass.json", matched=True,
              hash_video=V_SEG_V2, hash_base=V_SEG_V1, sidecar=SC_C2,
              scenes=SCOPE)
check("C2a default arithmetic still vetoes (control)",
      bool(m_old["vetoes"]), veto_text(m_old))
check("C2b matched arithmetic: zero vetoes",
      not m["vetoes"], veto_text(m))
sup = suppressed_types(m)
check("C2c every old veto type annotated as suppressed",
      {"weak_hook", "phone_readability", "cropped_content",
       "irrelevant_panel", "missing_payoff"} <= sup,
      f"suppressed={sorted(sup)}")
check("C2d verdict from score only (delta +21 => FAIL on score, no veto)",
      m["verdict"] == "FAIL" and m["score"]["delta"] == 21 and not m["vetoes"],
      f"verdict={m['verdict']} delta={m['score']['delta']}")

# --------------------------------------------------------------------------
print("== C3: exp-009 v1 — REAL regression still vetoes ==")
m = run_bench("c3_exp009_v1", R2 / "stable_base.json", R2 / "stable_t.json",
              matched=True, hash_video=V_T, hash_base=V_BASE, sidecar=SC_C3)
check("C3a cropped_content regression still vetoes",
      any("cropped_content" in v for v in m["vetoes"]), veto_text(m))
check("C3b verdict FAIL", m["verdict"] == "FAIL",
      f"verdict={m['verdict']} delta={m['score']['delta']}")

# --------------------------------------------------------------------------
print("== C4: gap-003 v2 — watermark residual ghost (p0017) still FAILs ==")
# Scoped to the 9 scenes exp-003 v2 actually cleaned (metrics.json archive).
m = run_bench("c4_gap003_v2", R2 / "stable_base.json", R2 / "stable_w2.json",
              matched=True, hash_video=V_W2, hash_base=V_BASE, sidecar=SC_C4,
              scenes="0,4,7,9,13,15,17,20,22")
# The p0017 residual manifests on scene 0 (changed pixels): unreadable_text +
# watermark cluster. Changed-frame faults must stay veto-eligible.
check("C4a real new faults on changed frames still veto",
      bool(m["vetoes"]), veto_text(m))
check("C4b verdict FAIL", m["verdict"] == "FAIL", f"verdict={m['verdict']}")
s0 = [p for a in m.get("matched_vetoes", {}).get("annotated_vetoes", [])
      for p in a["pairs_added"] if p["scene"] == 0]
check("C4c scene-0 (p0017 ghost site) pairs NOT hash-exempt (pixels changed)",
      bool(s0) and not any(p["frame_hash_exempt"] for p in s0),
      json.dumps(s0, default=str)[:200])

# --------------------------------------------------------------------------
print("== C5: same-video-twice control — zero vetoes ==")
# SUBSTITUTION (documented in IMPLEMENTATION.md): gap-011's round1-eval run/
# inputs no longer exist. stable_seg_v2.json (3-pass) vs
# stable_seg_v2_5pass.json (5-pass) are two independent stable reviews of the
# IDENTICAL video (exp004_bench_v2 sfxed.mp4) and additionally exercise the
# fixed-consensus normalization (3 vs 5 passes -> common 2/3 bar).
m_old = run_bench("c5_default", E4 / "stable_seg_v2.json",
                  E4 / "stable_seg_v2_5pass.json", matched=False)
m = run_bench("c5_same_video", E4 / "stable_seg_v2.json",
              E4 / "stable_seg_v2_5pass.json", matched=True,
              hash_video=V_SEG_V2, hash_base=V_SEG_V2, sidecar=SC_C5)
check("C5a default arithmetic vetoes on same-video churn (control)",
      bool(m_old["vetoes"]), veto_text(m_old))
check("C5b matched arithmetic: ZERO vetoes on identical video",
      not m["vetoes"], veto_text(m))
frac = m.get("matched_vetoes", {}).get("consensus_fraction")
pc = m.get("matched_vetoes", {}).get("pass_counts")
check("C5c consensus normalized across 3-pass vs 5-pass arms",
      pc == {"baseline": 3, "experiment": 5} and abs(frac - 2 / 3) < 1e-3,
      f"pass_counts={pc} fraction={frac}")

# --------------------------------------------------------------------------
print()
n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
print(f"{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed")
summary = {name: {"ok": ok, "detail": detail} for name, ok, detail in RESULTS}
(EXP / "replay" / "summary.json").write_text(json.dumps(summary, indent=2))
sys.exit(1 if n_fail else 0)
