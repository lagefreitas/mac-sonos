#!/bin/sh
# Opened by Terminal at login (see autostart.sh). Retries until Wi-Fi/Sonos/BlackHole are ready.
cd "$(dirname "$0")"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
while true; do
  .venv/bin/python mac_sonos.py run Office --volume 15
  sleep 10
done
