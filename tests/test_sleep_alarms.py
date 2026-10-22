"""Sleep timers and alarms without hardware: python -m unittest discover tests"""
import datetime
import unittest
from types import SimpleNamespace
from unittest import mock

from mac_sonos import controller, i18n
from mac_sonos.sonos import alarms as al
from mac_sonos.sonos import monitor as mon
from mac_sonos.sonos.monitor import GroupInfo, PLAYING, STOPPED
from mac_sonos.ui import model

i18n.set_language("pt")
T = datetime.time


class SleepModel(unittest.TestCase):
    def test_timer_remaining_and_expiry(self):
        t = model.SleepTimer(30, 20, now=1000.0)
        self.assertEqual(t.remaining(1000.0), 1800)
        self.assertEqual(t.remaining(1000.0 + 1799.4), 1)
        self.assertFalse(t.expired(1000.0 + 1799))
        self.assertTrue(t.expired(1000.0 + 1800))
        self.assertEqual(t.remaining(5000.0), 0)

    def test_fade_starts_in_the_last_thirty_seconds_and_never_reaches_zero(self):
        t = model.SleepTimer(1, 30, now=0.0)  # 60 s
        self.assertIsNone(t.fade_volume(0.0))
        self.assertIsNone(t.fade_volume(29.9))   # 30.1 s left: not yet
        self.assertEqual(t.fade_volume(30.0), 30)
        self.assertEqual(t.fade_volume(45.0), 15)
        self.assertEqual(t.fade_volume(59.0), 1)
        self.assertEqual(t.fade_volume(60.0), 1)  # volume 0 would be mute, not "faded"
        self.assertEqual(t.fade_volume(75.0), 1)

    def test_fade_is_monotonic(self):
        t = model.SleepTimer(1, 40, now=0.0)
        vols = [t.fade_volume(x / 2) for x in range(60, 121)]
        self.assertEqual(vols, sorted(vols, reverse=True))

    def test_text(self):
        self.assertEqual(model.sleep_text(65), "Restam 1:05")
        self.assertEqual(model.sleep_text(3725), "Restam 1:02:05")


class FakeZone:
    def __init__(self, state=PLAYING, sleep=None):
        self.player_name, self.ip_address, self.is_visible = "Office", "10.0.0.1", True
        self.state, self.sleep, self.set_calls = state, sleep, []

    def get_current_transport_info(self):
        return {"current_transport_state": self.state}

    def get_current_track_info(self):
        return {"title": "t", "artist": "a", "album": "", "album_art": "", "uri": "x:y", "position": "0:00:01",
                "duration": "0:03:00", "playlist_position": "1"}

    def get_current_media_info(self):
        return {"channel": ""}

    available_actions = ["Play"]

    def get_sleep_timer(self):
        if isinstance(self.sleep, Exception):
            raise self.sleep
        return self.sleep

    def set_sleep_timer(self, seconds):
        self.set_calls.append(seconds)
        self.sleep = seconds


class SleepMonitor(unittest.TestCase):
    def test_remaining_is_read_only_while_playing(self):
        playing = mon.read_group(FakeZone(PLAYING, 600), ("Office",), now=1000.0)
        self.assertEqual((playing.sleeping, playing.sleep_remaining, playing.sleep_active), (True, 600, True))
        self.assertEqual(playing.sleep_left(now=1030.0), 570)
        self.assertEqual(playing.sleep_left(now=9999.0), 0)
        stopped = mon.read_group(FakeZone(STOPPED, 600), ("Office",), now=1000.0)
        self.assertEqual((stopped.sleeping, stopped.sleep_left()), (False, None))
        none = mon.read_group(FakeZone(PLAYING, None), ("Office",), now=1000.0)
        self.assertFalse(none.sleeping)

    def test_unreadable_timer_means_none(self):
        info = mon.read_group(FakeZone(PLAYING, OSError("x")), ("Office",), now=1000.0)
        self.assertEqual((info.sleeping, info.sleep_remaining), (False, None))

    def test_countdown_is_not_a_change_but_setting_or_cancelling_is(self):
        a = mon.read_group(FakeZone(PLAYING, 600), ("Office",), now=1000.0)
        b = mon.read_group(FakeZone(PLAYING, 540), ("Office",), now=1060.0)
        off = mon.read_group(FakeZone(PLAYING, None), ("Office",), now=1060.0)
        self.assertEqual(a, b)
        self.assertNotEqual(a, off)

    def test_commands_set_and_cancel_the_native_timer(self):
        zone = FakeZone()
        group = mon._Group(zone, ("Office",), (zone,))
        m = mon.Monitor()
        m._execute(group, "sleep", 15, 0.0)
        m._execute(group, "sleep_cancel", None, 0.0)
        self.assertEqual(zone.set_calls, [900, None])


