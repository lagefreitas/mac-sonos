"""Scenes and media keys without hardware: python -m unittest discover tests"""
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from mac_sonos import i18n, scenes
from mac_sonos.sonos.monitor import GroupInfo, PAUSED, PLAYING, RoomSettings, STOPPED
from mac_sonos.ui import mediakeys, model

i18n.set_language("pt")


def group(coordinator, members=None, state=STOPPED, levels=(), uri="", channel="", title="", actions=()):
    return GroupInfo(coordinator, tuple(members or [coordinator]), state, uri=uri, channel=channel, title=title,
                     levels=tuple(levels), actions=frozenset(actions))


HOUSE = [group("Office", levels=[("Office", 20, False)]),
         group("TV Room", levels=[("TV Room", 30, True)]),
         group("Sala", levels=[("Sala", 40, False)])]


class Validate(unittest.TestCase):
    def test_good_scenes(self):
        self.assertEqual(scenes.validate({"name": "x"}), [])
        full = {"name": "Cinema", "stream": "off", "groups": [["TV Room"], ["Office"]],
                "rooms": {"TV Room": {"volume": 25, "dialog_mode": True}}, "source": {"type": "tv", "room": "TV Room"}}
        self.assertEqual(scenes.validate(full), [])

    def test_problems_are_reported(self):
        self.assertTrue(scenes.validate({}))
        self.assertTrue(scenes.validate({"name": "  "}))
        self.assertTrue(scenes.validate("not a dict"))
        self.assertTrue(scenes.validate({"name": "x", "stream": "maybe"}))
        self.assertTrue(scenes.validate({"name": "x", "stream": "on"}))            # no stream_rooms
        self.assertTrue(scenes.validate({"name": "x", "groups": [[]]}))
        self.assertTrue(scenes.validate({"name": "x", "groups": "Office"}))
        self.assertTrue(scenes.validate({"name": "x", "rooms": {"A": {"colour": 1}}}))
        self.assertTrue(scenes.validate({"name": "x", "source": {"type": "radio"}}))
        self.assertTrue(scenes.validate({"name": "x", "source": {"type": "favorite", "room": "A"}}))  # no title


class Capture(unittest.TestCase):
    def test_captures_grouping_levels_and_sound(self):
        groups = [group("Office", ["Office", "TV Room"], levels=[("Office", 20, False), ("TV Room", 30, True)]),
                  group("Sala", levels=[("Sala", 40, False)])]
        settings = {"Office": RoomSettings(bass=3, treble=1, loudness=True),
                    "TV Room": RoomSettings(0, 0, True, False, True, True, True)}
        scene = scenes.capture("Tudo", groups, settings)
        self.assertEqual(scene["groups"], [["Office", "TV Room"], ["Sala"]])
        self.assertEqual(scene["rooms"]["Office"], {"volume": 20, "mute": False, "bass": 3, "treble": 1, "loudness": True})
        self.assertEqual(scene["rooms"]["TV Room"]["mute"], True)
        self.assertEqual(scene["rooms"]["TV Room"]["dialog_mode"], True)
        self.assertNotIn("night_mode", scene["rooms"]["Office"])    # a Play:1 has none: not recorded
        self.assertEqual(scene["rooms"]["Sala"], {"volume": 40, "mute": False})
        self.assertEqual((scene["stream"], scene["source"]), ("off", None))
        self.assertEqual(scenes.validate(scene), [])

    def test_stream_rooms_mean_the_mac_stream_is_on(self):
        scene = scenes.capture("Trabalho", HOUSE, {}, stream_rooms=["Office"])
        self.assertEqual((scene["stream"], scene["stream_rooms"]), ("on", ["Office"]))
        self.assertEqual(scenes.validate(scene), [])

    def test_a_playing_tv_or_url_radio_becomes_the_source_but_not_our_own_stream(self):
        tv = [group("TV Room", state=PLAYING, uri="x-sonos-htastream:RINCON_1:spdif")]
        self.assertEqual(scenes.capture("c", tv, {})["source"], {"type": "tv", "room": "TV Room"})
        radio = [group("Sala", state=PLAYING, uri="x-rincon-mp3radio://http://ice1.somafm.com/g", channel="Groove")]
        self.assertEqual(scenes.capture("r", radio, {})["source"],
                         {"type": "url", "room": "Sala", "url": "http://ice1.somafm.com/g", "title": "Groove"})
        mac = "x-rincon-mp3radio://http://10.0.0.2:8000/mac.mp3"
        ours = [group("Office", state=PLAYING, uri=mac, channel="Mac")]
        self.assertIsNone(scenes.capture("m", ours, {}, mac_uri=mac)["source"])
        paused = [group("TV Room", state=PAUSED, uri="x-sonos-htastream:x")]
        self.assertIsNone(scenes.capture("p", paused, {})["source"])


