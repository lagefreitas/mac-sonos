#!/usr/bin/env python3
"""Stream the Mac's audio to Sonos speakers that lack AirPlay (Play:1, Playbar...).

Mac output -> BlackHole (virtual device) -> ffmpeg (MP3) -> HTTP stream -> Sonos.
"""
import argparse
import queue
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import soco

clients = set()
clients_lock = threading.Lock()


def broadcast(chunk):
    with clients_lock:
        for q in clients:
            try:
                q.put_nowait(chunk)
            except queue.Full:  # slow client: drop its oldest chunk
                try:
                    q.get_nowait()
                    q.put_nowait(chunk)
                except (queue.Empty, queue.Full):
                    pass


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        q = queue.Queue(maxsize=200)
        with clients_lock:
            clients.add(q)
        try:
            while True:
                self.wfile.write(q.get())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with clients_lock:
                clients.discard(q)

    def log_message(self, *args):
        pass


def pump(proc):
    while chunk := proc.stdout.read(4096):
        broadcast(chunk)


def find_rooms(names):
    speakers = soco.discover(timeout=5) or set()
    by_name = {s.player_name.lower(): s for s in speakers}
    found = []
    for n in names:
        if n.lower() not in by_name:
            sys.exit(f"Room '{n}' not found. Available: {', '.join(sorted(by_name)) or 'none'}")
        found.append(by_name[n.lower()])
    return found


def cmd_list(_):
    for s in sorted(soco.discover(timeout=5) or [], key=lambda s: s.player_name):
        print(f"{s.player_name:20} {s.ip_address}  {s.get_speaker_info().get('model_name', '')}")


def cmd_devices(_):
    subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""])


def device_exists(name):
    out = subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                         capture_output=True, text=True).stderr
    return any(line.endswith("] " + name) for line in out.splitlines())


def cmd_run(args):
    if not device_exists(args.device):
        sys.exit(f"Audio device '{args.device}' not found. If BlackHole is installed, load it with "
                 "`sudo killall coreaudiod` (or reboot); otherwise see `mac_sonos.py devices`.")
    rooms = find_rooms(args.rooms)
    leader = rooms[0]
    ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ip.connect((leader.ip_address, 1400))
    mac_ip = ip.getsockname()[0]
    ip.close()

    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "avfoundation", "-i", f":{args.device}",
         "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", args.bitrate,
         "-flush_packets", "1", "-f", "mp3", "pipe:1"],
        stdout=subprocess.PIPE)
    threading.Thread(target=pump, args=(ffmpeg,), daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), StreamHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    switch = shutil.which("SwitchAudioSource")
    previous = None
    if switch:
        previous = subprocess.run([switch, "-c"], capture_output=True, text=True).stdout.strip()
        subprocess.run([switch, "-s", args.device], capture_output=True)

    for s in rooms[1:]:
        s.join(leader)
    if args.volume is not None:
        for s in rooms:
            s.volume = args.volume
    url = f"http://{mac_ip}:{args.port}/mac.mp3"
    leader.play_uri(url, title="Mac", force_radio=True)
    print(f"Streaming Mac audio to {', '.join(s.player_name for s in rooms)} ({url})")
    if not switch:
        print(f"Set the Mac's sound output to '{args.device}' (or: brew install switchaudio-osx).")
    print("Ctrl-C to stop.")

    try:
        ffmpeg.wait()
        print("ffmpeg exited; is the BlackHole device name right? Try: mac_sonos.py devices")
    except KeyboardInterrupt:
        pass
    finally:
        ffmpeg.terminate()
        server.shutdown()
        try:
            leader.stop()
            for s in rooms[1:]:
                s.unjoin()
        except Exception:
            pass
        if switch and previous:
            subprocess.run([switch, "-s", previous], capture_output=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list Sonos rooms").set_defaults(fn=cmd_list)
    sub.add_parser("devices", help="list Mac audio input devices").set_defaults(fn=cmd_devices)
    r = sub.add_parser("run", help="stream Mac audio to Sonos")
    r.add_argument("rooms", nargs="+", help="room names; first one is the group leader")
    r.add_argument("--device", default="BlackHole 2ch")
    r.add_argument("--port", type=int, default=8000)
    r.add_argument("--bitrate", default="192k")
    r.add_argument("--volume", type=int, help="set Sonos volume (0-100) on start")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
