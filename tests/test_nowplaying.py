"""Now-playing tests that need no hardware: python -m unittest discover tests"""
import queue
import unittest
from unittest import mock

from mac_sonos import i18n
from mac_sonos.sonos import monitor as mon
from mac_sonos.sonos.monitor import GroupInfo, Monitor, PAUSED, PLAYING, RoomSettings, STOPPED
from mac_sonos.ui import model


i18n.set_language("pt")  # the expected texts below are the Portuguese ones


class Parsing(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(mon.parse_hms("0:02:35"), 155)
        self.assertEqual(mon.parse_hms("1:00:01"), 3601)
        for bad in ("", "NOT_IMPLEMENTED", None):
            self.assertEqual(mon.parse_hms(bad), 0)
        self.assertEqual(mon.format_hms(155), "0:02:35")
        self.assertEqual(mon.format_hms(-5), "0:00:00")

    def test_actions(self):
        self.assertEqual(mon.parse_actions("Set, Stop, Play"), {"set", "stop", "play"})
        self.assertEqual(mon.parse_actions(["Next", " Pause "]), {"next", "pause"})
        self.assertEqual(mon.parse_actions(""), frozenset())


def info(**kw):
    base = dict(coordinator="Office", members=("Office",), state=PLAYING, title="Song", artist="Band",
                duration=200, actions=frozenset({"play", "pause", "next", "previous", "seektime"}),
                position=10, read_at=1000.0)
    return GroupInfo(**{**base, **kw})


class GroupInfoTests(unittest.TestCase):
    def test_position_and_read_time_do_not_count_as_change(self):
        self.assertEqual(info(position=10, read_at=1.0), info(position=99, read_at=2.0))
        self.assertNotEqual(info(), info(title="Other"))

    def test_current_position_advances_only_while_playing_and_is_clamped(self):
        self.assertEqual(info().current_position(now=1005.0), 15)
        self.assertEqual(info(state=PAUSED).current_position(now=1005.0), 10)
        self.assertEqual(info(duration=12).current_position(now=1100.0), 12)


class ModelTests(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(model.track_text(info()), "Song – Band")
        self.assertEqual(model.track_text(info(artist="")), "Song")
        self.assertEqual(model.track_text(info(title="", artist="", channel="Radio X")), "Radio X")
        self.assertEqual(model.track_text(info(title="", artist="")), "—")
        self.assertEqual(model.group_name(info(members=("Office", "TV Room"))), "Office + TV Room")
        self.assertEqual(model.format_time(65), "1:05")
        self.assertEqual(model.format_time(3725), "1:02:05")
        self.assertEqual(model.shorten("abcdef", 4), "abc…")
        self.assertEqual(model.shorten("abc", 4), "abc")

    def test_controls_follow_actions_and_state(self):
        keys = lambda i: [k for k, _ in model.controls(i)]
        self.assertEqual(keys(info()), ["previous", "pause", "next", "back", "forward"])
        self.assertEqual(keys(info(state=PAUSED)), ["previous", "play", "next", "back", "forward"])
        # the Mac stream only reports Set/Stop/Play: while playing there is nothing to offer
        self.assertEqual(keys(info(actions=frozenset({"set", "stop", "play"}), duration=0)), [])
        self.assertEqual(keys(info(duration=0)), ["previous", "pause", "next"])  # stream: no seek

    def test_mac_stream_detection(self):
        self.assertTrue(model.is_mac_stream(info(uri="x-rincon-mp3radio://http://1.2.3.4:8000/mac.mp3")))
        self.assertFalse(model.is_mac_stream(info(uri="x-sonos-spotify:abc")))

    def test_primary_group_prefers_ours_then_playing(self):
        mine = info(coordinator="Office", members=("Office",), state=PAUSED)
        other = info(coordinator="TV Room", members=("TV Room",))
        stopped = info(coordinator="Sala", members=("Sala",), state=STOPPED)
        self.assertIs(model.primary_group([other, mine, stopped], ["Office"]), mine)
        self.assertIs(model.primary_group([stopped, other], ["Office"]), other)
        self.assertIsNone(model.primary_group([stopped], ["Office"]))

    def test_primary_group_skips_our_own_stream(self):
        mac = info(coordinator="Office", members=("Office",), uri="x://h:8000/mac.mp3", title="", artist="")
        other = info(coordinator="TV Room", members=("TV Room",))
        self.assertIs(model.primary_group([mac, other], ["Office"]), other)
        self.assertIsNone(model.primary_group([mac], ["Office"]))

    def test_bar_title(self):
        self.assertEqual(model.bar_title("🔊", None), "🔊")
        self.assertEqual(model.bar_title("🔊", info()), "🔊 Song – Band")
        mac = info(uri="x://h:8000/mac.mp3", title="", artist="")
        self.assertEqual(model.bar_title("🔊", mac), "🔊")

    def test_structure_key_ignores_position_and_idle_groups(self):
        a = [info(position=1), info(coordinator="Sala", members=("Sala",), state=STOPPED)]
        b = [info(position=50, read_at=5.0), info(coordinator="Sala", members=("Sala",), state=STOPPED, title="x")]
        self.assertEqual(model.structure_key(a), model.structure_key(b))
        self.assertNotEqual(model.structure_key(a), model.structure_key([info(state=PAUSED)]))


class FakeZone:
    def __init__(self, name, ip, groups_ref):
        self.player_name, self.ip_address = name, ip
        self.state, self.title, self.position, self.duration = PLAYING, "Song", "0:00:10", "0:03:20"
        self.available_actions = ["Play", "Pause", "Next", "SeekTime"]
        self.volume, self.mute, self.is_visible = 20, False, True
        self.bass, self.treble, self.loudness = 0, 0, True
        self.calls, self.groups_ref = [], groups_ref
        self.avTransport = mock.Mock()
        self.avTransport.subscribe.return_value = mock.Mock(events=queue.Queue())
        self.zoneGroupTopology = mock.Mock()
        self.zoneGroupTopology.subscribe.return_value = mock.Mock(events=queue.Queue())
        self.renderingControl = mock.Mock()
        self.renderingControl.subscribe.return_value = mock.Mock(events=queue.Queue())

    def get_current_transport_info(self):
        return {"current_transport_state": self.state}

    def get_current_track_info(self):
        return {"title": self.title, "artist": "Band", "album": "Alb", "album_art": "", "uri": "x:y",
                "position": self.position, "duration": self.duration}

    def get_current_media_info(self):
        return {"channel": ""}

    @property
    def all_groups(self):
        return self.groups_ref()

    def __getattr__(self, name):
        if name in ("play", "pause", "next", "previous", "stop", "seek", "join", "unjoin", "partymode", "switch_to_tv"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)


class FakeGroup:
    def __init__(self, coordinator, members):
        self.coordinator, self.members = coordinator, members


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.office = FakeZone("Office", "10.0.0.1", lambda: self.groups)
        self.tv = FakeZone("TV Room", "10.0.0.2", lambda: self.groups)
        self.groups = [FakeGroup(self.office, [self.office, self.office]), FakeGroup(self.tv, [self.tv])]
        self.seen = []
        patch = mock.patch.object(mon.discovery, "discover", lambda: [self.office, self.tv])
        patch.start()
        self.addCleanup(patch.stop)
        self.m = Monitor(on_change=self.seen.append)

    def test_first_step_reads_all_groups_with_unique_members(self):
        self.m._step(100.0)
        self.assertEqual([g.coordinator for g in self.m.groups], ["Office", "TV Room"])
        self.assertEqual(self.m.groups[0].members, ("Office",))  # stereo pair listed twice
        self.assertEqual(len(self.seen), 1)

    def test_no_notification_when_nothing_changed_only_position(self):
        self.m._step(100.0)
        self.office.position = "0:00:13"
        self.m._step(104.0)  # poll due (3 s while playing): position changed, nothing else
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.m.groups[0].position, 13)

    def test_change_is_notified(self):
        self.m._step(100.0)
        self.office.title = "Next song"
        self.m._step(104.0)
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(self.m.groups[0].title, "Next song")

    def test_event_marks_group_dirty_and_refreshes_before_poll_is_due(self):
        self.m._step(100.0)
        self.office.title = "Via event"
        self.m._dirty_ips.add("10.0.0.1")
        self.m._step(100.5)  # far from the 3 s poll
        self.assertEqual(self.m.groups[0].title, "Via event")

    def test_commands_reach_the_coordinator(self):
        self.m._step(100.0)
        for action in ("pause", "next"):
            self.m.command("Office", action)
        self.m.command("Nobody", "pause")  # unknown group is ignored
        self.m._step(100.5)
        self.assertEqual(self.office.calls, [("pause",), ("next",)])

    def test_seek_by_is_relative_and_clamped(self):
        self.m._step(100.0)
        self.m.command("Office", "seek_by", -30)
        self.m._step(100.0)
        self.assertEqual(self.office.calls[-1], ("seek", "0:00:00"))
        self.m.command("Office", "seek_by", 9999)
        self.m._step(100.0)
        self.assertEqual(self.office.calls[-1], ("seek", "0:03:19"))

    def test_a_failing_zone_does_not_break_the_others(self):
        self.m._step(100.0)
        self.office.get_current_transport_info = mock.Mock(side_effect=OSError("down"))
        self.tv.title = "Still fine"
        self.m._step(104.0)
        self.assertEqual([g.title for g in self.m.groups], ["Song", "Still fine"])

    def test_regroup_is_picked_up_at_next_topology_refresh(self):
        self.m._step(100.0)
        self.groups = [FakeGroup(self.office, [self.office, self.tv])]
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 1)
        self.assertEqual([(g.coordinator, g.members) for g in self.m.groups], [("Office", ("Office", "TV Room"))])


