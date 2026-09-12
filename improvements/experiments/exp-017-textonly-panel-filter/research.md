# exp-017 research — text-only / SFX-only / cover-fragment panel filter

Gap: `improvements/gaps/gap-017-textonly-panel-filter.md`
Target integration: `pipeline/verify_panels.py` (tier structure), new gate
between tier 1 and tier 2, plus a tier-2 proxy fix and a forced tier-3
escalation path for geometric suspects.

## TL;DR

**#1 recommendation: build-ourselves deterministic gate using the two text
detectors the pipeline already vendors** — DBNet line boxes
(`pipeline/text_boxes.py`, Apache-2.0, on disk) for the required
*text-coverage ratio* metric, plus the already-vendored
`deepghs/manga109_yolo` (`pipeline/yolo_detect.py`) run with ALL FOUR classes
(`body, face, frame, text`) instead of text-only, giving a direct
"any character/art present?" signal. Zero new pip deps, zero new license
exposure, fully deterministic, benchmark-able on the ready-made ch4 labeled
set (11 positives + final-script kept panels as negatives).

Runner-up (only if the heuristic's precision/recall on the ch4 set
disappoints): `ogkalu/comic-text-and-bubble-detector` (Apache-2.0, RT-DETRv2,
11 MB int8 ONNX, classes `bubble / text_bubble / text_free`) — trained
explicitly on webtoons/manhwa, bubble-coverage-ratio in one shot.

## Context established from the repo (read-only)

- `verify_panels.py:56-59` tier-1 gate keys entirely on OCR `kind`; 9/11 ch4
  offenders are tagged `kind=story, quality=ok` (confirmed: p0011 read at
  ocr.json:103-115 — dialogue "JAE HWAN HYUNG...", scene_beat literally
  says "A close-up of text in a speech bubble", yet kind=story).
- `verify_panels.py:363-369` quote-exception: p0021/p0022 ARE `kind=textonly`
  (ocr.json:227-243) but survived because narration quotes them verbatim.
- `verify_panels.py:153-166` `relevance_score` mixes `dialogue` +
  `narration_text` into the panel proxy → quoted bubbles score HIGH
  (p0011=0.444, p0021=0.467) and tier-3 (best<0.15, :558-563) never fires.
- `pipeline/text_boxes.py`: PP-OCRv3 DBNet det-only ONNX **already vendored**
  (`pipeline/models/text_detection_en_ppocrv3_2023may.onnx`, Apache-2.0,
  sha-checked), ~0.2-0.4 s/panel CPU, tall-panel tiling, sidecar cache
  (`<slug>.textboxes.json`), cv2-classic fallback. `ensure_boxes()` is a
  ready-made API.
- `pipeline/yolo_detect.py`: deepghs/manga109_yolo `v2021.12.30_n_yv11`
  (~10 MB ONNX, onnxruntime CPU, HF-cached) **already vendored**; currently
  filters `label != "text"` at :203-204 but the model emits
  `body, face, frame, text` (confirmed on the HF model card; nano F1≈0.88).
  Weights are Ultralytics-AGPL-3.0 — already recorded in README/ledger at
  gap-004 adoption, so reusing the same checkpoint adds NO new exposure.
- Offender geometry (panels_index.json): 243-608 px tall at 690 px wide —
  strip fragments. Known-good keeps include system-UI panels (scene 23) and
  quoted textonly keeps, so the gate cannot be geometry-only.
- venv: python3.12, opencv-headless, numpy>=2 (faster-whisper), onnxruntime
  1.29, huggingface-hub. dghs-imgutils itself CANNOT come in-process
  (numpy<2 + opencv-contrib collision — documented in yolo_detect.py:6-13).
- Gateway (`pipeline/gateway.py`): `llm_vision()` batching exists; tier-3
  already batches 4 scenes/call.

## Candidate matrix

