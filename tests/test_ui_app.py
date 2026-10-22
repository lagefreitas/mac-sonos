"""The menu bar app itself, instantiated headless (needs macOS + rumps): python -m unittest discover tests"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import rumps  # noqa: F401
    from mac_sonos.ui import menubar
    HAVE_UI = True
except Exception:  # pragma: no cover - not on macOS
    HAVE_UI = False

from mac_sonos import config, i18n
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
