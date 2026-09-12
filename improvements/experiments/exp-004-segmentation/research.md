# exp-004-segmentation — research

Gap: `improvements/gaps/gap-004-segmentation.md` — blind flattest-row splits cut through
bubbles/art (`pipeline/segment_panels.py:139-160`), near-black beats dropped (`:197-200`,
`MIN_CONTENT_FRAC` skip at `:254` and `:275`). 6 MEDIUM `cropped_content` faults on golden
chapter; downstream `empty_screen` / `unreadable_text`.

---

## 1. Paper digest — Pang, Cao, Lau, Chan, "A Robust Panel Extraction Method for Manga" (ACM MM 2014)

Source PDF read in full: https://www.cs.cityu.edu.hk/~rynson/papers/mm14a.pdf
(4 pages; 52 citations per Semantic Scholar; DOI 10.1145/2647868.2654990).

### Method (3 stages, all classic CV, no ML)

1. **Panel block generation.** Instead of CCL on foreground ink (fragile), run connected-
   component labeling on the **page background** (near-white pixels, threshold 235); the
   complement is the "panel block". Unclosed panels (open borders at page edges) are
   repaired first by scanning intensity variation along the four page boundaries
   (`max(φ·x)−min(φ·x) > 0.7·Le` and `Σφ' > 10` → draw a black closing line). Fourth-wall-
   break holes are fixed by merging components whose convex hulls overlap >50%.
2. **Panel block splitting (the core idea).** Recursive binary X-Y splitting of the panel
   block. At each level, **candidate splitting lines** are scored with a cost function on
   the accumulated row/column intensity `g(y) = Σ I(p)`:
   `Ch(Sy) = 2·g(y) − g(y+1) − g(y−1)` — i.e. a discrete Laplacian that peaks exactly at
   the *boundary between panel content and white spacing*, not merely at the whitest row.
   Lines with `Ch > 0.1·W` (or `g(y) > 0.1·W` to catch diagonal gutters) become candidates;
   nearby candidates are grouped into stripes and the stripe center is used.
   **False-cut elimination:** a candidate stripe is *rejected if it cuts the black panel
   region* — concretely if the span between its leftmost/rightmost black pixels exceeds
   `0.7·W`. Diagonal stripes are validated by ellipse fit (minor/major < 0.2) and a line is
   fitted through per-column centroids of white runs. The **optimal** line per recursion
   level = highest confidence (fraction of white pixels along its length; a line crossing
   nothing scores 1.0).
3. **Panel shape extraction.** Convex hull per region → 4 corner points (one per quadrant,
   furthest from centroid) → local search (r=10 px) maximizing quadrilateral area. Handles
   non-axis-aligned (slanted) panels.

Results: 91.3% panel rate / 87.9% page rate / 0.97 mean Jaccard on 104 Naruto+Slam Dunk
pages, beating erosion-dilation CCL [Ho et al.]. Known failure: large visual symbol
breaking a panel border → fragmented panel.

**No reference implementation exists.** No code from the authors; the closest public
reimplementation family is X-Y-cut-style splitters (e.g. `njean42/kumiko`, AGPL-3.0 —
flagged, contour-based not cost-based anyway). Treat the paper as an algorithm spec.

### Verdict on webtoon applicability

- **Direct use: NO.** The method assumes a *paged* layout with white spacing separating
  panels and terminates recursion when no legal cut exists. Our failure mode is exactly the
  opposite: long **gutterless** stretches where *no* clean gutter exists and we must place
  a forced cut anyway. On such a strip the paper's method returns "cannot divide further"
  — one giant panel — which is our pre-max_slice behavior, not a fix.
