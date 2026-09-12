# exp-004 evaluation report — split-guard on golden ch2 raws (segment-stage scope)

**Evaluator verdict: PASS-leaning-iterate (guard = yolo), with one decisive
side-finding** — the golden chapter's known `cropped_content` faults are NOT
forced-cut faults at all; they are **tile-edge cuts caused by `--mode auto`
failing to trigger the tile stitcher** (cover page is guessed `manga`, so
`pmode0 != webtoon` and stitching is skipped for the whole chapter). The
split guard is correct and passes its gate, but the render+review benchmark
must run with stitching active (`--mode webtoon`, or fix the auto-mode
vote) or the 6→≤2 acceptance criterion cannot possibly be met.

Golden project protection: `output/the-world-after-the-fall-ch2/` was never
written to. All runs used a fresh raw copy in `/tmp/opencode/exp004_ch2/raw`
and output dirs under `/tmp/opencode/seg*`. (Approach: copy, not
backup/restore.)

## 1. Raw re-download — SUCCESS, and byte-exact

- Source recovered from `channel_state.json` → MangaDex title
  `14b81959-13ed-46eb-a277-da66f672acb5`; API lookup found exactly one EN
  chapter-2 release: chapter id `9a7f68b6-fdb8-4280-b93b-32448c5501e0`,
  **98 pages** — matching the golden index (max page 98).
- Downloaded via the pipeline's own backend:
  `pipeline/download_chapter.py --backend mangadex` → 98 pages in
  `/tmp/opencode/exp004_ch2/raw` (1 cover 1778×1000 + 97 tiles @ width 690,
  88 of them 690×2000).
- **Source not re-encoded**: re-segmenting these raws with no guard
  reproduces the golden panels **byte-identically** (see §2), so all
  comparisons below are directly against the true golden baseline.

## 2. Byte-identical default check — PASS

```
segment_panels.py --raw <fresh raw> --out /tmp/opencode/seg_off --mode auto   (no flag)
```
- `panels_index.json`: `cmp` byte-identical to
  `output/the-world-after-the-fall-ch2/panels/panels_index.json`.
- All **154/154 panel PNGs byte-identical**, 0 differing.

This simultaneously proves (a) the default code path is unchanged by the
prototype, and (b) the fresh download is the same encode that produced the
golden panels.

## 3. THE mode-auto finding (why the golden faults exist)

Golden ch2 was segmented with `--mode auto` (per project metadata and
`manga.py` step 2). The stitching trigger is
`pmode0 = _guess_mode(imgs[0])` — page 1 is the 1778×1000 cover, guessed
`manga`, so `stitched=False` and **every 690×2000 tile was segmented
independently**. Consequences measured on the fresh raws:

- 99 of 154 golden webtoon panels have a bbox touching a tile edge (y=0 or
  y=2000) — i.e. cuts placed by the tile boundary, not by any gutter or
  flattest-row logic.
- Max panel height in auto mode is 2000 px < the 2600 px forced-cut
  threshold → **the forced-split code path (the thing the guard protects)
  never executes on ch2 in auto mode**. With `--split-guard blob|yolo` in
  auto mode the only delta is +3 kept `note:"beat"` panels (154→157); all
  non-beat panels are identical to baseline.
- The three known fault sites map exactly to tile boundaries:
  - scene 6 face crop = p0026/p0027 boundary at page19|20 edge (strip
    y=34976) — sample `samples/ch2/heal_scene6_tileedge_y34976.png` shows
    the red tile-edge line slicing the character's face/jaw.
  - scene 7 partial-SFX = p0030–p0033, tiles 21–23 sliced at raw edges
    through a full-strip action spread
    (`samples/ch2/heal_scene7_tileedge_y38976.png`).
  - scene 20 "TICED." = p0122/p0123 boundary at page80|81 edge (strip
    y=149567) — `samples/ch2/heal_scene20_TICED_tileedge_y149567.png`
    shows the red line clipping the top of the "THIS STONE… / …NOTICED."
    bubble chain.

**When stitching runs** (forced `--mode webtoon`: 97 tiles → one 690×183419
strip), all three fault locations fall INSIDE slices — no cut within 100+ px
of any of them, in the guarded runs *and* in the unguarded webtoon baseline.
The healer for the golden faults is the stitcher (already in HEAD), not the
guard; the guard's job is to protect the 10 forced cuts stitching then
requires.

## 4. Guard runs on the stitched strip (--mode webtoon)

| run | panels | strip slices | forced cuts | beats kept | wall |
|---|---|---|---|---|---|
| none | 117 | 116 | 10 | 0 (1 empty dropped) | 3.81 s |
| blob | 118 | 117 | 10 | 1 (`p0002` HUFF… beat) | 3.84 s (**1.0x**) |
| yolo | 118 | 117 | 10 | 1 (same) | 9.60 s (**2.5x**) |

Panel-count guard: +0.9% (webtoon) / +1.9% (auto) — far inside ±30%.
Index schema and reading order unchanged (beat uses the existing optional
`note` key). Wall-time budget ≤3x: **both pass** (blob 1.0x, yolo 2.5x).

### Cut-line movement (guard vs none, same strip)

blob moved 7 of 10 forced cuts; yolo moved 8. Most moves are ±1–5 px
(energy-ranking refinement); the meaningful ones:

| strip y (none) | blob | yolo | note |
|---|---|---|---|
| 56990 | 56654 (**−336**) | 56988 (−2) | none/yolo cut grazes top edge of a blob text box (233,56987–57323); blob relocates well clear |
| 65865 | 65965 (+100) | 65965 (+100) | both guards move off a busy art row (see sample cut2) |
| 85849 | 85849 (fallback) | 85851 (+2) | blob found **no legal row** (a 1628-px-tall blob "box" spans the whole band — giant red SFX region, mostly a false-positive envelope) and logged `! forced cut may cross content`, falling back to the legacy row; yolo sees no text there and keeps the cut |
| 17206/22865/97902/114707/178849 | ±1–5 px | ±1–5 px | micro-refinements, visually neutral |

