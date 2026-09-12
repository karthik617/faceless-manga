# exp-005 research: quote→panel payoff verification at script time

**Gap:** `gap-005-missing-payoff` — narration quotes dialogue ("he screams: 'X'") but the
scene's panels don't contain X. 4 HIGH `missing_payoff` on golden chapter
(`the-world-after-the-fall-ch2`, scenes 5/13/17/21), 1 on ch3 (scene 14). Detected only
post-render by `review_video.py::narration_quotes` (line 136, regex at 130-133).
Fix lands at script time as an extension of the adopted 3-tier verifier
(`pipeline/verify_panels.py`), NOT in review_video.py's fault definitions.

**Environment facts (verified locally, 2026-09-07):**
- Python 3.12 venv; no rapidfuzz/thefuzz installed. `difflib` already load-bearing in
  `verify_panels.py::_has_verbatim_quote` (line 115-128, word-granularity SequenceMatcher).
- OCR cache `<slug>.ocr.json` gives per-panel `dialogue[].text` + `narration_text` in
  reading order — the matching corpus is already free.
- Gateway `llm_text` exists; `_fix_narration` pattern (review_video.py:353) is the
  paraphrase precedent, `NARRATION_FIX_PROMPT` at line 342.

---

## Ground truth replayed against the golden-chapter cache (done locally, read-only)

Crucial: **every one of the 4 "missing" quotes exists verbatim in the chapter's OCR.**
The faults are panel-selection misses (and, for 2 of 4, review-side extraction artifacts),
not hallucinated dialogue:

| review fault (quote as extracted) | full quote in script | OCR panel(s) carrying it | scene's panels | diagnosis |
|---|---|---|---|---|
| `"re all thinking:"` (sc 5) | `'If we continue like this, we might…'` | **p0021** (exact) | p0021,p0022,p0024 | panel WAS in scene — old truncated extraction made the reviewer chase the wrong string `re all thinking:` (narration meta-text, never on any panel) |
| `"Yoonhwan!!!"` (sc 13) | same | **p0069** (exact) | p0065–p0069 | panel in scene; render/sampling-side miss, not a script gap |
| `"Let"` (sc 17) | `'Let's go, Yoonhwan!'` | **p0101** (exact) | p0097–p0101 | same truncation artifact (`Let` alone matches nothing) |
| `"Jaehwan, you punk… doesn"` (sc 21) | full 2-sentence quote | **p0122 + p0123** (split across two panels) | p0124–p0126 | genuine miss: payoff panels are the two BEFORE the scene's range — swap-in fixes it |
| ch3 sc 14 `"As for the blacksmiths…last one."` | same | **p0047 + p0048** (split) | p0047–p0051 | panels present; quote spans two panels so any single-panel exact-match test fails |

Two consequences:
1. **Quote-extraction robustness is half the gap.** The truncation bug ("Let",
   "re all thinking:") is ALREADY fixed in review_video.py's current `_QUOTE_RE`
   (verified: all 4 golden quotes now extract in full, internal apostrophes handled by the
   `(?<=[a-zA-Z])'(?=[a-zA-Z])` letter-flanked rule). The new script-time checker must be
   at least as good AND must not regress on the review side. Verified remaining hole:
   **curly quotes** (`“ ” ‘ ’`) return `[]` from the current regex — narrations produced
   by the LLM can contain them.
2. **Matching must support quotes spanning 2 adjacent panels** (2 of 5 faults). Scoring
   against the concatenation of reading-order-adjacent panel dialogue solves this
   (verified: coverage jumps 0.60 → 1.00 on the sc-21 quote when p0122+p0123 are joined).

### Stdlib scorer benchmark (replayed on the 154-panel ch2 OCR, 4 quotes)

Scorer = word-level `difflib.SequenceMatcher.get_matching_blocks` coverage:
`covered_words / quote_words` after lowercase + punctuation-strip normalization.

| quote | top-1 panel (score) | truth | verdict |
|---|---|---|---|
| "If we continue like this…" | p0021 (1.00) | p0021 | hit; next-best 0.64 |
| "Yoonhwan!!!" | 3-way tie 1.00 (p0069, p0109, p0151, p0153…) | p0069 | hit only with reading-order window — single-word quotes need the window constraint |
| "Let's go, Yoonhwan!" | p0101 (1.00) | p0101 | hit; next 0.75 |
| "Jaehwan, you punk… doesn't it?" | p0123 (0.60), pair-concat (1.00) | p0122+p0123 | hit with adjacent-pair concat |