- **Ideas that DO transfer (high value):**
  1. **Laplacian-of-projection cost `2g(y)−g(y+1)−g(y−1)`** — better cut scoring than our
     current "flattest 9-row window of (max−min) spread": it prefers the *edge* of a flat
     region (just past the art) instead of the middle of any flat texture (which can be the
     inside of a big bubble — the exact bug producing cropped bubbles).
  2. **Legality veto before choosing a cut** ("stripe cuts the black panel region → reject")
     — generalizes to: *a cut row is illegal if it intersects a detected text/bubble box*.
     This is precisely the gap card's acceptance criterion #2.
  3. **Confidence ranking of candidate cuts** (fraction of non-content pixels crossed) →
     pick the best *legal* row in the search band, else widen the band, else pick the
     min-energy row as last resort (never crash).
  4. Background-CCL panel-block extraction + corner recovery could later improve our
     `segment_page_manga()` for the occasional paged-manga input (handles unclosed/
     borderless/slanted panels our Otsu+close+contours misses) — out of scope for this gap
     but worth a note in the ledger.
- **Net:** use mm14a as the **build-ourselves algorithmic upgrade** for cut *placement*
  (energy + veto), paired with a text-box detector for the veto mask.

---

## 2. Candidate matrix

| # | Candidate | License | Model / size | CPU (we are CPU-only) | Py3.12 / venv fit | Maintenance | Fit (1-5) | Risk |
|---|-----------|---------|--------------|------------------------|-------------------|-------------|-----------|------|
| A | **mm14a build-ourselves** (Laplacian cut energy + legality veto, opencv only) | n/a (our code; paper is a spec, no code copied) | none | trivial (<1s/strip) | perfect (opencv+numpy already in venv) | n/a | 4 | veto mask from heuristics only → may still miss unusual bubbles |
| B | **deepghs/manga109_yolo via dghs-imgutils** (`text`+`frame` classes) as split-guard for A | **MIT** (lib) / model trained on Manga109-s (research dataset; weights redistributed openly by deepghs) | YOLO-n 2.6-3.0M params ONNX (~6-12MB); s/m/l available | yes — onnxruntime CPU; n-model ~0.1-0.3 s per 640px window; a 20k-px golden strip ≈ 20-30 windows ≈ 5-10 s | yes — pure-py wheel, py3.8-3.13, pulls onnxruntime+huggingface_hub; no torch | active: dghs-imgutils 0.19.0 released 2025-09-10, monthly releases | **5** | Manga109 is B/W paged manga; colored webtoon bubbles usually still detected (high-contrast text) but must verify on golden strip; model download needs HF access once (cacheable) |
| C | **comic-text-detector** (dmMaze) text lines+blocks+mask | **GPL-3.0 — flagged** | ~25MB ONNX (yolov5s-based + DBNet + UNet) | yes (onnxruntime) | no pip package — clone repo + manual model download from manga-image-translator release | last commit years old; 6 open issues; superseded by BallonsTranslator | 3 | GPL contaminates pipeline if imported; only shortlist if B fails on webtoon text |
| D | **Magi v1/v2/v3** (manga-whisperer) — panels+text+chars | **"academic research purposes only"** — NOT ok for this pipeline | DETR-style transformer, ~500MB+, `trust_remote_code=True` | painful — README assumes `.cuda()`; CPU inference minutes/page | needs torch+transformers (~2GB add to venv) | active (v3 2025) | 1 | license blocker alone disqualifies; also 10-100x wall-time budget |
| E | **DASS-Det** (barisbatuhan) | Apache-2.0 | YOLOX ~30-100MB | claims CUDA>=10.2 required; torch+chainer deps | poor (chainer is dead on py3.12) | stale (19 commits, inference-only) | 1 | detects **face/body only — no panel, no text class**; wrong tool for this gap |
| F | **comic-panel YOLO variants on HF** (e.g. arkaprav0/comic_panel_yolo26s) | unlicensed (no model card) → reject | unknown | unknown | unknown | single upload, 0 likes | 1 | unlicensed + undocumented |
| G | **kumiko** panel splitter | AGPL-3.0 — flagged | none (opencv contours) | yes | CLI-oriented | maintained | 2 | AGPL; contour method ≈ our existing manga mode; doesn't address gutterless webtoon splits |

## 3. RECOMMENDED: **B + A combined** — text-box-guarded energy splits ("bubble-aware split", gap card direction 2, powered by an MIT-licensed detector and mm14a's cut math)

