"""User settings, stored as JSON in ~/Library/Application Support/mac-sonos."""
import json
from pathlib import Path

PATH = Path.home() / "Library" / "Application Support" / "mac-sonos" / "config.json"

DEFAULTS = {
    "rooms": ["Office"],
    "volume": 15,
    "format": "mp3",
    "device": "BlackHole 2ch",
    "port": 8000,
    "autostart_stream": True,  # menu bar app: start streaming when it opens
    "track_in_bar": False,     # menu bar app: show the current track next to the icon
    "play_target": None,       # room whose group favorites, playlists and URLs play on (None: the Mac's first room)
    "radio_urls": [],          # saved stream URLs: [{"title": ..., "url": ...}]
    "language": "auto",        # "auto" follows the system; or a code such as "pt" (see mac_sonos/locales)
}


def load(path=None):
    try:
        saved = json.loads(Path(path or PATH).read_text())
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save(cfg, path=None):
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: cfg[k] for k in DEFAULTS if k in cfg}, indent=2) + "\n")
