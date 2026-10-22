#!/bin/sh
# Renders tools/site/og.html to docs/og.png (1200x630), the image link previews show. Needs Google Chrome.
set -e
cd "$(dirname "$0")/.."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=1 --window-size=1200,630 \
  --screenshot="$PWD/docs/og.png" "file://$PWD/tools/site/og.html" >/dev/null 2>&1
sips -g pixelWidth -g pixelHeight docs/og.png | tail -2
