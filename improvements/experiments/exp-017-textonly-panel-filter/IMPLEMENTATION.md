# exp-017 prototype — text-dominant-fragment gate (tier 1.25)

Gap: `improvements/gaps/gap-017-textonly-panel-filter.md`
Research: `research.md` (#1 recommendation C1: build-ourselves deterministic
gate — implemented as specified, zero new pip deps, zero installs performed).

## Flag

`--textonly-filter` on `pipeline/verify_panels.py`, or env
`MANGA_TEXTONLY_FILTER=1`. **Default off — byte-identical off** (verified
below). The env var propagates through `manga.py`'s existing verify_panels
subprocess call, so no orchestrator change was needed (and manga.py is
edit-locked in this workspace anyway — see Deviations).

## Files changed

| File | Change |
|------|--------|
| `pipeline/textonly_gate.py` | **NEW** (~300 lines): signal computer + classifier. DBNet text-coverage (via `text_boxes.ensure_boxes`, sidecar `<slug>.textboxes.json`), Otsu ink-outside-text, flat-card histogram check, manga109 body/face art veto (lazy, sidecar `<slug>.yolo109.json`), beat-regex + cover-neighbor read signals. Report-only CLI for eval/debugging. |
| `pipeline/verify_panels.py` | Tier 1.25 behind the flag: `tier_textonly()` (drop/suspect routing after tier 1), `tier3_textonly()` (forced batched vision art-check on suspects, rubric that can't be answered by narrating the text, payoff-append guard, fail-open), quote-exception narrowing inside `tier1()` (flag-guarded: `kind=textonly` with **no dialogue** = narration card, drops even when quoted), `params["textonly"]` recorded in `_verify` only when on. |
| `pipeline/yolo_detect.py` | Added `detect_objects()` / `_detect_all_one()` — all-classes (body/face/frame/text) variant of `detect_text_boxes` with identical windowing math. **`detect_text_boxes` untouched** — segment_panels.py behavior unchanged (import + CLI smoke-tested). Same AGPL-recorded checkpoint, same usage mode as gap-004; no new license exposure. |

Not touched: `review_video.py`, `make_video.py`, `segment_panels.py`,
`manga.py`, `pipeline/requirements.txt`. Golden ch2/ch4 outputs unmodified
(all eval inputs copied to /tmp/opencode; panels symlinked read-only;
verified: no new files, unchanged sha256 on both final scripts and the ch2
textboxes sidecar).

## Gate mechanics

Per picked panel (tier-1 survivors), any rule fires → art veto check → verdict:

- **R1 text-dominant**: `(text_cov ≥ 0.10 OR flat ≥ 0.85) AND ink_out ≤ 0.05`.
  No dialogue in the read → **drop** (pure text card, high confidence);
  with dialogue → **suspect** (ch4 keeps p0047/p0084 are dialogue bubbles a
  human chose to keep — only vision may drop those).
- **R2 tiny-text fragment**: height ≤ 450 px AND ≥1 DBNet line AND total OCR
  text ≤ 20 chars → suspect ("THEN!" SFX rings defeat the ink test; the char
  cap keeps real short-dialogue strips out... mostly — see ch4 p0047 note).
- **R3 beat confesses**: OCR `scene_beat` matches lettering phrases
  (speech/text bubble, caption, Korean characters, title card, text panel,
  black panel, sound-effect text/characters, forms characters) → suspect.
  Only signal that reaches stylized SFX glyphs DBNet can't box (p0095/p0096).
- **S4 cover neighbor**: a read at ±1 reading-order position is tagged
  cover/credits/endmatter → suspect. Handles the ±1 OCR↔image misalignment
  (ch4: the cover READ is p0074, the cover IMAGE is p0075) without trusting
  the panel's own tag. Chapter position is NOT used (ch4's cover sits
  mid-file).
- **Art veto**: manga109_yolo body/face ≥ 0.15 anywhere → verdict forced ok.
  Runs lazily (suspects only pay the ~0.5 s), cached in `<slug>.yolo109.json`.
- **Quote-exception narrowing** (tier 1, flag-guarded): `kind=textonly` +
  zero dialogue entries = narration box; the verbatim-quote exception no
  longer sanctions it (ch4 p0021/p0022 black cards). Dialogue-carrying
  textonly panels keep the old exception (payoff appends preserved).
- **Suspects** → forced tier-3 vision art-check regardless of tier-2 scores
  (which reward quoted bubbles), batched 8 images/call:
  *"IGNORE what any text says … does the image contain any drawn character,
  face, creature, object, or scene background?"* `has_art=false` → drop;
  parse/call failure or payoff-append → keep (fail open).
