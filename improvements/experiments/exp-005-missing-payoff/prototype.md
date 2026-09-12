# exp-005 prototype: tier-1.5 quote-payoff pass in verify_panels.py

**Gap:** gap-005-missing-payoff · **Design:** research.md "build-ourselves" primary
(C3 stdlib difflib + C5 fixed extractor + C4 LLM-paraphrase-as-action). Zero new deps.

## What changed

One file: `pipeline/verify_panels.py` (+~290 lines, nothing removed).

- **Flag:** `--payoff` (or env `MANGA_VERIFY_PAYOFF=1`). **Default OFF** — with the
  flag off, no payoff code runs, no log entries are added, and output is
  byte-identical to the pre-change verifier (verified by md5 on the golden copy).
- **Constants** `PAYOFF_OK=0.75`, `PAYOFF_ABSENT=0.45`, `PAYOFF_MIN_QUOTE=4` —
  thresholds validated on the golden ch2 OCR cache (research.md §3/§5: true quotes
  score 0.60–1.00, unrelated pairs ≤0.29).
- **`payoff_quotes()`** — module-local copy of review_video.py's speech-verb-anchored
  extractor with the two C5 fixes: Unicode normalization (curly quotes `’‘“”„`→ASCII,
  `…`→`...`) applied BEFORE matching, and paired-delimiter branches (`'…'` or `"…"`,
  never mixed) with the letter-flanked internal-apostrophe rule kept verbatim.
  review_video.py is untouched.
- **`payoff_score()`** — two-pass difflib scorer: word-coverage
  (`SequenceMatcher.get_matching_blocks`, order-sensitive) with a char-level
  partial-ratio fallback (`_char_partial_ratio`, rapidfuzz `partial_ratio` analog)
  only when the word pass lands in the ambiguous [0.35, 0.75) band.
- **`Verifier.tier_payoff()`** — runs per scene AFTER tier-1, BEFORE tier-2 scoring:
  1. score every quote vs each kept panel's dialogue+narration_text, singles **and
     adjacent-pair concats** (quotes split across two bubbles: ch3 sc14);
  2. ≥0.75 → `payoff_ok` (single) / `payoff_concat` (pair);
  3. 0.45–0.75 → `payoff_fuzzy` (OCR-noisy maybe; best panel+score logged, **no
     action** — tuning evidence);
  4. <0.45 in-scene → chapter-wide search bounded by a reading-order window
     (`_payoff_window`: prev scene's min index − 1 .. next scene's min index —
     backward-inclusive because already-shown panels are reprises, not spoilers;
     forward-strict, same no-spoiler rule as `substitute()`). In-window hit ≥0.75 →
     **append** panel(s) to the scene (never replace) → `payoff_append`;
  5. absent chapter-wide (or only out-of-window) → `payoff_miss` logged, then one
     `gateway.llm_text` rewrite per scene using `PAYOFF_FIX_PROMPT` (mirrors
     review_video `NARRATION_FIX_PROMPT` style + its `len(new)>40` guard) →
     `payoff_paraphrase`; skipped under `--no-vision` (offline) → `payoff_unresolved`.
- **Tier-3 interaction:** payoff-appended refs are tracked in
  `Verifier.payoff_appended`; a vision "irrelevant" verdict cannot drop them
  (a panel whose text the narration literally quotes is relevant by definition).
- **Log/params:** all actions go into the existing `_verify.verify_log`; the
  `params` dict gains a `payoff` sub-key **only when the flag is on**.

New log actions: `payoff_ok`, `payoff_concat`, `payoff_append`, `payoff_fuzzy`,
`payoff_miss`, `payoff_paraphrase`, `payoff_unresolved`.

## Fallback behavior

- Whole tier-1.5 body wrapped in try/except inside `run()`: any error logs a
  `warn` and the scene falls back to the tier-1 kept list — never crashes.
- Paraphrase failure (gateway error / short output) → `payoff_unresolved`,
  narration unchanged; review_video.py still catches it post-render. Fail open.
- Missing OCR reads pass through as before (existing `_read_for` fail-open).

## Smoke results (golden ch2, `--payoff --no-vision`)

The 4 ledger quotes (scenes 5/13/17/21) resolve exactly as research predicted:

| scene | quote | action | score |
|---|---|---|---|
| 5 | "If we continue like this…" | payoff_ok (p0021) | 1.00 |
| 13 | "Yoonhwan!!!" | payoff_ok (p0069) | 1.00 |
| 17 | "Let's go, Yoonhwan!" | payoff_ok (p0101) | 1.00 |
| 21 | "Jaehwan, you punk… doesn't it?" | **payoff_append p0122+p0123** (window-legal) | 1.00 |

Chapter totals ch2: 9 payoff_ok, 4 payoff_concat, 1 payoff_append, 0 fuzzy/miss —
**zero LLM calls**, projected missing_payoff 4 → 0. No scene lost panels; JSON valid.
ch3 spot check: 5 payoff_ok, 3 payoff_concat (incl. sc14 split-quote via
p0047+p0048 pair concat, the ch3 review fault), no appends/misses.
Regression: run without `--payoff` on a fresh copy → md5-identical to the
pre-change verifier output (`b05bef4a…`).

Samples: `samples/ch2_payoff_dryrun.log`, `samples/ch3_payoff_dryrun.log`,
`samples/ch2_payoff_log.json`; verified copy at `run_script.json`
(pristine backup `run_script.json.pre_verify`).

## Dep installs performed

None (stdlib difflib only, per research recommendation). rapidfuzz upgrade path
stays documented in research.md if round-eval finds OCR-noise misses.

## How the evaluator runs the stage-scoped experiment

```bash
# dry-run plan on any drafted script + its .ocr.json (name-matched next to it)
./venv/bin/python3 pipeline/verify_panels.py output/<slug>/<slug>.json --payoff --dry-run

# deterministic offline (no gateway; absent quotes log payoff_unresolved)
./venv/bin/python3 pipeline/verify_panels.py output/<slug>/<slug>.json --payoff --no-vision

# regression guard (must be byte-identical to a pre-exp-005 run)
./venv/bin/python3 pipeline/verify_panels.py <fresh-copy>.json --no-vision

# via env (for manga.py runs without arg plumbing)
MANGA_VERIFY_PAYOFF=1 ./venv/bin/python3 pipeline/verify_panels.py <script>.json
```

Evaluate by diffing `_verify.verify_log` payoff actions against review.md
missing_payoff faults on a full render of the golden chapter.
