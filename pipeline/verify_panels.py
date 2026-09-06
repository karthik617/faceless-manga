#!/usr/bin/env python3
"""verify_panels.py — STAGE 3.5 (opt-in): panel<->narration relevance verifier.

Post-processes a drafted script JSON (script_from_panels.py output) against the
cached OCR reads (<slug>.ocr.json). The scriptwriter picks panels prompt-only;
this pass catches the picks the prompt rules failed to prevent (title cards,
SFX-only frames, wrong-moment close-ups) BEFORE a full render, instead of
leaving them for review_video.py to flag after the fact (gap-001).

Three tiers (per improvements/experiments/exp-001-irrelevant-panel/research.md):
  1. deterministic gate — zero cost; trusts the OCR reads' kind/quality tags.
  2. lexical relevance  — stdlib token-overlap of narration vs the panel's
                          textual proxy (scene_beat + dialogue + narration_text).
                          No rapidfuzz/CLIP: photo-trained image models are
                          unvalidated on B/W manga and the OCR reads already
                          give us a frontier-VLM text description per panel.
  3. vision escalation  — ONLY for scenes tier 2 calls ambiguous: batched
                          gateway.llm_vision yes/no checks (the same model that
                          produced the reads, now looking at actual pixels).

Substitution guarantees no scene is left panel-less, drawing replacements from
kind=story/quality=ok panels in READING ORDER (bounded by the neighbour scenes'
panel indices, so no future-chapter spoiler jumps).

Everything it does is logged to stdout and into the script JSON under "_verify"
so the improvement-loop evaluator can diff decisions against review faults.

Usage:
    ./venv/bin/python3 pipeline/verify_panels.py output/<slug>/<slug>.json \
        [--ocr <path>] [--dry-run] [--no-vision]

Default writes the script in place (after a one-time backup to
<slug>.json.pre_verify). --dry-run prints the plan only.
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path

# Tier-1 rules ---------------------------------------------------------------
HARD_DROP_KINDS = {"cover", "credits", "endmatter"}
# textonly panels pass only when the narration actually quotes the panel's
# text; a >=6-char shared substring (case/whitespace-normalized) is the test.
QUOTE_MATCH_MIN = 6

# Tier-2 tuning --------------------------------------------------------------
# Below T_LOW even the scene's BEST panel is suspect -> escalate to vision.
# Tuned on the golden chapter cache (the-world-after-the-fall-ch2): clear
# matches land 0.3+, title-card/wrong-moment picks land under ~0.1.
T_LOW = 0.15
NAME_BOOST = 0.15          # per shared proper noun (character names carry the
NAME_BOOST_CAP = 0.30      # most signal across the essay/scene_beat register gap)
SUBSTITUTE_POOL = 8        # nearest-by-index candidates considered per rescue

# Tier-3 batching ------------------------------------------------------------
VISION_SCENES_PER_CALL = 4

# tiny stopword list: just enough to stop function words dominating overlap
# on short scene_beats (a full NLP stack would be overkill for this signal).
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "his", "her", "its",
    "was", "with", "that", "this", "have", "has", "had", "from", "they",
    "them", "then", "than", "into", "onto", "out", "off", "over", "under",
    "all", "one", "two", "who", "what", "when", "where", "why", "how",
    "can", "will", "just", "now", "down", "back", "there", "here", "him",
    "she", "he", "it", "is", "in", "on", "at", "of", "to", "a", "an", "as",
    "we", "our", "their", "your", "been", "being", "were", "does", "did",
}


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

def _norm(text):
    """Lowercase + collapse whitespace, for verbatim-substring checks."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _tokens(text):
    """Content tokens for overlap scoring (lowercased, stopwords removed)."""
    words = re.findall(r"[a-zA-Z']+", text or "")
    return {w.lower() for w in words if len(w) >= 3 and w.lower() not in _STOPWORDS}


def _proper_nouns(text):
    """Capitalized tokens = cheap proper-noun proxy (character/place names).
    Sentence-initial words sneak in, but shared *names* across both texts is
    what we boost, so false capitals rarely co-occur on both sides."""
    words = re.findall(r"\b[A-Z][a-z]+\b", text or "")
    return {w.lower() for w in words if w.lower() not in _STOPWORDS}


