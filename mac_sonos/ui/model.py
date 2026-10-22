"""Menu decisions with no AppKit in them, so they can be tested."""
import re

from ..controller import CONNECTING, ERROR, STOPPED, STREAMING
from ..i18n import t

INTERRUPTED = "interrupted"  # we think we stream, but Sonos stopped or left our stream
TITLES = {STOPPED: "🔈", CONNECTING: "🔄", STREAMING: "🔊", ERROR: "⚠️", INTERRUPTED: "⏸"}
VOLUME_STEPS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100]
FORMAT_CHOICES = ["mp3", "aac"]
RETRY_SECONDS = 10  # like the login loop: Wi-Fi, Sonos or BlackHole may not be ready yet


def status_text(state, interrupted=False):
    if interrupted:
        return t("status.interrupted")
    if state.status == STREAMING:
        return t("status.streaming", rooms=", ".join(state.rooms))
    if state.status == CONNECTING:
        return t("status.connecting")
    if state.status == ERROR:
        if state.error_key:  # localizable error: translate it, keep the English text as the fallback
            detail = t(state.error_key, **(state.error_vars or {}))
            if detail != state.error_key:
                return t("status.error", error=detail)
        return t("status.error", error=state.error or t("status.error_unknown"))
    return t("status.stopped")


def toggle_label(status, interrupted=False):
    return t("toggle.off") if status in (CONNECTING, STREAMING) and not interrupted else t("toggle.on")


def toggle_room(selected, name):
    """Add or remove a room, keeping the leader first; the last room can't be removed."""
    if name in selected:
        return [r for r in selected if r != name] if len(selected) > 1 else list(selected)
    return list(selected) + [name]


def room_choices(discovered, selected):
    """Discovered rooms, plus selected ones that were not found (so they can be unchecked)."""
    return list(discovered) + [r for r in selected if r not in discovered]


def volume_warning(mac_volume):
    if mac_volume is not None and mac_volume != 100:
        return t("warning.mac_volume", pct=mac_volume)
    return None


def should_retry(status, want_stream, since_last_attempt):
    return status == ERROR and want_stream and since_last_attempt >= RETRY_SECONDS


# --- now playing ---------------------------------------------------------------------------------
from ..sonos.monitor import PAUSED, PLAYING  # noqa: E402

STATE_ICONS = {PLAYING: "▶", PAUSED: "⏸"}
BACK_SECONDS, FORWARD_SECONDS = 10, 30


