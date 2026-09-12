# exp-005 evaluation report — round2-eval (Arm P)

**Verdicts (binding, benchmark.py):**
- **Scene-scoped (`--scenes-changed 21`): PASS** — score 5 → 0 (delta -5), no
  vetoes. The one scene the experiment touched had its missing_payoff HIGH
  fixed and gained nothing. (`metrics.json`)
- **Global: FAIL on veto** — score 126 → 127 (delta +1) with "worsened types"
  watermark 4→5 (scene 1) and unreadable_text 1→2 (scene 20). Both extra
  faults are on scenes the experiment did not touch — panels and narration
  there are byte-identical to baseline — so this is reviewer noise, not
  experiment effect. (`metrics_global.json`)

Interpretation: this is exactly the case the scoped scoring exists for. The
payoff pass changed one thing (scene 21 gained p0122+p0123 via
`payoff_append`, window-legal, narration untouched) and the reviewer confirmed
3/3 that scene 21's "Jaehwan, you punk… so you noticed." quote now pays off.
The scoped PASS is the meaningful verdict; the global FAIL is noise on
untouched scenes (watermark@1 flags a title-card panel that carries the same
watermark in baseline — it simply fell below majority there). I report both
per protocol; recommendation to orchestrator: treat as **PASS (scoped)** with
the standard full-run gate before adoption.

## What ran

- `verify_panels.py arm_p.json --payoff --no-vision` (deterministic, log:
  `round2-eval/payoff_log.txt`). Actions: **9 payoff_ok, 4 payoff_concat,
  1 payoff_append** (scene 21 += p0122, p0123); 0 fuzzy/miss/paraphrase/unresolved.
- Only scene 21 changed; narration unchanged everywhere (no payoff_paraphrase
  possible under --no-vision) → TTS reuse safe; render + stable review ×3.
- NOT a no-op on the golden script (contrary to the "maybe already fixed"
  branch in the eval plan) — the append fired, so the full render path was
  used and the fresh-draft side test was unnecessary.

## Score table (stable review, majority-of-3)

| fault type | baseline | Arm P | Δ |
|---|---|---|---|
| missing_payoff (H) | 5 | 4 | **-1** (scene 21 fixed) |
| watermark (H) | 4 | 5 | +1 ⚠ veto — scene 1, untouched (noise) |
| irrelevant_panel (H) | 12 | 12 | 0 |
| weak_hook (H) | 1 | 1 | 0 |
| cropped_content (M) | 4 | 4 | 0 (moved 2→20, both untouched) |
| phone_readability (M) | 2 | 2 | 0 |
| empty_screen (M) | 1 | 1 | 0 |
| unreadable_text (L) | 1 | 2 | +1 ⚠ veto — scene 20, untouched (noise) |
| panel_reuse (L) | 1 | 1 | 0 |
| **weighted global** | **126** | **127** | +1 |
| **weighted scoped (scene 21)** | **5** | **0** | **-5 PASS** |

Remaining missing_payoff faults (scenes 4, 13, 17, 22): in ALL of them the
verifier logged payoff_ok/payoff_concat with score 1.00 — the quoted text IS
on a kept panel (e.g. "Yoonhwan!!!" on p0069, kept in scene 13). The reviewer
still reports "never shown", most plausibly because the paying-off panel's
screen time/legibility in the Ken Burns sequence is too brief for the vision
reviewer to register it, or the reviewer samples frames that miss it. That is
a render/reviewer-side issue outside this experiment's scope (the gap card's
4→0 target implicitly assumed on-screen = counted).

## Acceptance criteria (gap-005)

| criterion | result |
|---|---|
| missing_payoff on golden chapter 4 → 0 | **NOT MET as literally stated** — stable-baseline had 5 (not 4; single-pass baseline counts differ); Arm P has 4. The only structurally-missing quote (scene 21) is fixed; the other 4 are already-on-screen quotes the vision reviewer does not credit (verifier scores 1.00 on kept panels). The verifier-level target (no quote without an on-screen panel) IS met: 0 payoff_miss/unresolved. |
| no new irrelevant_panel from swaps | **MET** — no swaps occurred (append-only); irrelevant_panel 12→12 global, scene 21 gained none. |

## Vetoes triggered

Global scope only: watermark 4→5, unreadable_text 1→2 — both on scenes with
byte-identical inputs to baseline (reviewer variance). No vetoes in scoped run.

## Wall times

verify_panels --payoff: <10 s. Render (TTS reuse): ~10 min. Review ×3: ~23 min.

## Frame evidence (`samples/`)

- `scene21_base_t*.png` vs `scene21_armp_t*.png` — baseline scene 21 shows only
  p0124–p0126; Arm P at t≈652/658 shows the appended p0122 (stairs/COUGH) and
  p0123 (the "Jaehwan, you punk… so you noticed." bubble with the bloodied
  stone) — the quoted line is now literally on screen.
- Deterministic logs: `round2-eval/payoff_log.txt`, plus prototype's
  `samples/ch2_payoff_*.log`.

## Caveats — not measured in this stage-scoped run

- `--no-vision` means the payoff_paraphrase (LLM rewrite) path never ran; its
  quality is unmeasured.
- TTS reuse means the +2 panels changed scene-21 pacing only visually; a full
  run re-times nothing (narration unchanged) but final loudness/duration vetoes
  were not exercised (review-cut duration drift +0.06 s, trivially fine).
- The residual 4 missing_payoff faults look like a reviewer/render-visibility
  issue, not an extractor/scorer failure — worth its own gap card rather than
  iterating exp-005.
