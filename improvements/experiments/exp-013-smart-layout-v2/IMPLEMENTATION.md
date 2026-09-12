# exp-013 prototype — smart layout v2 (magazine-style content-weighted composition)

Status: prototyped, ready for evaluator. Gap: `improvements/gaps/gap-013-smart-layout-v2.md`.
Design followed: `research.md` recommended candidate A with B's justified-row math ported
inside (MIT-attributed). **No new pip deps installed. No files under output/ touched.**

## Flag

`--layout smart2` on `pipeline/panel_render.py` (choices are now `seq | smart | smart2`,
default still `seq`). Opt-in only:

- `--layout seq` (panel_render default): **frame-identical** to pre-change (verified below).
- `--layout smart` (v1, the manga.py default): **frame-identical** to pre-change — the v1
  planner/renderer code paths are untouched; only the flag-gating condition was rewritten
  and it evaluates identically for `smart`.
- `--layout smart2`: new v2 planner (`layout_smart2.plan_layout_v2`); when no v2 template
  is feasible it chains to v1's `plan_layout` (bit-safe adopted behavior — a v1-shaped
  plan renders through the v1 renderer), and `None` falls through to sequential cuts.
  Any runtime exception is caught by panel_render's existing per-scene try/except and
  logs `smart layout failed (...), sequential render` — the pipeline never crashes.

## Files touched

| File | Change |
|---|---|
| `pipeline/layout_smart2.py` | **NEW** (~650 lines): v2 planner + renderer + smoke-test CLI. Imports v1's constants/guards/`_beat_times`/`_panel_min_h` so the two planners can never disagree on "readable". Flickr justified-layout row-partition idea ported in `_justify_row` with MIT attribution (module docstring + function comment); no code copied verbatim. |
| `pipeline/panel_render.py` | 4 flag-guarded hunks: (1) gate accepts `smart2` and lowers the panel floor to 2 for it (v1 keeps 3); (2) planner dispatch `smart2 -> ls2.plan_layout_v2` else v1 `plan_layout` unchanged; (3) renderer dispatch: `LayoutPlanV2 -> render_smart_scene_v2`, v1-shaped plans -> v1 renderer; (4) argparse choices + help. |
| `pipeline/layout_smart.py` | **NOT touched by this experiment** (its working-tree diff is prior uncommitted exp-009 work). |
| `manga.py` | **NOT touched** — edit blocked by workspace permission rules (edits allowed only in `pipeline/**` and `improvements/**`). Its `--layout` pass-through (`if args.layout != "seq": cmd += ["--layout", args.layout]`) would forward `smart2`, but its own argparse `choices=["seq","smart"]` rejects it, so for now the experiment is invoked at the panel_render stage directly (the normal stage-scoped experiment path). At adoption, add `"smart2"` to manga.py's choices + help (one-line change, orchestrator instruction). |

## Template library & selection

Five templates (research §3.1; `inset` deferred to phase 2 as designed). Each returns
v1 `Cell` lists in reading order so `render_smart_scene*` and the punch-in scheduler
work unchanged:

| Template | Trigger | Sizing |
|---|---|---|
| `hero_right` | n=2-3, LAST panel hero score ≥0.5 (splash art + payoff position) and ar ≤1.2 | hero column right at 0.40-0.62·W (share-scaled), others stacked left: min heights first, slack by W |
| `hero_left` | mirror — FIRST panel is the hero (establishing splash) | mirror |
| `mag_grid` | n=3, both 2+1 and 1+2 row groupings tried, larger feasible area wins | row heights = members' max required height + weight-share of slack; in-row widths = min width for required height first, slack by aspect×weight (justified — fills row exactly; Flickr port) |
| `strips` | all panels wide (ar ≥1.4), v1's stack trigger | resting rows weight-proportional, clamped to 0.6-1.6× the equal share; highlighted accordion pop keeps v1's exact guard |
| `rail` | n=2, wide scene panel (ar ≥1.3) + tall panel (ar ≤0.9) | scene 74% width, tall full-height right rail |

Selection: all triggered+feasible templates are scored by total displayed panel area ×
a round-robin recency penalty (last-used ×0.75, in last three ×0.9) — per-chapter
process-level counter (`_RECENT`), satisfying the "≥3 distinct templates" criterion
without ever overriding feasibility.

## Content-weighted sizing (the key inversion vs v1)

`W = 0.40*beat_share + 0.35*text_density_norm + 0.25*position` (research §3.2).
`text_density` uses measured boxes from the exp-009 `<slug>.textboxes.json` sidecar when
present, else a coarse OCR-based binary prior. Every solver satisfies each cell's
**minimum height as a hard constraint first** — the min comes from v1's `_panel_min_h`
(exp-009's `MIN_TEXT_H_1080 = 40` reused, not re-derived) — then distributes remaining
space by W. Mins don't fit → template infeasible → next template → v1 → seq. A small
text cell can therefore never be emitted, by construction.

## Per-cell Ken Burns

Active-cell drift only (research §3.3): drifting cells' bright PNGs render at 1.10×
their rect; an animated `crop` offset pans the visible window across the overscan during
the cell's beat (even cells horizontal, odd vertical, ~0.5 px/frame; clamps at beat end
so accumulated cells settle). Resting/dimmed copies static as v1. Whole-composite
zoompan, breathe, accent border and the guided-view punch-in scheduler are **copied
verbatim from v1** — they remain the static_scene safety net.

