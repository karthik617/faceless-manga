#!/usr/bin/env bash
set -e
CH="google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars"
BASE=/home/kmyadav/faceless-manga/brand
cd "$BASE"

# transparent PNG render: shot(svg, out, w, h)
shot(){ $CH --force-device-scale-factor=1 --default-background-color=00000000 \
  --screenshot="$2" --window-size="$3,$4" "file://$1" >/dev/null 2>&1; }
# opaque render (banner/avatar already fill their own bg)
shotbg(){ $CH --force-device-scale-factor=1 \
  --screenshot="$2" --window-size="$3,$4" "file://$1" >/dev/null 2>&1; }

# --- icons (transparent) ---
shot "$BASE/logo/panelbreak-icon.svg"       "$BASE/logo/panelbreak-icon-1024.png"      1024 1024
shot "$BASE/logo/panelbreak-icon.svg"       "$BASE/logo/panelbreak-icon-512.png"        512  512
shot "$BASE/logo/panelbreak-icon.svg"       "$BASE/logo/panelbreak-favicon-32.png"       32   32
shot "$BASE/logo/panelbreak-icon-ink.svg"   "$BASE/logo/panelbreak-icon-ink-1024.png"  1024 1024

# --- horizontal lockups (transparent, 520x120 aspect) ---
shot "$BASE/logo/panelbreak-lockup.svg"     "$BASE/logo/panelbreak-lockup-2080.png"    2080  480
shot "$BASE/logo/panelbreak-lockup-ink.svg" "$BASE/logo/panelbreak-lockup-ink-2080.png" 2080 480

# --- avatar (opaque, YT wants 800x800) ---
shotbg "$BASE/social/panelbreak-avatar.svg" "$BASE/social/panelbreak-avatar-800.png"    800  800

# --- banner (opaque, exact 2560x1440) ---
shotbg "$BASE/social/panelbreak-banner.svg" "$BASE/social/panelbreak-banner-2560x1440.png" 2560 1440

echo "RENDER COMPLETE"
ls -la "$BASE"/logo/*.png "$BASE"/social/*.png
