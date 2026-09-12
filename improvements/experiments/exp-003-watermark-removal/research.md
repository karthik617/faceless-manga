# exp-003: Scanlation watermark detect + inpaint — research

Gap: `improvements/gaps/gap-003-watermark-removal.md` (watermark HIGH faults 4→0
on golden chapter; no visible smearing; clean stage ≤ +60s/chapter CPU).

Context read: `pipeline/clean_bubbles.py` (cv tier already does mask →
`cv2.inpaint(..., cv2.INPAINT_TELEA)` at clean_bubbles.py:56, gemini tier =
`gateway.image_edit`, output convention `panels/ -> panels_clean/`, idempotent
skip at clean_bubbles.py:75), `pipeline/verify_panels.py` (OCR reads keyed by
bare panel filename, `<slug>.ocr.json`), `pipeline/gateway.py:82` (`llm_vision`
batched images → JSON), `pipeline/gateway.py:117` (`image_edit`).

Problem shape (from evidence): the SAME site watermark ("FLAMESCANS.ORG")
repeats near-identically across a chapter, always in the top/bottom ~12% band,
corner-anchored, solid or semi-transparent, on B/W-ish manhwa art. This is the
single most exploitable fact: detect once per chapter, template-match the rest.

---

## 1. Candidate matrix

| # | Candidate | Role | License | Install cost | CPU latency / panel | Fit (1-5) | Risk |
|---|-----------|------|---------|--------------|---------------------|-----------|------|
| A | **Build-ourselves: gateway-vision template discovery + cv2 matchTemplate + band-limited Telea** | detect+remove | n/a (existing deps only) | **zero** | ~10-40 ms CV + 2-3 vision calls per chapter (amortized) | **5** | template drift if scanlator varies placement/opacity; mitigated by multi-scale match + band re-verify |
| B | cv2.inpaint Telea/NS (already in stack, clean_bubbles.py:56) | remove | Apache-2.0 (OpenCV) | zero | ~5-30 ms for a corner-band mask | 5 (for small text) | smears if mask overlaps dense art/screentone; fine on flat margins & thin strokes |
| C | Crop-safe reframe (band trim) | remove | n/a | zero | ~1 ms | 4 (when band is dead margin) | crops art if misjudged; needs a uniformity test before use |
| D | gateway `image_edit` (Gemini) targeted re-edit (existing gemini tier, clean_bubbles.py:61) | remove (escalation) | gateway ToS | zero new deps; 1 call/panel | seconds + cost per call | 4 | non-deterministic; can alter art; use only for panels where CV mask overlaps art |
| E | Carve/LaMa-ONNX (`lama_fp32.onnx`) + onnxruntime | remove (big regions) | **Apache-2.0** (model), **MIT** (onnxruntime) | `pip install onnxruntime` (~23 MB wheel, cp312 manylinux avail) + ~200 MB model download from HF | ~1.5-4 s/512² tile CPU (fixed 512×512 input → tile or resize the band) | 3 | overkill for corner text; fixed input shape; photo-trained — quality on B/W line art unvalidated |
| F | simple-lama-inpainting 0.1.2 | remove | **Apache-2.0** (verified on repo) | pulls **torch** (~200 MB+ CPU wheel) + big-lama weights (~200 MB) | ~2-6 s/panel CPU | 2 | last release **Jul 2023**; PyPI classifier says py3.10/3.11 only (`>=3.10,<4.0` — 3.12 untested, 15 open issues); heavy for this gap |
| G | IOPaint (ex lama-cleaner) `iopaint run --model=lama --device=cpu` batch CLI | remove | **Apache-2.0** (NOT AGPL — verified) | torch + full webui stack, hundreds of MB | ~2-6 s/panel | 2 | repo **archived Aug 2025** (read-only); huge dep surface for one corner |
| H | PaddleOCR 3.x detector-only (PP-OCRv5/v6 det, DBNet-based) | detect | **Apache-2.0** | `pip install paddleocr` + paddlepaddle runtime (several hundred MB); py3.8-3.13 OK | ~0.3-1 s/panel CPU (server det); mobile det faster | 3 | massive dep for a corner-band OCR we can get from the gateway for free; det model is photo/doc-trained, decent on overlay text |
| I | EAST via `cv2.dnn.TextDetectionModel_EAST` | detect | opencv sample is Apache-2.0 **but the standard frozen_east_text_detection.pb comes from argman/EAST — GPL-3.0 repo, model license unclear → FLAG** | model ~95 MB, no new pip dep | ~0.5-2 s/panel CPU (320×320 input) | 2 | license murk on weights; scene-text trained, mediocre on stylized watermark fonts |
| J | comic-text-detector (dmMaze) | detect | **GPL-3.0 → do not shortlist** | torch + ~50 MB model | ~1-3 s/panel | 1 (license) | GPL contaminates; also detects ALL text incl. SFX/bubbles — over-broad for this gap |
| K | plain cv2 MSER / morphology on corner bands | detect | Apache-2.0 (OpenCV; MSER is in main `features2d`, no contrib needed) | zero | ~5-20 ms/band | 4 | tuned-threshold fragility on semi-transparent marks over screentone; great as the cheap first pass |