def _panel_text(read):
    """The panel's full textual proxy from its OCR read."""
    parts = [read.get("scene_beat", ""), read.get("narration_text", "")]
    for d in read.get("dialogue", []) or []:
        if isinstance(d, dict):
            parts.append(d.get("text", ""))
    return " ".join(p for p in parts if p)


def _dialogue_text(read):
    """Only the quoted/spoken text — what a textonly panel must share with
    the narration to be allowed to stay."""
    parts = [read.get("narration_text", "")]
    for d in read.get("dialogue", []) or []:
        if isinstance(d, dict):
            parts.append(d.get("text", ""))
    return " ".join(p for p in parts if p)


def _has_verbatim_quote(narration, panel_quote_text, min_len=QUOTE_MATCH_MIN):
    """True if narration shares a >=min_len-char verbatim run with the panel's
    text (case-insensitive, whitespace-normalized). Matched at WORD granularity
    — a raw char-substring test passes on mid-word fragments ('nothing' vs
    'everyTHING'), which is not "quoting the panel". SequenceMatcher over word
    sequences finds the longest common contiguous word run; autojunk off
    because narrations are long enough to trip the popular-element heuristic."""
    a = _norm(narration).split()
    b = _norm(panel_quote_text).split()
    if not a or not b:
        return False
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = m.find_longest_match(0, len(a), 0, len(b))
    return len(" ".join(a[match.a:match.a + match.size])) >= min_len


def relevance_score(narration, read):
    """Tier-2 lexical score in [0, 1]: token-overlap coefficient between the
    narration and the panel's textual proxy, plus a proper-noun boost.
    Overlap coefficient (|∩| / min(|A|,|B|)) rather than Jaccard, because
    scene_beats are one factual sentence vs 4-8 sentences of essay prose —
    Jaccard would punish the length mismatch, not the content mismatch."""
    ptext = _panel_text(read)
    nt, pt = _tokens(narration), _tokens(ptext)
    if not nt or not pt:
        return 0.0
    base = len(nt & pt) / min(len(nt), len(pt))
    names = _proper_nouns(narration) & _proper_nouns(ptext)
    boost = min(len(names) * NAME_BOOST, NAME_BOOST_CAP)
    return min(base + boost, 1.0)


# ---------------------------------------------------------------------------
# JSON parse helper (same behavior as script_from_panels._extract_json; local
# copy so this module imports standalone without pulling in gateway/requests)
# ---------------------------------------------------------------------------

def _extract_json(text, expect="array"):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    open_c, close_c = ("[", "]") if expect == "array" else ("{", "}")
    i, j = t.find(open_c), t.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        return json.loads(t[i:j + 1])
    raise ValueError(f"could not parse JSON {expect} from model output:\n{text[:400]}")


# ---------------------------------------------------------------------------
# core verifier
# ---------------------------------------------------------------------------