class Plan(unittest.TestCase):
    def kinds(self, steps):
        return [s[0] for s in steps]

    def test_stream_off_comes_first(self):
        steps = scenes.plan({"name": "x", "stream": "off"}, HOUSE)
        self.assertEqual(steps, [("stream_off",)])
        self.assertEqual(scenes.plan({"name": "x"}, HOUSE), [])      # keep: nothing to do
        self.assertEqual(scenes.plan({"name": "x", "stream": "keep", "groups": None}, HOUSE), [])

    def test_grouping_already_as_wanted_is_left_alone(self):
        same = {"name": "x", "groups": [["Office"], ["TV Room"], ["Sala"]]}
        self.assertEqual(scenes.plan(same, HOUSE), [])
        implicit = {"name": "x", "groups": [["Office"]]}   # the rest stay on their own: already so
        self.assertEqual(scenes.plan(implicit, HOUSE), [])

    def test_regrouping_splits_first_then_joins_then_waits(self):
        scene = {"name": "x", "groups": [["Office", "TV Room"]]}
        steps = scenes.plan(scene, HOUSE)
        self.assertEqual(steps[:2], [("ungroup_all",), ("join", "TV Room", "Office")])
        kind, wanted = steps[-1]
        self.assertEqual(kind, "wait_groups")
        self.assertEqual(wanted, {frozenset(["Office", "TV Room"]), frozenset(["Sala"])})   # Sala left on its own

    def test_asking_to_split_a_group(self):
        grouped = [group("Office", ["Office", "TV Room"]), group("Sala")]
        steps = scenes.plan({"name": "x", "groups": [["Office"], ["TV Room"]]}, grouped)
        self.assertEqual(self.kinds(steps), ["ungroup_all", "wait_groups"])

    def test_room_settings_in_a_fixed_order_and_only_those_given(self):
        scene = {"name": "x", "rooms": {"TV Room": {"dialog_mode": True, "volume": 25, "bass": 2},
                                        "Office": {"mute": False}}}
        self.assertEqual(scenes.plan(scene, HOUSE), [
            ("room", "TV Room", "volume", 25), ("room", "TV Room", "bass", 2), ("room", "TV Room", "dialog_mode", True),
            ("room", "Office", "mute", False)])

    def test_stream_on_starts_before_the_volumes_and_uses_the_leaders_volume(self):
        scene = {"name": "x", "stream": "on", "stream_rooms": ["Office", "Sala"],
                 "rooms": {"Office": {"volume": 15}, "Sala": {"volume": 10}},
                 "source": {"type": "tv", "room": "TV Room"}}
        steps = scenes.plan(scene, HOUSE)
        self.assertEqual(steps[0], ("stream_on", ["Office", "Sala"], 15))
        self.assertEqual(steps[1], ("wait_stream",))
        self.assertEqual(steps[2:], [("room", "Office", "volume", 15), ("room", "Sala", "volume", 10)])  # no source

    def test_stream_on_without_a_volume(self):
        steps = scenes.plan({"name": "x", "stream": "on", "stream_rooms": ["Office"]}, HOUSE)
        self.assertEqual(steps[0], ("stream_on", ["Office"], None))

    def test_sources(self):
        tv = scenes.plan({"name": "x", "source": {"type": "tv", "room": "TV Room"}}, HOUSE)
        fav = scenes.plan({"name": "x", "source": {"type": "favorite", "room": "Sala", "title": "LoungeFM"}}, HOUSE)
        url = scenes.plan({"name": "x", "source": {"type": "url", "room": "Sala", "url": "http://a.b/c"}}, HOUSE)
        self.assertEqual((tv, fav, url), ([("tv", "TV Room")], [("favorite", "Sala", "LoungeFM")],
                                          [("url", "Sala", "http://a.b/c", "")]))

    def test_full_order(self):
        scene = {"name": "x", "stream": "off", "groups": [["TV Room", "Sala"]], "rooms": {"TV Room": {"volume": 20}},
                 "source": {"type": "tv", "room": "TV Room"}}
        self.assertEqual(self.kinds(scenes.plan(scene, HOUSE)),
                         ["stream_off", "ungroup_all", "join", "wait_groups", "room", "tv"])