**Deviation (stricter than research):** drift only engages when we KNOW the 10% overscan
can't clip a bubble — panel is OCR-text-free, or all measured text boxes clear a 10%
margin on every edge; unknown → static. "No bubble cropping at cell edges" is a hard
guard; the research draft drifted every active cell. Drift is also disabled for beats
<2 s (research's wobble mitigation).

## Fallback chain

`plan_layout_v2` templates → v1 `plan_layout` (grid/stack, adopted behavior — verified
byte-parity below) → `None` → panel_render's sequential path. Runtime render errors →
panel_render's existing try/except → sequential render (graceful, logged).

## Smoke tests & results

Sandbox: `/tmp/opencode/exp013/proj` (2-scene script, ch2 panels copied out; golden
project only ever read). x264 encodes are container-nondeterministic run-to-run, so
parity is proven on **decoded frame md5s** (`ffmpeg -f framemd5`), which ARE
deterministic for identical inputs.

1. **Default `seq` byte-identical**: pre-change `panel_render.py` reconstructed at
   `/tmp/opencode/exp013/pr_pre/`; both variants rendered the same script + same cached
   TTS. Per-scene video framemd5: `3801b6df…`/`d6da1eb1…` identical pre vs post (audio
   differs only because edge-TTS itself is nondeterministic across full runs; with
   cached audio, video frames match exactly).
2. **`--layout smart` (v1) identical**: 3-square-panel scene plans `grid` on both
   variants; with TTS pinned via a fixture, framemd5 `bd16b11f…` identical pre vs post.
3. **v1-fallback inside smart2 identical**: the same scene under `--layout smart2`
   falls through to the v1 plan (small native text rejects every v2 template) and
   renders framemd5 `bd16b11f…` — byte-equal frames to v1's own output.
4. **Planner parity**: `plan_layout_v2` fallback plans compare equal to `plan_layout`
   (template, cells, beats, picked); hype pace / 1 panel / short duration all return
   None as v1 does.
5. **smart2 renders 3+ panel scenes without error**: end-to-end `panel_render.py
   --layout smart2` on the sandbox script logged
   `scene 1: smart layout (hero_right, 3 of 3 panels: …)` and produced a valid
   review cut. Full run wall time 18.5 s vs ~17 s seq (well under 1.3×; scene-scoped
   benchmark is the evaluator's job).
6. **All 5 templates exercise** on real ch2 panels (planner-level, with the ch2 OCR +
   textboxes sidecars): hero_right, hero_left, mag_grid, strips, rail all fire on
   suitable panel sets; every cell rect stays inside the 1920×1080 frame; reading
   order preserved by construction (templates emit cells in script order).
7. **Visual samples** in `samples/`: `hero_right.mp4`, `mag_grid.mp4`, `strips.mp4`,
   `rail.mp4` + extracted frames. hero_right shows the dominant right column with a
   weighted left stack (reference sample 1/6 look); mag_grid shows the 2+1 weighted
   rows with fully readable bubble text (sample 2 look); punch-ins/accent borders
   behave exactly as v1.

## How the evaluator should run it

```bash
# stage-scoped: render the golden chapter's smart scenes with v2 into a THROWAWAY
# workdir (never output/the-world-after-the-fall-ch2/):
./venv/bin/python3 pipeline/panel_render.py \
    output/<eval-copy-slug>/<slug>.json --res 1440p \
    --layout smart2 --text-aware \
    --workdir /tmp/opencode/exp013_eval --stop-after review-cut

# per-template plan/сell inspection without rendering:
./venv/bin/python3 pipeline/layout_smart2.py --panels <p1> <p2> [<p3>] \
    --plan-only --narration "..." --ocr <slug>.ocr.json --text-boxes <slug>.textboxes.json
```

Template distribution is greppable from the render log (`scene N: smart layout (<tname>…`).

## Deviations from the research design

1. **manga.py flag not added** (permission-blocked; see Files touched). Adoption needs
   the one-line choices change there.
2. **Drift gating stricter**: research drifted every active cell; prototype requires
   proof the overscan is bubble-safe (text-free per OCR, or measured boxes clear a 10%
   edge margin). Missing textboxes sidecar ⇒ text-bearing cells render static — safe
   default, slightly less motion than the research envisioned.
3. **`_justify_row` extends the research formula**: pure aspect×weight width splits
   couldn't guarantee min text heights, so row widths get the same mins-first inversion
   as column heights (min width for required height first, slack by aspect×weight).
4. **`inset` template deferred** — as the research itself specified (phase 2).
5. No env-var equivalent added — the codebase uses CLI flags for layout (no existing
   env convention for it).

## Deps

None installed. Everything is PIL/ffmpeg/stdlib already in the venv.
