# exp-001 research: panel↔narration relevance verification at script time

**Gap:** `gap-001-irrelevant-panel` — 20+ HIGH `irrelevant_panel` faults on golden chapter
(`the-world-after-the-fall-ch2`); panel selection is prompt-only in
`pipeline/script_from_panels.py` (RECAP_PROMPT rules at line 72), verified only post-render.

**Environment facts (verified locally, 2026-09-06):**
- CPU-only (`nvidia-smi` not present). Python 3.12 venv.
- venv already has: `onnxruntime 1.29.0`, `numpy 2.5.2`, `opencv-python-headless 5.0`, `pillow 12.3`. **No torch.**
- Gateway (`pipeline/gateway.py`) exposes ONLY `/v1/chat/completions` (text, vision, image-edit).
  There is **no embeddings route**, and the backing models are Claude (Anthropic ships no
  embedding API), so "cheap embedding via gateway" is NOT available. Text-to-text similarity
  must be lexical (local) or LLM-judged (gateway chat call).
- Crucial existing asset: `<slug>.ocr.json` already holds, per panel:
  `dialogue[]`, `narration_text`, `sfx`, `scene_beat`, `kind`, `quality` — a full textual
  proxy for every panel, produced by Claude vision. This makes text-to-text approaches
  first-class, not a fallback.

---

## Candidate matrix

### C1. OpenCLIP (openai CLIP / LAION checkpoints) via `open_clip_torch`
| field | value |
|---|---|
| URL | https://github.com/mlfoundations/open_clip · https://pypi.org/project/open-clip-torch/ |
| License | MIT (package); OpenAI/LAION weights MIT-ish/permissive |
| Install | `pip install open_clip_torch` → **pulls full torch**. CPU torch wheel ~200 MB (`--index-url .../cpu`) but default pip pulls CUDA torch + nvidia libs ≈ 2.5–4 GB |
| Model size | ViT-B-32 checkpoint ~600 MB (laion2b_s34b_b79k) / ~350 MB (openai) |
| Py 3.12 | yes (classifiers list 3.9–3.12; verified on PyPI) |
| CPU latency | ViT-B/32 ≈ 100–300 ms/image on a modest CPU; text encode ~20 ms. A 40-panel chapter scored against ~25 narrations ≈ 15–30 s total (embeddings cached) |
| Integration | new `verify_panels(reads, scenes)` after `draft_script()`: embed each scene narration + each candidate panel image, cosine-gate, substitute from in-order `kind=story` panels |
| B/W-manga risk | **HIGH.** CLIP is trained on web photos (LAION/WIT). Known domain gap on line-art/greyscale manga: the entire ecosystem of anime-finetuned CLIPs and Danbooru-trained taggers (see C5 evidence) exists precisely because vanilla CLIP scores are unreliable on anime/manga art. Absolute cosine values will be compressed and noisy on B/W panels; a fixed threshold will misfire. Narration is stylized essay prose ("nobody saw coming...") not caption-style text — further mismatch with CLIP's caption training distribution. |
| Verdict | Reject: 2.5+ GB dep for an unvalidated-domain signal |

### C2. SigLIP / SigLIP2 (google) via `transformers`
| field | value |
|---|---|
| URL | https://huggingface.co/google/siglip2-base-patch16-224 |
| License | Apache-2.0 |
| Install | `pip install transformers torch` (same torch weight problem as C1) |
| Model size | base = **0.4 B params, F32 safetensors ≈ 1.5 GB** download |
| Py 3.12 | yes |
| CPU latency | ~0.5–1.5 s/image (patch16-224, 400M params, fp32 CPU) — 40 panels ≈ 30–60 s |
| Integration | same hook as C1; sigmoid scores are better calibrated for absolute thresholding than CLIP softmax — its one real advantage here |
| B/W-manga risk | **HIGH.** Trained on WebLI (web photos, multilingual). Model card shows zero comics/manga evaluation. Same domain-gap caveat as C1, bigger download, slower on CPU |
| Verdict | Reject: best-in-class on natural images, unproven on manga, heaviest install |

### C3. sentence-transformers `clip-ViT-B-32`
| field | value |
|---|---|
| URL | https://huggingface.co/sentence-transformers/clip-ViT-B-32 |
| License | Apache-2.0 (package); model = OpenAI CLIP weights |
| Install | `pip install sentence-transformers` → **still pulls torch + transformers** (~2.5 GB default) |
| Model size | ~600 MB |
| Py 3.12 | yes |
| CPU latency | same as C1 (identical ViT-B/32 backbone), nicer API (`model.encode(Image)`, `util.cos_sim`) |
| B/W-manga risk | HIGH — identical weights to C1, identical domain gap |
| Verdict | Reject: convenience wrapper, same fundamentals |

