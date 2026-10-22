#!/bin/sh
# One-time setup: ffmpeg, BlackHole, SwitchAudioSource (auto-switch Mac output) and a venv with SoCo.
set -e
cd "$(dirname "$0")"
brew install ffmpeg blackhole-2ch switchaudio-osx
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "Done. BlackHole may need a reboot (or: sudo killall coreaudiod) before it shows up."