class FakeActions:
    def __init__(self):
        self.log, self.groups_now, self.is_streaming = [], list(HOUSE), False
        self.fail_on = None

    def _rec(self, *step):
        if self.fail_on == step[0]:
            raise OSError("down")
        self.log.append(step)

    def room(self, room, action, arg): self._rec("room", room, action, arg)
    def groups(self): return self.groups_now
    def streaming(self): return self.is_streaming
    def stream_on(self, rooms, volume): self._rec("stream_on", tuple(rooms), volume)
    def stream_off(self): self._rec("stream_off")
    def favorite(self, room, title): self._rec("favorite", room, title)
    def url(self, room, url, title): self._rec("url", room, url, title)
    def tv(self, room): self._rec("tv", room)


class FakeClock:
    def __init__(self): self.now = 1000.0
    def time(self): return self.now
    def sleep(self, s): self.now += s


class RunnerTests(unittest.TestCase):
    def runner(self, actions, wait=12.0):
        self.clock = FakeClock()
        return scenes.Runner(actions, wait_seconds=wait, poll=0.5, sleep=self.clock.sleep, clock=self.clock.time)

    def test_steps_run_in_order(self):
        a = FakeActions()
        scene = {"name": "x", "stream": "off", "rooms": {"Office": {"volume": 12}}, "source": {"type": "tv", "room": "TV Room"}}
        self.assertTrue(self.runner(a).run(scene, HOUSE))
        self.assertEqual(a.log, [("stream_off",), ("room", "Office", "volume", 12), ("tv", "TV Room")])

    def test_it_waits_for_the_grouping_before_going_on(self):
        a = FakeActions()
        scene = {"name": "x", "groups": [["Office", "TV Room"]], "rooms": {"Office": {"volume": 12}}}
        wanted = {frozenset(["Office", "TV Room"]), frozenset(["Sala"])}
        r = self.runner(a)
        original = r.a.groups
        calls = {"n": 0}

        def groups():   # the house takes a few polls to show the new grouping
            calls["n"] += 1
            return [group("Office", ["Office", "TV Room"]), group("Sala")] if calls["n"] > 3 else original()
        a.groups = groups
        self.assertTrue(r.run(scene, HOUSE))
        self.assertGreaterEqual(calls["n"], 4)
        self.assertEqual(a.log[-1], ("room", "Office", "volume", 12))     # only after the grouping showed up
        self.assertLess(self.clock.now, 1000.0 + 12.0)

    def test_it_waits_for_the_stream_then_sets_volumes(self):
        a = FakeActions()
        scene = {"name": "x", "stream": "on", "stream_rooms": ["Office"], "rooms": {"Office": {"volume": 12}}}
        r = self.runner(a)
        polls = {"n": 0}

        def streaming():
            polls["n"] += 1
            return polls["n"] > 2
        a.streaming = streaming
        self.assertTrue(r.run(scene, HOUSE))
        self.assertEqual(a.log, [("stream_on", ("Office",), 12), ("room", "Office", "volume", 12)])
        self.assertGreater(polls["n"], 2)

    def test_gives_up_waiting_but_still_applies_the_rest(self):
        a = FakeActions()
        scene = {"name": "x", "stream": "on", "stream_rooms": ["Office"], "rooms": {"Office": {"volume": 12}}}
        self.assertTrue(self.runner(a, wait=3.0).run(scene, HOUSE))    # never streams; waits 3 s then goes on
        self.assertEqual(a.log[-1], ("room", "Office", "volume", 12))
        self.assertGreaterEqual(self.clock.now, 1003.0)

    def test_a_failing_step_does_not_stop_the_others(self):
        a = FakeActions()
        a.fail_on = "tv"
        scene = {"name": "x", "rooms": {"Office": {"volume": 12}}, "source": {"type": "tv", "room": "TV Room"}}
        r = self.runner(a)
        with self.assertLogs("mac_sonos", level="WARNING"):
            self.assertFalse(r.run(scene, HOUSE))
        self.assertIsInstance(r.last_error, OSError)
        self.assertEqual(a.log, [("room", "Office", "volume", 12)])
        self.assertTrue(r.run({"name": "y"}, HOUSE))      # and the next scene starts clean
        self.assertIsNone(r.last_error)

    def test_only_one_scene_at_a_time(self):
        gate, started = threading.Event(), threading.Event()
        a = FakeActions()

        def slow(room, action, arg):
            started.set()
            gate.wait(5)
        a.room = slow
        r = self.runner(a)
        t = r.apply_async({"name": "slow", "rooms": {"Office": {"volume": 1}}}, HOUSE)
        self.assertTrue(started.wait(5))
        with self.assertLogs("mac_sonos", level="WARNING"):
            self.assertFalse(r.run({"name": "second", "stream": "off"}, HOUSE))
        self.assertEqual(a.log, [])        # the second scene did nothing
        gate.set()
        t.join(5)
        self.assertTrue(r.run({"name": "third", "stream": "off"}, HOUSE))


