# exp-009 research — phone readability: text-size-aware framing

Gap: `improvements/gaps/gap-009-phone-readability.md` (priority 9).
Evidence: **13 phone_readability faults in ch3** (#1 surviving type), 1 in ch2,
plus 1-3 unreadable_text per chapter. Two fault populations:
1. **Seq scenes** — small system/info boxes fitted whole via blur_bg
   (`panel_render.py:631-653`); the fit ratio shrinks a tall panel's text far
   below legibility and the renderer has *no idea* text exists.
2. **Smart-grid scenes** — 3 MEDIUM faults on ch3 smart scenes: right-column
   text cells. `layout_smart.py:44` GUARD_H_TEXT=0.55 uses **binary** OCR text
   presence (`_panel_has_text`, line 186), not text *size* — a panel whose
   in-panel text is already tiny at native scale passes the 55% guard and
   still renders unreadable.

Root cause in one line: **we know WHERE panels have text (OCR reads,
`script_from_panels.py:141-169`) but not the pixel HEIGHT of that text, so no
guard can be size-aware.** The OCR sidecar (`<slug>.ocr.json`) has dialogue
strings but NO bounding boxes. This gap needs detection-only text boxes
(heights) — transcription is already covered.

---

## 1. Candidate matrix — text detection with box heights

| # | Candidate | License | Install cost | Model size | CPU latency/panel | py3.12 | Manga fit | Fit (1-5) | Risk |
|---|-----------|---------|--------------|-----------|-------------------|--------|-----------|-----------|------|
| A | **PP-OCRv3 DBNet det ONNX via `cv2.dnn`** (opencv_zoo) | **Apache-2.0** (model dir has own LICENSE, Apache-2.0) | **zero new pip deps** — model file download only (4.7 MB fp32 / 2.3 MB int8) | 2.3–4.7 MB | ~150–400 ms @ 736px input | yes (pure cv2, already have opencv-python-headless>=4.8; `TextDetectionModel_DB` API exists since OpenCV 4.5) | good on latin scanlation lettering; trained on scene/doc text, not manga — SFX/stylized recall lower (fine: we care about dialogue/info-box text) | **5** | low-med: possible missed rotated/stylized SFX (acceptable — SFX aren't the fault source); int8 variant "may be unstable" per README → use fp32 |
| B | **RapidOCR** (`rapidocr` pkg, det-only mode) | Apache-2.0 | `pip install rapidocr onnxruntime` (~60 MB onnxruntime wheel + 15 MB models bundled) | ~5 MB det model | ~100–300 ms | yes (rapidocr 3.x supports 3.12; legacy `rapidocr-onnxruntime` 1.4.4 pins <3.13) | same DBNet family as A, nicer Python API, active maintenance (RapidAI, releases through 2025) | 4 | adds onnxruntime to a venv that doesn't have it; duplicate capability vs A |
| C | **comic-text-detector** (dmMaze) | **GPL-3.0 — FLAGGED, do not shortlist** | git clone + model download from manga-image-translator releases (also GPL repo) | ~50 MB (yolov5-based blk det + DBNet lines + Unet mask) | ~1–3 s CPU | requires torch or onnx export work | **best-in-class for manga**: trained on Manga109 + comics, returns block boxes, text LINES with heights, and pixel masks; 372★, last meaningful commit ~2022, 6 open issues, no releases/packaging | 2 (license-capped) | GPL contaminates the pipeline if imported; effectively unmaintained; heavy |
| D | **PaddleOCR det-only** (`paddleocr` + `paddlepaddle`) | Apache-2.0 | `pip install paddleocr paddlepaddle` — paddlepaddle CPU wheel is ~600 MB+, heavyweight framework | det model ~4.7 MB but framework huge | ~100–300 ms after slow init | yes (3.8–3.13 per PyPI, v3.7.0 Jun 2026, very active) | same detector as A but with the whole Paddle framework attached | 2 | massive dep for a 4.7 MB model we can run in cv2 (candidate A **is** this model exported to ONNX) |
| E | **EAST via `cv2.dnn`** (frozen_east_text_detection.pb) | GPL-3.0 (argman repo; weight provenance unclear) | 96 MB model file | 96 MB | ~500 ms–1.5 s @ 1280×720 | yes (cv2 only) | trained on ICDAR scene text (photos); known weak on dense document/comic text, no curved text; 2017-era, repo unmaintained | 1 | license + size + worst accuracy of the neural options |
| F | **cv2 classic (bubble-first threshold + morphology)** — build-ourselves | n/a (our code, existing deps) | zero | zero | ~20–50 ms | yes | high-contrast black-on-white bubble/box text is the *easy* case for classic CV; screentones/SFX are the hard case | 3 | precision on screentone-heavy panels; see §5 for algorithm + failure modes |
| G | **Gateway vision call returning bboxes** (extend the existing `gateway.llm_vision` OCR read to ask for normalized boxes) | n/a (existing infra) | zero | zero | ~1–3 s/panel batch (already paid — piggyback on the STAGE-3 read) | yes | see §4 — Claude-family VLMs are **not** grounding-trained; coordinates unreliable | 2 as a *measurer*, 3 as a coarse tie-breaker | ±20-40% coordinate error makes height thresholds meaningless; silent hallucinated boxes |

**Recommended: A — PP-OCRv3 DBNet det ONNX through `cv2.dnn`** (opencv_zoo
`models/text_detection_ppocr`, Apache-2.0). It is literally candidate D's
detector without the framework: zero new Python dependencies (we already ship
`opencv-python-headless>=4.8`), a 4.7 MB one-time model download vendored into
the repo (license permits), ~0.2-0.4 s per panel on CPU, and it returns
**rotated quads per text LINE** — line height is exactly the number the
decision rule needs. English scanlation lettering (clean latin type in white
bubbles/boxes) is close to the model's document-text training distribution;
the manga-specific weaknesses (hand-lettered SFX, vertical JP text) don't
matter because SFX aren't what the reviewer flags.

**Runner-up: F — cv2-classic detector** (zero-anything; §5). If A's recall on
the golden chapter's info boxes disappoints, F is the fallback, and F's
bubble-mask stage is worth building *anyway* as A's post-filter (drop
detections outside bright regions → kills screentone false positives).

Rejected: C on license (GPL — only reconsider if both A and F fail benchmark,
per protocol), D on install weight, E on license+accuracy, B as redundant
with A at higher install cost.

---

## 2. Decision rule — minimum readable text height at 1080p on a phone

The gap card guesses ~28 px. **Verified: 28 px is the absolute floor, not
comfortable.** Anchors:

- **BBC Subtitle Guidelines §9 (Typography/Size)** — subtitles are authored so
  a text line occupies ~**8% of active video height** ≈ **86 px line height at
  1080p**. That's the glanceable-while-watching standard (upper anchor; manga
  text gets full attention, so it can go smaller).
