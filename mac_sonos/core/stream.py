"""HTTP stream of the captured audio.

Each Sonos connection gets its own capture and ffmpeg, so a stream always starts at the live edge.
ffmpeg's own avfoundation capture drops chunks of audio (vinyl crackle), so PortAudio captures
and feeds ffmpeg through a pipe.
"""
import logging
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler

import sounddevice as sd

from .formats import FORMATS, RATE, ffmpeg_cmd

log = logging.getLogger("mac_sonos")


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = self.server.cfg
        t0 = time.time()
        self.server.gets.append(t0)
        pcm = queue.Queue(maxsize=500)
        first = []
        sent = 0
        log.info("stream: GET from %s", self.client_address[0])

        def on_audio(indata, frames, t, status):
            if not first:
                first.append(time.time() - t0)
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
                    if not sent:
                        log.info("stream: first bytes after %.2fs (capture started after %s)", time.time() - t0,
                                 "%.2fs" % first[0] if first else "never")
                    self.wfile.write(chunk)
                    sent += len(chunk)
        except (BrokenPipeError, ConnectionResetError) as e:
            log.info("stream: client closed (%s)", type(e).__name__)
        except Exception:
            log.exception("stream: failed")
        finally:
            log.info("stream: ended after %.1fs, %d bytes sent", time.time() - t0, sent)
            pcm.put(None)
            ff.kill()

    def log_message(self, *args):
        pass
