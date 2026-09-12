#!/usr/bin/env python3
"""exp-004 v2: remap the OCR sidecar (keyed by panel filename) from the v1
bench 118-panel names to the v2 115-panel names, using the same strip-y
overlap mapping as remap_panels_v2.py. Merged old panels (2->1) get their
dialogue/sfx concatenated so smart-layout text guards still fire."""
import json, collections

OLD_IDX = "/tmp/opencode/exp004_bench/proj/panels/panels_index.json"
NEW_IDX = "/tmp/opencode/exp004_bench_v2/proj/panels/panels_index.json"
OCR_IN = "/tmp/opencode/exp004_bench/proj/the-world-after-the-fall-ch2.ocr.json"
OCR_OUT = "/tmp/opencode/exp004_bench_v2/proj/the-world-after-the-fall-ch2.ocr.json"

old = json.load(open(OLD_IDX))
new = json.load(open(NEW_IDX))

def interval(e):
    x, y, w, h = e["bbox"]
    return (y, y + h)

new_strip = [e for e in new if e.get("page") == "strip"]
mapping = {}
for oe in old:
    if oe.get("page") != "strip":
        cands = [ne for ne in new if ne.get("page") == oe.get("page")]
        mapping[oe["panel"]] = cands[0]["panel"]
        continue
    oy0, oy1 = interval(oe)
    best, bestov = None, -1
    for ne in new_strip:
        ny0, ny1 = interval(ne)
        ov = min(oy1, ny1) - max(oy0, ny0)
        if ov > bestov:
            bestov, best = ov, ne
    mapping[oe["panel"]] = best["panel"]

ocr = json.load(open(OCR_IN))
by_new = collections.defaultdict(list)
for r in ocr:
    by_new[mapping[r["panel"]]].append(r)

out = []
for ne in new:
    name = ne["panel"]
    rs = by_new.get(name)
    if not rs:
        print(f"  ! new panel {name} has no OCR source (unmapped-to); "
              f"emitting empty read")
        out.append({"panel": name, "dialogue": [], "narration_text": "",
                    "sfx": "", "scene_beat": "", "kind": "art",
                    "quality": "ok"})
        continue
    if len(rs) == 1:
        r = dict(rs[0]); r["panel"] = name
        out.append(r)
    else:
        merged = dict(rs[0]); merged["panel"] = name
        merged["dialogue"] = [d for r in rs for d in r.get("dialogue", [])]
        merged["narration_text"] = " ".join(
            t for r in rs if (t := r.get("narration_text", "").strip()))
        merged["sfx"] = " ".join(
            t for r in rs if (t := r.get("sfx", "").strip()))
        merged["scene_beat"] = " / ".join(
            t for r in rs if (t := r.get("scene_beat", "").strip()))
        print(f"  merged OCR {[r['panel'] for r in rs]} -> {name}")
        out.append(merged)

json.dump(out, open(OCR_OUT, "w"), indent=1, ensure_ascii=False)
print(f"wrote {OCR_OUT}: {len(out)} reads for {len(new)} panels")
