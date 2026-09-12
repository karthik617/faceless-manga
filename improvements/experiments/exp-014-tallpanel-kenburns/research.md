# exp-014 research — tall-panel Ken Burns framing (gap-014, merged with gap-006)

Date: 2026-09-11. Researcher notes only — nothing installed, no pipeline code
touched.

## Problem restated

`panel_render.py` renders every panel with a center-anchored zoompan
(`render_panel_scene`, x/y locked to `iw/2-(iw/zoom/2)` at :1026; comments at
:564/:622/:868 and the parallax path :1093). On extreme-aspect merged webtoon
panels (h/w > 2.5, e.g. p0015 @3122px, p0041 @2845px) the visible window
sweeps past faces/bubbles → the residual `cropped_content` class on golden
ch2 scenes 15/21/28 and ch4 scenes 4/12/14/22. The fix must pick
content-aware start/end rects (or a vertical scroll) whose union always
contains the panel's must-see boxes, with the card's hard guards: no sideways
pans, center-anchor fallback, byte-identical output with the flag off,
wall-time ≤1.2x.

## Facts verified against this repo/venv (2026-09-11)

- venv `cv2` = **opencv-python-headless 5.0.0.93** (`cv2/version.py`:
  `contrib = False`, `headless = True`).
  - **`cv2.saliency` is ABSENT** — saliency is an opencv_contrib module and
    this is a main-modules headless build. (The known packaging gotcha from
    the gap card is confirmed: spectral residual via `cv2.saliency` is not
    available here.)
  - **`CascadeClassifier` is ABSENT from the OpenCV 5 stubs entirely**
    (grep of `cv2/__init__.pyi`: no `Cascade`, no `detectMultiScale`;
    `cv2/data/` ships no cascade XMLs). OpenCV 5.0 removed the legacy
    Haar/LBP cascade API from the main modules — `lbpcascade_animeface`
    cannot run in this venv at all, and swapping to
    `opencv-contrib-python-headless` is already ruled out repo-wide
    (yolo_detect.py header: the contrib `cv2` package collides with our
    headless install; that collision is why dghs-imgutils was vendored).
  - `cv2.FaceDetectorYN` (YuNet) IS present — but it is a *human* face
    detector; known-poor on anime/manga art. Not usable as the primary.
- `pipeline/yolo_detect.py` already runs **deepghs/manga109_yolo**
  (`v2021.12.30_n_yv11`, YOLO11n, ~10 MB ONNX, onnxruntime CPU) with
  windowed inference over tall regions. The checkpoint's classes are
  **`body`, `face`, `frame`, `text`** (HF model card, confirmed 2026-09-11;
  metadata `names` is read at `_get_session`). `_detect_one` **drops
  everything but `label == "text"`** at yolo_detect.py:203 — face/body boxes
  are already computed and thrown away. Weights license: embedded
  Ultralytics **AGPL-3.0** — already recorded/accepted in README + ledger
  for the split guard; reusing more classes of the same weights adds no new
  license exposure.
- exp-009 machinery reusable as-is:
  - `text_boxes.py` → per-panel OCR line boxes sidecar (DBNet, Apache-2.0
    model, cached `.textboxes.json`), loaded via `_ensure_text_boxes`
    (panel_render.py:912).
  - `_cluster_boxes` (:697), `_bubble_extents` (:643) — full-bubble outline
    estimation, exactly the "don't cut the drawn bubble" rects gap-014 needs.
  - `_motion_violations` (:619) — the containment-under-motion check the
    card wants extended from crop windows to Ken Burns start/end rects. For
    a center-anchored zoom the guaranteed-visible region is the centered
    `(w/zmax, h/zmax)` rect; for a box-aware move it generalizes to the
    **intersection of the start and end windows** (both must contain every
    must-see box, minus MOTION_JITTER).
- Render motion today: `render_panel_scene` (:989) — z expressions at
  :1017-1025 (`1.08` slow zoom, `1.16` emphasis punch), x/y center-locked at
  :1026; ffmpeg zoompan on a 2x supersampled composite (:1044-1072).
  Integration point for any change is exactly this function plus the
  `_text_aware_crop` call site at :1374.

## Candidate matrix

