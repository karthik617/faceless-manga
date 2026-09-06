# exp-002 — Creative multi-panel layout compositor (research)

**Gap:** gap-002-multipanel-layout | **Status:** research complete, BUILD-OURSELVES confirmed
**Date:** 2026-09-06

## 0. TL;DR recommendation

Build a **PIL/numpy pre-composited plate + per-cell ffmpeg overlay reveal** compositor,
implemented as a new `render_multipanel_scene()` in `pipeline/panel_render.py`, triggered
when a scene lists **3+ usable panels AND pace != "hype"**, behind `--layout smart`.
Two primary templates: **stack reveal** (vertical/horizontal, direction chosen by panel
aspect) and **grid highlight** (2x2 / 2-col grid, per-cell brighten+scale as its beat
narrates). Sequential wipe is a v2 stretch (xfade already gives 80% of it via the
existing `_slide_join`). Reveal times snap to **sentence/clause boundaries derived from
edge-tts word timings** (already produced per scene at `w{i:03}.json`). Hard fallback to
today's sequential-cuts path whenever the readability guard (each panel ≥45% frame
height at highlight time) cannot be met.

---

## 1. Prior art

### 1.1 Manga-recap / comic-dub channels (observed conventions)

Patterns worth copying, from studying the format (Shonen-Flux-style recaps, comic-dub
channels, official motion comics):

- **Sequential single-panel with Ken Burns** is the dominant baseline — exactly what
  panel_render.py does today. The differentiator among top channels is what happens on
  *dense* beats:
  - **Dim-and-spotlight**: the full page (or a stack of panels) shown at once, with the
    currently-narrated panel at full brightness and everything else dimmed ~40-60% and
    often slightly blurred. The highlight *moves* panel-to-panel in reading order as
    narration advances. This is the single most common "multi-panel moment" treatment
    and directly attacks `irrelevant_panel` (the narrated panel is always visibly "on").
  - **Progressive stack build**: panels slide/pop in one at a time, accumulating into a
    column (vertical video) or row (horizontal), each entrance synced to the line that
    describes it. Once all are in, a slow zoom on the composite carries the tail of the
    narration — this also fixes the ping-pong problem (line 807-817): late narration
    plays over the *full composite*, not a rewound panel.
  - **Rapid-fire strobe** for hype beats — the existing PACE_CUT_SEC "hype"=1.6s cadence
    already covers this; correct to exclude hype from the new layouts (gap card agrees).
- **Motion comics (DC/Marvel, Watchmen 2008, Astonishing X-Men)** — canonical
  conventions (see Wikipedia "Motion comic"): individual panels are *expanded to full
  shot* while narrated; panels are never shown as a static grid for long — the camera
  either dwells on one panel or animates the transition between them. Key takeaway:
  **a grid is a transient state**, used for build-up/recap moments, then the camera
  commits to one panel. Our grid-highlight template should therefore end by punching in
  on the final panel when the last beat lands (natural handoff back to single-panel).
- **Guided View (ComiXology) reading-order rules** — the de-facto standard for
  panel-by-panel comic reading: strict left→right, top→bottom (right→left per row for
  manga), one panel focused at a time, neighbors visible but de-emphasized, camera moves
  are simple pans/zooms between panel centroids. The in-house `xycut` in make_short.py
  (lines 197-228) already implements the manga right→left ordering rule
  (`spans = spans[::-1]` on vertical cuts) — reveal order must reuse this.

### 1.2 Open-source projects

Searched GitHub for "manga panel video animation", "comic guided view panel",
"comic reader animation", "panel-by-panel viewer":

- **appmancer/comic-reader** (Python, 0 stars, active) — "Deterministic guided-view
  generation for comics: finds speech balloons, derives panel layout, packs them into
  ordered reading beats." Closest conceptual match: their notion of *ordered reading
  beats* = our narration-beat mapping. Worth a skim during build for the balloon-aware
  packing idea (our quality guard "no cropping of bubbles" would benefit), but too small
  /young to depend on. BUILD-OURSELVES confirmed.
- **Sayandeep1013/PanelWeaver** — spec-only repo (single markdown, no code). A
  manga→narrated-video pipeline spec similar to ours; nothing implementable to reuse.
- **ag6778057-prog/manga-image-to-video** — single-panel subtle-motion tool (breathing/
  camera moves); no multi-panel layout logic.
