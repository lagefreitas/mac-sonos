"""Scenes: a saved state of the house that one click brings back.

A scene is plain JSON (kept in the config, so it can be edited by hand):

    {"name": "Cinema",
     "stream": "off",                                  # the Mac's stream: "on", "off" or "keep" (default)
     "stream_rooms": ["Office"],                       # rooms of the stream when it is "on"
     "groups": [["TV Room"], ["Office"]],              # the exact grouping, or null to leave it alone
     "rooms": {"TV Room": {"volume": 25, "mute": false, "bass": 2, "treble": 0, "loudness": true,
                           "night_mode": false, "dialog_mode": true, "surround": true}},
     "source": {"type": "tv", "room": "TV Room"}}      # or {"type": "favorite", "room", "title"},
                                                       #    {"type": "url", "room", "url", "title"}, or null

`plan()` turns a scene and the current groups into a list of steps and `Runner` carries them out, waiting
where one step depends on the previous (grouping before playing, the stream before the volumes).
"""
import logging
import threading
import time

log = logging.getLogger("mac_sonos")

SETTING_KEYS = ("volume", "mute", "bass", "treble", "loudness", "night_mode", "dialog_mode", "surround")
STREAM_MODES = ("on", "off", "keep")
RADIO_PREFIX = "x-rincon-mp3radio://"


def membership(groups):
    """The grouping as a set of frozensets of room names."""
    return {frozenset(g.members) for g in groups}


def capture(name, groups, settings, stream_rooms=None, mac_uri=None):
    """A scene holding the current state: grouping, volumes, mute, sound settings, the Mac stream, and a TV or
    URL-radio source if one is playing. `stream_rooms` is the Mac stream's rooms when it is running."""
    rooms = {}
    for g in groups:
        for room, volume, muted in g.levels:
            entry = {}
            if volume is not None:
                entry["volume"], entry["mute"] = volume, bool(muted)
            st = settings.get(room)
            if st:
                for key, value in (("bass", st.bass), ("treble", st.treble), ("loudness", st.loudness),
                                   ("night_mode", st.night_mode), ("dialog_mode", st.dialog_mode),
                                   ("surround", st.surround)):
                    if value is not None:
                        entry[key] = value
            if entry:
                rooms[room] = entry
    scene = {"name": name, "stream": "on" if stream_rooms else "off", "groups": [list(g.members) for g in groups],
             "rooms": rooms, "source": None}
    if stream_rooms:
        scene["stream_rooms"] = list(stream_rooms)
    else:
        for g in groups:
            if g.state != "PLAYING":
                continue
            if g.uri.startswith("x-sonos-htastream:"):
                scene["source"] = {"type": "tv", "room": g.coordinator}
            elif g.uri.startswith(RADIO_PREFIX) and not (mac_uri and g.uri == mac_uri):
                scene["source"] = {"type": "url", "room": g.coordinator, "url": g.uri[len(RADIO_PREFIX):],
                                   "title": g.channel or g.title}
            if scene["source"]:
                break
    return scene


def validate(scene):
    """A list of problems (empty when the scene is usable)."""
    problems = []
    if not isinstance(scene, dict) or not str(scene.get("name", "")).strip():
        return ["a scene needs a name"]
    if scene.get("stream", "keep") not in STREAM_MODES:
        problems.append(f"stream must be one of {STREAM_MODES}")
    if scene.get("stream") == "on" and not scene.get("stream_rooms"):
        problems.append("stream 'on' needs stream_rooms")
    groups = scene.get("groups")
    if groups is not None and not (isinstance(groups, list) and all(isinstance(g, list) and g for g in groups)):
        problems.append("groups must be null or a list of non-empty room lists")
    for room, entry in (scene.get("rooms") or {}).items():
        unknown = set(entry) - set(SETTING_KEYS)
        if unknown:
            problems.append(f"{room}: unknown settings {sorted(unknown)}")
    source = scene.get("source")
    if source is not None:
        kinds = {"tv": ("room",), "favorite": ("room", "title"), "url": ("room", "url")}
        if source.get("type") not in kinds or any(not source.get(k) for k in kinds.get(source.get("type"), ())):
            problems.append("source needs a type (tv, favorite, url) and its fields")
    return problems


