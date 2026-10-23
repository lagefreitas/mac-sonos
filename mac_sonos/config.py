"""User settings, stored as JSON in ~/Library/Application Support/mac-sonos."""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("mac_sonos")

PATH = Path.home() / "Library" / "Application Support" / "mac-sonos" / "config.json"

DEFAULTS = {
    "rooms": ["Office"],
    "volume": 15,
    "format": "mp3",
    "device": "BlackHole 2ch",
    "capture": "blackhole",    # "blackhole" (needs BlackHole) or "tap" (system audio; needs System Audio Recording)
    "mute_mac": True,          # with "tap": silence the Mac while it streams (BlackHole always does)
    "port": 8000,
    "autostart_stream": True,  # menu bar app: start streaming when it opens
    "track_in_bar": False,     # menu bar app: show the current track next to the icon
    "play_target": None,       # room whose group favorites, playlists and URLs play on (None: the Mac's first room)
    "radio_urls": [],          # saved stream URLs: [{"title": ..., "url": ...}]
    "scenes": [],              # saved scenes (see mac_sonos/scenes.py for the format)
    "media_keys": False,       # menu bar app: media and volume keys control the Sonos (needs Accessibility)
    "language": "auto",        # "auto" follows the system; or a code such as "pt" (see mac_sonos/locales)
}


_lock = threading.Lock()   # the menu and a scene's thread both save


def load(path=None):
    """The settings, with defaults for what is missing. An unreadable file is kept as config.json.bad (the next
    save would otherwise erase the scenes and radios it holds) and the defaults are used."""
    path = Path(path or PATH)
    try:
        raw = path.read_bytes()
    except OSError:
        return dict(DEFAULTS)
    try:
        saved = json.loads(raw)
        if not isinstance(saved, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        log.warning("config %s is unreadable (%s); using the defaults, a copy is kept as %s.bad", path, e, path.name)
        try:
            path.with_name(path.name + ".bad").write_bytes(raw)
        except OSError:
            log.warning("could not keep a copy of the unreadable config", exc_info=True)
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save(cfg, path=None):
    """Write to a temporary file and move it into place, so a crash never leaves half a file."""
    path = Path(path or PATH)
    text = json.dumps({k: cfg[k] for k in DEFAULTS if k in cfg}, indent=2) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