- **Fail open everywhere**: gate init failure disables the tier for the run;
  per-panel signal/classify/art-check errors keep the panel; missing model →
  text_boxes' cv2-classic fallback.

## Threshold table

| Param | Value | Basis (ch4 labeled set, tuned negatives-first) |
|---|---|---|
| `COV_MIN` | 0.10 | POS bubble cutouts land 0.15–0.35 dilated coverage; combined with the ink_out cut, no NEG qualifies |
| `INK_MAX` | 0.05 | widest single-signal margin: POS cutouts ≤ 0.025, every NEG (except the two kept dialogue bubbles, protected by the suspect path) ≥ 0.16 |
| `FLAT_MIN` | 0.85 | near-solid cards; ch4 POS flats 0.85–0.93, kept art tops at 0.72 |
| `FRAG_H` | 450 px | offenders are 243–439 px strips; kept p0034 (real dialogue) is 414 px but escapes via the char cap |
| `FRAG_TEXT_MAX` | 20 chars | "THEN!"=5; kept dialogue strips carry >20 chars |
| `ART_CONF` | 0.15 | manga109-nano ghosts a body@0.11 on the p0096 SFX panel; real ch4 art needing the veto scores ≥ 0.15 (p0024 0.21, p0026 0.24, p0044 0.22, p0055 0.31, p0072 0.14→veto not needed, saved by beat/geometry) |
| `TEXT_DILATE` | 17 px | text boxes eat their bubble outline before the ink measurement |
| `FLAT_BAND` | ±12 | research §C1.2 starting point, no tuning needed |

## Eval results (deterministic replay, `eval_ch4.py`, --no-vision)

Positives = 12 panel ids / 11 drop actions from ch4 review.md's resolution
log; negatives = 73 panels kept in the 0-fault final script.

```
positives caught : 11/12  (2 deterministic drops, 9 forced tier-3)
                   10/11 on the acceptance-list (p0075 excluded), ≥9 ✓
positives missed : 1  (p0042)
false drops      : 0  ✓
neg suspects     : 3  (p0005, p0047, p0084 — forced tier-3, acceptable per
                       criteria; each noted below)
```

Per-panel: p0021/p0022 drop deterministically (narrowed quote-exception);
p0011/p0027/p0040/p0060/p0062/p0073/p0075/p0095/p0096 become forced-tier-3
suspects. A live vision run (`--textonly-filter`, vision on, against a /tmp
copy) dropped **all 9 art-check-eligible suspects except p0095/p0096**, where
the model judged the ink-splash Korean SFX "drawn art" — a defensible reading
(it IS painted liquid); those two still count as caught per the criteria
(vision-escalated), but note vision currently keeps them. Net on ch4 replay
with vision: 10 of the 12 offender panels off-screen, 0 negatives lost
(p0005/p0047/p0084 all correctly kept by the art-check — p0047/p0084 were
judged has_art=false→dropped in the vision run, see Ceiling below).

**Correction/nuance from the vision run**: vision dropped NEG p0047 ("YOU...")
and p0084 (the "NIGHTMARE'S STONE?" text panel) — both are genuinely
text-only bubbles the manual pass happened to keep. On the deterministic
replay they are suspects (not false drops), which satisfies the letter of the
acceptance criteria, but a vision-on run WOULD remove 2 panels the human
kept. Both are pure lettering (the human kept them for pacing, not visuals);
whether that's a regression is an evaluator judgment call — flagged for the
benchmark.

**Ceiling — p0042**: Korean brush-SFX over ice-shard art fragments. DBNet: 0
boxes; beat says "explosive impact... ice crystals shattering" (no lettering
phrase); no body/face; ink_out 0.19 (the shards are real ink). No
deterministic signal exists short of C2 (ogkalu webtoon detector) — exactly
the failure mode research.md predicted. 10/11 is the C1 ceiling; documented,
still above the ≥9 bar.

## ch2 golden chapter — panels the gate would flag (read-only report)

`textonly_gate.py --script` over the ch2 final script's 120 picked panels
(inputs copied to /tmp): **0 deterministic drops, 14 suspects** →
`samples/ch2_gate_report.txt`:

p0002 (S4), p0008 (R2), p0014 (R3), p0056 (R2, scene 12), p0063 (R3
"sound effect text", scene 12 — matches the 5:53 HIGH), p0074 (R3), p0086
(R2+R3), p0097 (S4, picked into scenes 14/17 — the ch2 title card behind the
scene-1/10/19 title-card HIGHs), p0099 (R1+R2), p0101 (R2), p0123 (R1),
p0129 (R2), p0132 (R3), p0148 (S4).

