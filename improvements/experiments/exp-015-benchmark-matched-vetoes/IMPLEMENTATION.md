# exp-015 — benchmark matched-veto arithmetic (gap-015)

## What changed

**One file modified: `pipeline/benchmark.py`** (+270/-14). Everything is
behind the opt-in flag `--matched-vetoes`; with the flag off, output is
byte-identical (verified below). `pipeline/review_video.py` was NOT touched
by this experiment (see "Pre-existing tree state" at the bottom).

New CLI flags:

| flag | purpose |
|---|---|
| `--matched-vetoes` | opt in to the gap-015 veto arithmetic (replaces vetoes 1–2 only) |
| `--hash-video PATH` | experiment video for frame hashing ONLY (does not enable media vetoes the way `--video` does; defaults to `--video` when that is set) |
| `--hash-baseline-video PATH` | baseline video for frame hashing (defaults to `--baseline-video`) |
| `--hash-window S` | ± seconds around a fault timestamp to search baseline for a pixel-identical frame (default 3.0 — absorbs concat/Ken Burns sub-second phase offsets seen in exp-009 v3/v4) |

Untouched by design: severity weights, weighted-score arithmetic, the
score-improvement PASS condition, `--scenes-changed` scoping, media vetoes
(duration/loudness/encode), `review_error` exemption (carried over), and
`review_video.py` / `review_stable.py`.

## How the matched-pair arithmetic works

With `--matched-vetoes`, vetoes 1 (new fault types) and 2 (per-type count
worsened) are computed in three stages instead of on raw `type_counts`:

1. **Fixed-consensus normalization** (gap card direction 3). Each arm's pass
   count N is read from the `votes: "k/N"` field review_stable.py writes
   (missing/unparseable votes → 1/1, i.e. single-pass reviews still work).
   Both arms are re-thresholded at the **stricter** of the two native
   majority fractions `ceil(N/2)/N`. Symmetric arms are unchanged (3v3 →
   2/3; 5v5 → 3/5). A 3-pass vs 5-pass comparison renormalizes the 5-pass
   side to ≥4/5 (≥2/3 fraction) instead of refusing outright — the exp-004
   addendum showed this asymmetry alone (60% vs 67%) flips verdicts. Dropped
   faults are listed in `matched_vetoes.consensus_dropped` (never silent).

2. **Matched (scene,type) pair comparison** (direction 1). Faults are keyed
   `(scene, type)` — the same identity key review_stable.py majority-votes
   on. For each type with a raw count increase, the *added* pairs (in
   experiment, not in baseline) and *removed* pairs (in baseline, not in
   experiment) are enumerated. A fault that merely moved scenes nets out:
   `net = cur_count - base_count - exemptions`, and since
   `cur - base = added - removed` per type, removed pairs absorb scene
   churn automatically.

3. **Frame-hash exemption** (direction 2). When both hash videos are
   available, each *added* pair's evidence frame is decoded at the fault's
   `t` (`ffmpeg -ss t -frames:v 1 -f framemd5`, video stream only —
   **decoded-frame md5, never file md5**, per the quality guard about
   container-level byte diffs on identical pixels). If that md5 appears
   anywhere in the baseline video within `t ± hash-window`, the pair is
   pixel-identical to baseline and is exempted from the count. The window
   search handles the sub-second concat/Ken Burns phase offsets that made
   exact-timestamp comparison fail in exp-009 v3 evidence.

A type vetoes only if `net > 0` after all three stages. A genuinely new
fault on changed pixels always survives (hard-gate quality guard): hashing
can only exempt frames that literally exist in the baseline stream. The
new-fault-type veto (veto 1) fires through the same path — a type absent
from baseline vetoes only if at least one of its added pairs survives
netting + exemption.

**Never silent:** every type whose raw count increased gets a record in
`metrics.json → matched_vetoes.annotated_vetoes`, whether suppressed or
not: per-pair scene/t/votes/severity, `frame_hash_exempt` boolean,
`frame_hash_proof` (frame md5 + baseline search window), removed pairs, the
net after exemptions, and a `suppressed` + `reason` field. The
`matched_vetoes` block also records pass counts, the consensus fraction,
consensus-dropped items, and the hashing configuration.

