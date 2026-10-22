"""Menu decisions with no AppKit in them, so they can be tested."""
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
