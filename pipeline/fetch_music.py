#!/usr/bin/env python3
"""fetch_music.py — pick a background-music bed from freetouse.com that matches
the mood of a script, and drop it into pipeline/brand/music.mp3 so the renderer
uses it as the sidechain-ducked BGM (panel_render.py reads brand/music.mp3 when
the script's `music` field is truthy).

This mirrors fetch_sfx.py: the script tags cues, we search a free library, pick
the best match, and place the asset where the renderer already looks. Here the
match is at the WHOLE-VIDEO level (one bed per video) rather than per-word.

HOW IT WORKS
------------
freetouse.com is a JS app backed by a clean public JSON API (no key/login):
    https://api.freetouse.com/v3/music/tracks/search?query=<term>
Each track carries `files.mp3` (a direct CDN URL), `is_premium`, `duration`,
`artists`, and `categories`. We:
  1. derive a mood term from the script (explicit override > `music_mood` field
     > keyword/category vote over narration+captions),
  2. search the API for that term,
  3. keep only NON-premium tracks (free tier) at least --min-dur long,
  4. pick the most-downloaded match,
  5. download the MP3 to pipeline/brand/music.mp3,
  6. write pipeline/brand/music.credit.txt with the required attribution and
     stamp the same string onto the script's `music_attribution` field.

LICENSE — READ THIS
-------------------
freetouse.com Free License = NON-commercial user-generated content, and it
REQUIRES attribution in the video description. Monetization is allowed only for
individual-creator UGC, not brand/company content. Commercial/brand use needs a
paid license. This tool only ever downloads free-tier tracks and always emits
the attribution text — putting that text in your description is on you. Pass
--commercial-ok to acknowledge you understand and proceed anyway (it does NOT
grant a commercial license; it only silences the guard).

CONTENT ID CLAIMS — EXPECTED, NOT A STRIKE
------------------------------------------
Free-license tracks are often still registered with YouTube Content ID by the
composer's publisher — especially COMPOSITION ("Melody or lyrics") claims,
which are separate from the recording rights freetouse licenses. A claim on a
freetouse track is normal and is NOT a copyright strike. Procedure:
  1. Make sure the attribution block (music.credit.txt) is in the video
     description.
  2. Dispute the claim in YouTube Studio (reason: License), citing the
     freetouse Free License + the track's freetouse page URL (now included in
     the attribution text) + attribution in the description. Also submit
     freetouse's claim-release form.
  3. If the dispute is rejected (composition claims sometimes can't be
     released), accepting the revenue-share on that segment is fine.
  4. Add the track to pipeline/music_blocklist.txt ("Artist — Title" per
     line), delete the cached output/<slug>/music_<mood>.mp3, and re-run this
     script so future videos pick a different track. --allow-blocked bypasses
     the blocklist if you ever need to.

Usage:
    python3 pipeline/fetch_music.py output/<slug>/<slug>.json
    python3 pipeline/fetch_music.py output/<slug>/<slug>.json --mood cinematic
    python3 pipeline/fetch_music.py output/<slug>/<slug>.json --pick 1
    python3 pipeline/fetch_music.py output/<slug>/<slug>.json --print   # no write
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.freetouse.com/v3"
BRAND_DIR = Path(__file__).parent / "brand"
SOURCE_URL = "https://freetouse.com/music"
BLOCKLIST_PATH = Path(__file__).parent / "music_blocklist.txt"

# Valid freetouse categories that read as "moods" for a faceless-manga video.
# We vote over these using the script's text; the winner becomes the search
# term. Everything here is a real category on the site (see categories/all).
MOOD_CATEGORIES = [
    "Epic", "Cinematic", "Dramatic", "Emotional", "Sad", "Inspiring",
    "Uplifting", "Powerful", "Hype", "Energetic", "Exciting", "Action",
    "Happy", "Fun", "Chill", "Calm", "Peaceful", "Relaxing", "Romantic",
    "Mysterious", "Dark", "Suspense", "Lofi", "Ambient",
]

# Word cues -> mood category. Tuned to shonen/manga narration. First match in
# the vote tally wins ties by MOOD_CATEGORIES order (i.e. more dramatic first).
CUE_WORDS = {
    "Epic":       r"\b(epic|legend|ultimate|unleash|awaken|god|titan|destiny|war|battle)\b",
    "Cinematic":  r"\b(cinematic|trailer|world|fate|journey|rises?|dawn|horizon)\b",
    "Dramatic":   r"\b(shatter|betray|nightmare|crushing|desperate|storm|collapse)\b",
    "Emotional":  r"\b(tears?|heart|memories|goodbye|alone|promise|bond|feelings?)\b",
    "Sad":        r"\b(sad|grief|loss|lonely|loneliness|sorrow|cry|mourn|broken)\b",
    "Inspiring":  r"\b(hope|dream|believe|rise|overcome|never give up|courage|resolve)\b",
    "Uplifting":  r"\b(win|victory|triumph|together|friends?|shine|bright|future)\b",
    "Powerful":   r"\b(power|strength|force|dominate|crush|overwhelming|might)\b",
    "Hype":       r"\b(hype|insane|crazy|explosive|firestorm|ignite|blast|rush)\b",
    "Energetic":  r"\b(fast|race|speed|chase|adrenaline|charge|sprint)\b",
    "Exciting":   r"\b(exciting|thrill|shock|reveal|twist|surprise)\b",
    "Action":     r"\b(fight|clash|strike|attack|slash|punch|combat|duel)\b",
    "Happy":      r"\b(happy|smile|laugh|joy|cheer|fun day|celebrat)\b",
    "Chill":      r"\b(chill|calm day|relax|slice of life|casual|peaceful morning)\b",
    "Romantic":   r"\b(love|crush|blush|romance|date|kiss|confession)\b",
    "Mysterious": r"\b(mystery|secret|hidden|strange|unknown|shadow|whisper)\b",
}


def _get(url):
    req = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "faceless-manga/fetch_music (+local pipeline)",
    })
    with urlopen(req, timeout=30) as r:
        return r.read()


def _script_text(data):
    """Flatten narration + captions + keywords into one lowercase blob."""
    parts = []
    for sc in data.get("scenes", []) or []:
        parts.append(sc.get("narration", "") or "")
        parts.append(sc.get("caption", "") or "")
        parts.extend(sc.get("keywords", []) or [])
    parts.append(data.get("title", "") or "")
    return " ".join(parts).lower()


def derive_mood(data, override=None):
    """Return (mood, why). Priority: --mood > script music_mood field > vote."""
    if override:
        return override, "cli --mood"
    if data.get("music_mood"):
        return data["music_mood"], "script music_mood field"
    text = _script_text(data)
    tally = {}
    for mood, pat in CUE_WORDS.items():
        n = len(re.findall(pat, text))
        if n:
            tally[mood] = n
    if not tally:
        return "Cinematic", "default (no cues matched)"
    # highest count; tie broken by MOOD_CATEGORIES priority order
    best = max(tally, key=lambda m: (tally[m], -MOOD_CATEGORIES.index(m)
                                     if m in MOOD_CATEGORIES else 0))
    why = "voted from script (" + ", ".join(
        f"{m}:{tally[m]}" for m in sorted(tally, key=tally.get, reverse=True)
    ) + ")"
    return best, why


def track_artists(track):
    """Artist names as a list of strings (API shape: [[0, {name: ...}], ...])."""
    return [a[1]["name"] for a in track.get("artists", [])
            if isinstance(a, list) and len(a) > 1 and a[1].get("name")]


def track_url(track):
    """Best-effort freetouse track page URL, e.g.
    https://freetouse.com/music/pufino/thoughtful (site convention:
    /music/<artist-slug>/<title-slug>)."""
    def slug(s):
        s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
        return s
    artists = track_artists(track)
    title = track.get("title", "")
    if not artists or not title:
        return SOURCE_URL
    return f"{SOURCE_URL}/{slug(artists[0])}/{slug(title)}"


def load_blocklist(path=BLOCKLIST_PATH):
    """Read 'Artist — Title' lines -> set of (artist_lower, title_lower).
    Accepts an em dash or plain hyphen (with spaces) as the separator."""
    blocked = set()
    p = Path(path)
    if not p.exists():
        return blocked
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+[—–-]\s+", line, maxsplit=1)
        if len(parts) == 2:
            blocked.add((parts[0].strip().lower(), parts[1].strip().lower()))
    return blocked


def is_blocked(track, blocked):
    title = (track.get("title") or "").strip().lower()
    return any((a.strip().lower(), title) in blocked
               for a in track_artists(track))


def search(mood, min_dur, max_dur, pick, blocked=frozenset()):
    """Return the chosen free (non-premium, non-blocklisted) track dict,
    or None."""
    url = f"{API}/music/tracks/search?" + urlencode({"query": mood})
    data = json.loads(_get(url))
    tracks = data.get("data", []) if data.get("ok", True) else []
    free = [t for t in tracks
            if not t.get("is_premium")
            and t.get("files", {}).get("mp3")
            and min_dur <= (t.get("duration") or 0) <= max_dur]
    skipped = [t for t in free if is_blocked(t, blocked)]
    for t in skipped:
        print(f"  [blocklist] skipping {t.get('title')!r} by "
              f"{', '.join(track_artists(t))}")
    free = [t for t in free if t not in skipped]
    if not free:
        return None
    # most downloaded first = usually the cleanest, safest loop
    free.sort(key=lambda t: t.get("downloads", 0), reverse=True)
    return free[min(pick, len(free) - 1)]


def attribution(track):
    """The exact free-license attribution line the site asks creators to use,
    plus the track's own page URL (helps Content ID disputes)."""
    title = track.get("title", "Unknown")
    artists = ", ".join(track_artists(track)) or "Unknown"
    return (f"Music track: {title} by {artists}\n"
            f"Source: {track_url(track)}\n"
            f"Free Background Music from freetouse.com (attribution required)")


