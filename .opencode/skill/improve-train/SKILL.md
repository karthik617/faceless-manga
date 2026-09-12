---
name: improve-train
description: Run a self-improvement training round on the faceless-manga pipeline - mine review.md quality gaps, fan out web research for tools/techniques, prototype behind flags, benchmark against the golden chapter, and gate adoption on human approval. Use when the user says /improve-train, "training round", "improve the pipeline", or asks to fix recurring review faults systematically.
---

# improve-train: one training round

The self-improvement loop for the faceless-manga pipeline. State lives in
`improvements/ledger.json`; scoring in `pipeline/benchmark.py`; the golden
benchmark is pinned in `improvements/benchmark/config.json`.

## Roles

- **improve** (orchestrator, primary agent) — runs the round, owns the ledger,
  holds the human gate. If not already running as `improve`, treat this skill
  as the orchestrator's instructions.
- **gap-analyst** — refreshes gap cards from review outputs + code audit.
- **web-researcher** — one per gap, in parallel: candidates + licenses +
  build-ourselves fallback.
- **integrator** — prototypes behind an opt-in flag; default path untouched.
- **evaluator** — stage-scoped run + benchmark.py; verdict is binding.

## The round, step by step

1. **Load state.** Read `improvements/ledger.json` and
   `improvements/benchmark/config.json`. List gaps by status. Skip `adopted`
   and `rejected`. Ask the user which gaps to work (default: top 2-3
   `proposed`/`escalated` by priority) unless they already specified.
2. **Refresh (conditional).** If `output/*/review.md` files exist that
   post-date `ledger.json`'s `updated` field, run gap-analyst first.
3. **Research fanout.** One web-researcher task per selected gap, in a single
   parallel batch. Allocate experiment IDs `exp-NNN-<gap-slug>` (next NNN from
   ledger) and create `improvements/experiments/<exp-id>/` per gap. Update
   ledger status → `researching`.
4. **Prototype.** For each gap's recommended candidate: integrator task.
   Status → `prototyped`. If the researcher found nothing viable, go straight
   to the build-ourselves design (that IS the candidate).
5. **Evaluate.** Evaluator task per experiment (can run in parallel if they
   touch different stages; serialize if they share the golden project files).
   Status → `tested`. Stage scopes come from config `stage_scopes`. Reviews
   are ALWAYS `pipeline/review_stable.py` (majority-of-3; single-pass noise
   ±12 faults, gap-011); score globally AND with `--scenes-changed` when the
   touched scene set is known. NEVER run `--apply-fixes` during measurement.
   **Matched-veto scoring is mandatory (gap-015, adopted standard):** every
   arm is ALSO scored with `--matched-vetoes` (+ `--hash-video` /
   `--hash-baseline-video` when both mp4s exist, or `--hash-sidecar`;
   netting-only otherwise). Both arithmetics go in the report; the
   matched-veto result decides whether a type-count FAIL is reviewer noise
   or a real regression — a matched-veto FAIL is final. Binding archival
   rule: any eval that hashes videos must also `--write-hash-sidecar
   <exp-dir>/replay/sidecars/<pair>.hash_sidecar.json`.
6. **Human gate.** Present per gap: verdict, weighted score delta, per-fault-
   type table, sample frames, wall-time, caveats. Ask via `question`:
   adopt / reject / iterate.
   - **adopt** → evaluator does the FULL end-to-end run (`--video` +
     `--baseline-video` vetoes active). PASS → integrator merges (document
     the flag or make it default), adds deps to `pipeline/requirements.txt`,
     README table row if a new module was added. Ledger → `adopted` with
     experiment ref.
   - **reject** → ledger `rejected_candidates` += candidate + reason. If
     another researched candidate exists, loop to step 4 with it.
   - **iterate** → resume the same integrator task (task_id) with the
     evaluator's findings; re-evaluate.
7. **Escalation.** After 2 failed candidates on a gap: attempt the
   build-ourselves fallback. If that also fails its benchmark, mark
   `escalated`, write the post-mortem into the gap card, move on.
8. **Close the round.** Update ledger `updated` date. Summarize: gaps worked,
   verdicts, adoptions, ledger deltas, suggested next-round targets.

## Invariants (repeat offenders — check these every round)

- Quality-non-degradation is a hard veto; benchmark.py FAIL is final.
- Default pipeline behavior byte-identical until adoption.
- `~/faceless-youtube/pipeline/make_video.py` and review_video.py fault
  definitions are never modified.
- Golden project `output/the-world-after-the-fall-ch2/` is never mutated by
  experiments (copy or backup/restore).
- Licenses recorded for every adopted dep (MIT/Apache/BSD/CC0 preferred).
- Ledger updated before and after every state transition — rounds must be
  resumable after interruption.

## Improvement surface (do not tunnel-vision on review.md)

review faults are the richest signal, but the mandate covers the whole
pipeline: discover ranking, download, segmentation, OCR accuracy, script and
narration quality, panel layout/motion (e.g. multi-panel creative layouts
synced to narration beats), music/SFX fit and seams, thumbnails/CTR, shorts,
package metadata. gap-analyst owns finding gaps in uncovered stages.