| # | Candidate | License | New deps | CPU cost/panel | Fit (1-5) | Risk |
|---|-----------|---------|----------|----------------|-----------|------|
| 1 | **Reuse vendored manga109_yolo `face`/`body` classes + deterministic box-union framing + vertical scroll for h/w>2.5** | AGPL weights (already accepted), MIT glue | **zero** | ~0.3-1 s (windowed 640px, already-paid model load) | **5** | manga109 trained on B&W manga; face recall on colored webtoons unproven (the 2023 text checkpoints returned zero on colored bubbles — must smoke-test the 2021 face class on ch2/ch4 panels first) |
| 2 | deepghs/anime_face_detection (`face_detect_v1.4_s`, ONNX) via the same vendored glue | **MIT** (HF card) | zero pip deps; +40 MB model download | ~0.2-0.5 s | 4 | trained on anime illustrations (colored) — likely better webtoon recall than #1; output-format parity with our vendored nms postprocess needs a smoke test; second model in HF cache |
| 3 | Pure OCR-text-box framing (no face detector at all) + vertical scroll | n/a (existing) | zero | ~0 (sidecar cached) | 4 | faces without dialogue nearby can still be grazed (ch4 scene 22 "forehead cropped" has no bubble at the cut) — covers most but not all sites |
| 4 | Self-implemented spectral-residual saliency (≈15 lines numpy FFT + cv2 blur) | ours | zero | ~50 ms | 3 | saliency on manga art is noisy (screentones/SFX light up); use only as anchor-picker fallback, never as a containment source |
| 5 | hysts/anime-face-detector | MIT (code) | **PyTorch ≥2.2** (~800 MB, CPU wheels) | 1-4 s | 2 | dep budget blown for a CPU box; overkill (28 landmarks unneeded) |
| 6 | Fuyucch1/yolov8_animeface | **AGPL-3.0** + needs `ultralytics`+torch | heavy | ~1-2 s (x6 model!) | 1 | AGPL *new* exposure + torch dep + 1280px x6 model → reject |
| 7 | nagadomi/lbpcascade_animeface | **repo has NO LICENSE file** (README only) | needs `CascadeClassifier` | n/a | 0 | **API removed in OpenCV 5 main modules — cannot run in this venv.** Reject (also unlicensed) |
| 8 | cv2.saliency spectral residual (contrib) | Apache-2.0 | requires contrib build | n/a | 0 | **not in headless main build (verified)**; contrib package collides with our cv2 → reject, see #4 for the DIY equivalent |
| 9 | Google MediaPipe AutoFlip | Apache-2.0 | C++ graph, MediaPipe legacy | n/a | 1 | prior art only — its *strategy* (detect required regions → choose a camera path that covers them → prefer static/single-axis moves) is exactly the algorithm we should hand-roll; the code itself is not reusable from Python |
| 10 | cv2.FaceDetectorYN (YuNet, in venv) | Apache-2.0 | zero | ~50 ms | 1 | human-face model; anime recall is famously poor — note only |

Prior art on vertical scroll (goal 4): webtoon recap channels and ffmpeg
recipes use a constant-velocity crop scroll — `scale` to frame width, then
`crop=w:h:0:'min(ih-oh, (ih-oh)*t/D)'` (or the equivalent y expression inside
zoompan with `z=1`). Known pitfalls, with mitigations we already own:
- **Judder**: `crop` floors y to integer pixels; at 30fps a 3000px panel over
  8 s scrolls ~12 px/frame at source scale — visible stepping on fine line
  art. Mitigation: do the scroll on the existing **2x supersampled**
  composite (ss_w/ss_h pattern at :1011) so steps are effectively half-pixel
  after downscale, same trick layout_smart uses for smooth zoompan.
- **Scroll speed vs narration**: distance is fixed (panel height − window),
  duration is the narration beat, so speed = dist/dur is uncontrolled. Cap
  effective speed (~≤150 src-px/s); when the cap would truncate the sweep,
  scroll only between the box-union's top and bottom (not the full panel),
  and when even that is too fast, fall back to a static box-union fit.
  Ease-in/out (smoothstep on t/D) reads better than linear and masks stepping.