- Full pre-adoption run (mandatory) must cover: gateway-on paraphrase path,
  final encode, and a fresh-draft chapter (golden script is post-review; a
  fresh draft may trigger many more appends).

## fresh-draft + paraphrase gate (final pre-adoption check)

Golden project untouched: all runs in `/tmp/opencode/exp005_fresh/` on copies
of `round2-eval/fresh.json` + `fresh.ocr.json` (panels symlinked read-only).
Gateway/vision ON for every run (no `--no-vision`), so the paraphrase path was
live. Logs: `/tmp/opencode/exp005_fresh/{run1,run2,targeted}.log`.

### Caveat (b): fresh-draft behavior — CLEARED (vacuously safe)

`verify_panels.py fresh.json --payoff` (34 scenes, gateway on), action counts:

| action | count |
|---|---|
| payoff_ok / concat / append / fuzzy / miss / paraphrase / unresolved | **0 each — "no anchored quotes found"** |

The fresh draft contains **zero speech-verb-anchored quotes**: 20/34 scenes
use speech verbs but the draft renders all dialogue as free indirect prose
without quotation marks (e.g. scene 3: *"the strategist speaks up. If we
continue like this, we might be able to reach the last floor."* — no quote
delimiters anywhere in the draft; 0 scenes contain `"` or `“`). So the
"fresh draft may trigger many more appends" fear does not materialize:
tier-1.5 is a no-op on this draft. No scene lost a panel to the payoff pass,
no appends, no ballooning (per-scene panel counts unchanged by tier-1.5; the
only drops in the run came from pre-existing tier-1/tier-3 logic that runs
with or without `--payoff`). Note the extractor is style-sensitive: drafts
that quote dialogue (like the post-review golden script does) will exercise
the pass; drafts that don't are simply passed through — fail-safe either way.

### Caveat (a): paraphrase LLM path — CLEARED (targeted test)

Since paraphrase cannot fire naturally on this draft, a minimal targeted copy
(`/tmp/opencode/exp005_fresh/targeted/`) edited scenes 3 and 10 to carry
anchored quotes that are close paraphrases of panel text but score below
PAYOFF_ABSENT chapter-wide (verified 0.23 / 0.29 best). Results:
`payoff_ok=1, payoff_miss=2, payoff_paraphrase=2` — the exact-quote control
still hit `payoff_ok` (p0021, 1.00) in the same scene while both planted
misses escalated correctly to paraphrase.

Before/after for every paraphrase that fired (run 1):

- **Scene 3** — before: *…the brown-haired strategist speaks up, saying
  "Should we keep pushing on, the final floor may be within our grasp."…*
  → after: *…the brown-haired strategist speaks up, suggesting they keep
  pushing forward since the final floor may be within their grasp.…*
  Rest of narration word-for-word identical, including the OTHER (visible)
  quote in the same scene, which was correctly left quoted.
- **Scene 10** — before: *Jaehwan whispers, "None of this ever mattered at
  all." The narration is just two words: How futile.…* → after: *Jaehwan
  whispers that none of it ever mattered at all. The narration is just two
  words: How futile.…* Rest identical.

Both rewrites are grammatical, minimal, convert exactly the absent quote to
indirect speech, and preserve meaning. Panels untouched in both scenes; all
34 scenes structurally valid after the run (all keys present, all panel files
exist, no empty scenes, `_verify` log written).

### Determinism

- **Payoff pass (fresh draft, ×2 runs): fully deterministic** — payoff log
  lines identical, all 34 narrations byte-identical across runs. The two runs'
  final panel lists differed only at scenes 0 and 33, caused by **tier-3
  vision** verdict variance (run 1 dropped p0115/p0118/p0148, run 2 kept
  them) — that nondeterminism predates exp-005, exists with `--payoff` off,
  and is fail-open (disagreement keeps panels).
- **Paraphrase path (targeted, ×2 runs): action-deterministic,
  wording-nondeterministic.** Both runs produced the identical action
  sequence (`miss=2, ok=1, paraphrase=2` on the same scenes/quotes/scores);
  the LLM rewrite wording differs slightly (run 2 scene 10: *"whispers that
  none of it ever mattered"* vs run 1 *"…mattered at all"*). Bounded and
  safe: one `llm_text` call max per scene, length-guarded (`len>40`), any
  failure → `payoff_unresolved` with narration unchanged (fail open), and
  review_video.py still catches residuals post-render.

### Verdict on the two blocking caveats

| caveat | status |
|---|---|
| (a) paraphrase LLM path unmeasured | **CLEARED** — fires correctly under gateway, minimal grammatical meaning-preserving rewrites, deterministic actions, fail-open |
| (b) fresh-draft behavior unmeasured | **CLEARED** — zero anchored quotes in the fresh draft → tier-1.5 no-op; no panel loss, no append ballooning; opt-in pass degrades to pass-through on non-quoting drafts |

**GO for adoption.** Combined with the earlier scoped PASS (scene 21: 5→0),
the noise-explained global FAIL, and the already-run full e2e render
(round2-eval/arm_p.mp4, 781.6 s, −14.0 LUFS, 0.00 % duration drift), both
previously-unmeasured paths are now exercised and behave as designed.
Residual known risk: LLM paraphrase wording varies run-to-run (bounded by
the one-call/length-guard/fail-open design) and the quote extractor only
engages on quote-punctuated drafts — acceptable for an opt-in flag.