Magi and comic-text-detector fall to license; DASS-Det to capability. The cheap direction
in the gap card is also the best one: keep our splitter's topology, fix *where* forced cuts
land. Runner-up: **A alone** (pure-CV veto) — same code path, weaker veto mask, zero new deps.

### Install (top candidate B)

```bash
# inside the existing venv (python3.12)
pip install dghs-imgutils onnxruntime          # MIT + MIT; no torch
```

### Smoke test B (3 lines)

```bash
python3 -c "
from imgutils.generic.yolo import yolo_predict
dets = yolo_predict('output/<golden-slug>/panels/p0001.png', 'deepghs/manga109_yolo', 'v2023.12.07_n_yv11')
print([(box, lbl, round(c,2)) for box, lbl, c in dets if lbl in ('text','frame')][:10])
"
```
Expect a non-empty list of `text` boxes on any panel containing a bubble. First run
downloads the ONNX (~10MB) to `~/.cache/huggingface`.

### Install + smoke test runner-up C (only if B misses webtoon text; GPL — keep quarantined as a subprocess, never import)

```bash
git clone https://github.com/dmMaze/comic-text-detector /tmp/opencode/ctd
# download comictextdetector.pt.onnx from
# https://github.com/zyddnys/manga-image-translator/releases/tag/beta-0.2.1 into /tmp/opencode/ctd/data/
python3 /tmp/opencode/ctd/inference.py --img <panel.png>   # emits text mask + bboxes
```

### Integration design into `segment_panels.py`

Flag: **`--split-guard {none,blob,yolo}`** (default `none` = current behavior, so the
change is dark until benchmarked). Env override `FM_SPLIT_GUARD` for the orchestrator.

1. **New helper `_forbidden_rows(strip, guard)` → bool array `[h]`:**
   - `guard=="yolo"`: slide a window (height 1280, stride 1024, downscale width to ≤640)
     over the stitched strip; run `yolo_predict(..., 'deepghs/manga109_yolo',
     'v2023.12.07_n_yv11')`; for every `text` det above conf 0.35, mark rows
     `y0−pad .. y1+pad` (pad = 16 px) forbidden. Cache dets per strip hash.
   - `guard=="blob"` (build-ourselves, see §4): mark rows covered by bubble-blob boxes.
2. **Change the forced-split loop (`segment_strip_webtoon`, current lines 138-161):**
   - replace the flatness score with mm14a energy on the row projection:
     `E(y) = row_content(y) − λ·(2g(y) − g(y−1) − g(y+1))` where
     `g(y) = row_mean`, `row_content = row_spread`; pick `argmin E` **among legal rows**
     (`~forbidden[lo:hi]`).
   - if no legal row in the band, widen `hi` up to `y+bh` (still capped by max_slice
     enforcement on the *next* iteration); if still none, fall back to current
     flattest-row behavior and log `"! forced cut through content at y=…"` (never crash).
3. **Black beats (gap direction 3):** in the two `MIN_CONTENT_FRAC` skip sites (`:254`,
   `:275`), when `guard != none`, emit the panel and append `"note": "beat"` to its index
   entry instead of skipping. Using the **existing optional `note` key** (already emitted
   for odd-width pages at `:240`) keeps `panels_index.json` schema strictly unchanged.
   `panel_render.py` can later special-case `note=="beat"` (hold + narration, no Ken Burns).
4. **Deterministic "zero splits through text boxes" check** (benchmark script,
   `improvements/experiments/exp-004-segmentation/check_splits.py`):
   - run the detector once over the golden raw stitched strip → list of text boxes
     `[(x0,y0,x1,y1)]` in strip coordinates (fixed seed irrelevant — ONNX inference is
     deterministic on CPU);
   - read `panels_index.json`; every *interior* cut is `y_cut = bbox.y` of each webtoon
     panel except the first, restricted to cuts NOT coinciding with a detected gutter row
     (recompute `row_is_gutter` to classify);
   - assert `not any(b_y0 < y_cut < b_y1 for each text box)`; exit 1 with the offending
     `(panel, y_cut, box)` list otherwise. This is the acceptance-criterion gate.
