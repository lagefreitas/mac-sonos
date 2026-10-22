#!/usr/bin/env python3
"""Stream the Mac's audio to Sonos speakers that lack AirPlay (Play:1, Playbar...).

Mac output -> BlackHole (virtual device) -> PortAudio capture -> ffmpeg encoder -> HTTP stream -> Sonos.
Each Sonos connection gets its own capture and ffmpeg, so a stream always starts at the live edge.
(ffmpeg's own avfoundation capture drops chunks of audio, which sounds like vinyl crackle.)
"""
import argparse
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import soco
import sounddevice as sd

# name -> (ffmpeg codec args, content type, default bitrate, play as mp3 radio)
FORMATS = {
    "mp3": (["-c:a", "libmp3lame", "-f", "mp3"], "audio/mpeg", "192k", True),
    "aac": (["-c:a", "aac", "-f", "adts"], "audio/aac", "256k", True),
    "flac": (["-c:a", "flac", "-compression_level", "0", "-f", "flac"], "audio/flac", None, False),
    "wav": (["-c:a", "pcm_s16le", "-f", "wav"], "audio/wav", None, False),
}


DIDL = (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
    '<item id="R:0/0/0" parentID="R:0/0" restricted="true"><dc:title>Mac</dc:title>'
    "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
    '<res protocolInfo="http-get:*:{mime}:*">{url}</res></item></DIDL-Lite>'
)


RATE = 48000  # BlackHole's native rate


def ffmpeg_cmd(cfg):
    codec, _, default_br, _ = FORMATS[cfg["fmt"]]
    cmd = ["ffmpeg", "-loglevel", "error"]
    if cfg["lowlat"]:
        cmd += ["-fflags", "+nobuffer", "-flags", "+low_delay", "-probesize", "32"]
    cmd += ["-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "pipe:0", "-ar", "44100"]
    bitrate = cfg["bitrate"] or default_br
    if bitrate:
        cmd += ["-b:a", bitrate]
    return cmd + codec + ["-flush_packets", "1", "pipe:1"]


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = self.server.cfg
        self.server.gets.append(time.time())
        pcm = queue.Queue(maxsize=500)

        def on_audio(indata, frames, t, status):
            try:
                pcm.put_nowait(bytes(indata))
            except queue.Full:
                pass

        ff = subprocess.Popen(ffmpeg_cmd(cfg), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)

        def feed():
            try:
                while (block := pcm.get()) is not None:
                    ff.stdin.write(block)
            except (BrokenPipeError, ValueError, OSError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        try:
            with sd.RawInputStream(device=cfg["device"], samplerate=RATE, channels=2, dtype="int16",
                                   latency=0.1, callback=on_audio):
                self.send_response(200)
                self.send_header("Content-Type", FORMATS[cfg["fmt"]][1])
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                while chunk := ff.stdout.read1(4096):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            pcm.put(None)
            ff.kill()

    def log_message(self, *args):
        pass


class Session:
    """HTTP stream server plus Sonos group playing it."""

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


def find_rooms(names):
    speakers = soco.discover(timeout=5) or set()
    by_name = {s.player_name.lower(): s for s in speakers}
    found = []
    for n in names:
        if n.lower() not in by_name:
            sys.exit(f"Room '{n}' not found. Available: {', '.join(sorted(by_name)) or 'none'}")
        found.append(by_name[n.lower()])
    return found


def device_exists(name):
    out = subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                         capture_output=True, text=True).stderr
    return any(line.endswith("] " + name) for line in out.splitlines())


def require_device(name):
    if not device_exists(name):
        sys.exit(f"Audio device '{name}' not found. If BlackHole is installed, load it with "
                 "`sudo killall coreaudiod` (or reboot); otherwise see `mac_sonos.py devices`.")


class OutputSwitch:
    """Point the Mac output at the capture device; restore it on exit (needs SwitchAudioSource)."""

    def __init__(self, device):
        self.exe, self.device, self.previous = shutil.which("SwitchAudioSource"), device, None

    def __enter__(self):
        if self.exe:
            self.previous = subprocess.run([self.exe, "-c"], capture_output=True, text=True).stdout.strip()
            subprocess.run([self.exe, "-s", self.device], capture_output=True)
            # BlackHole scales the signal by the Mac volume (31% ~ -50 dB, near silence)
            subprocess.run(["osascript", "-e", "set volume output volume 100"], capture_output=True)
        else:
            print(f"Set the Mac's sound output to '{self.device}' (or: brew install switchaudio-osx).")
        return self

    def __exit__(self, *exc):
        if self.exe and self.previous:
            subprocess.run([self.exe, "-s", self.previous], capture_output=True)


def cmd_list(_):
    for s in sorted(soco.discover(timeout=5) or [], key=lambda s: s.player_name):
        print(f"{s.player_name:20} {s.ip_address}  {s.get_speaker_info().get('model_name', '')}")


def cmd_devices(_):
    subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""])


