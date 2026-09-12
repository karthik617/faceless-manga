# exp-013-smart-layout-v2 — evaluation report

**Binding verdict (benchmark.py): FAIL** — scoped score 0 → 0 (delta +0), global score
3 → 3 (delta +0). `benchmark.py` requires strict improvement (`cur_score < base_score`)
to PASS; the experiment changed **nothing measurable** on the eval chapter. This is a
**FAIL-iterate**, not a FAIL-regress: no vetoes fired, no fault got worse — the v2
planner simply never produced a v2 layout on any of the 25 golden-chapter scenes, so
arms A and B are frame-identical on 25/25 scenes.

## Protocol / golden-project protection

Golden project `output/the-world-after-the-fall-ch2/` was **never touched** — the run
used the pre-existing eval copy at `run/proj/` (eval.json + ocr/textboxes sidecars +
panels), with throwaway workdirs `run/work_A` (baseline `--layout smart`) and
`run/work_B` (`--layout smart2`). Arm A was already complete from the interrupted
session (review cut at `run/work_A/ambed.mp4`, exit 0); arm B was re-run to completion
(review cut at `run/work_B/ambed.mp4`, exit 0). Note: arm B resumed 5 cached scene
clips (1–5) from the interrupted first attempt — those clips were produced by the same
smart2 command line, and framemd5 confirms they match arm A pixel-wise like every other
scene, so the cache reuse does not contaminate the comparison (it only affects
wall-time, normalized below).

Both arms reviewed with `pipeline/review_stable.py` (majority-of-3, no `--apply-fixes`):
`run/stable_A.(json|md)` + 3 pass files, `run/stable_B.(json|md)` + 3 pass files.
All 6 passes completed with 0 review_errors.

## Score table (majority-voted faults)

| Fault type | Severity | Arm A (smart v1) | Arm B (smart2) | Δ |
|---|---|---|---|---|
| ambience_absent (global) | low | 1 | 1 | 0 |
| sfx_absent (global) | low | 1 | 1 | 0 |
| panel_reuse (scene 24) | low | 1 | 1 | 0 |
| **weighted score (global)** | | **3** | **3** | **+0** |
| **weighted score (scoped, scene 18)** | | **0** | **0** | **+0** |

- high: 0 → 0, medium: 0 → 0, low: 3 → 3. All three lows are deterministic
  script/timeline checks (no music/SFX layer in the stage-scoped cut, panel reuse in
  the script) — identical text in both arms, i.e. not vote noise, and per gap-011 they
  would not count against the experiment anyway since they appear in both arms.
- Vetoes: none triggered (no new fault types, no fault type worsened; no media vetoes —
  stage-scoped run, no `--video`).
- Scoped run: `--scenes-changed 18` (0-based; script scene 19, the only scene where any
  smart-layout code path executed). Both metrics files saved:
  `metrics.json` (scoped, with extra metrics) and `metrics_global.json`.

## The core finding: v2 templates never fire on the golden chapter

Template distribution grepped from `run/render_B.log`:

| Layout outcome | Scenes |
|---|---|
| V2 template (hero/rail/mag_grid/strips) | **0 of 25** |
| v1 `grid` fallback attempted | 1 (scene 19) — its ffmpeg render then failed and fell to sequential, **identically in both arms** (pre-existing v1 bug, filter graph builds 5088×2880 canvas > x264 limits at 1440p) |
| Sequential cuts | 24 |

**Acceptance criterion "≥3 distinct templates on golden smart scenes": NOT MET (0 of ≥3).**

Root cause (diagnosed by replaying `plan_layout_v2` offline with the exact runtime
inputs — 2560×1440 frame, real audio durations, the `{panel: boxes}` tboxes dict):

1. **Min-height math is infeasible for webtoon-strip panels at 16:9.** Ch2 panels are
   tall slivers (typical ar 0.34–0.9; e.g. p0002 690×1957, p0003 690×2000). The
   exp-009 size-aware guard demands e.g. 1147 px and 1440 px (= full frame height) for
   the two text panels of scene 2 — two stacked rows need 2587 px of the 1404 px
   available, so `mag_grid` can never fit; `hero_*` needs ar ≤ 1.2 heroes but also
   full-min-height side stacks; `strips` needs all-wide (ar ≥ 1.4) panels that this
   chapter simply doesn't have; `rail` needs a wide+tall pair. Every template is
   rejected on every scene, and 11/25 scenes are additionally `pace=hype` (planner
   returns None by design).
2. **One near-miss:** scene 18 would have planned `V2:hero_left` — but panel_render's
   broken-panel guard substituted p0099 (236×183 fragment) with p0098 (690×1725),
   changing the picked middle panel's aspect from 1.29 to 0.40 and killing feasibility.
   The planner sees post-substitution panels, so even this one never fired in the run.