- Kindle "Panel View" / comic reader apps (Tachiyomi forks etc.) implement guided view
  as pan/zoom between panel rects on the *page image* — an alternative worth noting:
  instead of compositing separate crops, keep the page and animate a spotlight over it.
  Rejected for v1: our pipeline works from segmented panel crops (panels_clean/), pages
  may not exist as clean plates, and blur_bg fitting is per-crop.

**Conclusion:** no off-the-shelf tool does narration-synced multi-panel composition.
Build ourselves, as the gap card expected.

### 1.3 Layout/timing patterns extracted (the shortlist)

| Pattern | When it shines | Complexity |
|---|---|---|
| Stack reveal (accumulate) | 3-4 sequential story beats, action progression | Low |
| Grid + moving highlight | 3-4 simultaneous/contrasting moments ("meanwhile…") | Medium |
| Sequential wipe/split | exactly 2-3 panels, cause→effect | Low (xfade exists) |
| Page spotlight (pan on page) | clean full pages available | Deferred (v2) |

---

## 2. ffmpeg techniques (syntax verified against ffmpeg-filters docs)

### 2.1 xstack — static grid composition
```
xstack=inputs=4:layout=0_0|0_h0|w0_0|w0_h0:fill=black
```
- `layout` uses `wX`/`hX` references summed with `+` (e.g. `0_h0+h1`); `grid=COLSxROWS`
  is a simpler alternative but **requires equal row heights / column widths** — our
  panels are heterogeneous, so `layout` + pre-scaled inputs is the safe form.
- **Pitfall (verified in docs):** "All streams must be of same pixel format"; different
  sizes leave gaps or overlap. So every cell input must be scale/padded to its exact
  cell size first.
- **Limitation:** xstack has no per-input timing — cells appear for the whole duration.
  Reveal timing must come from overlay `enable=` instead (or from feeding xstack
  pre-timed sub-streams, which is more graph plumbing for no gain).
- **Verdict: not the primary tool.** Use overlay chains; xstack only useful if we ever
  want a static contact-sheet plate, which PIL builds more controllably anyway.

### 2.2 overlay + enable='between(t,a,b)' — timed reveals (PRIMARY)
Timeline editing (docs §5) is supported by `overlay`. Canonical reveal chain:
```
[base][p0]overlay=x=X0:y=Y0:enable='gte(t,0.00)'[v0];
[v0][p1]overlay=x=X1:y=Y1:enable='gte(t,2.31)'[v1];
[v1][p2]overlay=x=X2:y=Y2:enable='gte(t,4.87)'[v2]
```
- Use `gte(t,T)` not `between(t,a,b)` for accumulating stacks (panel stays once
  revealed); `between` for transient highlights.
- **Slide-in entrance** via x/y expressions with `t` (evaluated per-frame by default,
  `eval=frame`):
  ```
  overlay=x='if(lt(t,T+0.25), W - (t-T)/0.25*(W-X0), X0)':y=Y0:enable='gte(t,T)'
  ```
  eases a panel from off-canvas right to its slot over 0.25s. Docs confirm `t`, `W/H`
  (main), `w/h` (overlay) are available in x/y expressions.
- **Pitfall:** `t` is NAN if input timestamps are unknown → every looped-image input
  must be given a real timebase (`-loop 1 -framerate 30 -t DUR -i plate.png`) and
  ideally normalized with `setpts=PTS-STARTPTS` per input (docs explicitly recommend
  this for overlay inputs whose initial timestamps differ).
- **Pitfall:** expressions containing commas must be quoted inside the filtergraph
  (`enable='gte(t,1.5)'`) — the codebase already does this in the beat-gain volume
  expression (panel_render.py:987-989), same escaping style works.

### 2.3 Per-cell highlight (grid template)
Two verified mechanisms, combined:
- **Dim non-active cells:** `drawbox=x=CX:y=CY:w=CW:h=CH:color=black@0.5:t=fill:
  enable='between(t,a,b)'` stamped over each inactive cell window. drawbox supports
  timeline editing and `t=fill`. Alternative: pre-render a dimmed copy of the whole
  plate with PIL and cross-enable two overlays — fewer filters, but N highlight windows
  need N plate variants (N images vs N drawboxes; drawbox wins for N≥3).
- **Active-cell scale pop:** cheapest honest version is brightness contrast only
  (dim others, leave active at 1.0). A true scale-up per cell inside one graph requires
  a per-cell `scale` + re-overlay per window — graph size grows O(N²). v1: brightness
  highlight + a final zoompan punch-in on the last cell. Keep scale-pop for v2.

