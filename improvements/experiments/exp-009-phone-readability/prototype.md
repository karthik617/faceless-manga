# exp-009 prototype — phone readability: text-size-aware framing

Gap: `improvements/gaps/gap-009-phone-readability.md` (priority 9).
Research: `research.md` (candidate A: PP-OCRv3 DBNet det-only ONNX via
cv2.dnn, with the cv2-classic detector as fallback).

Flag: **`--text-aware`** on `pipeline/panel_render.py` (and forwarded from
`manga.py --text-aware`). **Default OFF — default pipeline byte-identical**,
verified by md5-identical `render_panel_scene` output old-vs-new for both the
plain blur_bg path and `--tight-crop`, plus exp-002's smoke.py passing
unchanged (plan_layout with `text_boxes=None` produces the identical plan).

## What changed (files + why)

| File | Change |
|---|---|
| `pipeline/text_boxes.py` | NEW — detector module + CLI. Per panel: text LINE boxes `[[x,y,w,h],...]` via `cv2.dnn.TextDetectionModel_DB` (DBNet, input 736², mean/scale per opencv_zoo docs). Tall webtoon panels (aspect > 1.4 and long side > 736) are tiled into ~square windows with 10% overlap; tile boxes are offset back to panel coords and duplicate detections merged (intersection ≥ 0.6 of the smaller box). Post-filter drops "lines" taller than 0.25×panel width (stylized SFX lettering, not dialogue). Fallback `detect_classic`: bright(200)/dark(90) container threshold → Otsu ink inside eroded containers → wide-flat-kernel dilation fuses glyphs into lines → density/aspect filters. Results cache to a `<slug>.textboxes.json` sidecar keyed by panel filename + mtime + method (a chapter detected under the fallback re-detects once the model lands). Debug overlays (green boxes + heights + method tag) via `--debug-overlays`. |
| `pipeline/models/text_detection_en_ppocrv3_2023may.onnx` | NEW — vendored model (2.3 MB fp32). See "Model file" below. Loader sha256-verifies before use; mismatch/missing/any load error → classic fallback with a printed warning, never a crash. |
| `pipeline/models/LICENSE-text_detection_ppocr.txt` | Apache-2.0 license copy from the opencv_zoo model directory. |
| `pipeline/panel_render.py` | `--text-aware` flag. When ON: (1) `_ensure_text_boxes` loads/generates the sidecar at render start (any failure → warning + None → feature inert). (2) Seq scenes: `_tcrop` computes each panel's rendered median line height under the blur_bg fit; if < 40 px @1080p (`MIN_TEXT_H_1080`, scaled by frame height) it computes a data-driven crop via `_text_aware_crop`: boxes cluster into reading units (pad-overlap merge), the crop frames the cluster with the most sub-threshold lines + 24 px pad, floored at 55% of the panel's smaller dim; if the floored crop can't reach 40 px it escalates to the bare padded text region (research §3c). Containment guard: the rect grows to absorb any box it partially intersects — a crop can never cut through a bubble; fully-outside boxes may drop only because the framed cluster keeps text visible. Hysteresis: only panels on screen ≥ 1.2 s. Crop applied as `crop=` before the `decrease` scale in `render_panel_scene` (new optional `crop_rect` param; `None` = old filtergraph string byte-identical). (3) Passes `text_boxes` into `plan_layout`. |
| `pipeline/layout_smart.py` | `plan_layout(..., text_boxes=None)` + `_panel_min_h`: with measured boxes, a text-bearing panel's required cell-height fraction becomes `40px·frame_scale · panel_h / (median_line_h · frame_h)` clamped to [GUARD_H, 1.0] — a panel whose text can't reach 40 px in any cell gets req=1.0, the template rejects it, planner returns None → sequential fallback where the crop path takes over (exact ch3 right-column MEDIUM fix). `text_boxes=None` → the adopted binary 55/45 guard, verified identical. |
| `manga.py` | `--text-aware` flag, forwarded to panel_render **only when given** (default invocations byte-identical). |

Untouched: `review_video.py`, `~/faceless-youtube/pipeline/make_video.py`.
No new pip deps; `pipeline/requirements.txt` untouched.

## Model file