class Decode(unittest.TestCase):
    @staticmethod
    def data1(code, state, repeat=0):
        return (code << 16) | (state << 8) | repeat

    def test_known_keys_pressed_and_released(self):
        self.assertEqual(mediakeys.decode(self.data1(16, 0xA)), ("play", True, False))
        self.assertEqual(mediakeys.decode(self.data1(16, 0xB)), ("play", False, False))
        self.assertEqual(mediakeys.decode(self.data1(17, 0xA)), ("next", True, False))
        self.assertEqual(mediakeys.decode(self.data1(18, 0xA)), ("previous", True, False))
        self.assertEqual(mediakeys.decode(self.data1(0, 0xA)), ("volume_up", True, False))
        self.assertEqual(mediakeys.decode(self.data1(1, 0xA)), ("volume_down", True, False))
        self.assertEqual(mediakeys.decode(self.data1(7, 0xA)), ("mute", True, False))
        self.assertEqual(mediakeys.decode(self.data1(19, 0xA))[0], "next")        # fast-forward
        self.assertEqual(mediakeys.decode(self.data1(20, 0xA))[0], "previous")    # rewind

    def test_repeat_and_unknown_keys(self):
        self.assertEqual(mediakeys.decode(self.data1(0, 0xA, 1)), ("volume_up", True, True))
        self.assertEqual(mediakeys.decode(self.data1(3, 0xA))[0], None)    # brightness, keyboard backlight...
        self.assertEqual(mediakeys.decode(0)[0], "volume_up")


MAC = "x-rincon-mp3radio://http://10.0.0.2:8000/mac.mp3"
SPOTIFY = ("play", "pause", "next", "previous", "seektime")


class Decide(unittest.TestCase):
    def test_volume_keys_only_while_the_mac_streams(self):
        stream = group("Office", state=PLAYING, uri=MAC)
        self.assertEqual(model.decide_key("volume_up", True, stream, None), ("volume_by", "Office", 5))
        self.assertEqual(model.decide_key("volume_down", True, stream, None), ("volume_by", "Office", -5))
        self.assertEqual(model.decide_key("mute", True, stream, None), ("mute_group", "Office"))
        self.assertIsNone(model.decide_key("volume_up", False, stream, None))     # not streaming: the Mac's own volume
        self.assertIsNone(model.decide_key("volume_up", True, None, None))

    def test_transport_keys_drive_the_target_group_when_something_plays(self):
        playing = group("TV Room", state=PLAYING, uri="x-sonos-spotify:1", actions=SPOTIFY)
        paused = group("TV Room", state=PAUSED, uri="x-sonos-spotify:1", actions=SPOTIFY)
        self.assertEqual(model.decide_key("play", False, None, playing), ("transport", "TV Room", "pause"))
        self.assertEqual(model.decide_key("play", False, None, paused), ("transport", "TV Room", "play"))
        self.assertEqual(model.decide_key("next", False, None, playing), ("transport", "TV Room", "next"))
        self.assertEqual(model.decide_key("previous", False, None, playing), ("transport", "TV Room", "previous"))

    def test_transport_keys_go_to_the_system_when_nothing_real_is_playing(self):
        stopped = group("TV Room", state=STOPPED, actions=SPOTIFY)
        mac = group("Office", state=PLAYING, uri=MAC, actions=("play", "stop"))
        for target in (None, stopped, mac):
            for key in ("play", "next", "previous"):
                self.assertIsNone(model.decide_key(key, True, mac, target), (key, target))

    def test_a_key_the_group_cannot_do_is_swallowed_not_leaked(self):
        radio = group("Sala", state=PLAYING, uri="x-rincon-mp3radio://h/s", actions=("play", "stop"))
        self.assertEqual(model.decide_key("next", False, None, radio), ("noop",))
        self.assertEqual(model.decide_key("previous", False, None, radio), ("noop",))
        self.assertEqual(model.decide_key("play", False, None, radio), ("noop",))   # a radio cannot pause