### C4. fastembed (Qdrant) — ONNX CLIP, no torch  ← best ML option
| field | value |
|---|---|
| URL | https://github.com/qdrant/fastembed · https://pypi.org/project/fastembed/ (0.8.0) |
| License | Apache-2.0 |
| Install | `pip install fastembed` — wheel is **117 KB**, pure-python, deps are onnxruntime (already installed), tokenizers, pillow (already installed), huggingface-hub. Total new bytes on disk ≈ 20–40 MB of deps. **No torch.** Py ≥3.10 incl. 3.12 (verified classifiers) |
| Models | `ImageEmbedding("Qdrant/clip-ViT-B-32-vision")` ≈ 340 MB ONNX + `TextEmbedding("Qdrant/clip-ViT-B-32-text")` ≈ 250 MB ONNX, shared 512-d space. One-time HF download, cached |
| CPU latency | ONNX ViT-B/32 ≈ 50–150 ms/image on CPU (fastembed's raison d'être is CPU/serverless); 40 panels + 25 narrations ≈ 5–15 s, cache image embeddings in `<slug>.clipemb.json` |
| Integration | after `draft_script()`: (1) embed all panel PNGs once; (2) embed each scene's narration; (3) per scene, cosine(panel, narration); rank-based gate — reject a panel only if it scores in the bottom quartile *for that narration* AND another in-order story panel beats it by margin δ. Rank-relative gating sidesteps the absolute-threshold calibration problem |
| B/W-manga risk | **HIGH (same weights as C1/C3)** — the runtime is light but the signal is still photo-CLIP. Mitigations: use relative ranking not absolute threshold; use it only to *order* substitution candidates, with the deterministic gate (C6) as the primary filter. Must be validated on the golden chapter before trusting |
| Verdict | **Runner-up.** Cheapest possible real image-text model (no torch, reuses installed onnxruntime), but ship only after an offline sanity eval on golden-chapter panels |

### C5. Manga/anime-domain models (evidence check)
- **magiv2 / "Tails Tell Tales"** — https://huggingface.co/ragavsachdeva/magiv2 (Oxford VGG).
  Purpose-built manga model (detection, OCR, speaker diarisation). Proves the domain gap is
  real enough to warrant dedicated models. **License: research/non-commercial only** — a
  monetized YouTube recap channel is commercial use → **unusable** without a bespoke license.
  Also torch + `trust_remote_code`, GPU-oriented.
- **SmilingWolf/wd-swinv2-tagger-v3** — https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3.
  Apache-2.0, **ONNX (needs onnxruntime ≥1.17 — we have 1.29)**, 98 M params, Danbooru-trained.
  It's a fixed-vocab *tagger*, not free-text similarity — cannot score arbitrary narration.
  Could serve as a future auxiliary signal (e.g. tag `1boy, fighting` vs narration keywords)
  but does not solve this gap alone.
- Anime-finetuned CLIPs on HF (danbooru/hakubooru lineage) are mostly color-illustration
  trained, variously licensed, and none has published B/W-manga-panel retrieval evals.
  **Takeaway:** no drop-in, commercially-licensed, manga-validated image-text scorer exists.

### C6. Non-ML: reuse existing OCR reads — deterministic gate + lexical similarity
| field | value |
|---|---|
| URL | https://pypi.org/project/rapidfuzz/ (3.14.6) — optional; stdlib `difflib`/hand-rolled BM25 works too |
| License | rapidfuzz MIT |
| Install | `pip install rapidfuzz` — prebuilt manylinux wheel ~3 MB, C++ ext, Py ≥3.11 incl. 3.12. Or **zero installs** with a ~40-line pure-python token-overlap/BM25 scorer over numpy |
| Model size | none |
| CPU latency | **microseconds per pair**; whole chapter < 50 ms |
| Integration | two layers in `script_from_panels.py`: **(a) hard gate** — after `draft_script()`, walk `data["scenes"]`, look up each panel's read; reject `kind ∈ {cover,credits,endmatter}` outright (belt-and-braces: `usable_reads()` already drops them from the prompt, but the model can still *name* them since filenames are sequential — this is likely the source of several golden-chapter faults); reject `kind=textonly` unless narration shares a ≥6-word span with the panel's `narration_text`/dialogue (`fuzz.partial_ratio ≥ 85`); reject `quality=cut` when an alternative exists. **(b) soft relevance** — score narration vs (`scene_beat` + dialogue text + `narration_text`) with `fuzz.token_set_ratio` + noun/name overlap; below threshold → substitute the best-scoring `kind=story, quality=ok` panel within the scene's reading-order window (bounded window ⇒ no future-chapter spoiler jumps, satisfying the reading-order quality guard) |
| B/W-manga risk | **NONE on the image side** — it never looks at pixels; it trusts the Claude-vision reads, which are already the pipeline's ground truth. Risk shifts to read quality: a mis-tagged `kind` slips through. `scene_beat` is factual register while narration is dramatic essay register, so pure lexical overlap can be weak on paraphrase — mitigated by matching on character names / entities and by the escalation in C7 |
| Verdict | **Primary building block** — directly kills the three observed fault shapes (title cards ⇒ kind gate; SFX-only frames ⇒ sparse/textonly gate; dog-tag close-up during action ⇒ scene_beat mismatch) |