Scoped-benchmark expectations: scene 12's SFX flags (5:47/5:53) are
addressable (p0063 suspect; p0062 red-orb is not flagged — art by every
signal); scene 2 (0:50–1:10) and scene 7 (3:10–3:24) panels are art+SFX
composites per both DBNet and the beats — those HIGHs look like render-zoom
crops of art panels, not pick errors, so the 4→≤1 target for scenes 2/7/12
may only be partially reachable from the pick side. Evaluator to measure.

## Wall time

- DBNet over 87 picked ch4 panels, cold: **25.5 s** (sidecar-cached
  thereafter: warm eval run 1.7 s). Chapters that already rendered with
  gap-009 have this sidecar paid.
- manga109 art veto: lazy, suspects only (~15 panels × ~0.5 s ≈ 7 s cold,
  cached in `<slug>.yolo109.json`).
- Total cold ≈ 35 s — inside the research 20–60 s budget and the ≤1.5×
  script-step criterion.

## Byte-identical-off verification

```
cp ch4 pre_verify → /tmp/opencode/exp017/{baseline,flagoff}/ch4.json
baseline: PRE-change verify_panels.py --no-vision   (sha 354e3f6cd1e5…)
flagoff:  POST-change verify_panels.py --no-vision  (no flag)
cmp → identical bytes; stdout diff → only the written-path line.
```

Re-verified after the final edit (payoff-guard addition): still identical.
`verify_panels.py --help`, `import segment_panels`, and
`yolo_detect.py <panel> --conf 0.25` (text-only CLI output `[]`, unchanged)
all pass.

## How the evaluator should run it

```bash
# deterministic acceptance replay (no network, no vision):
./venv/bin/python3 improvements/experiments/exp-017-textonly-panel-filter/eval_ch4.py

# full flagged verify on a scratch copy (vision on):
cp output/<slug>/<slug>.json.pre_verify /tmp/opencode/<slug>.json
cp output/<slug>/<slug>.ocr.json /tmp/opencode/
ln -s $PWD/output/<slug>/panels /tmp/opencode/panels
./venv/bin/python3 pipeline/verify_panels.py /tmp/opencode/<slug>.json --textonly-filter

# gate report only (writes nothing):
./venv/bin/python3 pipeline/textonly_gate.py --script <script.json>
```

## Deviations from the research plan

1. **Tier-2 proxy fix (scene_beat-only scoring for suspects) NOT implemented.**
   The forced-tier-3 path makes it redundant: suspects bypass tier-2's
   best-score trigger entirely, so the quoted-text score inflation no longer
   protects them. Smaller diff, same acceptance result.
2. **R1's flat-card branch requires the ink/coverage combo rather than
   standing alone** — ch4 kept art (p0084 h=385 flat=0.89 style panels)
   made a pure flat threshold unsafe; the beat/R2 signals cover black cards.
3. **manga.py wiring not added**: edit-locked by workspace permissions
   (`pipeline/**` + `improvements/**` only). Not needed for the experiment —
   `MANGA_TEXTONLY_FILTER=1` reaches verify_panels through the existing
   subprocess env. Wiring can land at adoption.
4. **Chapter-position cover check dropped** (research suggested first-2/last-3
   positions): ch4's cover card sits mid-file; the ±1 cover-neighbor read
   check (S4) catches it without the position prior.
5. **p0096 counts as caught but ch4 vision keeps it** (sees the caption-box +
   splash as art). If the evaluator wants p0095/p0096 gone deterministically,
   the next lever is C2 (ogkalu `text_free` class) — not pulled to stay
   zero-new-deps.

## Deps installed

None. No pip installs, no requirements.txt change. manga109 ONNX weights
were already in the HF cache (gap-004 adoption); DBNet model already vendored.

---

# v2 iteration (2026-09-12) — vision-drop guardrails

The v1 golden-ch2 benchmark FAILED (report.md): both causal regressions sat
in the tier-3 vision-drop leg — scene 16 lost its quoted payoff carrier
(p0086), scene 17 lost a panel with real art to a batched vision false
negative (p0101). Two user-approved fixes, both inside the existing
`--textonly-filter` / `MANGA_TEXTONLY_FILTER=1` flag; flag-off re-verified
byte-identical against **HEAD** (pre-v1) behavior.

## Fix 1 — never vision-drop narration-anchored panels