class FakeQuartz:
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault = 1, 2, 3
    kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    kCFRunLoopCommonModes = "common"

    def __init__(self, create_result="tap"):
        self.create_result, self.enabled, self.sources = create_result, [], []

    def CGEventMaskBit(self, t): return 1 << t
    def CGEventTapCreate(self, *args): return self.create_result
    def CFMachPortCreateRunLoopSource(self, *a): return "source"
    def CFRunLoopGetMain(self): return "main"
    def CFRunLoopAddSource(self, loop, src, mode): self.sources.append(("add", src))
    def CFRunLoopRemoveSource(self, loop, src, mode): self.sources.append(("remove", src))
    def CGEventTapEnable(self, tap, on): self.enabled.append(on)


class TapTests(unittest.TestCase):
    def setUp(self):
        self.q = FakeQuartz()
        self.patches = [mock.patch.dict("sys.modules", {"Quartz": self.q})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.handled = []
        self.swallow = True
        self.keys = mediakeys.MediaKeys(lambda key, repeat: self.handled.append((key, repeat)) or self.swallow)

    def event(self, code, state, repeat=0, subtype=8):
        ns = SimpleNamespace(subtype=lambda: subtype, data1=lambda: (code << 16) | (state << 8) | repeat)
        return "cgevent", ns

    def fire(self, code, state, repeat=0, subtype=8, event_type=14):
        cg, ns = self.event(code, state, repeat, subtype)
        nsevent = SimpleNamespace(eventWithCGEvent_=lambda e: ns)
        with mock.patch.dict("sys.modules", {"AppKit": SimpleNamespace(NSEvent=nsevent)}):
            return self.keys._callback(None, event_type, cg, None), cg

    def test_start_needs_the_permission(self):
        with mock.patch.object(mediakeys, "is_trusted", return_value=False):
            self.assertFalse(self.keys.start())
        self.assertFalse(self.keys.active)

    def test_start_and_stop_when_trusted(self):
        with mock.patch.object(mediakeys, "is_trusted", return_value=True):
            self.assertTrue(self.keys.start())
            self.assertTrue(self.keys.active)
            self.assertTrue(self.keys.start())           # idempotent
        self.assertEqual(self.q.sources, [("add", "source")])
        self.keys.stop()
        self.assertFalse(self.keys.active)
        self.assertEqual((self.q.sources[-1], self.q.enabled), (("remove", "source"), [True, False]))
        self.keys.stop()                                  # harmless twice

    def test_macos_refusing_the_tap_is_not_active(self):
        self.q.create_result = None
        with mock.patch.object(mediakeys, "is_trusted", return_value=True):
            self.assertFalse(self.keys.start())
        self.assertFalse(self.keys.active)

    def test_a_swallowed_key_is_swallowed_on_release_too(self):
        out, _ = self.fire(0, 0xA)          # volume up pressed
        self.assertIsNone(out)
        out, _ = self.fire(0, 0xB)          # released
        self.assertIsNone(out)
        self.assertEqual(self.handled, [("volume_up", False)])      # the handler only hears presses
        self.swallow = False
        out, cg = self.fire(0, 0xA)         # now we let it through, and its release as well
        self.assertEqual(out, cg)
        out, cg = self.fire(0, 0xB)
        self.assertEqual(out, cg)

    def test_repeat_is_passed_to_the_handler(self):
        self.fire(1, 0xA, repeat=1)
        self.assertEqual(self.handled, [("volume_down", True)])

    def test_everything_else_goes_through(self):
        for kwargs in (dict(code=3, state=0xA),                  # a key we do not know
                       dict(code=16, state=0xA, subtype=7),      # not a media event
                       dict(code=16, state=0xA, event_type=10)): # not a system-defined event at all
            out, cg = self.fire(**kwargs)
            self.assertEqual(out, cg, kwargs)
        self.assertEqual(self.handled, [])

    def test_a_crashing_handler_never_breaks_the_keyboard(self):
        self.keys.handler = mock.Mock(side_effect=RuntimeError("bug"))
        with self.assertLogs("mac_sonos", level="ERROR"):
            out, cg = self.fire(16, 0xA)
        self.assertEqual(out, cg)

    def test_a_tap_switched_off_by_macos_is_switched_back_on(self):
        self.keys._tap = "tap"
        out, cg = self.fire(0, 0, event_type=FakeQuartz.kCGEventTapDisabledByTimeout)
        self.assertEqual(out, cg)
        self.assertEqual(self.q.enabled, [True])


class RealMacOS(unittest.TestCase):
    def test_without_the_permission_no_tap_is_created_and_nothing_raises(self):
        """The real Quartz: this process is normally not trusted, in which case start() must say False."""
        keys = mediakeys.MediaKeys(lambda k, r: False)
        started = keys.start()
        self.addCleanup(keys.stop)
        self.assertEqual(started, mediakeys.is_trusted())


if __name__ == "__main__":
    unittest.main()
