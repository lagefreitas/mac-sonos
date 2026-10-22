"""Finding Sonos players on the network."""
import soco


class RoomNotFound(Exception):
    """English text for the CLI; `key` and `vars` let the UI show it in the user's language."""
    key = "error.room_not_found"

    def __init__(self, room, available):
        shown = ", ".join(sorted(available)) or "none"
        super().__init__(f"Room '{room}' not found. Available: {shown}")
        self.vars = {"room": room, "available": shown}


def discover(timeout=5):
    return sorted(soco.discover(timeout=timeout) or [], key=lambda s: s.player_name)


def find_rooms(names):
    by_name = {s.player_name.lower(): s for s in discover()}
    found = []
    for n in names:
        if n.lower() not in by_name:
            raise RoomNotFound(n, by_name)
        found.append(by_name[n.lower()])
    return found