**Graceful fallback:** missing/invalid hash videos just disable the
exemption (netting still applies, `frame_hash.enabled: false`); any
unexpected exception inside the matched path logs to stderr and falls back
to the DEFAULT (stricter) veto arithmetic — the scorer can never crash or
silently under-gate.

## How to run (evaluator command)

```bash
# exp-009 v4 style A/B with frame hashing:
./venv/bin/python3 pipeline/benchmark.py \
    --experiment improvements/experiments/<exp-id> \
    --baseline improvements/experiments/round2-eval/stable_base.json \
    --review   improvements/experiments/round2-eval/stable_t4.json \
    --matched-vetoes \
    --hash-video          improvements/experiments/round2-eval/_work_t4/ambed.mp4 \
    --hash-baseline-video improvements/experiments/round2-eval/_work_base/ambed.mp4

# full acceptance replay (all five gap-card criteria, deterministic):
./venv/bin/python3 improvements/experiments/exp-015-benchmark-matched-vetoes/replay_acceptance.py
```

Runtime: framemd5 hashing adds ~1–10 s per compared arm pair (window
extraction is cached per (video, window)); the full 5-criterion replay is
~21 s wall.

## Replay acceptance results — 15/15 checks pass

`replay_acceptance.py` output (metrics per criterion under `replay/<tag>/`,
machine summary in `replay/summary.json`):

| criterion (gap card) | archived pair | result |
|---|---|---|
| C1 exp-009 v4 global: cropped_content 4→5 veto no longer fires; verdict score-driven | round2-eval `stable_base.json` vs `stable_t4.json` + `_work_base/ambed.mp4` vs `_work_t4/ambed.mp4` | **PASS** — verdict PASS, delta −12, zero vetoes. Scene-3 t94.8 added pair frame-hash-exempt (md5 `794bfdd2…` found in baseline t±3 s window); scene-20 t636.1 added pair not exempt but netted against the removed scene-0 pair → net 0, suppressed + annotated. |
| C2 exp-004 scoped 5-pass symmetric: churn vetoes suppressed + annotated | exp-004 `stable_seg_v1_5pass.json` vs `stable_seg_v2_5pass.json`, `--scenes-changed 0,1,3,4,10,13,14,15,16,18,19,20,21,23,24,26`, hash videos `/tmp/opencode/exp004_bench{,_v2}/proj/_work/sfxed.mp4` | **PASS** — default arithmetic still vetoes (control); matched arithmetic zero vetoes; all five old veto types (`weak_hook`, `phone_readability`, `cropped_content`, `irrelevant_panel`, `missing_payoff`) annotated as suppressed with hash proofs (6 of 7 added pairs pixel-identical to v1, incl. the scene-0 4/5 weak_hook+crop the addendum called byte-identical; scene-4 t106.6 NOT exempt — the tall-panel render-framing crop — netted against removed scene-3 pair). Verdict FAIL on score (+21) only — exactly "verdict driven by score". |
| C3 exp-009 v1 REAL regression still vetoes | round2-eval `stable_base.json` vs `stable_t.json` + `_work_t/ambed.mp4` | **PASS** — FAIL with vetoes `cropped_content 4→7` (net 2), `irrelevant_panel 12→15` (net 3), `watermark 4→6` (net 2). Crop-consistent added pairs (scenes 3/20 crops, 0/1/22 watermarks) all NOT hash-exempt — real changed pixels stay veto-eligible. One reviewer-churn pair (scene 14, 2/3) correctly exempted without rescuing the veto. |
| C4 gap-003 v2 residual ghost (p0017) still FAILs | round2-eval `stable_base.json` vs `stable_w2.json` + `_work_w2/ambed.mp4`, scoped `0,4,7,9,13,15,17,20,22` | **PASS** — FAIL with veto `unreadable_text 1→3` (net 1): the scene-0 t26.4 UT pair (the p0017 inpaint-border ghost site) is NOT hash-exempt — the cleaned panel genuinely differs from baseline pixels. Two pure-churn increases on scene 20 (cropped_content, irrelevant_panel — frames baseline-identical) suppressed + annotated. |
| C5 same-video-twice control: zero vetoes | **substitution, see deviations** — exp-004 `stable_seg_v2.json` (3-pass) vs `stable_seg_v2_5pass.json` (5-pass), both reviews of the identical `exp004_bench_v2 …/sfxed.mp4`; hash video = itself on both sides | **PASS** — default arithmetic vetoes on 2 new types + 5 worsened (control); matched arithmetic ZERO vetoes; consensus normalized (pass_counts 3/5 → common fraction 2/3, six 3/5 marginals dropped and listed). All three surviving added pairs frame-hash-exempt (trivially — same video), demonstrating the exemption path independent of netting. |

