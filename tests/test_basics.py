"""Smoke tests that need no hardware: python -m unittest discover tests"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mac_sonos import config, controller
from mac_sonos.core import output
from mac_sonos.core.formats import FORMATS, ffmpeg_cmd


class FfmpegCmd(unittest.TestCase):
    def cfg(self, **kw):
        return {"fmt": "mp3", "bitrate": None, "lowlat": False, **kw}

    def test_default_bitrate_and_pipes(self):
        cmd = ffmpeg_cmd(self.cfg())
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "192k")
        self.assertIn("pipe:0", cmd)
        self.assertEqual(cmd[-1], "pipe:1")

    def test_explicit_bitrate_wins(self):
        cmd = ffmpeg_cmd(self.cfg(bitrate="128k"))
        self.assertEqual(cmd[cmd.index("-b:a") + 1], "128k")

    def test_lossless_has_no_bitrate(self):
        self.assertNotIn("-b:a", ffmpeg_cmd(self.cfg(fmt="flac")))

    def test_lowlat_flags(self):
        self.assertIn("+nobuffer", ffmpeg_cmd(self.cfg(lowlat=True)))
        self.assertNotIn("+nobuffer", ffmpeg_cmd(self.cfg()))

    def test_every_format_builds(self):
        for fmt in FORMATS:
            self.assertEqual(ffmpeg_cmd(self.cfg(fmt=fmt))[0], "ffmpeg")


class Config(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(config.load(Path(d) / "none.json"), config.DEFAULTS)

    def test_roundtrip_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "c.json"
            config.save({**config.DEFAULTS, "rooms": ["Office", "TV Room"], "junk": 1}, p)
            got = config.load(p)
            self.assertEqual(got["rooms"], ["Office", "TV Room"])
            self.assertNotIn("junk", got)

    def test_corrupt_file_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text("{not json")
            self.assertEqual(config.load(p), config.DEFAULTS)


class OutputSwitchRestore(unittest.TestCase):
    def run_switch(self, current, remembered):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(stdout=current if "-c" in cmd else "")

        with tempfile.TemporaryDirectory() as d:
            mem = Path(d) / "prev"
            if remembered:
                mem.write_text(remembered)
            with mock.patch.object(output, "REMEMBERED", mem), \
                    mock.patch.object(output.shutil, "which", return_value="sas"), \
                    mock.patch.object(output.subprocess, "run", fake_run):
                with output.OutputSwitch("BlackHole 2ch"):
                    pass
                return [c[2] for c in calls if c[:2] == ["sas", "-s"]], mem.read_text()

    def test_restores_current_output(self):
        sets, saved = self.run_switch("LG ULTRAWIDE", None)
        self.assertEqual(sets, ["BlackHole 2ch", "LG ULTRAWIDE"])
        self.assertEqual(saved, "LG ULTRAWIDE")

    def test_after_killed_run_uses_remembered_output(self):
        sets, _ = self.run_switch("BlackHole 2ch", "MacBook Air Speakers")
        self.assertEqual(sets, ["BlackHole 2ch", "MacBook Air Speakers"])


class FakeSession:
    instances = []

    def __init__(self, speakers, *args):
        self.speakers, self.stopped = speakers, False
        FakeSession.instances.append(self)

    def start(self):
        return "http://x/mac.mp3"

    def stop(self):
        self.stopped = True


class FakeSwitch:
    log = []

    def __init__(self, device):
        pass

    def __enter__(self):
        FakeSwitch.log.append("on")
        return self

    def __exit__(self, *exc):
        FakeSwitch.log.append("off")


class ControllerStates(unittest.TestCase):
    def setUp(self):
        FakeSession.instances, FakeSwitch.log = [], []
        self.patches = [
            mock.patch.object(controller.output, "require_device"),
            mock.patch.object(controller.output, "OutputSwitch", FakeSwitch),
            mock.patch.object(controller.session_mod, "Session", FakeSession),
            mock.patch.object(controller.discovery, "find_rooms", lambda names: list(names)),
        ]
        for p in self.patches:
            p.start()
        self.seen = []
        self.ctl = controller.Controller(on_change=lambda s: self.seen.append(s.status))

    def tearDown(self):
        self.ctl.shutdown()
        for p in self.patches:
            p.stop()

    def test_start_then_stop(self):
        self.ctl.start(["Office"])
        st = self.ctl.wait_for(controller.STREAMING, controller.ERROR, timeout=5)
        self.assertEqual((st.status, st.url, st.rooms), (controller.STREAMING, "http://x/mac.mp3", ["Office"]))
        self.ctl.stop()
        self.ctl.wait_for(controller.STOPPED, timeout=5)
        self.assertTrue(FakeSession.instances[0].stopped)
        self.assertEqual(FakeSwitch.log, ["on", "off"])
        self.assertEqual(self.seen, [controller.CONNECTING, controller.STREAMING, controller.STOPPED])

    def test_restart_stops_previous_session(self):
        self.ctl.start(["Office"])
        self.ctl.wait_for(controller.STREAMING, timeout=5)
        self.ctl.start(["TV Room"])
        self.ctl.shutdown()
        self.assertEqual(len(FakeSession.instances), 2)
        self.assertTrue(all(s.stopped for s in FakeSession.instances))
        self.assertEqual(FakeSwitch.log, ["on", "off", "on", "off"])

    def test_error_is_reported_and_worker_survives(self):
        controller.output.require_device.side_effect = controller.output.DeviceNotFound("no device")
        self.ctl.start(["Office"])
        st = self.ctl.wait_for(controller.ERROR, timeout=5)
        self.assertEqual(st.error, "no device")
        controller.output.require_device.side_effect = None
        self.ctl.start(["Office"])
        self.assertEqual(self.ctl.wait_for(controller.STREAMING, timeout=5).status, controller.STREAMING)

    def test_wait_for_times_out(self):
        self.assertIsNone(self.ctl.wait_for(controller.STREAMING, timeout=0.1))


if __name__ == "__main__":
    unittest.main()
