#!/usr/bin/env python3
"""channel.py — channel continuity / series roster.

A YouTube recap channel dies if it topic-hops. Raw trend discovery
(research_trends.py) picks whatever is hottest RIGHT NOW, which reorders every
run — great for a views-farm, fatal for a subscriber channel. This module adds
the missing memory so the channel behaves like a channel:

  CHANNEL MODE = single-series focus
    * commit to ONE series and recap it chapter 1 -> latest, IN ORDER
    * "what do I make next?" is always "the next unrecapped chapter"
    * only when that series is FINISHED and fully recapped do we auto-promote
      the top-scoring trending candidate to become the new current series

State lives in channel_state.json at the repo root. It is the single source of
truth for what the channel is and what it has already posted.

CLI:
    ./venv/bin/python3 pipeline/channel.py status
    ./venv/bin/python3 pipeline/channel.py set --title "Absolute Regression"
    ./venv/bin/python3 pipeline/channel.py next            # human view
    ./venv/bin/python3 pipeline/channel.py next --json     # for manga.py --auto
    ./venv/bin/python3 pipeline/channel.py done --chapter 12
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = ROOT / "channel_state.json"
ANILIST_URL = "https://graphql.anilist.co"

_SEARCH_QUERY = """
query ($search: String) {
  Media(type: MANGA, search: $search, sort: SEARCH_MATCH) {
    id
    title { romaji english native }
    status
    chapters
    countryOfOrigin
    genres
    averageScore
    siteUrl
  }
}
"""


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _default_state() -> dict:
    return {
        "channel": "PanelBreak",
        "mode": "single-series",
        "current": None,          # {title, source_id, country, status, total_chapters, ...}
        "done_chapters": {},      # {series_title: [1,2,3,...]}
        "retired": [],            # fully-recapped/finished series (never re-picked)
        "skip": [],               # series you never want (manual blocklist)
    }


def load_state() -> dict:
    if STATE.exists():
        try:
            s = json.loads(STATE.read_text())
            for k, v in _default_state().items():
                s.setdefault(k, v)
            return s
        except Exception as e:
            print(f"  [warn] channel_state.json unreadable ({e}); starting fresh",
                  file=sys.stderr)
    return _default_state()


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _slug(series: str, chapter) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(series).lower()).strip("-")
    base = re.sub(r"-+", "-", base) or "manga-recap"
    return f"{base}-ch{chapter}"


def _chapters_done(state: dict, title: str) -> list:
    return sorted(state["done_chapters"].get(title, []),
                  key=lambda x: float(x) if str(x).replace(".", "", 1).isdigit()
                  else 0)


def _next_chapter(done: list) -> int:
    nums = [float(x) for x in done
            if str(x).replace(".", "", 1).replace("-", "").isdigit()]
    return int(max(nums)) + 1 if nums else 1


def _anilist_lookup(title: str) -> dict | None:
    try:
        r = requests.post(ANILIST_URL, timeout=30,
                          headers={"Content-Type": "application/json"},
                          json={"query": _SEARCH_QUERY,
                                "variables": {"search": title}})
        r.raise_for_status()
        m = r.json()["data"]["Media"]
    except Exception as e:
        print(f"  [warn] AniList lookup failed for '{title}': {e}", file=sys.stderr)
        return None
    if not m:
        return None
    t = m["title"]
    return {
        "title": t.get("english") or t.get("romaji") or title,
        "title_alt": t.get("romaji") or t.get("native") or "",
        "source_id": m.get("id"),
        "status": m.get("status") or "",
        "total_chapters": m.get("chapters"),   # often null while ongoing
        "country": m.get("countryOfOrigin") or "",
        "genres": m.get("genres") or [],
        "score": m.get("averageScore") or 0,
        "site_url": m.get("siteUrl") or "",
    }


def _is_exhausted(current: dict, done: list) -> bool:
    """A series is exhausted only when FINISHED and every chapter is recapped.
    An ongoing (RELEASING) series is NEVER exhausted — you wait for new chapters
    rather than switch away (switching is what loses the audience)."""
    total = current.get("total_chapters")
    status = (current.get("status") or "").upper()
    if status == "FINISHED" and total:
        return _next_chapter(done) > int(total)
    return False


# --------------------------------------------------------------------------- #
# Core: decide the next video
# --------------------------------------------------------------------------- #
def plan_next(state: dict, auto_add: bool = True) -> dict:
    """Return the next video to make, as a dict:
        {series, chapter, slug, is_new_series, caught_up, status, reason}
    Mutates `state` only in memory (caller saves if it acts on it)."""
    cur = state.get("current")

    # 1) an ongoing series we're committed to → next chapter (unless caught up)
    if cur:
        done = _chapters_done(state, cur["title"])
        if _is_exhausted(cur, done):
            state.setdefault("retired", []).append(cur["title"])
            state["current"] = None
            cur = None
        else:
            nxt = _next_chapter(done)
            total = cur.get("total_chapters")
            # ongoing + caught up to latest KNOWN total → wait, don't switch
            caught = bool(total and nxt > int(total))
            return {
                "series": cur["title"],
                "series_alt": cur.get("title_alt", ""),
                "chapter": nxt,
                "slug": _slug(cur["title"], nxt),
                "is_new_series": False,
                "caught_up": caught,
                "status": cur.get("status", ""),
                "reason": (f"caught up to latest known chapter ({total}); "
                           "waiting for a new chapter to drop"
                           if caught else
                           f"next chapter of the current series "
                           f"({len(done)} recapped so far)"),
            }

    # 2) no current series → promote the top trending candidate we haven't done
    if not auto_add:
        return {"series": None, "reason": "no current series set; run "
                "`channel set --title \"...\"` to commit one"}

    sys.path.insert(0, str(HERE))
    import research_trends as rt
    # only auto-commit to a series we can actually DOWNLOAD from chapter 1 —
    # a hot title whose back-catalog is licensed away is useless for a channel.
    ranked = rt.discover(limit=30, fetchable=True, probe_top=15)
    seen = set(state["done_chapters"]) | set(state["retired"]) | set(state["skip"])
    for c in ranked:
        if c.title in seen:
            continue
        if not c.starts_at_one:      # must be binge-able in order
            continue
        newcur = {
            "title": c.title, "title_alt": c.title_alt, "source_id": c.source_id,
            "status": c.status, "total_chapters": c.chapters,
            "country": c.country, "genres": c.genres, "score": c.score,
            "site_url": c.site_url, "video_score": c.video_score,
            "fetchable_chapters": c.fetchable_chapters,
        }
        state["current"] = newcur
        return {
            "series": c.title, "series_alt": c.title_alt, "chapter": 1,
            "slug": _slug(c.title, 1), "is_new_series": True, "caught_up": False,
            "status": c.status,
            "reason": (f"previous series exhausted; promoted top binge-able "
                       f"candidate ({c.fetchable_chapters} EN ch from ch1, "
                       f"video_score {c.video_score:.3f})"),
        }
    return {"series": None,
            "reason": "no binge-able (downloadable-from-ch1) series found in "
                      "current trends — try again later or set one manually"}


def mark_done(state: dict, series: str, chapter) -> None:
    lst = state["done_chapters"].setdefault(series, [])
    try:
        chapter = int(chapter) if float(chapter).is_integer() else float(chapter)
    except (TypeError, ValueError):
        pass
    if chapter not in lst:
        lst.append(chapter)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_status(args) -> int:
    s = load_state()
    print(f"Channel: {s['channel']}  (mode: {s['mode']})")
    cur = s.get("current")
    if cur:
        done = _chapters_done(s, cur["title"])
        total = cur.get("total_chapters") or "?"
        flag = {"JP": "manga", "KR": "manhwa", "CN": "manhua"}.get(
            cur.get("country"), cur.get("country", ""))
        print(f"\nCURRENT: {cur['title']}  [{cur.get('status','?')}, {flag}]")
        print(f"  recapped: {len(done)} chapter(s)"
              + (f" — {done}" if done else "")
              + f"   (total: {total})")
        print(f"  next up : chapter {_next_chapter(done)}")
    else:
        print("\nCURRENT: (none set) — next run will pick a trending series")
    if s["retired"]:
        print(f"\nretired (done): {', '.join(s['retired'])}")
    if s["skip"]:
        print(f"blocklist: {', '.join(s['skip'])}")
    print(f"\nstate file: {STATE}")
    return 0


def cmd_set(args) -> int:
    s = load_state()
    info = _anilist_lookup(args.title)
    if not info:
        # allow committing an off-AniList title by hand
        info = {"title": args.title, "title_alt": "", "source_id": None,
                "status": "", "total_chapters": args.total, "country": "",
                "genres": [], "score": 0, "site_url": ""}
        print(f"  (not found on AniList; committing '{args.title}' as-is)")
    if args.total:
        info["total_chapters"] = args.total
    s["current"] = info
    # a freshly-set series shouldn't be considered retired
    if info["title"] in s["retired"]:
        s["retired"].remove(info["title"])
    save_state(s)
    print(f"✓ current series = {info['title']}  "
          f"[{info.get('status','?')}, total {info.get('total_chapters')}]")
    print(f"  next up: chapter {_next_chapter(_chapters_done(s, info['title']))}")
    return 0


def cmd_next(args) -> int:
    s = load_state()
    plan = plan_next(s, auto_add=not args.no_auto_add)
    if plan.get("is_new_series"):
        save_state(s)   # persist the auto-promotion
    if args.json:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    if not plan.get("series"):
        print(f"NEXT: nothing to make — {plan['reason']}")
        return 1
    tag = " (NEW SERIES)" if plan["is_new_series"] else ""
    print(f"NEXT: {plan['series']} — chapter {plan['chapter']}{tag}")
    print(f"  slug: {plan['slug']}")
    print(f"  why : {plan['reason']}")
    if plan.get("caught_up"):
        print("  ⚠ caught up to the latest chapter — wait for a new release, or "
              "`channel set` a second series if you need cadence.")
    return 0


def cmd_done(args) -> int:
    s = load_state()
    series = args.series
    if not series:
        if not s.get("current"):
            sys.exit("no current series; pass --series")
        series = s["current"]["title"]
    mark_done(s, series, args.chapter)
    save_state(s)
    print(f"✓ recorded {series} ch {args.chapter} as done "
          f"({len(_chapters_done(s, series))} total)")
    return 0


def cmd_skip(args) -> int:
    s = load_state()
    if args.title not in s["skip"]:
        s["skip"].append(args.title)
    save_state(s)
    print(f"✓ '{args.title}' added to blocklist (never auto-picked)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="channel",
                                description="Channel continuity / series roster")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show current series + progress").set_defaults(
        func=cmd_status)

    st = sub.add_parser("set", help="commit the channel to a series")
    st.add_argument("--title", required=True)
    st.add_argument("--total", type=int, help="override total chapter count")
    st.set_defaults(func=cmd_set)

    nx = sub.add_parser("next", help="decide the next video to make")
    nx.add_argument("--json", action="store_true")
    nx.add_argument("--no-auto-add", action="store_true",
                    help="don't auto-promote a new series if current is exhausted")
    nx.set_defaults(func=cmd_next)

    dn = sub.add_parser("done", help="record a chapter as recapped")
    dn.add_argument("--chapter", required=True)
    dn.add_argument("--series", help="default: current series")
    dn.set_defaults(func=cmd_done)

    sk = sub.add_parser("skip", help="blocklist a series from auto-pick")
    sk.add_argument("--title", required=True)
    sk.set_defaults(func=cmd_skip)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