Note on C4/gap-card wording: the card says "watermark residual ghost p0017…
still vetoes/FAILs". In the archived `stable_w2.json` review the ghost
manifests as the scene-0 **unreadable_text** (2/3) + watermark (2/3) cluster;
per-type, `unreadable_text 1→3` is the count that survives netting — the
verdict FAILs on a real changed-frame fault at the p0017 site, which is the
substance of the criterion. (The scene-0 watermark flag itself nets against
baseline's removed scene-9 watermark — annotated, and the FAIL stands
regardless.)

## Default-path byte-identical verification

Before the change, the unmodified benchmark.py was run on two archived pairs
(outputs kept at `/tmp/opencode/exp015/pre_default/`, `pre_scoped/`); after
the change, identical invocations (no flag) were re-run:

```
diff pre_default/metrics.json post_default/metrics.json   → empty (identical)
diff pre_scoped/metrics.json  post_scoped/metrics.json    → empty (identical)
```

Pairs: stable_base vs stable_t4 (global) and stable_seg_v1_5pass vs
stable_seg_v2_5pass (scoped 16 scenes). Same FAIL verdicts, same veto
strings, same exit codes. Code-level guarantee: the new logic is inside
`if args.matched_vetoes:` branches; the default branch is the original
veto block verbatim, and the `matched_vetoes` key is only added to the
report when the flag is on.

Additional smoke tests:
- `--matched-vetoes` without hash videos: hashing disabled, netting-only —
  cropped_content 4→5 still vetoes (net 1 after netting alone); annotated.
- `--matched-vetoes --hash-video /nonexistent/…`: no crash, hashing
  silently disabled with `frame_hash.enabled: false`.
- Single-pass reviews (no `votes` field): pass counts default 1/1,
  consensus fraction 1.0, runs clean.

## Deviations from the gap card / task

1. **C5 substitution (documented as the task allows).** gap-011's spread-test
   inputs (`round1-eval/run/arm_A.mp4` + stable_A1/A2 reviews) no longer
   exist — `round1-eval/` retains only `stable_spread.md`. Substitute: the
   closest archived same-video pair is exp-004's `stable_seg_v2.json`
   (3-pass) vs `stable_seg_v2_5pass.json` (5-pass), two independent stable
   reviews of the byte-identical `sfxed.mp4` (documented in the exp-004
   addendum: "Re-reviewed the SAME v2 bench cut … not re-rendered"). Bonus:
   this pair also exercises the 3-vs-5-pass consensus normalization, which
   no other archive can. (exp-013's `run/stable_A.json` exists but has no
   second stable review of the same video to pair with.)
2. **Different-pass-count handling = renormalize, not refuse.** The card
   offered either; renormalization to the stricter common fraction was
   chosen because the only same-video archived pair (C5) is itself 3-vs-5 —
   refusal would have made the control un-runnable — and because it keeps
   asymmetric archives comparable. The normalization is fully reported
   (`consensus_fraction`, `consensus_dropped`) so a human can audit what was
   dropped.
3. **Frame-hash source: work-dir `ambed.mp4`/`sfxed.mp4` review cuts, not
   per-scene clip md5s.** The card allowed "the scene clip md5 already
   computed for --scenes-changed"; single-frame framemd5 at the fault
   timestamp + a ±3 s baseline window search was implemented instead because
   (a) the archived faults carry `t` on the review-cut timeline, not
   clip-relative offsets, and (b) whole-clip md5 equality is too strict —
   exp-009 v3/v4 showed sub-second Ken Burns phase offsets make honest
   identical-content clips md5-differ (e.g. only 161/865 frames shared in
   base-vs-t4 c020) while the flagged *frame* is still present in both.
