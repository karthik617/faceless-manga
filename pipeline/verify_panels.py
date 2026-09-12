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

Tier 1.5 (--payoff / MANGA_VERIFY_PAYOFF=1, exp-005 — ADOPTED: manga.py passes
--payoff by default, opt out there with --no-payoff-check; the module-level
default stays opt-in, same convention as panel_render's seq layout): quote-payoff
pass. Every speech-verb-anchored quote in a narration promises that line is
visible on a panel in the scene; this pass verifies it against the OCR cache
BEFORE render (review_video.py's missing_payoff catches it only after).
Misses are rescued by appending the quote-carrying panel (never replacing —
a panel whose text the narration literally quotes is relevance-safe by
definition), or, when the quote exists nowhere in the chapter, by rewriting
the narration to indirect speech. With the flag off this pass adds zero log
entries and the output is byte-identical.

Tier 1.25 (--textonly-filter / MANGA_TEXTONLY_FILTER=1, exp-017, opt-in):
text-dominant-fragment gate. Tier 1 trusts the OCR reads' kind tag, but the
vision OCR narrates what a bubble SAYS and files cutout fragments as
kind=story (9/11 ch4 offenders). This tier re-derives "effectively textonly"
from pixels (DBNet text-coverage + Otsu ink-outside-text via textonly_gate.py)
and the read's own confessions, drops high-confidence text cards, and forces
the grey zone into a tier-3 vision art-check regardless of tier-2 scores
(which REWARD quoted bubbles — the narration quoting a bubble makes it the
most lexically relevant, least visual panel in the scene). It also narrows
the tier-1 textonly quote-exception: a NARRATION-box card (no dialogue)
stays a black card on screen even when the narrator quotes it, so the
exception only sanctions dialogue-carrying panels. With the flag off this
tier adds zero log entries and the output is byte-identical.

Usage:
    ./venv/bin/python3 pipeline/verify_panels.py output/<slug>/<slug>.json \
        [--ocr <path>] [--dry-run] [--no-vision] [--payoff] [--textonly-filter]

Default writes the script in place (after a one-time backup to
<slug>.json.pre_verify). --dry-run prints the plan only.
"""
import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

# Tier-1 rules ---------------------------------------------------------------
HARD_DROP_KINDS = {"cover", "credits", "endmatter"}
# textonly panels pass only when the narration actually quotes the panel's
# text; a >=6-char shared substring (case/whitespace-normalized) is the test.
QUOTE_MATCH_MIN = 6
# exp-017 v2 anchor guard: a stricter verbatim floor than QUOTE_MATCH_MIN.
# 6 chars anchors single character names ("JAEHWAN..." bubble vs a narration
# that merely mentions Jaehwan) and would immunize pure name-cards the gate
# correctly drops; 10 chars needs a real quoted phrase ("what's that" = 11).
ANCHOR_QUOTE_MIN = 10

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
# exp-017 v2: single-image drop confirmations are bounded per run. Suspects
# run ~15/chapter and proposed drops ~6 (ch2 v1), so 12 never binds in
# practice — it exists so a pathological chapter can't turn the confirmation
# pass into an unbounded gateway bill. Over-cap panels are KEPT (fail open).
MAX_ART_CONFIRM = 12

# Tier-1.5 payoff tuning (exp-005) -------------------------------------------
# Thresholds tuned on the golden-chapter OCR cache (see research.md §3/§5):
# verbatim-in-OCR quotes score 0.60-1.00 even with split-panel and synthetic
# OCR-noise handicaps; unrelated quote<->panel pairs top out at 0.29. The
# 0.45/0.75 cuts sit in the empty middle of those two distributions.
PAYOFF_OK = 0.75           # >= this: the quote is on-screen, done
PAYOFF_ABSENT = 0.45       # < this everywhere: quote genuinely missing
PAYOFF_MIN_QUOTE = 4       # normalized quotes shorter than this match nothing

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
# tier-1.5 payoff helpers (exp-005)
# ---------------------------------------------------------------------------

# Speech-verb list mirrors review_video.py's — a quote only counts as a
# visual promise when a speech verb anchors it ("he screams: 'X'"); bare
# quotes are often stylistic. LOCAL COPY on purpose: review_video.py's fault
# definitions are the measuring stick and must not move while experimenting.
_SPEECH_VERBS = (r"(?:says?|said|screams?|screamed|shouts?|shouted|yells?|"
                 r"yelled|asks?|asked|whispers?|whispered|cries|cried|"
                 r"replies|replied|answers?|answered|speaks?|spoke|"
                 r"echo(?:es|ed)?|mutters?|muttered|calls?|called)")

# The FIXED extraction pattern (research.md C5). Two upgrades over the review
# regex: (1) input is Unicode-normalized first so curly quotes extract at all;
# (2) paired delimiters — the opener's class must match the closer ('…' or
# "…", two alternative branches, not a shared ['"] class), preventing the
# "…' cross-matches the shared class allows. The letter-flanked internal-
# apostrophe rule is kept verbatim (it is what fixed "don't"/"Let's" from
# truncating at the apostrophe).
_PAYOFF_QUOTE_RE = re.compile(
    _SPEECH_VERBS + r".{0,40}?(?<![a-zA-Z])(?:"
    r"'((?:[^'\"]|(?<=[a-zA-Z])'(?=[a-zA-Z])){3,120}?)'(?![a-zA-Z])"
    r'|"([^"]{3,120}?)"'
    r")", re.I)

# curly/typographic -> ASCII, so LLM-written narrations ("don’t", “X”) match
# the straight-quoted OCR text. Ellipsis collapsed for the same reason.
_UNICODE_QUOTES = {
    ord("\u2019"): "'", ord("\u2018"): "'",       # ’ ‘
    ord("\u201c"): '"', ord("\u201d"): '"',       # “ ”
    ord("\u201e"): '"',                            # „
    ord("\u2026"): "...",                          # …
}


def payoff_quotes(narration):
    """Speech-verb-anchored quoted lines in a narration, Unicode-normalized.
    Each one promises that the panel carrying it appears on screen."""
    text = (narration or "").translate(_UNICODE_QUOTES)
    quotes = []
    for m in _PAYOFF_QUOTE_RE.findall(text):
        q = (m[0] or m[1]).strip()  # one group per delimiter branch
        if len(q) >= PAYOFF_MIN_QUOTE:
            quotes.append(q)
    return quotes


def _payoff_words(text):
    """Normalized word list for payoff scoring: Unicode-normalized, lowered,
    punctuation stripped except letter-internal apostrophes survive via the
    char class (matches the OCR's LET'S / DOESN'T forms)."""
    t = (text or "").translate(_UNICODE_QUOTES).lower()
    return re.sub(r"[^a-z0-9' ]", " ", t).split()


def _char_partial_ratio(quote_s, panel_s):
    """difflib analog of rapidfuzz partial_ratio: best char-level ratio of the
    quote against a quote-sized window slid over the (longer) panel text.
    Absorbs OCR character noise (C0NTINUE/THLS) that breaks word-level token
    equality. Only called in the ambiguous band, so pure-Python cost is fine."""
    if not quote_s or not panel_s:
        return 0.0
    if len(panel_s) <= len(quote_s):
        return difflib.SequenceMatcher(None, quote_s, panel_s,
                                       autojunk=False).ratio()
    window = len(quote_s) + 10
    step = max(1, len(quote_s) // 4)
    best = 0.0
    for i in range(0, len(panel_s) - len(quote_s) + 1, step):
        best = max(best, difflib.SequenceMatcher(
            None, quote_s, panel_s[i:i + window], autojunk=False).ratio())
    return best


def _anchor_words(text):
    """Clean word list for the v2 anchor check: Unicode-normalized, lowered,
    LETTER-INTERNAL apostrophes kept but quote-delimiter apostrophes dropped.
    _payoff_words keeps ALL apostrophes, which glues narration quote marks to
    their first/last word ("'what's", "yoonhwan'") and silently breaks word
    equality against the panel's unquoted OCR text — the exact reason the
    ch2 p0086 payoff carrier scored 0.69, under every threshold."""
    t = (text or "").translate(_UNICODE_QUOTES).lower()
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", t)


def anchored_run_len(narration, panel_text):
    """Char length of the longest verbatim word run shared by the narration
    and the panel's text (both anchor-normalized). This is the v2 fix-1 test
    for "the narration quotes this panel": word granularity (no mid-word
    fragments), order-sensitive, punctuation-insensitive."""
    a, b = _anchor_words(narration), _anchor_words(panel_text)
    if not a or not b:
        return 0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    match = m.find_longest_match(0, len(a), 0, len(b))
    return len(" ".join(a[match.a:match.a + match.size]))


def payoff_score(quote, panel_text):
    """Two-pass quote-in-panel score in [0, 1]. Word pass: fraction of the
    quote's words covered by matching blocks against the panel text (order-
    sensitive, unlike token_set-style matching — 'go Yoonhwan let's' must NOT
    pass). Char pass: partial-ratio fallback, only when the word pass lands in
    the ambiguous band (OCR character noise breaks word equality but not
    char-level alignment)."""
    q_words = _payoff_words(quote)
    p_words = _payoff_words(panel_text)
    if not q_words or not p_words:
        return 0.0
    sm = difflib.SequenceMatcher(None, q_words, p_words, autojunk=False)
    word_cov = sum(b.size for b in sm.get_matching_blocks()) / len(q_words)
    if 0.35 <= word_cov < PAYOFF_OK:
        word_cov = max(word_cov, _char_partial_ratio(
            " ".join(q_words), " ".join(p_words)))
    return word_cov


# Paraphrase prompt modeled on review_video.py's NARRATION_FIX_PROMPT (:342)
# minimal-rewrite style; the trigger differs (line not visible vs panel
# removed) so it gets its own copy here rather than importing review_video.
PAYOFF_FIX_PROMPT = """A manga-recap scene's narration quotes a line of dialogue, but that line is not visible on any panel shown in the video. Rewrite the narration minimally, converting ONLY that quoted line into indirect speech (describe what is said without quoting it). Keep everything else word-for-word identical — same voice, same length feel, same story beats.

QUOTED LINE NOT VISIBLE ON ANY PANEL:
{quote}

CURRENT NARRATION:
{narration}

Output ONLY the rewritten narration text, no quotes around it, no commentary."""


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
    def __init__(self, data, reads, script_path, use_vision=True, t_low=T_LOW,
                 use_payoff=False, dry_run=False, use_textonly=False):
        self.data = data
        self.script_path = Path(script_path).resolve()
        self.script_dir = Path(script_path).resolve().parent
        self.use_vision = use_vision
        self.t_low = t_low
        self.use_payoff = use_payoff
        self.use_textonly = use_textonly
        # (scene_i, panel_ref) suspects the tier-1.25 gate wants a vision
        # art-check on regardless of tier-2 scores (quoted bubbles score HIGH
        # lexically — the existing best<t_low trigger never fires on them).
        self.textonly_suspects = []
        self.gate = None
        self._confirm_calls = 0  # v2 single-image art-confirm budget counter
        self.dry_run = dry_run
        # refs appended by the payoff pass — tier-3 vision must not drop them
        # (a panel whose text the narration literally quotes is relevant by
        # definition; vision judging a text-heavy panel "irrelevant" would
        # silently undo the payoff rescue).
        self.payoff_appended = set()
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
                # exp-017 narrowing (--textonly-filter): the quote-exception
                # only sanctions DIALOGUE-carrying panels. A narration-box
                # card (no dialogue entries) quoted by the narrator is still
                # a black text card on screen — ch4 p0021/p0022 survived the
                # broad exception and drew HIGH irrelevant_panel flags.
                dlg = any(isinstance(d, dict) and d.get("text")
                          for d in r.get("dialogue") or [])
                if self.use_textonly and not dlg:
                    dropped.append(p)
                    self._note("drop", scene_i, p,
                               "tier1: textonly narration card (quote-"
                               "exception narrowed by --textonly-filter)")
                elif _has_verbatim_quote(narration, _dialogue_text(r)):
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

    # -- tier 1.25: text-dominant-fragment gate (exp-017, --textonly-filter) --

    def _gate_init(self):
        """Lazy-build the pixel gate over ALL picked panels (one batched
        DBNet pass, sidecar-cached). Any failure leaves self.gate=None and
        tier 1.25 becomes a no-op — fail open, never delete panels."""
        if self.gate is not None:
            return self.gate
        try:
            import textonly_gate
            g = textonly_gate.TextonlyGate(self.script_path)
            picked, seen = [], set()
            for sc in self.data.get("scenes", []):
                for p in sc.get("panels", []):
                    if p not in seen:
                        seen.add(p)
                        picked.append(p)
            g.prepare(picked)
            self.gate = g
        except Exception as e:
            self._note("warn", None, None,
                       f"tier1.25 gate init failed ({type(e).__name__}: {e});"
                       " textonly filter disabled for this run")
            self.gate = False
        return self.gate

    def _neighbor_kinds(self, panel_ref):
        """kind tags of the reads at +-1 reading-order positions. Used for the
        cover check WITHOUT trusting the panel's own tag: the OCR stream can
        be misaligned +-1 vs the images (ch4: the cover READ sits at p0074,
        the cover IMAGE is p0075), so a cover tag NEXT DOOR is the signal."""
        i = self._idx(panel_ref)
        if i < 0:
            return []
        names = sorted(self.order, key=self.order.get)
        return [self.reads[names[j]].get("kind") for j in (i - 1, i + 1)
                if 0 <= j < len(names)]

    def tier_textonly(self, scene_i, kept, dropped):
        """Route each tier-1 survivor through the pixel gate: high-confidence
        text cards drop here (substitution rescues emptied scenes as usual);
        grey-zone suspects are queued for a forced tier-3 vision art-check.
        Wrapped per-panel: an error keeps the panel (fail open)."""
        gate = self._gate_init()
        if not gate:
            return kept, dropped
        out = []
        for p in kept:
            try:
                verdict, reason = gate.classify(
                    p, self._read_for(p), self._neighbor_kinds(p))
            except Exception as e:
                self._note("warn", scene_i, p,
                           f"tier1.25 classify failed ({type(e).__name__}: "
                           f"{e}); panel kept")
                verdict, reason = "ok", ""
            if verdict == "drop":
                dropped.append(p)
                self._note("drop", scene_i, p, f"tier1.25: {reason}")
                continue
            if verdict == "suspect":
                self.textonly_suspects.append((scene_i, p))
                self._note("textonly_suspect", scene_i, p,
                           f"tier1.25: {reason} -> forced tier-3 art-check")
            out.append(p)
        return out, dropped

    def _drop_protection(self, scene_i, p, scenes_kept):
        """exp-017 v2 fix 1: reason string when the VISION leg must not drop
        this panel, else None. The golden-ch2 regression showed vision drops
        of narration-anchored panels re-open missing_payoff/static_scene
        faults (p0086 was scene 16's quoted payoff carrier; p0101 was scene
        17's final panel). Only the deterministic tier-1/1.25 legs may remove
        such panels — vision opinions (batched OR confirmed) never do.

        Protected classes:
          * payoff appends (v1 guard, kept);
          * the scene's FINAL kept panel (payoff/last-content carrier —
            dropping it stretches the remaining panels over the scene tail:
            ch2 scene 17 grew a 12 s static_scene exactly this way);
          * panels whose OCR text the narration quotes: a verbatim shared
            word run >= ANCHOR_QUOTE_MIN chars (anchor-normalized, so quote
            punctuation can't break word equality). The floor keeps
            single-name bubbles like ch2 p0132 "JAEHWAN..." droppable (7
            chars) while p0086 "WHAT'S THAT?" anchors at 11. Additionally,
            a speech-verb-anchored narration quote landing on the panel
            (payoff machinery; p0086/p0101 both payoff-score 1.00 in-scene)
            protects regardless of run length."""
        if (scene_i, p) in self.payoff_appended:
            return "panel is a payoff append"
        kept = scenes_kept[scene_i]
        if kept and kept[-1] == p:
            return "panel is the scene's final panel (last-content carrier)"
        narration = self.data.get("scenes", [])[scene_i].get("narration", "")
        ptext = _dialogue_text(self._read_for(p))
        run = anchored_run_len(narration, ptext)
        if run >= ANCHOR_QUOTE_MIN:
            return f"narration quotes the panel's text ({run}-char run)"
        for q in payoff_quotes(narration):
            if payoff_score(q, ptext) >= PAYOFF_OK:
                q_short = q if len(q) <= 40 else q[:37] + "..."
                return f'panel carries anchored quote "{q_short}"'
        return None

    def _confirm_no_art(self, p):
        """exp-017 v2 fix 2: single-image second opinion before any art-check
        drop. The v1 golden run dropped ch2 p0101 (molten-terrain art + bubble)
        because the BATCHED art-check misjudged it at position 3 of a 7-image
        batch — two single-image re-asks of the same rubric both said
        has_art=true. So a batched has_art=false is now only a PROPOSAL; the
        drop needs this full-resolution single-image confirmation to agree.

        Returns True only when the confirmation positively says no art.
        Any failure/parse issue/over-budget returns False = panel kept (fail
        open). Verdicts cache in <slug>.artconfirm.json keyed by panel mtime
        (same sidecar convention as the yolo109 cache), so re-runs are free.
        Cost bound: confirmations fire only for proposed drops that survived
        the protection checks (ch2 v1: 6 proposals/chapter), hard-capped at
        MAX_ART_CONFIRM single-image calls per run."""
        import gateway  # lazy, same as tier3
        name = Path(p).name
        img = (self.script_dir / p).resolve()
        try:
            mtime = img.stat().st_mtime
        except OSError:
            return False
        cache = {}
        try:
            d = json.loads(self._artconfirm_path().read_text())
            if isinstance(d, dict):
                cache = d
        except Exception:
            pass
        rec = cache.get(name)
        if rec and abs(rec.get("mtime", -1) - mtime) < 1e-6:
            return not bool(rec.get("has_art", True))
        if self._confirm_calls >= MAX_ART_CONFIRM:
            self._note("warn", None, p,
                       f"art-confirm budget ({MAX_ART_CONFIRM}) exhausted; "
                       "panel kept unconfirmed")
            return False
        self._confirm_calls += 1
        instr = (
            "Look carefully at this single manga panel. IGNORE what any text "
            "says. Does this panel contain meaningful story ART beyond text "
            "and sound effects — any drawn character, face, creature, "
            "object, or scene background (even partially visible behind or "
            "around the lettering)? Output ONLY a JSON object: "
            '{"has_art": true/false} — no prose, no markdown fence.')
        try:
            raw = gateway.llm_vision([img], instr)
            has_art = bool(_extract_json(raw, expect="object")
                           .get("has_art", True))
        except Exception as e:
            self._note("warn", None, p, f"art-confirm call failed: {e}; "
                                        "panel kept")
            return False
        cache[name] = {"mtime": mtime, "has_art": has_art}
        try:
            self._artconfirm_path().write_text(json.dumps(cache))
        except OSError:
            pass  # cache write failure only costs a future re-ask
        return not has_art

    def _artconfirm_path(self):
        return self.script_path.with_suffix(".artconfirm.json")

    def tier3_textonly(self, scenes_kept):
        """Forced vision art-check on tier-1.25 suspects, batched like tier3.
        The rubric question cannot be answered by narrating the text (the
        failure mode that mis-tagged these panels kind=story in the first
        place): does the image contain any DRAWN art besides lettering?
        Unanswered/failed panels stay kept (fail open). v2: a batched
        has_art=false is only a proposal — narration-anchored/final panels
        are exempt (_drop_protection) and everything else needs a
        single-image confirmation (_confirm_no_art) before removal."""
        import gateway  # lazy, same as tier3
        suspects = [(si, p) for si, p in self.textonly_suspects
                    if p in scenes_kept[si]]
        for start in range(0, len(suspects), VISION_SCENES_PER_CALL * 2):
            batch = suspects[start:start + VISION_SCENES_PER_CALL * 2]
            images, key_by_pos = [], []
            for si, p in batch:
                img = self.script_dir / p
                if not img.exists():
                    self._note("warn", si, p, "image missing; skip art-check")
                    continue
                images.append(img)
                key_by_pos.append((si, p))
            if not images:
                continue
            instr = (
                f"I show you {len(images)} manga panel images. For EACH image, "
                "IGNORE what any text says. Answer only: does the image contain "
                "any drawn character, face, creature, object, or scene "
                "background — anything besides lettering, speech bubbles, "
                "sound-effect typography, solid color fills, or title/credits "
                "design? Output ONLY a JSON array with exactly "
                f"{len(images)} objects, in image order: "
                '[{"panel": <1-based image number>, "has_art": true/false}] '
                "— no prose, no markdown fence.")
            print(f"  [verify] textonly art-check batch: {len(images)} panel(s)")
            try:
                raw = gateway.llm_vision(images, instr)
                arr = _extract_json(raw, expect="array")
            except Exception as e:  # keep panels on any failure
                self._note("warn", None, None,
                           f"tier3 art-check call failed: {e}")
                continue
            for k, (si, p) in enumerate(key_by_pos):
                item = arr[k] if k < len(arr) and isinstance(arr[k], dict) else {}
                if not bool(item.get("has_art", True)):
                    protect = self._drop_protection(si, p, scenes_kept)
                    if protect:
                        # v2 fix 1: vision may not remove narration-anchored
                        # panels — only the deterministic legs can (and R1's
                        # no-dialogue condition already excludes quoted
                        # bubbles). ch2 p0086 regression class.
                        self._note("keep", si, p,
                                   f"tier3 art-check rejected but {protect}; "
                                   "keeping (vision-drop protected)")
                        continue
                    if p not in scenes_kept[si]:
                        continue
                    # v2 fix 2: batched verdicts are proposals; confirm on the
                    # full-res single image before dropping. ch2 p0101
                    # regression class (batch-position false negative).
                    if self._confirm_no_art(p):
                        scenes_kept[si].remove(p)
                        self._note("drop", si, p,
                                   "tier3: no drawn art (batched verdict + "
                                   "single-image confirmation)")
                    else:
                        self._note("keep", si, p,
                                   "tier3: batched art-check proposed drop "
                                   "but single-image confirmation dissents; "
                                   "keeping")
                else:
                    self._note("keep", si, p,
                               "tier3: vision sees drawn art; suspect kept")

    # -- tier 1.5: quote payoff (exp-005, opt-in via --payoff) -----------------

    def _payoff_window(self, scene_i):
        """Reading-order bounds for payoff rescues, computed from the ORIGINAL
        script's scenes (deterministic regardless of what earlier passes kept).
        Forward bound is strict — next scene's min index — same no-spoiler
        rule as substitute(). Backward bound is the PREVIOUS scene's min index
        (not its max): panels inside the previous scene's range are already on
        screen by this point, so re-showing one is a reprise, not a spoiler —
        and that range is exactly where split-quote payoffs live (golden ch2
        sc21: the quote's panels sit inside scene 20's index span)."""
        scenes = self.data.get("scenes", [])
        lo, hi = -1, self.n_reads
        for j in range(scene_i - 1, -1, -1):
            idxs = [self._idx(p) for p in scenes[j].get("panels", [])
                    if self._idx(p) >= 0]
            if idxs:
                lo = min(idxs) - 1
                break
        for j in range(scene_i + 1, len(scenes)):
            idxs = [self._idx(p) for p in scenes[j].get("panels", [])
                    if self._idx(p) >= 0]
            if idxs:
                hi = min(idxs)
                break
        return lo, hi

    def _payoff_best_in_scene(self, quote, kept):
        """Best (score, panels) for the quote among the scene's own panels:
        singles plus adjacent-pair concats (a quote split across two speech
        bubbles fails any single-panel test — verified on ch3 sc14)."""
        best_score, best_panels, best_kind = 0.0, [], "single"
        texts = [_dialogue_text(self._read_for(p)) for p in kept]
        for p, t in zip(kept, texts):
            s = payoff_score(quote, t)
            if s > best_score:
                best_score, best_panels, best_kind = s, [p], "single"
        for k in range(len(kept) - 1):
            s = payoff_score(quote, texts[k] + " " + texts[k + 1])
            if s > best_score:
                best_score, best_panels = s, [kept[k], kept[k + 1]]
                best_kind = "pair"
        return best_score, best_panels, best_kind

    def _payoff_search_chapter(self, quote):
        """Best (score, [names], in_window) for the quote across ALL chapter
        reads — singles and reading-order-adjacent pairs. Returns both the
        chapter-wide best and whether it lies in this quote's rescue window
        (checked by the caller); cover/credits/endmatter never qualify."""
        names = sorted(self.order, key=self.order.get)
        ok = [n for n in names
              if self.reads[n].get("kind") not in HARD_DROP_KINDS]
        best_score, best_names = 0.0, []
        texts = {n: _dialogue_text(self.reads[n]) for n in ok}
        for n in ok:
            s = payoff_score(quote, texts[n])
            if s > best_score:
                best_score, best_names = s, [n]
        for k in range(len(ok) - 1):
            a, b = ok[k], ok[k + 1]
            if self.order[b] != self.order[a] + 1:
                continue  # pairs must be reading-order consecutive
            s = payoff_score(quote, texts[a] + " " + texts[b])
            if s > best_score:
                best_score, best_names = s, [a, b]
        return best_score, best_names

    def tier_payoff(self, scene_i, scene, kept):
        """Verify every speech-verb-anchored quote in the narration is visible
        on a kept panel; rescue misses by appending the quote-carrying
        panel(s) (append, never replace: existing panels already passed the
        earlier gates, and a panel whose text the narration quotes satisfies
        both the tier-1 textonly rule and tier-2 relevance by construction).
        Returns (kept, absent_quotes) — absent quotes are paraphrased per
        scene by the caller so it's one llm_text call max per scene."""
        absent = []
        for quote in payoff_quotes(scene.get("narration", "")):
            q_short = quote if len(quote) <= 60 else quote[:57] + "..."
            score, panels, kind = self._payoff_best_in_scene(quote, kept)
            if score >= PAYOFF_OK:
                action = "payoff_ok" if kind == "single" else "payoff_concat"
                self._note(action, scene_i,
                           "+".join(Path(p).name for p in panels),
                           f'quote on-screen ({score:.2f}): "{q_short}"',
                           score=round(score, 3))
                continue
            if score >= PAYOFF_ABSENT:
                # OCR-noisy maybe: the quote is probably there but stylized/
                # translated text degraded the read. No action — evidence for
                # threshold tuning in the next round.
                self._note("payoff_fuzzy", scene_i,
                           "+".join(Path(p).name for p in panels) or None,
                           f'quote maybe on-screen ({score:.2f}): "{q_short}"',
                           score=round(score, 3))
                continue
            # genuinely absent from the scene -> chapter-wide window search
            c_score, c_names = self._payoff_search_chapter(quote)
            lo, hi = self._payoff_window(scene_i)
            idxs = [self.order[n] for n in c_names]
            in_window = bool(idxs) and all(lo < i < hi for i in idxs)
            if c_score >= PAYOFF_OK and in_window:
                added = []
                for n in c_names:
                    ref = f"panels/{n}"
                    if ref not in kept:
                        kept.append(ref)
                        self.payoff_appended.add((scene_i, ref))
                        added.append(n)
                self._note("payoff_append", scene_i, "+".join(c_names),
                           f'quote found in window ({c_score:.2f}), appended '
                           f'{added or "(already kept)"}: "{q_short}"',
                           score=round(c_score, 3))
                continue
            # absent chapter-wide (or only out-of-window = spoiler guard):
            # queue for paraphrase.
            where = (f"best {c_score:.2f} at {'+'.join(c_names) or '-'} "
                     f"out-of-window {lo}..{hi}"
                     if c_score >= PAYOFF_OK else
                     f"chapter-wide best {c_score:.2f}")
            self._note("payoff_miss", scene_i, None,
                       f'quote absent from scene ({where}): "{q_short}"',
                       score=round(c_score, 3))
            absent.append(quote)
        return kept, absent

    def payoff_paraphrase(self, scene_i, scene, absent_quotes):
        """Rewrite the narration so it stops quoting lines no panel shows
        (indirect speech). Mirrors review_video._fix_narration: one llm_text
        call, length guard, and on ANY failure the narration is left alone —
        review_video.py still catches it post-render, so failing open here
        can never regress the default behavior."""
        if not self.use_vision:
            self._note("payoff_unresolved", scene_i, None,
                       "quote absent chapter-wide; paraphrase skipped "
                       "(--no-vision); review_video will catch post-render")
            return
        if self.dry_run:
            self._note("payoff_paraphrase", scene_i, None,
                       f"dry-run: would rewrite narration to paraphrase "
                       f"{len(absent_quotes)} quote(s)")
            return
        try:
            import gateway  # lazy, same as tier3
            new = gateway.llm_text(PAYOFF_FIX_PROMPT.format(
                quote="\n".join(f"- {q}" for q in absent_quotes),
                narration=scene.get("narration", ""))).strip()
            if new and len(new) > 40:
                scene["narration"] = new
                self._note("payoff_paraphrase", scene_i, None,
                           f"narration rewritten to paraphrase "
                           f"{len(absent_quotes)} absent quote(s)")
                return
            self._note("payoff_unresolved", scene_i, None,
                       "paraphrase rejected (too short); narration unchanged")
        except Exception as e:
            self._note("payoff_unresolved", scene_i, None,
                       f"paraphrase failed ({type(e).__name__}: {e}); "
                       "narration unchanged")

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
            if self.use_textonly:
                kept, dropped = self.tier_textonly(i, kept, dropped)
            if self.use_payoff:
                # tier 1.5 must never take the pipeline down: any unexpected
                # error logs and falls back to the pre-payoff kept list.
                try:
                    kept, absent = self.tier_payoff(i, sc, kept)
                    if absent:
                        self.payoff_paraphrase(i, sc, absent)
                except Exception as e:
                    self._note("warn", i, None,
                               f"tier1.5 payoff failed ({type(e).__name__}: "
                               f"{e}); scene left as tier-1 kept it")
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
                    if (scene_i, p) in self.payoff_appended:
                        # payoff appends carry quoted text — text relevance
                        # trumps a vision "not depicting the moment" verdict.
                        self._note("keep", scene_i, p,
                                   "tier3 rejected but panel carries a quoted "
                                   "line (payoff append); keeping")
                        continue
                    if self.use_textonly:
                        # exp-017 v2: the anchor guard covers EVERY vision
                        # drop while the flag is on, not just the art-check
                        # leg — the ch2 scene-16/17 regressions prove a
                        # vision opinion must not outrank narration evidence.
                        # Flag off: this branch never runs (byte-identical).
                        protect = self._drop_protection(scene_i, p,
                                                        scenes_kept)
                        if protect:
                            self._note("keep", scene_i, p,
                                       f"tier3 rejected but {protect}; "
                                       "keeping (vision-drop protected)")
                            continue
                    scenes_kept[scene_i].remove(p)
                    self._note("drop", scene_i, p, "tier3: vision judged irrelevant")
        elif ambiguous:
            for scene_i, _sc, _kept in ambiguous:
                self._note("skip-vision", scene_i, None,
                           "tier3 skipped (--no-vision); keeping tier-1 survivors")

        if self.textonly_suspects:
            if self.use_vision:
                try:
                    self.tier3_textonly(scenes_kept)
                except Exception as e:
                    self._note("warn", None, None,
                               f"tier3 art-check failed ({type(e).__name__}: "
                               f"{e}); suspects kept")
            else:
                for scene_i, p in self.textonly_suspects:
                    self._note("skip-vision", scene_i, p,
                               "textonly suspect kept (--no-vision)")

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
           t_low=T_LOW, use_payoff=False, use_textonly=False):
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

    v = Verifier(data, reads, script_path, use_vision=use_vision, t_low=t_low,
                 use_payoff=use_payoff, dry_run=dry_run,
                 use_textonly=use_textonly)
    changed = v.run()

    n_drop = sum(1 for e in v.log if e["action"] == "drop")
    n_sub = sum(1 for e in v.log if e["action"] == "substitute")
    print(f"  [verify] {changed} scene(s) changed "
          f"({n_drop} drops, {n_sub} substitutions)")
    if use_payoff:
        counts = {}
        for e in v.log:
            if e["action"].startswith("payoff"):
                counts[e["action"]] = counts.get(e["action"], 0) + 1
        summary = ", ".join(f"{k}={n}" for k, n in sorted(counts.items())) \
            or "no anchored quotes found"
        print(f"  [verify] payoff: {summary}")

    if dry_run:
        print("  [verify] dry-run: no files written")
        return script_path

    params = {"t_low": t_low, "vision": use_vision}
    if use_payoff:
        # key added only when the pass ran, so payoff-off output stays
        # byte-identical to the pre-exp-005 verifier.
        params["payoff"] = {"ok": PAYOFF_OK, "absent": PAYOFF_ABSENT}
    if use_textonly:
        # same convention: recorded only when the tier ran.
        import textonly_gate
        params["textonly"] = {"cov_min": textonly_gate.COV_MIN,
                              "ink_max": textonly_gate.INK_MAX,
                              "flat_min": textonly_gate.FLAT_MIN,
                              "frag_h": textonly_gate.FRAG_H,
                              "art_conf": textonly_gate.ART_CONF,
                              # v2: anchor guard + single-image confirmation
                              "anchor_min": ANCHOR_QUOTE_MIN,
                              "confirm_cap": MAX_ART_CONFIRM}
    data["_verify"] = {"verify_log": v.log, "params": params}
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
    ap.add_argument("--payoff", action="store_true",
                    default=os.environ.get("MANGA_VERIFY_PAYOFF") == "1",
                    help="enable tier-1.5 quote-payoff pass (exp-005; also "
                         "via env MANGA_VERIFY_PAYOFF=1; module default off — "
                         "manga.py passes this by default, opt out there with "
                         "--no-payoff-check)")
    ap.add_argument("--textonly-filter", action="store_true",
                    default=os.environ.get("MANGA_TEXTONLY_FILTER") == "1",
                    help="enable tier-1.25 text-dominant-fragment gate "
                         "(exp-017; also via env MANGA_TEXTONLY_FILTER=1; "
                         "opt-in, default off — output is byte-identical "
                         "when disabled)")
    args = ap.parse_args(argv)
    verify(args.script, ocr_path=args.ocr, dry_run=args.dry_run,
           use_vision=not args.no_vision, t_low=args.t_low,
           use_payoff=args.payoff, use_textonly=args.textonly_filter)


if __name__ == "__main__":
    main()
