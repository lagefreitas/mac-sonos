#!/usr/bin/env python3
"""Builds docs/index.html (the GitHub Pages site) from tools/site/template.html.

The "menu tour" on the page is not a mock-up: it is the menu tree of the real MenuBarApp, rendered headless with
made-up rooms, songs and alarms, in each of the app's languages. Run it on a Mac after changing the menu or the
template:  .venv/bin/python tools/build_site.py
"""
import datetime
import html
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mac_sonos import config, controller, i18n  # noqa: E402
from mac_sonos.sonos.alarms import AlarmInfo  # noqa: E402
from mac_sonos.sonos.library import Favorite, PLAYLIST, QueueEntry, RADIO, TRACK  # noqa: E402
from mac_sonos.sonos.monitor import GroupInfo, Monitor, PLAYING, RoomSettings, STOPPED  # noqa: E402

LANGUAGES = ("en", "es", "fr", "pt")


def demo_app():
    from mac_sonos.ui import menubar
    now = time.time()
    app = menubar.MenuBarApp()
    stream_uri = "x-rincon-mp3radio://http://192.168.1.20:8000/mac.mp3"
    app.cfg.update(rooms=["Living Room", "Kitchen"], play_target="TV Room", capture="tap", mute_mac=True, volume=24,
                   scenes=[{"name": "Work"}, {"name": "Movie night"}],
                   radio_urls=[{"title": "My station", "url": "http://example.com/stream"}])
    app.ctl.state.status, app.ctl.state.rooms = controller.STREAMING, ["Living Room", "Kitchen"]
    app.ctl.state.url, app.ctl.state.started_at = "http://192.168.1.20:8000/mac.mp3", now - 60
    app.groups = [
        GroupInfo("Living Room", ("Living Room", "Kitchen"), PLAYING, uri=stream_uri, channel="Mac",
                  levels=(("Living Room", 24, False), ("Kitchen", 18, False)), read_at=now),
        GroupInfo("TV Room", ("TV Room",), PLAYING, title="Morning Light", artist="The Examples", album="Sample Album",
                  duration=215, position=83, uri="x-sonos-spotify:track", queue_position=2,
                  actions=frozenset({"play", "pause", "next", "previous", "seektime"}),
                  levels=(("TV Room", 30, False),), read_at=now),
        GroupInfo("Bedroom", ("Bedroom",), STOPPED, levels=(("Bedroom", 15, False),), read_at=now)]
    app.settings = {"Living Room": RoomSettings(2, 0, True), "Kitchen": RoomSettings(0, 0, True),
                    "TV Room": RoomSettings(2, 0, True, False, True, True, True), "Bedroom": RoomSettings(0, 0, True)}
    app.library.favorites = [Favorite("Morning Radio", RADIO), Favorite("Jazz Lounge", RADIO), Favorite("Rainy Day", TRACK),
                             Favorite("Focus Mix", PLAYLIST)]
    app.library.playlists = [mock.Mock(title="Weekend Playlist")]
    app.library.queues = {"TV Room": (QueueEntry(0, "Sunrise", "The Examples"), QueueEntry(1, "Morning Light", "The Examples"),
                                      QueueEntry(2, "Long Way Home", "Sample Band"))}
    app.library.version = 1
    app.alarm_manager.alarms = [AlarmInfo("1", "Bedroom", datetime.time(7, 0), "WEEKDAYS", True, 20),
                                AlarmInfo("2", "Kitchen", datetime.time(9, 30), "WEEKENDS", False, 15)]
    app.alarm_manager.version += 1
    app.monitor.library_stub = True
    return app


def tree(menu):
    """The visible items of a rumps menu as nested {title, checked, children}."""
    items = []
    for item in menu.values():
        if type(item).__name__ == "SeparatorMenuItem":
            items.append({"separator": True})
            continue
        title = getattr(item, "title", "")
        if not title or (hasattr(item, "_menuitem") and item._menuitem.isHidden()):
            continue
        node = {"title": title}
        if getattr(item, "state", 0):
            node["checked"] = True
        if hasattr(item, "values") and len(item):
            node["children"] = tree(item)
        items.append(node)
    # no separator first, last or twice in a row
    out = []
    for node in items:
        if node.get("separator") and (not out or out[-1].get("separator")):
            continue
        out.append(node)
    while out and out[-1].get("separator"):
        out.pop()
    return out


