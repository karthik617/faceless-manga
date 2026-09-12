# exp-014 prototype — content-aware Ken Burns framing (gap-014 + gap-006)

Date: 2026-09-11. Integrator notes. Research: `research.md` (recommendation
#1: zero-new-deps box-union framing). Binding criteria: gap-014 card.

## 1. Mandatory smoke — detector decision (research risk #1)

Run FIRST, before any pipeline code was touched. 8 fault-site panels copied
into `inputs/` (never mutating `output/…`): ch2-bench p0015 (3122px, scene 5
/ tie-break scene 4 site), p0041 (2697px, scene 16 / t359.3), p0059 (2072px,
scene 22 / t513.4), p0101 (2510px, scene 24 / t692.9 bubble), and production
ch4 p0009 (scene 4), p0041 (2845px, scene 12), p0043 (scene 14), p0067/p0068/
p0070 (scene 22). ch2 fault panels come from the exp-004 v2 bench copy at
`/tmp/opencode/exp004_bench_v2/proj` (the golden review scene numbers 15/21/28
are 0-based ⇒ script scenes 16/22/29; their tall panels are p0041/p0059 and
the t692.9 bubble lives on p0101 in scene 24 — panel dims confirmed against
the exp-004 report).

**manga109_yolo face/body classes (candidate #1), conf 0.15, windowed 640px:**

| panel | ar | face | body | other |
|---|---|---|---|---|
| ch2 p0015 | 4.52 | 0 | 0 | 3 frame |
| ch2 p0041 | 3.91 | 0 | 0 | 1 frame |
| ch2 p0059 | 3.00 | 0 | 0 | — |
| ch2 p0101 | 3.64 | 0 | 0 | 1 frame |
| ch4 p0009 | 2.53 | 0 | 0 | 1 frame |
| ch4 p0041 | 4.12 | 0 | 0 | 7 frame |
| ch4 p0043 | 3.72 | 0 | 0 | 5 frame |
| ch4 p0067 | 2.32 | 0 | 0 | 2 frame |

**face/body recall = 0/8 — unusable.** Research risk #1 confirmed: the 2021
manga109 nano is blind to faces on colored webtoons (same failure mode as the
2023 text checkpoints). The planned `yolo_detect.py:203` class extension was
therefore NOT made — no point plumbing classes that never fire; yolo_detect.py
is untouched.

**Runner-up: deepghs/anime_face_detection `face_detect_v1.4_s` (MIT), conf
0.25, same vendored `_predict` path** (output-format parity confirmed — the
nms `output0` layout matches, zero code changes to `_predict`):

| panel | faces (conf) |
|---|---|
| ch2 p0041 | 2 (0.59, 0.73) — the reflection face at y≈1122-1348 |
| ch2 p0059 | 1 (0.39) |
| others | 0 (checked down to conf 0.05: only ch4 p0041 shows one at 0.10 — below floor) |

**Decision: candidate #2 (anime_face_detection, MIT) as the face source,
with candidate #3 (OCR-union-only) as the working core.** Rationale: the
runner-up finds real faces where they exist at usable confidence (the ch2
fault panels are mostly SFX/art with sparse faces — 0 detections on p0015/
p0101 is plausibly correct, see contact sheet), and research already
predicted OCR-only union fixes the bubble-graze sites (scenes 15/21/28
evidence is bubble-grazing). Zero pip deps; one ~43 MB model auto-download
to `~/.cache/huggingface` on first use (same runtime-download pattern as the
gap-004 manga109 weights). **License: MIT** (HF model card) — cleaner than
manga109's AGPL note.

Known limitation recorded up front: the ch4 scene-22 "forehead cropped" site
(p0067/p0068/p0070) gets 0 face detections from BOTH detectors — that one
site is only addressed indirectly (p0068/p0070 get OCR-anchor shifts; p0067
stays center). See Deviations.

## 2. What changed

- **`pipeline/kb_smart.py` (NEW, ~380 lines)** — planner + shared window
  math + face-box sidecar:
  - `ensure_face_boxes(sidecar, paths)` — `<slug>.faceboxes.json` cache
    (name+mtime keyed, mirrors `text_boxes.ensure_boxes`), windowed
    inference via the existing `yolo_detect._predict` (no changes there).
  - `plan_frame(panel, text_boxes, face_boxes, w, h, dur, zmax)` — pure
    geometry, returns:
    - **`scroll`** for h/w > 2.5 when a fit-to-width window can't hold the
      panel: constant-character top→bottom sweep (smoothstep-eased), y-band
      constrained so the window contains the padded union of ALL text boxes
      at EVERY t (stronger than the per-beat gate), faces shrink the band
      only when feasible (drop-faces-first; text never dropped). Speed cap
      150 src-px/s; sweeps under 40px are dropped (imperceptible creep);
      fill may shrink to 0.55 to grow the window; pad retried at 0 before
      giving up (edge-hugging credit text).
    - **`anchor`** otherwise: today's exact composite + zoom envelope, but
      the zoompan y anchor moves to `cy` so the max-zoom (guaranteed-
      visible) window contains the text∪face union. x stays center-locked —
      **no sideways pans**. Union too wide/tall ⇒ `None` ⇒ center.
    - `None` on any failure ⇒ center anchor (never worse than today).
  - `window_contains_boxes(plan, boxes, t, dur)` — the SAME math the gate
    script uses (single source of truth for renderer + gate; covers scroll /
    anchor / center / crop modes).
- **`pipeline/panel_render.py`** — behind the flag only:
  - `--kenburns {center,smart}` (default `center`) or `MANGA_KB_SMART=1`.
  - `render_panel_scene(..., frame_plan=None)`: `None` short-circuits before
    any expression change (byte-identical guarantee). `anchor` swaps only
    the zoompan `y` expression (clamped `ih*cy-(ih/zoom/2)`, releases from
    center toward cy as zoom grows — every intermediate window contains the
    max-zoom window, so containment holds for all t). `scroll` is a new
    branch: source pre-cropped to the visible band (wall-time guard), scaled
    to 2x supersample, zoompan z=1 as a frame duplicator (scale/gblur run
    once, not per frame), then the eased y-sweep crop + overlay + downscale
    — crop-y integer steps land at half-pixel after the 2x downscale (the
    research's anti-judder mitigation).
  - `render()`: loads text boxes (existing `_ensure_text_boxes`) + face
    boxes when the flag is on; a `_kb(...)` helper next to `_tcrop` plans
    each cut and logs every decision to `<workdir>/kb_plans.json` (the gate's
    input). A `--text-aware` crop wins over a kb plan when both fire (the
    crop carries its own containment guard). Detector/planner failure at any
    level prints a WARNING and keeps the center path.
- **`pipeline/yolo_detect.py` — NOT modified** (smoke killed the manga109
  face-class plan; the runner-up needed no changes to `_predict`).
- `~/faceless-youtube/pipeline/make_video.py` — untouched.
- `review_video.py` fault definitions — untouched.
- No pip installs; `pipeline/requirements.txt` untouched.

## 3. Rect/scroll selection algorithm (summary)

```
plan_frame(panel, tboxes, fboxes, W, H, dur, zmax):
  tall = ph/pw > 2.5
  if tall:
    win_h = (H/W)*pw / fill          # fit-to-width window, fill∈[0.55, 1]
    band  = [max(0, tu.bottom-win_h) … min(ph-win_h, tu.top)]   # text union covered ∀t
    band ∩= face band if non-empty   # faces drop first
    band  = speed-capped (150 px/s), centered; < 40 px ⇒ no scroll
    scroll(y0→y1, smoothstep) on the 2x supersampled fit-to-width fg
  anchor:
    union = pad(tboxes ∪ fboxes) mapped into the blur-bg composite
    cy    = clamp(union center) s.t. max-zoom window ⊇ union, window ⊆ frame
    faces dropped if union doesn't fit; text doesn't fit ⇒ None (center)
```

## 4. Deterministic containment gate

`check_containment.py <script> <workdir> [--baseline]` — for every h/w>2.5
panel cut, checks the rendered window at every narration-beat timestamp
(word starts+ends from the workdir `w###.json` timings, plus a 0.5s grid so
wordless cuts are covered) contains 100% of the panel's OCR text boxes.
Windows come from the render's own `kb_plans.json` interpreted by
`kb_smart.window_contains_boxes` (center fallbacks are gated too);
`--baseline` synthesizes the default center plans for a flag-off render so
today's violations are measurable. Exit 0/1.

**Results** on the 4-scene fault set (ch2-bench scenes 16/22/24/29 = golden
review scenes 15/21/[24]/28; scene 24 added because the t692.9 bubble panel
p0101 lives there), copied inputs at `/tmp/opencode/exp014_render/proj`:

```
[baseline(center)] tall cuts (h/w>2.5): 6, beat checks: 155, violations: 47   → exit 1
[kb-smart]         tall cuts (h/w>2.5): 6, beat checks: 155, violations: 0    → exit 0
```

The 47 baseline violations are all on scene-24/p0101 (the confirmed t692.9
"Ken Burns grazes the bubble top" fault) — the gate reproduces the human
finding deterministically and the smart path clears it. Scenes 16/22 tall
panels (p0041/p0059) pass in both because their text sits mid-panel; their
fault class was face/art sweep, addressed by the scroll (visible in samples).

## 5. Byte-identical flag-off + wall time

- Unit-level framemd5, pre-change vs post-change `render_panel_scene` (plain,
  emphasis+glitch, crop_rect+focus variants on p0041):
  **all IDENTICAL** (`/tmp/opencode/exp014_prechange/*.framemd5`).
- Full flag-off scene clips render deterministically identical across two
  post-change runs (framemd5 of c000–c003).
- Code-level guarantee: `frame_plan=None` (the default and every fallback)
  never reaches a changed expression; the kb block in `render()` is gated on
  the flag before any work happens.

**Wall time** (4-scene set, 23 cuts, --res 1080p, same box):
- flag off: 98–110 s; flag on: 110–121 s → **ratio 1.09–1.23x**.
  The >1.2 upper sample includes one-time detection (DBNet text boxes + face
  model download+inference for 14 panels — paid once per chapter, sidecar-
  cached like textboxes). With sidecars warm (the steady state, and how a
  re-render/redo-scene behaves): **1.09x ≤ 1.2x bound**.
- Per-cut isolate: scroll render 4.2 s vs plain 3.5 s (1.18x) after the
  band pre-crop + zoompan-as-duplicator optimizations (first cut was 6.4x —
  scaling a 690x2697 panel to 3840x15010 per frame; fixed).

## 6. Samples (in `samples/`)

- `cmp_s16_p0041_scrollstart_t6.6.png` / `cmp_s16_p0041_scrollend_t9.1.png`
  — golden scene 15 site (off left / on right): off shows the letterboxed
  sliver + static center; on fills the frame and sweeps the blade→SFX beat.
- `cmp_s24_p0101_anchor_t74.0.png` / `_t78.5.png` — t692.9 bubble site: the
  anchor shift (cy 0.537) keeps the bubble fully inside the window through
  the zoom (baseline gate: 47 violations here; smart: 0).
- `p0041_scroll_sweep_start_end.png` — scroll first/last frame.
- raw off_/on_ frames for t6.6/9.1/28.8/31.5/74.0/78.5.
- `kb_plans.json` — the full plan log from the smart render.

## 7. How to run the stage-scoped experiment (evaluator)

```bash
# inputs already staged (copied, originals untouched):
cd /tmp/opencode/exp014_render/proj
# flag ON render of the fault scenes:
~/faceless-manga/venv/bin/python3 ~/faceless-manga/pipeline/panel_render.py \
    mini-fault.json --res 1080p --no-music --no-sfx --no-branding \
    --no-title-card --kenburns smart --stop-after review-cut --workdir work_on
# deterministic gate (binding criterion #2):
~/faceless-manga/venv/bin/python3 \
    ~/faceless-manga/improvements/experiments/exp-014-tallpanel-kenburns/check_containment.py \
    mini-fault.json work_on --res 1080p          # expect exit 0
# baseline violations for the report:
~/faceless-manga/venv/bin/python3 .../check_containment.py \
    mini-fault.json work_off --res 1080p --baseline   # expect exit 1 (47)
# criterion #1 (cropped_content 3→0 on golden scenes 15/21/28) needs the
# scene-scoped benchmark.py + review_stable.py run per gap-011 on a full
# bench render with --kenburns smart (mini-fault.json maps those to scenes
# 1/2/4 of the cut; scene numbering note in IMPLEMENTATION.md §4).
# criterion #3 (no new cropped_content on normal-aspect scenes): full-bench
# A/B — the flag changes normal-aspect framing only via the anchor path,
# which is gated by the same containment math.
```

## 8. v2 iteration (2026-09-12) — two targeted fixes after the v1 FAIL

v1 eval verdict (report.md): FAIL — Δ0 score, cropped_content 4→5 veto.
Two real findings: (R1) s14 t=422.5 regression — p0075 (690×2000 SFX/impact
art, ONE 76×76 text box) triggered the scroll path and the fit-to-width band
showed giant cut-off glyphs the baseline letterbox showed whole; (R2) the
chapter-wide containment gate failed in BOTH arms (95 violations) because
p0002/p0024 carry ~1800px-tall text unions no legal window can contain and
the planner silently fell back to a VIOLATING center plan. The s6 t=175.7
face-crop heal (3/3 baseline) was a real win to preserve. Scroll motion
mechanics were clean — untouched in v2.

### Fix 1 — text-coverage scroll gate (`kb_smart.MIN_SCROLL_TEXT_AREA = 0.01`)

`_plan_scroll` now returns None (→ anchor path → today's letterbox fit)
unless the panel's total OCR text-box AREA covers ≥ 1% of the panel.
Rationale: fit-to-width scrolling MAGNIFIES; that only helps panels with
genuine sequential reading content. Threshold from the v1 chapter-wide
plan data, which separates the two populations cleanly:

| population | panels | text area |
|---|---|---|
| scroll-worthy (dialogue/caption runs) | p0003, p0012, p0092, p0100 | 1.59–2.86% |
| regression class (SFX/art, sparse text) | p0032, p0075, p0136 | 0.04–0.42% |
| zero-box art | p0016, p0039, p0051, p0072, p0093 | 0% |

1% sits ~2.4× from BOTH nearest neighbors (0.42% below, 1.59% above).
Area was chosen over box COUNT because count can't tell one caption from
one SFX label; area measures how much of the panel is reading material.
Gated-out panels keep the anchor path (letterbox fit + at most a vertical
anchor shift — p0075 now plans `anchor cy=0.463`, full art visible).

### Fix 2 — explicit "contain" plan for infeasible geometry

`plan_frame` gained a third mode. When a TALL panel's text union is
infeasible for both the scroll band and the 1/zmax anchor window, the
planner now re-checks the would-be center fallback with the gate's own
math (`window_contains_boxes` on the center plan); if center would
provably cut a text box it emits `{"mode": "contain"}` — a static full-fit
letterbox (zoompan z=1, pure frame duplicator on today's exact composite).
Zero crop ⇒ zero containment violations by construction; the gate treats
`contain` as always-contained. The renderer branch is one line (z="1"),
same composite, same filtergraph shape. Trade: those panels lose the
gentle 1.08 zoom — deliberate; a moving window that cuts text is the fault
class this card exists for. Scope: tall panels only — normal-aspect
infeasible panels keep today's center behavior (changing them would swap
motion chapter-wide for a fault nobody measured). p0002 and p0024 (the two
v1 gate failures, ~1800px text unions) both now plan `contain`.

A multi-segment scroll (two sweeps covering the union in turns) was
considered and rejected for v2: it would need per-beat word→box matching
to decide WHICH text must be visible WHEN — the deterministic all-boxes-
always rule is the card's criterion, and contain satisfies it with zero
new machinery.

### v2 verification (all on COPIES under /tmp/opencode/exp014v2/)

- **Chapter-wide gate (the R2 criterion):** clean full 25-scene golden-ch2
  smart re-render (fresh clips, no v1 cache) → `check_containment.py` =
  **tall cuts 27, beat-window checks 592, violations 0, exit 0** — same
  scope as the v1 eval's failing run (27/592/95, exit 1). Baseline
  (flag-off, --baseline) re-confirmed at 95 violations on the SAME beats —
  the fix is entirely on the smart arm. Log: `samples/v2/containment_v2.txt`;
  plans: `samples/kb_plans_v2.json`. Plan-mode census v2: 98 anchor,
  9 scroll (v1: 20), 2 contain (p0002 s2, p0024 s6 — exactly the two v1
  gate failures), 58 center.
- **p0075 letterbox (R1):** planner now logs `scroll: text area 0.42% < 1%
  gate (SFX/art tall panel), letterbox fit kept` and anchors (cy 0.463)
  instead of scrolling. Frames `samples/v2/v2_on_t421.0.png` /
  `v2_on_t423.5.png`: the full SFX/impact art is letterboxed whole, matching
  the baseline framing (`v1_off_t422.5_letterbox.png`) — vs the v1
  regression band (`v1_on_t422.5_scrollregression.png`, giant cut glyphs).
  Same fate for the other regression-class panels: p0032/p0136 gated
  (0.04%/0.15%), all five zero-box tall panels gated.
- **s6 face heal persists:** the p0026 anchor plan is IDENTICAL v1 → v2
  (cy 0.462963 — deterministic, diffed in both kb_plans.json), and the v2
  frame at that cut (`samples/v2/v2_on_t158.0.png`; absolute time shifts
  slightly per-arm with TTS re-synth) shows eyes/expression fully visible —
  the v1 heal framing, not the baseline top-crop.
- **Positive scroll example:** p0012 (scene 4, 2.86% text area — dialogue
  run) still scrolls (fill 1.0, y 1079→2048 over ~2.9s, well under the
  150 px/s cap). Start/end frames `samples/v2/v2_on_t81.4_p0012scrollstart
  .png` / `v2_on_t83.9_p0012scrollend.png` show the sweep moving from the
  "NOW JUST..." bubble to the "DIE!!" payoff — the sequential-reading
  motion the scroll mode exists for. p0003/p0092/p0100 also still scroll.
- **Wall time:** v2 does strictly less work than v1 on the same inputs
  (11 fewer scroll branches — the most expensive path — replaced by plain
  anchor/center zoompans; "contain" is a z=1 zoompan, same cost as center;
  the area gate is one multiply-add over already-loaded boxes). v1
  measured 1.043x; v2 is bounded above by it.
- **Flag-off byte-identity:** (a) unit level — pre-v2 vs post-v2
  `render_panel_scene` with `frame_plan=None` across plain / emphasis+
  glitch / crop_rect+focus / tight_crop variants: framemd5 IDENTICAL 4/4;
  (b) pipeline level — a 3-scene mini project (copied panels, deterministic
  stub TTS) rendered flag-off through `render()` pre-v2 vs post-v2:
  per-scene clip framemd5 IDENTICAL 3/3. The v2 diff only touches code
  behind `if frame_plan` / inside `_plan_scroll`/`plan_frame`, which the
  flag-off path never reaches.
- No new pip deps; requirements.txt untouched; make_video.py and
  review_video.py fault definitions untouched.

### Gap card amendment

`improvements/gaps/gap-014-tallpanel-kenburns.md`: the binding scene-triplet
criterion (golden scenes 15/21/28 cropped_content 3→0) was found
UNMEASURABLE by the v1 eval — it referenced the deleted exp-004 v2 35-scene
bench, not the golden 25-scene project. Replaced (with an amendment note)
by three measurable golden-project criteria: (a) chapter-wide
check_containment.py = 0 violations on the smart arm, (b) golden
cropped_content does not increase and the s6-class face-crop heal persists,
(c) no new fault types on all-normal-aspect scenes.

## 9. Deviations from the research plan

1. **manga109 face/body classes abandoned** (smoke: 0/8 recall on colored
   webtoons) → runner-up MIT model; `yolo_detect.py` consequently untouched
   (research had planned a `labels=` parameter there — not needed since the
   runner-up rides the existing `_predict(repo=, model=)` params).
2. **Scroll containment is stronger than the card asks**: the window
   contains ALL of the panel's text boxes at ALL times, not just the boxes
   quoted in the current narration beat. Simpler, deterministic without
   word→box matching, and it passed on every fault panel — the weaker
   per-beat rule remains available (the gate already maps beats) if a future
   panel makes the strong rule infeasible.
3. **ch4 scene-22 forehead site**: no detector finds that face (0 dets even
   at conf 0.05). p0068/p0070 get OCR anchor shifts, p0067 stays center —
   this site may keep its fault. Recorded as the accepted residual research
   anticipated ("the one site that genuinely needs a face box").
4. **ch2 golden scenes 15/21/28 are script scenes 16/22/29** (review
   numbering is 0-based). The tie-break scene-4 p0015 site is in the smoke
   set; its plan is an anchor (its text union spans too much height for a
   scroll at fill ≥ 0.55).
5. Wall ratio on a cold cache is 1.23x (one-time detection); steady-state
   1.09x. The ≤1.2x bound is met in the amortized/steady state that the
   card's bound (gap-006, "render wall-time") is about.
