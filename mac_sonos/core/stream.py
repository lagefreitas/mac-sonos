"""HTTP stream of the captured audio.

Each Sonos connection gets its own reader of the capture (see capture.py) and its own ffmpeg, so a stream always
starts at the live edge. ffmpeg's own avfoundation capture drops chunks of audio (vinyl crackle), so the audio is
captured elsewhere and fed to ffmpeg through a pipe.
"""
import contextlib
import logging
import queue
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler

from .formats import FORMATS, ffmpeg_cmd

log = logging.getLogger("mac_sonos")

QUEUE_BLOCKS = 500   # captured blocks waiting for ffmpeg (about 5 s); newer ones are dropped while it is full


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        cfg = self.server.cfg
        t0 = time.time()
        self.server.gets.append(t0)
        pcm = queue.Queue(maxsize=QUEUE_BLOCKS)
        first = []
        sent = 0
        log.info("stream: GET from %s", self.client_address[0])

        def on_audio(chunk):
            if not first:
                first.append(time.time() - t0)
            try:
                pcm.put_nowait(chunk)
            except queue.Full:
                pass

        capture = cfg["capture"]
        ff = subprocess.Popen(ffmpeg_cmd({**cfg, "rate": capture.rate}), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)

        def feed():
            try:
                while (block := pcm.get()) is not None:
                    ff.stdin.write(block)
            except (BrokenPipeError, ValueError, OSError):
                pass

        threading.Thread(target=feed, daemon=True).start()
        try:
            with capture.open(on_audio):
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
            ff.kill()   # first: a stalled client leaves ffmpeg, and feed() behind it, blocked with the queue full
            with contextlib.suppress(queue.Full):
                pcm.put_nowait(None)   # feed() may be waiting on an empty queue
            for pipe in (ff.stdin, ff.stdout):   # kill() alone leaves the pipes open and a zombie behind
                try:
                    pipe.close()
                except OSError:
                    pass
            try:
                ff.wait(timeout=2)
            except subprocess.TimeoutExpired:
                log.warning("stream: ffmpeg did not exit")

    def log_message(self, *args):
        pass