### 2.4 zoompan per input / on composite
- Existing pattern (panel_render.py:651): zoompan AFTER compositing, on the supersampled
  composite — reuse unchanged for the "settled" phase of stack reveal (slow 1.0→1.05 on
  the full stack once all panels are in).
- zoompan resets `on` per input image; with `-loop 1` single-image input `d=frames`
  drives the whole clip — the codebase's `d={frames}:s={w}x{h}:fps={mv.FPS}` idiom is
  correct, keep it.
- **Pitfall:** zoompan *outputs* a new timebase/fps stream; do NOT put zoompan before
  timed overlays unless you re-anchor time (`setpts=PTS-STARTPTS`) — safest graph order:
  scale/pad cells → timed overlays on the static base → zoompan/effects LAST → format=yuv420p.

### 2.5 xfade (sequential wipe template)
`_slide_join` (panel_render.py:508-528) already implements xfade slide transitions
between equal-length subclips. The wipe template is a *parameterization* of it:
- Use `transition=wipeleft|wipeup|smoothleft` etc. with `offset=` set from **sentence
  boundaries** instead of the uniform `sub_dur` grid — that alone converts "evenly-timed
  slides" into "narration-synced wipes" with ~10 lines of change.
- **Pitfall (verified in docs):** xfade requires both inputs "constant frame-rate and
  have the same resolution, pixel format, frame rate and timebase." Since subclips come
  from our own `render_panel_scene` (uniform `mv._INTER_V`, same WxH, same fps) this
  holds today — but any new subclip path must keep encoding through `mv._INTER_V`.

### 2.6 Timebase / fps pitfalls (repo-specific)
- panel_render.py:896-925 documents the real hazard: per-scene clips historically come
  out with mixed timebases (1/30000, 1/15360, 1/737280) and copy-concat breaks; the fix
  is remux to `CONCAT_TIMESCALE = 15360`. **Any new compositor output joins the same
  concat path, so it inherits the normalization for free** — but the new render must
  still (a) encode with `mv._INTER_V` and (b) produce exactly `-t {dur:.3f}` so the
  audio re-attach (`apad` + `-t`) clamps drift like existing paths do.
- Keep `fps=mv.FPS` (30) on any zoompan and `-framerate 30` on image loops; mismatched
  input framerates in one filtergraph make `enable=` windows land on wrong wall-times.

---

## 3. Kinetic layout inspiration (motion comics, guided view, papers)

- **Motion-comic grammar** (Watchmen/Marvel-Neal-Adams releases): (1) camera commits to
  one panel per narration beat; (2) grids appear only as transitional/recap states;
  (3) text/bubbles removed or de-emphasized because narration replaces them; (4) 10-20
  min episodes = ~3-6s per panel — matches PACE_CUT_SEC "normal"=3.5s. Validation that
  reveal beats should target ~2-5s per panel; if sentence boundaries produce a <1.2s
  beat, merge it with its neighbor.
- **Guided View rules** (ComiXology convention, mirrored by Kindle Panel View):
  reading order sacred (manga: right→left within a row, top→bottom rows); focus moves
  monotonically forward, never backward. → Reveal order = the scene's panel list order
  (which stage-4 scripting already emits in story order; see FORWARD-ONLY comment at
  panel_render.py:807).
- **Automatic comic layout research** (for the build's geometry heuristics, not deps):
  the classic XY-cut recursive gutter split is what make_short.py already implements
  (lines 182-228); academic work on comics-to-video ("comic ken burns", manga109-based
  panel-order estimation) consistently uses (a) XY-cut or connected-component panel
  extraction, (b) aspect-ratio-driven layout packing, (c) saliency for focal points.
  Our v1 needs only (a)+(b): choose stack axis by mean panel aspect (wide panels →
  vertical stack; tall panels → horizontal row), which is the standard packing
  heuristic in these papers.

---

## 4. In-house prior art: make_short.py plate approach (lines 167-283)

Directly reusable assets:
- **`xycut(img)`** (197-228): recursive white-gutter XY-cut with manga right→left
  ordering. Reuse if a scene references a *page* rather than pre-segmented panels.
- **`panel_crops`** (231-253): sliver filtering (`w ≥ 0.25*Wp`, `h ≥ 0.12*Hp`,
  area ≥ 5% page) — same spirit as panel_render's `_panel_ok` (MIN_PANEL_DIM=180,
  MIN_PANEL_AREA=120k px²). The compositor should apply `_panel_ok` per cell *after*
  computing cell sizes (a panel that is fine fullscreen may die inside a 1/3 cell —
  this is exactly the ≥45%-height guard).
