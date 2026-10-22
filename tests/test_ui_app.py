"""The menu bar app itself, instantiated headless (needs macOS + rumps): python -m unittest discover tests"""
import datetime
import json
import tempfile
import time
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock

try:
    import rumps  # noqa: F401
    from mac_sonos.ui import menubar
    HAVE_UI = True
except Exception:  # pragma: no cover - not on macOS
    HAVE_UI = False

from mac_sonos import config, controller, i18n, scenes
from mac_sonos.sonos.alarms import AlarmInfo
from mac_sonos.sonos.library import Favorite, PLAYLIST, QueueEntry, RADIO, TRACK
from mac_sonos.sonos.monitor import GroupInfo, Monitor, PLAYING, RoomSettings, STOPPED

REAL_LOCALES = i18n.LOCALES


@unittest.skipUnless(HAVE_UI, "needs rumps / AppKit")
class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.locales = tmp / "locales"
        self.locales.mkdir()
        for p in REAL_LOCALES.glob("*.json"):
            (self.locales / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        for patch in (mock.patch.object(config, "PATH", tmp / "config.json"),
                      mock.patch.object(i18n, "LOCALES", self.locales),
                      mock.patch.object(i18n, "_catalogs", {}),
                      mock.patch.object(i18n, "system_languages", return_value=["pt-BR"]),
                      mock.patch.object(Monitor, "start")):
            patch.start()
            self.addCleanup(patch.stop)
        saved = i18n.current_language()
        self.addCleanup(lambda: setattr(i18n, "_language", saved))
        config.save({**config.DEFAULTS, "autostart_stream": False})

    def make_app(self):
        app = menubar.MenuBarApp()
        self.addCleanup(app.ctl.shutdown)
        self.addCleanup(app.library.stop)
        self.addCleanup(app.alarm_manager.stop)
        return app

    def top_titles(self, app):
        return [getattr(i, "title", None) for i in app.menu.values() if hasattr(i, "title")]

    def groups(self, volume=20, muted=False):
        now = time.time()
        return [GroupInfo("Office", ("Office",), PLAYING, levels=(("Office", volume, muted),), read_at=now),
                GroupInfo("TV Room", ("TV Room",), STOPPED, levels=(("TV Room", 22, False),), read_at=now)]

    def test_house_menu_survives_repeated_ticks_with_sound_items(self):
        """Regression: a leaked loop variable once turned the volume label into a str and broke every tick."""
        app = self.make_app()
        app.groups = self.groups()
        app.settings = {"Office": RoomSettings(3, 1, True), "TV Room": RoomSettings(2, 0, True, False, True, True, True)}
        app._tick(None)   # builds the menu
        item, label = app._house_items["Office"]
        self.assertIsInstance(label, rumps.MenuItem)
        app.groups = self.groups(volume=25)
        app._tick(None)   # same structure: updates in place
        app._tick(None)
        self.assertIs(app._house_items["Office"][0], item)
        self.assertEqual(item.title, "Office  ·  25%")
        self.assertEqual(label.title, "Volume: 25%")
        self.assertEqual(app.status_item.title, "Parado")  # the rest of the tick still ran

    # --- library ---------------------------------------------------------------------------------

    def library_app(self):
        app = self.make_app()
        app.library.favorites = [Favorite("LoungeFM", RADIO), Favorite("Brahms", TRACK), Favorite("My list", PLAYLIST),
                                 Favorite("BBC 3", RADIO)]
        app.library.playlists = [mock.Mock(title="Degas")]
        app.library.queues = {"Office": (QueueEntry(0, "Bubbly", "Eliza"), QueueEntry(1, "Poker Face", "Gaga"))}
        app.library.version = 1
        now = time.time()
        app.groups = [GroupInfo("Office", ("Office",), PLAYING, queue_position=2, read_at=now),
                      GroupInfo("TV Room", ("TV Room",), STOPPED, read_at=now)]
        for name in ("play_favorite", "play_playlist", "play_url", "play_queue_index", "remove_from_queue",
                     "clear_queue", "watch"):
            setattr(app.library, name, mock.Mock())
        app._tick(None)
        return app

    def children(self, item):
        return {i.title: i for i in item.values() if getattr(i, "title", None)}

    def test_library_menu_structure(self):
        app = self.library_app()
        top = self.children(app.library_menu)
        self.assertIn("Tocar em: Office", top)
        self.assertEqual(sorted(self.children(top["Favoritos"])), sorted(["Playlists (1)", "Rádios (2)", "Músicas (1)"]))
        self.assertEqual(list(self.children(self.children(top["Favoritos"])["Rádios (2)"])), ["LoungeFM", "BBC 3"])
        self.assertEqual(list(self.children(top["Playlists do Sonos"])), ["Degas"])
        queue = self.children(top["Fila"])
        self.assertEqual(list(queue)[:2], ["1. Bubbly – Eliza", "▶ 2. Poker Face – Gaga"])  # current track marked
        self.assertIn("Limpar fila", queue)
        self.assertEqual(list(self.children(queue["1. Bubbly – Eliza"])), ["Tocar", "Remover"])
        self.assertEqual(list(self.children(top["Rádios por URL"])), ["Tocar URL…", "Adicionar rádio…"])
        app.library.watch.assert_called_with("Office")

    def test_library_items_play_on_the_target_group(self):
        app = self.library_app()
        top = self.children(app.library_menu)
        radios = self.children(self.children(top["Favoritos"])["Rádios (2)"])
        radios["LoungeFM"].callback(None)
        self.assertEqual(app.library.play_favorite.call_args.args[0], "Office")
        self.assertEqual(app.library.play_favorite.call_args.args[1].title, "LoungeFM")
        self.children(top["Playlists do Sonos"])["Degas"].callback(None)
        self.assertEqual(app.library.play_playlist.call_args.args[0], "Office")
        row = self.children(self.children(top["Fila"])["1. Bubbly – Eliza"])
        row["Tocar"].callback(None)
        row["Remover"].callback(None)
        self.children(top["Fila"])["Limpar fila"].callback(None)
        app.library.play_queue_index.assert_called_with("Office", 0)
        app.library.remove_from_queue.assert_called_with("Office", 0)
        app.library.clear_queue.assert_called_with("Office")

    def test_choosing_where_to_play_moves_everything_and_is_saved(self):
        app = self.library_app()
        where = self.children(self.children(app.library_menu)["Tocar em: Office"])
        self.assertEqual(sorted(where), ["Office", "TV Room"])
        where["TV Room"].callback(None)
        app._tick(None)
        self.assertEqual(config.load()["play_target"], "TV Room")
        self.assertIn("Tocar em: TV Room", self.children(app.library_menu))
        app.library.watch.assert_called_with("TV Room")
        radios = self.children(self.children(self.children(app.library_menu)["Favoritos"])["Rádios (2)"])
        radios["BBC 3"].callback(None)
        self.assertEqual(app.library.play_favorite.call_args.args[0], "TV Room")

    def test_nothing_happens_without_any_group(self):
        app = self.make_app()
        app.library.play_url = mock.Mock()
        app._tick(None)
        app._play("play_url", "http://a.b/c")
        app.library.play_url.assert_not_called()
        self.assertIn("Nada por aqui", self.children(self.children(app.library_menu)["Favoritos"]))

    def test_url_dialogs(self):
        app = self.library_app()
        with mock.patch.object(app, "_ask", return_value="http://ice1.somafm.com/g"):
            app.on_play_url(None)
        app.library.play_url.assert_called_once_with("Office", "http://ice1.somafm.com/g")
        app.library.play_url.reset_mock()
        with mock.patch.object(app, "_ask", return_value="not a url"), mock.patch.object(rumps, "alert") as alert:
            app.on_play_url(None)
        alert.assert_called_once()
        app.library.play_url.assert_not_called()
        with mock.patch.object(app, "_ask", return_value=None):
            app.on_play_url(None)
            app.on_add_radio(None)
        app.library.play_url.assert_not_called()
        self.assertEqual(config.load()["radio_urls"], [])

    def test_saved_radios_can_be_added_played_and_removed(self):
        app = self.library_app()
        answers = iter(["http://ice1.somafm.com/g", "Groove Salad"])
        with mock.patch.object(app, "_ask", side_effect=lambda *a, **k: next(answers)):
            app.on_add_radio(None)
        self.assertEqual(config.load()["radio_urls"], [{"title": "Groove Salad", "url": "http://ice1.somafm.com/g"}])
        app._tick(None)
        radios = self.children(self.children(app.library_menu)["Rádios por URL"])
        self.assertIn("Groove Salad", radios)
        radios["Groove Salad"].callback(None)
        app.library.play_url.assert_called_with("Office", "http://ice1.somafm.com/g", "Groove Salad")
        self.children(radios["Remover rádio"])["Groove Salad"].callback(None)
        self.assertEqual(config.load()["radio_urls"], [])

    def test_library_menu_is_not_rebuilt_on_every_tick(self):
        app = self.library_app()
        with mock.patch.object(app, "_rebuild_library", wraps=app._rebuild_library) as rebuild:
            for _ in range(3):
                app._tick(None)
            self.assertEqual(rebuild.call_count, 0)
            app.library.version += 1  # favorites changed
            app._tick(None)
            self.assertEqual(rebuild.call_count, 1)
            app.groups = [GroupInfo(**{**app.groups[0].__dict__, "queue_position": 1}), app.groups[1]]
            app._tick(None)  # another track is current: the queue marker moves
            self.assertEqual(rebuild.call_count, 2)

    # --- sleep timer -----------------------------------------------------------------------------

    MAC_URI = "x-rincon-mp3radio://http://10.0.0.2:8000/mac.mp3"

    def sleep_groups(self, sleeping=False, mac=False, volume=20):
        now = time.time()
        if mac:
            return [GroupInfo("Office", ("Office",), PLAYING, uri=self.MAC_URI, channel="Mac",
                              levels=(("Office", volume, False),), read_at=now)]
        return [GroupInfo("TV Room", ("TV Room",), PLAYING, title="Song", artist="Band", uri="x-sonos-spotify:1",
                          sleeping=sleeping, sleep_remaining=600 if sleeping else None, read_at=now)]

    def sleep_menu(self, app, header_title):
        header = self.children(app.now_menu)[header_title]
        return header, self.children(self.children(header)["Soneca"])

    def test_sleep_on_a_normal_group_uses_the_native_timer(self):
        app = self.make_app()
        app.monitor.command = mock.Mock()
        app.groups = self.sleep_groups()
        app._tick(None)
        _, menu = self.sleep_menu(app, "▶ TV Room")
        self.assertEqual(list(menu), ["15 min", "30 min", "45 min", "60 min", "90 min", "120 min"])
        menu["30 min"].callback(None)
        app.monitor.command.assert_called_with("TV Room", "sleep", 30)
        self.assertIsNone(app.mac_sleep)

    def test_a_running_native_timer_shows_the_time_left_and_can_be_cancelled(self):
        app = self.make_app()
        app.monitor.command = mock.Mock()
        app.groups = self.sleep_groups(sleeping=True)
        app._tick(None)
        _, menu = self.sleep_menu(app, "▶ TV Room")
        self.assertTrue(any(t.startswith("Restam 9:") or t.startswith("Restam 10:") for t in menu))
        menu["Cancelar soneca"].callback(None)
        app.monitor.command.assert_called_with("TV Room", "sleep_cancel")

    def test_menu_is_rebuilt_when_the_native_timer_starts_but_not_while_it_counts(self):
        app = self.make_app()
        app.groups = self.sleep_groups()
        app._tick(None)
        with mock.patch.object(app, "_rebuild_now", wraps=app._rebuild_now) as rebuild:
            app._tick(None)
            self.assertEqual(rebuild.call_count, 0)
            app.groups = self.sleep_groups(sleeping=True)
            app._tick(None)
            self.assertEqual(rebuild.call_count, 1)
            app.groups = [GroupInfo(**{**app.groups[0].__dict__, "sleep_remaining": 590})]
            app._tick(None)  # counting down: only the label text changes
            self.assertEqual(rebuild.call_count, 1)

    def mac_app(self, volume=20):
        app = self.make_app()
        app.ctl.set_volume, app.ctl.stop = mock.Mock(), mock.Mock()
        app.groups = self.sleep_groups(mac=True, volume=volume)
        app.ctl.state.status, app.ctl.state.rooms, app.ctl.state.started_at = controller.STREAMING, ["Office"], 0.0
        app._tick(None)
        return app

    def at(self, now):
        return mock.patch.object(menubar, "time", SimpleNamespace(time=lambda: now))

    def test_the_macs_own_stream_gets_an_app_side_timer_with_the_current_volume(self):
        app = self.mac_app(volume=23)
        _, menu = self.sleep_menu(app, "▶ Office")
        menu["1 min" if "1 min" in menu else "15 min"].callback(None)
        self.assertIsNotNone(app.mac_sleep)
        self.assertEqual(app.mac_sleep.base_volume, 23)
        self.assertEqual(app.mac_sleep.deadline - app.mac_sleep.started, 15 * 60)

    def test_fade_then_stop_then_volume_restored(self):
        app = self.mac_app(volume=20)
        app.mac_sleep = menubar.model.SleepTimer(1, 20, now=1000.0)  # 60 s
        for now, expect in ((1010.0, None), (1030.0, 20), (1045.0, 10), (1058.0, 1)):
            app.ctl.set_volume.reset_mock()
            with self.at(now):
                app._tick_mac_sleep()
            if expect is None:
                app.ctl.set_volume.assert_not_called()
            else:
                app.ctl.set_volume.assert_called_once_with(expect)
        with self.at(1045.0):   # same volume again: not re-sent
            app.ctl.set_volume.reset_mock()
            app._tick_mac_sleep()
            app._tick_mac_sleep()
            self.assertLessEqual(app.ctl.set_volume.call_count, 1)
        with self.at(1060.5):
            app._tick_mac_sleep()
        app.ctl.stop.assert_called_once_with(volume=20)
        self.assertIsNone(app.mac_sleep)
        self.assertFalse(app.want_stream)

    def test_cancelling_during_the_fade_restores_the_volume_but_not_before(self):
        app = self.mac_app(volume=20)
        app.mac_sleep = menubar.model.SleepTimer(1, 20, now=1000.0)
        app._cancel_mac_sleep()  # no fade yet: nothing to undo
        app.ctl.set_volume.assert_not_called()
        app.mac_sleep = menubar.model.SleepTimer(1, 20, now=1000.0)
        with self.at(1050.0):
            app._tick_mac_sleep()
        app.ctl.set_volume.reset_mock()
        app._cancel_mac_sleep()
        app.ctl.set_volume.assert_called_once_with(20)
        self.assertIsNone(app.mac_sleep)

    def test_timer_is_dropped_when_the_stream_is_gone(self):
        app = self.mac_app()
        app.mac_sleep = menubar.model.SleepTimer(5, 20, now=1000.0)
        app.ctl.state.status = controller.STOPPED
        with self.at(1002.0):
            app._tick_mac_sleep()
        self.assertIsNotNone(app.mac_sleep)   # a few seconds of grace: the stream may be restarting
        with self.at(1010.0):
            app._tick_mac_sleep()
        self.assertIsNone(app.mac_sleep)
        app.ctl.stop.assert_not_called()

    # --- alarms ----------------------------------------------------------------------------------

    def alarm_app(self):
        app = self.make_app()
        app.alarm_manager.alarms = [AlarmInfo("1", "Office", datetime.time(6, 30), "WEEKDAYS", True, 20),
                                    AlarmInfo("2", "TV Room", datetime.time(23, 0), "ON_06", False, 10)]
        app.alarm_manager.version += 1
        for name in ("add", "change", "remove"):
            setattr(app.alarm_manager, name, mock.Mock())
        app.groups = self.groups()
        app._tick(None)
        return app

    def test_alarms_menu_structure_and_state(self):
        app = self.alarm_app()
        top = self.children(app.alarms_menu)
        self.assertEqual(list(top), ["06:30 · Office · Dias úteis", "23:00 · TV Room · Dom, Sáb", "Novo alarme…"])
        self.assertEqual(top["06:30 · Office · Dias úteis"].state, 1)
        self.assertEqual(top["23:00 · TV Room · Dom, Sáb"].state, 0)
        one = self.children(top["06:30 · Office · Dias úteis"])
        self.assertEqual(list(one), ["Ativado", "Horário…", "Repetir", "Volume: 20%", "Volume +5", "Volume −5", "Remover alarme"])
        repeat = self.children(one["Repetir"])
        self.assertEqual({k: v.state for k, v in repeat.items()},
                         {"Todos os dias": 0, "Dias úteis": 1, "Fins de semana": 0, "Uma vez": 0})

    def test_alarm_actions(self):
        app = self.alarm_app()
        one = self.children(self.children(app.alarms_menu)["06:30 · Office · Dias úteis"])
        two = self.children(self.children(app.alarms_menu)["23:00 · TV Room · Dom, Sáb"])
        m = app.alarm_manager
        one["Ativado"].callback(None)
        m.change.assert_called_with("1", enabled=False)       # it was on
        two["Ativado"].callback(None)
        m.change.assert_called_with("2", enabled=True)        # it was off
        self.children(one["Repetir"])["Fins de semana"].callback(None)
        m.change.assert_called_with("1", recurrence="WEEKENDS")
        one["Volume +5"].callback(None)
        m.change.assert_called_with("1", volume=25)
        two["Volume −5"].callback(None)
        m.change.assert_called_with("2", volume=5)
        one["Remover alarme"].callback(None)
        m.remove.assert_called_with("1")

    def test_alarm_volume_is_kept_within_bounds(self):
        app = self.alarm_app()
        app.alarm_manager.alarms = [AlarmInfo("9", "Office", datetime.time(7, 0), "DAILY", True, 98),
                                    AlarmInfo("8", "Office", datetime.time(8, 0), "DAILY", True, 2)]
        app.alarm_manager.version += 1
        app._tick(None)
        top = self.children(app.alarms_menu)
        self.children(top["07:00 · Office · Todos os dias"])["Volume +5"].callback(None)
        app.alarm_manager.change.assert_called_with("9", volume=100)
        self.children(top["08:00 · Office · Todos os dias"])["Volume −5"].callback(None)
        app.alarm_manager.change.assert_called_with("8", volume=0)

    def test_new_alarm_dialog(self):
        app = self.alarm_app()
        with mock.patch.object(app, "_ask", return_value="7:15"):
            app.on_alarm_new(None)
        app.alarm_manager.add.assert_called_once_with("Office", datetime.time(7, 15))
        app.alarm_manager.add.reset_mock()
        with mock.patch.object(app, "_ask", return_value="quase sete"), mock.patch.object(rumps, "alert") as alert:
            app.on_alarm_new(None)
        alert.assert_called_once()
        with mock.patch.object(app, "_ask", return_value=None):
            app.on_alarm_new(None)
        app.alarm_manager.add.assert_not_called()

    def test_new_alarm_goes_to_the_play_target_room(self):
        app = self.alarm_app()
        app.cfg["play_target"] = "TV Room"
        with mock.patch.object(app, "_ask", return_value="22:00") as ask:
            app.on_alarm_new(None)
        self.assertIn("TV Room", ask.call_args.args[0])
        app.alarm_manager.add.assert_called_once_with("TV Room", datetime.time(22, 0))

    def test_change_alarm_time_dialog(self):
        app = self.alarm_app()
        info = app.alarm_manager.alarms[0]
        with mock.patch.object(app, "_ask", return_value="06:45") as ask:
            app.on_alarm_time(info)
        self.assertEqual(ask.call_args.args[1], "06:30")   # the current time is the default
        app.alarm_manager.change.assert_called_once_with("1", start_time=datetime.time(6, 45))

    def test_alarms_menu_rebuilds_only_when_the_alarms_change(self):
        app = self.alarm_app()
        with mock.patch.object(app, "_rebuild_alarms", wraps=app._rebuild_alarms) as rebuild:
            app._tick(None)
            app._tick(None)
            self.assertEqual(rebuild.call_count, 0)
            app.alarm_manager.version += 1
            app._tick(None)
            self.assertEqual(rebuild.call_count, 1)

    def test_empty_alarm_list(self):
        app = self.make_app()
        self.assertEqual(list(self.children(app.alarms_menu)), ["Nenhum alarme", "Novo alarme…"])

    # --- scenes ----------------------------------------------------------------------------------

    def scene_app(self):
        app = self.make_app()
        now = time.time()
        app.groups = [GroupInfo("Office", ("Office",), PLAYING, uri=self.MAC_URI, channel="Mac",
                                levels=(("Office", 15, False),), read_at=now),
                      GroupInfo("TV Room", ("TV Room",), STOPPED, levels=(("TV Room", 30, True),), read_at=now)]
        app.settings = {"TV Room": RoomSettings(2, 0, True, False, True, True, True)}
        app.scene_runner = mock.Mock()
        app._tick(None)
        return app

    def test_empty_scene_menu(self):
        app = self.make_app()
        self.assertEqual(list(self.children(app.scenes_menu)), ["Nenhuma cena salva", "Salvar cena atual…"])

    def test_saving_the_current_state_as_a_scene(self):
        app = self.scene_app()
        app.ctl.state.status, app.ctl.state.rooms, app.ctl.state.url = controller.STREAMING, ["Office"], "http://10.0.0.2:8000/mac.mp3"
        app.ctl.state.started_at = 0.0
        with mock.patch.object(app, "_ask", return_value="Trabalho"):
            app.on_scene_save(None)
        saved = config.load()["scenes"]
        self.assertEqual([s["name"] for s in saved], ["Trabalho"])
        scene = saved[0]
        self.assertEqual((scene["stream"], scene["stream_rooms"]), ("on", ["Office"]))
        self.assertEqual(scene["rooms"]["Office"]["volume"], 15)
        self.assertEqual(scene["rooms"]["TV Room"], {"volume": 30, "mute": True, "bass": 2, "treble": 0, "loudness": True,
                                                    "night_mode": False, "dialog_mode": True, "surround": True})
        self.assertEqual(scenes.validate(scene), [])
        app._tick(None)
        self.assertEqual(list(self.children(app.scenes_menu)), ["Trabalho", "Salvar cena atual…", "Remover cena"])

    def test_saving_with_the_same_name_replaces_and_cancel_does_nothing(self):
        app = self.scene_app()
        for name in ("A", "A"):
            with mock.patch.object(app, "_ask", return_value=name):
                app.on_scene_save(None)
        self.assertEqual([s["name"] for s in config.load()["scenes"]], ["A"])
        for answer in (None, ""):
            with mock.patch.object(app, "_ask", return_value=answer):
                app.on_scene_save(None)
        self.assertEqual(len(config.load()["scenes"]), 1)

    def test_a_scene_that_is_not_streaming_records_the_stream_as_off(self):
        app = self.scene_app()   # the controller is stopped
        with mock.patch.object(app, "_ask", return_value="Silêncio"):
            app.on_scene_save(None)
        self.assertEqual(config.load()["scenes"][0]["stream"], "off")

    def test_applying_and_removing_scenes(self):
        app = self.scene_app()
        scene = {"name": "Cinema", "stream": "off"}
        app.cfg["scenes"] = [scene]
        app._save()
        app._tick(None)
        self.children(app.scenes_menu)["Cinema"].callback(None)
        app.scene_runner.apply_async.assert_called_once_with(scene, app.groups)
        self.children(self.children(app.scenes_menu)["Remover cena"])["Cinema"].callback(None)
        self.assertEqual(config.load()["scenes"], [])

    def test_an_invalid_scene_is_not_applied(self):
        app = self.scene_app()
        with mock.patch.object(rumps, "alert") as alert:
            app.on_scene({"name": "x", "stream": "on"})   # no stream_rooms
        alert.assert_called_once()
        app.scene_runner.apply_async.assert_not_called()

    def test_scene_actions_start_the_stream_without_touching_the_menu(self):
        app = self.scene_app()
        app.ctl.start = mock.Mock()
        actions = menubar._SceneActions(app)
        actions.stream_on(["Office", "Sala"], 12)
        app.ctl.start.assert_called_once()
        args = app.ctl.start.call_args.args
        self.assertEqual((args[0], args[6]), (["Office", "Sala"], 12))
        self.assertEqual(config.load()["rooms"], ["Office", "Sala"])
        self.assertTrue(app._rooms_dirty and app.want_stream)
        app._tick(None)
        self.assertFalse(app._rooms_dirty)
        app.ctl.start.reset_mock()
        actions.stream_on(["Office"], None)
        self.assertEqual(app.ctl.start.call_args.args[6], app.cfg["volume"])   # no scene volume: the usual one

    def test_scene_actions_stop_the_stream(self):
        app = self.scene_app()
        app.ctl.stop = mock.Mock()
        menubar._SceneActions(app).stream_off()
        app.ctl.stop.assert_called_once_with()
        self.assertFalse(app.want_stream)

    def test_scene_actions_play_favorites_urls_and_tv_on_the_rooms_group(self):
        app = self.scene_app()
        app.library.favorites = [Favorite("LoungeFM", RADIO)]
        app.library.play_favorite, app.library.play_url = mock.Mock(), mock.Mock()
        app.monitor.room_command = mock.Mock()
        actions = menubar._SceneActions(app)
        actions.favorite("TV Room", "loungefm")                       # case-insensitive
        self.assertEqual(app.library.play_favorite.call_args.args[0], "TV Room")
        with self.assertLogs("mac_sonos", level="WARNING"):
            actions.favorite("TV Room", "Nonexistent")
            actions.favorite("Nowhere", "LoungeFM")
            actions.url("Nowhere", "http://a.b/c", "x")
        self.assertEqual(app.library.play_favorite.call_count, 1)
        actions.url("Office", "http://a.b/c", "My")
        app.library.play_url.assert_called_once_with("Office", "http://a.b/c", "My")
        actions.tv("TV Room")
        app.monitor.room_command.assert_called_once_with("TV Room", "tv")

    # --- media keys ------------------------------------------------------------------------------

    def key_app(self, streaming=True):
        app = self.scene_app()
        app.monitor.command, app.monitor.room_command = mock.Mock(), mock.Mock()
        if streaming:
            app.ctl.state.status, app.ctl.state.rooms, app.ctl.state.started_at = controller.STREAMING, ["Office"], 0.0
        return app

    def test_volume_keys_drive_the_stream_group_while_streaming(self):
        app = self.key_app()
        self.assertTrue(app._on_media_key("volume_up", False))
        app.monitor.command.assert_called_with("Office", "volume_by", 5)
        self.assertTrue(app._on_media_key("volume_down", True))      # a held volume key keeps going
        app.monitor.command.assert_called_with("Office", "volume_by", -5)

    def test_volume_keys_go_to_the_mac_when_not_streaming(self):
        app = self.key_app(streaming=False)
        self.assertFalse(app._on_media_key("volume_up", False))
        app.monitor.command.assert_not_called()

    def test_mute_key_mutes_the_whole_group_then_unmutes_it(self):
        app = self.key_app()
        app.groups = [GroupInfo("Office", ("Office", "Sala"), PLAYING, uri=self.MAC_URI, channel="Mac",
                                levels=(("Office", 15, False), ("Sala", 15, True)), read_at=time.time())]
        app._on_media_key("mute", False)
        self.assertEqual([c.args for c in app.monitor.room_command.call_args_list],
                         [("Office", "mute", True), ("Sala", "mute", True)])    # one was loud: mute all
        app.monitor.room_command.reset_mock()
        app.groups = [GroupInfo(**{**app.groups[0].__dict__, "levels": (("Office", 15, True), ("Sala", 15, True))})]
        app._on_media_key("mute", False)
        self.assertEqual([c.args for c in app.monitor.room_command.call_args_list],
                         [("Office", "mute", False), ("Sala", "mute", False)])

    def test_transport_keys_drive_the_target_group(self):
        app = self.key_app(streaming=False)
        app.cfg["play_target"] = "TV Room"
        app.groups = [GroupInfo("TV Room", ("TV Room",), PLAYING, uri="x-sonos-spotify:1", read_at=time.time(),
                                actions=frozenset({"play", "pause", "next", "previous"}))]
        self.assertTrue(app._on_media_key("play", False))
        app.monitor.command.assert_called_with("TV Room", "pause")
        self.assertTrue(app._on_media_key("next", False))
        app.monitor.command.assert_called_with("TV Room", "next")
        app.monitor.command.reset_mock()
        self.assertTrue(app._on_media_key("next", True))             # repeat: swallowed, not repeated
        app.monitor.command.assert_not_called()

    def test_transport_keys_go_to_the_mac_when_the_stream_is_the_only_thing_playing(self):
        app = self.key_app()
        for key in ("play", "next", "previous"):
            self.assertFalse(app._on_media_key(key, False), key)
        app.monitor.command.assert_not_called()

    def test_turning_media_keys_on_without_permission_asks_for_it(self):
        app = self.make_app()
        with mock.patch.object(app.media_keys, "start", return_value=False) as start, \
                mock.patch.object(rumps, "alert") as alert:
            app.on_media_keys(None)
        start.assert_called_once_with(prompt=True)
        alert.assert_called_once()
        self.assertTrue(config.load()["media_keys"])     # stays on: it starts by itself once permission is granted

    def test_with_permission_it_just_turns_on_and_off(self):
        app = self.make_app()
        with mock.patch.object(app.media_keys, "start", return_value=True), mock.patch.object(rumps, "alert") as alert, \
                mock.patch.object(app.media_keys, "stop") as stop:
            app.on_media_keys(None)
            alert.assert_not_called()
            app.on_media_keys(None)
            stop.assert_called_once()
        self.assertFalse(config.load()["media_keys"])

    def test_it_retries_quietly_until_the_permission_arrives(self):
        app = self.make_app()
        app.cfg["media_keys"] = True
        with mock.patch.object(app.media_keys, "start", return_value=False) as start:
            app.ticks = 1
            for _ in range(10):
                app._tick_media_keys()
                app.ticks += 1
            self.assertGreaterEqual(start.call_count, 2)
            self.assertTrue(all(c == mock.call(prompt=False) for c in start.call_args_list))
        app.cfg["media_keys"] = False
        with mock.patch.object(app.media_keys, "start") as start:
            app.ticks = 5
            app._tick_media_keys()
            start.assert_not_called()

    def test_language_menu_is_hidden_with_a_single_language(self):
        app = self.make_app()
        self.assertEqual(i18n.available_languages(), ["pt"])
        self.assertNotIn("Idioma", self.top_titles(app))

    def test_switching_language_retitles_the_menu(self):
        en = {k: (v if k.startswith("meta.") else f"EN {v}") for k, v in
              json.loads((self.locales / "pt.json").read_text(encoding="utf-8")).items()}
        en["meta.language_name"] = "English"
        (self.locales / "en.json").write_text(json.dumps(en), encoding="utf-8")
        i18n._catalogs.clear()
        app = self.make_app()
        self.assertIn("Idioma", self.top_titles(app))
        self.assertEqual([i.title for i in app.language_menu.values()], ["Automático", "English", "Português"])
        self.assertIn("Sair", self.top_titles(app))

        app.on_language("en")
        titles = self.top_titles(app)
        self.assertIn("EN Sair", titles)
        self.assertIn("EN Idioma", titles)
        self.assertEqual(config.load()["language"], "en")
        app.groups = self.groups()
        app._tick(None)
        self.assertEqual(app.status_item.title, "EN Parado")
        self.assertTrue(any(i.title.startswith("EN Procurando") or i.title.startswith("Office")
                            for i in app.house_menu.values() if hasattr(i, "title")))
        checked = {i.title: i.state for i in app.language_menu.values()}
        self.assertEqual(checked["English"], 1)

        app.on_language("auto")  # system says pt-BR
        self.assertEqual(i18n.current_language(), "pt")
        self.assertIn("Sair", self.top_titles(app))


if __name__ == "__main__":
    unittest.main()
