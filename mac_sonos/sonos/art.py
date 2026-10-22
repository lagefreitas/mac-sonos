"""Album-art cache for the menu (files under ~/Library/Caches/mac-sonos/art)."""
import hashlib
import logging
import urllib.request
from pathlib import Path

log = logging.getLogger("mac_sonos")
CACHE = Path.home() / "Library" / "Caches" / "mac-sonos" / "art"


def path_for(url):
    return CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".img")


def cached(url):
    """The cached file for `url`, or None if it was not fetched yet."""
    if not url:
        return None
    p = path_for(url)
    return p if p.exists() else None


def fetch(url, timeout=5):
    """Download `url` into the cache (blocking); returns the path or None on failure."""
    if not url:
        return None
    p = path_for(url)
    if p.exists():
        return p
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = r.read(2_000_000)
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(p)
        return p
    except Exception:
        log.info("album art fetch failed: %s", url, exc_info=True)
        return None
