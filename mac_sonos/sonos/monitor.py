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


def clamp_volume(v):
    return max(0, min(100, int(v)))


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
    levels: tuple = ()      # (room, volume, muted) per member; volume is None if unreadable
    # volatile: they change every second and must not count as a change
    position: int = field(default=0, compare=False)
    read_at: float = field(default=0.0, compare=False)

    def current_position(self, now=None):
        if self.state != PLAYING:
            return self.position
        elapsed = int((now or time.time()) - self.read_at)
        pos = self.position + elapsed
        return min(pos, self.duration) if self.duration else pos


def read_levels(zones):
    levels = []
    for zone in zones:
        try:
            levels.append((zone.player_name, int(zone.volume), bool(zone.mute)))
        except Exception:
            levels.append((zone.player_name, None, None))
    return tuple(levels)


def read_group(coordinator, members, now=None, zones=()):
    """Read the playback state of one group from its coordinator (and the volume of its rooms)."""
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
        duration=parse_hms(track.get("duration")), actions=actions, levels=read_levels(zones),
        position=parse_hms(track.get("position")), read_at=now or time.time())


@dataclass
class _Group:
    coordinator: object            # SoCo
    members: tuple
    zones: tuple = ()              # one SoCo per room (stereo pairs appear once), coordinator first
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
        self._subs = {}                # (ip, service) -> Subscription
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
        """Queue a group command: play/pause/next/previous/stop, seek (absolute seconds), seek_by (relative
        seconds), volume_by (relative, every room of the group) or partymode (join everyone to the group)."""
        self._commands.put(("group", coordinator, action, arg))

    def room_command(self, room, action, arg=None):
        """Queue a room command: volume (0-100), volume_by (relative), mute (bool, None toggles), join
        (arg: coordinator name of the group to join) or unjoin. room=None with 'ungroup_all' splits every group."""
        self._commands.put(("room", room, action, arg))

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
        self._drain_events()  # before the topology check: a topology event forces it
        if now >= self._next_topology:
            changed |= self._refresh_topology(now)
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
                if self._zones:
                    self._subscribe_topology(self._zones[0])
            all_groups = self._fresh_groups(self._zones[0]) if self._zones else []
        except Exception:
            log.exception("topology refresh failed")
            self._zones, self._next_discover = [], 0.0  # rediscover next time
            return False
        fresh, ip_map = {}, {}
        for group in all_groups:
            coordinator = group.coordinator
            by_name = {}
            for m in group.members:
                ip_map[m.ip_address] = coordinator.player_name
                # stereo pairs list two players under one name: only the visible one takes group commands
                current = by_name.get(m.player_name)
                if current is None or (not getattr(current, "is_visible", True) and getattr(m, "is_visible", True)):
                    by_name[m.player_name] = m
            names = sorted(by_name, key=lambda n: n != coordinator.player_name)  # coordinator first
            zones = tuple(by_name[n] for n in names)
            old = self._groups.get(coordinator.player_name)
            if old and old.members == tuple(names):
                old.coordinator, old.zones = coordinator, zones
                fresh[coordinator.player_name] = old
            else:
                fresh[coordinator.player_name] = _Group(coordinator, tuple(names), zones)
        changed = {k: g.members for k, g in fresh.items()} != {k: g.members for k, g in self._groups.items()}
        with self._lock:
            self._groups, self._ip_to_group = fresh, ip_map
        return changed

    @staticmethod
    def _fresh_groups(zone):
        """The groups as they are now: SoCo caches the topology and would lag behind a join/unjoin."""
        try:
            zone.zone_group_state.clear_cache()
        except AttributeError:
            pass
        return zone.all_groups

    def _subscribe_topology(self, zone):
        """Any one zone reports the whole house: its events tell us at once that the grouping changed."""
        try:
            if (zone.ip_address, "zoneGroupTopology") not in self._subs:
                self._subs[(zone.ip_address, "zoneGroupTopology")] = zone.zoneGroupTopology.subscribe(auto_renew=True)
        except Exception:
            log.warning("topology subscription failed; relying on polling", exc_info=True)

    def _subscribe(self, zone):
        try:
            for name in ("avTransport", "renderingControl"):  # what plays, and volume/mute
                if (zone.ip_address, name) not in self._subs:
                    self._subs[(zone.ip_address, name)] = getattr(zone, name).subscribe(auto_renew=True)
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
        for (ip, service), sub in self._subs.items():
            try:
                while True:
                    sub.events.get_nowait()
                    log.info("monitor: %s event from %s", service, ip)
                    if service == "zoneGroupTopology":
                        self._next_topology = 0.0  # grouping changed: look at it on this very step
                    else:
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
            info = read_group(group.coordinator, group.members, now, group.zones)
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
                kind, target, action, arg = self._commands.get_nowait()
            except queue.Empty:
                return changed
            try:
                if kind == "group":
                    group = self._groups.get(target)
                    if not group:
                        continue
                    self._execute(group, action, arg, now)
                    group.dirty = True
                else:
                    self._execute_room(target, action, arg)
                    for g in self._groups.values():
                        g.dirty = True
                    if action in ("join", "unjoin", "ungroup_all"):
                        self._next_topology = 0.0  # membership changed: look again right away
            except Exception:
                log.warning("command %s on %s failed", action, target, exc_info=True)

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
        elif action == "volume_by":
            for z in group.zones:
                z.volume = clamp_volume(z.volume + arg)
        elif action == "partymode":
            zone.partymode()
        elif action in ("play", "pause", "next", "previous", "stop"):
            getattr(zone, action)()
        else:
            raise ValueError(f"unknown action {action}")

    def _find_zone(self, room):
        for group in self._groups.values():
            for z in group.zones:
                if z.player_name == room:
                    return z
        raise LookupError(f"room {room!r} not found")

    def _execute_room(self, room, action, arg):
        if action == "ungroup_all":
            for group in self._groups.values():
                for z in group.zones[1:]:
                    z.unjoin()
            return
        zone = self._find_zone(room)
        if action == "volume":
            zone.volume = clamp_volume(arg)
        elif action == "volume_by":
            zone.volume = clamp_volume(zone.volume + arg)
        elif action == "mute":
            zone.mute = (not zone.mute) if arg is None else bool(arg)
        elif action == "join":
            group = self._groups.get(arg)
            if not group:
                raise LookupError(f"group {arg!r} not found")
            zone.join(group.coordinator)
        elif action == "unjoin":
            zone.unjoin()
        else:
            raise ValueError(f"unknown room action {action}")
