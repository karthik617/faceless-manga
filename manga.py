#!/usr/bin/env python3
"""manga.py — Manga/Manhwa recap video pipeline (resumable orchestrator).

Pipeline (mirrors faceless-studio/studio.py):
  0. discover -> (research_trends.py: AniList trend ranking) [optional, --discover]
  1. download -> raw/       (download_chapter.py: gallery-dl | mangadex | folder)
  2. segment  -> panels/    (segment_panels.py: page/strip -> ordered panels)
  3. clean    -> panels_clean/  (optional, --clean-bubbles)
  4. script   -> <slug>.json    (script_from_panels.py: OCR + recap LLM)
                 [pause to review/edit unless --yes]
  5. render   -> <slug>.mp4 + .srt  (panel_render.py, real panels + Ken Burns)
  6. extras   -> thumbs/    (make_thumbs.py)
  7. package  -> upload_package.md

Steps are resumable: each writes into output/<slug>/ and is skipped when its
output already exists. --from <step> jumps ahead.

Usage:
    # STAGE 0 — what should I make? (trending, ranked for recap suitability)
    ./venv/bin/python3 manga.py --discover
    ./venv/bin/python3 pipeline/research_trends.py discover --top 15

    ./venv/bin/python3 manga.py --url <chapter-url> --series "Series" --chapter 179
    ./venv/bin/python3 manga.py --folder ~/panels --series "Series" --chapter 1 --mode auto
    ./venv/bin/python3 manga.py --project output/series-ch179 --from render
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPE = HERE / "pipeline"
PY = str((HERE / "venv" / "bin" / "python3")) if (HERE / "venv" / "bin" / "python3").exists() else sys.executable

sys.path.insert(0, str(PIPE))
import gateway  # noqa: E402

STEPS = ["download", "segment", "clean", "script", "render", "review",
         "extras", "review_thumbs", "short", "review_short", "package"]


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def run_step(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"step failed: {' '.join(str(c) for c in cmd)}")


def has_images(d):
    d = Path(d)
    return d.is_dir() and any(
        p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for p in d.iterdir())


def step_download(args, proj):
    raw = proj / "raw"
    if has_images(raw) and args.from_step != "download":
        print("  [cached] raw/ already populated")
        return
    cmd = [PY, str(PIPE / "download_chapter.py"), "--out", str(raw),
           "--backend", args.backend, "--jobs", str(args.jobs)]
    if args.folder:
        cmd += ["--folder", args.folder]
    if args.url:
        cmd += ["--url", args.url]
    for u in args.img_url:
        cmd += ["--img-url", u]
    run_step(cmd)


def step_segment(args, proj):
    panels = proj / "panels"
    if has_images(panels) and args.from_step not in ("segment",):
        print("  [cached] panels/ already populated")
        return
    # Split guard (adopted from exp-004, gap-004): yolo text-box veto + v2
    # art-ink ceiling + gutter-edge SFX snapping on forced webtoon cuts, plus
    # the auto-mode majority vote (a cover page can no longer disable tile
    # stitching for a whole webtoon chapter -- the root cause of the golden
    # ch2 cropped_content faults). ON by default; the module CLI keeps
    # "none" as its own default (gap-002 convention: orchestrator passes the
    # flag). Opt-out: --split-guard none (byte-identical legacy splits).
    # yolo degrades to blob then to legacy cuts on any failure, never crashes.
    run_step([PY, str(PIPE / "segment_panels.py"), "--raw", str(proj / "raw"),
              "--out", str(panels), "--mode", args.mode,
              "--split-guard", args.split_guard])


def step_clean(args, proj):
    if not args.clean_bubbles:
        return
    out = proj / "panels_clean"
    if has_images(out) and args.from_step != "clean":
        print("  [cached] panels_clean/ already populated")
        return
    tier = args.clean_bubbles if args.clean_bubbles in ("cv", "gemini") else "cv"
    run_step([PY, str(PIPE / "clean_bubbles.py"), "--panels", str(proj / "panels"),
              "--out", str(out), "--tier", tier])


def step_clean_watermarks(args, proj):
    # exp-003 prototype: scanlation watermark detect+remove. Opt-in only
    # (--clean-watermarks); composes with bubble cleanup by writing into the
    # same panels_clean/. clean_watermarks.py caches per chapter
    # (watermark_template.json + watermark_log.json) so re-runs are no-ops,
    # and it exits 0 on internal failure so it can never break the pipeline.
    if not args.clean_watermarks:
        return
    run_step([PY, str(PIPE / "clean_watermarks.py"),
              "--panels", str(proj / "panels")])


def step_script(args, proj, slug):
    out_json = proj / f"{slug}.json"
    if out_json.exists() and out_json.stat().st_size > 0 and args.from_step != "script":
        print(f"  [cached] {out_json.name}")
        return out_json
    run_step([PY, str(PIPE / "script_from_panels.py"),
              "--panels", str(proj / "panels"), "--out", str(out_json),
              "--series", args.series, "--chapter", str(args.chapter),
              "--mode", args.mode, "--channel", args.channel])
    # Panel<->narration verifier (adopted from exp-001: -71% weighted faults
    # on treated scenes, full-run benchmark PASS). ON by default; the LLM's
    # panel picks are prompt-only and this deterministic+vision gate catches
    # title cards / SFX-only frames / wrong-moment panels BEFORE the render.
    # Disable with --no-verify-panels or MANGA_VERIFY_PANELS=0.
    if not getattr(args, "no_verify_panels", False) and \
            os.environ.get("MANGA_VERIFY_PANELS") != "0":
        cmd = [PY, str(PIPE / "verify_panels.py"), str(out_json)]
        # Quote-payoff pass (adopted from exp-005: golden ch2 missing_payoff
        # 5->0 on scene 21, zero LLM calls, zero new deps). Tier 1.5 inside
        # verify_panels; the module keeps --payoff opt-in (same convention as
        # panel_render's seq default, gap-002) and the orchestrator passes it
        # by default here. Disable just this tier with --no-payoff-check or
        # MANGA_VERIFY_PAYOFF=0; --no-verify-panels above disables the whole
        # verifier, payoff included.
        if not getattr(args, "no_payoff_check", False) and \
                os.environ.get("MANGA_VERIFY_PAYOFF") != "0":
            cmd.append("--payoff")
        run_step(cmd)
    if not args.yes:
        input(f"\nReview/edit {out_json} (narration + scene panels), then press "
              f"Enter to render (Ctrl-C to stop)... ")
    return out_json


def step_music(args, proj, slug):
    """Fetch a mood-matched background bed into the project dir (project-local,
    so it never clobbers the shared pipeline/brand/music.mp3). Also stamps the
    required attribution onto the script + a music.credit.txt the package step
    folds into the description. Best-effort: on any failure the renderer falls
    back to the shared brand bed."""
    if args.no_music:
        return None
    out_json = proj / f"{slug}.json"
    bed = proj / "music.mp3"
    if bed.exists() and bed.stat().st_size > 0 and args.from_step != "render":
        print(f"  [cached] {bed.name}")
        return bed
    cmd = [PY, str(PIPE / "fetch_music.py"), str(out_json),
           "--out", str(bed), "--commercial-ok"]
    if args.music_mood:
        cmd += ["--mood", args.music_mood]
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0 or not bed.exists():
        print("  ! music fetch failed; falling back to shared brand bed")
        return None
    return bed


def _render_cmd(args, proj, slug, redo=(), stop_after=None):
    """Build a panel_render.py invocation with the shared flag set."""
    out_json = proj / f"{slug}.json"
    cmd = [PY, str(PIPE / "panel_render.py"), str(out_json), "--res", args.res,
           "--workdir", str(proj / "_work")]
    bed = proj / "music.mp3"
    if bed.exists() and bed.stat().st_size > 0:
        cmd += ["--music", str(bed)]
    from fetch_sfx import resolve_key as _fs_key
    fs_key = _fs_key(args.freesound_key)
    if fs_key:
        cmd += ["--freesound-key", fs_key]
    if args.karaoke:
        cmd.append("--karaoke")
    if args.no_music:
        cmd.append("--no-music")
    if args.no_branding:
        cmd.append("--no-branding")
    if args.layout != "seq":
        cmd += ["--layout", args.layout]
    if args.text_aware:
        # exp-009 opt-in only: default invocations stay byte-identical
        cmd.append("--text-aware")
    for n in redo:
        cmd += ["--redo-scene", str(n)]
    if stop_after:
        cmd += ["--stop-after", stop_after]
    return cmd


def step_render(args, proj, slug):
    """Render the story cut. With review enabled (default) this stops at the
    review cut-point (no branding overlays, no final delivery encode) so the
    QC step inspects clean story frames and fix rounds stay cheap; the review
    step finalizes afterwards. With --no-review it renders straight through."""
    mp4 = proj / f"{slug}.mp4"
    if mp4.exists() and mp4.stat().st_size > 0 and args.from_step != "render":
        print(f"  [cached] {mp4.name}")
        return
    step_music(args, proj, slug)
    stop = None if args.no_review else "review-cut"
    run_step(_render_cmd(args, proj, slug, stop_after=stop))


def _review_src(proj):
    """Path of the pre-branding intermediate the reviewer should inspect."""
    marker = proj / "_work" / "review_src.txt"
    if marker.exists():
        p = Path(marker.read_text().strip())
        if p.exists():
            return p
    return None


def step_review(args, proj, slug):
    """STAGE 5.5 — automated human-like QC, run on the PRE-BRANDING cut:
    vision-LLM reviewers check every scene's frames against its narration
    (relevance, crops, empty screens, hook strength, watermarks, payoff
    panels) plus deterministic sweeps. On faults it applies safe script fixes
    (drop/replace bad panels + narration touch-up) and re-renders only the
    affected scenes, up to --review-rounds times. Afterwards it finalizes
    (branding overlays + single delivery encode) and runs a cheap
    deterministic guard on the final file."""
    out_json = proj / f"{slug}.json"
    mp4 = proj / f"{slug}.mp4"
    if args.no_review:
        return
    if mp4.exists() and mp4.stat().st_size > 0 and args.from_step not in \
            ("render", "review"):
        print(f"  [cached] {mp4.name} — skipping review")
        return

    src = _review_src(proj)
    if src is None:
        print("  ! no review intermediate found; rendering to review cut")
        run_step(_render_cmd(args, proj, slug, stop_after="review-cut"))
        src = _review_src(proj)
        if src is None:
            sys.exit("render did not produce a review intermediate")

    import json as _json
    passed = False
    rounds_log = []           # (round, faults_summary, fixes/action) history
    for round_i in range(args.review_rounds):
        before = out_json.read_text()
        r = subprocess.run([PY, str(PIPE / "review_video.py"), str(out_json),
                            "--video", str(src),
                            "--workdir", str(proj / "_work"),
                            "--apply-fixes"])
        # archive this round's report before the next round overwrites it,
        # so review.md history survives the loop (review_r1.md, review_r2.md…)
        for ext in (".json", ".md"):
            rp = proj / f"review{ext}"
            if rp.exists():
                (proj / f"review_r{round_i + 1}{ext}").write_text(
                    rp.read_text())
        n_high = n_med = 0
        rj = proj / "review.json"
        if rj.exists():
            _faults = _json.loads(rj.read_text())
            n_high = sum(1 for f in _faults if f.get("severity") == "high")
            n_med = sum(1 for f in _faults if f.get("severity") == "medium")
        if r.returncode == 0:
            print("  review: PASS")
            rounds_log.append((round_i + 1, n_high, n_med, "PASS"))
            passed = True
            break
        after = out_json.read_text()
        if after == before:
            print("  review: faults remain but no auto-fix applies; "
                  "see review.md — manual pass needed")
            rounds_log.append((round_i + 1, n_high, n_med,
                               "faults remain — no auto-fix applies"))
            break
        # figure out which scenes changed and re-render only those (cheap:
        # cached clips + copy-concat + copy-mix, still pre-branding)
        b, a = _json.loads(before), _json.loads(after)
        redo = [i + 1 for i, (sb, sa) in
                enumerate(zip(b["scenes"], a["scenes"])) if sb != sa]
        print(f"  review round {round_i+1}: re-rendering scenes {redo}")
        rounds_log.append((round_i + 1, n_high, n_med,
                           f"fixes applied — re-rendered scenes {redo}"))
        run_step(_render_cmd(args, proj, slug, redo=redo,
                             stop_after="review-cut"))
        src = _review_src(proj) or src
    else:
        print("  review: max rounds reached; check review.md before publishing")

    # append the round history to review.md: the report then reads as
    # flagged -> fixed -> re-checked, and states whether the last pass is clean
    rmd = proj / "review.md"
    if rmd.exists() and rounds_log:
        with open(rmd, "a") as fh:
            fh.write("\n## Fix-loop history\n\n")
            fh.write("| Round | High | Medium | Action |\n|---|---|---|---|\n")
            for rnd, nh, nm, action in rounds_log:
                fh.write(f"| {rnd} | {nh} | {nm} | {action} |\n")
            if passed:
                fh.write("\nFinal round re-reviewed CLEAN — earlier-round "
                         "faults confirmed fixed, no new faults introduced. "
                         "Proceeding to encode.\n")
            else:
                fh.write("\nFaults REMAIN after the fix loop (see the list "
                         "above). Per-round snapshots: review_r*.md.\n")

    # ---- STAGE 5.6: automated triage of surviving HIGH faults ----
    # replicates the manual adjudication pass (panel images + fault-time
    # frame + narration -> DROP contentless panels / DISMISS reviewer false
    # positives). On drops it re-renders the changed scenes and re-reviews
    # once to confirm the fixes held and nothing new was introduced.
    if not passed and not args.no_triage:
        rj = proj / "review.json"
        n_high = 0
        if rj.exists():
            n_high = sum(1 for f in _json.loads(rj.read_text())
                         if f.get("severity") == "high")
        if n_high:
            print(f"  triage: adjudicating {n_high} surviving HIGH fault(s)...")
            r = subprocess.run(
                [PY, str(PIPE / "review_triage.py"), str(out_json),
                 "--video", str(src), "--workdir", str(proj / "_work")],
                capture_output=True, text=True)
            print(r.stdout, end="")
            if r.stderr.strip():
                print(r.stderr[-800:], end="")
            redo = []
            for line in r.stdout.splitlines():
                if line.startswith("TRIAGE_REDO_SCENES="):
                    redo = [int(x) for x in
                            line.split("=", 1)[1].split(",") if x]
            if redo:
                print(f"  triage: re-rendering scenes {redo} and "
                      f"re-reviewing to confirm...")
                run_step(_render_cmd(args, proj, slug, redo=redo,
                                     stop_after="review-cut"))
                src = _review_src(proj) or src
                r2 = subprocess.run(
                    [PY, str(PIPE / "review_video.py"), str(out_json),
                     "--video", str(src),
                     "--workdir", str(proj / "_work")])
                # archive the post-triage confirmation pass
                for ext in (".json", ".md"):
                    rp = proj / f"review{ext}"
                    if rp.exists():
                        (proj / f"review_posttriage{ext}").write_text(
                            rp.read_text())
                if r2.returncode == 0:
                    passed = True
                else:
                    # confirmation still shows HIGHs: triage them once more
                    # (drops may have shifted pans; classic variance flags) —
                    # dismiss/drop what it can, no further re-render round
                    subprocess.run(
                        [PY, str(PIPE / "review_triage.py"), str(out_json),
                         "--video", str(src),
                         "--workdir", str(proj / "_work")])
            if not passed and rj.exists():
                left = sum(1 for f in _json.loads(rj.read_text())
                           if f.get("severity") == "high")
                if left == 0:
                    print("  triage: all surviving HIGH faults resolved "
                          "(dropped or dismissed) — proceeding to encode")
                    passed = True
                    with open(proj / "review.md", "a") as fh:
                        fh.write("\nAutomated triage resolved every "
                                 "surviving HIGH fault — proceeding to "
                                 "encode.\n")

    # ---- encode gate: don't spend the expensive finalize on a cut that
    # still carries HIGH faults — a manual pass (or --encode-anyway) decides.
    if not passed and not getattr(args, "encode_anyway", False):
        remaining_high = 0
        rj = proj / "review.json"
        if rj.exists():
            remaining_high = sum(
                1 for f in _json.loads(rj.read_text())
                if f.get("severity") == "high")
        if remaining_high:
            print(f"  review: {remaining_high} HIGH fault(s) remain — "
                  "STOPPING before the delivery encode.")
            print("  fix manually (edit the script JSON, delete affected "
                  "_work/cNNN.mp4) then re-run: --from review")
            print("  or force the encode: --encode-anyway")
            # remove stale final-guard reports from a previous successful
            # run: they no longer describe any existing delivered file and
            # would skew review_final-vs-review deltas (gap-analyst)
            for ext in (".json", ".md"):
                (proj / f"review_final{ext}").unlink(missing_ok=True)
            return

    # ---- finalize: branding overlays + the single delivery encode ----
    print("  finalizing (branding + delivery encode)...")
    run_step(_render_cmd(args, proj, slug))

    # ---- cheap deterministic guard on the final pixels ----
    # catches encode/overlay-stage breakage (blank/empty frames) that the
    # pre-branding review can't see. No LLM cost.
    r = subprocess.run([PY, str(PIPE / "review_video.py"), str(out_json),
                        "--workdir", str(proj / "_work"), "--det-only",
                        "--out", str(proj / "review_final")])
    if r.returncode != 0:
        print("  !! FINAL-VIDEO GUARD FAILED: blank/empty frames in the "
              "delivered file — see review_final.md. This indicates a "
              "render-stage bug, NOT a panel problem. Do not publish as-is.")


def step_extras(args, proj, slug):
    run_step([PY, str(PIPE / "make_thumbs.py"), str(proj / f"{slug}.json")])


def step_review_thumbs(args, proj, slug):
    """STAGE 6d — human-like QC of the thumbnails (deterministic format/
    sharpness/palette checks + vision-LLM feed-size/focal/artifact judgment).
    On HIGH faults: retry 1 regenerates the offending thumb (fresh AI hero),
    retry 2 gives up and leaves the report for a manual pass."""
    if args.no_review:
        return
    out_json = proj / f"{slug}.json"
    if not (proj / "thumbs").exists():
        print("  ! no thumbs dir; skipping thumbnail review")
        return
    for round_i in range(2):
        r = subprocess.run([PY, str(PIPE / "review_thumbs.py"),
                            str(out_json)])
        if r.returncode == 0:
            print("  thumbnail review: PASS")
            return
        if round_i == 0:
            # find which thumbs drew HIGH faults and regenerate just those
            import json as _json
            try:
                faults = _json.loads((proj / "review_thumbs.json").read_text())
            except Exception:
                faults = []
            bad = sorted({f.get("item", "").replace("_final", "")
                          for f in faults
                          if f.get("severity") == "high" and f.get("item")})
            if not bad:
                break
            for name in bad:
                print(f"  regenerating {name} (fresh AI hero attempt)...")
                # drop the cached hero so make_thumbs re-runs the image edit
                hero = proj / "thumbs" / f"{name}_hero.png"
                hero.unlink(missing_ok=True)
                subprocess.run([PY, str(PIPE / "make_thumbs.py"),
                                str(out_json), "--only", name])
    print("  thumbnail review: faults remain — see review_thumbs.md before "
          "uploading")


def _build_short(args, proj, slug, start_scene=None):
    mp4 = proj / f"{slug}.mp4"
    short = proj / f"{slug}_short.mp4"
    cmd = [PY, str(PIPE / "make_short.py"), str(mp4),
           "--duration", str(args.short_duration)]
    if start_scene is not None:
        cmd += ["--start-scene", str(start_scene)]
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd)
    return short if r.returncode == 0 and short.exists() else None


def step_short(args, proj, slug):
    """Build a native 9:16 Short from the project assets (emotional-peak
    scene window, panel-cropped visuals, kinetic captions)."""
    if args.no_short:
        return None
    mp4 = proj / f"{slug}.mp4"
    short = proj / f"{slug}_short.mp4"
    if not mp4.exists():
        print("  ! no rendered video yet; skipping short")
        return None
    if short.exists() and short.stat().st_size > 0 and args.from_step != "short":
        print(f"  [cached] {short.name}")
        return short
    out = _build_short(args, proj, slug, args.short_start_scene)
    if out is None:
        print("  ! short generation failed (non-fatal); continuing")
    return out


def step_review_short(args, proj, slug):
    """STAGE 6c — human-like QC of the Short (deterministic format/loudness/
    safe checks + vision-LLM hook/safe-zone/payoff judgment). On HIGH faults
    it rebuilds the Short from the next-best scene window (ranked by
    make_short --list-windows), up to 2 retries."""
    if args.no_review or args.no_short:
        return
    out_json = proj / f"{slug}.json"
    short = proj / f"{slug}_short.mp4"
    mp4 = proj / f"{slug}.mp4"
    if not short.exists():
        print("  ! no short; skipping short review")
        return
    tried = set()
    meta = proj / "_short_work" / "window.json"
    for round_i in range(3):
        r = subprocess.run([PY, str(PIPE / "review_short.py"),
                            str(out_json)])
        if r.returncode == 0:
            print("  short review: PASS")
            return
        if round_i == 2 or args.short_start_scene is not None:
            break
        # remember the failed window's start, rebuild from the next-best
        import json as _json
        try:
            tried.add(_json.loads(meta.read_text())["scenes"][0])
        except Exception:
            pass
        lw = subprocess.run([PY, str(PIPE / "make_short.py"), str(mp4),
                             "--duration", str(args.short_duration),
                             "--list-windows"],
                            capture_output=True, text=True)
        try:
            ranked = _json.loads(lw.stdout.strip().splitlines()[-1])
        except Exception:
            break
        nxt = next((s for s in ranked if s not in tried), None)
        if nxt is None:
            break
        print(f"  short review round {round_i+1}: rebuilding from "
              f"scene {nxt}")
        if _build_short(args, proj, slug, nxt) is None:
            break
    print("  short review: faults remain — see review_short.md before "
          "uploading")


def step_package(args, proj, slug):
    import json
    data = json.loads((proj / f"{slug}.json").read_text())
    title_words = args.series + f" Chapter {args.chapter} Recap"
    # cold-open hook — tolerate both this pipeline's schema (scenes[].narration)
    # and the studio's (top-level "hook" / segments[].narration/caption).
    scenes = data.get("scenes") or data.get("segments") or []
    hook = (data.get("hook")
            or (scenes[0].get("narration") or scenes[0].get("caption")
                if scenes else "")
            or f"{args.series} chapter {args.chapter}")
    doc = proj / "upload_package.md"
    meta = gateway.llm_text(
        f"You write YouTube metadata for a manga/manhwa recap channel. Video: "
        f"{title_words}. Cold-open narration: \"{hook}\"\n"
        f"Write:\n1. Three title options (<70 chars, curiosity-gap, include series name)\n"
        f"2. A 150-word description (hook first line, no timestamps)\n"
        f"3. 12 comma-separated tags\nUse markdown headings.")
    # Required background-music attribution (freetouse free license). Prefer the
    # string stamped onto the script by fetch_music.py; fall back to the sidecar
    # credit file. Always surfaced in the description so it can't be forgotten.
    credit = data.get("music_attribution")
    if not credit:
        cf = proj / "music.credit.txt"
        credit = cf.read_text().strip() if cf.exists() else None
    attribution_block = (
        f"## Music attribution (paste into the video description)\n\n"
        f"```\n{credit}\n```\n\n" if credit else "")

    # --- asset manifest: point at every deliverable, flag what's missing ---
    def _find(*globs):
        for g in globs:
            hits = sorted(proj.glob(g))
            if hits:
                return hits[0]
        return None

    long_mp4 = proj / f"{slug}.mp4"
    short_mp4 = proj / f"{slug}_short.mp4"
    srt = proj / f"{slug}.srt"
    thumb = _find("thumbs/*_final.jpg", "thumbs/*.jpg", "thumbs/*.png")

    def _row(label, path):
        ok = path and Path(path).exists()
        mark = "✅" if ok else "⬜ (missing)"
        loc = str(Path(path).relative_to(proj)) if path else "—"
        return f"| {label} | `{loc}` | {mark} |"

    manifest = (
        "## Assets (everything this run produced)\n\n"
        "| Asset | Path (under project dir) | Ready |\n"
        "|---|---|---|\n"
        + _row("Long video (16:9)", long_mp4) + "\n"
        + _row("Short (9:16, peak-scene cut)", short_mp4) + "\n"
        + _row("Subtitles", srt) + "\n"
        + _row("Thumbnail", thumb) + "\n"
        + f"\n_Project dir: `{proj}`_\n\n")

    short_block = ""
    if short_mp4.exists():
        short_block = (
            "## Short (post separately, links back to the long video)\n\n"
            f"- File: `{short_mp4.name}` (1080×1920, burned captions + "
            "\"▶ FULL VIDEO\" end card)\n"
            "- Title: reuse the hook line + `#Shorts` (e.g. "
            f"\"{title_words} #Shorts\")\n"
            "- Description: 1 hook line + link to the long video + 3-5 hashtags\n"
            "- Post 2-4 h before or after the long video for cross-traffic\n\n")

    # --- QC summary: one verdict line per reviewer, straight from their
    # reports, so the human sees all three checks in one place ---
    def _qc_row(label, base):
        j = proj / f"{base}.json"
        if not j.exists():
            return f"| {label} | — | not run |"
        try:
            fl = json.loads(j.read_text())
        except Exception:
            return f"| {label} | — | unreadable report |"
        nh = sum(1 for f in fl if f.get("severity") == "high")
        nm = sum(1 for f in fl if f.get("severity") == "medium")
        verdict = "PASS ✅" if nh == 0 else f"{nh} HIGH ⚠️"
        return f"| {label} | `{base}.md` | {verdict} ({nm} medium) |"

    qc_block = (
        "## QC verdicts (automated human-like review)\n\n"
        "| Check | Report | Verdict |\n|---|---|---|\n"
        + _qc_row("Long video (pre-branding cut)", "review") + "\n"
        + _qc_row("Long video (final pixels guard)", "review_final") + "\n"
        + _qc_row("Short", "review_short") + "\n"
        + _qc_row("Thumbnails", "review_thumbs") + "\n"
        + "\nRead every report's `## Human spot-checks` section before "
          "uploading.\n\n")

    doc.write_text(
        f"# Upload package — {title_words}\n\n"
        f"{manifest}"
        f"{qc_block}"
        f"{meta}\n\n"
        f"{attribution_block}"
        f"{short_block}"
        "## Long-video checklist\n"
        + ("- [ ] Paste the music attribution above into the description "
           "(required by the freetouse free license)\n" if credit else "")
        +
        "- [ ] Upload the long video; set the thumbnail (thumbs/*_final.jpg)\n"
        "- [ ] Upload subtitles (.srt) : Subtitles > English > Upload (with timing)\n"
        "- [ ] End screen over the outro: 1 video element + 1 subscribe element\n"
        "- [ ] Post the Short; put the long-video link in its description\n"
        "- [ ] Mark \"Altered content / synthetic media: Yes\" (AI narration)\n"
        "- [ ] COPYRIGHT: confirm you have rights / a transformative-commentary\n"
        "      basis for this series before publishing; credit the source and\n"
        "      link the official release; honor takedowns. Full chapter dumps\n"
        "      are the weakest fair-use position.\n")
    print(f"  [doc] {doc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="chapter URL (gallery-dl / mangadex)")
    ap.add_argument("--folder", help="local folder of page images (manual mode)")
    ap.add_argument("--img-url", action="append", default=[])
    ap.add_argument("--backend",
                    choices=["auto", "folder", "gallerydl", "mangadex", "requests"],
                    default="auto")
    ap.add_argument("--series", help="series name (required unless --project)")
    ap.add_argument("--chapter", help="chapter number/label")
    ap.add_argument("--mode", choices=["auto", "manga", "webtoon"], default="auto")
    ap.add_argument("--split-guard", choices=["none", "blob", "yolo"],
                    default="yolo",
                    help="webtoon forced-cut guard (adopted from exp-004): "
                         "veto cut rows through detected text/dense art, snap "
                         "gutter edges off SFX tips, keep beat panels, and "
                         "majority-vote the auto mode so a cover page cannot "
                         "disable tile stitching. Default yolo (manga109 "
                         "text detector, ~10MB ONNX cached on first use; "
                         "falls back to blob then legacy cuts on any error). "
                         "Opt-out: --split-guard none = pre-exp-004 "
                         "byte-identical segmentation.")
    ap.add_argument("--channel", default=None,
                    help="channel name for the scriptwriter prompt "
                         "(default: the name in channel_state.json)")
    ap.add_argument("--clean-bubbles", nargs="?", const="cv",
                    choices=["cv", "gemini"], default=None,
                    help="enable bubble cleanup (default tier cv)")
    ap.add_argument("--clean-watermarks", action="store_true",
                    help="enable scanlation watermark removal (exp-003)")
    ap.add_argument("--project", help="existing output/<slug> dir (resume)")
    ap.add_argument("--from", dest="from_step", choices=STEPS,
                    help="resume from this step")
    ap.add_argument("--res", default="1440p", choices=["1080p", "1440p", "2160p"])
    ap.add_argument("--karaoke", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-review", action="store_true",
                    help="skip the automated vision-QC review step")
    ap.add_argument("--review-rounds", type=int, default=3,
                    help="max review->fix->re-render loops (default 3)")
    ap.add_argument("--encode-anyway", action="store_true",
                    help="run the final delivery encode even when HIGH "
                         "review faults remain after the fix loop (default: "
                         "stop and wait for a manual pass)")
    ap.add_argument("--no-triage", action="store_true",
                    help="skip the automated HIGH-fault triage (stage 5.6: "
                         "vision adjudication that drops contentless panels "
                         "and dismisses reviewer false positives)")
    ap.add_argument("--music-mood", default=None,
                    help="force the background-music mood/search term (e.g. "
                         "Epic, Cinematic, Sad) instead of deriving it from the "
                         "script. Passed to fetch_music.py before render.")
    ap.add_argument("--no-branding", action="store_true")
    ap.add_argument("--layout", choices=["seq", "smart"], default="smart",
                    help="panel_render layout mode: 'smart' (default) "
                         "composites 3+ panel scenes into narration-synced "
                         "grids/stacks; 'seq' = one panel at a time. Promoted "
                         "to default after the ch3 production trial (0 HIGH "
                         "faults on smart scenes; improvements/ledger.json "
                         "gap-002).")
    ap.add_argument("--text-aware", action="store_true",
                    help="pass --text-aware to panel_render (exp-009 phone-"
                         "readability framing: measured text-line boxes "
                         "drive crops and smart-layout cell sizing). Off by "
                         "default.")
    ap.add_argument("--freesound-key", default=None,
                    help="Freesound API token for auto-fetching content SFX "
                         "(door slams, gasps, etc.) tagged in the script. Falls "
                         "back to $FREESOUND_API_KEY. Without either, SFX are "
                         "skipped.")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--yes", action="store_true", help="skip the script review pause")
    ap.add_argument("--no-verify-panels", action="store_true",
                    help="skip the panel<->narration relevance verifier that "
                         "runs after the script step (verify_panels.py; also "
                         "MANGA_VERIFY_PANELS=0). Disables ALL verifier tiers, "
                         "including the quote-payoff pass.")
    ap.add_argument("--no-payoff-check", action="store_true",
                    help="skip only the quote-payoff tier (tier 1.5) of the "
                         "script verifier: the OCR cross-check that every "
                         "narration quote lands on a kept panel (exp-005; on "
                         "by default; also MANGA_VERIFY_PAYOFF=0). Other "
                         "verifier tiers still run unless --no-verify-panels "
                         "is given.")
    ap.add_argument("--no-short", action="store_true",
                    help="skip cutting the 9:16 Short from the long video")
    ap.add_argument("--short-duration", type=float, default=60.0,
                    help="Short target length in seconds; the actual length "
                         "snaps to scene boundaries (default 60)")
    ap.add_argument("--short-start-scene", type=int, default=None,
                    help="force the Short window to start at this scene index "
                         "(default: auto-pick the emotional peak)")
    ap.add_argument("--auto", action="store_true",
                    help="fully unattended: implies --yes (no script-review "
                         "pause) so research→render→short→package runs hands-off")
    ap.add_argument("--no-new-series", action="store_true",
                    help="with channel continuity: don't auto-promote a new "
                         "series when the current one is exhausted (just stop)")
    ap.add_argument("--discover", nargs="?", const=10, type=int, metavar="TOP",
                    help="STAGE 0: show trending manga ranked for recap-video "
                         "suitability (AniList) and exit. Pick one, then re-run "
                         "with --series/--chapter pointed at a legit source.")
    args = ap.parse_args()

    if args.auto:
        args.yes = True   # unattended: never block on the script-review pause

    # STAGE 0 — research/discovery. Advisory: it decides the TOPIC; you still
    # supply a legitimate --url/--folder for acquisition. Runs and exits.
    if args.discover is not None:
        sys.path.insert(0, str(PIPE))
        import research_trends as rt
        ranked = rt.discover(limit=max(args.discover + 5, 25))
        rt._print_ranked(ranked, args.discover)
        top = ranked[0]
        print(f"\nTo make the #1 pick ({top.title}):")
        print(f"  ./venv/bin/python3 manga.py --series \"{top.title}\" "
              f"--chapter 1 --url <legit-chapter-url> --mode auto")
        print("\n(Research chooses the topic; you still point download at a "
              "source you have the rights to — MangaDex-permitted title, "
              "official app, or your own pages.)")
        return

    # CHANNEL CONTINUITY — when series/chapter aren't given explicitly, ask the
    # channel roster what's next (single-series, in-order). This is what keeps
    # the channel consistent instead of topic-hopping on live trends.
    if not args.channel:
        sys.path.insert(0, str(PIPE))
        import channel as chan
        args.channel = chan.load_state().get("channel", "Manga Recap")

    used_channel = False
    if not args.project and not args.series:
        sys.path.insert(0, str(PIPE))
        import channel as chan
        st = chan.load_state()
        plan = chan.plan_next(st, auto_add=not args.no_new_series)
        if plan.get("is_new_series"):
            chan.save_state(st)   # persist auto-promotion
        if not plan.get("series"):
            sys.exit(f"channel: nothing to make — {plan['reason']}\n"
                     f"  set one with: {PY} pipeline/channel.py set --title \"...\"")
        if plan.get("caught_up") and not args.chapter:
            sys.exit(f"channel: caught up on '{plan['series']}' "
                     f"(latest known chapter recapped). Wait for a new chapter, "
                     f"or pass --chapter to force one. {plan['reason']}")
        args.series = plan["series"]
        args.chapter = args.chapter or str(plan["chapter"])
        used_channel = True
        tag = " [NEW SERIES]" if plan["is_new_series"] else ""
        print(f"Channel says next: {args.series} ch {args.chapter}{tag}")
        print(f"  why: {plan['reason']}")

    # resolve project dir + slug
    if args.project:
        proj = Path(args.project).resolve()
        slug = proj.name
        # backfill series/chapter from source metadata if present
        sj = proj / f"{slug}.json"
        if sj.exists():
            import json
            src = json.loads(sj.read_text()).get("source", {})
            args.series = args.series or src.get("series", slug)
            args.chapter = args.chapter or src.get("chapter", "?")
        args.series = args.series or slug
        args.chapter = args.chapter or "?"
    else:
        if not args.series or not args.chapter:
            sys.exit("--series and --chapter are required (or use --project, "
                     "or set a channel series: pipeline/channel.py set)")
        slug = f"{slugify(args.series)}-ch{args.chapter}"
        proj = (HERE / "output" / slug).resolve()
    proj.mkdir(parents=True, exist_ok=True)
    print(f"Project: {proj}  (slug {slug})")

    start = STEPS.index(args.from_step) if args.from_step else 0

    if start <= STEPS.index("download"):
        print("\n=== DOWNLOAD ===");  step_download(args, proj)
    if start <= STEPS.index("segment"):
        print("\n=== SEGMENT ===");   step_segment(args, proj)
    if start <= STEPS.index("clean"):
        if args.clean_bubbles:
            print("\n=== CLEAN BUBBLES ==="); step_clean(args, proj)
        if args.clean_watermarks:
            print("\n=== CLEAN WATERMARKS ==="); step_clean_watermarks(args, proj)
    if start <= STEPS.index("script"):
        print("\n=== SCRIPT ===");    step_script(args, proj, slug)
    if start <= STEPS.index("render"):
        print("\n=== RENDER ===");    step_render(args, proj, slug)
    if start <= STEPS.index("review"):
        print("\n=== REVIEW ===");    step_review(args, proj, slug)
    if start <= STEPS.index("extras"):
        print("\n=== EXTRAS ===");    step_extras(args, proj, slug)
    if start <= STEPS.index("review_thumbs"):
        print("\n=== REVIEW THUMBS ==="); step_review_thumbs(args, proj, slug)
    if start <= STEPS.index("short"):
        print("\n=== SHORT ===");     step_short(args, proj, slug)
    if start <= STEPS.index("review_short"):
        print("\n=== REVIEW SHORT ==="); step_review_short(args, proj, slug)
    if start <= STEPS.index("package"):
        print("\n=== PACKAGE ===");   step_package(args, proj, slug)

    # record this chapter as recapped so the channel advances to the next one
    # — but only when a deliverable exists (the review encode gate may have
    # stopped before finalize; the channel must not advance past a chapter
    # that has no video)
    delivered = (proj / f"{slug}.mp4")
    if not (delivered.exists() and delivered.stat().st_size > 0):
        if used_channel:
            print("  [channel] no delivered mp4 (encode gate?) — NOT marking "
                  "chapter done; re-run --from review after fixing")
        used_channel = False
    if used_channel and args.chapter not in (None, "?"):
        try:
            import channel as chan
            st = chan.load_state()
            chan.mark_done(st, args.series, args.chapter)
            chan.save_state(st)
            print(f"  [channel] recorded {args.series} ch {args.chapter} as done")
        except Exception as e:
            print(f"  [channel] could not record progress: {e}")

    # feed this run's surviving faults into the improve-train evidence pool
    # and nudge when a training round looks worthwhile (recurring fault types
    # or several highs the fix loop couldn't clear). Never fails the run.
    try:
        import improve_nudge
        improve_nudge.run(str(proj))
    except Exception as e:
        print(f"  [improve] nudge skipped: {e}")

    print(f"\nALL DONE -> {proj}")
    print(f"  long : {proj/(slug+'.mp4')}")
    sp = proj / (slug + "_short.mp4")
    if sp.exists():
        print(f"  short: {sp}")
    print(f"  package (upload details): {proj/'upload_package.md'}")


if __name__ == "__main__":
    main()
