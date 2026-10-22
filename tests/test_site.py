"""The GitHub Pages site (docs/) stays true to the app: python -m unittest discover tests"""
import importlib.util
import html as htmllib
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


ARTICLE = ROOT / "docs" / "articles" / "core-audio-tap-from-python" / "index.html"


class ArticlePage(unittest.TestCase):
    def setUp(self):
        self.html = ARTICLE.read_text(encoding="utf-8")
        self.md = (ROOT / "articles" / "core-audio-tap-from-python.md").read_text(encoding="utf-8")

    def test_it_is_a_complete_page_with_its_own_metadata(self):
        h = self.html
        self.assertRegex(h, r"<title>Capturing macOS system audio from Python with Core Audio taps — Mac Sonos</title>")
        self.assertIn('<link rel="canonical" href="https://lagefreitas.github.io/mac-sonos/articles/core-audio-tap-from-python/">', h)
        self.assertIn('<meta property="og:type" content="article">', h)
        self.assertIn('<meta property="og:image" content="https://lagefreitas.github.io/mac-sonos/og.png">', h)
        self.assertIn('<meta property="article:published_time" content="2026-10-22">', h)
        self.assertRegex(h, r'<meta name="description" content="since macOS 14\.2')
        self.assertNotRegex(h, r"__[A-Z_]+__")
        self.assertEqual(h.count("<h1>"), 1)

    def test_it_stays_english_in_the_portuguese_view_and_shares_the_site_chrome(self):
        self.assertIn('<article class="prose keep" lang="en">', self.html)
        for link in ("../../#features", "../../#how", "../../#tour", "../../#install"):
            self.assertIn(f'href="{link}"', self.html)
        self.assertIn('href="../../"', self.html)               # the way back
        self.assertIn("Made by <b>", self.html)
        self.assertIn("data-set-lang", self.html)

    def test_every_heading_has_a_unique_id_and_every_code_block_a_copy_button(self):
        ids = re.findall(r'<h2 id="([^"]+)"', self.html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(re.findall(r"^## ", self.md, re.M)))
        self.assertEqual(self.html.count("<pre>"), self.md.count("```") // 2)
        self.assertEqual(self.html.count("data-copy>Copy</button>"), self.html.count("<pre>"))

    def test_the_code_on_the_page_is_the_script_that_was_run(self):
        script = (ROOT / "articles" / "tap_demo.py").read_text(encoding="utf-8")
        shown = htmllib.unescape(re.search(r"<pre><code class=\"language-python\">(.*?)</code>", self.html, re.S).group(1))
        strip = lambda t: re.sub(r"\s+#.*$", "", t, flags=re.M).strip()
        self.assertEqual(strip(shown), strip(script[script.index("import ctypes\n"):]))
        import ast
        ast.parse(script)   # and it is valid Python

    def test_the_home_page_links_to_it(self):
        home = PAGE.read_text(encoding="utf-8")
        self.assertGreaterEqual(home.count('href="articles/core-audio-tap-from-python/"'), 2)   # the card and the footer

    def test_sitemap_and_robots(self):
        sitemap = (ROOT / "docs" / "sitemap.xml").read_text(encoding="utf-8")
        for url in ("https://lagefreitas.github.io/mac-sonos/", "https://lagefreitas.github.io/mac-sonos/articles/core-audio-tap-from-python/"):
            self.assertIn(f"<loc>{url}</loc>", sitemap)
        robots = (ROOT / "docs" / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("Sitemap: https://lagefreitas.github.io/mac-sonos/sitemap.xml", robots)

    def test_no_claim_in_the_article_goes_beyond_what_was_verified(self):
        """Phrases I removed after checking them against the measurements must not come back."""
        for gone in ("can read and request the state", "32-bit float. In my app I check both", "and so did the one after that"):
            self.assertNotIn(gone, self.md)


class ArticleIsCurrent(unittest.TestCase):
    def test_the_committed_page_is_what_the_markdown_builds_today(self):
        """Edit articles/*.md and forget tools/build_site.py, and the site would keep the old text."""
        module = load_generator()
        author = json.loads((ROOT / "tools" / "site" / "author.json").read_text(encoding="utf-8"))
        for entry in json.loads((ROOT / "tools" / "site" / "articles.json").read_text(encoding="utf-8")):
            url, page = module.render_article(entry, author)
            committed = (ROOT / "docs" / "articles" / entry["slug"] / "index.html").read_text(encoding="utf-8")
            self.assertEqual(page, committed, f"{entry['slug']}: run .venv/bin/python tools/build_site.py")


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
        html = PAGE.read_text(encoding="utf-8") + ARTICLE.read_text(encoding="utf-8")
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


def load_generator():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(HAVE_UI, "needs rumps / AppKit")
class MenuTourIsCurrent(unittest.TestCase):
    def test_the_committed_tour_is_what_the_app_builds_today(self):
        """If someone changes the menu and forgets tools/build_site.py, the site would describe a menu that is gone."""
        module = load_generator()
        self.assertEqual(module.menus(), embedded_menus(),
                         "docs/index.html is out of date: run .venv/bin/python tools/build_site.py")


if __name__ == "__main__":
    unittest.main()
