# exp-003 prototype: scanlation watermark detect + remove

Gap: `improvements/gaps/gap-003-watermark-removal.md` (watermark HIGH faults
4→0 on golden chapter). Research: `research.md` (candidate A+B+C adopted).

## What changed

| File | Change |
|------|--------|
| `pipeline/clean_watermarks.py` | **NEW** (~640 lines). Discover → match → remove, cached + idempotent, `panels/ -> panels_clean/` convention. Zero new dependencies (cv2/numpy/gateway only). |
| `manga.py` | +16 lines: `--clean-watermarks` flag (store_true, default OFF), `step_clean_watermarks()` runs after CLEAN BUBBLES / before SCRIPT. Nothing else touched. |

`review_video.py`, `panel_render.py`, `make_video.py`: untouched.

### How it works

1. **DISCOVER** (once per chapter, cached to `output/<slug>/watermark_template.{json,png}`):
   - Cross-panel recurrence prescan: white/dark glyph runs in the top/bottom
     12% bands, corner-anchored, clustered by (band, corner, size); a cluster
     on ≥3 panels is a watermark candidate.
   - `gateway.llm_vision` on up to 3 candidate panels (one per top cluster)
     confirms watermark text + band. The **CV glyph run supplies the tight
     bbox** (vision boxes swallow surrounding art and kill match scores);
     `_lockup_bbox` extends the text run left to the logo badge outline.
   - **No watermark → negative cache** (`{"found": false}`) → all future runs
     no-op.
   - `--no-vision` / gateway failure → pure-CV fallback: each candidate
     cluster's template is probe-matched against ≤40 spread panels and
     accepted only if it matches ≥3 and ≤30% of them (a genuine site mark
     stamps a minority of panels; repeating art texture matches everywhere —
     the 30% cap rejected a 44%-matching texture cluster on the golden
     chapter while the true mark matched 8/154 ≈ 5%).

2. **MATCH** (per panel, pure cv2, ~15 ms): multi-scale
   (0.55–1.2 — the scanlator stamps a ~0.65× lockup on short panels, found on
   p0117) `cv2.matchTemplate` TM_CCOEFF_NORMED, restricted to the top/bottom
   bands (band widened to 2× template height on short panels, capped at h/3).
   **Acceptance gates** (tuned on golden chapter: 8/8 known marks, 0 false
   positives on 146 clean panels):
   - white-glyph dice ≥ 0.30 (matched crop's bright pixels must overlap the
     template's glyph pattern — plain score can't separate the mark from dark
     corner art), AND
   - score ≥ 0.50, or score ≥ 0.45 with dark-badge dice ≥ 0.60;
   - sub-0.8 scale additionally requires the dark-badge dice unconditionally.

3. **REMOVE** (cheapest-safe first, only ever inside matched bbox + dilation):
   - **crop** the band when it's a dead margin (std < 8, ≥90% height kept);
   - **reflect**: when the mark touches a panel edge (the usual case), mirror
     the adjacent art rows into the box + feather seams with a thin Telea
     pass. Chosen over plain Telea after before/after crops showed Telea's
     directional smear on the 133×51 px opaque banner (far past its
     thin-stroke comfort zone); reflection continues the local texture.
   - **inpaint**: full-rect Telea (radius 4, 6 px+proportional dilation)
     fallback when no clean mirror source exists.
   - NOTE deviation: the research's glyph-only stroke mask was implemented
     first and left the opaque banner/badge behind — this lockup is opaque,
     so removal covers the whole matched rect.

4. Per-panel decisions logged to `output/<slug>/watermark_log.json`
   (`{panel, method, score, scale, band, dice, bbox}`); the log + intact
   outputs double as the idempotency cache (2nd run: 0.17 s no-op).

### Output convention (matches panel_render exactly)

`panel_render.py:_resolve_panel`, `make_short.py:444`, `make_thumbs.py:261`
all resolve `panels_clean/<name>` **per-file** and fall through to
`panels/<name>` — a partial mirror is the convention. So we write **only the
cleaned panels**. Composes with `clean_bubbles.py`: when
`panels_clean/<name>` already exists (bubble pass), it is used as source and
overwritten, so both cleanups stack in either order.

### Fallback behavior

- Vision unavailable → validated CV recurrence discovery (same result on the
  golden chapter).
- Discovery finds nothing → negative cache, clean exit, no outputs.
- Any per-panel error → logged, original panel used, loop continues.
- Any top-level error in the CLI → prints and `sys.exit(0)` so a manga.py run
  with the flag on can never be killed by this stage.
- Flag off → `step_clean_watermarks` returns immediately; default path
  byte-identical (only additions to manga.py are the flag guard + argparse).