class HouseTests(MonitorTests):
    """Volume, mute and grouping through the monitor (same fakes)."""

    def setUp(self):
        super().setUp()
        self.m._step(100.0)

    def run_cmds(self):
        self.m._step(100.5)

    def test_levels_are_read_per_room(self):
        self.tv.volume, self.tv.mute = 35, True
        self.m._groups["TV Room"].dirty = True
        self.m._step(100.5)
        tv = [g for g in self.m.groups if g.coordinator == "TV Room"][0]
        self.assertEqual(tv.levels, (("TV Room", 35, True),))

    def test_volume_and_mute_commands(self):
        self.m.room_command("Office", "volume", 40)
        self.m.room_command("Office", "volume_by", 5)
        self.m.room_command("TV Room", "mute")
        self.run_cmds()
        self.assertEqual(self.office.volume, 45)
        self.assertTrue(self.tv.mute)
        self.m.room_command("TV Room", "mute")
        self.m.room_command("TV Room", "mute", True)
        self.run_cmds()
        self.assertTrue(self.tv.mute)

    def test_volume_is_clamped(self):
        self.m.room_command("Office", "volume_by", 500)
        self.m.room_command("TV Room", "volume_by", -500)
        self.run_cmds()
        self.assertEqual((self.office.volume, self.tv.volume), (100, 0))

    def test_group_volume_moves_every_room(self):
        self.groups = [FakeGroup(self.office, [self.office, self.tv])]
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 1)
        self.m.command("Office", "volume_by", 5)
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 2)
        self.assertEqual((self.office.volume, self.tv.volume), (25, 25))

    def test_join_unjoin_party_and_ungroup_all(self):
        self.m.room_command("TV Room", "join", "Office")
        self.m.room_command("TV Room", "unjoin")
        self.m.command("Office", "partymode")
        self.run_cmds()
        self.assertEqual(self.tv.calls, [("join", self.office), ("unjoin",)])
        self.assertEqual(self.office.calls, [("partymode",)])
        self.groups = [FakeGroup(self.office, [self.office, self.tv])]
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 1)
        self.m.room_command(None, "ungroup_all")
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 2)
        self.assertEqual(self.tv.calls[-1], ("unjoin",))

    def test_join_after_ungroup_all_targets_a_room_that_was_only_a_member(self):
        # what a scene sends: split everything, then build its groups. Office leads the new group but, until the
        # topology is read again, it is still a member of the Living Room group
        living = FakeZone("Living Room", "10.0.0.3", lambda: self.groups)
        self.groups = [FakeGroup(living, [living, self.office]), FakeGroup(self.tv, [self.tv])]
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 1)
        self.m.room_command(None, "ungroup_all")
        self.m.room_command("TV Room", "join", "Office")
        self.m._step(100.0 + mon.TOPOLOGY_EVERY + 2)
        self.assertEqual(self.office.calls, [("unjoin",)])
        self.assertEqual(self.tv.calls, [("join", self.office)])

    def test_membership_commands_force_a_topology_refresh(self):
        self.m._next_topology = 999.0  # far in the future: only the command can trigger a refresh
        self.m.room_command("TV Room", "join", "Office")
        self.groups = [FakeGroup(self.office, [self.office, self.tv])]
        self.run_cmds()
        self.assertEqual([g.members for g in self.m.groups], [("Office", "TV Room")])

    def test_topology_event_forces_a_refresh(self):
        self.m._next_topology = 999.0
        self.groups = [FakeGroup(self.office, [self.office, self.tv])]
        self.m._subs[("10.0.0.1", "zoneGroupTopology")].events.put(object())
        self.m._step(101.0)
        self.assertEqual([g.members for g in self.m.groups], [("Office", "TV Room")])

    def test_stereo_pair_commands_go_to_the_visible_player(self):
        hidden = FakeZone("Office", "10.0.0.9", lambda: self.groups)  # same name, hidden secondary
        hidden.is_visible = False
        self.groups = [FakeGroup(self.office, [hidden, self.office]), FakeGroup(self.tv, [self.tv])]
        self.m._next_topology = 0.0
        self.m._step(101.0)
        self.assertIs(self.m._find_zone("Office"), self.office)
        self.m.room_command("Office", "join", "TV Room")
        self.m._step(102.0)
        self.assertEqual(self.office.calls[-1][0], "join")
        self.assertEqual(hidden.calls, [])

    def test_bad_targets_do_not_break_the_queue(self):
        self.m.room_command("Nowhere", "mute")
        self.m.room_command("TV Room", "join", "Nobody")
        self.m.room_command("Office", "volume", 33)
        self.run_cmds()
        self.assertEqual(self.office.volume, 33)


