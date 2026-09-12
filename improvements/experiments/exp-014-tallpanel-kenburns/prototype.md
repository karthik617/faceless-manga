# exp-014 prototype summary (gap-014 + gap-006)

**Flag:** `panel_render.py --kenburns smart` (or `MANGA_KB_SMART=1`).
Default `--kenburns center` = byte-identical to today (framemd5-verified).

**What changed and why:**
- `pipeline/kb_smart.py` (new): frame planner (vertical scroll for h/w>2.5
  panels, box-aware vertical anchor for the rest), anime-face sidecar cache,
  and the shared window-containment math used by both renderer and gate.
- `pipeline/panel_render.py`: `--kenburns` flag, `frame_plan` parameter on
  `render_panel_scene` (None = untouched path), scroll render branch, plan
  logging to `<workdir>/kb_plans.json`.
- `pipeline/yolo_detect.py`: NOT modified — the smoke killed the manga109
  face-class plan (0/8 recall on colored webtoons); faces come from
  deepghs/anime_face_detection (MIT, ~43 MB runtime download, same vendored
  `_predict` path, zero pip deps).
- `make_video.py`, `review_video.py`, `requirements.txt`: untouched.

**Dep installs performed:** none. One HF model auto-download at first
`--kenburns smart` use (`deepghs/anime_face_detection/face_detect_v1.4_s`,
MIT — recorded in IMPLEMENTATION.md §1).

**Fallback behavior:** every failure (text boxes unavailable, face model
download/inference error, planner exception, infeasible geometry) logs a
warning and returns `frame_plan=None` → exact center-anchored path. No new
crash surface; flag off never executes any new code.

**How to run the stage-scoped experiment:** IMPLEMENTATION.md §7 (copied
inputs at `/tmp/opencode/exp014_render/proj`, script `mini-fault.json` =
ch2-bench fault scenes; gate: `check_containment.py <script> <workdir>`).

**Results:** containment gate 47 violations (baseline center) → 0 (smart),
exit 0; flag-off byte-identical (framemd5); wall 1.09x warm / 1.23x with
one-time detection (≤1.2x bound met at steady state). Samples in `samples/`.

---

## v2 iteration (2026-09-12, post-eval FAIL → user-approved iterate)

Two targeted fixes in `kb_smart.py` + one render branch in
`panel_render.py`, same flag, flag-off path untouched (framemd5-verified
at unit level 4/4 and through `render()` on a 3-scene mini project 3/3):

1. **Text-coverage scroll gate** (`MIN_SCROLL_TEXT_AREA = 0.01`): tall
   panels scroll only when OCR text-box area ≥ 1% of the panel — SFX/art
   panels (the s14/p0075 regression: one 76×76 box, 0.42%) keep the
   letterbox fit via the anchor path. Threshold separates the v1
   populations with ~2.4x margin on both sides.
2. **Explicit "contain" plan for infeasible geometry**: when a tall
   panel's text union fits NO legal scroll/anchor window AND the center
   fallback would provably cut text (checked with the gate's own math),
   the planner emits a static full-fit letterbox plan (zoompan z=1) —
   zero crop ⇒ zero violations by construction. Fires on exactly
   p0002/p0024, the two panels behind the v1 chapter-wide gate failure.

**v2 evaluator command** (full golden chapter, copied project):
```bash
cp -r output/the-world-after-the-fall-ch2 /tmp/opencode/<eval>/proj  # copy!
cd ~/faceless-manga
./venv/bin/python3 pipeline/panel_render.py \
    /tmp/opencode/<eval>/proj/the-world-after-the-fall-ch2.json \
    --res 1080p --no-music --no-sfx --no-branding --no-title-card \
    --kenburns smart --stop-after review-cut --workdir <proj>/work_on
./venv/bin/python3 improvements/experiments/exp-014-tallpanel-kenburns/check_containment.py \
    <proj>/the-world-after-the-fall-ch2.json <proj>/work_on --res 1080p
# expect: tall cuts 27, beat-window checks 592, violations 0, exit 0
```

**v2 results:** chapter-wide gate 95 → **0** (exit 0; baseline re-confirmed
at 95 on the same beats); p0075 letterboxed (frame evidence samples/v2/);
s6 heal persists (p0026 anchor plan bit-identical v1→v2); p0012 still
scrolls (positive example, frames in samples/v2/). Plan census:
98 anchor / 9 scroll / 2 contain / 58 center. Details: IMPLEMENTATION.md §8.
Gap card amended per the evaluator finding (unmeasurable scene-triplet
criterion → three measurable golden-project criteria).
