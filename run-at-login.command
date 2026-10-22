#!/bin/sh
# Opened by Terminal at login (see autostart.sh): starts the menu bar app, which streams to the
# saved rooms and retries by itself until Wi-Fi/Sonos/BlackHole are ready.
cd "$(dirname "$0")"
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH
exec .venv/bin/python -m mac_sonos menubar