class SoundTests(MonitorTests):
    def setUp(self):
        super().setUp()
        self.tv.is_soundbar, self.tv.night_mode, self.tv.dialog_mode, self.tv.surround_enabled = True, False, False, True
        self.m._step(100.0)

    def step(self, now=100.5):
        self.m._step(now)

    def test_settings_are_read_and_capabilities_differ_by_room(self):
        st = self.m.settings
        self.assertEqual(st["Office"], RoomSettings(bass=0, treble=0, loudness=True))
        self.assertEqual(st["TV Room"], RoomSettings(0, 0, True, False, False, True, True))
        self.assertIsNone(st["Office"].night_mode)  # a Play:1 has no night mode

    def test_eq_commands_are_relative_and_clamped(self):
        self.m.room_command("Office", "bass_by", 1)
        self.m.room_command("Office", "treble_by", -1)
        for _ in range(15):
            self.m.room_command("TV Room", "bass_by", 1)
        self.step()
        self.assertEqual((self.office.bass, self.office.treble, self.tv.bass), (1, -1, 10))
        self.assertEqual(self.m.settings["Office"].bass, 1)  # re-read right after the command

    def test_toggles(self):
        for action in ("loudness", "night_mode", "dialog_mode", "surround"):
            self.m.room_command("TV Room", action)
        self.m.room_command("TV Room", "night_mode", True)
        self.step()
        self.assertEqual((self.tv.loudness, self.tv.night_mode, self.tv.dialog_mode, self.tv.surround_enabled),
                         (False, True, True, False))

    def test_tv_input(self):
        self.m.room_command("TV Room", "tv")
        self.step()
        self.assertEqual(self.tv.calls, [("switch_to_tv",)])

    def test_rendering_event_refreshes_settings_before_the_minute_is_up(self):
        self.office.bass = 6  # changed outside, e.g. in the Sonos app
        self.m._subs[("10.0.0.1", "renderingControl")].events.put(object())
        self.step()
        self.assertEqual(self.m.settings["Office"].bass, 6)

    def test_settings_are_polled_every_minute_as_a_fallback(self):
        self.office.bass = 4
        self.step(100.0 + mon.SETTINGS_EVERY + 1)
        self.assertEqual(self.m.settings["Office"].bass, 4)

    def test_settings_change_is_notified(self):
        before = len(self.seen)
        self.m.room_command("Office", "bass_by", 2)
        self.step()
        self.assertGreater(len(self.seen), before)


