#!/usr/bin/env python3
"""download_chapter.py — STAGE 1: get a chapter's page images into raw/.

Backends (--backend):
  folder    — copy an existing local folder of images (manual mode, no network)
  gallerydl — shell out to gallery-dl (broad site coverage)
  mangadex  — shell out to mangadex-downloader (legal MangaDex source)
  requests  — pure requests + threads fallback (point at explicit image URLs
              or a page whose <img> srcs we can scrape)
  auto      — folder if --folder given, else mangadex for mangadex.org URLs,
              else gallerydl.

Output: pages copied/renumbered as raw/001.ext, 002.ext ... (natural order).

LEGAL: manga panels are copyrighted. Prefer legal sources (MangaDex permitted
titles, official apps). Scraping aggregator sites violates their ToS. See README.

Usage:
    python3 download_chapter.py --folder ~/panels --out output/<slug>/raw
    python3 download_chapter.py --url https://mangadex.org/chapter/<id> \
        --backend mangadex --out output/<slug>/raw
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
FETCH_GAP = 1.0  # min seconds between requests-backend fetches (politeness)
_last_fetch = [0.0]


def natural_sort(paths):
    """Sort so '2.jpg' < '10.jpg' (numeric-aware)."""
    def key(p):
        s = str(p)
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", s)]
    return sorted(paths, key=key)


def _renumber_into(src_files, out_dir):
    """Copy an ordered list of image files into out_dir as 001.ext, 002.ext ..."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, src in enumerate(natural_sort(src_files), 1):
        src = Path(src)
        if src.suffix.lower() not in IMG_EXTS:
            continue
        n += 1
        dst = out_dir / f"{n:03}{src.suffix.lower()}"
        shutil.copy(src, dst)
    return n


def from_folder(src_dir, out_dir):
    """Manual mode: ingest an existing local folder of panel/page images."""
    src_dir = Path(src_dir).expanduser()
    if not src_dir.is_dir():
        sys.exit(f"--folder not a directory: {src_dir}")
    files = [p for p in src_dir.rglob("*") if p.suffix.lower() in IMG_EXTS]
    if not files:
        sys.exit(f"no images found under {src_dir}")
    n = _renumber_into(files, out_dir)
    print(f"  [folder] ingested {n} page(s) -> {out_dir}")
    return n


def _resolve_bin(name):
    """Find a CLI next to the running interpreter (venv bin/) or on PATH."""
    cand = Path(sys.executable).parent / name
    if cand.exists():
        return str(cand)
    return shutil.which(name)


def _run_cli(cmd, tool):
    """Run an external downloader CLI, streaming output; exit on failure."""
    exe = _resolve_bin(cmd[0])
    if not exe:
        sys.exit(f"{tool} not found (looked next to {sys.executable} and on "
                 f"PATH). Install it: pip install {cmd[0]} into the project venv.")
    cmd = [exe] + list(cmd[1:])
    print(f"  [{tool}] {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"{tool} failed (exit {r.returncode})")


def _download_gallerydl(url, out_dir, jobs=4):
    tmp = Path(out_dir).parent / "_dl_gallerydl"
    tmp.mkdir(parents=True, exist_ok=True)
    _run_cli(["gallery-dl", "--dest", str(tmp), url], "gallery-dl")
    files = [p for p in tmp.rglob("*") if p.suffix.lower() in IMG_EXTS]
    if not files:
        sys.exit("gallery-dl downloaded no images")
    n = _renumber_into(files, out_dir)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  [gallery-dl] {n} page(s) -> {out_dir}")
    return n


def _download_mangadex(url, out_dir):
    tmp = Path(out_dir).parent / "_dl_mangadex"
    tmp.mkdir(parents=True, exist_ok=True)
    # mangadex-downloader saves raw images with --save-as raw into --folder
    _run_cli(["mangadex-dl", "--save-as", "raw", "--folder", str(tmp), url],
             "mangadex-downloader")
    files = [p for p in tmp.rglob("*") if p.suffix.lower() in IMG_EXTS]
    if not files:
        sys.exit("mangadex-downloader produced no images")
    n = _renumber_into(files, out_dir)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  [mangadex] {n} page(s) -> {out_dir}")
    return n


def _fetch_one(url, dst, retries=5):
    """Fetch a single image URL with backoff, honoring Retry-After (mirrors
    make_video.py::fetch_image discipline)."""
    for attempt in range(retries):
        wait = _last_fetch[0] + FETCH_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, timeout=180,
                             headers={"User-Agent": UA, "Referer": url})
            _last_fetch[0] = time.time()
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ct.startswith("image"):
                Path(dst).write_bytes(r.content)
                return True
            if r.status_code == 429 or r.status_code >= 500:
                ra = int(r.headers.get("retry-after", 0) or 0)
                time.sleep(max(ra, 15 * (attempt + 1)))
                continue
            return False
        except requests.RequestException:
            _last_fetch[0] = time.time()
            if attempt == retries - 1:
                return False
            time.sleep(10 * (attempt + 1))
    return False


def _download_requests(img_urls, out_dir, jobs=4):
    """Fetch an explicit ordered list of image URLs concurrently."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {}
        for i, url in enumerate(img_urls, 1):
            ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
            dst = out_dir / f"{i:03}{ext}"
            futs[ex.submit(_fetch_one, url, dst)] = (i, dst)
        for fut in as_completed(futs):
            i, dst = futs[fut]
            results[i] = fut.result() and dst.exists()
    ok = sum(1 for v in results.values() if v)
    print(f"  [requests] {ok}/{len(img_urls)} page(s) -> {out_dir}")
    if ok == 0:
        sys.exit("requests backend fetched no images")
    return ok


def download(url=None, folder=None, out_dir=None, backend="auto", jobs=4,
             img_urls=None):
    out_dir = Path(out_dir)
    if backend == "auto":
        if folder:
            backend = "folder"
        elif url and "mangadex.org" in url:
            backend = "mangadex"
        elif img_urls:
            backend = "requests"
        else:
            backend = "gallerydl"
    if backend == "folder":
        return from_folder(folder, out_dir)
    if backend == "gallerydl":
        return _download_gallerydl(url, out_dir, jobs)
    if backend == "mangadex":
        return _download_mangadex(url, out_dir)
    if backend == "requests":
        if not img_urls:
            sys.exit("requests backend needs --img-url (repeatable)")
        return _download_requests(img_urls, out_dir, jobs)
    sys.exit(f"unknown backend: {backend}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="chapter URL (gallery-dl / mangadex)")
    ap.add_argument("--folder", help="local folder of page images (manual mode)")
    ap.add_argument("--img-url", action="append", default=[],
                    help="explicit image URL for the requests backend (repeatable)")
    ap.add_argument("--out", required=True, help="output raw/ dir")
    ap.add_argument("--backend",
                    choices=["auto", "folder", "gallerydl", "mangadex", "requests"],
                    default="auto")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    if not (args.url or args.folder or args.img_url):
        sys.exit("give --url, --folder, or --img-url")
    download(url=args.url, folder=args.folder, out_dir=args.out,
             backend=args.backend, jobs=args.jobs,
             img_urls=args.img_url or None)


if __name__ == "__main__":
    main()