- **File**: `pipeline/models/text_detection_en_ppocrv3_2023may.onnx` (fp32, 2,423,490 bytes — research said 4.7 MB; that's the `cn` variant, the `en` model is 2.3 MB)
- **Source**: https://github.com/opencv/opencv_zoo/raw/main/models/text_detection_ppocr/text_detection_en_ppocrv3_2023may.onnx (git-lfs)
- **License**: Apache-2.0 (PaddlePaddle Authors; LICENSE copy vendored alongside)
- **sha256**: `03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587` — matches the upstream git-lfs pointer oid exactly; hardcoded in `text_boxes.MODEL_SHA256` and checked at every load.

## Fallback chain

1. `--text-aware` not given → **nothing runs**; all defaults byte-identical (md5-verified).
2. Flag given, model present + sha OK → DBNet detector (tiled), ~16 s for the 154-panel golden chapter, cached in the sidecar after the first run.
3. Model missing / sha mismatch / load error → `detect_classic` (cv2-only), method recorded per cache entry; re-detects under DBNet automatically when the model appears.
4. Sidecar generation or any per-panel detect throws → printed warning, `tboxes=None`, render proceeds exactly as without the flag (graceful-fallback rule).
5. In `plan_layout`: text known present (OCR) but zero measured boxes → binary conservative guard (never looser than adopted behavior).

## Smoke results

- `text_boxes.py` CLI on 10 golden panels (p0002/18/19/20/21/24/27/28/113/151, incl. scene 5's info-card panel **p0024** and textonly p0028/p0113/p0151): 9/10 with lines, overlays in `samples/overlays/` — boxes land on the bubble/info-card lines, tile offsets correct on the 690×1840 and 690×2000 talls (visually verified).
- Full golden chapter: 154 panels in 16 s, 99 carry text; sidecar written to `output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.textboxes.json`.
- `smoke.py` (this dir): detection + cache-hit, crop math (p0024: median line 14 px → 70 px rendered, crop (220,1316,423,379) contains all 8 boxes, in-bounds), before/after `render_panel_scene` frames, classic fallback finds 9 lines on p0024, layout guard inert with `text_boxes=None` and correctly strict/relaxed with measured boxes. **PASS**.
- Real `render()` with `--text-aware` on a 1-scene copy of scene 5: crops fired on p0022 + p0024; before/after frames in `samples/scene5_{baseline,textaware}_t{2,5,8}.png` — the orange info-card text goes from squint-size to full-frame legible.
- exp-002 `smoke.py`: still **SMOKE PASS** (adopted smart-layout behavior unchanged).
- `panel_render.py --help`, `manga.py --help`: OK.

## Exact evaluator commands

```bash
cd ~/faceless-manga
PROJ=output/the-world-after-the-fall-ch2
SLUG=the-world-after-the-fall-ch2

# 0. (once, already done) sidecar pre-generated; to redo from scratch:
#    rm $PROJ/$SLUG.textboxes.json
#    ./venv/bin/python3 pipeline/text_boxes.py --panels $PROJ/panels \
#        --out $PROJ/$SLUG.textboxes.json

# 1. experiment arm: render-stage-scoped review cut with the flag
./venv/bin/python3 pipeline/panel_render.py $PROJ/$SLUG.json \
    --res 1080p --layout smart --text-aware \
    --music $PROJ/music.mp3 \
    --workdir $PROJ/_work_exp009 --stop-after review-cut

# 2. baseline arm (identical but no --text-aware, fresh workdir)
./venv/bin/python3 pipeline/panel_render.py $PROJ/$SLUG.json \
    --res 1080p --layout smart \
    --music $PROJ/music.mp3 \
    --workdir $PROJ/_work_exp009_base --stop-after review-cut

# 3. review both cuts (path in <workdir>/review_src.txt), diff fault counts:
#    acceptance: phone_readability + unreadable_text 4 -> <=1 on the golden
#    chapter; NO new cropped_content faults; scan the per-scene
#    "text-aware crop on <panel>" log lines against the frames.
```

## v2 iterate

v1 FAILED evaluation (`report.md`: +19 score, vetoes on cropped_content /
watermark / irrelevant_panel). v2 implements the user-approved fix direction:
three guards added to the crop decision, all in `pipeline/panel_render.py`
(text_boxes.py, layout_smart.py, manga.py untouched from v1). All new code
carries `exp-009 v2` markers. **Crops are now REJECTED, never force-fit** —
a rejected panel simply keeps today's full-panel blur_bg framing.

### Fix 1 — containment under MOTION (v1's half-cut-bubble class)

v1 checked box containment against the STATIC crop; the Ken Burns zoompan
then shrank the visible window past the boxes mid-motion (scene 20 t=628.5
bubble+face cut, scene 0 t=26.4). The zoompan is center-anchored, so the
intersection of visible windows across the whole motion path is computable
in closed form: the centered `(cw/zmax, ch/zmax)` sub-rect of the crop.
`_motion_violations` (panel_render.py:604) checks every rect intersecting
the crop against that window (inset `MOTION_JITTER=4`px for per-frame x/y
rounding); `zmax` is the zoom the cut will actually run — `MOTION_ZMAX=1.08`
plain, `MOTION_ZMAX_EMPH=1.16` emphasis first-cut — plumbed from `_tcrop`
(panel_render.py:1327, new `emph_cut` arg).

Containment is checked over the text LINE boxes **plus an estimated FULL
bubble outline per cluster** (`_bubble_extents`, panel_render.py:628):
DBNet boxes cover only the lettering; the drawn bubble extends further, and
v1's scene-20 evidence shows all line boxes contained while the bubble was
sliced. The estimate thresholds bright(>200)/dark(<90) container blobs (the
clean_bubbles.py split) and takes the connected component under the cluster;
estimation failure degrades to line-box-only containment (= v1, never worse).

On violation: one deterministic repair (grow the rect about its center by
zmax so the max-zoom window equals the old static rect, clamp to panel),
re-check, then REJECT if still violated (panel_render.py:857-885).

### Fix 2 — minimum crop size (v1's degenerate 65×62 / 160×160 crops)

Three floors, any failure = reject (panel_render.py:827-841):

| floor | value | why this value |
|---|---|---|
| `MIN_CROP_PX` | 200 px per dim | 200px of a 690px-wide webtoon source is already a 9.6× upscale at 1920×1080; smaller is pixel mush AND an art fragment. Kills v1's 65×62 (p0068) and 160×160 (p0117). |
| `MIN_CROP_AREA_FRAC` | 25% of min(pw,ph)² | Panel-relative, vs the SQUARE of the shorter side (reading-axis width) — the task's "25% of panel area" applied to full area would demand ~the whole panel on 1:3 webtoon strips and kill the scene-5 win (12.6% of full panel area but 135% of width²/4). Both v1 degenerates fail it; all legitimate v1 crops pass. |
| `MIN_TEXT_COVER` | text ≥ 1% of crop area | A punch-in justified by one micro-word (p0032: lone 24×23 box, 0.4% of its 379² crop) frames mostly art/black space — v1's "partial SFX + black space" / irrelevant-fragment class. Real dialogue crops measure 1.2–15% in the v1 log; 1% keeps them all. |

### Fix 3 — watermark-aware (v1's watermark 4→6 veto)

`_load_watermark_panels` (panel_render.py:591) reads the project's
`watermark_log.json` (clean_watermarks.py output); any panel with a recorded
instance (`method != None`) is only croppable when the render is actually
using a cleaned `panels_clean/<name>` copy (checked in `_tcrop`,
panel_render.py:1333-1339 — `_resolve_panel` already prefers panels_clean).
Missing/broken log → `{}` → guard inert (v1 trust level). On the golden
chapter this alone removes crops from p0017/p0040 (scene 0), p0031
(scene 7), p0117 (scene 20), p0066, — 9 recorded panels. Stacking with
exp-003 (panels_clean present) re-enables those crops safely.

### v2 outcome on the v1 crop log (all 21 v1-accepted crops re-evaluated)

- **Win preserved**: p0024 scene-5 info card → same crop (220,1316,423,379);
  p0003, p0043, p0060, p0126, p0031* also still crop (*only via cleaned copy).
- **All 4 evaluator-flagged failure scenes now reject/skip**: scene 0
  (p0017/p0040: watermark skip; containment reject if cleaned), scene 7
  (p0025 containment, p0031 watermark, p0032 text-cover), scene 20 (p0117
  min-size+watermark, p0122 containment — the t=628.5 half-cut bubble),
  degenerates p0068 (min-size) and p0117 (min-size).
- Crop volume drops from 31 accepted panel-instances to ~6 distinct panels —
  the feature now fires only where it can win.

### Smoke (smoke_v2.py — PASS)

- **A. flag OFF byte-identical**: 12 `render_panel_scene` filtergraphs
  (3 panels × plain/emphasis/tight_crop/focus) captured BEFORE the v2 edit
  (`samples/v2/flagoff_vf_v1.json`) match the post-edit capture exactly.
  v1's smoke.py still passes; `panel_render.py --help` OK. layout_smart /
  layout_smart2 (exp-013) hooks untouched.
- **B. scene-5 win**: p0024 crop fires, identical rect, ≥ all floors.
- **C. v1 failure panels reject** with logged reasons (containment-under-
  motion / min-size / text-cover / watermark-skip) — exact output in the
  section above.
- **D. floor sweep** over every panel in the round2-eval sidecar
  (`arm_t.textboxes.json`, both zmax values): 9 accepted crop-evals, all
  ≥ floors and motion-contained; 131 rejected; 8 watermark-skipped. No
  accepted crop below the floor exists.
- **Sample render**: 4-scene mini script (v1-fail scenes 0/7/20 + win
  scene 5) rendered with `--text-aware --layout smart`;
  `samples/v2/_work/all.mp4` + frames `v2_scene0_t26.4.png` (full panel,
  bubble intact), `v2_scene5win_card.png` (info card full-frame legible),
  `v2_scene7_midcut.png`, `v2_scene20_t20.7.png` (bubble + face fully in
  frame — the exact v1 fault frame, fixed). Render log shows the per-panel
  reject reasons.

### v2 evaluator notes

- Same stage-scoped recipe as v1 (commands above) — no new flags, no new
  deps, sidecar reused. Expect far fewer "text-aware crop on" log lines and
  new "crop rejected/skipped" lines explaining each non-crop.
- The `_panel_min_h` smart-layout interaction from v1 is unchanged; the v1
  caveat that some irrelevant_panel faults may come from layout fallbacks
  (scene-3 t=94.3 fragment framing came from the layout path + broken-panel
  substitution, NOT from a crop — no crop fired on that scene) still applies
  and is not addressed by this iterate.
- Watermark guard means arm-vs-baseline diffs on scenes 0/7/20 shrink to
  motion/layout noise; the readability win is concentrated in scene 5 (and
  any ch3 scenes with system boxes).

## v3 iterate

v2 passed scoped (22→20, PR 2→0, all v1 regressions fixed) but FAILED global
on vetoes the round3 evaluator root-caused to the smart-layout `_panel_min_h`
interaction, NOT the crop path: feeding measured boxes to the planner made
the size-aware `_panel_min_h` reject grid plans the binary guard accepts
(baseline plans grids on 0-based scenes 1/4/9/18/24; the v2 arm kept only
18). The lost scene-1 grid magnified a corner watermark (watermark 4→5 veto)
and a lost composite left a solo pendant panel as empty_screen (1→2 veto).

**v3 deciding change (user-approved): the smart-layout PLANNER always sees
the adopted binary guard; measured boxes drive only crop decisions.**

### What changed

- `pipeline/panel_render.py` (~:1288-1314, `exp-009 v3` comment): both
  planner calls — `ls.plan_layout(...)` and `ls2.plan_layout_v2(...)` — now
  pass `text_boxes=None` unconditionally. Layout plans are therefore
  byte-identical with `--text-aware` on or off (the argument is literally
  the same `None` the flag-off path passes). `tboxes` still feeds `_tcrop`
  (the crop/punch-in path), which is unchanged from v2.
- `pipeline/layout_smart.py` `_panel_min_h` docstring notes the v3 split;
  the size-aware branch itself is NOT deleted — it remains for explicit
  opt-in callers (layout_smart2's own CLI `--text-boxes` flag) and stays
  inert with `None` (the adopted behavior, still verified by v1 smoke §5
  and exp-002's smoke).

### Smoke (smoke_v3.py — PASS; smoke.py, smoke_v2.py, exp-002 smoke also re-run PASS)

- **A** flag OFF byte-identical: same 12-filtergraph comparison as v2, identical.
- **B** source split asserted: every `plan_layout*` call in panel_render
  passes `text_boxes=None`; crop path still reads `tboxes`.
- **C** plan set restored: replaying the golden chapter's planning inputs
  (arm_t.json + baseline cached word timings/durations), the binary guard
  plans grids on scenes **{1, 4, 9, 18, 24}** — v2's size-aware set was
  **{18}** only. ON == OFF by construction.
- **D** crop decisions unchanged from v2: 12 spot-checks (4 crops incl. the
  scene-5 win, 4 watermark-skips, 4 rejects with the same reasons) match
  exactly; full-sidecar floor sweep 9 accepted / 131 rejected / 8
  watermark-skipped == v2's counts.

### v3 evaluator notes

- Same stage-scoped recipe; expect the arm's "smart layout" log lines to
  now match the baseline's exactly (5 grids), while crop accept/reject
  lines stay as in v2.
- v2's caveat about `_panel_min_h` layout fallbacks contaminating
  irrelevant_panel counts is resolved — layout differences between arms
  are now zero by construction; any remaining fault delta is attributable
  to the crops alone.

## v4 iterate

v3 FAILED on a noise-floor veto whose one genuine component was the p0126
crop (scene 21, irrelevant_panel 2/3 scoped): p0126's median line already
rendered 36px under DEFAULT framing — legible, just below the 40px comfort
target — so the crop traded framing quality for a marginal gain. v4
(user-approved deciding change): **a readability TRIGGER gate** — crop only
when the text is genuinely unreadable without it.

### What changed (`exp-009 v4` markers, pipeline/panel_render.py only)

- New constant `TEXT_TRIGGER_H_1080 = 30` (panel_render.py:~553) — the
  TRIGGER threshold, split from the 40px TARGET (`MIN_TEXT_H_1080`, which
  still sizes the crop once triggered).
- `_text_aware_crop` trigger check (panel_render.py:~770-782): the median
  rendered line height under the default blur_bg fit
  (`box_h * min(w/pw, h/ph)`) must fall below `trig_px` for anything to
  happen; the needy-cluster selection uses the same trigger threshold.
  Panels at/above it return None silently (no reject log — nothing was
  wrong).

### Threshold justification (30px @1080p)

Research §2's sizing sources (BBC subtitles, Material 12sp, physical mm at
arm's length, broadcast minima) put COMFORTABLE at 40-59px; ~0.75× the
band's lower edge (≈30px ≈ 1.9mm cap height on a 6" phone) is the
strained-reading floor where users stop being able to read without effort.
Golden-chapter separation check: the reviewer-confirmed-unreadable info
card (p0024) renders 14.7px median; the reviewer-confirmed-fine crops
p0126/p0060/p0003 render 34-38px. 30 splits every confirmed-bad from every
confirmed-fine instance with margin on both sides (nearest neighbors:
p0043 at 20px triggers, p0128 at 27px triggers-but-rejects on containment,
p0025 at 31px doesn't trigger).

### New accepted-crop set (golden ch2, arm_t.textboxes.json)

**{p0024, p0043}** — subset of v3's {p0003, p0024, p0043, p0060, p0126}:

| panel | median @default | v3 | v4 |
|---|---|---|---|
| p0024 (scene-5 win) | 14.7px | crop | **crop (same rect)** |
| p0043 | 20.0px | crop | **crop (same rect)** |
| p0003 | 34.0px | crop | no-trigger |
| p0126 (the round4 flag) | 36.1px | crop | **no-trigger** |
| p0060 | 37.6px | crop | no-trigger |

Full sweep (both zmax values): 4 accepted crop-evals / 136 no-crop /
8 watermark-skipped (was 9/131/8).

### Smoke

- smoke_v3.py updated (spot-check table + sweep counts) — PASS: flag-off
  filtergraphs identical, planner split intact (plans {1,4,9,18,24}),
  p0024/p0043 crops byte-identical to v2/v3, p0126/p0003/p0060/p0025
  no-trigger, p0032/p0068/p0122 rejects unchanged.
- smoke_v2.py updated (v1-failure panels may now be silent no-trigger
  instead of logged reject — stricter, still counts) — PASS.
- v1 smoke.py — PASS unchanged (p0024 at 14.7px still triggers).
- flagoff_vf re-capture — byte-identical.

### v4 evaluator notes

- Crops now fire on 2 panels of the golden chapter (scenes 5 and 10 by the
  v1 log numbering). Scene-21/p0126 renders identically to baseline.
- The trigger/target split means ch3's genuinely-tiny system boxes (the
  gap's origin) still crop toward 40px; only already-legible text is left
  alone.

## Deviations from the research plan

- Module named `text_boxes.py` (task spec) not `text_metrics.py` (research §6).
- Flag named `--text-aware` (task spec) not `--text-aware-framing` (research §6).
- Model is 2.3 MB not 4.7 MB (research quoted the cn variant's size; en fp32 is smaller). fp32 used as planned.
- Textonly §3(c) is implemented as the escalation rung of the same crop (bare padded text-region union when the 55%-floored crop can't reach 40 px) rather than a separate PIL pre-crop temp file — same effect, one code path, no temp panels.
- Crop targets the densest sub-threshold text CLUSTER rather than the union of all boxes: on tall webtoons the all-box union spans the whole panel (boxes at top and bottom), which would make every crop degenerate. Boxes fully outside the crop may drop only when the framed cluster keeps text visible (matches the task's containment rule).
- The smart-grid guard uses required-height fractions (research §3b formula) via a new `_panel_min_h`; `GUARD_H` stays the floor so text-free panels behave exactly as adopted.