class SoundModel(unittest.TestCase):
    def test_eq_text(self):
        self.assertEqual(model.eq_text(0), "0")
        self.assertEqual(model.eq_text(3), "+3")
        self.assertEqual(model.eq_text(-2), "−2")
        self.assertEqual(model.eq_text(None), "?")

    def test_sound_items_only_list_what_the_room_has(self):
        labels = lambda st: [i[0] for i in model.sound_items(st)]
        play1 = RoomSettings(bass=2, treble=0, loudness=True)
        self.assertEqual(labels(play1), ["Graves: +2", "Graves +1", "Graves −1", "Agudos: 0", "Agudos +1", "Agudos −1", "Loudness"])
        bar = RoomSettings(0, 0, True, False, True, True, True)
        self.assertEqual(labels(bar)[-4:], ["Modo noite", "Realce de diálogo", "Surround", "Áudio da TV"])
        self.assertEqual(model.sound_items(None), [])
        self.assertEqual(labels(RoomSettings()), [])

    def test_sound_items_carry_action_arg_and_state(self):
        by = {i[0]: i for i in model.sound_items(RoomSettings(0, 0, False, True, False, None, True))}
        self.assertEqual(by["Graves −1"][1:], ("bass_by", -1, None))
        self.assertEqual(by["Loudness"][1:], ("loudness", None, False))
        self.assertEqual(by["Modo noite"][3], True)
        self.assertEqual(by["Áudio da TV"][1], "tv")
        self.assertNotIn("Surround", by)  # None: not supported
        self.assertIsNone(by["Graves: 0"][1])  # info line

    def test_house_key_reacts_to_settings(self):
        g = [info()]
        a, b = {"Office": RoomSettings(bass=0)}, {"Office": RoomSettings(bass=1)}
        self.assertNotEqual(model.house_key(g, a), model.house_key(g, b))
        self.assertEqual(model.house_key(g, a), model.house_key(g, dict(a)))

    def test_tv_and_line_in_labels(self):
        self.assertEqual(model.track_text(info(title="", artist="", uri="x-sonos-htastream:RINCON_1:spdif")), "TV")
        self.assertEqual(model.track_text(info(title="", artist="", uri="x-rincon-stream:RINCON_1")), "Line-in")
        self.assertEqual(model.track_text(info(title="", artist="", channel="Mac", uri="x-sonos-htastream:x")), "Mac")


