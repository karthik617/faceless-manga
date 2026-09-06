#!/usr/bin/env python3
"""fetch_sfx.py — pull real CC0 sound effects from Freesound into
pipeline/brand/sfx/ so the renderer uses them instead of the ffmpeg-synthesized
placeholders.

Only CC0 ("Creative Commons 0", public domain) sounds are fetched, so there is
NO attribution obligation and they are safe for monetized/commercial use.

Auth: Freesound APIv2 needs only a TOKEN for search + preview download (full
original download would need OAuth2, but the HQ preview MP3 at ~128kbps is
plenty for a short whoosh/impact and needs just the token). Get a free key at:
    https://freesound.org/apiv2/apply/
Then either export FREESOUND_API_KEY or pass --key.

Usage:
    python3 pipeline/fetch_sfx.py --key <API_KEY>
    python3 pipeline/fetch_sfx.py            # uses $FREESOUND_API_KEY

Downloads one sound per target name (whoosh, impact, subboom, riser) into
pipeline/brand/sfx/<name>.wav. Re-run to refresh; --pick N grabs the Nth result
if you don't like the top one.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://freesound.org/apiv2"
SFX_DIR = Path(__file__).parent / "brand" / "sfx"
KEY_FILE = Path(__file__).parent / "freesound_key.txt"


def resolve_key(explicit=None):
    """Freesound token resolution: explicit arg > $FREESOUND_API_KEY >
    pipeline/freesound_key.txt. Returns None if nothing is configured."""
    if explicit:
        return explicit
    env = os.environ.get("FREESOUND_API_KEY")
    if env:
        return env
    if KEY_FILE.exists():
        tok = KEY_FILE.read_text().strip()
        if tok:
            return tok
    return None

# Per target: a search query + a max duration (s). Queries are tuned to the
# Shonen-Flux sound palette. All filtered to CC0 only.
TARGETS = {
    "whoosh":  ("whoosh transition swish", 2.0),
    # keep queries specific and exclude "whoosh/woosh" so transition swishes
    # don't leak into the impact/boom slots.
    "impact":  ("cinematic impact hit boom -whoosh -woosh -swoosh", 2.0),
    "subboom": ("bass drop boom rumble", 2.5),
    "riser":   ("riser build up sweep tension", 3.0),
}


def _get(url, token):
    req = Request(url, headers={"Authorization": f"Token {token}"})
    with urlopen(req, timeout=30) as r:
        return r.read()


def _search(token, query, max_dur, pick):
    params = {
        "query": query,
        "filter": f'license:"Creative Commons 0" duration:[0.1 TO {max_dur}]',
        "sort": "downloads_desc",   # popular = usually clean/usable
        "fields": "id,name,license,previews,duration,avg_rating",
        "page_size": 15,
    }
    import json
    data = json.loads(_get(f"{API}/search/text/?{urlencode(params)}", token))
    results = data.get("results", [])
    if not results:
        return None
    idx = min(pick, len(results) - 1)
    return results[idx]


def fetch(token, pick=0, only=None):
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    got = {}
    for name, (query, max_dur) in TARGETS.items():
        if only and name not in only:
            continue
        try:
            snd = _search(token, query, max_dur, pick)
            if not snd:
                print(f"  ! {name}: no CC0 results for '{query}'")
                continue
            purl = snd["previews"]["preview-hq-mp3"]
            mp3 = Path(tempfile.mktemp(suffix=".mp3"))
            mp3.write_bytes(_get(purl, token))
            out = SFX_DIR / f"{name}.wav"
            # normalize to 48k stereo wav, trim any long tail to keep it snappy
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
                 "-ar", "48000", "-ac", "2", "-t", f"{max_dur:.2f}",
                 "-c:a", "pcm_s16le", str(out)], check=True)
            mp3.unlink(missing_ok=True)
            print(f"  ok {name:8s} <- freesound #{snd['id']} "
                  f"\"{snd['name'][:40]}\" ({snd['duration']:.1f}s, CC0)")
            got[name] = out
        except Exception as e:
            print(f"  ! {name}: {type(e).__name__}: {e}")
    return got


def slugify(cue):
    """A stable filename slug for a cue phrase, e.g. 'door slam' -> door_slam."""
    return "".join(c if c.isalnum() else "_" for c in cue.lower()).strip("_")[:40]


def fetch_cues(token, cues, max_dur=2.5):
    """Fetch each distinct content cue phrase as a CC0 sound into SFX_DIR.
    Returns {cue: Path|None} — None means no CC0 match (caller SKIPS that
    event). A cached file for the cue's slug is reused (no refetch)."""
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for cue in dict.fromkeys(cues):     # de-dup, preserve order
        slug = slugify(cue)
        dest = SFX_DIR / f"cue_{slug}.wav"
        if dest.exists() and dest.stat().st_size > 0:
            out[cue] = dest
            print(f"  cached {cue!r} -> {dest.name}")
            continue
        try:
            snd = _search(token, cue, max_dur, 0)
            if not snd:
                print(f"  SKIP  {cue!r}: no CC0 result")
                out[cue] = None
                continue
            mp3 = Path(tempfile.mktemp(suffix=".mp3"))
            mp3.write_bytes(_get(snd["previews"]["preview-hq-mp3"], token))
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
                 "-ar", "48000", "-ac", "2", "-t", f"{max_dur:.2f}",
                 "-c:a", "pcm_s16le", str(dest)], check=True)
            mp3.unlink(missing_ok=True)
            print(f"  ok    {cue!r} <- #{snd['id']} \"{snd['name'][:36]}\" (CC0)")
            out[cue] = dest
        except Exception as e:
            print(f"  SKIP  {cue!r}: {type(e).__name__}: {e}")
            out[cue] = None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=None,
                    help="Freesound API token (or set FREESOUND_API_KEY, or "
                         "put it in pipeline/freesound_key.txt)")
    ap.add_argument("--pick", type=int, default=0,
                    help="use the Nth search result (0=top) if the default "
                         "isn't good")
    ap.add_argument("--only", nargs="*", choices=list(TARGETS),
                    help="fetch only these names (default: all)")
    args = ap.parse_args()
    args.key = resolve_key(args.key)
    if not args.key:
        sys.exit("no API key: set FREESOUND_API_KEY, pass --key, or put it in "
                 "pipeline/freesound_key.txt "
                 "(get one free at https://freesound.org/apiv2/apply/)")
    print(f"Fetching CC0 SFX into {SFX_DIR} ...")
    got = fetch(args.key, pick=args.pick, only=args.only)
    print(f"Done: {len(got)} file(s). The renderer auto-uses these over synth.")


if __name__ == "__main__":
    main()