Noise floor: unrelated quote↔panel pairs score ≤ 0.07–0.29. Whole scan: **4 ms**.
Separation is wide enough that no C-extension fuzzy lib is *needed* for accuracy; the
real question is OCR-noise tolerance (below).

---

## Candidate matrix

| # | candidate | license | install cost | py3.12 | maint. | fit (1-5) | risk |
|---|---|---|---|---|---|---|---|
| C1 | rapidfuzz 3.14.6 | MIT | ~3.2 MB manylinux wheel, C++ ext, zero deps | yes (cp312 wheel) | active — released 2026-08-30 | 4 | binary dep for marginal gain; new-dep policy friction (verify_panels deliberately avoided it) |
| C2 | thefuzz 0.22.1 | MIT | 8 KB wheel + pulls rapidfuzz | yes | low-activity wrapper (last release Jan 2024) | 2 | strictly worse than C1 (same engine, older API, int scores) |
| C3 | stdlib difflib (extend verify_panels) | PSF (stdlib) | **zero** | n/a | n/a | 5 | word-level matching drops ~15 pts under char-level OCR noise (see below); mitigated by char-level fallback pass |
| C4 | LLM-judge quote presence via gateway `llm_text` | n/a (existing) | zero | n/a | n/a | 2 as primary / 4 as escalation | non-deterministic, ~2-5 s/call; use only for the "rewrite narration" action, not for matching |
| C5 | regex-only quote extraction w/ curly-quote normalization (build) | n/a | zero | n/a | n/a | 5 | tiny; the one known failure mode (nested quotes) is absent from this corpus |

### C1. rapidfuzz — details
- URL: https://github.com/rapidfuzz/RapidFuzz · https://pypi.org/project/RapidFuzz/ (3.14.6)
- License MIT (explicitly chosen over fuzzywuzzy's GPL). Requires Python ≥3.11; cp312
  manylinux_2_28 x86_64 wheel 3.2 MB. Zero runtime deps. Actively maintained
  (release 2026-08-30).
- **`partial_ratio` vs `token_set_ratio` for "quote inside longer dialogue":**
  - `partial_ratio(quote, panel_text)` slides a quote-length window over the longer
    string at **character** level → exactly the "needle in haystack" shape of this gap,
    and char-level alignment absorbs OCR character noise (`C0NTINUE`/`THLS`) that
    word-level matching misses. This is the right primitive here.
  - `token_set_ratio` sorts/dedups tokens then compares set differences → returns 100
    whenever the quote's *words* are a subset of the panel text **in any order**. That
    is too permissive for payoff checking ("go Yoonhwan let's" would pass) and its
    word-tokenization is as OCR-noise-brittle as difflib's. Use `partial_ratio`
    (optionally `partial_token_sort_ratio` as tiebreak); do NOT use `token_set_ratio`
    as the gate.
- Smoke test:
  ```
  ./venv/bin/pip install rapidfuzz==3.14.6
  ./venv/bin/python3 -c "from rapidfuzz import fuzz, utils; \
    print(fuzz.partial_ratio(\"let's go, yoonhwan\", 'LET\\'S GO, YOONHWAN!', processor=utils.default_process))"
  # expect 100.0
  ```
- Integration: drop-in replacement for the scorer function inside the verify_panels
  extension (below); everything else identical.

### C2. thefuzz — rejected
MIT, but it is a maintenance-mode wrapper that itself depends on rapidfuzz
(`requires_dist: rapidfuzz>=3.0.0`) and returns coarser int scores. If installing
anything, install rapidfuzz directly.

### C3. stdlib difflib (primary building block — see build-ourselves)
Measured OCR-noise behaviour (local replay):
- clean/case-noise quotes: word-coverage = 1.00 (normalization handles case/punct).
- synthetic char-level OCR noise (`C0NTINUE`, `THLS`): word-coverage drops to 0.60
  (2 of 5 words no longer token-equal) while a difflib **char-level sliding-window
  ratio** (a ~10-line partial_ratio analog) still scores 0.76 vs a 0.28 unrelated-pair
  floor. → a two-pass scorer (word coverage first, char-partial fallback) keeps stdlib
  accuracy within a few points of rapidfuzz on this corpus. Cost: char pass is
  O(len·window) pure Python ≈ 1-2 ms/pair, and it only runs when the word pass is in
  the ambiguous band — tens of pairs per chapter, < 0.5 s total.

### C4. LLM-judge / rewrite via gateway
Not for matching (slow, non-deterministic, matching is trivially lexical here). Its
role: **action (b)** — when the quote is genuinely absent from the whole chapter's OCR,
rewrite the narration to paraphrase, reusing the `NARRATION_FIX_PROMPT` /
`_fix_narration` pattern (review_video.py:342-383) with "removed content" replaced by
"this quoted line is not visible on any panel — paraphrase it as indirect speech".
One `llm_text` call per affected scene; golden chapter would need **zero** such calls
(all quotes exist in OCR), so expected steady-state cost ≈ 0-2 calls/chapter.

### C5. Quote extraction (build; the review-side regex is the starting point, not the target)
The current review_video.py `_QUOTE_RE` already fixed the apostrophe truncation
(verified against all 4 golden quotes). The new module gets its **own copy** so
review_video.py fault definitions stay untouched, with two upgrades:
1. **Unicode normalization before matching:** map `’‘` → `'`, `“”„` → `"`, collapse
   `…` → `...`. This fixes the verified curly-quote miss
   (`She said “don’t go”` currently extracts nothing).
2. **Paired-delimiter matching:** require the closing delimiter to be the same class
   as the opener (`'…'` or `"…"`, not mixed), i.e. two alternative branches instead of
   the shared `['\"]` class — prevents `"…'` cross-matches the shared class allows.
   Keep the letter-flanked internal-apostrophe rule verbatim (it is what fixed
   "don't"/"Let's").
How others do it (survey): NLP quote-attribution systems (e.g. BookNLP, GutenTag) use
exactly this two-step — Unicode quote normalization then paired-delimiter regex — and
only escalate to a stack-based parser for *nested* quotes. Nested quotes do not occur
in this pipeline's narrations (single-level speech-verb quotes by construction of
RECAP_PROMPT rule 3), so a regex is sufficient; a parser is over-engineering here.

