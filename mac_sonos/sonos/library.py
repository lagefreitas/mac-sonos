"""What can be played: Sonos favorites, Sonos playlists, a group's queue, and radio URLs.

Everything here talks to the speakers over the network and can take seconds (a Spotify playlist is slow to
queue), so it runs on its own worker thread; the UI only reads the cached results and queues jobs.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger("mac_sonos")

RADIO, TRACK, PLAYLIST = "radio", "track", "playlist"
RADIO_SCHEMES = ("x-sonosapi-stream:", "x-sonosapi-radio:", "x-rincon-mp3radio:", "x-sonosapi-hls:")
LIBRARY_EVERY = 300.0   # seconds between full reloads of favorites and playlists
QUEUE_EVERY = 15.0      # seconds between reloads of a watched queue
QUEUE_LIMIT = 100       # queue entries read at a time, around the current track
QUEUE_LEAD = 10         # of which this many come before it


@dataclass(frozen=True)
class Favorite:
    title: str
    kind: str                                    # RADIO, TRACK or PLAYLIST
    item: object = field(default=None, compare=False, repr=False)   # the SoCo object to play


@dataclass(frozen=True)
class QueueEntry:
    index: int       # 0-based position in the queue
    title: str
    artist: str = ""


def _uri(item):
    resources = getattr(item, "resources", None) or []
    return resources[0].uri if resources else ""


def classify(favorite):
    """RADIO, TRACK or PLAYLIST for a favorite we know how to play; None for the rest (service hubs, artists
    without a URI...)."""
    ref = getattr(favorite, "reference", None)
    uri = _uri(favorite)
    if ref is None or not uri:
        return None
    kind = type(ref).__name__
    if uri.startswith(RADIO_SCHEMES) or kind == "DidlAudioBroadcast":
        return RADIO
    if kind == "DidlMusicTrack":
        return TRACK
    if kind in ("DidlPlaylistContainer", "DidlMusicAlbum") and _uri(ref):
        return PLAYLIST
    return None


def favorites_from(items):
    out = []
    for item in items:
        kind = classify(item)
        if kind:
            out.append(Favorite(item.title, kind, item))
    return out


def valid_stream_url(url):
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def default_title(url):
    return urlparse(url.strip()).netloc


# --- playing: plain functions over a coordinator zone, so they can be tested with fakes ---------------

def play_favorite(zone, favorite):
    item = favorite.item
    if favorite.kind == RADIO:
        zone.play_uri(_uri(item), meta=item.resource_meta_data, title=item.title)
    else:  # what "play now" does in the Sonos app: replace the queue and start it
        zone.clear_queue()
        first = zone.add_to_queue(item.reference)
        zone.play_from_queue(max(int(first) - 1, 0))


def play_playlist(zone, playlist):
    zone.clear_queue()
    first = zone.add_to_queue(playlist)
    zone.play_from_queue(max(int(first) - 1, 0))


def play_url(zone, url, title=""):
    zone.play_uri(url.strip(), title=title or default_title(url), force_radio=True)


class Library:
    def __init__(self, zone_for_group, any_zone, on_change=None, refresh_group=None):
        """zone_for_group(name) -> the SoCo coordinator of that group (or None); any_zone() -> any SoCo (or
        None); refresh_group(name) asks the monitor to re-read a group after we changed it."""
        self._zone_for_group, self._any_zone = zone_for_group, any_zone
        self._on_change, self._refresh_group = on_change, refresh_group
        self.favorites, self.playlists = [], []
        self.queues = {}               # coordinator name -> tuple of QueueEntry (a window of the queue)
        self.queue_totals = {}         # coordinator name -> length of the whole queue
        self.version = 0               # bumps whenever favorites, playlists or a queue change
        self._jobs = queue.Queue()
        self._watched = None           # coordinator whose queue we keep fresh
        self._position = 0             # its current track (1-based, 0: unknown): the window is read around it
        self._requested = None         # coordinator whose queue a job is already on its way to read
        self._next_library = self._next_queue = 0.0
        self._stop = threading.Event()
        self._thread = None

    # --- lifecycle ------------------------------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="library")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._jobs.put(None)
        if self._thread:
            self._thread.join(timeout=10)

    # --- requests from the UI (non-blocking) ----------------------------------------------------

    def watch(self, coordinator, position=0):
        """Keep this group's queue fresh around its current track (1-based `position`); reloads it at once when
        the group changes or the track leaves the window that was read."""
        self._position = position
        if coordinator != self._watched or (not self._covers(coordinator, position) and self._requested != coordinator):
            self._watched = self._requested = coordinator
            self._jobs.put(("queue", coordinator))

    def _covers(self, coordinator, position):
        entries = self.queues.get(coordinator)
        if not entries or position <= 0:
            return True   # nothing read yet (a job is on its way), or no current track to follow
        first, last = entries[0].index, entries[-1].index
        start = max(0, position - 1 - QUEUE_LEAD)
        end = min(position - 1 + QUEUE_LIMIT // 2, self.queue_totals.get(coordinator, last + 1) - 1)
        return first <= start and end <= last

    def reload(self):
        self._jobs.put(("library", None))

    def play_favorite(self, coordinator, favorite):
        self._jobs.put(("do", (coordinator, lambda z: play_favorite(z, favorite))))

    def play_playlist(self, coordinator, playlist):
        self._jobs.put(("do", (coordinator, lambda z: play_playlist(z, playlist))))

    def play_url(self, coordinator, url, title=""):
        self._jobs.put(("do", (coordinator, lambda z: play_url(z, url, title))))

    def play_queue_index(self, coordinator, index):
        self._jobs.put(("do", (coordinator, lambda z: z.play_from_queue(index))))

    def remove_from_queue(self, coordinator, index):
        self._jobs.put(("do", (coordinator, lambda z: z.remove_from_queue(index))))

    def clear_queue(self, coordinator):
        self._jobs.put(("do", (coordinator, lambda z: z.clear_queue())))

    # --- worker ---------------------------------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=1.0)
            except queue.Empty:
                job = None
            if self._stop.is_set():
                return
            try:
                self._step(job, time.time())
            except Exception:
                log.exception("library job failed")

    def _step(self, job, now):
        if job:
            kind, arg = job
            if kind == "library":
                self._load_library(now)
            elif kind == "queue":
                self._load_queue(arg, now)
            elif kind == "do":
                self._do(*arg, now)
        if now >= self._next_library and self._any_zone():
            self._load_library(now)
        if self._watched and now >= self._next_queue:
            self._load_queue(self._watched, now)

    def _bump(self):
        self.version += 1
        if self._on_change:
            self._on_change()

    def _load_library(self, now):
        self._next_library = now + LIBRARY_EVERY
        zone = self._any_zone()
        if zone is None:
            return
        favorites = favorites_from(zone.music_library.get_sonos_favorites(complete_result=True))
        playlists = list(zone.get_sonos_playlists(complete_result=True))
        changed = [(f.title, f.kind) for f in favorites] != [(f.title, f.kind) for f in self.favorites] or \
            [p.title for p in playlists] != [p.title for p in self.playlists]
        self.favorites, self.playlists = favorites, playlists
        if changed:   # only now: the menu rebuilds as soon as it sees the new version
            log.info("library: %d favorites, %d playlists", len(favorites), len(playlists))
            self._bump()

    def _load_queue(self, coordinator, now):
        self._next_queue = now + QUEUE_EVERY
        self._requested = None
        zone = self._zone_for_group(coordinator)
        if zone is None:
            return
        position = self._position if coordinator == self._watched else 0
        start = max(0, position - 1 - QUEUE_LEAD)
        items = zone.get_queue(start=start, max_items=QUEUE_LIMIT)
        entries = tuple(QueueEntry(start + i, getattr(t, "title", "") or "", getattr(t, "creator", "") or "")
                        for i, t in enumerate(items))
        total = getattr(items, "total_matches", None)
        total = int(total) if total is not None else start + len(entries)
        if entries != self.queues.get(coordinator) or total != self.queue_totals.get(coordinator):
            self.queues[coordinator], self.queue_totals[coordinator] = entries, total
            self._bump()

    def _do(self, coordinator, action, now):
        zone = self._zone_for_group(coordinator)
        if zone is None:
            log.warning("library action: group %s not found", coordinator)
            return
        try:
            action(zone)
        finally:  # whatever happened, look again: the state is probably different now
            if self._refresh_group:
                self._refresh_group(coordinator)
            self._load_queue(coordinator, now)