def mood_slug(mood):
    return "".join(c if c.isalnum() else "_" for c in mood.lower()).strip("_")


def id3_info(path):
    """(title, artist) from an mp3's tags via ffprobe, or (None, None)."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, timeout=15).stdout
        tags = json.loads(out).get("format", {}).get("tags", {})
        tags = {k.lower(): v for k, v in tags.items()}
        return tags.get("title"), tags.get("artist")
    except Exception:
        return None, None


def fetch_track(mood, out, min_dur=60.0, max_dur=600.0, pick=0,
                blocked=frozenset()):
    """Search + download one free track for `mood` into `out`.
    Returns (track, credit) or (None, None). `blocked` entries of
    (artist_lower, title_lower) are skipped."""
    track = search(mood, min_dur, max_dur, pick, blocked)
    if not track:
        return None, None
    tmp = Path(tempfile.mktemp(suffix=".mp3"))
    tmp.write_bytes(_get(track["files"]["mp3"]))
    if tmp.stat().st_size < 10_000:
        tmp.unlink(missing_ok=True)
        return None, None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(out)
    return track, attribution(track)


def fetch_plan(data, out_dir, min_dur=60.0, max_dur=600.0, pick=0,
               blocked=frozenset()):
    """Multi-mood mode: the script's `music_plan` is a list of sections
    [{"mood": "Dramatic", "from_scene": 0}, ...]. Fetch one bed per DISTINCT
    mood into out_dir/music_<mood>.mp3, and stamp track/artist/url onto every
    plan section of that mood (so a Content ID claim at a timestamp maps
    straight to a track). Returns ({mood: path}, [credits])."""
    plan = data.get("music_plan") or []
    moods = list(dict.fromkeys(
        s.get("mood") for s in plan if s.get("mood")))
    got, credits = {}, []
    used = set()   # (artist_lower, title_lower) already in this plan —
                   # avoid the same track scoring two different moods
    for m in moods:
        dest = Path(out_dir) / f"music_{mood_slug(m)}.mp3"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  [cached] {dest.name}")
            got[m] = dest
            # backfill track metadata onto plan sections from the ID3 tags
            title, artist = id3_info(dest)
            if title:
                fake = {"title": title,
                        "artists": [[0, {"name": artist}]] if artist else []}
                if artist:
                    used.add((artist.strip().lower(), title.strip().lower()))
                for sec in plan:
                    if sec.get("mood") == m and not sec.get("track"):
                        sec["track"] = title
                        sec["artist"] = artist or "Unknown"
                        sec["url"] = track_url(fake)
            continue
        try:
            track, credit = fetch_track(m, dest, min_dur, max_dur, pick,
                                        blocked | frozenset(used))
        except Exception as e:
            print(f"  ! mood {m!r}: {type(e).__name__}: {e}")
            track = None
        if track:
            print(f"  ok {m:12s} <- {track['title']!r} "
                  f"({track.get('duration', 0):.0f}s)")
            got[m] = dest
            credits.append(credit)
            title_l = (track.get("title") or "").strip().lower()
            used.update((a.strip().lower(), title_l)
                        for a in track_artists(track))
            for sec in plan:
                if sec.get("mood") == m:
                    sec["track"] = track.get("title")
                    sec["artist"] = ", ".join(track_artists(track))
                    sec["url"] = track_url(track)
        else:
            print(f"  ! no free track for mood {m!r}")

    # ---- dedupe pass: two moods must never share one track ----
    # (the `used` set can miss dupes when a cached file has no ID3 tags, or
    # the cache predates dedup; ch2 shipped Dramatic==Epic=="Cinematic" and
    # the planned climax lift never happened). Content-hash comparison
    # catches every case; the later mood is refetched with escalating pick.
    import hashlib
    seen = {}
    for m in moods:
        p = got.get(m)
        if not p:
            continue
        h = hashlib.md5(Path(p).read_bytes()).hexdigest()
        if h not in seen:
            seen[h] = m
            continue
        print(f"  dup: mood {m!r} uses the same track as {seen[h]!r}; "
              f"refetching a distinct one")
        Path(p).unlink(missing_ok=True)
        replaced = False
        for pk in range(0, 6):
            try:
                track, credit = fetch_track(m, p, min_dur, max_dur, pk,
                                            blocked | frozenset(used))
            except Exception as e:
                print(f"  ! mood {m!r} retry {pk}: {type(e).__name__}: {e}")
                break
            if not track:
                break
            h2 = hashlib.md5(Path(p).read_bytes()).hexdigest()
            if h2 not in seen:
                seen[h2] = m
                credits.append(credit)
                title_l = (track.get("title") or "").strip().lower()
                used.update((a.strip().lower(), title_l)
                            for a in track_artists(track))
                for sec in plan:
                    if sec.get("mood") == m:
                        sec["track"] = track.get("title")
                        sec["artist"] = ", ".join(track_artists(track))
                        sec["url"] = track_url(track)
                print(f"  ok {m:12s} <- {track['title']!r} (dedup retry)")
                replaced = True
                break
            Path(p).unlink(missing_ok=True)
        if not replaced:
            # give up: drop the mood so the renderer reuses a neighbor bed
            # rather than double-booking one track across two moods
            got.pop(m, None)
            print(f"  ! mood {m!r}: could not find a distinct track; "
                  f"section will reuse the surrounding bed")
    return got, credits


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="path to output/<slug>/<slug>.json")
    ap.add_argument("--mood", help="force a mood/search term (e.g. Cinematic, "
                    "Epic, Sad) instead of deriving it from the script")
    ap.add_argument("--pick", type=int, default=0,
                    help="use the Nth free result (0=top by downloads)")
    ap.add_argument("--min-dur", type=float, default=60.0,
                    help="skip tracks shorter than this many seconds "
                         "(default 60; the bed loops anyway)")
    ap.add_argument("--max-dur", type=float, default=600.0,
                    help="skip tracks longer than this (default 600)")
    ap.add_argument("--out", default=str(BRAND_DIR / "music.mp3"),
                    help="output bed path (default pipeline/brand/music.mp3)")
    ap.add_argument("--print", dest="dry", action="store_true",
                    help="only resolve + print the pick; download nothing")
    ap.add_argument("--commercial-ok", action="store_true",
                    help="acknowledge the free tier is non-commercial + needs "
                         "attribution, and proceed anyway")
    ap.add_argument("--allow-blocked", action="store_true",
                    help="ignore pipeline/music_blocklist.txt (tracks that "
                         "previously drew Content ID claims)")
    args = ap.parse_args()

    blocked = frozenset() if args.allow_blocked else load_blocklist()
    if blocked:
        print(f"Blocklist: {len(blocked)} track(s) "
              f"({BLOCKLIST_PATH.name}; --allow-blocked to ignore)")

    spath = Path(args.script)
    if not spath.exists():
        sys.exit(f"script not found: {spath}")
    data = json.loads(spath.read_text())

    # multi-mood: a script music_plan fetches one bed per section mood, and
    # the renderer stitches them into a single narrative-scored bed.
    if data.get("music_plan") and not args.mood and not args.dry:
        print(f"Music plan: {len(data['music_plan'])} sections")
        got, credits = fetch_plan(data, Path(args.out).parent,
                                  args.min_dur, args.max_dur, args.pick,
                                  blocked)
        if got:
            # Rebuild the FULL credit list from the plan's per-section track
            # metadata (covers cached beds too, so a partial re-fetch doesn't
            # drop attributions for beds that were already on disk).
            seen, full = set(), []
            for sec in data["music_plan"]:
                if sec.get("track") and sec.get("mood") in got:
                    key = (sec["track"], sec.get("artist"))
                    if key in seen:
                        continue
                    seen.add(key)
                    full.append(
                        f"Music track: {sec['track']} by "
                        f"{sec.get('artist') or 'Unknown'}\n"
                        f"Source: {sec.get('url') or SOURCE_URL}\n"
                        f"Free Background Music from freetouse.com "
                        f"(attribution required)")
            cred = "\n\n".join(full or credits)
            if cred:
                cred_path = Path(args.out).parent / "music.credit.txt"
                cred_path.write_text(cred + "\n")
                data["music_attribution"] = cred
                data.setdefault("music", True)
                spath.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            # also keep the FIRST section's bed as the plain music.mp3 so the
            # single-bed path still works as a fallback
            first_mood = next((s["mood"] for s in data["music_plan"]
                               if s.get("mood") in got), None)
            if first_mood:
                import shutil as _sh
                _sh.copy(got[first_mood], args.out)
            print(f"ok  {len(got)} mood bed(s) in {Path(args.out).parent}")
            return
        print("  ! plan fetch got nothing; falling back to single mood")

    mood, why = derive_mood(data, args.mood)
    print(f"Mood: {mood}  ({why})")

    try:
        track = search(mood, args.min_dur, args.max_dur, args.pick, blocked)
    except Exception as e:
        sys.exit(f"search failed: {type(e).__name__}: {e}")
    if not track:
        sys.exit(f"no free track for mood {mood!r} in "
                 f"[{args.min_dur:.0f},{args.max_dur:.0f}]s — try --mood or "
                 f"widen --min-dur/--max-dur")

    mp3_url = track["files"]["mp3"]
    dur = track.get("duration", 0)
    dls = track.get("downloads", 0)
    credit = attribution(track)
    print(f"Pick: {track['title']!r}  {dur:.0f}s  downloads={dls}  free")
    print(f"MP3 : {mp3_url}")
    print("Attribution (put this in your video description):")
    print("  " + credit.replace("\n", "\n  "))

    print("\nNOTE: freetouse Free License = NON-commercial creator content, "
          "attribution REQUIRED. Brand/commercial use needs a paid license.")
    if not args.dry and not args.commercial_ok:
        # Not a hard block — but make the human confirm they read the terms.
        print("Proceeding to download for personal/creator use. Pass "
              "--commercial-ok to silence this note.")

    if args.dry:
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mktemp(suffix=".mp3"))
    try:
        tmp.write_bytes(_get(mp3_url))
    except Exception as e:
        sys.exit(f"download failed: {type(e).__name__}: {e}")
    size = tmp.stat().st_size
    if size < 10_000:
        tmp.unlink(missing_ok=True)
        sys.exit(f"downloaded file too small ({size} B) — aborting")
    tmp.replace(out)
    print(f"\nok  wrote {out}  ({size/1e6:.1f} MB)")

    # Persist attribution next to the bed and onto the script so downstream
    # (description generation) can pick it up automatically.
    cred_path = out.parent / "music.credit.txt"
    cred_path.write_text(credit + "\n")
    print(f"ok  wrote {cred_path}")
    try:
        data["music_attribution"] = credit
        data.setdefault("music", True)
        spath.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"ok  stamped music_attribution onto {spath.name}")
    except Exception as e:
        print(f"  ! could not update script json: {e}")

    print("\nThe renderer will use this bed automatically (music:true). "
          "Disable per-run with panel_render.py --no-music.")


if __name__ == "__main__":
    main()