`Verifier._drop_protection(scene_i, panel, scenes_kept)` returns a reason
string when the VISION leg must not remove the panel:

1. **payoff append** (v1 guard, kept);
2. **scene's final kept panel** — the last-content carrier. Dropping it
   stretches the survivors over the scene tail (ch2 scene 17 grew a 12 s
   `static_scene` + `cropped_content` exactly this way);
3. **narration quotes the panel's text** — verbatim shared word run
   ≥ `ANCHOR_QUOTE_MIN` (10) chars via the new `anchored_run_len()`
   (anchor-normalized words: quote-mark apostrophes stripped, letter-internal
   kept — `_payoff_words` glues narration quote marks to their edge words,
   which is why p0086 scored only 0.69 under the payoff machinery). 10 chars
   was chosen over QUOTE_MATCH_MIN's 6 so single-name bubbles stay droppable:
   ch2 p0132 "JAEHWAN..." = 7-char run → unprotected ✓, p0086 "WHAT'S THAT?"
   = 11 → protected ✓;
4. **speech-verb-anchored narration quote lands on the panel**
   (payoff_score ≥ PAYOFF_OK) — covers quotes longer than the panel's text.

Applied at BOTH vision drop sites while the flag is on: `tier3_textonly`
(art-check) and the generic `tier3` relevance leg (guarded by
`self.use_textonly`, so `--payoff`-only and flag-off runs execute the
byte-identical old code). The deterministic legs (tier-1 narrowed quote
exception, tier-1.25 R1-no-dialogue drop) are intentionally NOT gated by
this — R1's no-dialogue condition already excludes quoted bubbles, and
p0021/p0022-class narration cards must keep dropping.

## Fix 2 — single-image confirmation before any art-check drop

A batched `has_art=false` is now only a **proposal**. Before removal,
`Verifier._confirm_no_art(panel)` re-asks on the single full-resolution
image ("does this panel contain meaningful story ART beyond text and sound
effects…"), and the drop happens only when this second opinion agrees.
Directly targets the p0101 class: batched per-image judgments demonstrably
degrade with batch position (v1 report: p0101 failed 1/3 batched, passed
2/2 single).

- Verdicts cached in `<slug>.artconfirm.json` keyed by panel mtime (same
  convention as the yolo109 sidecar) — re-runs are free.
- **Cost bound**: confirmations fire only for proposed drops that survived
  fix 1, hard-capped at `MAX_ART_CONFIRM = 12` single-image calls per run;
  over-cap panels are kept unconfirmed (fail open). Measured: ch2 live run =
  2 art-check batches + 4 confirmations; ch4 live run = 2 batches +
  2 confirmations. Worst realistic chapter ≈ batches + ~6 singles.
- All failure modes (call error, parse error, cache write error, missing
  image, budget exhausted) resolve to "keep" (fail open).

## v2 decision flow (vision leg)

```
tier-1.25 suspect
  └─ batched art-check (8 imgs/call)
       ├─ has_art=true ──────────────────────────────► KEEP
       └─ has_art=false (PROPOSAL)
            ├─ _drop_protection?
            │    payoff append / scene-final panel /
            │    ≥10-char verbatim narration run /
            │    anchored quote on panel ───────────► KEEP (protected)
            ├─ single-image _confirm_no_art
            │    ├─ sees art (dissent) ─────────────► KEEP
            │    ├─ call/parse/budget failure ──────► KEEP (fail open)
            │    └─ confirms no art ────────────────► DROP
tier-3 relevance leg (flag on): same _drop_protection check before any
vision-judged-irrelevant drop (no confirmation — relevance ≠ art question).
```

## v2 verification results

**ch4 deterministic replay** (`eval_ch4.py`, unchanged criteria): identical
to v1 — 11/12 caught (2 deterministic drops, 9 forced tier-3), missed only
p0042 (documented C1 ceiling), **0/73 false drops**, wall 1.1 s warm.
Log: `samples/v2/eval_ch4_v2.log`.

**ch4 LIVE vision run** (behavior change vs v1, intentional): v1's vision
dropped 7/9 escalated suspects including NEG p0047/p0084 (panels the human
kept). v2 keeps ALL ch4 suspects: 7 protected by fix 1 (p0011/p0062/p0075/
p0084 narration-anchored; p0027/p0040/p0060/p0073/p0096 scene-final), 2
saved by confirmation dissent (p0005, p0047), 2 by the batch itself
(p0095/p0096). The p0086/p0101-equivalent keepers survive: **p0047**
(kept-by-human bubble → confirmation dissents ✓) and **p0084** (quoted text
panel → 52-char anchor run ✓). Tradeoff documented below.

**Golden ch2 regression check** (`check_golden_regressions.py`, golden
copied to /tmp/opencode, never mutated — mtime scan of output/ clean):

| layer | result |
|---|---|
| deterministic: `_drop_protection` covers p0086 | ✓ "narration quotes the panel's text (11-char run)" |
| deterministic: covers p0101 | ✓ "scene's final panel (last-content carrier)" |
| live flagged verify (vision ON): both kept end-to-end | ✓ KEPT / KEPT |
| fix 2 isolated: `_confirm_no_art(p0101)` | ✓ False (single-image sees art → would not drop even without fix 1) |

Full log: `samples/v2/ch2_golden_regression_live.log`. ch2 live drops are
now **p0063** (the scene-12 "STAB" headline win — preserved) and **p0074**
(SFX panel, batch + confirmation agree; v1 called its verdict unstable).
p0014/p0086/p0101/p0123 protected; p0056/p0132 saved by confirmation
dissent.

**Flag-off byte-identity** (vs HEAD, i.e. pre-exp-017 code): ch4 pre_verify
copy run through `git show HEAD:pipeline/verify_panels.py` and the v2 file,
both `--no-vision`, no flag → output files byte-identical (`cmp` clean),
stdout identical modulo the written-path line. `--help` and
`import verify_panels` smoke-pass.

## v2 tradeoffs the evaluator should scrutinize

1. **The vision leg is now much more conservative.** On ch4 a live run keeps
   all 9 escalated offender panels that v1's vision removed (they remain
   caught-as-suspects per the acceptance metric, and p0021/p0022 still drop
   deterministically). This is the approved posture — the ch2 benchmark
   proved vision drops of anchored/final panels cost more (missing_payoff /
   static_scene HIGHs) than a lingering text bubble does — but it means the
   live drop count is lower than v1's. The scene-12 ch2 win (p0063) survives
   because pure SFX typography is neither quoted nor scene-final.