class Verifier:
    def __init__(self, data, reads, script_path, use_vision=True, t_low=T_LOW):
        self.data = data
        self.script_dir = Path(script_path).resolve().parent
        self.use_vision = use_vision
        self.t_low = t_low
        self.log = []
        # reads keyed by bare filename; index = READING ORDER position in the
        # OCR file (the file is written in panel order by script_from_panels).
        self.reads = {}
        self.order = {}
        for i, r in enumerate(reads):
            name = r.get("panel", "")
            self.reads[name] = r
            self.order[name] = i
        self.n_reads = len(reads)

    # -- helpers -------------------------------------------------------------

    def _read_for(self, panel_ref):
        """Panel refs in the script are paths like 'panels/p0001.png'; the OCR
        file keys bare filenames. Missing reads pass through as story/ok so an
        OCR gap can never delete panels (fail open, log it)."""
        name = Path(panel_ref).name
        r = self.reads.get(name)
        if r is None:
            self._note("warn", None, panel_ref, "no OCR read; treated as story/ok")
            r = {"panel": name, "dialogue": [], "narration_text": "",
                 "sfx": "", "scene_beat": "", "kind": "story", "quality": "ok"}
        return r

    def _idx(self, panel_ref):
        return self.order.get(Path(panel_ref).name, -1)

    def _note(self, action, scene, panel, reason, **extra):
        entry = {"action": action, "scene": scene, "panel": panel,
                 "reason": reason}
        entry.update(extra)
        self.log.append(entry)
        s = f"scene {scene}" if scene is not None else "-"
        print(f"  [verify] {action:<10} {s:<9} {panel or '-':<22} {reason}")

    # -- tier 1: deterministic gate -------------------------------------------

    def tier1(self, scene_i, scene):
        narration = scene.get("narration", "")
        kept, dropped = [], []
        for p in scene.get("panels", []):
            r = self._read_for(p)
            kind, quality = r.get("kind", "story"), r.get("quality", "ok")
            if kind in HARD_DROP_KINDS:
                dropped.append(p)
                self._note("drop", scene_i, p, f"tier1: kind={kind}")
                continue
            if kind == "textonly":
                if _has_verbatim_quote(narration, _dialogue_text(r)):
                    kept.append(p)  # narration quotes this exact text -> allowed
                else:
                    dropped.append(p)
                    self._note("drop", scene_i, p,
                               "tier1: textonly, narration does not quote it")
                continue
            if scene_i == 0 and not (kind == "story" and quality == "ok"):
                # hook retention rule: only clean story art in scene 0
                dropped.append(p)
                self._note("drop", scene_i, p,
                           f"tier1: hook requires story/ok (got {kind}/{quality})")
                continue
            kept.append(p)
        # cut panels go only when the scene still has another passing panel —
        # a cut panel beats an empty screen.
        non_cut = [p for p in kept
                   if self._read_for(p).get("quality") != "cut"]
        if non_cut and len(non_cut) < len(kept):
            for p in kept:
                if self._read_for(p).get("quality") == "cut":
                    dropped.append(p)
                    self._note("drop", scene_i, p,
                               "tier1: quality=cut with alternatives present")
            kept = non_cut
        return kept, dropped

    # -- tier 2: lexical relevance --------------------------------------------

    def tier2(self, scene_i, scene, kept):
        narration = scene.get("narration", "")
        scores = {p: relevance_score(narration, self._read_for(p)) for p in kept}
        for p, s in scores.items():
            self.log.append({"action": "score", "scene": scene_i,
                             "panel": p, "score": round(s, 3)})
        best = max(scores.values()) if scores else 0.0
        ambiguous = bool(kept) and best < self.t_low
        if ambiguous:
            self._note("ambiguous", scene_i, None,
                       f"tier2: best score {best:.3f} < {self.t_low}")
        return scores, ambiguous

    # -- tier 3: batched vision verify ----------------------------------------

    def tier3(self, ambiguous):
        """ambiguous: list of (scene_i, scene, kept_panels). One llm_vision
        call per batch of up to VISION_SCENES_PER_CALL scenes: all their panel
        images + narrations, strict yes/no JSON back. Returns
        {(scene_i, panel_ref): relevant_bool}; unanswered pairs default True
        (fail open — vision must positively reject to drop)."""
        import gateway  # lazy: --no-vision runs must not need requests/token
        verdicts = {}
        for start in range(0, len(ambiguous), VISION_SCENES_PER_CALL):
            batch = ambiguous[start:start + VISION_SCENES_PER_CALL]
            images, lines, key_by_pos = [], [], []
            pos = 1
            for scene_i, scene, kept in batch:
                lines.append(f'Scene {scene_i} narration: "{scene.get("narration", "")}"')
                for p in kept:
                    img = self.script_dir / p
                    if not img.exists():
                        self._note("warn", scene_i, p, "image missing; skip vision")
                        continue
                    images.append(img)
                    lines.append(f"  image {pos}: panel {Path(p).name} (scene {scene_i})")
                    key_by_pos.append((scene_i, p))
                    pos += 1
            if not images:
                continue
            instr = (
                "You are verifying panel relevance for a manga recap video. "
                f"I show you {len(images)} manga panel images. Each belongs to a "
                "narration scene as listed:\n\n" + "\n".join(lines) + "\n\n"
                "For EACH image, answer: does this panel visually depict any part "
                "of its scene's narration (the moment, characters, or action being "
                "described)? Output ONLY a JSON array with exactly "
                f"{len(images)} objects, in image order: "
                '[{"scene": <scene index>, "panel": "<filename>", '
                '"relevant": true/false}] — no prose, no markdown fence.')
            print(f"  [verify] vision batch: {len(batch)} scene(s), "
                  f"{len(images)} panel(s)")
            try:
                raw = gateway.llm_vision(images, instr)
                arr = _extract_json(raw, expect="array")
            except Exception as e:  # gateway/parse failure -> keep panels
                self._note("warn", None, None, f"tier3 call failed: {e}")
                continue
            # align positionally (same drift guard as read_panels): the model
            # sometimes renames panels, but image order is ours.
            for k, key in enumerate(key_by_pos):
                item = arr[k] if k < len(arr) and isinstance(arr[k], dict) else {}
                verdicts[key] = bool(item.get("relevant", True))
        return verdicts

    # -- substitution ----------------------------------------------------------

    def _scene_window(self, scenes_kept, scene_i):
        """Reading-order bounds: (prev scene's max panel index, next scene's
        min panel index). Falls back to the chapter edges when neighbours are
        empty. Keeps rescues from jumping past the story position."""
        lo, hi = -1, self.n_reads
        for j in range(scene_i - 1, -1, -1):
            idxs = [self._idx(p) for p in scenes_kept[j] if self._idx(p) >= 0]
            if idxs:
                lo = max(idxs)
                break
        for j in range(scene_i + 1, len(scenes_kept)):
            idxs = [self._idx(p) for p in scenes_kept[j] if self._idx(p) >= 0]
            if idxs:
                hi = min(idxs)
                break
        return lo, hi

    def substitute(self, scene_i, scene, scenes_kept, original_panels):
        """Rescue an emptied scene: nearest SUBSTITUTE_POOL kind=story,
        quality=ok panels by reading-order index, in-window ones first, best
        tier-2 score wins. Never leaves the scene panel-less: if no candidate
        exists it restores the original panels (a bad panel beats a blank)."""
        narration = scene.get("narration", "")
        lo, hi = self._scene_window(scenes_kept, scene_i)
        # anchor: where this scene "lives" in reading order — its original
        # panels' mean index, else the middle of the neighbour window.
        orig_idxs = [self._idx(p) for p in original_panels if self._idx(p) >= 0]
        anchor = (sum(orig_idxs) / len(orig_idxs)) if orig_idxs else (lo + hi) / 2.0

        pool = [(name, i) for name, i in self.order.items()
                if self.reads[name].get("kind") == "story"
                and self.reads[name].get("quality") == "ok"]
        if not pool:
            self._note("keep-orig", scene_i, None,
                       "substitute: no story/ok panels exist; restoring originals")
            return list(original_panels)

        in_window = [(n, i) for n, i in pool if lo < i < hi]
        # prefer in-window candidates; only fall back outside when the window
        # holds nothing (still nearest-first, so drift stays minimal).
        ranked = sorted(in_window or pool, key=lambda t: abs(t[1] - anchor))
        candidates = ranked[:SUBSTITUTE_POOL]
        scored = sorted(
            candidates,
            key=lambda t: (-relevance_score(narration, self.reads[t[0]]),
                           abs(t[1] - anchor)))
        name, idx = scored[0]
        ref = f"panels/{name}"
        score = relevance_score(narration, self.reads[name])
        self._note("substitute", scene_i, ref,
                   f"rescued empty scene (score {score:.3f}, idx {idx}, "
                   f"window {lo}..{hi}{'' if in_window else ', out-of-window fallback'})")
        return [ref]

    # -- driver ----------------------------------------------------------------

    def run(self):
        scenes = self.data.get("scenes", [])
        scenes_kept = []
        scene_scores = []
        ambiguous = []
        for i, sc in enumerate(scenes):
            kept, dropped = self.tier1(i, sc)
            scores, is_amb = self.tier2(i, sc, kept)
            scenes_kept.append(kept)
            scene_scores.append(scores)
            if is_amb:
                ambiguous.append((i, sc, kept))
            # per-scene plan line (the evaluator + --dry-run read this)
            panel_bits = " ".join(
                f"{Path(p).name}:{scores[p]:.2f}" for p in kept) or "(empty)"
            flag = " AMBIGUOUS" if is_amb else ""
            drop_bit = f" dropped={len(dropped)}" if dropped else ""
            print(f"  [plan] scene {i:>2}: keep {panel_bits}{drop_bit}{flag}")

        if ambiguous and self.use_vision:
            verdicts = self.tier3(ambiguous)
            for (scene_i, p), ok in verdicts.items():
                if not ok and p in scenes_kept[scene_i]:
                    scenes_kept[scene_i].remove(p)
                    self._note("drop", scene_i, p, "tier3: vision judged irrelevant")
        elif ambiguous:
            for scene_i, _sc, _kept in ambiguous:
                self._note("skip-vision", scene_i, None,
                           "tier3 skipped (--no-vision); keeping tier-1 survivors")

        # rescue emptied scenes IN ORDER so earlier substitutions tighten the
        # window for later ones (reading order stays monotone-ish).
        for i, sc in enumerate(scenes):
            if not scenes_kept[i]:
                scenes_kept[i] = self.substitute(i, sc, scenes_kept,
                                                 sc.get("panels", []))

        changed = 0
        for i, sc in enumerate(scenes):
            if sc.get("panels", []) != scenes_kept[i]:
                changed += 1
            sc["panels"] = scenes_kept[i]
        return changed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def verify(script_path, ocr_path=None, dry_run=False, use_vision=True,
           t_low=T_LOW):
    script_path = Path(script_path)
    data = json.loads(script_path.read_text())
    if "_verify" in data and not dry_run:
        # idempotent: a verified script is left alone (re-running the pipeline
        # with the flag must not churn panels a second time).
        print(f"  [verify] {script_path.name} already verified; skipping "
              "(delete the '_verify' key to re-run)")
        return script_path
    ocr_path = Path(ocr_path) if ocr_path else script_path.with_suffix(".ocr.json")
    if not ocr_path.exists():
        sys.exit(f"OCR reads not found: {ocr_path} (run the script step first)")
    reads = json.loads(ocr_path.read_text())

    v = Verifier(data, reads, script_path, use_vision=use_vision, t_low=t_low)
    changed = v.run()

    n_drop = sum(1 for e in v.log if e["action"] == "drop")
    n_sub = sum(1 for e in v.log if e["action"] == "substitute")
    print(f"  [verify] {changed} scene(s) changed "
          f"({n_drop} drops, {n_sub} substitutions)")

    if dry_run:
        print("  [verify] dry-run: no files written")
        return script_path

    data["_verify"] = {"verify_log": v.log,
                       "params": {"t_low": t_low, "vision": use_vision}}
    backup = script_path.with_suffix(".json.pre_verify")
    if not backup.exists():  # first verification keeps the pristine draft
        backup.write_text(script_path.read_text())
        print(f"  [verify] backup -> {backup.name}")
    script_path.write_text(json.dumps(data, indent=2))
    print(f"  [verify] wrote {script_path}")
    return script_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="output/<slug>/<slug>.json (drafted script)")
    ap.add_argument("--ocr", default=None,
                    help="path to <slug>.ocr.json (default: next to the script)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the per-scene plan; write nothing")
    ap.add_argument("--no-vision", action="store_true",
                    help="skip tier-3 gateway vision escalation (offline)")
    ap.add_argument("--t-low", type=float, default=T_LOW,
                    help=f"tier-2 ambiguity threshold (default {T_LOW})")
    args = ap.parse_args(argv)
    verify(args.script, ocr_path=args.ocr, dry_run=args.dry_run,
           use_vision=not args.no_vision, t_low=args.t_low)


if __name__ == "__main__":
    main()