- **Never exclude a text box mid-beat** (card guard): with word timings from
  the existing edge-tts/whisper alignment, assert per narration beat that the
  window `[y(t), y(t)+oh]` contains every text box quoted in that beat; a
  violation reduces scroll range or forces the static fallback. This is the
  natural extension of `_motion_violations` from a static max-zoom window to
  a time-parameterized window — deterministic and scriptable for the gate.

## Recommendation

### #1 (recommended): zero-new-deps box-union framing — manga109 face/body + OCR boxes + vertical scroll

Everything needed already ships in the repo. Three pieces:

1. **Expose face/body boxes from the vendored detector.**
   `yolo_detect._detect_one` already sees all four classes and filters to
   `text` (:203). Add a `labels=("text",)` parameter and return
   `{label: [boxes]}` (or a parallel `detect_boxes(labels=...)` entry point)
   — no new model, no new dep, model load already amortized per process.
   Cache into the existing `.textboxes.json` sidecar pattern (new
   `.contentboxes.json` sidecar keyed by panel+mtime, same as text_boxes).

2. **Box-aware start/end rect selection** (normal-ish panels, h/w ≤ 2.5, and
   tall panels where the box-union fits one window):
   - must-include set = OCR text-box clusters (+ `_bubble_extents` outlines)
     ∪ detected `face` boxes (+ `body` boxes at lower priority, droppable
     when they don't fit);
   - compute the padded union rect; derive start/end zoom windows *both*
     containing the union (AutoFlip's "required region" rule). Since the
     no-sideways-pan guard forbids x-motion, the start/end windows share a
     center-x and may differ only in zoom and (for tall panels) center-y;
   - verify with the generalized `_motion_violations`: every must-include
     box ⊂ (start window ∩ end window inset by MOTION_JITTER) — reject →
     center-anchor fallback (guard: never worse than today);
   - implementation is a pure function `(pw, ph, boxes) -> (z0, z1, cy0,
     cy1) | None` feeding new zoompan `y` expressions at :1026 — z stays in
     today's 1.0–1.08/1.16 envelope so motion character is unchanged.

3. **Vertical top→bottom scroll for h/w > 2.5** when the union cannot fit a
   single window: webtoon-native reading motion, the card's sanctioned pan.
   Constant-velocity (smoothstep-eased) y-scroll on the supersampled
   composite from union-top to union-bottom; speed cap + per-beat text-box
   containment as above; static box-union fit as the inner fallback.

   Flag: `--content-aware` (or extend `--text-aware`), default off →
   byte-identical output (guard). Detector failure → `None` → center anchor
   (same graceful-fallback rule as `_ensure_text_boxes`).

**Why #1**: zero install, zero new license exposure, reuses four
battle-tested exp-009 components (sidecar cache, clustering, bubble extents,
motion containment), directly satisfies the card's NEW deterministic gate
(extend `_motion_violations` to start/end rects + scroll path), and the
wall-time budget is trivially met (detection ~0.3-1 s/panel, cached; render
cost unchanged — the ffmpeg graph gains only a y expression).

**Known unknown to burn down FIRST** (30-min smoke): manga109 `face` recall
on *colored* webtoon panels. exp-004 found the 2023 checkpoints return zero
*text* detections on colored bubbles; the 2021 nano's text class works, but
its face class is untested on our sources. Smoke-test on the 7 known fault
panels (ch2 p0015/p0041/p0101, ch4 scene-4/12/14/22 sources) before building.
If recall is bad → swap the face source to candidate #2 (one string change:
`repo="deepghs/anime_face_detection", model="face_detect_v1.4_s"` through the
same `_get_session`/`_predict` path — it takes repo/model params already).
Even with ZERO usable face boxes, option #3 (OCR-only union + vertical
scroll) fixes the bubble-cut sites (scenes 15/21/28 evidence is
bubble-grazing), likely enough for the binding 3→0 criterion; the ch4
scene-22 forehead crop is the one site that genuinely needs a face box.

### Runner-up: deepghs/anime_face_detection (MIT) through the same vendored glue