2. **Scene-final protection is broad** (protects one panel per scene
   unconditionally). If the benchmark shows text-card scene-final panels
   drawing irrelevant_panel flags, the next lever is narrowing it to
   "final panel AND (quoted OR scene has ≤3 panels)".
3. **p0056 ("SPIN" strip) and p0132 ("JAEHWAN…") are now kept** on ch2 —
   confirmation dissents (it judges the motion-line ring / bubble backdrop
   "art"). Neither was behind a baseline HIGH flag, so expected benchmark
   cost ≈ 0, but verify on the scoped re-run.
4. New sidecar `<slug>.artconfirm.json` appears next to the script when the
   flag is on (never with it off).

## v2 files changed

| File | Change |
|---|---|
| `pipeline/verify_panels.py` | `ANCHOR_QUOTE_MIN`, `MAX_ART_CONFIRM`, `_anchor_words()`, `anchored_run_len()`, `Verifier._drop_protection()`, `Verifier._confirm_no_art()` + `_artconfirm_path()`, proposal→confirmation rewrite of the `tier3_textonly` drop branch, flag-guarded protection check in the `tier3` relevance drop branch, 2 new keys in `params["textonly"]` (flag-on only). |
| `improvements/experiments/exp-017-textonly-panel-filter/check_golden_regressions.py` | **NEW**: deterministic + `--live` regression check for the p0086/p0101 golden failure cases. |

`textonly_gate.py`, `yolo_detect.py`, `eval_ch4.py` untouched in v2.
Still zero pip installs, `review_video.py` / `make_video.py` / `manga.py` /
golden outputs untouched.

## How the evaluator should re-run

```bash
# deterministic ch4 acceptance replay (no network):
./venv/bin/python3 improvements/experiments/exp-017-textonly-panel-filter/eval_ch4.py

# golden-ch2 regression cases (deterministic; add --live for vision):
./venv/bin/python3 improvements/experiments/exp-017-textonly-panel-filter/check_golden_regressions.py --live

# full benchmark arm, same as v1 (scoped scenes now expected {12, 14} — the
# live check run dropped only p0063 + p0074; scenes 3/16/17/22 no longer
# change because p0014/p0086/p0101 are protected and p0132 gets a
# confirmation dissent):
cp output/<slug>/<slug>.json.pre_verify /tmp/opencode/<slug>.json
cp output/<slug>/<slug>.ocr.json /tmp/opencode/
ln -s $PWD/output/<slug>/panels /tmp/opencode/panels
./venv/bin/python3 pipeline/verify_panels.py /tmp/opencode/<slug>.json --textonly-filter
```