5. **Guards mapping:**
   - *panel count ±30%*: legality veto only relocates cuts, never adds them; the merge/
     min-slice logic is untouched. Beat panels add at most the previously "skipped" count —
     report both numbers in the benchmark.
   - *wall-time ≤3x*: yolo-n over ~25 windows ≈ 5-10 s CPU vs current ~1-2 s segment step
     → borderline; mitigations: stride 2048 with 256 overlap, or run detector only inside
     the >2600 px slices that actually need forced cuts (recommended — typically 2-5
     slices, <3 s total).
   - *reading order / schema*: unchanged (top-to-bottom preserved; only `note` reused).

## 4. Build-ourselves fallback (no new deps — opencv/numpy only)

If `dghs-imgutils`' Manga109 model under-detects colored webtoon bubbles, implement
`guard=="blob"`:

1. **Bubble candidates:** threshold near-white AND near-black regions
   (`gray>=225 | gray<=30`), CCL (`cv2.connectedComponentsWithStats`); keep components with
   area 1k-250k px², fill-ratio (area/bbox) > 0.55, aspect 0.2-5, and **interior edge
   density** (Canny pixels inside eroded component / area) in 0.02-0.35 — bubbles are flat
   fields *containing* thin strokes (text); flat backgrounds have ~0 edge density and art
   has high density. This reuses the exact contrast machinery already in `_content_frac`.
2. **Veto mask:** union of candidate bboxes inflated 16 px → forbidden rows.
3. **Cut placement:** identical mm14a energy + legality logic as §3 step 2 (the code is
   shared; only `_forbidden_rows` differs), so A/B-ing yolo vs blob guard is one flag flip.
4. Deterministic and fast (<0.5 s per strip); fully deterministic check in §3 step 4 works
   unchanged with blob boxes.

Expected: blob guard catches classic white balloons and black caption boxes (the 6 golden
faults are bubble-cut faults, likely all coverable); yolo guard additionally catches
borderless floating text and screentoned bubbles.

## 5. Open risks → quality guards

| Risk | Guard hit | Mitigation |
|------|-----------|------------|
| Manga109-trained yolo misses colored webtoon text | cropped_content stays >2 | benchmark detector recall on golden strip *first*; fall back to blob guard or C-as-subprocess |
| Detector windowing splits a bubble across windows → half box | check §3.4 fails | 256 px window overlap + merge boxes across windows (IoU>0.3) |
| Veto leaves no legal row in a dense stretch → slice > max_slice_px persists | panel count / renderer assumptions | allow +25% soft cap overflow before forcing an illegal cut; log it |
| Beat panels re-enter delivery as black frames | empty_screen fault returns | tag `note:"beat"` and have panel_render hold w/ narration; benchmark reviews beats explicitly |
| Wall-time 3x budget | segment ≤3x | detect only inside slices needing forced cuts (2-5 per chapter) |
| Magi/CTD license contamination | legal | Magi rejected outright; CTD only ever as quarantined subprocess, last resort |

## 6. Sources

- mm14a PDF (read in full): cs.cityu.edu.hk/~rynson/papers/mm14a.pdf — Pang, Cao, Lau, Chan, ACM MM 2014, DOI 10.1145/2647868.2654990, 52 citations
- github.com/dmMaze/comic-text-detector (GPL-3.0, 372★, stale)
- github.com/ragavsachdeva/magi (research-only license; HF: ragavsachdeva/magi{,v2,v3})
- github.com/barisbatuhan/DASS_Det_Inference (Apache-2.0; face/body only)
- huggingface.co/deepghs/manga109_yolo (ONNX; classes body/face/frame/text; n=2.59M params F1 0.88, s=9.4M F1 0.90)
- pypi.org/project/dghs-imgutils (MIT, 0.19.0 2025-09-10, py3.8-3.13)
- huggingface.co/arkaprav0/comic_panel_yolo26s (no card, no license → rejected)