def cmd_run(args):
    require_device(args.device)
    rooms = find_rooms(args.rooms)
    with OutputSwitch(args.device):
        session = Session(rooms, args.device, args.format, args.port, args.bitrate, args.lowlat, args.volume)
        url = session.start()
        print(f"Streaming Mac audio to {', '.join(s.player_name for s in rooms)} ({url}, {args.format})")
        print("Ctrl-C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            session.stop()


# --- latency benchmark -------------------------------------------------------------------------
# The source is live (1 s of audio per second), so the time Sonos waits between connecting to the
# stream and reporting PLAYING is the depth of its start-up buffer, i.e. the latency it adds.

def measure(session, timeout=30):
    """Seconds from Sonos's first HTTP GET to the transport reporting PLAYING (None on timeout)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if session.server.gets and session.playing():
            return time.time() - session.server.gets[0]
        time.sleep(0.2)
    return None


CONFIGS = {  # name -> Session kwargs
    "mp3": dict(fmt="mp3"),
    "mp3-lowlat": dict(fmt="mp3", lowlat=True),
    "aac-lowlat": dict(fmt="aac", lowlat=True),
    "flac-lowlat": dict(fmt="flac", lowlat=True),
    "wav-lowlat": dict(fmt="wav", lowlat=True),
}


def cmd_bench(args):
    require_device(args.device)
    rooms = find_rooms(args.rooms)
    results = {}
    with OutputSwitch(args.device):
        for name in args.configs or list(CONFIGS):
            lat = []
            for _ in range(args.runs):
                session = Session(rooms, args.device, port=args.port, volume=args.volume, **CONFIGS[name])
                try:
                    session.start()
                    m = measure(session)
                except Exception as e:
                    print(f"[{name}] failed: {e}", flush=True)
                    m = None
                finally:
                    session.stop()
                print(f"[{name}] {'timeout' if m is None else f'{m:.2f}s'} ({len(session.server.gets)} GETs)", flush=True)
                if m is not None:
                    lat.append(m)
                time.sleep(3)
            results[name] = lat
    print("\nSummary (median seconds from connect to PLAYING):")
    for name, lat in results.items():
        print(f"  {name:12} " + (f"{sorted(lat)[len(lat) // 2]:.2f}s  {['%.1f' % x for x in lat]}" if lat else "n/a"))


def main():
    # Children (ffmpeg...) inherit stdin; if it is a tty, a background job gets stopped (SIGTTIN).
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list Sonos rooms").set_defaults(fn=cmd_list)
    sub.add_parser("devices", help="list Mac audio input devices").set_defaults(fn=cmd_devices)
    r = sub.add_parser("run", help="stream Mac audio to Sonos")
    r.add_argument("rooms", nargs="+", help="room names; first one is the group leader")
    r.add_argument("--device", default="BlackHole 2ch")
    r.add_argument("--port", type=int, default=8000)
    r.add_argument("--format", choices=FORMATS, default="mp3")
    r.add_argument("--bitrate", help="mp3/aac bitrate, e.g. 192k")
    r.add_argument("--lowlat", action="store_true", help="low-latency ffmpeg input flags")
    r.add_argument("--volume", type=int, help="set Sonos volume (0-100) on start")
    r.set_defaults(fn=cmd_run)
    b = sub.add_parser("bench", help="measure Sonos start-up buffer (latency) per stream config")
    b.add_argument("rooms", nargs="+")
    b.add_argument("--configs", nargs="+", choices=CONFIGS)
    b.add_argument("--device", default="BlackHole 2ch")
    b.add_argument("--port", type=int, default=8000)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--volume", type=int, default=15)
    b.set_defaults(fn=cmd_bench)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