## 5. Acceptance gate (check_splits.py, --pad 16)

| index under test | detector | forced∩text | exit |
|---|---|---|---|
| none (baseline) | blob | **3** (p0037 y=56990; p0054 y=85849 ×2 boxes) | 1 |
| none (baseline) | yolo | 0 | 0 |
| **blob guard** | blob (its own) | **2** (p0055 y=85849 — the logged fallback) | **1 FAIL** |
| blob guard | yolo | 0 | 0 |
| **yolo guard** | yolo (its own) | **0** | **0 PASS** |
| yolo guard | blob | 3 (85851 fallback-region + 56988 grazing a box top edge within pad) | 1 |

- **yolo guard passes its own gate: zero forced cuts through yolo-detected
  text boxes.** This is the gap-004 deterministic acceptance criterion,
  met.
- **blob guard fails its own gate**, but only at the y≈85849 fallback where
  its own detector produced a full-band 1628-px box (false-positive
  envelope around a huge stylized SFX + white art — see
  `samples/ch2/cut4_y85849_fallback.png`; the cut actually passes through
  art whitespace between the SFX and the dragon, and yolo confirms no real
  text there). This is the known blob noise trade-off from
  IMPLEMENTATION.md deviation 1 biting on real data.
- Baseline none-run had 3 blob-detected violations; blob guard reduced to 2
  (fixed the real one at 56990), yolo guard has 0 on its own detector.

## 6. Sample evidence (`improvements/experiments/exp-004-segmentation/samples/ch2/`)

Red = no-guard cut, green = blob cut, blue = yolo cut.

