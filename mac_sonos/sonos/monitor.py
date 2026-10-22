"""Watches the Sonos groups (what is playing) and sends them transport commands.

Events from the speakers (avTransport) only mark a group as dirty; its state is then read with plain
UPnP calls, which is simpler than parsing every event variant. Polling is the fallback (and refreshes
the position): fast while playing, slow when idle.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from . import discovery

log = logging.getLogger("mac_sonos")

PLAYING, PAUSED, STOPPED, TRANSITIONING = "PLAYING", "PAUSED_PLAYBACK", "STOPPED", "TRANSITIONING"
TOPOLOGY_EVERY = 10.0   # seconds between group-membership checks
REDISCOVER_EVERY = 120.0


def parse_hms(text):
    """'0:02:35' -> 155; anything else (empty, NOT_IMPLEMENTED) -> 0."""
    try:
        seconds = 0
        for part in str(text).split(":"):
            seconds = seconds * 60 + int(part)
        return seconds
    except ValueError:
        return 0


def format_hms(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_actions(actions):
    """'Set, Stop, Play' or ['Set', 'Stop'] -> frozenset({'set', 'stop'})."""
    if isinstance(actions, str):
        actions = actions.split(",")
    return frozenset(a.strip().lower() for a in actions if a.strip())


@dataclass(frozen=True)
class GroupInfo:
    coordinator: str
    members: tuple          # unique room names, coordinator first
    state: str
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    uri: str = ""
    channel: str = ""       # radio stream name, e.g. "Mac"
    duration: int = 0       # seconds, 0 for streams
    actions: frozenset = frozenset()
    # volatile: they change every second and must not count as a change
    position: int = field(default=0, compare=False)
    read_at: float = field(default=0.0, compare=False)

    def current_position(self, now=None):
        if self.state != PLAYING:
            return self.position
        elapsed = int((now or time.time()) - self.read_at)
        pos = self.position + elapsed
        return min(pos, self.duration) if self.duration else pos


def read_group(coordinator, members, now=None):
    """Read the playback state of one group from its coordinator."""
    state = coordinator.get_current_transport_info()["current_transport_state"]
    track = coordinator.get_current_track_info()
    channel = ""
    if not track.get("title"):  # radio: the stream name is in the media info
        channel = coordinator.get_current_media_info().get("channel", "")
    try:
        actions = parse_actions(coordinator.available_actions)
    except Exception:
        actions = frozenset()
    return GroupInfo(
        coordinator=coordinator.player_name, members=tuple(members), state=state,
        title=track.get("title", ""), artist=track.get("artist", ""), album=track.get("album", ""),
        art_url=track.get("album_art", ""), uri=track.get("uri", ""), channel=channel,
        duration=parse_hms(track.get("duration")), actions=actions,
        position=parse_hms(track.get("position")), read_at=now or time.time())


@dataclass
class _Group:
    coordinator: object            # SoCo
    members: tuple
    info: GroupInfo = None
    dirty: bool = True
    last: float = 0.0


class Monitor:
    def __init__(self, on_change=None, poll_playing=3.0, poll_idle=15.0):
        self._on_change = on_change
        self.poll_playing, self.poll_idle = poll_playing, poll_idle
        self.events_ok = True
        self._lock = threading.Lock()
        self._groups = {}              # coordinator name -> _Group
        self._ip_to_group = {}         # any member ip -> coordinator name
        self._zones = []
        self._subs = {}                # ip -> Subscription
        self._dirty_ips = set()
        self._commands = queue.Queue()
        self._next_topology = 0.0
        self._next_discover = 0.0
        self._stop = threading.Event()
        self._thread = None

    # --- public ---------------------------------------------------------------------------------

    @property
    def groups(self):
        """Latest GroupInfo of every group, sorted by coordinator name."""
        with self._lock:
            return sorted((g.info for g in self._groups.values() if g.info), key=lambda i: i.coordinator)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._unsubscribe_all()

    def command(self, coordinator, action, arg=None):
        """Queue play/pause/next/previous/stop/seek (absolute seconds)/seek_by (relative seconds)."""
        self._commands.put((coordinator, action, arg))

    # --- loop -----------------------------------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._step(time.time())
            except Exception:
                log.exception("monitor step failed")
            self._stop.wait(0.5)

    def _step(self, now):
        changed = self._run_commands(now)
        if now >= self._next_topology:
            changed |= self._refresh_topology(now)
        self._drain_events()
        for g in list(self._groups.values()):
            interval = self.poll_playing if g.info and g.info.state == PLAYING else self.poll_idle
            if g.dirty or g.info is None or now - g.last >= interval:
                changed |= self._read(g, now)
        if changed and self._on_change:
            self._on_change(self.groups)

    # --- topology and events --------------------------------------------------------------------

    def _refresh_topology(self, now):
        self._next_topology = now + TOPOLOGY_EVERY
        try:
            if not self._zones or now >= self._next_discover:
                self._zones = discovery.discover()
                self._next_discover = now + REDISCOVER_EVERY
                for zone in self._zones:
                    self._subscribe(zone)
            all_groups = self._zones[0].all_groups if self._zones else []
        except Exception:
            log.exception("topology refresh failed")
            self._zones, self._next_discover = [], 0.0  # rediscover next time
            return False
        fresh, ip_map = {}, {}
        for group in all_groups:
            coordinator = group.coordinator
            names = []
            for m in group.members:
                ip_map[m.ip_address] = coordinator.player_name
                if m.player_name not in names:
                    names.append(m.player_name)
            names.sort(key=lambda n: n != coordinator.player_name)  # coordinator first
            old = self._groups.get(coordinator.player_name)
            if old and old.members == tuple(names):
                old.coordinator = coordinator
                fresh[coordinator.player_name] = old
            else:
                fresh[coordinator.player_name] = _Group(coordinator, tuple(names))
        changed = {k: g.members for k, g in fresh.items()} != {k: g.members for k, g in self._groups.items()}
        with self._lock:
            self._groups, self._ip_to_group = fresh, ip_map
        return changed

    def _subscribe(self, zone):
        if zone.ip_address in self._subs:
            return
        try:
            self._subs[zone.ip_address] = zone.avTransport.subscribe(auto_renew=True)
            log.info("monitor: subscribed to events of %s", zone.player_name)
        except Exception:
            if self.events_ok:
                log.warning("event subscription failed; polling only", exc_info=True)
            self.events_ok = False
            self.poll_playing = min(self.poll_playing, 2.0)

    def _unsubscribe_all(self):
        for sub in list(self._subs.values()):
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subs.clear()
        try:
            from soco import events
            events.event_listener.stop()
        except Exception:
            pass

    def _drain_events(self):
        for ip, sub in self._subs.items():
            try:
                while True:
                    sub.events.get_nowait()
                    log.info("monitor: event from %s", ip)
                    self._dirty_ips.add(ip)
            except queue.Empty:
                pass
        for ip in self._dirty_ips:
            group = self._groups.get(self._ip_to_group.get(ip))
            if group:
                group.dirty = True
        self._dirty_ips.clear()

    # --- reading and commands -------------------------------------------------------------------

    def _read(self, group, now):
        group.last, group.dirty = now, False
        try:
            info = read_group(group.coordinator, group.members, now)
        except Exception:
            log.warning("reading %s failed", group.members[0], exc_info=True)
            return False
        changed = info != group.info
        with self._lock:
            group.info = info
        return changed

    def _run_commands(self, now):
        changed = False
        while True:
            try:
                name, action, arg = self._commands.get_nowait()
            except queue.Empty:
                return changed
            group = self._groups.get(name)
            if not group:
                continue
            try:
                self._execute(group, action, arg, now)
            except Exception:
                log.warning("command %s on %s failed", action, name, exc_info=True)
            group.dirty = True

    def _execute(self, group, action, arg, now):
        zone = group.coordinator
        if action == "seek_by":
            info = group.info
            target = (info.current_position(now) if info else 0) + arg
            if info and info.duration:
                target = min(target, max(info.duration - 1, 0))
            action, arg = "seek", max(target, 0)
        if action == "seek":
            zone.seek(format_hms(arg))
        elif action in ("play", "pause", "next", "previous", "stop"):
            getattr(zone, action)()
        else:
            raise ValueError(f"unknown action {action}")
