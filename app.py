"""Entry point of the Mac Sonos.app bundle (see setup.py / build-app.sh)."""
import os
import sys

# A bundle launched from Finder or at login gets a minimal PATH; ffmpeg and SwitchAudioSource are in Homebrew.
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

from mac_sonos.cli import main

args = [a for a in sys.argv[1:] if not a.startswith("-psn")]  # Finder adds -psn_*
sys.argv = ["mac_sonos"] + (args or ["menubar"])
main()
