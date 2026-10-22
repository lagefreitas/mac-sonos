"""The GitHub Pages site (docs/) stays true to the app: python -m unittest discover tests"""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "index.html"

try:
    import rumps  # noqa: F401
    HAVE_UI = True
except Exception:  # pragma: no cover - not on macOS
    HAVE_UI = False


def embedded_menus():
    html = PAGE.read_text(encoding="utf-8")
    return json.loads(re.search(r"const MENUS = (.*?);\nconst LANG_NAMES", html, re.S).group(1))


class SiteFiles(unittest.TestCase):
    def test_pages_serves_docs_without_jekyll(self):
        self.assertTrue((ROOT / "docs" / ".nojekyll").exists())
        self.assertTrue((ROOT / "docs" / "404.html").exists())
        self.assertTrue(PAGE.exists())

    def test_every_english_block_has_a_portuguese_twin(self):
        html = PAGE.read_text(encoding="utf-8")
        body = html[html.index("<body>"):html.index("<script>")]
        chunks = re.findall(r'<([a-z0-9]+)[^>]*\slang="(en|pt)"[^>]*>', body)
        en = [t for t, l in chunks if l == "en"]
        pt = [t for t, l in chunks if l == "pt"]
        self.assertEqual(en, pt, "the English and Portuguese blocks must come in the same order and kinds")

    def test_no_resource_is_loaded_from_the_network(self):
        html = PAGE.read_text(encoding="utf-8")
        loaded = re.findall(r'<(?:script|img|iframe|source|video|audio)\b[^>]*\ssrc="(?!data:)[^"]+"', html)
        loaded += re.findall(r'<link\b[^>]*rel="(?:stylesheet|preload|icon)"[^>]*href="(?!data:)[^"]+"', html)
        self.assertEqual(loaded, [])
        self.assertNotIn("@import", html)

    def test_internal_links_resolve(self):
        html = PAGE.read_text(encoding="utf-8")
        ids = set(re.findall(r'\sid="([^"]+)"', html))
        for target in re.findall(r'href="#([^"]+)"', html):
            self.assertIn(target, ids, f"#{target} has no target")

    def test_the_install_steps_match_the_repository(self):
        html = PAGE.read_text(encoding="utf-8")
        for script in ("setup.sh", "build-app.sh"):
            self.assertIn(f"./{script}", html)
            self.assertTrue((ROOT / script).exists())
        requirements = (ROOT / "requirements.txt").read_text()
        for needed in ("py2app", "pyobjc-framework-CoreAudio", "rumps", "soco"):   # what the install really needs
            self.assertIn(needed, requirements)
        self.assertIn("https://github.com/lagefreitas/mac-sonos.git", html)

    def test_the_menu_tour_covers_all_the_apps_languages(self):
        menus = embedded_menus()
        from mac_sonos import i18n
        self.assertEqual(sorted(menus), i18n.available_languages())
        for code, menu in menus.items():
            self.assertGreater(len(menu["items"]), 15, code)


@unittest.skipUnless(HAVE_UI, "needs rumps / AppKit")
class MenuTourIsCurrent(unittest.TestCase):
    def test_the_committed_tour_is_what_the_app_builds_today(self):
        """If someone changes the menu and forgets tools/build_site.py, the site would describe a menu that is gone."""
        spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.menus(), embedded_menus(),
                         "docs/index.html is out of date: run .venv/bin/python tools/build_site.py")


if __name__ == "__main__":
    unittest.main()