Consequence: **arm B is pixel-identical to arm A** — verified with `ffmpeg -f framemd5`
on the first 3 s of all 25 scene clips: 24/25 identical; scene 3 differs only in
duration (26.93 s vs 26.47 s) because edge-TTS is nondeterministic run-to-run
(audio md5s differ; frames at shared timestamps are identical, mean abs pixel diff 0.0).
Review-cut durations: 781.48 s vs 781.07 s (0.05 % drift, TTS-only).

## Acceptance criteria (gap card)

| Criterion | Status |
|---|---|
| Smart-scene phone_readability MEDIUMs 3 → 0 | **Not measurable** — 0 mediums in both arms on this cut; and since no v2 layout rendered, the experiment could not have moved this number anyway. (ch3 evidence not testable in this scope.) |
| No regression on gap-002 win (smart-scene HIGHs stay 0) | **Met (vacuously)** — 0 HIGHs both arms; outputs identical. |
| ≥3 distinct templates on golden smart scenes | **NOT MET** — 0 v2 templates fired. |
| Render wall-time ≤ 1.3× | **Met** — A 24:51.13 (25 scenes rendered), B 19:06.52 (20 rendered + 5 cached). Per-rendered-scene: 59.6 s vs 57.3 s → **0.96×** (raw 0.77× flattered by cache). Planner overhead is negligible; but note no v2 *renderer* time was ever exercised, so the ≤1.3× bound is only certified for the planner+fallback path. |

Quality guards (reading order, no bubble cropping, readable text cells): **vacuously
held** — no v2 cell was ever rendered on this chapter.

## Sample frames (`samples/eval/`)

A/B pairs at identical timestamps; every pair is pixel-identical (that IS the finding):

- `s19_t2_A.png` / `s19_t2_B.png`, `s19_t15_*`, `s19_t28_*` — scene 19, the only
  smart-layout attempt (v1 grid plan → ffmpeg failure → sequential in both arms).
- `s18_t5_A.png` / `s18_t5_B.png` — scene 18, the near-miss hero_left (killed by the
  p0099→p0098 substitution); sequential in both arms.
- `s02_t4_A.png` / `s02_t4_B.png` — scene 2, representative tall-strip-panel scene
  where all five v2 templates are min-height-infeasible.

## Wall-time

| | Arm A (smart) | Arm B (smart2) |
|---|---|---|
| Elapsed | 24:51.13 | 19:06.52 |
| Scenes rendered (not cached) | 25 | 20 |
| Per-rendered-scene | 59.6 s | 57.3 s |
| **Ratio (normalized)** | | **0.96× (≤1.3× ✓)** |

## Caveats — what this stage-scoped run could NOT measure

- **TTS, music mix, final loudness/branding/encode**: `--no-music`,
  `--stop-after review-cut` — the mandatory full pre-adoption run must check these.
  (Moot until the planner actually fires.)
- **The v2 renderer (per-cell Ken Burns, drift gating) is completely untested on real
  chapter content** — zero v2 layouts rendered. The prototype's smoke tests are the
  only evidence it works.
- **ch3 evidence in the gap card is not covered** — this eval used the ch2 golden copy
  only.
- Vision-reviewer noise floor was unusually quiet (identical 3 lows across all 6
  passes); this run cannot re-certify the ±12 noise figure, but it did not need to —
  the arms are pixel-identical.
- The scene 19 ffmpeg failure (v1 smart grid canvas 5088×2880 at 1440p) is a
  **pre-existing v1 bug**, identical in both arms; it is out of scope here but worth a
  separate gap card — v1's adopted smart layout silently loses its one golden-chapter
  firing at 1440p.

## Recommendation to orchestrator (iterate direction)

The prototype is safe (perfect fallback, no regression, no wall-time cost) but inert on
real webtoon content. One change would decide the next round: **make the template
feasibility math aware of tall-strip source panels** — e.g. allow cells to crop tall
panels to their text/focus region (reuse exp-009's `_text_aware_crop`) instead of
requiring the full panel to fit above min height, and/or run hero/rail on the panel's
cropped aspect. Secondary: plan before the too-small-panel substitution or re-plan with
the original candidate set (scene 18's hero_left was lost to substitution). Without one
of these, smart2 will fire ~never on this catalogue and can't be adopted.

## Artifacts

- Metrics: `metrics.json` (scoped, scene 18), `metrics_global.json`
- Reviews: `run/stable_A.(json|md)` (+pass1-3), `run/stable_B.(json|md)` (+pass1-3)
- Render logs: `run/render_A.log` (25:51 elapsed, exit 0), `run/render_B.log`
  (19:07 elapsed, exit 0)
- Review cuts: `run/work_A/ambed.mp4`, `run/work_B/ambed.mp4` (781 s each)
- Samples: `samples/eval/*.png` (5 A/B pairs)
