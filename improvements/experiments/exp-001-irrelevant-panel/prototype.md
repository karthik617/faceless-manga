# exp-001 prototype: panel↔narration relevance verifier

Implements the research recommendation (C6 deterministic gate + lexical score,
C7 batched vision escalation) as `pipeline/verify_panels.py`, wired into
`manga.py` behind an opt-in flag. Zero new dependencies (stdlib `difflib` +
token overlap; no rapidfuzz, no CLIP).

## Files changed

| File | Change |
|---|---|
| `pipeline/verify_panels.py` | NEW (~430 lines): module + CLI. Tier 1 deterministic gate, Tier 2 lexical relevance, Tier 3 batched `gateway.llm_vision` verify, reading-order substitution, `_verify` log. |
| `manga.py` | +10 lines in `step_script` + 1 new argparse flag. Verifier runs ONLY when `--verify-panels` or `MANGA_VERIFY_PANELS=1`. Default path byte-identical. |

NOT touched: `pipeline/script_from_panels.py`, `pipeline/review_video.py`
(fault definitions), `~/faceless-youtube/pipeline/make_video.py`.

## Flags / env

- `manga.py --verify-panels` — opt-in wire-in after the script step.
- `MANGA_VERIFY_PANELS=1` — same, via env (for unattended runs).
- `verify_panels.py` CLI: `--ocr <path>` (default: sibling `<slug>.ocr.json`),
  `--dry-run` (plan only, no writes), `--no-vision` (skip Tier 3, offline),
  `--t-low <float>` (Tier-2 ambiguity threshold, default 0.15).

## Behavior

1. **Tier 1 (deterministic, free):** drop `kind∈{cover,credits,endmatter}`
   always; drop `kind=textonly` unless the narration shares a ≥6-char verbatim
   word-run with the panel's dialogue/narration_text (case/whitespace
   normalized — word-granular so "no**thing**"≠"every**thing**"); drop
   `quality=cut` when the scene has another passing panel. Hook (scene 0)
   additionally requires `kind=story` AND `quality=ok`.
2. **Tier 2 (lexical, free):** score = token-overlap coefficient
   (narration vs scene_beat+dialogue+narration_text) + proper-noun boost
   (+0.15/shared name, cap +0.30). Scene ambiguous when best panel < 0.15.
   Golden-chapter calibration: real matches score 0.2–1.0; the weakest
   legitimate scene (hook, flash-forward panels) lands 0.125.
3. **Tier 3 (vision, gateway):** ambiguous scenes only, batched 4 scenes/call,
   strict-JSON yes/no per panel. Fail-open: gateway/parse failure keeps panels.
4. **Substitution:** any emptied scene gets the best-Tier-2 of the nearest 8
   `kind=story quality=ok` panels by reading-order index, preferring panels
   strictly between the previous scene's max index and next scene's min index
   (no spoiler jumps). If no story/ok panels exist at all, originals are
   restored — no scene is ever left panel-less.
5. **Logging:** every drop/substitution/score to stdout and to
   `script["_verify"]["verify_log"]`. First real run backs up the pristine
   script to `<slug>.json.pre_verify`. Re-runs on an already-verified script
   are no-ops (idempotent, matching the pipeline's cached-step convention).

## Fallback behavior (safety)

- Missing OCR read for a panel → treated as story/ok (fail open, logged warn).
- Tier-3 gateway error / JSON drift → panels kept (positive rejection required).
- Empty substitution pool → original panels restored + logged.
- `--dry-run` writes nothing; without the flag the default pipeline never
  invokes the verifier at all.

## Evaluator command sequence

```bash
cd ~/faceless-manga
SLUG=the-world-after-the-fall-ch2
PROJ=output/$SLUG

# 1. verify the script in place (backs up to $SLUG.json.pre_verify)
./venv/bin/python3 pipeline/verify_panels.py $PROJ/$SLUG.json          # full (with vision)
#   offline variant: add --no-vision

# 2. re-render the review cut for the changed scenes (or the whole cut)
./venv/bin/python3 pipeline/panel_render.py $PROJ/$SLUG.json \
    --res 1440p --workdir $PROJ/_work --stop-after review-cut

# 3. re-run review; compare irrelevant_panel HIGH count vs baseline (20+)
./venv/bin/python3 pipeline/review_video.py $PROJ/$SLUG.json \
    --video "$(cat $PROJ/_work/review_src.txt)" --workdir $PROJ/_work
grep -c irrelevant_panel $PROJ/review.json   # acceptance: <=10

# rollback at any point:
cp $PROJ/$SLUG.json.pre_verify $PROJ/$SLUG.json
```

Full-pipeline variant: `./venv/bin/python3 manga.py --project $PROJ --from script --verify-panels --yes`

## Smoke results (2026-09-06)

- dry-run on golden-chapter copy: per-scene plan for 25 scenes, no crash.
- real run (--no-vision): 25 scenes, 0 empty, `_verify` present (129 entries),
  backup written, second run no-op.
- synthetic checks: credits dropped (incl. hook rule), unquoted textonly
  dropped, QUOTED textonly kept, cut-with-alternative dropped, emptied scene
  substituted in-window.
- `import verify_panels` clean; `manga.py --help` unchanged behavior.
- NOTE: golden `output/` script is post-review (review rounds already swapped
  its worst panels), so 0 drops there is expected; the un-reviewed draft is the
  real target. Scene 0 flags ambiguous (0.125 < 0.15) → Tier 3 would check it.

## Deviations from research plan

- `quality=sparse` is NOT dropped in Tier 1 (task spec listed only cut/kind
  gates; research had it optional). Sparse panels still get Tier-2 scored.
- Tier-2 weights simplified from research's 0.6/0.3/0.1 split to a single
  overlap over the concatenated panel text + name boost — fewer knobs, same
  signal on the calibration data.
- Verdict cache `<slug>.verify.json` folded into the script's `_verify` key
  (task spec) instead of a sidecar file.
