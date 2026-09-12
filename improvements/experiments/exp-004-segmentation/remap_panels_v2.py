#!/usr/bin/env python3
"""exp-004 v2 re-benchmark: remap bench script panel refs old(118)->new(115)
by strip-y overlap. Rule: each old panel maps to (a) the new panel with max
overlap, PLUS (b) any new panel whose own height is >=50% contained in the
old panel (coverage expansion — a relocated cut can split an old panel's
content across two new panels, e.g. old p0061 -> new p0058+p0059).
Scenes must NOT be re-scripted (clean A/B) — only panel refs change."""
import json, os, collections

OLD_IDX = "/tmp/opencode/exp004_bench/proj/panels/panels_index.json"
NEW_IDX = "/tmp/opencode/exp004_bench_v2/proj/panels/panels_index.json"
SCRIPT_IN = "/tmp/opencode/exp004_bench/proj/the-world-after-the-fall-ch2.json"
SCRIPT_OUT = "/tmp/opencode/exp004_bench_v2/proj/the-world-after-the-fall-ch2.json"

old = json.load(open(OLD_IDX))
new = json.load(open(NEW_IDX))

def iv(e):
    x, y, w, h = e["bbox"]
    return (y, y + h)

new_strip = sorted((e for e in new if e.get("page") == "strip"),
                   key=lambda e: e["bbox"][1])

mapping = {}  # old panel name -> ordered list of new panel names
for oe in old:
    if oe.get("page") != "strip":
        cands = [ne for ne in new if ne.get("page") == oe.get("page")]
        assert len(cands) == 1, (oe, cands)
        mapping[oe["panel"]] = [cands[0]["panel"]]
        continue
    oy0, oy1 = iv(oe)
    best, bestov = None, -1
    incl = []
    for ne in new_strip:
        ny0, ny1 = iv(ne)
        ov = min(oy1, ny1) - max(oy0, ny0)
        if ov > bestov:
            bestov, best = ov, ne
        if ov >= 0.5 * (ny1 - ny0):
            incl.append(ne)
    assert bestov > 0, ("no overlap for", oe)
    if best not in incl:
        incl.append(best)
    incl.sort(key=lambda e: e["bbox"][1])
    mapping[oe["panel"]] = [e["panel"] for e in incl]
    if len(incl) > 1:
        print(f"  expansion {oe['panel']} -> {[e['panel'] for e in incl]}")

inv = collections.defaultdict(list)
for o, ns in mapping.items():
    for n in ns:
        inv[n].append(o)
merged = {n: os_ for n, os_ in inv.items() if len(os_) > 1}
print("new panels fed by multiple old panels (merges):")
for n, os_ in sorted(merged.items()):
    print(f"  {sorted(os_)} -> {n}")
used_new = {n for ns in mapping.values() for n in ns}
print("new panels not mapped-to:", [e["panel"] for e in new
                                    if e["panel"] not in used_new])

script = json.load(open(SCRIPT_IN))
changed_scenes = []
for i, sc in enumerate(script["scenes"]):
    old_panels = list(sc.get("panels", []))
    new_panels = []
    for p in old_panels:
        base = p.split("/")[-1]
        for nb in mapping[base]:
            new_panels.append(p.replace(base, nb))
    seen, dedup = set(), []
    for p in new_panels:
        if p not in seen:
            seen.add(p); dedup.append(p)
    if dedup != old_panels:
        changed_scenes.append((i, old_panels, dedup))
    sc["panels"] = dedup
    assert len(dedup) >= 1, f"scene {i} lost all panels"

if "thumbnails" in script:
    single = {o: ns[0] for o, ns in mapping.items()}
    def remap_str(s):
        for o, n in sorted(single.items(), reverse=True):
            s = s.replace(o, n)
        return s
    script["thumbnails"] = json.loads(remap_str(json.dumps(script["thumbnails"])))

print(f"\nscenes changed: {len(changed_scenes)} / {len(script['scenes'])}")
for i, o, n in changed_scenes:
    print(f"  scene {i}: {[p.split('/')[-1] for p in o]} -> "
          f"{[p.split('/')[-1] for p in n]}")

pdir = "/tmp/opencode/exp004_bench_v2/proj/panels"
for sc in script["scenes"]:
    for p in sc["panels"]:
        assert os.path.exists(os.path.join(pdir, p.split("/")[-1])), p

json.dump(script, open(SCRIPT_OUT, "w"), indent=1, ensure_ascii=False)
print("\nwrote", SCRIPT_OUT)