### C7. Vision-LLM pairwise verify via gateway (zero new deps)
| field | value |
|---|---|
| URL | existing `pipeline/gateway.py::llm_vision` (Claude Sonnet 4.5 via Athena) |
| License / install | n/a — already in use |
| Cost/latency | ~3–8 s per vision call. Naive 1 call/scene × 25 scenes ≈ 2–4 min added — risks the ≤2x wall-time budget. Batched (6 panel-images + their scene narrations per call, mirroring `READ_BATCH`) ≈ 5–7 calls ≈ 40–60 s — comfortably inside budget |
| Integration | `verify_scene_panels(scenes, reads)`: send each scene's chosen panel images + narration, ask for JSON `{scene_idx, panel, relevant: bool, reason}`; on `false`, trigger the C6 substitution machinery. Best used as **escalation tier**: only scenes whose C6 lexical score falls in an ambiguous band (clear-pass and clear-fail skip the call), typically 5–10 calls/chapter |
| B/W-manga risk | **LOW** — the same model already reads these exact panels successfully in stage READ; frontier VLMs handle manga art well. Residual risk: JSON drift (reuse `_extract_json`) and verifier disagreeing with reader tags (log, prefer verifier) |
| Verdict | **Escalation tier of the recommendation** |

---

## "Build ourselves" fallback (existing deps + gateway only)

`pipeline/panel_verify.py`, called from `build()` between `draft_script()` and the JSON write:

1. **Deterministic gate (0 cost).** For every `scenes[i].panels[j]`, join to its OCR read.
   Reject: `kind ∈ {cover,credits,endmatter}`; `kind=textonly` without a quoted-text match
   (difflib `SequenceMatcher.find_longest_match` ≥ 6 words vs narration); `quality ∈ {cut,sparse}`
   when a substitute exists. Hook scene additionally: require `kind=story` and non-empty
   `scene_beat` (covers the `weak_hook` guard).
2. **Lexical relevance (0 cost).** Score = 0.6·token-overlap(narration, scene_beat) +
   0.3·overlap(narration, dialogue+narration_text) + 0.1·name-overlap (capitalized tokens).
   Pure python + numpy; rapidfuzz optional upgrade. Panels < T_low ⇒ replace; > T_high ⇒ keep.
3. **LLM escalation (gateway, ~5–10 batched vision calls).** Ambiguous band [T_low, T_high]
   ⇒ batched `llm_vision` yes/no verify (C7). Cache verdicts to `<slug>.verify.json`.
4. **Substitution (never panel-less).** Replacement pool = `kind=story, quality=ok` panels
   whose reading-order index lies within the window spanned by the scene's neighbours'
   panels (order preserved ⇒ no spoiler jumps). Rank pool by lexical score (and by CLIP
   cosine if C4 is later enabled). If pool empty, keep the original panel and log —
   satisfies "no scene left panel-less".
5. **Report.** Write per-scene decisions to `<slug>.verify.json` for the improvement loop
   to diff against `review_video.py` faults.

Tuning data is free: replay the cached `output/the-world-after-the-fall-ch2/*.ocr.json` +
script against `review.md`'s 10 known-bad scenes to pick T_low/T_high before any render.

## Recommendation

**Primary: C6 + C7 hybrid ("build ourselves", zero mandatory new deps).**
The pipeline already owns a per-panel textual description from a frontier VLM; the observed
faults (title cards, SFX-only frames, wrong-moment close-ups) are all detectable from
`kind`/`quality`/`scene_beat` alone. Deterministic gate + lexical score + batched LLM
escalation directly targets all 10 flagged scenes, is deterministic-first (reproducible,
threshold-tunable offline against the cached golden chapter), respects reading order, and
adds ~1 min wall time (within ≤2x). Expected impact: title-card/credits/textonly faults
(~half the cluster) eliminated by the hard gate alone; wrong-moment panels caught by
lexical+LLM tiers → **20 → ≤10 HIGH is realistic; ≤5 plausible.**
Optional 3 MB `rapidfuzz` (MIT) sharpens matching but stdlib difflib suffices to ship.

**Runner-up: C4 fastembed ONNX CLIP** (Apache-2.0, ~120 KB package reusing installed
onnxruntime, ~600 MB one-time model download, ~100 ms/panel CPU) as an additional ranking
signal for substitution — but only after an offline eval proves photo-trained CLIP separates
relevant/irrelevant on these B/W panels; do not gate on absolute CLIP scores.

**Rejected:** open_clip_torch / transformers SigLIP2 / sentence-transformers (2.5+ GB torch
stack on CPU-only box for an unvalidated domain signal); magiv2 (non-commercial license);
wd-tagger (fixed vocab, not free-text similarity — possible future auxiliary signal).

## Main risks
1. Verifier depends on OCR-read accuracy: a panel mis-tagged `kind=story` with a vague
   `scene_beat` passes the gate. Mitigation: LLM escalation tier sees actual pixels.
2. Register mismatch (dramatic essay prose vs factual scene_beat) weakens lexical scores ⇒
   over-escalation to the LLM tier. Mitigation: name/entity matching, band tuning on cache.
3. Over-rejection could starve scenes of panels. Mitigation: substitution always falls back
   to keeping the original + log (acceptance: zero new fault types).