| # | Candidate | License | New deps | CPU cost | Fit (1-5) | Risk |
|---|-----------|---------|----------|----------|-----------|------|
| C1 | **Build-ourselves: DBNet text-coverage + ink-outside-text + manga109 body/face check** | Apache-2.0 (DBNet) + AGPL weights already accepted | none | ~0.3-0.6 s/panel, picked panels only (~40-100/ch → 20-60 s) | **5** | threshold tuning; SFX Korean glyphs may evade DBNet (mitigated by ink-density + yolo `text` class) |
| C2 | `ogkalu/comic-text-and-bubble-detector` (RT-DETRv2, HF) | **Apache-2.0** | none (onnxruntime already in) — int8 ONNX 11.1 MB | ~0.3-1 s/panel CPU (640² input) | 4 | maintained (last upload 4 mo ago); needs vendored pre/post-process (RT-DETR, not YOLO — new decode code); trained on webtoons incl. manhwa → good `text_free` (SFX) recall |
| C3 | `deepghs/manga109_yolo` all-classes only (no DBNet) | AGPL weights (already accepted) | none | ~0.2 s/panel | 4 | nano recall on colored webtoon bodies unproven; used as *component* of C1 rather than alone |
| C4 | Vision-LLM prompt hardening via Athena (`gateway.llm_vision`) targeted "text-fragment?" check | n/a (gateway) | none | ~2-5 batched calls/chapter (suspects only, 4-8 imgs/call) | 4 | non-deterministic; per-call latency; model may again "narrate the text" — needs a strict rubric prompt; best as *escalation tier*, not primary |
| C5 | `deepghs/anime_classification` (comic/illustration/not_painting, mobilenetv3 4.2 M) | MIT | none (ONNX via hf_hub) | ~50 ms/panel | 2 | classes are page-level (comic vs illustration), not "text-fragment vs art panel"; wrong granularity for bubble cutouts — would misfile most crops as `comic` either way |
| C6 | `kitsumed/yolov8m_seg-speech-bubble` (bubble segmentation) | **GPL-3.0 — flagged, do not shortlist** | — | — | 1 | license; also only bubbles, no SFX/black-card coverage |
| C7 | Tesseract/paddleocr full OCR + confidence | Apache-2.0 | new heavy deps (paddle wheels problematic on py3.12) | slow | 1 | redundant — we don't need to READ text, only measure its area; DBNet already does that |

### C1 (recommended) — deterministic multi-signal gate, no new deps

Per picked panel (verify time, cached in the textboxes sidecar):

1. **text_coverage** = Σ(DBNet line-box areas, clipped/deduped) / panel area.
   This is the gap card's REQUIRED metric. Uses
   `text_boxes.ensure_boxes(<slug>.textboxes.json, picked_paths)` verbatim.
2. **ink_outside_text** = Otsu-ink pixel count with all text boxes (dilated
   ~8 px to eat bubble outlines) masked out, / panel area. Bubble cutouts
   and black cards → near zero; story art → high. Pure cv2, ~10 ms.
   For **black cards** specifically add a histogram-flatness check: ≥85 % of
   pixels within ±12 gray levels of the modal value ⇒ flat card.
3. **has_art** = manga109_yolo detections of class `body` or `face` above
   conf ~0.25 anywhere in the panel (extend `yolo_detect.detect_text_boxes`
   with a `labels=` param, or add `detect_all(regions)` — 5-line change,
   both callers keep behavior).
4. Classification: `effective_textonly` when
   `(text_coverage ≥ T_cov OR flat_card) AND ink_outside_text ≤ T_ink AND
   not has_art`. The `has_art` veto is what protects the deliberately-kept
   system-notification UI panels *if* they contain art; for pure-UI keeps,
   the tier-1 quote-exception path below still protects them.

Wiring into `verify_panels.py`:
- New tier 1.25 after `tier1()`: panels classified `effective_textonly` get
  routed into the EXISTING textonly rule — kept only via
  `_has_verbatim_quote` against **dialogue-only** text; else dropped
  (substitution logic unchanged). This reuses the gate the card says must
  stay, just no longer trusting the OCR kind tag.
- **Quote-exception narrowing** (gap card mechanism 1b): for
  `effective_textonly` panels that are *black narration cards*
  (flat_card=True, no dialogue entries in the read), the quote-exception
  does NOT apply — narration-box text quoted by the narrator is still a
  black card on screen. Speech-bubble cutouts with a quoted character line
  remain keepable (payoff appends preserved — quality guard).