4. **exp-004 hash videos live in /tmp** (`/tmp/opencode/exp004_bench{,_v2}/`,
   ~0.5 GB each) — not copied into the repo. If /tmp is evicted, C2/C5 lose
   the hash-exemption leg; `replay_acceptance.py` degrades gracefully
   (netting-only) but C2c/C5b would then fail and the videos would need
   re-rendering from the archived exp-004 scripts.
5. **No `samples/` images.** The prototype's outputs are metrics JSON, not
   pixels; the per-criterion `replay/<tag>/metrics.json` + `stdout.txt`
   serve as the eyeball-able artifacts.

## Pre-existing tree state (not this experiment)

The working tree was already dirty before this session (uncommitted work
from prior experiments: panel_render, segment_panels, layout_smart, etc.).
That includes an 8-line pre-existing diff on `pipeline/review_video.py`
(an md audit-trail append; file mtime 2026-09-11 14:17, hours before this
session's first edit at 17:42). **exp-015 did not modify review_video.py**;
its diff is not empty only because of that earlier, unrelated change. Fault
definitions and review behavior are untouched by exp-015 either way — the
only file this experiment edited is `pipeline/benchmark.py`, plus the two
new files in this experiment dir.

## Fixture restoration 2026-09-12 (post-approval verification)

Both /tmp exp-004 bench dirs were evicted after the 2026-09-11 replay run,
taking the two frame-hash videos with them (deviation 4's predicted failure
mode). `replay_acceptance.py` degraded exactly as designed — netting-only,
`frame_hash.enabled: false` — and now returns **11/15 (exit 1)**: C2b, C2c,
C2d, C5b fail; every repo-resident-video check (C1 incl. hash-positive proof,
C3, C4 incl. C4c not-exempt, C5a/C5c) still passes.

### What was rebuilt (deterministic layer — fully restored)

- `/tmp/opencode/exp004_bench/proj/raw`: ch2 re-downloaded via
  `download_chapter.py --backend mangadex` (chapter
  `9a7f68b6-fdb8-4280-b93b-32448c5501e0`, 98 pages). **Byte-exact source
  proof:** re-segmenting with no guard (`--mode auto`) reproduces the golden
  `output/…-ch2/panels` — index equal, 0/154 PNG diffs.
- `/tmp/opencode/exp004_bench_v2/proj/panels`: re-segmented
  (`--mode webtoon --split-guard yolo`, HEAD = adopted v2 guard) → 115
  panels; **repeat run byte-identical** (0 file diffs); acceptance gate
  exit 0 (text 0 / art 0 / gutter 0); the four archived exp-014 fixture
  panels `inputs/ch2_p{0015,0041,0059,0101}.png` **md5-match the fresh
  panels exactly**. Segmentation is fully deterministic and reproducible.

### What could NOT be restored, and why (the two sfxed.mp4 renders)

The bench review cuts require the exp-004 bench's **fresh 35-scene script**
(script_from_panels LLM output, 2026-09-08), its **edge-tts mp3s** and
**word-timing w###.json files**. Exhaustive search — whole disk (incl.
Trash), the opencode session DB (part/message/event tables), and the
opencode git snapshot stores — finds **no surviving copy** of any of them.
(Only exp-014's 25-scene *golden-script* work dirs survive in
`/tmp/opencode/exp014_eval` — a different script; exp-014's report already
recorded `/tmp/opencode/exp004_bench_v2 — deleted` and "word timings not
archived".) Regenerating the script is LLM-nondeterministic and edge-tts
audio is nondeterministic, so any re-render would carry different scene
boundaries/timestamps than the archived `stable_seg_*` reviews reference —
it would be a NEW fixture, not a restoration, and hashing the archived fault
timestamps against it would be meaningless. Additionally the C2 baseline arm
needs the pre-v2 (v1) guard's 118-panel segmentation, which HEAD no longer
produces (the adopted v2 guard yields 115). Per the no-check-weakening rule,
C2b/C2c/C2d/C5b are left failing rather than edited.

**The failures indict the lost fixture, not the code.** Evidence:

1. `replay/archived_pass_2026-09-11/` — the ORIGINAL 15/15 metrics recovered
   from the opencode snapshot store (git blobs `3e9d6967`/`fc7013d5`/
   `d91c0466`…), matching the recorded 15/15 stdout of 2026-09-11 17:48:28
   (DB part `prt_09067adb4001kZ0KLvsBrex21R`, 21.3 s wall). They show C2
   with zero vetoes, all five churn types suppressed with per-pair
   frame-hash proofs (scene-0 t6.5 md5 `173af575…` exempt; scene-4 t106.6
   correctly NOT exempt — the render-framing crop — netted against the
   removed scene-3 pair), and C5 with zero vetoes on hash-video ==
   hash-baseline-video.
2. `replay/selfhash_control_2026-09-12/` — a fresh same-video-hash control
   run TODAY on repo-resident archives (stable_base vs stable_t with
   `--hash-video == --hash-baseline-video` = `_work_t/ambed.mp4`): every
   added pair frame-hash-exempted, net 0 across all three types, **zero
   vetoes** — the exact code path C5b exercises, passing on current code.
3. C1/C3/C4 pass live, including C1c's positive hash proof and C4c's
   changed-pixels-NOT-exempt hard-gate guard.

Live suite after restoration work: **11/15, exit 1** — unchanged, because
the four failing checks need the unreproducible renders, not the rebuilt
raws/panels.

## Hash sidecars — fixtures made eviction-proof (implemented 2026-09-12)

The 0.5 GB videos were never the real dependency — the hash-exemption leg
only consumes (a) the single decoded-frame md5 at each fault `t` in the
experiment arm and (b) the set of decoded-frame md5s in `t ± hash-window` of
the baseline arm. Those values are now archivable as per-review-pair JSON
sidecars, and `benchmark.py` can replay the exemption from them when the
videos are gone. The suite is back to **15/15 (exit 0)** with no /tmp
dependency.

### Sidecar schema (`benchmark-hash-sidecar/v1`)

One combined file per (experiment review, baseline video) pair — combined
rather than split exp/base files because the baseline window md5s are keyed
by the *experiment's* fault timestamps, so the two halves are meaningless
apart:

```json
{
  "schema": "benchmark-hash-sidecar/v1",
  "hash_window_s": 3.0,
  "video": "<original experiment video path, informational>",
  "baseline_video": "<original baseline video path, informational>",
  "provenance": "<how the hashes were obtained>",
  "frames":           { "<t %.3f>": "<framemd5 of experiment frame at t>" },
  "baseline_windows": { "<t %.3f>": ["<sorted framemd5s in baseline t±window>"] }
}
```

Timestamps are keyed `f"{t:.3f}"` (fault `t` values are floats from
review.json). Equivalence by construction: the exporter calls the very same
`frame_md5_at` / `frame_md5_window` helpers the live path uses, and the
consumer performs the identical membership test (`frames[t] in
baseline_windows[t]`), so exemption decisions from a sidecar are bit-for-bit
the same as from the videos. A missing `frames[t]` entry means "no md5
obtainable" → not exempt (the conservative direction — a sidecar can never
manufacture an exemption the video would not have granted).

