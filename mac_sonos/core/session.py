"""HTTP stream server plus the Sonos group playing it."""
import socket
import threading
import time
from http.server import ThreadingHTTPServer

from .formats import DIDL, FORMATS
from .stream import StreamHandler


class Session:
    def __init__(self, rooms, device="BlackHole 2ch", fmt="mp3", port=8000, bitrate=None,
                 lowlat=False, volume=None):
        self.rooms, self.volume, self.fmt, self.port = rooms, volume, fmt, port
        self.leader = rooms[0]
        self.server = ThreadingHTTPServer(("0.0.0.0", port), StreamHandler)
        self.server.cfg = dict(device=device, fmt=fmt, bitrate=bitrate, lowlat=lowlat)
        self.server.gets = []

    def start(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((self.leader.ip_address, 1400))
        mac_ip = probe.getsockname()[0]
        probe.close()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        for s in self.rooms[1:]:
            s.join(self.leader)
        if self.volume is not None:
            for s in self.rooms:
                s.volume = self.volume
        self.url = f"http://{mac_ip}:{self.port}/mac.{self.fmt}"
        _, mime, _, radio = FORMATS[self.fmt]
        if radio:
            self.leader.play_uri(self.url, title="Mac", force_radio=True)
        else:  # Sonos needs the MIME type in the metadata for non-radio http streams
            meta = DIDL.format(mime=mime, url=self.url)
            self.leader.play_uri(self.url, meta=meta)
        return self.url

    def playing(self):
        return self.leader.get_current_transport_info()["current_transport_state"] == "PLAYING"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        try:
            self.leader.stop()
            for s in self.rooms[1:]:
                s.unjoin()
            for _ in range(25):  # wait for the stop to land so a later session sees no stale PLAYING
                if self.leader.get_current_transport_info()["current_transport_state"] != "PLAYING":
                    break
                time.sleep(0.2)
        except Exception:
            pass