- **Tier-2 proxy fix**: for suspects (fragment geometry: height <450 px at
  full width, OR text_coverage above ~0.2), score `relevance_score` against
  `scene_beat` ONLY (drop dialogue/narration_text from `_panel_text`) so
  quoted text stops masquerading as visual relevance.
- **Forced tier-3**: any suspect that survives 1.25 (e.g. quote-kept) is
  appended to the `ambiguous` list regardless of the scene best-score, with
  a hardened prompt (see C4) — bounded at ≤ ~10 panels/chapter in ch4 terms.
- **Cover/credits**: independent cheap check — panel is within the first 2
  or last 3 reading-order positions of the chapter AND (flat/decorative
  large-type layout: ≤3 text boxes all wider than 40 % of panel width, or
  the OCR kind of ANY read within ±1 index says cover) → force tier-3
  vision on it instead of trusting the possibly-misaligned kind tag. This
  handles the p0075 ±1 misalignment without trusting alignment.
- **Fail open** everywhere: detector error ⇒ treat as story/ok (mirrors
  :328-338); flag `--textonly-gate` / env `MANGA_VERIFY_TEXTONLY=1`, output
  byte-identical when off (quality guard).

Threshold tuning: the ch4 labeled set is ready-made — 11 positives
(p0011, p0021, p0022, p0027, p0040, p0042, p0060, p0062, p0073, p0095,
p0096 — p0075 excluded per acceptance criteria if alignment-dependent) vs
negatives = every panel in the 0-fault final `the-world-after-the-fall-ch4.json`.
Expected starting points: T_cov ≈ 0.35, T_ink ≈ 0.04, tune by sweeping and
requiring 0 false drops on negatives first (acceptance criterion), then
maximizing positives caught (≥9/11 required).

Smoke test (integrator, no installs needed):

```bash
./venv/bin/python3 pipeline/text_boxes.py \
  --panels output/the-world-after-the-fall-ch4/panels \
  --out /tmp/opencode/ch4.textboxes.json \
  --only p0011.png --only p0021.png --only p0023.png
./venv/bin/python3 pipeline/yolo_detect.py \
  output/the-world-after-the-fall-ch4/panels/p0011.png --conf 0.25
# expect: p0011/p0021 boxes cover most of panel; p0023 (story art) low;
# yolo currently prints text boxes only — the experiment extends it to all
# 4 classes and expects zero body/face on p0011, ≥1 body on p0023.
```

