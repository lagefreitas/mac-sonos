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
