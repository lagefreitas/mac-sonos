"""Capture sources (BlackHole and system audio tap) without hardware: python -m unittest discover tests"""
import contextlib
import ctypes
import math
import shutil
import threading
import time
import unittest
import urllib.request
from array import array
from http.server import ThreadingHTTPServer
from unittest import mock

from mac_sonos import controller, i18n
from mac_sonos.core import capture, output, tapcapture as tc
from mac_sonos.core.formats import ffmpeg_cmd
from mac_sonos.core.stream import StreamHandler

i18n.set_language("pt")


class Factory(unittest.TestCase):
    def test_kinds(self):
        self.assertIsInstance(capture.make_capture("blackhole", "My Device"), capture.BlackHoleCapture)
        self.assertEqual(capture.make_capture("blackhole", "My Device").device, "My Device")
        tap = capture.make_capture("tap", mute_mac=False)
        self.assertIsInstance(tap, capture.TapCapture)
        self.assertFalse(tap.mute_mac)
        self.assertEqual(capture.KINDS, ("blackhole", "tap"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            capture.make_capture("microphone")

    def test_ffmpeg_takes_the_rate_of_the_capture(self):
        base = {"fmt": "mp3", "bitrate": None, "lowlat": False}
        for rate, expected in ((None, "48000"), (44100.0, "44100"), (96000, "96000")):
            cmd = ffmpeg_cmd({**base, "rate": rate})
            self.assertEqual(cmd[cmd.index("-ar") + 1], expected)
        self.assertEqual(ffmpeg_cmd(base)[ffmpeg_cmd(base).index("-ar") + 1], "48000")   # no rate given: BlackHole's


class BlackHole(unittest.TestCase):
    def test_prepare_checks_the_device_and_the_output_is_switched(self):
        c = capture.BlackHoleCapture("BlackHole 2ch")
        with mock.patch.object(output, "require_device") as require, mock.patch.object(output, "OutputSwitch") as switch:
            c.prepare()
            ctx = c.output_context()
        require.assert_called_once_with("BlackHole 2ch")
        switch.assert_called_once_with("BlackHole 2ch")
        self.assertIs(ctx, switch.return_value)
        self.assertEqual(c.rate, 48000)

    def test_a_missing_device_fails_in_prepare(self):
        with mock.patch.object(output, "require_device", side_effect=output.DeviceNotFound("X")):
            with self.assertRaises(output.DeviceNotFound):
                capture.BlackHoleCapture("X").prepare()

    def test_open_streams_the_device_through_portaudio(self):
        events = []

        class Stream:
            def __init__(self, **kw):
                events.append(("open", kw["device"], kw["samplerate"], kw["channels"], kw["dtype"]))
                self.cb = kw["callback"]

            def __enter__(self):
                self.cb(memoryview(b"\x01\x02\x03\x04"), 1, None, None)
                return self

            def __exit__(self, *a):
                events.append(("close",))
        with mock.patch("sounddevice.RawInputStream", Stream):
            pushed = []
            with capture.BlackHoleCapture("BH").open(pushed.append):
                pass
        self.assertEqual(events, [("open", "BH", 48000, 2, "int16"), ("close",)])
        self.assertEqual(pushed, [b"\x01\x02\x03\x04"])


class FakeSource:
    """Stands in for tapcapture.TapSource."""
    instances, outcomes = [], []

    def __init__(self, mute_local=True, **kw):
        self.mute_local, self.rate, self.started, self.stopped = mute_local, 48000.0, False, False
        FakeSource.instances.append(self)

    def start(self):
        outcome = FakeSource.outcomes.pop(0) if FakeSource.outcomes else None
        if outcome:
            raise outcome
        self.started = True

    def stop(self):
        self.stopped = True

    def open(self, push):
        return contextlib.nullcontext()


class TapPermissionFlow(unittest.TestCase):
    def setUp(self):
        FakeSource.instances, FakeSource.outcomes = [], []
        self.permission, self.request = "granted", mock.Mock(return_value=True)
        for p in (mock.patch.object(tc, "TapSource", FakeSource),
                  mock.patch.object(tc, "audio_permission", lambda: self.permission),
                  mock.patch.object(tc, "request_audio_permission", self.request)):
            p.start()
            self.addCleanup(p.stop)
        self.c = capture.TapCapture(mute_mac=True)
        delay = mock.patch.object(capture, "RETRY_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)

    def test_it_just_starts_when_audio_flows(self):
        self.c.start()
        self.assertTrue(FakeSource.instances[0].started)
        self.assertEqual((self.c.rate, FakeSource.instances[0].mute_local), (48000.0, True))
        self.request.assert_not_called()   # no need to ask: the audio is there

    def test_the_mute_option_reaches_the_source(self):
        capture.TapCapture(mute_mac=False).start()
        self.assertFalse(FakeSource.instances[-1].mute_local)

    def test_no_audio_and_never_asked_then_it_asks_and_retries(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission = "undetermined"
        self.c.start()
        self.request.assert_called_once()
        self.assertEqual(len(FakeSource.instances), 2)
        self.assertTrue(FakeSource.instances[1].started)

    def test_the_user_says_no(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission, self.request.return_value = "undetermined", False
        with self.assertRaises(tc.TapPermissionError):
            self.c.start()

    def test_the_user_does_not_answer(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission, self.request.return_value = "undetermined", None
        with self.assertRaises(tc.TapPermissionError):
            self.c.start()
        self.assertEqual(len(FakeSource.instances), 1)   # no second attempt without an answer

    def test_denied_does_not_ask_again(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission = "denied"
        with self.assertRaises(tc.TapPermissionError):
            self.c.start()
        self.request.assert_not_called()

    def test_granted_but_still_silent_is_reported_as_silence_after_a_few_tries(self):
        """What happens for real when nothing plays on the Mac: the tap only delivers once something does."""
        FakeSource.outcomes = [tc.TapNoAudio("no audio")] * 4
        with self.assertRaises(tc.TapSilent):
            self.c.start()
        self.assertEqual(len(FakeSource.instances), 1 + capture.ATTEMPTS_AFTER_GRANT)

    def test_still_silent_and_the_permission_unknown_is_reported_as_permission_trouble(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")] * 4
        self.permission = "unavailable"
        with self.assertRaises(tc.TapPermissionError):
            self.c.start()

    def test_silence_is_retried_but_a_missing_permission_is_not(self):
        from mac_sonos.ui import model
        self.assertTrue(model.should_retry("error", True, 11, error_key=tc.TapSilent.key))
        self.assertFalse(model.should_retry("error", True, 999, error_key=tc.TapPermissionError.key))

    def test_a_permission_just_granted_takes_a_moment_to_apply(self):
        """What happened for real: the dialog was answered during the first attempt, and the next one, right away,
        still got no audio; the third worked."""
        FakeSource.outcomes = [tc.TapNoAudio("no audio"), tc.TapNoAudio("no audio")]
        self.c.start()                                  # state is "granted": no dialog, only patience
        self.assertEqual(len(FakeSource.instances), 3)
        self.assertTrue(FakeSource.instances[2].started)
        self.request.assert_not_called()

    def test_the_attempts_are_spaced_out(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")] * 4
        with mock.patch.object(capture, "RETRY_DELAY", 1.5), mock.patch.object(capture.time, "sleep") as sleep:
            with self.assertRaises(tc.TapSilent):
                self.c.start()
        self.assertEqual([c.args for c in sleep.call_args_list], [(1.5,)] * capture.ATTEMPTS_AFTER_GRANT)

    def test_the_dialog_is_answered_while_we_wait_and_then_the_audio_comes(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio"), tc.TapNoAudio("no audio")]
        self.permission, self.request.return_value = "undetermined", True
        self.c.start()
        self.assertEqual(len(FakeSource.instances), 3)

    def test_no_answer_to_the_request_but_the_permission_appears_granted(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission, self.request.return_value = "undetermined", None

        def request(timeout):
            self.permission = "granted"   # answered in the dialog; our call just never heard back
            return None
        self.request.side_effect = request
        self.c.start()
        self.assertTrue(FakeSource.instances[-1].started)

    def test_when_the_permission_state_cannot_be_read_it_still_tries_again(self):
        FakeSource.outcomes = [tc.TapNoAudio("no audio")]
        self.permission = "unavailable"
        self.c.start()
        self.request.assert_not_called()
        self.assertEqual(len(FakeSource.instances), 2)

    def test_other_errors_pass_through_untouched(self):
        FakeSource.outcomes = [tc.TapError("create the process tap", -50)]
        with self.assertRaises(tc.TapError):
            self.c.start()
        self.request.assert_not_called()

    def test_stop_and_open(self):
        self.c.start()
        src = FakeSource.instances[0]
        with self.c.open(lambda chunk: None):
            pass
        self.c.stop()
        self.assertTrue(src.stopped)
        self.c.stop()   # harmless twice
        self.assertIsNone(self.c.source)

    def test_it_leaves_the_macs_output_alone(self):
        with self.c.output_context() as ctx:
            self.assertIsNone(ctx)

    def test_the_error_is_localizable(self):
        e = tc.TapPermissionError()
        self.assertIn("System Audio Recording", str(e))
        for error in (e, tc.TapSilent()):
            for lang in ("pt", "en", "es", "fr"):
                self.assertIn(error.key, i18n.catalog(lang))
        self.assertEqual(str(tc.TapSilent()), i18n.catalog("en")[tc.TapSilent.key])


class Conversion(unittest.TestCase):
    def test_float_to_int16_scales_and_clips(self):
        out = memoryview(tc.float_to_int16(array("f", [0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -3.0]))).cast("h").tolist()
        self.assertEqual(out, [0, 16383, -16383, 32767, -32768, 32767, -32768])

    def test_interleaving(self):
        stereo = array("f", [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(tc.interleave_stereo([(2, stereo)]).tolist(), stereo.tolist())
        mono = tc.interleave_stereo([(1, array("f", [0.5, 0.25]))])
        self.assertEqual(mono.tolist(), [0.5, 0.5, 0.25, 0.25])
        surround = tc.interleave_stereo([(4, array("f", [1, 2, 3, 4, 5, 6, 7, 8]))])
        self.assertEqual(surround.tolist(), [1, 2, 5, 6])    # only the first two channels
        split = tc.interleave_stereo([(1, array("f", [1, 2, 3])), (1, array("f", [10, 20, 30]))])
        self.assertEqual(split.tolist(), [1, 10, 2, 20, 3, 30])


def audio_buffer_list(ca, buffers):
    """A real AudioBufferList in memory, as Core Audio hands one to an IOProc: [(channels, [floats])]."""
    arrays = [(ch, (ctypes.c_float * len(data))(*data)) for ch, data in buffers]
    raw = ctypes.create_string_buffer(8 + 16 * len(buffers))
    ctypes.c_uint32.from_buffer(raw, 0).value = len(buffers)
    for i, (ch, arr) in enumerate(arrays):
        b = ca.AudioBuffer.from_buffer(raw, 8 + 16 * i)
        b.channels, b.size, b.data = ch, ctypes.sizeof(arr), ctypes.addressof(arr)
    return raw, arrays


class TapSourceCallback(unittest.TestCase):
    def setUp(self):
        self.src = tc.TapSource()
        self.src._ca = tc._core_audio()
        self.src._lock, self.src._first = threading.Lock(), threading.Event()

    def feed(self, buffers):
        raw, keep = audio_buffer_list(self.src._ca, buffers)
        self.src._on_buffers(ctypes.addressof(raw))
        return keep

    def test_interleaved_stereo_reaches_every_reader_as_int16(self):
        a, b = [], []
        with self.src.open(a.append), self.src.open(b.append):
            self.feed([(2, [0.5, -0.5, 0.25, -0.25])])
        expected = array("h", [16383, -16383, 8191, -8191]).tobytes()
        self.assertEqual((a, b), ([expected], [expected]))
        self.assertTrue(self.src._first.is_set())

    def test_non_interleaved_buffers(self):
        got = []
        with self.src.open(got.append):
            self.feed([(1, [0.5, 0.25]), (1, [-0.5, -0.25])])
        self.assertEqual(memoryview(got[0]).cast("h").tolist(), [16383, -16383, 8191, -8191])

    def test_a_reader_stops_hearing_when_it_closes(self):
        got = []
        with self.src.open(got.append):
            self.feed([(2, [0.1, 0.1])])
        self.feed([(2, [0.9, 0.9])])
        self.assertEqual(len(got), 1)

    def test_silence_with_no_reader_costs_no_conversion_but_marks_audio_as_arrived(self):
        with mock.patch.object(tc, "float_to_int16") as convert:
            self.feed([(2, [0.0, 0.0])])
        convert.assert_not_called()
        self.assertTrue(self.src._first.is_set())

    def test_an_empty_or_null_list_is_ignored(self):
        self.src._on_buffers(None)
        self.src._on_buffers(0)
        self.feed([])
        self.assertFalse(self.src._first.is_set())


class StreamEndToEnd(unittest.TestCase):
    """The real HTTP handler and the real ffmpeg, fed by a fake capture at an unusual rate. If the handler assumed
    48 kHz instead of asking the capture, the 1 kHz tone would come out at the wrong pitch."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_the_tone_keeps_its_pitch_at_the_captures_rate(self):
        rate, freq = 44100, 1000

        class Fake:
            def __init__(self):
                self.rate = rate

            @contextlib.contextmanager
            def open(self, push):
                stop = threading.Event()

                def run():
                    n = 0
                    while not stop.is_set():
                        frames = 441   # 10 ms of stereo tone
                        samples = array("h", [int(8000 * math.sin(2 * math.pi * freq * (n + i) / rate)) for i in range(frames) for _ in (0, 1)])
                        push(samples.tobytes())
                        n += frames
                        time.sleep(0.01)
                t = threading.Thread(target=run, daemon=True)
                t.start()
                try:
                    yield
                finally:
                    stop.set()
                    t.join(2)

        server = ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
        server.cfg = dict(capture=Fake(), fmt="wav", bitrate=None, lowlat=False)
        server.gets = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/mac.wav", timeout=10) as response:
            body = b""
            while len(body) < 44100 * 4:   # about a second of stereo 16-bit
                chunk = response.read(4096)
                if not chunk:
                    break
                body += chunk
        data = body[body.index(b"data") + 8:]
        left = memoryview(data[: len(data) // 4 * 4]).cast("h")[0::2]
        window = left[4410:4410 + 22050]     # skip the start, half a second
        crossings = sum(1 for a, b in zip(window, window[1:]) if a < 0 <= b)
        measured = crossings / (len(window) / 44100)
        self.assertAlmostEqual(measured, freq, delta=25, msg=f"tone came out at {measured:.0f} Hz instead of {freq}")
        self.assertEqual(len(server.gets), 1)


class StalledClient(unittest.TestCase):
    """A client that stops reading fills the socket, then ffmpeg's pipes, then the queue of captured blocks. When it
    finally drops the connection the handler must still end and take its ffmpeg along, instead of waiting forever
    for a feeder that is itself stuck on ffmpeg."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_the_handler_ends_and_kills_ffmpeg_after_a_stall(self):
        import socket
        import struct
        import subprocess
        from mac_sonos.core import stream

        class Fast:   # far faster than real time, so the buffers fill in a moment
            rate = 48000

            @contextlib.contextmanager
            def open(self, push):
                stop = threading.Event()

                def run():
                    while not stop.is_set():
                        push(b"\x01\x02" * 9600)
                        time.sleep(0.002)
                threading.Thread(target=run, daemon=True).start()
                try:
                    yield
                finally:
                    stop.set()

        done, procs = threading.Event(), []

        class Handler(StreamHandler):
            def do_GET(self):
                try:
                    super().do_GET()
                finally:
                    done.set()

        real_popen = subprocess.Popen

        def popen(*args, **kwargs):
            procs.append(real_popen(*args, **kwargs))
            return procs[-1]

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.cfg = dict(capture=Fast(), fmt="wav", bitrate=None, lowlat=False)
        server.gets = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(stream, "QUEUE_BLOCKS", 5), mock.patch.object(stream.subprocess, "Popen", popen):
            client = socket.socket()
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            client.connect(server.server_address)
            client.sendall(b"GET /mac.wav HTTP/1.0\r\n\r\n")
            time.sleep(2.5)   # never read
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()    # reset, as a speaker that gives up does
            ended = done.wait(5)
        for proc in procs:
            self.addCleanup(proc.kill)
        self.assertTrue(ended, "the handler is still blocked after the client left")
        self.assertEqual(len(procs), 1)
        self.assertIsNotNone(procs[0].poll(), "ffmpeg is still running")


class ControllerModes(unittest.TestCase):
    def setUp(self):
        self.log = []
        log = self.log

        class Source:
            kind = "x"
            rate = 48000

            def __init__(self, name, fail=None):
                self.name, self.fail = name, fail

            def prepare(self): log.append((self.name, "prepare"))

            def output_context(self):
                log.append((self.name, "output_context"))
                return contextlib.nullcontext()

            def start(self):
                log.append((self.name, "start"))
                if self.fail:
                    raise self.fail

            def stop(self): log.append((self.name, "stop"))

        class Session:
            def __init__(inner, speakers, source, *a):
                inner.source, inner.rooms = source, speakers

            def start(inner): return "http://x/mac.mp3"

            def stop(inner): log.append(("session", "stop"))
        self.Source, self.fail = Source, None
        self.made = []

        def make(kind, device, mute_mac):
            self.made.append((kind, device, mute_mac))
            return Source(kind, self.fail)
        for p in (mock.patch.object(capture, "make_capture", make),
                  mock.patch.object(controller.session_mod, "Session", Session),
                  mock.patch.object(controller.discovery, "find_rooms", lambda names: list(names))):
            p.start()
            self.addCleanup(p.stop)
        self.ctl = controller.Controller()
        self.addCleanup(self.ctl.shutdown)

    def test_the_capture_choice_and_mute_option_reach_the_factory(self):
        self.ctl.start(["Office"], "BlackHole 2ch", capture="tap", mute_mac=False)
        self.ctl.wait_for(controller.STREAMING, controller.ERROR, timeout=5)
        self.assertEqual(self.made, [("tap", "BlackHole 2ch", False)])
        self.ctl.start(["Office"])
        self.ctl.wait_for(controller.STREAMING, timeout=5)
        self.ctl.shutdown()
        self.assertEqual(self.made[-1], ("blackhole", "BlackHole 2ch", True))   # the defaults

    def test_order_of_events_on_start_and_stop(self):
        self.ctl.start(["Office"], capture="tap")
        self.ctl.wait_for(controller.STREAMING, timeout=5)
        self.ctl.stop()
        self.ctl.wait_for(controller.STOPPED, timeout=5)
        self.assertEqual(self.log, [("tap", "prepare"), ("tap", "output_context"), ("tap", "start"),
                                    ("session", "stop"), ("tap", "stop")])   # the session ends before the capture

    def test_a_permission_error_is_reported_with_its_key_and_leaves_nothing_running(self):
        self.fail = tc.TapPermissionError()
        self.ctl.start(["Office"], capture="tap")
        st = self.ctl.wait_for(controller.ERROR, timeout=5)
        self.assertEqual(st.error_key, "error.tap_permission")
        self.assertIn("System Audio Recording", st.error)
        self.assertNotIn(("session", "stop"), self.log)
        from mac_sonos.ui import model
        self.assertIn("Gravação de Áudio do Sistema", model.status_text(st))
        self.fail = None   # and the worker is still alive: a retry with the other source works
        self.ctl.start(["Office"], capture="blackhole")
        self.assertEqual(self.ctl.wait_for(controller.STREAMING, timeout=5).status, controller.STREAMING)


if __name__ == "__main__":
    unittest.main()