### New flags

| flag | purpose |
|---|---|
| `--hash-sidecar PATH` | sidecar standing in for BOTH hash videos in the `--matched-vetoes` exemption. Affects nothing outside that leg. |
| `--write-hash-sidecar OUT` | generator mode: export a sidecar for `--review` + `--hash-video`/`--hash-baseline-video` (+`--hash-window`) and exit without scoring. Deterministic — re-export is byte-identical. Exports **every** fault timestamp in the full review (pre `--scenes-changed`), so one sidecar serves any scoping variant. |

**Precedence: live videos win.** If both videos exist and a sidecar is
given, the videos are used (they are ground truth; a sidecar is an archived
projection of them, so a stale sidecar can never mask a real pixel change).
The sidecar is used only when a hash video is missing. Neither available →
hashing off, netting-only (unchanged behavior).

**Validation & fallback:** wrong schema, unreadable JSON, or a
`hash_window_s` that differs from `--hash-window` (window sets are baked at
export time — using them at another window would silently change decisions)
all disable the sidecar with a loud stderr note and degrade to exactly the
missing-video behavior (`frame_hash.enabled: false`). Never a crash. When the
sidecar IS used, `metrics.json → matched_vetoes.frame_hash` gains
`source: "sidecar"` + `sidecar_provenance` + the original video paths — the
video-fed output stays byte-identical to pre-sidecar builds.

