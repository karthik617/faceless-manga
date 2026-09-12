# exp-004 prototype: text-box-guarded webtoon splits (`--split-guard`)

Implements research §3 (recommended B+A hybrid): mm14a-style Laplacian cut
energy ranks forced-cut rows, and detected text/bubble boxes VETO rows so a
forced cut can never land through a speech bubble. Near-black dramatic beats
are kept (tagged `note:"beat"`) instead of dropped. All of it dark behind an
opt-in flag; default output is byte-identical.

## Files changed

| File | Change |
|---|---|
| `pipeline/segment_panels.py` | +~200 lines, all guard-gated. New: `_blob_text_boxes`, `_yolo_text_boxes_batch`, `_guard_text_boxes`, `_forbidden_rows_multi`, `_best_legal_cut`; forced-split loop consults the veto when `guard != "none"`; both `MIN_CONTENT_FRAC` skip sites emit `note:"beat"` panels under a guard; `--split-guard` CLI flag / `FM_SPLIT_GUARD` env. `guard=="none"` skips every new code path. |
| `improvements/experiments/exp-004-segmentation/yolo_detect.py` | NEW: quarantined detector script, runs ONLY under the experiment venv. Accepts N images per call (model import dominates wall time), windows tall regions (1280/1024 stride = 256 px overlap), merges cross-window duplicates (IoU>0.3), prints JSON `text` boxes. |
| `improvements/experiments/exp-004-segmentation/check_splits.py` | NEW: deterministic acceptance gate. Rebuilds the stitched strip, classifies interior cuts as forced (non-gutter) via the splitter's own gutter test, detects text boxes with the same guard detectors, exits 1 if any forced cut intersects a box (`--pad` adds margin). |