class HouseModel(unittest.TestCase):
    def test_level_text(self):
        self.assertEqual(model.level_text(20, False), "20%")
        self.assertEqual(model.level_text(20, True), "🔇 mudo")
        self.assertEqual(model.level_text(None, None), "?")

    def test_house_rooms_and_key(self):
        g = info(members=("Office", "TV Room"), levels=(("Office", 20, False), ("TV Room", 30, True)))
        rooms = model.house_rooms([g])
        self.assertEqual([(r, v, m) for r, _, v, m in rooms], [("Office", 20, False), ("TV Room", 30, True)])
        louder = info(members=("Office", "TV Room"), levels=(("Office", 25, False), ("TV Room", 30, True)))
        muted = info(members=("Office", "TV Room"), levels=(("Office", 20, True), ("TV Room", 30, True)))
        self.assertEqual(model.house_key([g]), model.house_key([louder]))  # volume alone: no rebuild
        self.assertNotEqual(model.house_key([g]), model.house_key([muted]))

    def test_join_targets_exclude_own_group(self):
        a, b = info(coordinator="A", members=("A",)), info(coordinator="B", members=("B",))
        self.assertEqual(model.join_targets([a, b], a), ["B"])

    def test_party_target(self):
        a = info(coordinator="A", members=("A",), state=STOPPED)
        b = info(coordinator="B", members=("B",))
        self.assertIs(model.party_target([a, b], ["A"]), b)
        self.assertIs(model.party_target([a], ["Z"]), a)
        self.assertIsNone(model.party_target([], ["Z"]))

    def test_interrupted_detection(self):
        from mac_sonos.controller import State
        mac_uri = "x-rincon-mp3radio://http://h:8000/mac.mp3"
        st = State(status="streaming", rooms=["Office"], started_at=1000.0)
        ok = info(members=("Office",), uri=mac_uri, read_at=1100.0)
        self.assertFalse(model.is_interrupted(st, [ok], 1100.0))
        self.assertTrue(model.is_interrupted(st, [info(members=("Office",), uri=mac_uri, state=STOPPED, read_at=1100.0)], 1100.0))
        self.assertTrue(model.is_interrupted(st, [info(members=("Office",), uri="spotify:x", read_at=1100.0)], 1100.0))
        # grace period, stale reading, unknown group, not streaming
        self.assertFalse(model.is_interrupted(st, [info(members=("Office",), uri="x", state=STOPPED, read_at=1100.0)], 1005.0))
        self.assertFalse(model.is_interrupted(st, [info(members=("Office",), uri="x", state=STOPPED, read_at=999.0)], 1100.0))
        self.assertFalse(model.is_interrupted(st, [info(coordinator="Z", members=("Z",))], 1100.0))
        self.assertFalse(model.is_interrupted(State(status="stopped", rooms=["Office"]), [ok], 1100.0))


if __name__ == "__main__":
    unittest.main()