License summary: **rejected** J (GPL), **flagged** I (weights provenance GPL
repo). Everything shortlisted is Apache-2.0/MIT/zero-dep.

### Notes on "is Telea actually sufficient for small corner text on B/W manga?"

- Telea/NS quality degrades with mask *radius*, not mask *count*. Watermark
  glyph strokes are 2-6 px wide after dilation — same regime as the existing
  bubble-text tier, which already ships and passes review. On flat margins
  (white gutter, black letterbox, flat-tone sky) Telea is visually perfect.
- The known Telea failure mode is directional texture (screentone/hatching)
  under a WIDE mask: it produces a soft smear. Community comparisons
  (IOPaint/lama-cleaner docs and the LaMa paper's motivation) agree LaMa wins
  for *large* masks; for thin-stroke text over simple background the delta is
  negligible. That is exactly why the plan below uses Telea first and
  escalates only when the mask is large or overlaps detected art.
- Semi-transparent watermarks are actually *easier* for inpainting when the
  underlying band is flat (blend converges to the flat color), but the mask
  must be built from the template alpha, not a dark-pixel threshold —
  threshold misses low-contrast semi-transparent strokes. Template matching
  handles that (match on edges / normalized cross-correlation).

---

## 2. Recommended: build-ourselves (candidate A + B + C, tier D escalation)

**Zero new dependencies.** New file `pipeline/clean_watermarks.py`, mirroring
the `clean_bubbles.py` contract (reads `panels/`, writes `panels_clean/`,
idempotent, `panel_render.py` auto-prefers `panels_clean/` — no renderer change).
It can also be invoked BEFORE clean_bubbles so both cleanups compose into the
same `panels_clean/` dir (watermark pass first, bubble pass reads its output).

### Design: per-chapter template discovery, then template-match + inpaint

**Stage 0 — free detection from existing OCR reads (no new calls).**
`<slug>.ocr.json` reads already describe each panel. Extend the read prompt in
`script_from_panels.py::read_panels` (one prompt line, zero extra calls) to add
`"watermark": {"text": ..., "corner": "bl|br|tl|tr", "bbox": [x,y,w,h]} | null`
per panel. Panels whose reads flag a watermark seed the discovery set. If reads
are already cached without this field, fall back to Stage 1.

**Stage 1 — template discovery (2-3 gateway calls per chapter, cached).**
- Pick 3 sample panels (first/middle/last flagged, else first/middle/last of
  chapter). One batched `gateway.llm_vision` call (same batching pattern as
  verify_panels.py:270 tier3): "Find any website/scanlation watermark text in
  these panels. For each, return JSON: panel, text, and bbox as fractions of
  image size." Vision bboxes are approximate → snap: clamp to top/bottom 12%
  bands, then refine inside the clamped box with cv2 (Otsu/adaptive threshold
  + connected components + `cv2.boundingRect`) to pixel-tight bounds.
- Crop the tightest instance as the **template**; store grayscale template +
  a stroke mask (threshold of template minus local background) in
  `output/<slug>/watermark_template.png` + `.json` (text, corner, band).
  Cache = one discovery per chapter, idempotent (quality guard).

**Stage 2 — per-panel match (pure cv2, ~10-40 ms).**
- For every panel, crop only the top and bottom 12% bands (guard: mask is
  band-limited by construction — gap card quality guard #1).
- `cv2.matchTemplate(band_edges, template_edges, cv2.TM_CCOEFF_NORMED)` on
  Canny/gradient images (robust to semi-transparent alpha and background
  variation); try 3 scales (0.9/1.0/1.1) since panel widths vary post-segment.
  Accept peaks ≥ 0.55; multiple corners allowed.
- MSER/morphology backstop (candidate K): if template match finds nothing but
  the OCR read flagged a watermark, run MSER on the band, keep letter-sized
  components arranged in a horizontal run near a corner.

**Stage 3 — remove, cheapest-safe-method first.**
1. **Crop** (candidate C) when safe: compute the band's content outside the
   matched bbox; if `std < 8` and mean near 0 or 255 (dead margin / letterbox)
   AND the panel stays ≥ 90% of original height and within the renderer's
   usable aspect, trim the band instead of inpainting (zero artifact risk).
   Webtoon strips have generous gutters so this fires often.
2. **Telea inpaint** (candidate B) otherwise: mask = template stroke mask
   placed at the matched location, dilated 2 px (same recipe as
   clean_bubbles.py:49); `cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)`.
   Radius 5 (vs 3 in bubbles) because watermark strokes sit over gradients
   more often than bubble text.
3. **Escalate to `gateway.image_edit`** (candidate D) only when the mask
   region's local texture is busy (Laplacian variance of the surrounding ring
   above a threshold ⇒ Telea would smear): instruction "remove the watermark
   text '<TEXT>' in the <corner>; keep all artwork identical." Expected on
   ≤ 1-2 panels per chapter based on the evidence (marks sit in margins).
4. Log per-panel `{panel, method, score, bbox}` into
   `output/<slug>/watermark_log.json` so the evaluator can attach
   before/after crops (acceptance criterion #2).

**Wall-time budget:** discovery ≈ 10-20 s (vision calls, once), CV pass
≈ 0.05 s × ~60 panels ≈ 3 s, 0-2 image_edit escalations ≈ 0-20 s.
Comfortably under the +60 s guard.

**Quality-guard compliance:**
- band-limited masks only (Stage 2 crops bands before any matching);
- never touches regions overlapping bubbles/art unless escalated to the
  vision editor (which is instructed to preserve art);
- writes to `panels_clean/`, skips existing non-empty outputs
  (clean_bubbles.py:75 pattern), template cached ⇒ idempotent.

**Legal-optics note (gap card):** removing aggregator branding aligns with the
README legal stance; the log file doubles as an audit trail of what was removed.

### When crop beats inpaint (candidate C, answered)

Crop is safe iff ALL of: (1) mark fully inside the top/bottom band; (2) band
content outside the mark is near-uniform (std < ~8 gray levels — dead margin,
gutter, or letterbox); (3) remaining panel keeps enough height that
`panel_render.py` framing/zoom is unaffected (≥ 90% height, and the panel is
not already extreme-aspect); (4) segmenter didn't place story art at the very
edge (check `_content_frac`-style density on the trimmed strip). When those
hold, crop is strictly better: zero artifacts, zero cost. Otherwise inpaint.

---

## 3. Runner-up: LaMa via ONNX (candidate E) — only if Telea visibly smears

If the evaluator's before/after crops show Telea smearing that the image_edit
escalation doesn't cover economically:

- Model: **Carve/LaMa-ONNX** `lama_fp32.onnx` (HF, Apache-2.0, port of
  big-lama; fixed 512×512 input, opset 17). https://huggingface.co/Carve/LaMa-ONNX
- Runtime: **onnxruntime** (MIT, Microsoft, cp312 manylinux wheel ~23 MB,
  actively released — 1.29.0 Aug 2026). No torch.
- Usage: crop a 512×512 tile around the watermark bbox (bands are ≤ 12% of
  panel height, so one tile nearly always suffices), run LaMa on tile+mask,
  paste back. ~1.5-4 s/tile CPU — still inside the wall-time guard for a
  handful of panels.
- Avoid simple-lama-inpainting (F: stale, torch, py3.12 unverified) and
  IOPaint (G: archived, huge) — the ONNX route gets the same weights lighter.

### Install + smoke tests (top 2)

**Top pick (A) — nothing to install.** Smoke test (3 lines, integrator runs):
```bash
./venv/bin/python3 - <<'EOF'
import cv2, numpy as np
img = cv2.imread("output/<golden-slug>/panels/p0013.png"); h = img.shape[0]
band = img[int(h*0.88):]; print("band", band.shape, "ok:", band.size > 0)
EOF
```
(then the real prototype: run `clean_watermarks.py --panels ... --dry-run` on
the golden chapter and eyeball the logged bboxes on scenes 1/13/15/20.)

**Runner-up (E):**
```bash
./venv/bin/pip install onnxruntime          # MIT, ~23 MB, cp312 wheel
# model (Apache-2.0, one-time, ~200 MB):
curl -L -o models/lama_fp32.onnx https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx
```
Smoke test:
```bash
./venv/bin/python3 - <<'EOF'
import onnxruntime as ort, numpy as np
s = ort.InferenceSession("models/lama_fp32.onnx", providers=["CPUExecutionProvider"])
img = np.random.rand(1,3,512,512).astype(np.float32); m = np.zeros((1,1,512,512), np.float32)
print(s.run(None, {s.get_inputs()[0].name: img, s.get_inputs()[1].name: m})[0].shape)
EOF
```

---

## 4. Open risks → quality guards

| Risk | Guard mapping | Mitigation |
|------|---------------|------------|
| Template drift (scanlator moves/rescales the mark mid-chapter, or title card uses a different lockup — evidence: 0:39 title-card watermark differs from corner marks) | acceptance #1 (4→0) | discovery samples first/middle/last; keep up to 2 templates per chapter; MSER backstop; title-card panels usually dropped by verify_panels tier1 anyway (kind=cover) |
| Telea smear over screentone | acceptance #2 (no smearing) | Laplacian-variance texture check routes busy regions to image_edit; evaluator attaches before/after crops from watermark_log.json |
| image_edit alters art (non-determinism) | quality guard #1 (never touch art) | escalation expected on ≤ 2 panels/chapter; instruction pins the exact text+corner; diff-check output vs input outside the band and reject edits that change > 1% of out-of-band pixels (copy original through on reject) |
| False-positive match nukes in-art text (SFX in a corner) | quality guard #1 | require match score ≥ 0.55 AND bbox within 12% band AND (when OCR read available) read does not place dialogue/SFX at that location |
| Wall time | acceptance #3 (≤ +60 s) | CV path ≈ 3 s/chapter; vision discovery amortized once; escalations capped (e.g. max 4/chapter, rest fall back to Telea + log) |
| Legal/licensing | README legal section | all shortlisted components Apache-2.0/MIT/zero-dep; GPL candidates (comic-text-detector, EAST weights) explicitly rejected/flagged above |

## 5. Decision summary

Adopt **A (+B/C, D escalation)**: gateway-vision per-chapter watermark template
discovery → cached template → cv2 multi-scale template match restricted to
top/bottom 12% bands → crop when the band is dead margin, else band-limited
Telea inpaint, else targeted Gemini re-edit; new `pipeline/clean_watermarks.py`
following the `panels/ -> panels_clean/` idempotent convention. Keep
**E (LaMa-ONNX + onnxruntime)** as the flag-gated upgrade path if the golden
chapter’s before/after crops show Telea smearing.
