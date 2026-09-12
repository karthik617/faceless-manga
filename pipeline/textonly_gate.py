#!/usr/bin/env python3
"""textonly_gate.py — tier-1.25 text-dominant-fragment signals (gap-017, exp-017).

The gap-001 verifier's tier 1 trusts the OCR reads' `kind` tag, and that tag
is wrong for cutout fragments: the vision OCR narrates what a bubble SAYS and
files it as kind=story (9/11 ch4 offenders — p0011 "A close-up of text in a
speech bubble" landed story/ok). This module computes deterministic per-panel
pixel + read signals so verify_panels.py (--textonly-filter) can route
text-dominant fragments into a narrowed textonly rule / a forced tier-3
vision check WITHOUT trusting the kind tag.

Signals (picked panels only, sidecar-cached):
  text_cov — DBNet line-box union area (dilated TEXT_DILATE px to eat bubble
             outlines) / panel area, via the already-vendored text_boxes.py
             detector and its <slug>.textboxes.json sidecar (gap-009 dep —
             a chapter that already rendered has this cache paid for).
  ink_out  — Otsu ink outside the dilated text boxes / panel area (pure cv2):
             on the ch4 labeled set bubble cutouts land <= 0.03 while kept
             story art never dips below 0.16 — the widest margin of any
             single signal, which is why R1 keys on it.
  flat     — fraction of pixels within +-12 gray levels of the modal value:
             near-solid cards (black narration boxes, white bubble fill).
  has_art  — manga109_yolo body/face detection (lazy: only panels some rule
             already suspects pay the ~0.5 s), cached in <slug>.yolo109.json.
             A panel with a drawn character is ART — never dropped here.
             Same AGPL-recorded checkpoint + usage mode as gap-004; the only
             change is reading the body/face classes the model always emitted.

Rules (any hit -> art veto check -> verdict):
  R1 text-dominant: (text_cov >= COV_MIN or flat >= FLAT_MIN) and
     ink_out <= INK_MAX. With dialogue -> suspect (a character speaks the
     text; the human keeps some of these, so only vision may drop);
     without dialogue -> drop (pure text card, high confidence).
  R2 tiny-text fragment: height <= FRAG_H px strip with >= 1 text line and
     the read's ENTIRE text (dialogue+narration+sfx) <= FRAG_TEXT_MAX chars
     -> suspect. Catches SFX cards like "THEN!" whose burst ring defeats the
     ink test. The text-length cap is what keeps real dialogue keeps
     (h=414 "THIS IS STILL A BIT OVERWHELMING...!") out.
  R3 beat says text: the OCR's own scene_beat literally describes lettering
     ("speech bubble", "caption", "Korean characters", ...) -> suspect. The
     kind tag lies but the free-text beat usually confesses; this is the only
     signal that reaches SFX glyphs DBNet can't box (ch4 p0095).
  S4 cover neighbor: a read within +-1 reading-order position is tagged
     cover/credits/endmatter -> suspect. Handles the OCR<->image +-1
     misalignment (ch4 p0075: the cover READ sits at p0074) without trusting
     the panel's own possibly-shifted tags. Position-in-chapter is NOT used:
     ch4's cover card sits mid-file (webtoon cold-open convention).

Verdicts: "ok" (no rule fired / art veto), "suspect" (forced tier-3 vision),
"drop" (high confidence). Everything fails open: any per-panel error returns
"ok" — an inference failure must never delete panels (mirrors
verify_panels' missing-OCR behavior).

Thresholds tuned NEGATIVES-FIRST on the ch4 labeled set (11 drop actions /
12 panel ids vs the 0-fault final script's keeps): see
improvements/experiments/exp-017-textonly-panel-filter/IMPLEMENTATION.md.

CLI (report-only, for eval/debugging — never writes a script):
    ./venv/bin/python3 pipeline/textonly_gate.py --script output/<slug>/<slug>.json \
        [--ocr <path>] [--panels <name.png> ...]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Thresholds (ch4 sweep, negatives-first) ------------------------------------
COV_MIN = 0.10        # R1: dilated text boxes cover >= this of the panel
INK_MAX = 0.05        # R1: ink outside text <= this (POS <= 0.025, NEG >= 0.16
                      # on ch4 apart from the two quote-kept bubble negatives,
                      # which the suspect path + tier-3 protects)
FLAT_MIN = 0.85       # R1 alt: near-solid card (mode +-12 gray levels)
FRAG_H = 450          # R2: strip fragments (ch4 offenders are 243-439 px)
FRAG_TEXT_MAX = 20    # R2: total OCR chars; "THEN!" = 5, real dialogue > 20
ART_CONF = 0.15       # body/face veto floor. NOT lower: manga109-nano hits a
                      # spurious body@0.11 on the p0096 SFX panel; every real
                      # ch4 art panel that needs the veto scores >= 0.15.
TEXT_DILATE = 17      # px square kernel: text boxes eat their bubble outline
                      # before the ink-outside measurement (research §C1.2)
FLAT_BAND = 12        # +- gray levels around the mode for the flat check
BEAT_RE = re.compile(
    r"(?:speech|text)\s*bubbles?|captions?\b|korean characters?|"
    r"title card|text[ -]?panels?|black panel|"
    # SFX lettering DBNet can't box (stylized glyphs): the beat confesses
    # with "sound effect text/characters" or "forms characters" phrasing.
    r"sound[- ]?effect\s+(?:text|characters|lettering)|"
    r"form(?:s|ing)\s+(?:korean\s+)?characters", re.I)
COVER_KINDS = {"cover", "credits", "endmatter"}   # = verify_panels.HARD_DROP_KINDS


def _read_text_len(read):
    """Length of ALL text the OCR saw on the panel (dialogue+narration+sfx)."""
    parts = [d.get("text", "") for d in read.get("dialogue") or []
             if isinstance(d, dict)]
    parts += [read.get("narration_text") or "", read.get("sfx") or ""]
    return len(" ".join(p for p in parts if p).strip())


def _has_dialogue(read):
    return any(isinstance(d, dict) and d.get("text")
               for d in read.get("dialogue") or [])


class TextonlyGate:
    """Signal computer + classifier for one chapter's picked panels.

    panels are addressed by script-relative refs ('panels/p0011.png'); the
    gate resolves them against script_dir and caches everything in sidecars
    next to the script (same convention as the OCR / textboxes caches)."""

    def __init__(self, script_path):
        self.script_dir = Path(script_path).resolve().parent
        base = Path(script_path).resolve()
        self.tb_sidecar = base.with_suffix(".textboxes.json")
        self.yolo_sidecar = base.with_suffix(".yolo109.json")
        self._boxes = {}       # name -> [[x,y,w,h],...]
        self._signals = {}     # name -> dict | None
        self._art = {}         # name -> bool
        self._yolo_cache = None

    # -- DBNet text boxes (batched once over all picked panels) --------------

    def prepare(self, panel_refs):
        """Run/refresh the DBNet sidecar for every picked panel up front so
        classify() is pure cv2 afterwards. Errors degrade to no boxes for the
        whole chapter (each classify then fails open on missing signals)."""
        import text_boxes
        paths = []
        seen = set()
        for ref in panel_refs:
            p = (self.script_dir / ref).resolve()
            if p.name not in seen and p.exists():
                seen.add(p.name)
                paths.append(p)
        try:
            self._boxes = text_boxes.ensure_boxes(self.tb_sidecar, paths)
        except Exception as e:
            print(f"  [textonly] text-box detection failed "
                  f"({type(e).__name__}: {e}); gate degrades to OCR-tag "
                  f"signals only")
            self._boxes = {}

    # -- pure-cv2 pixel signals ----------------------------------------------

    def signals(self, ref):
        """Pixel signals for one panel ref, or None (fail open upstream)."""
        name = Path(ref).name
        if name in self._signals:
            return self._signals[name]
        s = None
        try:
            import cv2
            import numpy as np
            img = cv2.imread(str(self.script_dir / ref))
            if img is not None:
                h, w = img.shape[:2]
                area = w * h
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                boxes = self._boxes.get(name, [])
                mask = np.zeros((h, w), np.uint8)
                for x, y, bw, bh in boxes:
                    cv2.rectangle(mask, (x, y), (x + bw, y + bh), 255, -1)
                if boxes:
                    mask = cv2.dilate(mask, cv2.getStructuringElement(
                        cv2.MORPH_RECT, (TEXT_DILATE, TEXT_DILATE)))
                # Otsu split; the MINORITY class is the ink (webtoon panels
                # are majority background whichever polarity the art uses).
                _, th = cv2.threshold(cv2.medianBlur(gray, 3), 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                ink = th if cv2.countNonZero(th) < area / 2 \
                    else cv2.bitwise_not(th)
                outside = cv2.bitwise_and(ink, cv2.bitwise_not(mask))
                hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
                mode = int(hist.argmax())
                flat = float(hist[max(0, mode - FLAT_BAND):
                                  mode + FLAT_BAND + 1].sum()) / area
                s = {"w": w, "h": h, "n_boxes": len(boxes),
                     "text_cov": cv2.countNonZero(mask) / area,
                     "ink_out": cv2.countNonZero(outside) / area,
                     "flat": flat}
        except Exception as e:
            print(f"  [textonly] signals failed on {name} "
                  f"({type(e).__name__}: {e}); panel passes")
        self._signals[name] = s
        return s

    # -- manga109 body/face art veto (lazy: suspects only) --------------------

    def _load_yolo_cache(self):
        if self._yolo_cache is None:
            try:
                d = json.loads(self.yolo_sidecar.read_text())
                self._yolo_cache = d if isinstance(d, dict) and "panels" in d \
                    else {"panels": {}}
            except Exception:
                self._yolo_cache = {"panels": {}}
        return self._yolo_cache

    def has_art(self, ref):
        """True when manga109_yolo sees a body/face >= ART_CONF anywhere on
        the panel. On ANY failure returns True — "has art" vetoes the gate,
        so failing toward True keeps the panel (fail open)."""
        name = Path(ref).name
        if name in self._art:
            return self._art[name]
        result = True
        try:
            path = (self.script_dir / ref).resolve()
            cache = self._load_yolo_cache()
            mtime = path.stat().st_mtime
            rec = cache["panels"].get(name)
            if rec and abs(rec.get("mtime", -1) - mtime) < 1e-6:
                dets = rec["dets"]
            else:
                import yolo_detect
                # conf floor below ART_CONF so the cache stays reusable if
                # the veto threshold moves during tuning.
                dets = yolo_detect.detect_objects([path], conf=0.10)[0]
                cache["panels"][name] = {"mtime": mtime, "dets": dets}
                self.yolo_sidecar.write_text(json.dumps(cache))
            result = any(lbl in ("body", "face") and c >= ART_CONF
                         for lbl, c, *_ in dets)
        except Exception as e:
            print(f"  [textonly] art check failed on {name} "
                  f"({type(e).__name__}: {e}); treating as art (panel kept)")
        self._art[name] = result
        return result

    # -- classification --------------------------------------------------------

    def classify(self, ref, read, neighbor_kinds=()):
        """('ok'|'suspect'|'drop', reason) for one picked panel. Caller wraps
        in try/except and treats any raise as ok — but this method itself
        already degrades per-signal."""
        s = self.signals(ref)
        beat = read.get("scene_beat") or ""
        hits = []
        if s is not None:
            if (s["text_cov"] >= COV_MIN or s["flat"] >= FLAT_MIN) \
                    and s["ink_out"] <= INK_MAX:
                hits.append(("R1", f"text-dominant cov={s['text_cov']:.2f} "
                                   f"ink_out={s['ink_out']:.3f} "
                                   f"flat={s['flat']:.2f}"))
            tlen = _read_text_len(read)
            if s["h"] <= FRAG_H and s["n_boxes"] >= 1 \
                    and 0 < tlen <= FRAG_TEXT_MAX:
                hits.append(("R2", f"tiny-text fragment h={s['h']} "
                                   f"chars={tlen}"))
        m = BEAT_RE.search(beat)
        if m:
            hits.append(("R3", f'beat says text ("{m.group(0)}")'))
        if any(k in COVER_KINDS for k in neighbor_kinds):
            hits.append(("S4", "cover/credits read within +-1 position"))
        if not hits:
            return "ok", ""
        if self.has_art(ref):
            return "ok", (f"art veto (body/face >= {ART_CONF}) over "
                          + "+".join(h[0] for h in hits))
        reason = "; ".join(f"{r}: {d}" for r, d in hits)
        # Deterministic drop ONLY at high confidence: a text-dominant panel
        # carrying no character speech is a pure card. Anything with dialogue
        # might be a deliberately-kept bubble (ch4 keeps p0047/p0084) —
        # vision decides those.
        if hits[0][0] == "R1" and not _has_dialogue(read):
            return "drop", reason
        return "suspect", reason


# ---------------------------------------------------------------------------
# report-only CLI (used by the exp-017 eval; never writes a script)
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", required=True,
                    help="output/<slug>/<slug>.json — panels are classified "
                         "from its scenes; nothing is written to it")
    ap.add_argument("--ocr", default=None,
                    help="<slug>.ocr.json (default: next to the script)")
    ap.add_argument("--panels", nargs="*", default=None,
                    help="restrict to these panel filenames")
    args = ap.parse_args()
    script = Path(args.script)
    data = json.loads(script.read_text())
    ocr = Path(args.ocr) if args.ocr else script.with_suffix(".ocr.json")
    reads = json.loads(ocr.read_text())
    byname = {r.get("panel"): r for r in reads}
    order = {r.get("panel"): i for i, r in enumerate(reads)}
    names = [r.get("panel") for r in reads]

    picked, seen = [], set()
    for sc in data.get("scenes", []):
        for p in sc.get("panels", []):
            if p not in seen:
                seen.add(p)
                picked.append(p)
    if args.panels:
        keep = set(args.panels)
        picked = [p for p in picked if Path(p).name in keep]

    gate = TextonlyGate(script)
    gate.prepare(picked)
    counts = {"ok": 0, "suspect": 0, "drop": 0}
    for ref in picked:
        name = Path(ref).name
        read = byname.get(name, {})
        i = order.get(name, -1)
        nk = [byname[names[j]].get("kind") for j in (i - 1, i + 1)
              if 0 <= j < len(names)] if i >= 0 else []
        verdict, reason = gate.classify(ref, read, nk)
        counts[verdict] += 1
        if verdict != "ok" or reason:
            print(f"{verdict:<8} {name}  {reason}")
    print(f"-- {len(picked)} panel(s): {counts['ok']} ok, "
          f"{counts['suspect']} suspect, {counts['drop']} drop")


if __name__ == "__main__":
    main()