Wall-time: DBNet 0.2-0.4 s × ~100 picked panels ≈ 20-40 s + yolo ~0.2 s ×
suspects only ≈ well under the 1.5x script-step budget (and the textboxes
sidecar amortizes across gap-009's render-side use — likely already paid).

### C2 (runner-up) — ogkalu/comic-text-and-bubble-detector

- **What**: RT-DETRv2-r50vd fine-tuned on ~11 k manga/webtoon/manhua/western
  pages; classes `0: bubble, 1: text_bubble, 2: text_free`. `text_free`
  directly covers SFX lettering outside bubbles — the class DBNet is weakest
  on (stylized Korean SFX like p0042/p0095).
- **License**: Apache-2.0 (model card). Maintenance: 17 commits, int8 export
  uploaded 4 months ago, 77 k downloads/mo — healthy.
- **CPU**: `detector-v4-s_int8.onnx` = 11.1 MB; RT-DETR small at 640² is
  ~0.3-1 s/panel CPU. Runs on existing onnxruntime 1.29; NO ultralytics/
  transformers needed if we vendor the RT-DETR decode (input `images`
  float32 1×3×640×640, outputs logits+boxes, sigmoid + top-k — ~40 lines,
  same vendoring pattern as yolo_detect.py). No NMS needed (DETR-style).
- **Gate**: `bubble+text_free coverage ratio ≥ T AND no residual art ink` —
  same downstream wiring as C1; it substitutes for signals 1+3.
- **Risk**: new decode code to validate; int8 quantization behavior on B/W
  manga unverified; 640² resize on tall strips may miss small text (tile
  like text_boxes.py does). Precision on manhwa should be BETTER than
  manga109 (trained on webtoons; manga109 is grayscale Japanese manga).

Install/smoke (download only, ~11 MB):

```bash
./venv/bin/python3 - <<'EOF'
from huggingface_hub import hf_hub_download
p = hf_hub_download("ogkalu/comic-text-and-bubble-detector",
                    "detector-v4-s_int8.onnx")
import onnxruntime as ort
s = ort.InferenceSession(p, providers=["CPUExecutionProvider"])
print(p, [(i.name, i.shape) for i in s.get_inputs()],
      [(o.name, o.shape) for o in s.get_outputs()])
EOF
```

### C4 — Athena vision hardening (kept as the escalation tier inside C1)

Prompt design (tier-3 addendum for suspects): show the panel and ask a
rubric question that cannot be answered by narrating the text:
"Ignore what the text SAYS. Does this image contain any drawn character,
face, object, or scene background — anything besides lettering, speech
bubbles, solid color, or title/credits typography? Answer JSON
{\"panel\":…, \"has_art\": true/false, \"art_fraction\": 0-1}." Drop when
`has_art=false` (fail open on parse errors, same as tier3 today).

Cost: only geometric/coverage suspects escalate — ch4 would have sent ~12
panels ⇒ 2-3 batched calls (8 imgs/call) per chapter, negligible vs the
existing OCR pass (1 call per ~4 panels). Determinism caveat: keep it as
the LAST tier so the deterministic replay acceptance test (ch4, --no-vision)
is satisfiable by C1 signals alone; the card's ≥9/11 target should be met
WITHOUT vision (measured in the experiment), vision only mops up residue.

### C5 / C6 — rejected

- `deepghs/anime_classification` (MIT): page-type classifier
  (comic/illustration/3d/bangumi/not_painting) — granularity mismatch; a
  bubble cutout and a story panel both classify `comic`. Not usable.
- `kitsumed/yolov8m_seg-speech-bubble`: GPL-3.0 → flagged per protocol;
  also bubbles-only (no SFX/black-card coverage). Not shortlisted.
- Full-OCR stacks (paddleocr, tesseract, manga-ocr): unnecessary — the gap
  needs text AREA, not text CONTENT; manga-ocr (Apache) is Japanese-only
  and torch-based (heavy on CPU box, numpy pin risk).

## Ranked list

1. **C1 build-ourselves** (DBNet coverage + ink-outside-text + manga109
   body/face veto + narrowed quote-exception + scene_beat-only tier-2 for
   suspects + forced tier-3, cover check by position+layout) — zero deps,
   deterministic, directly emits the required text-coverage metric, tunable
   on the ch4 labeled set.
2. **C2 ogkalu RT-DETRv2** — Apache-2.0, webtoon-native, one model for
   bubbles+SFX; adopt if C1's ch4 sweep can't reach ≥9/11 @ 0 false drops
   (most likely failure mode: DBNet missing stylized Korean SFX — check
   p0042/p0095 first in the smoke test).
3. **C4 Athena vision rubric** — escalation tier only, ~2-3 calls/chapter.
4. C3 manga109-only — folded into C1 as the has_art signal.
5. C5, C6 — rejected (granularity / GPL).

## Open risks → quality-guard mapping

| Risk | Guard hit | Mitigation |
|------|-----------|------------|
| DBNet misses stylized SFX (p0042/p0095 Korean glyphs) → positives leak | acceptance ≥9/11 | ink-density + flat-card signals catch black cards; manga109 `text` class catches SFX DBNet misses; C2 fallback if still short |
| False drops on kept system-UI panels (scene 23) or quoted bubble keeps | "zero false drops" criterion | has_art veto + quote-exception retained for dialogue-carrying panels; tune thresholds negatives-first |
| OCR↔image ±1 misalignment poisons kind-based decisions | p0075 case | cover check uses POSITION+layout, never the kind tag alone; ±1 index tolerance |
| Wall time | ≤1.5x script step | detectors run on picked panels only (~40-100), sidecar-cached; ~1 min worst case |
| Detector crash deletes panels | fail-open guard | any per-panel error ⇒ story/ok, logged (mirrors verify_panels.py:328-338) |
| Flag off must be byte-identical | byte-identical guard | gate + proxy change + forced escalation all behind one flag; `_verify.params` records it only when on |
| AGPL weights concern resurfacing | ledger | no NEW exposure — same checkpoint, same usage mode as gap-004; note in report |