def format_time(seconds):
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def shorten(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def group_name(info):
    return " + ".join(info.members)


def track_text(info):
    """'Title – Artist', else the radio stream name, else a dash."""
    if info.title and info.artist:
        return f"{info.title} – {info.artist}"
    if not info.title and not info.channel:
        if info.uri.startswith("x-sonos-htastream:"):
            return t("track.tv")
        if info.uri.startswith("x-rincon-stream:"):
            return t("track.line_in")
    return info.title or info.channel or "—"


def is_mac_stream(info):
    """True for the group playing this app's own stream (it has no title, artist or controls to show)."""
    return bool(re.search(r"/mac\.\w+$", info.uri))


def is_active(info):
    return info.state in (PLAYING, PAUSED)


def primary_group(groups, rooms):
    """The group to feature in the bar and summary line: one with our rooms if active, else the first
    playing one. This app's own stream is skipped: it has no track to show."""
    groups = [g for g in groups if not is_mac_stream(g)]
    mine = [g for g in groups if is_active(g) and set(rooms) & set(g.members)]
    playing = [g for g in groups if g.state == PLAYING]
    return (mine or playing or [None])[0]


def controls(info):
    """(key, label) of the transport controls this group supports right now."""
    a, out = info.actions, []
    if "previous" in a:
        out.append(("previous", t("transport.previous")))
    if info.state == PLAYING and "pause" in a:
        out.append(("pause", t("transport.pause")))
    elif info.state != PLAYING and "play" in a:
        out.append(("play", t("transport.play")))
    if "next" in a:
        out.append(("next", t("transport.next")))
    if "seektime" in a and info.duration:
        out.append(("back", t("transport.back", n=BACK_SECONDS)))
        out.append(("forward", t("transport.forward", n=FORWARD_SECONDS)))
    return out


def time_text(info, now=None):
    if not info.duration:
        return ""
    return f"{format_time(info.current_position(now))} / {format_time(info.duration)}"


def bar_title(icon, info, limit=28):
    if info is None or is_mac_stream(info):
        return icon
    return f"{icon} {shorten(track_text(info), limit)}"


def structure_key(groups):
    """Changes only when the now-playing submenu must be rebuilt (not for position ticks)."""
    return tuple((g.coordinator, g.members, g.state, track_text(g), g.art_url, tuple(controls(g)), g.sleep_active)
                 for g in groups if is_active(g))


# --- stream reconciliation -----------------------------------------------------------------------
from ..sonos.monitor import STOPPED as SONOS_STOPPED  # noqa: E402

INTERRUPT_GRACE = 10  # seconds after the stream started during which Sonos may still be settling


def is_interrupted(state, groups, now):
    """True when we are streaming but the Sonos group of our leader stopped, paused or left our stream
    (someone used the Sonos app, a physical button, or another source took the room)."""
    if state.status != STREAMING or not state.rooms or now - state.started_at < INTERRUPT_GRACE:
        return False
    leader = state.rooms[0]
    info = next((g for g in groups if leader in g.members), None)
    if info is None or info.read_at < state.started_at:  # unknown, or a reading from before we started
        return False
    return not is_mac_stream(info) or info.state in (SONOS_STOPPED, PAUSED)


# --- house: volume, mute and grouping ------------------------------------------------------------
VOLUME_DELTA = 5


def level_text(volume, muted):
    if volume is None:
        return "?"
    return t("level.muted") if muted else f"{volume}%"


def house_rooms(groups):
    """(room, group, volume, muted) for every room, grouped rooms together, in group order."""
    out = []
    for g in groups:
        levels = {name: (v, m) for name, v, m in g.levels}
        for room in g.members:
            v, m = levels.get(room, (None, None))
            out.append((room, g, v, m))
    return out


def house_key(groups, settings=None):
    """Changes when the house submenu must be rebuilt: rooms, grouping, mute or sound settings (not volume)."""
    return (tuple((g.coordinator, g.members, tuple((n, m) for n, _, m in g.levels)) for g in groups),
            tuple(sorted((settings or {}).items())))


def join_targets(groups, group):
    """Coordinators of the other groups a room of `group` can join."""
    return [g.coordinator for g in groups if g.coordinator != group.coordinator]


def party_target(groups, rooms):
    """The group everyone should join: ours if active, else the first playing one, else the first."""
    mine = [g for g in groups if is_active(g) and set(rooms) & set(g.members)]
    playing = [g for g in groups if g.state == PLAYING]
    return (mine or playing or groups or [None])[0]


# --- sound ---------------------------------------------------------------------------------------
EQ_STEP = 1


def eq_text(value):
    if value is None:
        return "?"
    return "0" if value == 0 else f"{value:+d}".replace("-", "−")


def sound_items(settings):
    """(label, action, arg, checked) for a room's sound submenu; action None is an info line, checked
    None is a plain item. Only what the room supports is listed."""
    if settings is None:
        return []
    items = []
    if settings.bass is not None:
        items += [(t("sound.bass", value=eq_text(settings.bass)), None, None, None),
                  (t("sound.bass_up", n=EQ_STEP), "bass_by", EQ_STEP, None),
                  (t("sound.bass_down", n=EQ_STEP), "bass_by", -EQ_STEP, None)]
    if settings.treble is not None:
        items += [(t("sound.treble", value=eq_text(settings.treble)), None, None, None),
                  (t("sound.treble_up", n=EQ_STEP), "treble_by", EQ_STEP, None),
                  (t("sound.treble_down", n=EQ_STEP), "treble_by", -EQ_STEP, None)]
    if settings.loudness is not None:
        items.append((t("sound.loudness"), "loudness", None, bool(settings.loudness)))
    if settings.night_mode is not None:
        items.append((t("sound.night_mode"), "night_mode", None, bool(settings.night_mode)))
    if settings.dialog_mode is not None:
        items.append((t("sound.dialog_mode"), "dialog_mode", None, bool(settings.dialog_mode)))
    if settings.surround is not None:
        items.append((t("sound.surround"), "surround", None, bool(settings.surround)))
    if settings.soundbar:
        items.append((t("sound.tv"), "tv", None, None))
    return items


# --- language ------------------------------------------------------------------------------------
def language_choices(available):
    """Language codes to offer in the menu, or [] when there is nothing to choose from."""
    return list(available) if len(available) > 1 else []


# --- library: favorites, playlists, queue, radio URLs --------------------------------------------
from ..sonos.library import PLAYLIST, RADIO, TRACK, default_title  # noqa: E402

KIND_ORDER = (RADIO, TRACK, PLAYLIST)
KIND_KEYS = {RADIO: "library.radios", TRACK: "library.tracks", PLAYLIST: "library.playlists"}
QUEUE_WINDOW = 40


def play_target(groups, rooms, target_room=None):
    """The group library items play on: the one holding `target_room`, else the Mac's first room, else the
    first group."""
    for room in (target_room, rooms[0] if rooms else None):
        found = next((g for g in groups if room and room in g.members), None)
        if found:
            return found
    return groups[0] if groups else None


def target_room_name(groups, rooms, chosen=None):
    """The room shown as the target: the chosen one if it exists, else the Mac's first room, else the first
    room of the first group, else ''."""
    existing = {m for g in groups for m in g.members}
    for room in (chosen, rooms[0] if rooms else None):
        if room in existing:
            return room
    return groups[0].members[0] if groups else ""


def favorite_sections(favorites):
    """(title, favorites) per kind that has any, in a fixed order."""
    out = []
    for kind in KIND_ORDER:
        items = [f for f in favorites if f.kind == kind]
        if items:
            out.append((t("library.group", kind=t(KIND_KEYS[kind]), n=len(items)), items))
    return out


def queue_window(entries, current_index, size=QUEUE_WINDOW):
    """(shown entries, hidden before, hidden after): a window of `size` entries around the current one."""
    if len(entries) <= size:
        return list(entries), 0, 0
    start = max(0, min((current_index if current_index >= 0 else 0) - size // 8, len(entries) - size))
    return list(entries[start:start + size]), start, len(entries) - start - size


def queue_entry_text(entry, current):
    text = f"{entry.index + 1}. {shorten(f'{entry.title} – {entry.artist}' if entry.artist else entry.title, 44)}"
    return f"▶ {text}" if current else text


def add_radio(saved, title, url):
    """Saved stations with `url` added (or its title updated); the list is not modified in place."""
    url, title = url.strip(), title.strip() or default_title(url)
    return [r for r in saved if r["url"] != url] + [{"title": title, "url": url}]


def remove_radio(saved, url):
    return [r for r in saved if r["url"] != url]


# --- sleep timer ---------------------------------------------------------------------------------
SLEEP_MINUTES = (15, 30, 45, 60, 90, 120)
FADE_SECONDS = 30   # the Mac's own sleep timer lowers the volume over this long before stopping


def sleep_text(seconds):
    return t("sleep.remaining", time=format_time(seconds))


class SleepTimer:
    """Sleep timer for the Mac's own stream (Sonos' native one would cut it abruptly): fades the volume out
    over the last FADE_SECONDS, then the app stops the stream and puts the volume back."""

    def __init__(self, minutes, base_volume, now, fade=FADE_SECONDS):
        self.deadline, self.base_volume, self.fade, self.started = now + minutes * 60, base_volume, fade, now

    def remaining(self, now):
        return max(0, int(round(self.deadline - now)))

    def expired(self, now):
        return now >= self.deadline

    def fade_volume(self, now):
        """The volume to set now, or None while the fade has not begun."""
        left = self.deadline - now
        if left > self.fade:
            return None
        return max(1, round(self.base_volume * max(left, 0) / self.fade))


# --- alarms --------------------------------------------------------------------------------------
RECURRENCE_KEYS = {"DAILY": "alarm.daily", "WEEKDAYS": "alarm.weekdays", "WEEKENDS": "alarm.weekends",
                   "ONCE": "alarm.once"}
RECURRENCE_CHOICES = ("DAILY", "WEEKDAYS", "WEEKENDS", "ONCE")
DAY_KEYS = ("alarm.day.0", "alarm.day.1", "alarm.day.2", "alarm.day.3", "alarm.day.4", "alarm.day.5", "alarm.day.6")
ALARM_VOLUME_DELTA = 5


def recurrence_label(recurrence):
    if recurrence in RECURRENCE_KEYS:
        return t(RECURRENCE_KEYS[recurrence])
    from ..sonos.alarms import days_of
    days = days_of(recurrence)
    return ", ".join(t(DAY_KEYS[d]) for d in days) if days else str(recurrence)


def alarm_title(info):
    from ..sonos.alarms import format_time as clock
    return t("alarm.title", time=clock(info.time), room=info.room, repeat=recurrence_label(info.recurrence))