- **`build_plate`** (259-283): the key pattern — **PIL pre-composites a fixed-size
  plate (1350x2400 = 1.25x canvas so zoompan has headroom), tall crops fill the art
  band, wide crops fit width, then ffmpeg only does zoompan on a single image.** This
  is the cheapest possible compositor: all layout math in Python (testable, no
  filtergraph escaping), ffmpeg does one dumb zoompan per segment.

**Design consequence:** hybrid approach. PIL builds the *final* composite plate (and,
for grid highlight, one dimmed variant); ffmpeg's job reduces to timed overlays of
per-cell PNGs onto a blurred-bg base + one zoompan at the end. Cell PNGs are just
PIL-cropped/resized files — no scale filters inside the graph, sidestepping most
pixel-format/size pitfalls in §2.1.

---

## 5. Concrete build-ourselves design

### 5.1 Trigger condition
In `render()` after the panel-fixing block (panel_render.py:791) and pace resolution
(795-798):
```python
use_smart = (args.layout == "smart"
             and len(resolved) >= 3
             and pace != "hype"
             and not para_done)          # parallax keeps priority
```
Then attempt `plan = plan_multipanel_layout(resolved, w, h, sent_bounds)`; if it
returns None (guards failed), fall through to the existing n_cuts/sequential path
untouched.

### 5.2 Beat extraction from edge-tts word timings
Word timings `wt` = `[{word, start, end}, ...]` are already absolute within the scene
(after `_tts_with_pauses` + `_truncate_silence` remapping). Beat boundaries:
1. Split narration into sentences/clauses on `. ! ? … ;` (and `,` only if needed to
   reach N beats). Map each sentence to its word span by walking `wt` in order
   (the caption code in make_short.build_ass shows the word↔text alignment idiom).
2. Beat k's reveal time = `start` of its first word, minus a 0.12s pre-roll (panel
   lands just before the line about it begins — motion-comic convention).
3. If `#sentences > #panels`: merge shortest adjacent beats. If `<`: split the longest
   sentence at its largest inter-word gap (silence truncation keeps real gaps ≥
   TRUNCATE_SILENCE_MS floor, so gaps are meaningful).
4. Enforce min beat length 1.2s (merge forward if violated). Last beat extends to
   scene end — the composite + slow zoompan carries long tails (kills ping-pong).

### 5.3 Layout templates (v1 ships two, wipe is a cheap third)

**A. Stack reveal** (default for 3-4 panels of similar aspect)
- Axis: vertical video (h>w) or wide panels → vertical stack of N rows; tall panels /
  horizontal video → horizontal row of N columns. Cell size: frame divided evenly along
  the axis minus 8px gutters; panel fit-inside cell (force_original_aspect_ratio=
  decrease equivalent in PIL, `Image.thumbnail`).
- Base = blurred/darkened composite of all panels (reuse the gblur=24 / brightness
  -0.14 recipe from render_panel_scene:647-648, but done once on the PIL plate exported
  as base.png).
- ffmpeg graph: base loop → per-cell PNG overlays with
  `enable='gte(t,Tk)'` + 0.25s slide-in x/y expression (direction alternating like
  `_SLIDE_DIRS`) → after last reveal, zoompan 1.0→1.05 on composite → fx suffix (focus
  vignette if scene has focus) → format=yuv420p. Audio attach identical to existing
  multi-cut path (apad + `-t dur`).