class SleepAndRooms(unittest.TestCase):
    def test_zone_for_room_and_group_lookups(self):
        z = FakeZone()
        m = mon.Monitor()
        m._groups = {"Office": mon._Group(z, ("Office",), (z,))}
        self.assertIs(m.zone_for_room("Office"), z)
        self.assertIsNone(m.zone_for_room("Nowhere"))
        self.assertIs(m.zone_for_group("Office"), z)
        self.assertIs(m.any_zone(), z)


class AlarmParsing(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(al.parse_time("07:30"), T(7, 30))
        self.assertEqual(al.parse_time(" 7:05 "), T(7, 5))
        self.assertEqual(al.parse_time("23:59:30"), T(23, 59, 30))
        for bad in ("", None, "7", "24:00", "12:60", "ab:cd", "7h30", "07:3", "-1:00"):
            self.assertIsNone(al.parse_time(bad), bad)

    def test_format_and_recurrence(self):
        self.assertEqual(al.format_time(T(7, 5, 59)), "07:05")
        for ok in ("DAILY", "ONCE", "WEEKDAYS", "WEEKENDS", "ON_0", "ON_135", "ON_0123456"):
            self.assertTrue(al.valid_recurrence(ok), ok)
        for bad in ("", None, "weekly", "ON_", "ON_7", "ON_12345678", "ON_1a"):
            self.assertFalse(al.valid_recurrence(bad), bad)
        self.assertEqual(al.days_of("ON_531"), [1, 3, 5])
        self.assertEqual(al.days_of("DAILY"), [])

    def test_labels_and_titles(self):
        self.assertEqual(model.recurrence_label("DAILY"), "Todos os dias")
        self.assertEqual(model.recurrence_label("WEEKDAYS"), "Dias úteis")
        self.assertEqual(model.recurrence_label("ON_036"), "Dom, Qua, Sáb")
        self.assertEqual(model.recurrence_label("WHAT"), "WHAT")
        info = al.AlarmInfo("3", "Office", T(7, 30), "WEEKDAYS", True, 20)
        self.assertEqual(model.alarm_title(info), "07:30 · Office · Dias úteis")


class FakeAlarm:
    saved, removed, created = [], [], []

    def __init__(self, zone, start_time=None, recurrence="DAILY", enabled=True, volume=20, alarm_id="1"):
        self.zone, self.start_time, self.recurrence, self.enabled, self.volume = zone, start_time, recurrence, enabled, volume
        self.alarm_id = alarm_id
        FakeAlarm.created.append(self)

    def save(self):
        FakeAlarm.saved.append(self)

    def remove(self):
        FakeAlarm.removed.append(self)


class AlarmManagerTests(unittest.TestCase):
    def setUp(self):
        FakeAlarm.saved, FakeAlarm.removed, FakeAlarm.created = [], [], []
        self.zone = SimpleNamespace(player_name="Office")
        self.stored = [FakeAlarm(self.zone, T(8, 0), "WEEKDAYS", True, 25, "2"),
                       FakeAlarm(self.zone, T(6, 30), "DAILY", False, 15, "1")]
        FakeAlarm.created.clear()
        patch = mock.patch.object(al, "soco_alarms", SimpleNamespace(get_alarms=lambda zone: list(self.stored), Alarm=FakeAlarm))
        patch.start()
        self.addCleanup(patch.stop)
        self.changes = []
        self.m = al.AlarmManager(lambda: self.zone, lambda room: self.zone if room == "Office" else None,
                                 on_change=lambda: self.changes.append(1))

    def run_job(self, job, now=1000.0):
        self.m._step(job, now)

    def test_alarms_are_loaded_sorted_by_time(self):
        self.run_job(None)
        self.assertEqual([(a.alarm_id, str(a.time)) for a in self.m.alarms], [("1", "06:30:00"), ("2", "08:00:00")])
        self.assertEqual(self.m.version, 1)
        self.run_job(None, 1000.0 + al.RELOAD_EVERY + 1)   # same alarms: no new version
        self.assertEqual(self.m.version, 1)

    def test_nothing_happens_without_a_zone(self):
        m = al.AlarmManager(lambda: None, lambda r: None)
        m._step(None, 1000.0)
        self.assertEqual(m.alarms, [])

    def test_add_creates_and_saves_then_reloads(self):
        self.m.add("Office", T(7, 0), "WEEKENDS", 30, enabled=False)
        self.run_job(self.m._jobs.get_nowait())
        new = FakeAlarm.saved[-1]
        self.assertEqual((new.start_time, new.recurrence, new.volume, new.enabled), (T(7, 0), "WEEKENDS", 30, False))
        self.assertEqual(self.m.version, 1)  # read back right after

    def test_add_validates(self):
        self.m.add("Nowhere", T(7, 0))
        with self.assertRaises(LookupError):
            self.run_job(self.m._jobs.get_nowait())
        self.m.add("Office", T(7, 0), "FORTNIGHTLY")
        with self.assertRaises(ValueError):
            self.run_job(self.m._jobs.get_nowait())
        self.assertEqual(FakeAlarm.saved, [])

    def test_change_applies_fields_and_saves(self):
        self.run_job(None)
        self.m.change("1", enabled=True, start_time=T(6, 45), recurrence="ON_135", volume=40)
        self.run_job(self.m._jobs.get_nowait())
        a = self.stored[1]
        self.assertEqual((a.enabled, a.start_time, a.recurrence, a.volume), (True, T(6, 45), "ON_135", 40))
        self.assertIn(a, FakeAlarm.saved)

    def test_change_rejects_bad_recurrence_and_unknown_alarm_without_touching_anything(self):
        self.run_job(None)
        self.m.change("1", recurrence="nope", volume=99)
        with self.assertRaises(ValueError):
            self.run_job(self.m._jobs.get_nowait())
        self.assertEqual(self.stored[1].volume, 15)  # not half-applied
        self.m.change("zzz", enabled=True)
        with self.assertRaises(LookupError):
            self.run_job(self.m._jobs.get_nowait())
        self.assertEqual(FakeAlarm.saved, [])

    def test_remove(self):
        self.run_job(None)
        self.m.remove("2")
        self.run_job(self.m._jobs.get_nowait())
        self.assertEqual([a.alarm_id for a in FakeAlarm.removed], ["2"])

    def test_worker_starts_and_stops(self):
        self.m.start()
        self.m.reload()
        self.m.stop()
        self.assertFalse(self.m._thread.is_alive())


class StopWithVolume(unittest.TestCase):
    def test_volume_is_restored_after_the_stop(self):
        events = []

        class Speaker:
            def __init__(self, name):
                self.name = name

            def __setattr__(self, key, value):
                if key == "volume":
                    events.append(("volume", self.name, value))
                object.__setattr__(self, key, value)

        class Session:
            def __init__(self, speakers, *a):
                self.rooms = [Speaker(n) for n in speakers]

            def start(self):
                return "http://x"

            def stop(self):
                events.append(("stopped",))

        patches = [mock.patch.object(controller.output, "require_device"),
                   mock.patch.object(controller.output, "OutputSwitch", mock.MagicMock()),
                   mock.patch.object(controller.session_mod, "Session", Session),
                   mock.patch.object(controller.discovery, "find_rooms", lambda names: list(names))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        ctl = controller.Controller()
        self.addCleanup(ctl.shutdown)
        ctl.start(["Office", "TV Room"])
        ctl.wait_for(controller.STREAMING, timeout=5)
        ctl.stop(volume=18)
        ctl.wait_for(controller.STOPPED, timeout=5)
        ctl.shutdown()
        self.assertEqual(events, [("stopped",), ("volume", "Office", 18), ("volume", "TV Room", 18)])


if __name__ == "__main__":
    unittest.main()
