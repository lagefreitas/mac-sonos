"""Smoke tests that need no hardware: python -m unittest discover tests"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mac_sonos import i18n
from mac_sonos import config, controller
from mac_sonos.core import output
from mac_sonos.core.formats import FORMATS, ffmpeg_cmd
from mac_sonos.ui import loginitem, model


i18n.set_language("pt")  # the expected texts below are the Portuguese ones


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


class MenuModel(unittest.TestCase):
    def state(self, status, rooms=(), error=None):
        return controller.State(status=status, rooms=list(rooms), error=error)

    def test_status_text(self):
        self.assertEqual(model.status_text(self.state("stopped")), "Parado")
        self.assertEqual(model.status_text(self.state("streaming", ["Office", "TV Room"])),
                         "Tocando em Office, TV Room")
        self.assertIn("boom", model.status_text(self.state("error", error="boom")))

    def test_toggle_label(self):
        self.assertEqual(model.toggle_label("stopped"), "Ligar")
        self.assertEqual(model.toggle_label("error"), "Ligar")
        self.assertEqual(model.toggle_label("streaming"), "Desligar")
        self.assertEqual(model.toggle_label("connecting"), "Desligar")

    def test_toggle_room_keeps_leader_and_last_room(self):
        self.assertEqual(model.toggle_room(["Office"], "TV Room"), ["Office", "TV Room"])
        self.assertEqual(model.toggle_room(["Office", "TV Room"], "Office"), ["TV Room"])
        self.assertEqual(model.toggle_room(["Office"], "Office"), ["Office"])

    def test_room_choices_keeps_missing_selected(self):
        self.assertEqual(model.room_choices(["A", "B"], ["B", "Gone"]), ["A", "B", "Gone"])

    def test_volume_warning(self):
        self.assertIsNone(model.volume_warning(100))
        self.assertIsNone(model.volume_warning(None))
        self.assertIn("31%", model.volume_warning(31))

    def test_retry_only_after_error_and_delay(self):
        self.assertTrue(model.should_retry("error", True, 11))
        self.assertFalse(model.should_retry("error", True, 3))
        self.assertFalse(model.should_retry("error", False, 99))
        self.assertFalse(model.should_retry("streaming", True, 99))

    def test_every_volume_step_is_valid(self):
        self.assertTrue(all(0 < v <= 100 for v in model.VOLUME_STEPS))


class LoginItem(unittest.TestCase):
    def test_not_available_from_source(self):
        self.assertIsNone(loginitem.status())
        self.assertIsNone(loginitem.set_enabled(True))


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
        self.assertIn("'no device' not found", st.error)
        self.assertEqual((st.error_key, st.error_vars), ("error.device_not_found", {"device": "no device"}))
        controller.output.require_device.side_effect = None
        self.ctl.start(["Office"])
        self.assertEqual(self.ctl.wait_for(controller.STREAMING, timeout=5).status, controller.STREAMING)

    def test_set_volume_applies_to_streaming_rooms(self):
        class Speaker:
            volume = 0
        self.ctl.start(["Office"])
        self.ctl.wait_for(controller.STREAMING, timeout=5)
        speaker = Speaker()
        FakeSession.instances[0].rooms = [speaker]
        self.ctl.set_volume(30)
        self.ctl.stop()
        self.ctl.wait_for(controller.STOPPED, timeout=5)
        self.assertEqual(speaker.volume, 30)

    def test_a_room_that_fails_a_volume_change_does_not_end_the_stream(self):
        class Offline:
            player_name = "Office"

            @property
            def volume(self):
                return 0

            @volume.setter
            def volume(self, value):
                raise OSError("timed out")

        class Speaker:
            volume = 0
        self.ctl.start(["Office", "TV Room"])
        self.ctl.wait_for(controller.STREAMING, timeout=5)
        session, speaker = FakeSession.instances[0], Speaker()
        session.rooms = [Offline(), speaker]
        self.ctl.set_volume(30)
        self.ctl.stop(volume=20)   # restoring the volume after a fade fails the same way
        self.ctl.shutdown()        # the volume is restored after STOPPED: wait for the worker to finish
        self.assertEqual(speaker.volume, 20)
        self.assertNotIn(controller.ERROR, self.seen)
        self.assertEqual(self.seen, [controller.CONNECTING, controller.STREAMING, controller.STOPPED])

    def test_set_volume_without_session_is_harmless(self):
        self.ctl.set_volume(30)
        self.ctl.start(["Office"])
        self.assertEqual(self.ctl.wait_for(controller.STREAMING, timeout=5).status, controller.STREAMING)

    def test_wait_for_times_out(self):
        self.assertIsNone(self.ctl.wait_for(controller.STREAMING, timeout=0.1))


if __name__ == "__main__":
    unittest.main()