**B. Grid highlight** (3-4 panels, mixed aspects, narration with "meanwhile/at the same
time" texture — or simply when stack cells would violate the height guard but grid
cells wouldn't... in practice: 4 panels → 2x2, 3 panels → 2-over-1 or 1x3 by aspect)
- PIL builds two plates: `plate_lit.png` (all cells full brightness) and per-beat the
  graph dims *inactive* cells with `drawbox=...color=black@0.5:t=fill:
  enable='between(t,Tk,Tk+1)'` (N cells × N beats boxes, N≤4 → ≤12 drawboxes, fine).
- Active cell additionally gets a thin accent border (drawbox t=6, brand red) during
  its window.
- Final beat: punch-in — crossfade (existing xfade path) or zoompan from plate to the
  last panel rendered fullscreen for the remaining tail. Reuses render_panel_scene for
  that tail subclip; joined with `_slide_join`-style xfade.

**C. Sequential wipe** (3 panels, strongly sequential narration — v1 optional)
- Reuse `_slide_join` but pass per-beat offsets (sentence boundaries) instead of the
  uniform `sub_dur` grid, and pick wipe transitions (`wipeleft/wipeup/smoothleft`).
  Smallest diff, ships almost free; scheduled after A and B prove out.

Template selection heuristic (deterministic, no LLM): 4 panels + mixed aspect → grid;
else stack; scene JSON may force via optional `"layout": "grid"|"stack"|"wipe"` field.

### 5.4 Phone-readability guard & fallback
For each candidate template compute every panel's *highlighted* display height:
- stack: cell height when revealed (before final zoom) — for N=3 vertical stack on
  1440p horizontal (h=1440... actually per --res), cell_h ≈ (h - gutters)/3 ≈ 31% —
  **stack of 3+ full-height cells fails the ≥45% guard on equal division**; so stack
  reveal must use an *accordion* layout: the newest panel gets 55-60% of the axis,
  previously revealed panels compress to share the rest. Guard then checks the panel at
  its **moment of highlight** (accordion focus ≥45% ✓) not its resting size.
- grid: active cell height ≥ 0.45*h is impossible in a true 2x2 (50% minus gutter is
  borderline ✓ at 8px gutters; 2-over-1 bottom cell ✓; 1x3 columns give full height ✓
  for horizontal video).
- If no template satisfies the guard for all panels (e.g. 6+ panels, or micro-panels
  that `_panel_ok` barely passed), `plan_multipanel_layout` returns None → existing
  sequential-cut path runs (zero behavior change). Also fall back when the scene
  duration < N*1.2s (beats too short to read).
- blur_bg fitting per cell: fit-inside always (never crop) — respects "no cropping of
  bubbles"; bubbles guard upgrade (balloon detection à la appmancer/comic-reader) is v2.

### 5.5 Flag & integration point
- `--layout` argparse choice `{"seq","smart"}` default `"seq"`, added next to
  `--no-slide` (panel_render.py:1242). `"seq"` = today's behavior bit-for-bit.
- New module `pipeline/layout_smart.py` (planner: beats, template pick, PIL plates,
  guard) + `render_multipanel_scene(plan, audio, clip, w, h, dur)` in panel_render.py
  (the ffmpeg graph builder), called from the branch described in §5.1. The planner is
  pure-Python (PIL/numpy only — both in venv) → unit-testable without ffmpeg.
- Output clip encodes via `mv._INTER_V` / `mv._INTER_A`, duration clamped `-t dur`,
  then flows into the untouched `concat_copy` 15360-timescale normalization.

### 5.6 Cost estimate vs acceptance criteria
- Wall-time: PIL plate ≈ instant; single ffmpeg pass with ≤4 overlays + ≤12 drawboxes +
  1 zoompan ≈ comparable to ONE render_panel_scene call — vs N calls today. Likely
  *faster* than sequential for the same scene; ≤1.5x criterion is safe. (Grid template's
  fullscreen tail adds one render_panel_scene + one xfade join — still ≤2 encodes vs
  N+1 today.)
- irrelevant_panel: narrated panel is always the lit/most-recent one → direct fix.
- static_scene: reveals every 2-5s + slide-ins + final zoom → motion present throughout;
  long tails play over full composite, not a frozen re-shown panel.
- Risks (ranked):
  1. **Beat↔panel misalignment** — sentence k may not describe panel k (script's panel
     list vs narration order drift). Mitigation: forward-only mapping (same philosophy
     as line 807) + optional per-scene `"beats": [...]` override for the scripting
     stage to fill later.
  2. Filtergraph expression escaping bugs (commas in enable=) — mitigate with a tiny
     graph-builder helper + a golden-scene smoke test.
  3. Readability at 1080x1920 Shorts reuse — guard is computed per --res, so vertical
     runs recheck automatically.
  4. Panels with baked-in text too small in cells even at 45% — the guard uses frame
     height, not text height; if the reviewer still flags phone_readability, tighten
     guard to 50% or restrict grid to 3 panels max.

### 5.7 Experiment plan (next step, not this doc)
1. Implement planner + stack reveal only; render golden chapter with `--layout smart`.
2. Run reviewer; compare irrelevant_panel + static_scene counts on 3+-panel scenes vs
   baseline (target ≥40% drop), check no new phone_readability faults, log wall-time.
3. Add grid highlight; A/B on the 2-3 densest scenes.
4. Wipe template last, only if reviewer flags monotony.
