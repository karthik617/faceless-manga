# gap-017: Text-only / SFX-only / cover-fragment panels slip the panel verifier

**Stage:** script | **Fault types:** `irrelevant_panel` (bubble-only, SFX-card, cover-fragment subclass) | **Priority:** 3

## Problem
The adopted gap-001 verifier (`pipeline/verify_panels.py`, default-on) failed to
stop 11 panels in ch4 that a manual pass had to drop/replace — nearly all
"bubble-only cutouts", "SFX-text-only panels", "text-box cutouts", or a chapter
cover card picked into scenes. Three compounding mechanisms:

1. **Tier 1 trusts the OCR `kind` tag, and the tag is wrong for cutout
   fragments.** 9 of the 11 ch4 offenders carry `kind=story, quality=ok` in
   `the-world-after-the-fall-ch4.ocr.json` — the vision OCR describes what the
   text *says* and frames it as a story beat (p0011: "A close-up of text in a
   speech bubble calling out a name" → story/ok; p0060: "Two black speech
   bubbles contain the creature's monologue" → story/ok; p0095: "Dark liquid
   forms Korean characters" → story/ok). The `HARD_DROP_KINDS` /
   `kind=="textonly"` gate (`verify_panels.py:56-59`, tier1 at :353-389) never
   fires because the tag says story.
1b. **The tier-1 textonly quote-exception keeps correctly-tagged text cards
   whenever the narration quotes them** (`verify_panels.py:363-369`): the
   remaining 2 offenders, p0021/p0022, ARE tagged `kind=textonly`, but the
   scriptwriter had woven their text verbatim into the scene-6 narration
   ("I started tower raids later than other walkers…", pre_verify:170), so
   `_has_verbatim_quote` sanctioned them — and the render is still a black
   text card the reviewer flags HIGH (2:21–2:35). Same mechanism let
   p0031/p0032 through (scene 9, auto-dropped round 1, review_run.log:73-77).
   Quoting a NARRATION-box panel is not the same visual promise as quoting a
   speech bubble a character utters — the exception is too broad.
2. **Tier 2's lexical score REWARDS these panels.** `relevance_score`
   (`verify_panels.py:153-166`) overlaps narration tokens against the panel's
   scene_beat + dialogue + narration_text — a bubble whose text the narration
   quotes/paraphrases is the *most* lexically relevant panel in the scene while
   being the *least* visual. Ch4 `_verify` scores: p0011 = 0.444 (highest-ish
   in scene 3), p0021 = 0.467, p0022 = 0.400 — all far above T_LOW = 0.15, so
   tier-3 vision (only fired when the scene's BEST score < 0.15,
   `verify_panels.py:558-563`) never inspects them.
3. **No geometric/text-coverage signal exists anywhere.** The offenders are
   tiny strip fragments per `panels/panels_index.json` (p0011 = 243px tall,
   p0073 = 251, p0022 = 273, p0021 = 290, p0027 = 301, p0062 = 357, p0040 =
   439, p0060 = 608 — at 690px width), i.e. bubble cutouts that
   `segment_panels.py` legitimately keeps (its floor is
   `max(48, min_slice_px//4)` ≈ 55px, and 220px+ slices are standalone by
   design — segmentation is doing its job; the *pick/verify* stage is where
   "this fragment is only text" must be decided).

Secondary finding: the OCR read stream can be **misaligned ±1 vs the actual
panel images** — the manual pass identifies p0075 as a "chapter cover/credits
card" and p0076 as the "5 YEARS LATER" time-skip panel, while the OCR file has
the cover at p0074 and "5 YEARS LATER" at p0075. So even a correct `kind=cover`
tag can protect/condemn the wrong panel. (Same smell in the p0024/p0025 region:
the manual fix "added p0025 (training-montage art)" but the OCR tags p0025 as a
'PIERCING!' title card, kind=cover, and describes montage art at p0024.) Any
fix that leans harder on OCR tags must first sanity-check read↔image alignment.

## Evidence
- `output/the-world-after-the-fall-ch4/review.md:15-29` (manual resolution
  log): 11 panels dropped/replaced across 2 fix rounds — scene 3 p0011
  ("JAE HWAN" bubble-only cutout), scene 6 p0021/p0022 (text-only), scene 8
  p0027 (black text-box cutout), scene 12 p0040 ("THEN!" SFX card), scene 13
  p0042 (Korean-SFX-only; produced the 3 HIGH "blurred close-up" flags at
  4:42–4:54, review.md:66-68), scene 18 p0060 (floating speech-bubble-only,
  2 HIGH at 6:54/7:00, review.md:70-71), scene 19 p0062 ("NIGHTMARE'S STONE?"
  bubble-only), scene 22 p0073 (bubble-only), scene 23 p0075 (cover/credits
  card, replaced by p0076), scene 28 p0095+p0096 (SFX-text-only).
- Ch4 pre-fix fault load: 18 HIGH `irrelevant_panel` total (review.md:2); the
  majority trace to these panels across the two review rounds (final-list
  HIGHs at scenes 6/13/18 plus the round-1 flags that forced the other 8
  drops); 7 of the 18 were reviewer false positives (separate — gap-015).
- Systemic across chapters, not a ch4 one-off:
  - `output/the-world-after-the-fall-ch2/review.md:10` (0:50 scene 2 "manga
    sound effects and speech bubble"), :19 (3:24 scene 7 "partial text only"),
    :25-26 (5:47/5:53 scene 12 "only stylized text/sound effect") — 4+ HIGH.
  - `output/the-world-after-the-fall-ch3/review.md:5-8` (2:31–2:47 scene 6
    title-overlay card ×4 HIGH), :17 (10:42 scene 24 "Title card text
    overlay") — 5 HIGH.
- Verifier mechanics: `pipeline/verify_panels.py:56-59` (kind-gated tier 1),
  :153-166 (lexical proxy includes the panel's own text), :558-563 (tier-3
  only on best-score < 0.15). Ch4 `_verify` log scores confirming the reward
  effect: `the-world-after-the-fall-ch4.json:644-655` (p0011 → 0.444),
  :698-708 (p0021 → 0.467, p0022 → 0.400).
- Fragment geometry: `output/the-world-after-the-fall-ch4/panels/panels_index.json`
  bbox heights listed above (243–608px at 690px width).
- OCR↔image misalignment: `the-world-after-the-fall-ch4.ocr.json` p0074/p0075
  reads vs review.md:27-28 manual identification of p0075 as the cover card.

## Candidate directions
(brief — researcher to expand)
1. **Deterministic text-coverage gate at verify time**: run the existing DBNet
   text-box detector (`pipeline/text_boxes.py`, already a gap-004/009 dep) on
   each picked panel; classify as effective-textonly when text-box area covers
   most of the panel's ink area (or ink outside text boxes is below a floor).
   Apply the existing tier-1 textonly rule to that classification instead of
   trusting the OCR `kind` tag. Zero new deps, deterministic, benchmark-able.
2. **Score the panel's VISUAL proxy only**: exclude `dialogue`/`narration_text`
   from the tier-2 relevance proxy for suspected-fragment panels (geometry
   below a threshold), so quoted text stops masquerading as visual relevance;
   force tier-3 vision on suspects regardless of scene best-score.
3. **OCR read↔image alignment self-check**: cheap per-panel consistency probe
   (e.g. does a kind=cover read land on a panel whose text boxes/aspect look
   like a cover?) before any kind-gated decision; flag misaligned regions.
4. **Aspect/height prior**: strip fragments under ~450px tall with ≥1 text box
   and near-zero non-text ink are bubble cutouts with very high prior; cheap
   pre-filter before 1-2.

## Acceptance criteria
- **New metric required**: per-panel *text-coverage ratio* (text-box area /
  panel ink area, from `pipeline/text_boxes.py` boxes) — deterministic,
  scriptable; the experiment must add a gate script that emits it per panel.
- Deterministic replay on ch4 (no new reviews needed): running the modified
  verifier on `the-world-after-the-fall-ch4.json.pre_verify` +
  `the-world-after-the-fall-ch4.ocr.json` drops/replaces/vision-escalates
  ≥9 of the 11 manually-identified panels (p0011, p0021, p0022, p0027, p0040,
  p0042, p0060, p0062, p0073, p0095, p0096; p0075 may require the alignment
  fix and can be excluded from the 9 if documented).
- Zero false drops on ch4's known-good picks: every panel present in the
  manually-fixed final script (`the-world-after-the-fall-ch4.json` scenes,
  which re-reviewed 0-fault) survives the new gate — including the
  deliberately-kept system-notification UI panels in scene 23 (review.md:42-43)
  and the kept textonly-quoted panels.
- Golden chapter (ch2) benchmark via `pipeline/benchmark.py` +
  `review_stable.py` (gap-011): HIGH `irrelevant_panel` on the text/SFX-only
  scenes (2, 7, 12) drops 4 → ≤1; no per-type veto regressions (subject to
  gap-015 matched-veto arithmetic once landed).
- No scene left panel-less (substitution must fire, same as gap-001).
- Script-step wall-time increase ≤1.5x (DBNet already runs elsewhere in the
  pipeline; batching acceptable).

## Quality guards
- Fail open: if text-box detection errors on a panel, treat it as story/ok
  (matching the verifier's existing missing-OCR behavior at
  `verify_panels.py:328-338`) — an inference failure must never delete panels.
- The tier-1 textonly quote-exception stays: a text panel the narration
  literally quotes remains keepable (payoff appends must not be undone).
- Substituted panels obey reading order / no-spoiler window (gap-001 guard).
- Byte-identical script output when the new gate is flag-disabled.
- Do NOT change segmentation floors in `segment_panels.py` to "fix" this —
  fragments riding with adjacent art would regress gap-004's adopted guards;
  the decision belongs at pick/verify time.
