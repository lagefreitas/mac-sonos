"""Menu decisions with no AppKit in them, so they can be tested."""
import re

from ..controller import CONNECTING, ERROR, STOPPED, STREAMING

INTERRUPTED = "interrupted"  # we think we stream, but Sonos stopped or left our stream
TITLES = {STOPPED: "🔈", CONNECTING: "🔄", STREAMING: "🔊", ERROR: "⚠️", INTERRUPTED: "⏸"}
VOLUME_STEPS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100]
FORMAT_CHOICES = ["mp3", "aac"]
RETRY_SECONDS = 10  # like the login loop: Wi-Fi, Sonos or BlackHole may not be ready yet


def status_text(state, interrupted=False):
    if interrupted:
        return "Interrompido no Sonos"
    if state.status == STREAMING:
        return "Tocando em " + ", ".join(state.rooms)
    if state.status == CONNECTING:
        return "Conectando…"
    if state.status == ERROR:
        return "Erro: " + (state.error or "desconhecido")
    return "Parado"


def toggle_label(status, interrupted=False):
    return "Desligar" if status in (CONNECTING, STREAMING) and not interrupted else "Ligar"


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
        return f"⚠️ Volume do Mac em {mac_volume}% (deixe em 100%)"
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
            return "TV"
        if info.uri.startswith("x-rincon-stream:"):
            return "Line-in"
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
        out.append(("previous", "⏮ Anterior"))
    if info.state == PLAYING and "pause" in a:
        out.append(("pause", "⏸ Pausar"))
    elif info.state != PLAYING and "play" in a:
        out.append(("play", "▶ Tocar"))
    if "next" in a:
        out.append(("next", "⏭ Próxima"))
    if "seektime" in a and info.duration:
        out.append(("back", f"⏪ −{BACK_SECONDS}s"))
        out.append(("forward", f"⏩ +{FORWARD_SECONDS}s"))
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
    return tuple((g.coordinator, g.members, g.state, track_text(g), g.art_url, tuple(controls(g)))
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
    return "🔇 mudo" if muted else f"{volume}%"


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
        items += [(f"Graves: {eq_text(settings.bass)}", None, None, None),
                  (f"Graves +{EQ_STEP}", "bass_by", EQ_STEP, None),
                  (f"Graves −{EQ_STEP}", "bass_by", -EQ_STEP, None)]
    if settings.treble is not None:
        items += [(f"Agudos: {eq_text(settings.treble)}", None, None, None),
                  (f"Agudos +{EQ_STEP}", "treble_by", EQ_STEP, None),
                  (f"Agudos −{EQ_STEP}", "treble_by", -EQ_STEP, None)]
    if settings.loudness is not None:
        items.append(("Loudness", "loudness", None, bool(settings.loudness)))
    if settings.night_mode is not None:
        items.append(("Modo noite", "night_mode", None, bool(settings.night_mode)))
    if settings.dialog_mode is not None:
        items.append(("Realce de diálogo", "dialog_mode", None, bool(settings.dialog_mode)))
    if settings.surround is not None:
        items.append(("Surround", "surround", None, bool(settings.surround)))
    if settings.soundbar:
        items.append(("Áudio da TV", "tv", None, None))
    return items