Same integration, +1 model download (~40 MB HF cache), MIT-licensed weights
(cleaner than manga109's AGPL note), trained on colored anime art → better
expected webtoon recall. Choose it if the manga109 face smoke fails. It is
literally used by a `kakaoent/webtoon_cropper` HF space — direct prior art
for this exact task.

### Build-ourselves fallback (no detector at all)

Deterministic OCR-union framing + vertical scroll (candidate #3), plus the
15-line DIY spectral-residual saliency (candidate #4) as an *anchor picker
only* (choose which end of a too-tall panel the static window prefers), never
as a containment source. Existing deps only: cv2 (FFT/blur), numpy,
text_boxes sidecar, ffmpeg. This is also the graceful-degradation path the
adopted feature keeps when detectors fail at runtime.

## Install + smoke-test commands (top 2)

**#1 — no install.** Smoke (run from repo root, venv python):
```bash
# 1) verify the vendored model emits face/body on a colored webtoon panel
./venv/bin/python3 - <<'EOF'
import sys; sys.path.insert(0, "pipeline")
import yolo_detect as yd
from PIL import Image
img = Image.open("output/the-world-after-the-fall-ch4/panels/p0041.png").convert("RGB")
dets = yd._predict(img.crop((0, 0, img.width, min(1280, img.height))), conf=0.15)
print([(l, c) for (_b, l, c) in dets])   # expect ('face', ...) / ('body', ...) entries
EOF
```
(3 lines of substance: import vendored module, run `_predict` on a known
fault panel, print labels — nonzero `face` detections = green light.)

**#2 — no pip install; one model download** (auto via hf_hub on first use):
```bash
./venv/bin/python3 - <<'EOF'
import sys; sys.path.insert(0, "pipeline")
import yolo_detect as yd
from PIL import Image
img = Image.open("output/the-world-after-the-fall-ch4/panels/p0041.png").convert("RGB")
print(yd._predict(img, conf=0.25, repo="deepghs/anime_face_detection",
                  model="face_detect_v1.4_s"))
EOF
```
Watch for: output-tensor name/format mismatch (our vendored postprocess
assumes the nms-based `output0` layout — if this model differs, the fix is
confined to `_predict`).

## Integration sketch (file:line)

- `pipeline/yolo_detect.py:203` — stop discarding non-text labels behind a
  parameter; add `detect_content_boxes()` returning per-label boxes.
- `pipeline/panel_render.py:912` (`_ensure_text_boxes`) — sibling
  `_ensure_content_boxes` with the same sidecar/mtime/fallback-to-None
  pattern.
- `pipeline/panel_render.py:989` (`render_panel_scene`) — new optional
  `frame_plan` arg: `None` = today's path (byte-identical); else carries
  `(z0, z1, cy0, cy1)` or `scroll=(y_top, y_bottom)`; only the z/x/y
  expression strings at :1017-1026 and the fg chain at :1050 change.
- `pipeline/panel_render.py:1374` (crop call site) — compute the frame plan
  next to `_text_aware_crop`, sharing its boxes/clusters/bubble extents.
- Gate script (new, in exp dir): for every h/w>2.5 panel, simulate the
  window `[y(t), y(t)+oh]` per narration-beat timestamp and assert 100%
  text-box containment — the card's new deterministic check, built on
  `_motion_violations` math.

## Risks → quality guards mapping

| Risk | Guard it threatens | Mitigation |
|---|---|---|
| manga109 face class blind on colored webtoons | acceptance 3→0 on scenes 15/21/28 | smoke FIRST; swap to MIT anime_face_detection (#2); OCR-only union already covers the bubble sites |
| false-positive face/body boxes inflate the union → zoom-out to whole panel | "no new cropped_content on normal panels" / feels like no motion | conf floor (≥0.35 for face), drop `body` boxes first when union > window, reject→center fallback |
| scroll speed uncontrolled by narration length | new judder/motion-sickness faults | speed cap + ease + static-fit fallback; scroll on 2x supersample |
| crop-y integer stepping at 30fps | perceived quality | supersampled composite (existing ss pattern) |
| detector wall time on tall panels | ≤1.2x render bound | sidecar cache (pay once/chapter, like textboxes); windowed 640px inference ~0.3-1 s/panel, only for scene panels |
| flag-off regression | byte-identical guard | `frame_plan=None` short-circuits before any expression change; diff a flag-off render in the gate |
| AGPL optics of reusing manga109 weights more broadly | legal | no NEW exposure (same weights, already ledgered); runner-up is MIT if the ledger owner prefers to retire the AGPL note |
