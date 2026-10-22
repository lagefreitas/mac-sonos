"""The GitHub Pages site (docs/) stays true to the app: python -m unittest discover tests"""
import importlib.util
import json
import re
import struct
import subprocess
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


class Branding(unittest.TestCase):
    def test_share_image_is_a_1200_by_630_png_and_the_tags_point_at_it(self):
        data = (ROOT / "docs" / "og.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (1200, 630))
        self.assertLess(len(data), 600_000)   # link previews are fetched by crawlers: keep it light
        html = PAGE.read_text(encoding="utf-8")
        for tag in ('property="og:image" content="https://lagefreitas.github.io/mac-sonos/og.png"',
                    'name="twitter:image" content="https://lagefreitas.github.io/mac-sonos/og.png"',
                    'name="twitter:card" content="summary_large_image"',
                    'property="og:image:width" content="1200"', 'property="og:image:height" content="630"'):
            self.assertIn(tag, html)

    def test_the_byline_names_the_author_and_the_ai_help_truthfully(self):
        html = PAGE.read_text(encoding="utf-8")
        author = json.loads((ROOT / "tools" / "site" / "author.json").read_text(encoding="utf-8"))
        self.assertIn(f"Made by <b>{author['name']}</b>", html)
        self.assertIn("Claude Code", html)
        # the model named on the page is the one the commits credit
        trailers = subprocess.run(["git", "log", "--format=%B"], cwd=ROOT, capture_output=True, text=True).stdout
        credited = set(re.findall(r"Co-Authored-By: (Claude [A-Za-z]+ [\d.]+)", trailers))
        named = re.findall(r"Claude Code</a> \((Claude [A-Za-z]+ [\d.]+)\)", html)
        self.assertTrue(named and set(named) <= credited, f"page names {named}, commits credit {credited}")
        self.assertIn(f"Copyright (c) 2026 {author['name']}", (ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_no_placeholder_leaks_and_the_linkedin_link_is_valid_or_absent(self):
        html = PAGE.read_text(encoding="utf-8")
        self.assertNotRegex(html, r"__[A-Z_]+__")
        author = json.loads((ROOT / "tools" / "site" / "author.json").read_text(encoding="utf-8"))
        if author["linkedin"]:
            self.assertRegex(author["linkedin"], r"^https://([a-z]{2,3}\.)?linkedin\.com/in/[^/\s]+/?$")
            self.assertIn(f'<a href="{author["linkedin"]}" rel="me">LinkedIn</a>', html)
        else:
            self.assertNotIn("linkedin.com", html)


class SiteFiles(unittest.TestCase):
    def test_the_license_is_mit_and_the_site_and_readme_say_so(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Permission is hereby granted, free of charge", text)
        self.assertRegex(text, r"Copyright \(c\) 20\d\d \S")
        self.assertIn("MIT License", PAGE.read_text(encoding="utf-8"))
        self.assertIn("[MIT](LICENSE)", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_pages_serves_docs_without_jekyll(self):
        self.assertTrue((ROOT / "docs" / ".nojekyll").exists())
        self.assertTrue((ROOT / "docs" / "404.html").exists())
        self.assertTrue(PAGE.exists())

    def test_every_english_block_has_a_portuguese_twin(self):
        """Except the blocks marked "keep": English on purpose, shown in the Portuguese view too."""
        html = PAGE.read_text(encoding="utf-8")
        body = html[html.index("<body>"):html.index("<script>")]
        en, pt = [], []
        for tag, attrs in re.findall(r"<([a-z0-9]+)([^>]*)>", body):
            lang = re.search(r'\slang="(en|pt)"', attrs)
            if lang and "keep" not in (re.search(r'class="([^"]*)"', attrs) or [None, ""])[1].split():
                (en if lang.group(1) == "en" else pt).append(tag)
        self.assertEqual(en, pt, "the English and Portuguese blocks must come in the same order and kinds")
        self.assertGreater(len(en), 50)

    def test_the_english_only_blocks_stay_visible_in_the_portuguese_view(self):
        html = PAGE.read_text(encoding="utf-8")
        self.assertIn('html[data-lang="pt"] .keep[lang="en"]{display:revert!important}', html)
        self.assertRegex(html, r'<h2 class="keep" lang="en">Why I built this</h2>')

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
