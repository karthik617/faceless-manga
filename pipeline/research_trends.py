#!/usr/bin/env python3
"""research_trends.py — STAGE 0: decide WHAT to make a video about.

Your pipeline starts at `download` — it assumes you already picked the series and
chapter. This stage adds the missing front end: it discovers what's trending in
the manga world RIGHT NOW and ranks each title by how well a recap VIDEO can be
made from it, so `manga.py` can be pointed at a winner instead of a guess.

Primary source : AniList GraphQL (free, no API key, reliable).
Fallback       : Jikan / MyAnimeList (if AniList is unreachable).
Everything here is READ-ONLY metadata — it only decides the topic. Acquiring the
actual pages stays with download_chapter.py against legitimate sources.

Ranking blends four live signals into a single video_score (0..1):
    trend momentum · audience size · passionate fanbase · rating · genre fit
plus small nudges for "ongoing" (fresh chapters to recap) and format.

Usage:
    # See what's hot, ranked for recap suitability
    ./venv/bin/python3 pipeline/research_trends.py discover --top 10

    # Emit the #1 pick as JSON ready to feed manga.py
    ./venv/bin/python3 pipeline/research_trends.py pick --rank 1 --json

    # Full ranked list saved to a file
    ./venv/bin/python3 pipeline/research_trends.py discover --limit 30 \
        --out research_trends.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field

import requests

ANILIST_URL = "https://graphql.anilist.co"
JIKAN_URL = "https://api.jikan.moe/v4/top/manga"

_TRENDING_QUERY = """
query ($perPage: Int) {
  Page(perPage: $perPage) {
    media(type: MANGA, sort: TRENDING_DESC,
          status_in: [RELEASING, FINISHED]) {
      id
      title { romaji english native }
      popularity
      trending
      favourites
      averageScore
      chapters
      status
      genres
      format
      isAdult
      countryOfOrigin
      description(asHtml: false)
      siteUrl
    }
  }
}
"""

# Genres that historically retain viewers as recap videos (action-forward,
# strong visual beats). Used as a light ranking nudge, not a gate.
_HIGH_RETENTION_GENRES = {
    "Action", "Adventure", "Fantasy", "Supernatural", "Drama",
    "Mystery", "Thriller", "Sports", "Sci-Fi", "Horror",
}


@dataclass
class Candidate:
    source_id: int
    title: str
    title_alt: str = ""
    trending: int = 0
    popularity: int = 0
    favourites: int = 0
    score: int = 0            # averageScore (0-100)
    chapters: int | None = None
    status: str = ""
    genres: list = field(default_factory=list)
    format: str = ""
    country: str = ""         # JP=manga, KR=manhwa, CN=manhua
    is_adult: bool = False
    synopsis: str = ""
    site_url: str = ""
    origin: str = "anilist"
    video_score: float = 0.0
    reasons: list = field(default_factory=list)
    # legal-availability (filled in only when --fetchable / check_availability)
    fetchable: bool | None = None       # any in-order English chapters on MangaDex?
    fetchable_chapters: int = 0         # how many fetchable EN chapters
    starts_at_one: bool = False         # is chapter 1 fetchable (binge-in-order)?
    availability_note: str = ""

    def to_dict(self):
        return asdict(self)

    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return re.sub(r"-+", "-", s) or "manga-recap"


def _clean(text: str, limit: int = 400) -> str:
    text = (text or "").replace("<br>", " ").replace("<i>", "").replace("</i>", "")
    return " ".join(text.split())[:limit]


# --------------------------------------------------------------------------- #
# Legal availability — the hot-vs-fetchable reality check.
#
# The manga that trends hardest is usually OFFICIALLY LICENSED, so MangaDex is
# only permitted to host its single newest chapter — useless for a channel that
# recaps chapter 1 onward. This probe asks MangaDex what's actually fetchable and
# whether chapter 1 is among it (binge-in-order), so ranking can prefer series
# you can really make videos from.
# --------------------------------------------------------------------------- #
MANGADEX_API = "https://api.mangadex.org"


def _md_availability(title: str, alt: str = "") -> dict:
    """Return {fetchable, fetchable_chapters, starts_at_one, note} for a title."""
    import time
    for q in [t for t in (title, alt) if t]:
        try:
            r = requests.get(f"{MANGADEX_API}/manga", timeout=20, params={
                "title": q, "limit": 1, "order[relevance]": "desc",
                "contentRating[]": ["safe", "suggestive"]})
            data = r.json().get("data", [])
            if not data:
                continue
            mid = data[0]["id"]
            fr = requests.get(f"{MANGADEX_API}/manga/{mid}/feed", timeout=20, params={
                "translatedLanguage[]": ["en"], "limit": 100,
                "order[chapter]": "asc", "contentRating[]": ["safe", "suggestive"]})
            feed = fr.json().get("data", [])
            nums = []
            for x in feed:
                a = x["attributes"]
                if a.get("externalUrl") or a.get("pages", 0) <= 0:
                    continue
                c = a.get("chapter")
                try:
                    nums.append(float(c))
                except (TypeError, ValueError):
                    pass
            time.sleep(0.25)
            if not nums:
                note = ("licensed/external — no fetchable EN pages"
                        if feed else "no English chapters on MangaDex")
                return {"fetchable": False, "fetchable_chapters": 0,
                        "starts_at_one": False, "note": note}
            starts = min(nums) <= 1.0
            note = (f"{len(nums)} fetchable EN chapters"
                    + (" from ch1 (binge-in-order)" if starts
                       else f", but earliest is ch{min(nums):g} (no ch1)"))
            return {"fetchable": True, "fetchable_chapters": len(nums),
                    "starts_at_one": starts, "note": note}
        except Exception as e:
            return {"fetchable": None, "fetchable_chapters": 0,
                    "starts_at_one": False, "note": f"check failed: {e}"}
    return {"fetchable": False, "fetchable_chapters": 0,
            "starts_at_one": False, "note": "not found on MangaDex"}


def check_availability(cands: list[Candidate], only_top: int = 15) -> None:
    """Fill availability fields in-place for the first `only_top` candidates
    (probing is a live API call each, so we cap it)."""
    for c in cands[:only_top]:
        info = _md_availability(c.title, c.title_alt)
        c.fetchable = info["fetchable"]
        c.fetchable_chapters = info["fetchable_chapters"]
        c.starts_at_one = info["starts_at_one"]
        c.availability_note = info["note"]


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def _discover_anilist(limit: int, include_adult: bool) -> list[Candidate]:
    r = requests.post(ANILIST_URL, timeout=30,
                      headers={"Content-Type": "application/json"},
                      json={"query": _TRENDING_QUERY,
                            "variables": {"perPage": limit}})
    r.raise_for_status()
    media = r.json()["data"]["Page"]["media"]
    out: list[Candidate] = []
    for m in media:
        if m.get("isAdult") and not include_adult:
            continue
        t = m["title"]
        out.append(Candidate(
            source_id=m["id"],
            title=t.get("english") or t.get("romaji") or t.get("native") or "?",
            title_alt=t.get("romaji") or t.get("native") or "",
            trending=m.get("trending") or 0,
            popularity=m.get("popularity") or 0,
            favourites=m.get("favourites") or 0,
            score=m.get("averageScore") or 0,
            chapters=m.get("chapters"),
            status=m.get("status") or "",
            genres=m.get("genres") or [],
            format=m.get("format") or "",
            country=m.get("countryOfOrigin") or "",
            is_adult=bool(m.get("isAdult")),
            synopsis=_clean(m.get("description")),
            site_url=m.get("siteUrl") or "",
            origin="anilist",
        ))
    return out


def _discover_jikan(limit: int, include_adult: bool) -> list[Candidate]:
    """Fallback: MyAnimeList top manga via Jikan. No 'trending' field, so we
    approximate momentum from popularity rank."""
    r = requests.get(JIKAN_URL, timeout=30,
                     params={"limit": min(limit, 25)})
    r.raise_for_status()
    data = r.json().get("data", [])
    out: list[Candidate] = []
    n = len(data) or 1
    for i, m in enumerate(data):
        genres = [g["name"] for g in m.get("genres", [])]
        out.append(Candidate(
            source_id=m.get("mal_id", 0),
            title=m.get("title_english") or m.get("title") or "?",
            title_alt=m.get("title") or "",
            trending=n - i,                       # rank as pseudo-momentum
            popularity=m.get("members") or 0,
            favourites=m.get("favorites") or 0,
            score=int((m.get("score") or 0) * 10),
            chapters=m.get("chapters"),
            status=(m.get("status") or "").upper().replace(" ", "_"),
            genres=genres,
            format=m.get("type") or "",
            country="JP",
            is_adult=bool(m.get("rating") and "Rx" in str(m.get("rating"))),
            synopsis=_clean(m.get("synopsis")),
            site_url=m.get("url") or "",
            origin="jikan",
        ))
    return out


def discover_trending(limit: int = 25, include_adult: bool = False) -> list[Candidate]:
    try:
        cands = _discover_anilist(limit, include_adult)
        if cands:
            return cands
        raise RuntimeError("AniList returned no media")
    except Exception as e:
        print(f"  [warn] AniList failed ({e}); falling back to Jikan/MAL",
              file=sys.stderr)
        return _discover_jikan(limit, include_adult)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def rank_candidates(cands: list[Candidate]) -> list[Candidate]:
    if not cands:
        return cands
    max_pop = max(c.popularity for c in cands) or 1
    max_fav = max(c.favourites for c in cands) or 1
    max_trend = max(c.trending for c in cands) or 1

    for c in cands:
        reasons = []
        trend_n = c.trending / max_trend
        pop_n = c.popularity / max_pop
        fav_n = c.favourites / max_fav
        score_n = (c.score or 0) / 100
        genre_hit = len(set(c.genres) & _HIGH_RETENTION_GENRES)
        genre_n = min(genre_hit / 3, 1.0)

        video_score = (
            0.35 * trend_n +      # what's hot RIGHT NOW
            0.25 * pop_n +        # total audience that will search it
            0.15 * fav_n +        # passionate fanbase (comments/shares)
            0.15 * score_n +      # quality signal
            0.10 * genre_n        # visual/recap fit
        )
        if trend_n > 0.6:
            reasons.append("surging in current trends")
        if pop_n > 0.6:
            reasons.append("very large existing audience")
        if genre_hit:
            reasons.append(f"{genre_hit} high-retention genre(s)")
        if c.status == "RELEASING":
            reasons.append("ongoing — fresh chapters to recap")
            video_score += 0.03
        if (c.score or 0) >= 80:
            reasons.append(f"highly rated ({c.score}/100)")
        # long-runners have huge back-catalogs = many videos from one series
        if c.chapters and c.chapters > 100:
            reasons.append(f"deep back-catalog ({c.chapters} ch)")
            video_score += 0.02

        # LEGAL-AVAILABILITY adjustment (only if we actually probed MangaDex).
        # This is the hot-vs-fetchable correction: a series you can binge from
        # ch1 is worth far more to a channel than a hot title you can't download.
        if c.fetchable is not None:
            if c.starts_at_one and c.fetchable_chapters >= 5:
                video_score += 0.20
                reasons.append(f"binge-able: {c.fetchable_chapters} EN ch from ch1")
            elif c.fetchable and c.fetchable_chapters >= 5:
                video_score += 0.05
                reasons.append(f"{c.fetchable_chapters} EN ch (not from ch1)")
            elif c.fetchable:
                # only the newest chapter is hostable (licensed back-catalog):
                # can't build an in-order channel from it → push below binge-ables
                video_score -= 0.15
                reasons.append("only latest chapter fetchable (can't start ch1)")
            else:
                video_score -= 0.30      # can't legally download in order
                reasons.append("NOT legally downloadable in order")

        c.video_score = round(min(max(video_score, 0.0), 1.0), 4)
        c.reasons = reasons

    return sorted(cands, key=lambda x: x.video_score, reverse=True)


def discover(limit: int = 25, include_adult: bool = False,
             fetchable: bool = False, probe_top: int = 15) -> list[Candidate]:
    """Rank trending manga. When fetchable=True, probe MangaDex availability for
    the top `probe_top` and re-rank so binge-in-order series rise to the top."""
    cands = rank_candidates(discover_trending(limit=limit,
                                              include_adult=include_adult))
    if fetchable:
        check_availability(cands, only_top=probe_top)
        cands = rank_candidates(cands)   # re-rank with availability factored in
    return cands


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_ranked(cands: list[Candidate], top: int):
    print(f"\n=== Trending manga — ranked for recap-video suitability "
          f"(top {top}) ===")
    for i, c in enumerate(cands[:top], 1):
        chap = f"{c.chapters}ch" if c.chapters else "ongoing"
        flag = {"JP": "manga", "KR": "manhwa", "CN": "manhua"}.get(c.country, c.country)
        avail = ""
        if c.fetchable is not None:
            mark = ("✅ ch1+" if c.starts_at_one else
                    "🟡 partial" if c.fetchable else "❌ locked")
            avail = f"  [{mark}]"
        print(f"{i:2d}. [{c.video_score:.3f}] {c.title}{avail}")
        print(f"      trend={c.trending} pop={c.popularity} score={c.score} "
              f"{c.status} {chap} {flag} | {', '.join(c.genres[:4])}")
        if c.availability_note:
            print(f"      fetch: {c.availability_note}")
        if c.reasons:
            print(f"      why: {', '.join(c.reasons)}")


def cmd_discover(args) -> int:
    cands = discover(limit=args.limit, include_adult=args.adult,
                     fetchable=args.fetchable)
    _print_ranked(cands, args.top)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(
            json.dumps([c.to_dict() for c in cands], indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nfull ranked list -> {args.out}")
    return 0


def cmd_pick(args) -> int:
    cands = discover(limit=max(args.rank + 5, 15), include_adult=args.adult)
    if args.rank < 1 or args.rank > len(cands):
        sys.exit(f"--rank {args.rank} out of range (1..{len(cands)})")
    c = cands[args.rank - 1]
    if args.json:
        # machine-readable handoff for manga.py / auto orchestration
        print(json.dumps({
            "title": c.title,
            "title_alt": c.title_alt,
            "slug": c.slug(),
            "video_score": c.video_score,
            "status": c.status,
            "chapters": c.chapters,
            "reasons": c.reasons,
        }, ensure_ascii=False))
    else:
        print(f"picked #{args.rank}: {c.title}  (video_score {c.video_score:.3f})")
        print(f"  why: {', '.join(c.reasons)}")
        print(f"\nnext:\n  ./venv/bin/python3 manga.py --series \"{c.title}\" "
              f"--chapter 1 --mode auto   # then point download at a legit source")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="research_trends",
        description="Discover trending manga, ranked for recap-video suitability")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="list trending manga, ranked")
    d.add_argument("--limit", type=int, default=25)
    d.add_argument("--top", type=int, default=10)
    d.add_argument("--adult", action="store_true")
    d.add_argument("--fetchable", action="store_true",
                   help="probe MangaDex availability and rank series you can "
                        "actually download in order (from ch1) to the top")
    d.add_argument("--out", help="also write full ranked JSON here")
    d.set_defaults(func=cmd_discover)

    k = sub.add_parser("pick", help="print one ranked pick (optionally as JSON)")
    k.add_argument("--rank", type=int, default=1, help="1 = top trend")
    k.add_argument("--adult", action="store_true")
    k.add_argument("--json", action="store_true", help="emit JSON for manga.py")
    k.set_defaults(func=cmd_pick)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
