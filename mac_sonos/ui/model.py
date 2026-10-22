"""Menu decisions with no AppKit in them, so they can be tested."""
import re

from ..controller import CONNECTING, ERROR, STOPPED, STREAMING

TITLES = {STOPPED: "🔈", CONNECTING: "🔄", STREAMING: "🔊", ERROR: "⚠️"}
VOLUME_STEPS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100]
FORMAT_CHOICES = ["mp3", "aac"]
RETRY_SECONDS = 10  # like the login loop: Wi-Fi, Sonos or BlackHole may not be ready yet


def status_text(state):
    if state.status == STREAMING:
        return "Tocando em " + ", ".join(state.rooms)
    if state.status == CONNECTING:
        return "Conectando…"
    if state.status == ERROR:
        return "Erro: " + (state.error or "desconhecido")
    return "Parado"


def toggle_label(status):
    return "Desligar" if status in (CONNECTING, STREAMING) else "Ligar"


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