def menus():
    saved_path, saved_language = config.PATH, i18n.current_language()
    with tempfile.TemporaryDirectory() as tmp:
        config.PATH = Path(tmp) / "config.json"
        config.save({**config.DEFAULTS, "autostart_stream": False})
        with mock.patch.object(Monitor, "start"):
            app = demo_app()
        out = {}
        try:
            for code in LANGUAGES:
                app.on_language(code)
                app._tick(None)
                out[code] = {"icon": app.title, "items": tree(app.menu)}
        finally:
            app.ctl.shutdown()
            app.library.stop()
            app.alarm_manager.stop()
            config.PATH = saved_path
            i18n._language = saved_language
    return out


SITE = ROOT / "tools" / "site"


def fill(template, root, author, links):
    """Insert the parts every page shares; `root` is the path from the page back to the site's root."""
    parts = {name: (SITE / file).read_text(encoding="utf-8")
             for name, file in (("STYLE", "style.css"), ("HEADER", "header.html"), ("FOOTER", "footer.html"),
                                ("COMMON_JS", "common.js"))}
    page = template
    for name in ("HEADER", "FOOTER"):   # these contain placeholders of their own
        page = page.replace(f"__{name}__", parts[name])
    for name in ("STYLE", "COMMON_JS"):
        page = page.replace(f"__{name}__", parts[name])
    return (page.replace("__ROOT__", root).replace("__AUTHOR_NAME__", author["name"])
            .replace("__AUTHOR_LINKS__", links))


SITE_URL = "https://lagefreitas.github.io/mac-sonos/"
MONTHS = ("January February March April May June July August September October November December").split()


def author_links(author):
    # an empty value leaves the link out instead of publishing a wrong one
    return f' · <a href="{author["linkedin"]}" rel="me">LinkedIn</a>' if author.get("linkedin") else ""


def render_article(entry, author):
    """(url, html) of one article, from its Markdown source (tools/site/articles.json lists them)."""
    links = author_links(author)
    sys.path.insert(0, str(ROOT / "tools"))
    import minimd
    article = minimd.convert((ROOT / entry["source"]).read_text(encoding="utf-8"))
    when = datetime.date.fromisoformat(entry["date"])
    url = f"{SITE_URL}articles/{entry['slug']}/"
    summary = re.sub(r"^TL;DR:\s*", "", article.summary)
    description = summary if len(summary) <= 200 else summary[:197].rsplit(" ", 1)[0] + "..."
    page = fill((SITE / "article.html").read_text(encoding="utf-8"), root="../../", author=author, links=links)
    values = {"__TITLE_ATTR__": html.escape(article.title, quote=True), "__TITLE__": html.escape(article.title, quote=False),
              "__DESCRIPTION__": html.escape(description, quote=True), "__URL__": url, "__DATE__": entry["date"],
              "__DATE_LABEL__": f"{MONTHS[when.month - 1]} {when.year}", "__MINUTES__": str(max(1, round(article.words / 200))),
              "__DECK__": f'<p class="deck">{article.deck}</p>' if article.deck else "", "__BODY__": article.body}
    for key in sorted(values, key=len, reverse=True):    # longest first: __TITLE_ATTR__ before __TITLE__
        page = page.replace(key, values[key])
    return url, page


def article_page(entry, author):
    url, page = render_article(entry, author)
    out = ROOT / "docs" / "articles" / entry["slug"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    return url


def main():
    template = (ROOT / "tools" / "site" / "template.html").read_text(encoding="utf-8")
    author = json.loads((ROOT / "tools" / "site" / "author.json").read_text(encoding="utf-8"))
    links = author_links(author)
    data = menus()
    page = fill(template, root="", author=author, links=links).replace("__MENUS__", json.dumps(data, ensure_ascii=False))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(page, encoding="utf-8")
    urls = [SITE_URL] + [article_page(e, author)
                         for e in json.loads((SITE / "articles.json").read_text(encoding="utf-8"))]
    (ROOT / "docs" / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n", encoding="utf-8")
    (ROOT / "docs" / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print(f"articles: {len(urls) - 1} | sitemap: {len(urls)} URLs")
    count = sum(1 for _ in json.dumps(data["en"]).split('"title"')) - 1
    print(f"docs/index.html written: {len(page):,} bytes, {count} menu entries per language, languages {sorted(data)}")


if __name__ == "__main__":
    main()