---

## "Build ourselves" design (recommended): tier 1.5 in `verify_panels.py`

New pass `tier_payoff(scene_i, scene, kept, scenes_kept)` between tier1 and tier2
(pure stdlib, no new imports beyond what the file has):

1. **Extract quotes** from `scene["narration"]` with the C5 regex (module-local copy +
   Unicode normalization; review_video.py untouched). Skip quotes < 4 chars after
   normalization (nothing matchable).
2. **Score the quote against each panel already in the scene** using
   `payoff_score(quote, dialogue_text(read))`:
   - normalize both sides (`_norm` + strip non-alnum, keep letter-flanked apostrophes);
   - **word pass:** `sum(block.size for block in SequenceMatcher(None, q_words,
     p_words, autojunk=False).get_matching_blocks()) / len(q_words)`;
   - **char pass (only if word pass in [0.35, 0.75)):** sliding-window char-level
     SequenceMatcher ratio (partial_ratio analog, window = len(quote)+10, step
     len(quote)//4) — absorbs OCR character noise;
   - **adjacent-pair concat:** also score against `dialogue(p_i) + " " + dialogue(p_{i+1})`
     for reading-order-consecutive panels in the scene (covers 2-panel quotes; verified
     necessary for ch2 sc21 and ch3 sc14).
3. **Thresholds** (tuned on the golden cache — clear hits ≥ 0.75 incl. the pair-concat
   cases; unrelated pairs ≤ 0.29):
   - `PAYOFF_OK = 0.75` → quote is on-screen; done.
   - `0.45 ≤ score < 0.75` → "**present but OCR-noisy**": keep the panel, log
     `payoff-noisy` (no action; this band is where translated/stylized text lands).
   - `score < 0.45` for every panel in the scene → "**genuinely absent from scene**"
     → step 4.
4. **Rescue, ranked (a) then (b):**
   a. **Swap-in:** scan ALL chapter reads for the best `payoff_score` (single +
      adjacent-pair), restricted to the scene's reading-order window via the existing
      `_scene_window` (verify_panels.py:322) — same no-spoiler bound as `substitute()`.
      If best windowed score ≥ `PAYOFF_OK`: **append** the carrying panel(s) to the
      scene (append, don't replace — the existing panels passed tier-2 relevance, so
      appending cannot create a new `irrelevant_panel`; the quote panel itself is
      relevance-safe by construction since the narration literally quotes it,
      satisfying the tier-1 textonly rule `_has_verbatim_quote` as well). Log
      `payoff-swap` with score + indices.
      *Out-of-window hit* (quote exists but only outside the window, like a
      flash-forward): do NOT swap (spoiler guard); fall through to (b).
   b. **Paraphrase:** best chapter-wide score < 0.45 (quote exists nowhere) or only
      out-of-window → one gateway `llm_text` call with a `PAYOFF_FIX_PROMPT` modeled on
      `NARRATION_FIX_PROMPT` (review_video.py:342): "rewrite minimally, converting this
      quoted line to indirect speech, everything else word-for-word". Log
      `payoff-rewrite`. On LLM failure: leave narration, log `payoff-unresolved`
      (review_video.py still catches it post-render — fail open, no regression).
5. **Threshold rationale ("OCR-noisy" vs "genuinely absent"):** the two distributions
   on this corpus don't overlap — verbatim-in-OCR quotes score 0.60-1.00 even with the
   split-panel and synthetic-noise handicaps; unrelated pairs top out at 0.29. The
   0.45 cut sits in the empty middle with ≥ 0.15 margin each side. Log every score so
   round-N+1 evaluation can re-tune from data.
6. **Wall time:** ≤ 10 ms/chapter deterministic + 0-2 optional llm_text calls. Runs
   under the existing `--dry-run` / `_verify` log / `.pre_verify` backup machinery
   unchanged; add `--no-payoff` flag mirroring `--no-vision` for offline runs.

Golden-chapter projection: sc5/sc13/sc17 pass step 3 outright (quote panels already in
scene, scores 1.00) — the old review faults on these were extraction artifacts that the
C5 extractor no longer produces; sc21 rescued by (a) swap-in of p0122+p0123 (window-legal:
they sit between scene 20's and scene 21's panel indices); ch3 sc14 passes via
adjacent-pair concat. **missing_payoff 4 → 0 with zero LLM calls.**

## Recommendation

**Primary: build-ourselves stdlib tier-1.5 in verify_panels.py (C3+C5+C4-as-action).**
Zero installs, consistent with the file's existing difflib/no-rapidfuzz stance, ~120
lines, deterministic and offline-tunable against the cached golden chapter, and the
replay shows it clears all 5 observed faults — 4 without any gateway call.

**Runner-up: same design with rapidfuzz 3.14.6 (MIT, 3.2 MB wheel, py3.12) replacing
the two-pass scorer** with `fuzz.partial_ratio(..., processor=utils.default_process)`
(threshold ≈ 80/100). Adopt only if round-eval finds real chapters where char-level OCR
noise pushes true quotes below 0.45 on the stdlib scorer — then it's a 5-line swap
(`pip install rapidfuzz==3.14.6`; scorer function body only). Do NOT use
`token_set_ratio` for the gate (order-insensitive subset match = false payoffs);
`partial_ratio` is the correct primitive for "quote inside longer dialogue".

**Rejected:** thefuzz (wrapper over rapidfuzz, stale), LLM-as-matcher (slow,
non-deterministic, unnecessary — matching is lexical here).

### Smoke tests (top 2)

Stdlib (no install):
```
./venv/bin/python3 - <<'EOF'
import difflib, re
q, p = "let's go, yoonhwan", "LET'S GO, YOONHWAN!"
n = lambda t: re.sub(r"[^a-z0-9' ]", " ", t.lower()).split()
sm = difflib.SequenceMatcher(None, n(q), n(p), autojunk=False)
print(sum(b.size for b in sm.get_matching_blocks()) / len(n(q)))  # expect 1.0
EOF
```
rapidfuzz:
```
./venv/bin/pip install rapidfuzz==3.14.6
./venv/bin/python3 -c "from rapidfuzz import fuzz, utils; \
  print(fuzz.partial_ratio('lets go yoonhwan', 'LET\\'S GO, YOONHWAN!', processor=utils.default_process))"
# expect >= 90
```

## Open risks → quality guards

1. **Swap-in creating irrelevant_panel** (gap acceptance guard): mitigated by
   *appending* rather than replacing, reading-order window reuse, and the fact that a
   quote-carrying panel is definitionally relevant to a narration that quotes it. Every
   swap logged under `_verify` for the round evaluator to diff.
2. **OCR-noise threshold drift on other series** (stylized/translated fonts): the 0.45
   band is tuned on one series' cache. Guard: log all scores; the char-pass fallback
   narrows the miss window; rapidfuzz upgrade path is pre-specified.
3. **Paraphrase rewrite degrading narration voice** (wall-time/quality): reuses the
   proven minimal-rewrite prompt; ≤ 2 calls/chapter; length guard (`len(new) > 40`)
   copied from `_fix_narration`; failure = no-op (review still catches it).
4. **Curly-quote normalization changing extraction counts** vs review_video.py's
   extractor: intentional and one-directional (script-time extractor is a strict
   superset); review_video.py fault definitions untouched per gap constraint. Residual:
   a quote the new extractor fixes at script time can no longer be A/B'd against old
   review runs — the `_verify` log preserves the audit trail.