- **Material Design minimums mapped to video pixels**: a 1080p landscape video
  full-width on a ~411 dp-wide phone (Pixel-class) renders at ~4.7 video-px
  per dp. Material's 12 sp minimum body text ⇒ **~56 px** at 1080p; 10 sp
  caption floor ⇒ ~47 px.
- **Physical check**: on a 6.1" 19.5:9 phone the video is ~2.56" wide, i.e.
  ~750 effective ppi. Typographic comfort at 30–35 cm needs ≥ ~2 mm glyph
  height ⇒ 2 mm / (2.56"/1920 px) ≈ **59 px**; the strain floor (~1 mm) ≈ 30 px.
- **Broadcast rule-of-thumb** (safe-title era): minimum character height
  1/25–1/32 of picture height ⇒ 34–43 px at 1080 — and that assumed TV
  viewing distance, roughly comparable angular size to a phone at arm's length.

All four anchors converge on the same band. **Proposed constants** (DBNet
boxes are per-LINE, so detected box height ≈ line height, compare directly):

```
MIN_TEXT_H  = 40   # px at 1080p — trigger: median rendered line height below
                   # this ⇒ take a readability action (~3.7% frame height)
TARGET_TEXT_H = 50 # px — what the action should achieve (comfort band 47-59)
FLOOR_TEXT_H = 28  # px — below this even briefly = fault-certain (gap card's
                   # number lands here: it's the floor, not the target)
```

Use median (not min) of detected line heights per panel so one tiny
footnote-style line doesn't force a punch-in; but if the OCR read marks the
panel `textonly` or the narration quotes it, use the min of the quoted
region's lines.

**Critical sizing fact for the response design:** the existing emphasis
punch-in (`panel_render.py:605-607`) peaks at **1.16×** and settles at 1.02×
— a 20 px line becomes 23 px. **Zoom alone cannot close this gap.** The fix
must be *framing* (crop the fit window to the text-bearing region → 1.5-3×
effective scale), with the punch-in machinery reused only as the transition.

---

## 3. Response actions in our renderer

### (a) Seq scenes — text-aware tight_crop (primary)

Hook: `panel_render.py` scene loop (~line 762-767) + `render_panel_scene`
(583). Today `tight_crop` (638-641) is a *fixed* 0.9 center crop. Replace
with a computed crop when text would render small:

1. Load text boxes from the new sidecar (see §6). Compute blur_bg fit ratio
   `r = min(w/panel_w, h/panel_h)` (the `force_original_aspect_ratio=decrease`
   at line 643). Rendered median line height = `median(box_h) * r`.
2. If `< MIN_TEXT_H`: compute the union rect of text boxes (merge boxes whose
   expanded rects overlap), **pad by ≥24 panel-px beyond every box edge**
   (quality guard: never half-cut a bubble — the padded window must fully
   contain every detected box it intersects, else grow it), clamp the crop so
   the window still covers ≥55% of the panel's smaller dimension
   (cropped_content guard from acceptance criteria).
3. Feed that window into the existing filtergraph as a crop before the
   `decrease` scale — same shape as the current tight_crop branch, just
   data-driven coordinates instead of `0.9/0.35` constants.
4. If even a maximal legal crop can't reach `MIN_TEXT_H` (dense tiny text):
   escalate to (c).

### (b) Smart-grid — feed heights into the guard (replaces binary has-text)

Hook: `layout_smart.plan_layout` (259) / `min_hs` (308). Replace
`GUARD_H_TEXT if _panel_has_text(...)` with a per-panel **required cell
height fraction**:

```
req_h = MIN_TEXT_H / (median_box_h * frame_h / panel_h)   # fraction of frame
min_hs[i] = clamp(req_h, GUARD_H, 1.0)
```

A panel whose text needs >100% of frame height can never sit in a grid cell →
planner drops it from the composite (it still appears via the guided-view
punch plates at PUNCH_FILL=0.94, `layout_smart.py:95`, which is near-native)
or returns None → sequential fallback. This directly fixes the ch3
"right-column text cells" MEDIUM faults: those panels either get a taller
cell, get excluded from the grid, or the whole scene falls back — all
existing, tested code paths. No detector = keep current binary behavior
(zero regression).

### (c) Textonly panels — full-frame the text region

Hook: same scene loop; OCR reads already tag `kind == "textonly"`
(`script_from_panels.py:165`, `verify_panels.py`). For textonly panels (and
(a)-escalations): crop to the padded text-region union and fit THAT as the
foreground over the blur_bg plate — i.e. the text region becomes the panel.
Reuse `render_panel_scene` unchanged by pre-cropping the PNG with PIL before
render (simplest: write a `p0001.textcrop.png` temp, pass it as `panel`).

### Prior art on auto-framing comic text in video

- **Comixology "Guided View"** — the canonical panel-to-panel auto-framing
  convention (patented UX, concept not code); layout_smart's punch-ins
  already imitate it. Its relevant lesson: frame to the *reading unit*
  (bubble/box), not the panel.
- **manga-image-translator / BallonsTranslator** (both GPL) — detect text
  blocks to *replace* them; their block-merge heuristics (merge line boxes
  into blocks by proximity, pad to bubble) are the same union-rect logic as
  (a) step 2. Reimplement independently, trivial geometry.
- Motion-comic makers (Madefire-era authoring tools) key camera moves to
  balloon rects by hand — no OSS auto-framer found. Our (a)+(c) is a small
  novel-but-obvious composition of existing pieces; no library to adopt.

---

## 4. Zero-new-deps option: gateway vision bboxes — researched, rejected as measurer

Claude-family models (our Athena gateway) are **not grounding-trained**:
Anthropic documents no bbox output format (contrast Gemini's `box_2d`
normalized-0-1000 contract and Qwen2-VL's grounding tokens, both explicitly
trained/documented for localization). Community/benchmark evidence is
consistent: GPT-4V/Claude-class VLMs show large, *inconsistent* localization
error when asked for pixel or normalized coordinates (commonly ±10-25% of
image dimension per edge, worse on tall images that get internally resized —
and our webtoon panels are extremely tall, so the model sees a downscaled
tile grid it can't map back precisely). A ±20% height error on a 30 px
threshold decision is a coin flip; hallucinated boxes fail silently.

Verdict: **do not use VLM boxes to measure height.** Acceptable secondary
uses: (i) keep the existing binary text-presence (already have), (ii) a cheap
tie-breaker "is the text in the top/middle/bottom third?" when the detector
returns nothing but OCR says text exists (coarse thirds are within VLM
accuracy). The detector (A or F) is the measurer.

---

## 5. MANDATORY build-ourselves fallback — cv2-only text-region detector

Exploits the domain: scanlation dialogue is near-black type on near-white
bubbles/boxes. Two-stage, reusing thresholds already proven in this repo
(`clean_bubbles.py:41-46` uses the same 200/90 split; `segment_panels.py:53`
uses Otsu):

```
Stage 1 — bubble/box candidate mask (bright containers):
  gray = cv2.cvtColor(panel); blur 3x3
  _, bright = cv2.threshold(gray, 200, 255, THRESH_BINARY)
  close (7x7) then open (5x5) to solidify bubble interiors
  connected components: keep blobs with area >= 0.2% of panel,
  fill_ratio (area/bbox) >= 0.45, bbox aspect 0.2..8 -> "containers"

Stage 2 — text lines inside each container:
  roi = gray[container]; _, ink = cv2.threshold(roi, 0, 255,
        THRESH_BINARY_INV + THRESH_OTSU)          # dark glyphs -> white
  mask ink to container interior (erode container 2px first, so the
        bubble outline stroke doesn't join the text)
  dilate ink with a wide flat kernel (kw = 6*est_stroke, kh = 1) to fuse
        glyphs into LINES; findContours -> line boxes
  keep boxes with h in [6, 0.25*panel_w], w/h >= 1.2, ink density in box
        0.15..0.85 (rejects solid black shapes and speckle)
  output: [(x, y, w, h)] in panel coords + per-container median line h
```

~40 lines, runs in tens of ms, zero deps. **Also valuable as candidate A's
post-filter** (intersect DBNet boxes with the Stage-1 container mask).

**Failure modes (honest):**
- *Screentone/halftone noise*: dot patterns survive Otsu inside grayish
  containers → speckle contours. Mitigation: the ink-density band + minimum
  line width; median-blur 3 before threshold kills most dots. Residual risk
  on dense tone.
- *SFX lettering*: huge stylized strokes over art — no bright container, so
  Stage 1 skips them entirely. That's *by design* (SFX aren't the fault
  source) but means the fallback under-reports "text present" vs OCR; keep
  the OCR binary flag as the OR-term for the grid guard.
- *Black caption boxes with white text* (common for system messages!): Stage 1
  misses dark containers. Fix: run a mirrored pass — `threshold(gray, 90,
  THRESH_BINARY_INV)` for dark containers, then bright-ink Otsu inside. The
  ch3 "orange box" style UI panels are mid-tone: add a low-saturation…
  actually panels are B/W after scanlation; mid-gray boxes are the weak spot.
- *Gray-on-gray / low-contrast scans*: both stages degrade. DBNet (A)
  handles these; the fallback doesn't.
- *Text over art without a bubble* (floating narration): missed by Stage 1.
  Partial mitigation: run Stage 2 on the whole panel with stricter filters
  when OCR says text exists but Stage 1 found nothing.

---

## 6. Integration plan (files, sidecar, flags)

New module `pipeline/text_metrics.py`:
- `detect_text_boxes(png_path) -> [{"x":..,"y":..,"w":..,"h":..}]` — cv2.dnn
  DBNet (A) with cv2-classic (F) fallback when the model file is absent.
- CLI: writes `<slug>.textboxes.json` sidecar next to `<slug>.ocr.json`,
  cached like the OCR reads (`script_from_panels.py:216-225` pattern). One
  pass over ~60-100 panels ≈ 20-40 s CPU, once per chapter.

Consumers:
- `panel_render.py` — load sidecar where the OCR sidecar is loaded
  (850-866); implement §3(a)/(c) behind a new flag `--text-aware-framing`
  (default off until benchmarked, per improve-train protocol).
- `layout_smart.py` — `plan_layout(..., textboxes=None)`; §3(b) replaces the
  binary guard only when the sidecar exists.

Model vendoring: commit `models/text_detection_en_ppocrv3_2023may.onnx`
(4.7 MB, Apache-2.0 — include the opencv_zoo LICENSE copy alongside).

## 7. Install + smoke tests

**A — PP-OCRv3 det ONNX via cv2.dnn (no pip install):**
```bash
mkdir -p models && curl -L -o models/text_detection_en_ppocrv3_2023may.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/text_detection_ppocr/text_detection_en_ppocrv3_2023may.onnx
python - <<'EOF'
import cv2
det = cv2.dnn.TextDetectionModel_DB("models/text_detection_en_ppocrv3_2023may.onnx")
det.setBinaryThreshold(0.3); det.setPolygonThreshold(0.5)
det.setInputParams(1.0/255, (736, 736), (122.67891434, 116.66876762, 104.00698793), True)
img = cv2.imread("projects/<golden>/panels/p0001.png")  # any text-bearing panel
quads, conf = det.detect(img)
print(len(quads), "boxes; heights:", sorted(int(max(q[:,1])-min(q[:,1])) for q in quads)[-5:])
EOF
```
(Note: `detect()` returns quads on the 736×736 letterboxed input in recent
OpenCV; verify scaling back to panel coords on our cv2 build — the opencv_zoo
`ppocr_det.py` demo shows the exact pre/post-processing to copy if the
high-level API misbehaves. Tall webtoon panels: tile into ~1:1 windows with
10% overlap before detect, merge boxes — same tiling trick as any tall-image
OCR.)

**F — cv2-classic fallback (nothing to install):**
```bash
python - <<'EOF'
import cv2
g = cv2.cvtColor(cv2.imread("projects/<golden>/panels/p0001.png"), cv2.COLOR_BGR2GRAY)
_, bright = cv2.threshold(cv2.medianBlur(g,3), 200, 255, cv2.THRESH_BINARY)
n, lab, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(bright, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT,(7,7))))
print("containers:", sum(1 for s in stats[1:] if s[4] > 0.002*g.size and s[4]/(s[2]*s[3]+1e-9) > 0.45))
EOF
```

## 8. Open risks → quality guards

| Risk | Maps to guard | Mitigation |
|---|---|---|
| Over-zealous crop cuts art / half-cuts bubbles | gap card guard "punch-in must respect bubble boundaries"; acceptance "no new cropped_content" | crop union must fully contain every intersected detected box + 24 px pad; crop floor 55% of panel dim; escalate to (c) instead of cropping harder |
| DBNet misses stylized/rotated text → no action taken | acceptance 4→≤1 | OR with OCR binary flag: text-known-present but zero boxes ⇒ conservative fallback (treat as GUARD_H_TEXT=0.55 today's behavior; never worse than baseline) |
| DBNet false positives on screentone → needless punch-ins | no new cropped_content / no visual churn | Stage-1 container-mask post-filter (§5); require ≥2 line boxes or OCR agreement before acting |
| Tall-panel tiling bugs (coords off) | all | unit-test box coords by rendering debug overlays on 5 golden panels before wiring into render |
| Wall-time | pipeline budget | detection cached in sidecar, one-time ~30 s/chapter; render-side cost is pure arithmetic |
| int8 model instability | correctness | use fp32 model (4.7 MB) |
| Threshold too aggressive (everything punches in) | style regression | hysteresis: act only when median rendered height < 40 px AND panel is on screen ≥ 1.2 s; log per-scene decisions for the benchmark diff |

## 9. Expected impact vs acceptance criteria

- Golden-chapter `phone_readability + unreadable_text`: **4 → 0-1.** The
  detector directly measures the faulted quantity; (a)+(b)+(c) cover both
  fault populations. Residual: panels with dense tiny text where even
  full-frame can't reach 40 px (rare; (c) handles most).
- Ch3-style load (13 faults): projected ~2-4 residual — the 3 smart-grid
  MEDIUMs are fully addressed by (b) (height-aware guard makes the exact
  faulted cells impossible); seq info-box faults addressed by (a)/(c) except
  where the reviewer flags text that is small *in the source art itself* at
  full frame.
- `cropped_content`: guarded to zero-new by the ≥55%/full-box-containment
  rules; benchmark must confirm.