| file | what to look at |
|---|---|
| `cut1_y56990_moved.png` | red/blue lines sit right at the top border of the inset battle panel (blob box starts y=56987); green (blob) line 336 px higher in clean art — the guard's clearest win |
| `cut2_y65865_moved.png` | both guards move the cut +100 px off a cluttered armor row onto a calmer band |
| `cut3_y17206_moved.png`, `cut5_y114707_moved.png` | ±3–5 px micro-moves, visually neutral (evidence the energy ranking doesn't wreck already-good cuts) |
| `cut4_y85849_fallback.png` | the blob-gate failure: cut crosses whitespace/art below a giant red SFX; there is no dialogue text here (yolo: 0 boxes) — a blob false-positive envelope, not a real fault |
| `beat_y1444.png` | the kept `note:"beat"` panel — a "HUFF…" bubble on white that legacy dropped as low-content; keeping it is correct (it carries dialogue) |
| `heal_scene6_tileedge_y34976.png` | golden fault site: red tile-edge line through the character's jaw/neck — the scene 6 "face cropped" fault; no cut anywhere near this row in stitched mode |
| `heal_scene7_tileedge_y38976.png` | golden fault site: tile edges slicing the full-page action spread (scene 7 partial-SFX frames) |
| `heal_scene20_TICED_tileedge_y149567.png` | golden fault site: red line clipping the bubble chain that produced the "TICED." OCR fragment |

## 7. Acceptance criteria checklist (gap-004)

| criterion | status |
|---|---|
| cropped_content 6 → ≤2 on golden chapter | **not measurable in this scope** (needs render+review). Mechanistically promising: all 3 known fault sites are healed in stitched mode. But the heal comes from stitching, not the guard — the follow-up MUST segment stitched or the count will not move at all. |
| zero splits through detected text boxes (deterministic) | **MET for yolo guard** (own-detector gate exit 0). **NOT met for blob guard** (1 fallback site; its own detector's false-positive box). |
| segment wall-time ≤3x | **MET**: blob 1.0x, yolo 2.5x. |
| panel count ±30% | MET (+0.9% / +1.9%). |
| reading order + schema unchanged | MET (byte-identical default; beats reuse existing `note` key). |

## 8. Recommendation

**Take the `yolo` guard into the render+review benchmark**, with blob
remaining the automatic fallback (already wired). Reasons: yolo is the only
guard that passes its own acceptance gate; its 20 boxes on the ch2 strip are
all real text (vs 373 blob boxes with large false positives that both cause
the blob gate failure and occasionally shove cuts unnecessarily); 2.5x wall
time is inside budget and absolute cost is ~6 s/chapter.

**Blocking prerequisite for the benchmark**: the guarded segmentation must
actually stitch. Two options — (a) run the benchmark with explicit
`--mode webtoon` (matches how ch2 content really is; zero code change), or
(b) fix the auto-mode vote (majority mode over common-width pages instead of
`imgs[0]`) as a companion one-liner. Option (a) is the clean stage-scoped
experiment; note that any fault reduction will then be attributable to
stitching+guard jointly, and the report for adoption must say so.

### Follow-up commands (full downstream benchmark)

```bash
EXP=improvements/experiments/exp-004-segmentation
RUN=$EXP/run && mkdir -p $RUN
# project copy (never the golden dir): reuse fresh raws + golden script/ocr
cp -r /tmp/opencode/exp004_ch2/raw $RUN/raw
cp output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.json \
   output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.ocr.json $RUN/

# experiment segmentation (stitched + yolo guard)
./venv/bin/python3 pipeline/segment_panels.py --raw $RUN/raw \
    --out $RUN/panels --mode webtoon --split-guard yolo
# NOTE: panel filenames/ids change under stitching (117-slice strip vs 154
# per-tile panels) — the script's panel references must be remapped before
# render, or the script stage re-run (`script_from_panels.py`) on the new
# panels. Decide with orchestrator; remap keeps narration identical (cleaner
# A/B), re-run measures the true end-to-end path.

# render review cut + stable review (never single-pass, never --apply-fixes)
./venv/bin/python3 manga.py --project $RUN --from render --stop-after review-cut
./venv/bin/python3 pipeline/review_stable.py --video $RUN/<slug>.mp4 \
    --script $RUN/the-world-after-the-fall-ch2.json --out $RUN/review.json

# score
./venv/bin/python3 pipeline/benchmark.py --experiment $EXP --review $RUN/review.json
```

## 9. Honest caveats

- This pass measured segmentation geometry only: **no evidence yet** on
  downstream `cropped_content`/`empty_screen`/`unreadable_text` fault
  counts, TTS, pacing, loudness, or reviewer perception of the 117-panel
  (vs 154) layout — the stitched panels are taller/differently shaped and
  will change Ken Burns framing everywhere, so global review scores may
  move for reasons unrelated to the guard. Scene-scoped scoring
  (`--scenes-changed 0,6,7,18,20`) is essential in the follow-up.
- The stitched-mode panel set does not map 1:1 to the golden script's panel
  references; the follow-up has a real remap/re-script decision to make
  (flagged in commands above).
- The blob gate failure is judged benign here by cross-checking with yolo
  and visual inspection of one site; on other chapters blob's false-positive
  envelopes could push cuts into worse places. That's another reason the
  recommendation is yolo-primary.
- yolo detector coverage: 20 boxes on 10 forced-cut bands only (by design it
  never scans the whole strip); undetected stylized SFX could still be cut —
  none observed on ch2's forced cuts.

## downstream benchmark (re-script on stitched panels)

**Verdict (binding, benchmark.py): FAIL — score improved 126 → 106 (Δ −20)
but vetoes fired** (`metrics_global.json`): new fault type `static_scene`,
and worsened counts `empty_screen 1→2`, `irrelevant_panel 12→13`,
`cropped_content 4→5`. **However, the gap's causal mechanism — tile-edge
slice faults — is demonstrably eliminated** (audit below); the veto drivers
are fresh-script confounds plus a residual forced-cut-through-ART class the
text-only guard does not cover. Global-only scoring, per plan:
`--scenes-changed` is meaningless on a re-scripted chapter (35 scenes vs the
golden 25; different narration, panel picks and pacing everywhere).

Golden project protection: never touched. Fresh project built in
`/tmp/opencode/exp004_bench/proj/` from the byte-exact raw copy (approach:
copy, not backup/restore). Baseline review reused:
`round2-eval/stable_base.json` (review-cut, majority-of-3).

### What ran (mirrors manga.py step order, HEAD defaults)

1. `segment_panels.py --mode webtoon --split-guard yolo` → **118 panels**
   (117 slices + 1 kept beat), 10.6 s.
2. `script_from_panels.py --series "The World After the Fall" --chapter 2
   --mode webtoon --channel PanelBreak` — fresh vision OCR of all 118 panels
   + fresh recap script (35 scenes; 6 non-story panels excluded), then
   `verify_panels.py` as production default → **0 drops / 0 substitutions**.
3. `panel_render.py --res 1080p --no-music --layout smart --stop-after
   review-cut` (same flags as the stable_base baseline render) → review cut
   865.3 s @1920×1080, 0 TTS retries.
4. `review_stable.py` ×3 → `stable_seg.json/.md`: **28 confirmed
   (17H/10M/1L), 6 unconfirmed-high**.
5. `benchmark.py --baseline round2-eval/stable_base.json` →
   `metrics_global.json`.

### Score table (global, review-cut stable reviews)

| fault type | baseline (stable_base) | experiment | Δ |
|---|---|---|---|
| irrelevant_panel (H) | 12 | 13 | +1 ⚠ veto |
| missing_payoff (H) | 5 | 2 | −3 |
| watermark (H) | 4 | 2 | −2 |
| weak_hook (H) | 1 | 0 | −1 |
| **cropped_content (M)** | **4** | **5** | **+1 ⚠ veto** |
| empty_screen (M) | 1 | 2 | +1 ⚠ veto |
| phone_readability (M) | 2 | 2 | 0 |
| unreadable_text (L) | 1 | 1 | 0 |
| panel_reuse (L) | 1 | 0 | −1 |
| static_scene (M) | 0 | 1 | NEW ⚠ veto |
| **weighted score** | **126** | **106** | **−20 (better)** |

Gap-card criterion `cropped_content 6 → ≤2`: **NOT MET on raw counts**
(pinned full baseline 6 / review-cut stable baseline 4 → experiment 5), but
see the audit — the counted faults are a different fault class than the one
the gap describes.

### Tile-edge fault audit — the original fault class is GONE

Geometric check (panels_index vs the three golden fault sites):

| golden fault site | strip y | nearest cut in experiment index | now inside |
|---|---|---|---|
| scene-6 face crop | 34976 | 244 px away | p0024 (733 px from top) |
| scene-7 partial SFX | 38976 | 1113 px away | p0027 (mid-panel) |
| scene-20 "TICED." bubble | 149567 | 259 px away | p0096 (1387 px from top) |

Frame evidence (all three healed, `samples/ch2_bench/`):
`healed_scene6class_p0024_t187.png` (full face + intact "LAST FLOOR" bubble),
`healed_scene7class_p0027_t231.png` (full Room-of-Flames monster spread, SFX
complete), `healed_scene20class_p0096_t645.png` (full "SO YOU NOTICED." /
"THIS STONE…" bubble chain — the exact text the golden "TICED." fragment came
from, now rendered whole). **Zero faults in the experiment review are cuts of
a face/bubble/SFX at a horizontal tile boundary.**

### What the remaining 5 cropped_content faults actually are

| scene | t | cause class | evidence |
|---|---|---|---|
| 15 (3/3 votes) | 359.2 | **forced cut through ART** — p0042\|p0043 boundary at strip y=65965 cuts a full-page action drawing (text-free; guard-legal) | `seg_fault_t359.2.png`, `cut_y65965_scene15_16_context.png` |
| 21 (2/3) | 513.4 | **forced cut through ART** — p0061\|p0062 at y=97903, motion-blur action band, no text | `seg_fault_t513.4.png`, `cut_y97903_scene21_context.png` |
| 16 (3/3) | 386.1 | **gutter cut through tall stylized SFX** — p0044's top edge clips "SLASH"/"SPIN" typography whose letter gaps read as gutter rows (654 px gap, so a gutter cut, NOT a forced cut — the guard never sees it) | `seg_fault_t386.1.png` |
| 28 (3/3) | 692.9 | render framing — the bubble is complete in p0101 (bbox verified); Ken Burns window grazes its top | `seg_fault_t692.9.png` |
| 3 (3/3) | 92.3 | script panel pick ("DIE!!" SFX-only panel) — not segmentation | — |

So segmentation still causes ~3 of 5, but via two NEW residual mechanisms
(forced-cut-through-art, gutter-through-SFX), not the tile-edge mechanism the
gap card targets. The yolo guard did its exact job: 11 contiguous forced-cut
boundaries in the index, none through detected text.

### Vetoes: real vs confound

- `static_scene` (NEW, 3/3 but scene=None, deterministic 12 s no-change
  sweep): an artifact of the fresh 35-scene script's pacing on a long
  single-panel scene — a script/render property, not segmentation.
- `irrelevant_panel 12→13`, `empty_screen 1→2`, `missing_payoff 5→2`,
  `weak_hook 1→0`, `watermark 4→2`: all script-lottery movement (different
  panel picks/narration). Note the improvements are as unearned as the
  regressions; ±1–2 here is fresh-script noise, and benchmark.py rightly
  refuses to net them off.

### Wall time vs budget

| stage | wall | budget |
|---|---|---|
| segment (webtoon+yolo) | 10.6 s | ≤3x legacy: met |
| OCR + script + verify | ~34 min | (not in gap budget; fresh-script cost) |
| render review cut | ~23 min | ~19 min — **over** (865 s cut vs baseline 781 s; longer fresh script) |
| stable review ×3 | ~15 min | ~10 min — over (35 scenes vs 25) |

### Honest caveats

- **Fresh-script comparability is weak by construction**: 35 vs 25 scenes,
  every fault count except the tile-edge audit moves partly for script
  reasons. This run measures "the stitched+guarded pipeline end to end", not
  "segmentation, ceteris paribus". The −20 score is real but not attributable
  to segmentation alone; the same is true of the +1s that triggered vetoes.
- Not measured: TTS quality, final loudness/duration/encode vetoes (no final
  encode — review cut only). Mandatory full pre-adoption run must cover them.
- The scene-16 SFX-gutter and scenes-15/21 art-cut faults were 3/3- and
  2/3-confirmed respectively — treat as real residuals, not reviewer noise.

### Recommendation — iterate (two targeted fixes), then re-benchmark

The stitching+guard combination heals the gap's entire original fault class
and the weighted score improves 16%. FAIL is driven by residuals + confounds,
so this is **not adopt-shaped yet**, but close:

1. **Extend the guard veto beyond text**: forced cuts through figures/art
   (scenes 15, 21) are the dominant residual. The yolo detector's
   body/face/frame classes (already in the manga109 model) can veto rows the
   same way text boxes do, or a cut-energy ceiling can reject high-detail
   rows outright.
2. **Run the guard on gutter cuts of over-tall SFX spans too** (scene 16):
   cheap version — before accepting a gutter row, check it against detected
   boxes when the adjacent slice is taller than ~1.5x median.
3. Keep the companion one-liner (majority-vote auto-mode fix) as the
   production trigger for stitching; `--mode webtoon` was forced here.
4. Re-benchmark the iterated guard **reusing this run's script/OCR/TTS**
   (panels only change at the residual cut sites) so the A/B is finally
   clean of the script lottery — then the ≤2 criterion is actually testable.

---

## v2 re-benchmark (split-guard v2: art veto + soft-cap escape + gutter snapping)

**Run date:** 2026-09-08. **Isolation:** golden project untouched; v1 bench
(`/tmp/opencode/exp004_bench/`) kept read-only as the A/B baseline; v2 ran in
a fresh copy at `/tmp/opencode/exp004_bench_v2/proj/`, reusing the v1 bench's
script/OCR/TTS (panel refs remapped 118→115 via
`remap_panels_v2.py`/`remap_ocr_v2.py`) so this A/B is finally free of the
script lottery that confounded the v1 run. 16 of 35 scenes had changed
pixels and were re-rendered; 19 reused cached clips. Timeline parity holds
(v1 865.301s vs v2 865.382s). Reviewed with `review_stable.py` majority-of-3
(`stable_seg_v2.json`), no `--apply-fixes`.

### Verdict — FAIL (binding), but the segmentation objective itself is met

`benchmark.py` returns **FAIL on both scoreboards** and that verdict is
binding: no adoption from this run. However, the per-fault attribution below
shows every segmentation-caused `cropped_content` site from v1 is healed at
the panel level, and the remaining flags sit on byte-identical frames,
unchanged panels, or render-framing crops the segmenter cannot influence.
The FAIL is driven by (a) a borderline 2/3 low-severity `unreadable_text`
re-typing on scene 10 that trips the fault-type veto, and (b) out-of-scope
fault classes (`static_scene` pacing, render-framing crops) inherited from
the fresh-script v1 bench.

### Scores

| comparison | baseline | v2 | delta | verdict | vetoes |
|---|---|---|---|---|---|
| Global vs golden `stable_base.json` | 126 | 99 | **−27** | FAIL | new type `static_scene`; worsened `cropped_content` 4→5, `unreadable_text` 1→2 (`metrics_global.json`) |
| A/B vs v1 bench `stable_seg.json` (scoped, `--scenes-changed 0,1,3,4,10,13,14,15,16,18,19,20,21,23,24,26`) | 65 | 61 | **−4** | FAIL | worsened `unreadable_text` 1→2 (`metrics_ab_scoped.json`) |
| A/B vs v1 bench (unscoped) | 106 | 99 | −7 | FAIL | same veto (`metrics.json`) |

Note on the global row: the −27 improvement direction is inverted vs the v1
run's global comparison because the fresh-script bench differs from the
golden script; `static_scene` (12s unchanged visuals, t720.5) is a script
pacing artifact present identically in the v1 bench review — not introduced
by v2 segmentation.

### Fault counts A/B (v1 bench 28 → v2 27)

| type | v1 | v2 | note |
|---|---|---|---|
| irrelevant_panel | 13 | 12 | script-pick class, out of scope |
| cropped_content | 5 | 5 | see attribution below — seg-attributable **3 → 0** |
| empty_screen | 2 | 0 | improved |
| phone_readability | 2 | 2 | unchanged |
| missing_payoff | 2 | 2 | unchanged |
| unreadable_text | 1 | 2 | **veto trigger** — see scene 10 |
| watermark | 2 | 2 | unchanged (gap-003 scope) |
| static_scene | 1 | 1 | script pacing, unchanged |
| review_error | 0 | 1 | reviewer JSON parse hiccup (2/3, low), not a video fault |

### cropped_content attribution — the number that matters

| flag | v1 | v2 | segmentation-attributable? |
|---|---|---|---|
| scene 0, t6.5 (2/3) | not flagged | flagged | **No.** v1/v2 frames are byte-identical (`md5 d8ae6f77…` both) — pure reviewer variance on identical pixels. |
| scene 3, t92.3 | flagged | not flagged | No (v1 flag was a text-only script pick, not a crop). |
| scene 15, t359.3 (3/3) | flagged | flagged | **v1 yes → v2 no.** v1 cut through art (p0042/p0043, edge ink 1.00). v2 merges them into p0041 (2697px, top/bottom ink **0.00** — clean gutters); heal visually confirmed at t350/t356 (`v2_s15_healed_*`). The persisting flag is the ken-burns framing crop of a very tall panel — render stage, not a cut. |
| scene 16, t386.2 (3/3) | flagged | flagged | **No (either run).** Flagged frames are byte-identical v1↔v2 (`md5 90f7635d…`); the sub-clip renders panel v1 p0046 == v2 p0044 (byte-identical, unchanged by re-segmentation). The *actual* v1 seg defect — SLA SFX clipped at the top of v1 p0044 — **is fixed** by v2 gutter snapping (v2 p0042 bbox y=67367 h=1872 places a real gutter above the SFX; crops `v1_s16_p0044_top.png` vs `v2_s16_p0042_top.png`), but that panel was never what the reviewer's flag pointed at. |
| scene 21, t513.4 (v1 2/3 → v2 3/3) | flagged | flagged | **v1 yes → v2 no.** v1 art cut (p0062 top-row ink 0.93) relocated to whitespace in v2 (p0059 top ink 0.11); heal confirmed at t497/t500 (`v2_s21_healed_*`). Panels at the flagged timestamp (v2 p0060/p0061) are byte-identical to v1's p0063/p0064 with clean edges (top ink 0.06) — the residual flag is render framing. |
| scene 28, t692.9 (3/3) | flagged | flagged | **No.** Scene untouched by re-segmentation (cached clip); frames near-identical (2-byte container diff). Render framing, out of scope. |

**Segmentation-attributable cropped_content: v1 = 3 (scenes 15, 16, 21) → v2 = 0.**

### Scene-10 borderline item (the veto trigger)

v2 adds `unreadable_text` low 2/3 at t223.7 ("shows stylized sound effects
text, not the monstrous creature"). The v2 guard did move scene 10's cut
(40130→39547; both edges land in a solid-dark spread where row-ink density
is saturated at 1.00 in both v1 and v2, so the ink audit is blind there).
But the complaint text is a *panel-relevance* complaint — v1 flagged the
same timestamp as `irrelevant_panel` high 3/3 ("VS CHILL" text panel); v2
kept an `irrelevant_panel` on the same scene at t238.8. Most likely this is
the same underlying script-pick fault re-typed under a different label at
2/3 confidence, not a new segmentation defect — but because the frames do
differ (cut moved), it cannot be fully excluded. This single low-severity
item is what flips `unreadable_text` 1→2 and triggers the veto in both A/B
scorings.

### Acceptance gate & guards

- `check_splits.py --detector yolo --pad 16`: **EXIT 0 — text 0 / art 0 /
  gutter 0 violations** (all three v2 audits clean).
- Panel count 118→115 (−2.5%), well inside the ±30% sanity guard; reading
  order and `panels_index.json` schema preserved; every scene kept ≥1 panel
  after remap.
- Wall-time: segment 8.6s — within the ≤3x budget (v1 measured yolo at 2.5x).

### Gap-004 criteria checklist

| criterion | status |
|---|---|
| cropped_content on golden chapter 6 → ≤2 | **NOT MET on raw review counts** (5→5 on the bench; benchmark verdict FAIL is binding). **Segmentation-attributable component: 3 → 0** — the segmenter no longer produces any of the flagged crops; the remaining 5 are render-framing (3), reviewer variance on identical pixels (1), and an unchanged cached scene (1). |
| Zero splits through detected text boxes (deterministic) | **MET** (gate exit 0, all three audits). |
| Segment wall-time ≤3x | **MET** (8.6s, yolo 2.5x). |

### Evidence (`samples/ch2_bench/`)

- `v1_t359.3.png` / `v2_t359.3.png` — scene 15 flagged frames; v2 shows the merged panel (no art seam), residual crop is framing.
- `v1_s15_artcut_t350/356.png` vs `v2_s15_healed_t350/356.png` — the healed art cut.
- `v1_s16_p0044_top.png` vs `v2_s16_p0042_top.png` (+ `v2_s16_gutter_context.png`) — SLA SFX clipped in v1, gutter-snapped clear in v2.
- `v1_t386.2.png` / `v2_t386.2.png` — byte-identical flagged frames (scene 16).
- `v1_s21_p0062_top.png` vs `v2_s21_p0059_top.png`, `v1_s21_artcut_t497/500.png` vs `v2_s21_healed_t497/500.png` — art cut relocated to whitespace.
- `v1_t6.5.png` / `v2_t6.5.png` — byte-identical; reviewer-variance flag.
- `v1_t223.7.png` / `v2_t223.7.png` — scene 10 borderline item.
- `v1_t692.9.png` / `v2_t692.9.png` — scene 28, untouched cached scene.

### Caveats

- This scope did not measure: TTS quality (mp3s reused byte-equal from v1),
  final encode loudness/duration vetoes, music seams, thumbnails. A full
  pre-adoption run must cover them.
- Row-ink audits are blind inside solid-dark spreads (scene 10) — the guard
  cannot certify cuts there; only the reviewer can.
- The p0061→[p0058,p0059] expansion emits an empty OCR read for new p0058
  (no source panel); no fault surfaced from it, but a production run would
  re-OCR rather than remap.
- `review_error` (1, low) means one of the three review passes partially
  failed to parse on one scene; majority voting absorbed it, but it slightly
  weakens confidence in that scene's tally.

### Recommendation — do not adopt from this run; the residual work is not a segmentation problem

The binding verdict is FAIL and stands. But the v2 guard has exhausted what
segmentation can contribute to gap-004: all three v1 segmentation-caused
crop sites are verifiably healed at the panel level, the deterministic gate
is clean, and every remaining `cropped_content` flag traces to render-stage
ken-burns framing of tall panels, reviewer variance on byte-identical
frames, or an untouched cached scene. Two decisive next steps:

1. **File the render-framing crops as their own gap** (tall-panel ken-burns
   crops at scene ends — scenes 15/16/21/28 pattern) and pursue them in
   `panel_render.py`, not the segmenter. That, not more guard iterations, is
   what can move the raw 6→≤2 criterion.
2. If a tie-break on the scene-10 veto item is wanted before shelving: one
   more stable-review of only scene 10's clip (or a 5-pass vote) would
   settle whether `unreadable_text` at t223.7 is reviewer re-typing (expected)
   or real — if it drops, the scoped A/B becomes 65→60 with no veto, i.e. a
   clean PASS on the changed-scenes scoreboard.

### Addendum — scene-10 tie-break (5-pass stable review)

**Method.** Re-reviewed the SAME v2 bench cut (`sfxed.mp4`, not re-rendered)
with `review_stable.py --passes 5` → `stable_seg_v2_5pass.json` (31 confirmed,
18H/13M/0L). For a symmetric comparison, also 5-pass-reviewed the untouched
v1 bench cut → `stable_seg_v1_5pass.json` (29 confirmed). Note a first v1
attempt pointed `--workdir` at a nonexistent dir and produced a
vision-fault-free review; it was deleted and re-run correctly (workdir is
read-only for the reviewer; v1 bench remained unmutated throughout).

**Tie-break vote — the veto item is noise.** `unreadable_text` at scene 10
t223.7 appeared in **1/5 passes** (pass 1 only; pass 4 emitted a different
low `unreadable_text` at t238.8, also 1/5). Both fall well below the 3/5
majority and are absent from the confirmed list. The original attribution is
confirmed: the 2/3 flag was reviewer re-typing of the persistent scene-10
script-pick fault (`irrelevant_panel` t223.7, 4/5 confirmed), not a
segmentation defect. **v2 confirmed low-severity faults at 5 passes: zero.**

**But the A/B does not become a clean PASS.** All recomputed scoreboards:

| comparison | baseline | v2 | delta | verdict | vetoes |
|---|---|---|---|---|---|
| 3-pass v1 `stable_seg.json` vs 5-pass v2, scoped | 65 | 76 | +11 | FAIL | new `weak_hook`; `cropped_content` 4→5, `irrelevant_panel` 8→9 (`metrics_ab_scoped_5pass.json`) |
| symmetric 5-pass v1 vs 5-pass v2, scoped | 55 | 76 | +21 | FAIL | new `weak_hook`,`phone_readability`; `cropped_content` 3→5, `irrelevant_panel` 8→9, `missing_payoff` 1→2 (`metrics_ab_5pass.json`) |
| symmetric, ≥4/5 supermajority only | 49 | 68 | +19 | FAIL | similar churn (`metrics_ab_5pass_supermajority.json`) |

Why: 5 passes with a ceil(N/2)=3 threshold is *more permissive* than 3-pass
(60% vs 67% consensus), so marginal 3/5 items surface on both sides and
churn the per-type counts that the veto layer compares — e.g. v1's scene-21
crop dropped below majority at 5 passes while v2's held at 3/5; v2 gained a
3/5 `cropped_content` at scene 4 t106.6 (rebuilt scene — merged panel v2
p0015 is 3122px tall; top edge improved 0.08→0.00 ink, bottom edge inked
1.00 in BOTH v1 p0016 and v2 p0015, and reviewers describe side/framing
crops — render framing of a tall panel, not a new cut); v2 gained a 4/5
`weak_hook`+crop on scene 0 whose earlier flagged frame was byte-identical
to v1's. Every count-increase behind the vetoes traces to reviewer churn on
marginal items or render-stage framing, none to a v2 cut through content —
consistent with the deterministic gate's 0/0/0. The gap-011 noise floor is
simply larger than the per-type deltas the veto layer keys on.

**Verdicts and recommendation.** The tie-break resolved what it was asked
to resolve — the original veto item is confirmed reviewer noise (1/5) — but
the binding benchmark verdict on every recomputed A/B remains **FAIL**, and
I do not soften it: no adoption through this scoreboard from this run.

Adopt-shaped assessment for the human gate: the segment-stage evidence is
as strong as this bench can make it — deterministic gate 0 text / 0 art /
0 gutter violations; segmentation-attributable cropped_content 3→0 verified
at panel level; zero confirmed low-severity faults at 5 passes; panel-count,
schema, reading-order, and wall-time guards all met. The remaining FAIL
drivers are (a) render-stage ken-burns framing of tall panels and (b)
reviewer-noise churn in per-type veto counts. Recommendation: **lean-adopt
yolo+v2 guard as the production default for `--mode webtoon`, contingent
on human sign-off plus the mandatory full pre-adoption run** (TTS, loudness,
duration, final encode — unmeasured here), and in parallel (1) file the
tall-panel ken-burns framing crops as a new render-stage gap, and (2) file
a benchmark-layer follow-up to gap-011: per-type vetoes should compare
matched (scene,type) pairs at a fixed consensus level, otherwise any
experiment of this effect size will veto on noise regardless of merit.

---

## full-run gate (mandatory pre-adoption e2e: final render → encode → media vetoes → full-video stable review)

**Run date:** 2026-09-09. **Scope:** the FULL pipeline tail on the v2 bench
project — branding popup + title card + single delivery encode
(`panel_render.py <script> --res 1080p --no-music --layout smart` without
`--stop-after`, mirroring `manga.py`'s finalize invocation per
`round2-eval/final_base.log`), then all four media vetoes, then
`review_stable.py` ×3 of the final mp4.

**Isolation:** golden project and production ch3 untouched. The v2 bench
(`/tmp/opencode/exp004_bench_v2/proj`) was NOT mutated — the full run worked
on a fresh copy at `/tmp/opencode/exp004_full/proj/` (approach: copy, not
backup/restore). All 35 scene clips + TTS reused from the bench cache (log
shows 35/35 `(cached)`), so the story pixels/audio are byte-continuous with
the reviewed v2 review cut; only popup/title-card/loudnorm-encode are new.

### Media vetoes — ALL PASS

| veto | measured | threshold | verdict |
|---|---|---|---|
| encode succeeds | `Done: …ch2.mp4` — 865.5 s, 1920×1080 h264+aac, 3.2 Mbps, faststart; independent ffprobe confirms | must succeed | **PASS** |
| loudness | **−14.1 LUFS** (independent ebur128 re-measure: I = −14.1, LRA 3.2) | −17..−11 | **PASS** |
| duration | final 865.472 s vs Σ(35 scene clips) 865.352 s → **+0.014%**; vs the reviewed v2 review cut (`sfxed.mp4` 865.382 s) → +0.010%; Σ narration 858.62 s + pacing gaps consistent | ≤5% | **PASS** |
| scene count | 35 rendered clips / 35 concat entries == 35 script scenes | must match | **PASS** |

**Explicit deviation on the duration-drift veto:** the config's drift veto
compares against a baseline video, and the natural one
(`round2-eval/base.mp4`, 781.5 s golden encode) renders a DIFFERENT script
(golden 25 scenes vs this bench's fresh 35-scene script), so drift vs 781.5 s
is meaningless (it would read +10.7% purely from script length). Per the
gate plan, internal consistency was verified instead: final duration vs the
sum of scene audio/clips (+0.014%, i.e. the branding overlays and loudnorm
encode added no timeline drift) plus loudness/encode/scene-count as normal.
`benchmark.py` was accordingly run with `--video` (enabling the loudness +
encode-sanity vetoes) but without `--baseline-video`.

### Full-video stable review (×3) vs review-cut

`stable_seg_v2_full.json/.md` (+ pass files): **25 confirmed (15H/9M/1L),
1 unconfirmed-high** — vs the review-cut `stable_seg_v2.json`: 27 confirmed
(16H/8M/3L). Per-type:

| type | review cut (v2) | full video | Δ |
|---|---|---|---|
| irrelevant_panel | 12 | 10 | −2 |
| cropped_content | 5 | 4 | −1 |
| phone_readability | 2 | 3 | +1 |
| missing_payoff | 2 | 2 | 0 |
| watermark | 2 | 2 | 0 |
| unreadable_text | 2 | 1 | −1 |
| static_scene | 1 | 1 | 0 |
| review_error | 1 | 0 | −1 |
| empty_screen | 0 | 1 | +1 (present in v1-bench review; "new" only vs the v2 cut) |
| weak_hook | 0 | 1 | +1 |
| weighted score | 99 | 94 | −5 |

`benchmark.py` scoreboards (recorded, verdicts binding, not softened):
- vs v1 bench `stable_seg.json`: 106 → 94 (Δ −12) — **FAIL** (vetoes: new
  `weak_hook`; worsened `weak_hook` 0→1, `phone_readability` 2→3) —
  `metrics_full_vs_v1bench.json`.
- vs v2 review cut `stable_seg_v2.json`: 99 → 94 (Δ −5) — **FAIL** (vetoes:
  new `weak_hook`, `empty_screen`; worsened `phone_readability` 2→3) —
  `metrics_full_vs_v2cut.json`.

### Are any new flags full-encode-only faults (branding/title-card interactions)? — No.

The specific worry this gate exists for — popup/title-card/loudnorm breaking
something the review cut couldn't see — did not materialize:

- **Zero confirmed faults fall inside the branding windows** (title card
  8–12 s, popup 857.9–864.9 s). The only branding-adjacent item is 1/3
  UNCONFIRMED (scene 34 "channel watermark/subscribe button" at t863 — that
  is the intentional popup, correctly not scored).
- Deterministic scan on the final pixels: **0 fault spans** in all 3 passes
  (no blank/black frames introduced by overlays or the CRF-18 encode).
- Every count that moved is item-level reviewer churn on story frames that
  are pixel-continuous with the review cut (PSNR 30–50 dB at flagged
  timestamps = encode-only differences; the one 9 dB reading at t106.6 is a
  ±1-frame Ken Burns motion offset from ffmpeg seek, not content change):
  - `weak_hook` (2/3, sc0 t6.5) and `empty_screen` (2/3, sc1 t36.7): both
    were **already confirmed at 4/5 in the 5-pass review of the identical v2
    review cut** (`stable_seg_v2_5pass.json`) — pre-existing script-pick
    complaints that the 3-pass cut review happened to miss, not encode
    faults. `empty_screen` was also 2 in the v1 bench review.
  - `cropped_content` sc4 t106.6 (3/3): same item as the 3/5 flag in the
    5-pass cut review — the tall-panel render-framing crop already
    attributed to render stage in the addendum.
  - `phone_readability` +1 and the sc10 crop/irrelevant re-typings: 2/3
    marginal items of the same script-pick faults, inside the gap-011 noise
    floor; their counterparts (`review_error`, sc16/15/21 crop flags,
    unreadable_text) dropped out symmetrically.
- Segmentation regression check: the full-video review contains **zero**
  cropped_content at the v2-healed sites (scenes 15, 16, 21 — all absent).
  The 4 remaining crops are scene 0 (2/3, reviewer-variance site from the
  A/B — byte-identical frames there), scene 4 + scene 28 (render-framing of
  tall panels, filed as the render-stage gap), and scene 10 (2/3 re-typing
  of the script-pick fault, 1/5 in the tie-break). Segmentation-attributable
  count remains **0**.

### Wall time

| stage | wall |
|---|---|
| finalize (popup + title card + delivery encode, 35 cached scenes) | ~7.7 min |
| media veto measurements (ffprobe/ebur128/per-clip sums) | ~2 min |
| stable review ×3 (full 865 s video) | ~14 min |

### Evidence (`samples/full_run/`)

- `cut_t*.png` / `full_t*.png` pairs at 6.5/36.7/106.6/223.7/423.3/472.7 s —
  review-cut vs final-encode frames at every newly-flagged timestamp; story
  content identical (encode-grade PSNR only).
- `full_titlecard_t9.5.png`, `full_popup_t860.png`,
  `full_popup_watermark_t863.png` — the branding overlays render correctly
  and are what the 1/3 unconfirmed watermark sighting saw.

### Honest caveats

- The 35-scene fresh-script bench still isn't the golden script; the gap-004
  raw criterion (golden cropped_content 6→≤2) remains formally untested on
  golden narration. The evidence chain (tile-edge class eliminated,
  seg-attributable 3→0, deterministic gate 0/0/0) is indirect but consistent
  across four independent reviews now.
- TTS was reused from the bench cache (byte-equal mp3s) — this gate verified
  TTS *integration* (mix, truncation, loudnorm survival), not fresh TTS
  synthesis quality.
- Thumbnails/shorts/music-bed stages were not exercised (`--no-music`
  matches the bench baseline; segmentation does not touch them).
- 3-pass full review vs 3-pass cut review still carries the gap-011 ±noise;
  the deltas above are all within it, and every "new" item was cross-checked
  against the 5-pass cut review rather than trusted at face value.

### Final adopt recommendation

**ADOPT (recommend), with the formal FAIL verdicts recorded and not
softened.** The full-run gate's own remit — media vetoes and
full-encode-only fault discovery — is **clean on every count**: encode
succeeds, −14.1 LUFS in range, +0.014% duration drift, 35/35 scenes, zero
faults in branding windows, zero deterministic faults on final pixels, zero
segmentation-attributable crops, and every review-count movement traces to
pre-existing script-pick items or gap-011 reviewer churn (each verified
against the 5-pass review of the identical cut). The binding benchmark FAILs
are, as in the addendum, per-type vetoes tripping on noise/out-of-scope
classes — the same items that were already adjudicated as non-segmentation.
Per the addendum's lean-adopt contingency, this run discharges the "mandatory
full pre-adoption run" condition. Remaining adoption mechanics for the
orchestrator: (1) human sign-off acknowledging the formal FAIL verdicts and
their attribution, (2) flip `--split-guard yolo` + `--mode webtoon` (or the
auto-mode majority-vote one-liner) as production defaults, (3) move the yolo
detector deps in-process per IMPLEMENTATION.md deviation 3, and (4) keep the
two filed follow-ups (render-stage tall-panel framing gap; gap-011 veto
matching) open — they, not the segmenter, own the residual faults.