## How to run

```bash
# stage-scoped, dry-run (log decisions only):
./venv/bin/python3 pipeline/clean_watermarks.py \
    --panels output/the-world-after-the-fall-ch2/panels --dry-run

# real run (writes output/<slug>/panels_clean/ + watermark_log.json):
./venv/bin/python3 pipeline/clean_watermarks.py \
    --panels output/the-world-after-the-fall-ch2/panels

# via orchestrator:
./venv/bin/python3 manga.py --project output/the-world-after-the-fall-ch2 \
    --clean-watermarks --from clean
```

## Smoke results (golden chapter, 154 panels)

- Discovery: vision confirmed `FLAMESCANS.ORG`, bottom band, template
  133×51 px from p0080 (2 gateway calls total incl. the negative-cache probe
  during tuning; steady-state = 1 batched call per chapter).
- Matched + cleaned **8/154**: p0017 (0.565), p0031 (0.546), p0040 (0.527),
  p0080 (1.000), p0094 (0.869), p0101 (0.754), p0117 (0.621@0.65×),
  p0133 (0.661). 7 reflect / 1 reflect+feather variants; **0 false positives**
  (visually audited top-20 scorers below threshold).
- Note: the review evidence named scenes 1/13/15/20, but those timestamps map
  to panels via the OLD render; scanning bands found the marks on the 8
  panels above, which the CURRENT script uses in scenes 1, 5, 8, 10, 16, 18,
  21, 23. Scene 1's title-card watermark (p0017/p0040) is covered.
- Wall time: 6.9 s cold (incl. discovery), 2.5 s warm, 0.17 s cached — far
  under the +60 s guard.
- Idempotency: 2nd run no-ops via watermark_log.json cache. PASS
- Negative case: 5 clean panels → "no watermark" negative cache, no
  panels_clean dir created, rerun no-ops. PASS
- `manga.py --help` works; flag off leaves behavior unchanged. PASS
- Samples: `samples/before_after_p*.png` (8 crops),
  cleaned panels in `samples/panels_clean_test/`.

## Evaluator command sequence

```bash
# 1. which panels changed
./venv/bin/python3 pipeline/clean_watermarks.py \
    --panels output/the-world-after-the-fall-ch2/panels        # writes panels_clean/
cat output/the-world-after-the-fall-ch2/watermark_log.json | \
    python3 -c "import json,sys; [print(e['panel'],e['method'],e['score']) \
    for e in json.load(sys.stdin)['panels'] if e['method']]"

# 2. affected scenes (current script): 1, 5, 8, 10, 16, 18, 21, 23
#    (panels p0017/p0031/p0040/p0080/p0094/p0101/p0117/p0133)

# 3. stage-scoped re-render of just those scenes (panel_render auto-prefers
#    panels_clean/) then the stable review:
./venv/bin/python3 pipeline/panel_render.py \
    output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.json \
    --res 1440p --workdir output/the-world-after-the-fall-ch2/_work \
    --redo-scene 1 --redo-scene 5 --redo-scene 8 --redo-scene 10 \
    --redo-scene 16 --redo-scene 18 --redo-scene 21 --redo-scene 23
./venv/bin/python3 pipeline/review_stable.py \
    output/the-world-after-the-fall-ch2/the-world-after-the-fall-ch2.mp4

# acceptance: watermark HIGH faults 4 -> 0; no smearing in the re-review;
# attach samples/before_after_p*.png crops to the verdict.
```

## Deviations from the research plan

1. **Edge-based matching dropped** — Canny-edge TM_CCOEFF scored ≤0.29 on
   true marks (semi-transparent mark over varying art gives unstable edges).
   Replaced with intensity matching + white/dark glyph-dice gates (stronger
   discriminator than the researched 0.55 edge-score floor).
2. **Stroke-mask inpaint replaced by full-rect removal** — the lockup is
   opaque; glyph-only masks left the banner. Added the **reflect** method
   (mirror adjacent rows) because full-rect Telea visibly smeared on the
   133×51 box; Telea remains the fallback.
3. **Scale range widened to 0.55–1.2** (from 0.8–1.2) for the small-stamp
   variant, with a stricter gate below 0.8.
4. **Stage-0 OCR-read seeding and gateway `image_edit` escalation not
   implemented** — CV reflect/inpaint passed visual inspection on all 8
   panels, so the escalation tier wasn't needed; noted as a follow-up if the
   evaluator finds smearing on other chapters.
5. **CV fallback validates clusters by match-fraction probe** (≤30% of
   panels) — the raw "strongest recurrence" cluster was repeating art texture
   on the golden chapter and would have matched 69/154 panels.
