"""HTTP stream of the captured audio.

Each Sonos connection gets its own capture and ffmpeg, so a stream always starts at the live edge.
ffmpeg's own avfoundation capture drops chunks of audio (vinyl crackle), so PortAudio captures
and feeds ffmpeg through a pipe.
"""
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler

import sounddevice as sd

from .formats import FORMATS, RATE, ffmpeg_cmd


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