NOT touched: `~/faceless-youtube/pipeline/make_video.py`,
`pipeline/review_video.py`, `manga.py`, `pipeline/requirements.txt`,
`output/the-world-after-the-fall-ch2/` (golden — ch2 has no local raw/, so
smoke ran on a COPY of ch3's raw strips under `work/raw_strip`).

## Flag

- `segment_panels.py --split-guard {none,blob,yolo}` — default `none`
  (byte-identical legacy behavior; verified below). Env: `FM_SPLIT_GUARD`.
- `blob`: pure-OpenCV heuristic, zero new deps. Text = dark strokes whose
  neighborhood is bright (ink-on-bubble), letters fused morphologically,
  blocks verified by bright/ink fractions. (The research §4 flat-field-CCL
  design detected 0/4 real bubbles in smoke — text strokes carve holes so
  fill-ratio collapses — and was replaced by this ink-near-bright variant,
  which finds all known-dialogue panels' bubbles. DEVIATION from research.)
- `yolo`: deepghs/manga109_yolo `text` class via a quarantined subprocess in
  the experiment venv. DEVIATION: model `v2021.12.30_n_yv11` @ conf 0.2, not
  the researched `v2023.12.07_n_yv11` @ 0.35 — the 2023 checkpoints return
  ZERO text detections on colored webtoon bubbles even at conf 0.1 (research
  §5 risk confirmed for those weights); the 2021 nano finds them at 0.27-0.30.
- Cut placement (both guards share it): rank rows in the search band by
  `E = mean(spread[9-row window]) − 4.0·(2g(y)−g(y−1)−g(y+1))` over LEGAL
  rows only (not within 16 px of a text box); no legal row → widen band to
  1.25×max_slice, then upward; still none → legacy flattest-row cut + log.
  Detector runs ONLY inside the >2600 px slices needing forced cuts, batched
  into one subprocess for yolo.
- Beats: with a guard on, sub-`MIN_CONTENT_FRAC` slices are emitted with
  `"note": "beat"` (existing optional key — schema unchanged) instead of
  being dropped.

## Deps (experiment venv ONLY)

```
improvements/experiments/exp-004-segmentation/venv   (python3.12)
  pip install dghs-imgutils onnxruntime              # both MIT
```
`pipeline/requirements.txt` untouched. The pipeline venv shells out to this
venv for `yolo` mode; if the venv/script/model is missing or errors, it logs
and falls back to the blob guard (verified with `FM_YOLO_PY=/nonexistent`).
Override paths: `FM_YOLO_EXP_DIR`, `FM_YOLO_PY`. First yolo run downloads the
ONNX (~10 MB) to `~/.cache/huggingface`.

## Fallback chain (never crashes)

yolo subprocess error → blob boxes → no legal row in band → widened band →
legacy flattest-row cut (logged `! forced cut may cross content`).

## Smoke results (ch3 raw copy: 19 common-width tiles → 690×129855 strip; plus
a synthetic 4000 px gutterless strip with a full-width caption box at rows
1850-2150 planted in the forced-cut band)

Byte-identical default: `--split-guard none` (and no flag) on all 21 ch3 raw
pages → `panels_index.json` and every PNG identical to pre-change output
(diff -rq = 0 differences; 95 panels).

| run | panels | beats kept | forced cuts | cuts∩text (pad 0/16) | wall |
|---|---|---|---|---|---|
| strip, none | 91 | 0 (1 dropped) | 3 | blob-det: 0 / **1** (`p0005` cut y=5685 grazes box y=5699) · yolo-det: 0/0 | 2.5 s |
| strip, blob | 92 | 1 | 3 | 0 / 0 | 2.5 s (1.0x) |
| strip, yolo | 92 | 1 | 3 | 0 / 0 (yolo-det) | 7.5 s (2.9x) |
| synth, none | 1 | 0 | 1 | cut y=1848 lands ON the caption top border (see samples) | — |
| synth, blob | 2 | 1 | 1 | 0 — cut moved to y=1731, 119 px clear | — |
| synth, yolo | 2 | 1 | 1 | 0 — cut at y=2146 (below caption; yolo missed the synthetic Hershey text, blob veto not consulted in yolo mode there — cut still legal by both detectors) | — |

Panel counts: 92 vs 91 = +1.1% (well inside ±30%; the only delta is the kept
beat). Wall-time: yolo 2.9x ≤ 3x budget (batched subprocess; was 4.7x with
one subprocess per slice before batching). Reading order and index schema
unchanged.

Samples: `samples/cut_{none,blob,yolo}_y*.png` (real strip, red line = cut),
`samples/synth_*_cut_y*.png` (synthetic caption-box demo). Per-mode indexes
kept at `work/indexes/*.panels_index.json`.

## Evaluator commands

```bash
# stage-scoped experiment on a raw copy (never the golden dir itself):
EXP=improvements/experiments/exp-004-segmentation
./venv/bin/python3 pipeline/segment_panels.py --raw $EXP/work/raw_strip \
    --out /tmp/opencode/seg_blob --mode webtoon --split-guard blob
./venv/bin/python3 pipeline/segment_panels.py --raw $EXP/work/raw_strip \
    --out /tmp/opencode/seg_yolo --mode webtoon --split-guard yolo

# acceptance gate (exit 0 = zero forced cuts through text boxes):
./venv/bin/python3 $EXP/check_splits.py --raw $EXP/work/raw_strip \
    --index /tmp/opencode/seg_blob/panels_index.json --detector blob --pad 16
./venv/bin/python3 $EXP/check_splits.py --raw $EXP/work/raw_strip \
    --index /tmp/opencode/seg_yolo/panels_index.json --detector yolo --pad 16

# byte-identical default check:
./venv/bin/python3 pipeline/segment_panels.py --raw <raw> --out /tmp/opencode/seg_off --mode auto
diff /tmp/opencode/seg_off/panels_index.json <pre-change index>
```

For the golden chapter (ch2 raw not present locally): re-download ch2 raw into
a temp project copy, segment with each guard, then re-run the downstream
render+review to count `cropped_content` faults (target 6 → ≤2).

## v2 guard extensions (post-downstream-benchmark iterate)

Targets the 3 residual segmentation faults from report.md §downstream:
forced cuts through text-free ART (bench scenes 15/21) and a gutter cut
clipping tall stylized SFX (scene 16). Same flag (`--split-guard blob|yolo`),
same byte-identical default; all v2 code sits behind `guard != "none"`.

### Design chosen: ink-density ceiling + gutter-edge snapping (NOT yolo classes)

The approved alternative — yolo `body`/`face` class vetoes — was probed
first and REJECTED empirically: at both art-cut fault sites the
`v2021.12.30_n_yv11` model returns **zero** body/face detections (scene 15:
only a `frame` box spanning the whole search band, useless as a veto; scene
21: nothing at all; scene 16 SFX: undetected). The failing rows instead have
an unmistakable pixel signature: **ink density 0.90/0.93** (fraction of
pixels with gray < 110) vs a strip median of 0.46 — flat DARK art reads as
low-spread to the v1 energy, so it actively preferred those rows. Hence:

1. **ART veto = ink ceiling** (`pipeline/segment_panels.py::_best_legal_cut`):
   rows with ink > `INK_CEIL=0.55` are ineligible, and `INK_W=120` adds an
   ink term to the cut energy so the least-inked legal row wins. Softer than
   the text veto: the pick ladder is band+ink → widened+ink → band-no-ink →
   widened-no-ink → legacy fallback — cutting art beats cutting a bubble,
   and an all-dark dark-mode strip still always gets a cut (no deadlock).
2. **Soft-cap escape** (`CLEAN_INK=0.20`): if a slice would fit under the
   existing +25% soft cap and even the best LEGAL row is dirtier than 0.20
   ink, skip the forced cut and emit the slice whole. Scene 15 is a 2628-px
   full-page drawing (cap 2600) with no clean row anywhere in or beyond the
   band — any cut through it is a cropped_content fault; 28 px over cap is
   not. Only fires under the soft cap, so gutterless 10k-px stretches still
   get cut (slice height stays bounded by 1.25×cap).
3. **Gutter-edge snapping** (`_snap_gutter_edges`, called at the end of
   `segment_strip_webtoon`): for slices taller than `OVER_TALL=1.5`×median
   (the report's cheap scope), walk outward from each edge through the
   adjacent "gutter" and pull the edge across any gutter-classified rows
   whose min/max spread exceeds `SNAP_SPREAD=40` (real marks — SFX letter
   tips pass the mean≥245 gutter test but have spread ~140). `SNAP_GAP=8`
   empty rows are tolerated inside an SFX (letter tips aren't contiguous);
   `SNAP_PAD=4` margin is kept. Snapping only GROWS a slice into empty
   gutter (walk stops at the neighboring slice), so content is never lost,
   order never changes, and no fallback is needed. Detector-free — this is
   the text/SFX veto generalized to gutter cuts without paying yolo cost on
   every gutter.

Thresholds (all constants at the top of segment_panels.py, exp-004 v2
block): `INK_GRAY=110, INK_CEIL=0.55, INK_W=120, CLEAN_INK=0.20,
SNAP_SPREAD=40, SNAP_GAP=8, SNAP_PAD=4, OVER_TALL=1.5`. `INK_CEIL` was set
so the ch2 fault bands keep ≥25% legal rows; `CLEAN_INK` separates true
gutters/whitespace (≤0.11 at every good v2 cut) from art (0.30–0.51 at the
4 kept-whole slices).

### check_splits.py v2

Two new audits, on by default (`--no-art` / `--no-gutter` restore the v1
gate); thresholds imported from segment_panels so the gate can't drift:
- **ART**: forced cuts whose row ink > `INK_CEIL`.
- **GUTTER**: over-tall slices with inky gutter rows (spread >
  `SNAP_SPREAD`) within `SNAP_PAD+2` px outside an edge.
v2 acceptance = zero violations across text ∩ art ∩ gutter audits.

### v2 smoke results (fresh ch2 raws, /tmp/opencode/exp004_ch2/raw)

- **Byte-identical default (re-verified after v2)**: `--mode auto`, no flag →
  `panels_index.json` and all 154 PNGs byte-identical to the golden panels;
  `--mode webtoon`, no flag → index identical to the pre-v2 plain run.
- **The 3 residual sites, `--mode webtoon --split-guard yolo`**:
  - scene-15 art cut y=65965 (ink .90): **removed** — soft-cap escape keeps
    the 2628-px drawing whole (p0041, h=2697 after snap). First attempt with
    ceiling-only moved it to y=66079 (ink .51) which still crossed the SFX —
    that's what motivated `CLEAN_INK`.
  - scene-21 art cut y=97903 (ink .93): **relocated** to y=96779 (ink .11),
    a whitespace band above the drawing.
  - scene-16 gutter cut y=67407 (clipped "SLA…" SFX): slice top **snapped
    −40 px to y=67367**, above the SFX tips (row spread 0 at the new edge).
- **Acceptance gate**: v2 index → `forced∩text 0, art 0, gutter 0`, exit 0.
  Cross-check: the v1 bench index under the v2 gate correctly FAILS with 6
  art + 20 gutter violations including all three fault sites.
- **Wall time**: plain 3.82 s → yolo v2 8.18 s = **2.14x** (≤3x budget;
  down from v1's 2.5x because 4 soft-cap escapes skip detector-consulting
  re-cuts).
- **Panel count**: 115 (114 slices + 1 beat) vs plain 117 — **−1.7%**
  (4 slices kept whole under the escape merged 3 would-be cuts; +1 beat).
  Max slice height 3122 px = 1.20×cap, inside the 1.25 soft cap.
- **Fallback**: `FM_YOLO_PY=/nonexistent` → logs `! yolo guard unavailable
  … falling back to blob`, completes with 116 panels, no crash.
- 26 gutter-edge snaps fired strip-wide, all ≤456 px; largest
  (y=160042 −456 px) recovers a big white-on-white "WOO" SFX verified by
  eyeball (`samples/ch2/v2_snap_y160042_ctx.png`).

Samples: `samples/ch2/v2_s15_artcut_y65965.png`,
`v2_s21_artcut_y97903.png`, `v2_s16_gutterSFX_y67407.png`,
`v2_snap_y160042_ctx.png` (red = v1 cut, blue = v2 cut/edge).

### v2 re-benchmark commands (evaluator)

Reuse the existing bench project's script/OCR/TTS so only cut sites change
(clean A/B, no script lottery — report.md §downstream recommendation 4).
The bench project `/tmp/opencode/exp004_bench/proj` must NOT be mutated;
work on a copy:

```bash
EXP=improvements/experiments/exp-004-segmentation
B=/tmp/opencode/exp004_bench          # v1 bench (do not delete/modify)
R=/tmp/opencode/exp004_bench_v2 && mkdir -p $R
cp -r $B/proj $R/proj                  # carries raw, script json, ocr json, TTS cache

# re-segment with the v2 guard (overwrites the copied panels only)
rm -rf $R/proj/panels
./venv/bin/python3 pipeline/segment_panels.py --raw $R/proj/raw \
    --out $R/proj/panels --mode webtoon --split-guard yolo

# v2 acceptance gate (must exit 0 before rendering)
./venv/bin/python3 $EXP/check_splits.py --raw $R/proj/raw \
    --index $R/proj/panels/panels_index.json --detector yolo --pad 16

# CAUTION: panel ids shifted (115 vs v1's 118 — 3 cuts merged, ids after
# p0040 renumbered). Remap the script's panel references by strip-y overlap
# of old vs new bboxes (old index: $B/proj/panels/panels_index.json) before
# rendering; scenes must NOT be re-scripted or the A/B breaks.

# re-render review cut with the SAME flags as the v1 bench, then stable review
./venv/bin/python3 manga.py --project $R/proj --from render --stop-after review-cut
./venv/bin/python3 pipeline/review_stable.py --video $R/proj/<slug>.mp4 \
    --script $R/proj/the-world-after-the-fall-ch2.json --out $R/review.json

# score against the SAME baseline as v1 (stable_base), and against v1's
# stable_seg.json to isolate the v2 delta
./venv/bin/python3 pipeline/benchmark.py --experiment $EXP --review $R/review.json
```

Expected: the 3 segmentation-attributable cropped_content faults (scenes
15/16/21) gone; scene-28 (render framing) and scene-3 (script pick) faults
are NOT segmentation's and should be judged out of scope for gap-004.

## Deviations from the research design

1. **Blob heuristic redesigned** (flat-field CCL → ink-near-bright-field):
   the researched filters (fill>0.55 + edge-density band) rejected every real
   bubble on ch3 panels because lettering carves the flat field into low-fill
   fragments. New variant detects bubbles on all known-dialogue smoke panels.
   Trade-off: noisier (304 boxes on the full strip, some false positives on
   bright snowy art) — acceptable for a veto, since false positives only
   shrink the legal-row pool and the widen/fallback path guarantees a cut.
2. **YOLO model/conf**: `v2021.12.30_n_yv11` @ 0.2 instead of
   `v2023.12.07_n_yv11` @ 0.35 (2023 weights blind to webtoon text — the
   research's own §5 risk, confirmed empirically).
3. **yolo runs as a quarantined subprocess** in the experiment venv rather
   than an in-venv import, because the hard rule "deps in experiment venv
   only" forbids installing dghs-imgutils into the pipeline venv at prototype
   stage. At adoption the import can move in-process.
4. **Detection batching** (all over-tall slices in one subprocess call) added
   to meet the 3x wall-time budget; not in the research doc.

## ADOPTED 2026-09-09 (production default)

Human sign-off received 2026-09-09 acknowledging the formal benchmark FAIL
verdicts and their attribution (reviewer noise + out-of-scope render framing).
Adoption mechanics executed per report.md "Final adopt recommendation":

- **Default flipped in `manga.py`**: new orchestrator arg
  `--split-guard {none,blob,yolo}`, default **yolo**, passed to
  `segment_panels.py` by `step_segment`. Opt-out: `--split-guard none`
  (byte-identical legacy splits, verified against the golden panels index).
  The module CLI's own default stays `none` (gap-002 convention).
- **Auto-mode majority vote** (report recommendation option b) added in
  `segment_panels.segment()`, guard-gated: with any guard on, the chapter
  mode is voted by majority over all pages instead of trusting page 1, so a
  cover page can no longer disable tile stitching (the root cause of the
  golden ch2 faults). `--split-guard none` keeps the legacy first-page guess.
- **Deviation 3 discharged — detector moved in-process**: quarantined
  subprocess replaced by `pipeline/yolo_detect.py`. The full dghs-imgutils
  package could NOT be installed into the pipeline venv (requires numpy<2,
  conflicting with faster-whisper's numpy>=2, and opencv-contrib-python,
  whose cv2 collides with opencv-python-headless), so the yolo
  pre/postprocess glue was vendored (MIT) and runs on onnxruntime==1.29.0
  (MIT) + huggingface-hub>=1.29 (Apache-2.0) — both were already in the venv
  via faster-whisper and are now direct pins in pipeline/requirements.txt.
  Model weights (deepghs/manga109_yolo v2021.12.30_n_yv11) declare
  Ultralytics AGPL-3.0 in their embedded metadata; downloaded at runtime,
  never redistributed — recorded in README + ledger.
- **Equivalence proofs** (ch2 raw copy, /tmp/opencode/adopt_smoke):
  in-process detector boxes diff-identical to the experiment subprocess on 3
  sample tiles; adopted default run (`--mode auto --split-guard yolo`,
  majority vote -> webtoon, 97 tiles stitched) produces a panels dir
  **byte-identical** to the experiment-venv `--mode webtoon --split-guard
  yolo` run (115 panels); acceptance gate exit 0 (text/art/gutter 0/0/0);
  `--split-guard none` byte-identical to pre-change output AND to the golden
  panels index; offline fallback (`HF_HUB_OFFLINE=1`) logs and degrades to
  blob, completes with 116 panels, no crash.
- The experiment venv + `yolo_detect.py` here are retained for
  reproducibility of the recorded runs but are no longer on any production
  path (`FM_YOLO_EXP_DIR`/`FM_YOLO_PY` env overrides removed with the
  subprocess path).