### Archived sidecars (`replay/sidecars/`)

| file | pair | provenance |
|---|---|---|
| `c1_stable_t4_vs_base.hash_sidecar.json` | stable_t4 vs stable_base | exported from live `_work_t4`/`_work_base/ambed.mp4` |
| `c3_stable_t_vs_base.hash_sidecar.json` | stable_t vs stable_base | exported from live `_work_t`/`_work_base/ambed.mp4` |
| `c4_stable_w2_vs_base.hash_sidecar.json` | stable_w2 vs stable_base | exported from live `_work_w2`/`_work_base/ambed.mp4` |
| `c2_seg_v2_vs_v1_5pass.hash_sidecar.json` | stable_seg_v2_5pass vs v1_5pass | **reconstructed from archived proofs** (see below) |
| `c5_seg_v2_selfpair.hash_sidecar.json` | stable_seg_v2 (3p) vs v2_5pass | **reconstructed from archived proofs** (see below) |

**C2/C5 provenance (reconstructed, not exported).** The exp-004 /tmp bench
videos are unrecoverable, so these two sidecars were built by
`replay/sidecars/reconstruct_from_proofs.py` (kept for audit; deterministic)
from the `frame_hash_proof` entries in the recovered original 15/15 metrics
(`replay/archived_pass_2026-09-11/`): exempt pairs carry their recorded
md5 in both `frames` and the (singleton) `baseline_windows` set; non-exempt
pairs deliberately have **no** `frames` entry, so the consumer returns
not-exempt — the same decision the live video produced. Because the fault
lists are frozen JSON, the set of (t → decision) queries is fixed forever,
so this is decision-for-decision faithful replay, and it is flagged in the
sidecar's `provenance` field ("reconstructed from archived
frame_hash_proof entries …"). Verified: sidecar-replayed C2/C5 metrics ==
archived originals modulo `frame_hash` path fields (JSON-normalized diff
empty).

**Video↔sidecar equivalence proof (C1/C3/C4, live videos available):** each
pair was benchmarked twice — once with `--hash-video/--hash-baseline-video`,
once with only `--hash-sidecar` — and the two metrics.json are identical
after dropping only the input-path fields (`inputs.video`,
`frame_hash.video/baseline_video/source/sidecar_*`). All three: IDENTICAL.

### replay_acceptance.py update

Each check now passes `sidecar=` alongside the video paths; `run_bench`
prefers the live video and falls back to the archived sidecar (instead of
silently disabling hashing as before). **No assertion was changed** — C2/C5
assert exactly what they asserted in the original 15/15. Result 2026-09-12:
**15/15, exit 0**, ~ no /tmp dependency remains.

### Default-path identity re-verified (2026-09-12)

Pre-change flagless runs (`/tmp/opencode/exp015_sidecar/pre_default`,
`pre_scoped` — stable_base vs stable_t4 global; seg_v1 vs v2 5-pass scoped)
diffed against post-change flagless runs: **both empty** (byte-identical
metrics.json, same FAIL verdicts/exit codes). Code-level: all sidecar logic
sits behind `--hash-sidecar`/`--write-hash-sidecar`/`--matched-vetoes`;
the default branch is untouched.

### ARCHIVAL RULE (binding for future experiments)

**Every future experiment that relies on the frame-hash exemption must
export sidecars at eval time, while the videos still exist:**

```bash
./venv/bin/python3 pipeline/benchmark.py \
    --review <experiment stable review.json> \
    --hash-video <experiment video> --hash-baseline-video <baseline video> \
    --experiment <exp dir> \
    --write-hash-sidecar <exp dir>/replay/sidecars/<pair>.hash_sidecar.json
```

Sidecars are ~50–200 KB per pair (vs 0.5 GB videos) and commit-safe. This is
what makes an acceptance suite permanently replayable after /tmp eviction —
the 2026-09-12 C2/C5 loss is exactly the failure mode this rule closes.
Secondary hardening still recommended: archive the bench project's
script/OCR/w###.json/TTS mp3s (≈15 MB, the only nondeterministic inputs) so
faithful re-renders stay possible too.