def _desired_membership(scene, groups):
    """The grouping a scene asks for, with every room it does not mention left on its own."""
    all_rooms = [m for g in groups for m in g.members]
    wanted = [frozenset(g) for g in scene["groups"]]
    mentioned = set().union(*wanted) if wanted else set()
    return {*wanted, *(frozenset([r]) for r in all_rooms if r not in mentioned)}


def plan(scene, groups):
    """Steps to apply `scene` given the current `groups`:
    ("stream_off",) ("ungroup_all",) ("join", room, leader) ("wait_groups", membership)
    ("stream_on", rooms, volume) ("wait_stream",) ("room", room, action, arg)
    ("tv", room) ("favorite", room, title) ("url", room, url, title)"""
    steps = []
    stream = scene.get("stream", "keep")
    if stream == "off":
        steps.append(("stream_off",))
    if scene.get("groups") is not None:
        desired = _desired_membership(scene, groups)
        if desired != membership(groups):
            steps.append(("ungroup_all",))
            for members in scene["groups"]:
                for room in members[1:]:
                    steps.append(("join", room, members[0]))
            steps.append(("wait_groups", desired))
    rooms = scene.get("rooms") or {}
    if stream == "on":
        stream_rooms = scene["stream_rooms"]
        steps.append(("stream_on", list(stream_rooms), (rooms.get(stream_rooms[0]) or {}).get("volume")))
        steps.append(("wait_stream",))
    for room, entry in rooms.items():
        for key in SETTING_KEYS:
            if key in entry:
                steps.append(("room", room, key, entry[key]))
    source = scene.get("source")
    if source and stream != "on":  # the Mac stream is the source when it is on
        if source["type"] == "tv":
            steps.append(("tv", source["room"]))
        elif source["type"] == "favorite":
            steps.append(("favorite", source["room"], source["title"]))
        else:
            steps.append(("url", source["room"], source["url"], source.get("title", "")))
    return steps


class Runner:
    """Carries out a plan on a worker thread, one scene at a time.

    `actions` supplies what each step needs: room(room, action, arg), command(group, action, arg) is not needed;
    groups() -> current groups, stream_on(rooms, volume), stream_off(), streaming() -> bool,
    favorite(room, title), url(room, url, title), tv(room). `sleep`/`clock` can be replaced in tests."""

    def __init__(self, actions, wait_seconds=12.0, poll=0.3, sleep=time.sleep, clock=time.time):
        self.a, self.wait_seconds, self.poll, self._sleep, self._clock = actions, wait_seconds, poll, sleep, clock
        self._lock = threading.Lock()
        self.last_error = None

    def apply_async(self, scene, groups):
        thread = threading.Thread(target=self.run, args=(scene, groups), daemon=True, name="scene")
        thread.start()
        return thread

    def run(self, scene, groups):
        """Apply the scene; returns True if every step ran. Only one scene runs at a time."""
        if not self._lock.acquire(blocking=False):
            log.warning("scene %s ignored: another one is still being applied", scene.get("name"))
            return False
        try:
            self.last_error = None
            for step in plan(scene, groups):
                try:
                    self._do(step)
                except Exception as e:  # one failing step must not leave the rest of the house half-set
                    self.last_error = e
                    log.warning("scene %s: step %s failed", scene.get("name"), step, exc_info=True)
            return self.last_error is None
        finally:
            self._lock.release()

    def _wait(self, condition, what):
        end = self._clock() + self.wait_seconds
        while self._clock() < end:
            if condition():
                return True
            self._sleep(self.poll)
        log.warning("scene: gave up waiting for %s", what)
        return False

    def _do(self, step):
        kind = step[0]
        a = self.a
        if kind == "stream_off":
            a.stream_off()
        elif kind == "ungroup_all":
            a.room(None, "ungroup_all", None)
        elif kind == "join":
            a.room(step[1], "join", step[2])
        elif kind == "wait_groups":
            self._wait(lambda: membership(a.groups()) == step[1], "the grouping")
        elif kind == "stream_on":
            a.stream_on(step[1], step[2])
        elif kind == "wait_stream":
            self._wait(a.streaming, "the stream")
        elif kind == "room":
            a.room(step[1], step[2], step[3])
        elif kind == "tv":
            a.tv(step[1])
        elif kind == "favorite":
            a.favorite(step[1], step[2])
        elif kind == "url":
            a.url(step[1], step[2], step[3])
