#!/usr/bin/env bash
# render.sh — memory-safe wrapper for pipeline/panel_render.py
#
# Fixes the two failure modes of the ch4 OOM incident (Sep 10):
#   1. systemd-oomd killed the whole terminal cgroup when one smart-layout
#      ffmpeg peaked -> run the render in its OWN systemd scope with
#      MemoryHigh (throttle, don't kill) and MemoryMax as a hard ceiling.
#   2. The kill left a half-written clip (c028.mp4, "moov atom not found")
#      that the resume logic would have silently reused -> ffprobe-validate
#      every existing scene clip in the workdir and delete corrupt ones
#      before starting.
#
# Usage:
#   ./render.sh <script.json> [panel_render.py args...]
#
# Defaults:
#   --workdir <script_dir>/_work   (resume-safe; pass your own to override)
#   MemoryHigh=16G MemoryMax=20G   (override: FM_MEM_HIGH / FM_MEM_MAX)
#   FM_FFMPEG_THREADS              (optional; caps ffmpeg filter threads in
#                                   the smart-layout encodes, lowers peak RAM)
#
# Examples:
#   ./render.sh output/the-world-after-the-fall-ch4/the-world-after-the-fall-ch4.json --karaoke
#   FM_MEM_HIGH=12G FM_FFMPEG_THREADS=2 ./render.sh output/ch5/ch5.json

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <script.json> [panel_render.py args...]" >&2
    exit 1
fi

SCRIPT_JSON=$1
shift
if [[ ! -f "$SCRIPT_JSON" ]]; then
    echo "error: script not found: $SCRIPT_JSON" >&2
    exit 1
fi

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$(dirname -- "$SCRIPT_JSON")" && pwd)

MEM_HIGH=${FM_MEM_HIGH:-16G}
MEM_MAX=${FM_MEM_MAX:-20G}

# ---- workdir: honor an explicit --workdir, else default to <project>/_work
WORKDIR=""
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
    if [[ "${ARGS[$i]}" == "--workdir" && $((i + 1)) -lt ${#ARGS[@]} ]]; then
        WORKDIR=${ARGS[$((i + 1))]}
    elif [[ "${ARGS[$i]}" == --workdir=* ]]; then
        WORKDIR=${ARGS[$i]#--workdir=}
    fi
done
if [[ -z "$WORKDIR" ]]; then
    WORKDIR="$PROJECT_DIR/_work"
    ARGS+=("--workdir" "$WORKDIR")
fi
mkdir -p "$WORKDIR"

# ---- pre-flight: delete corrupt scene clips so resume never reuses them
echo "render.sh: validating cached scene clips in $WORKDIR ..."
CORRUPT=0
shopt -s nullglob
for clip in "$WORKDIR"/c[0-9][0-9][0-9].mp4; do
    if ! ffprobe -v error -show_entries format=duration \
            -of csv=p=0 "$clip" >/dev/null 2>&1; then
        echo "  corrupt (deleting): $clip"
        rm -f "$clip"
        CORRUPT=$((CORRUPT + 1))
    fi
done
shopt -u nullglob
if [[ $CORRUPT -eq 0 ]]; then
    echo "  all cached clips OK"
else
    echo "  removed $CORRUPT corrupt clip(s); they will be re-rendered"
fi

# ---- free-memory advisory
AVAIL_KB=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [[ $AVAIL_GB -lt 12 ]]; then
    echo "render.sh: WARNING — only ${AVAIL_GB}G RAM available. Consider" \
         "closing Chrome/VS Code; the smart-layout ffmpeg pass can peak >10G." >&2
fi

# ---- run inside a dedicated systemd scope with memory limits.
# MemoryHigh throttles (kernel reclaims aggressively but does NOT kill);
# MemoryMax is the hard cap — if it is ever hit only THIS scope dies, the
# terminal and other apps survive, and the workdir resume covers the restart.
RUNNER=(python3 "$REPO_DIR/pipeline/panel_render.py" "$SCRIPT_JSON" "${ARGS[@]}")

if command -v systemd-run >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    echo "render.sh: MemoryHigh=$MEM_HIGH MemoryMax=$MEM_MAX (scope-isolated)"
    exec systemd-run --user --scope --same-dir --collect \
        -p "MemoryHigh=$MEM_HIGH" -p "MemoryMax=$MEM_MAX" \
        --setenv=FM_FFMPEG_THREADS="${FM_FFMPEG_THREADS:-}" \
        "${RUNNER[@]}"
else
    echo "render.sh: systemd-run unavailable — running without memory caps" >&2
    exec "${RUNNER[@]}"
fi
