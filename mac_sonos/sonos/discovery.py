"""Finding Sonos players on the network."""
import soco


class RoomNotFound(Exception):
    pass


def discover(timeout=5):
    return sorted(soco.discover(timeout=timeout) or [], key=lambda s: s.player_name)


def find_rooms(names):
    by_name = {s.player_name.lower(): s for s in discover()}
    found = []
    for n in names:
        if n.lower() not in by_name:
            raise RoomNotFound(f"Room '{n}' not found. Available: {', '.join(sorted(by_name)) or 'none'}")
        found.append(by_name[n.lower()])
    return found
