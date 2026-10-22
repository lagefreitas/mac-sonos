"""Interface strings.

Catalogs are flat JSON files, mac_sonos/locales/<language>.json: {"menu.quit": "Sair", ...}. Look strings up
with t("menu.quit") or t("status.streaming", rooms="Office"); never write UI text in code. A plural entry is
an object: {"one": "{n} sala", "other": "{n} salas"}, used through tn("key", count, ...).

Lookup order: the chosen language, then English, then Portuguese; a key found nowhere is returned as is (and
logged once) so a missing translation is visible but never crashes.
"""
import json
import locale
import logging
import os
from pathlib import Path

log = logging.getLogger("mac_sonos")

LOCALES = Path(__file__).parent / "locales"
FALLBACKS = ("en", "pt")
AUTO = "auto"

_catalogs = {}
_language = "pt"
_reported = set()


def available_languages():
    return sorted(p.stem for p in LOCALES.glob("*.json"))


def catalog(language):
    """The key -> text mapping of a language ({} if there is no such catalog)."""
    if language not in _catalogs:
        try:
            _catalogs[language] = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogs[language] = {}
    return _catalogs[language]


def language_name(language):
    """A language's own name, e.g. 'Português' (what the language menu shows)."""
    return catalog(language).get("meta.language_name", language)


def pick_language(preferred, available, default=None):
    """First of the system's preferred tags ('pt-BR', 'en_US'...) whose base language is available."""
    for tag in preferred:
        base = str(tag).replace("_", "-").split("-")[0].lower()
        if base in available:
            return base
    return default


def system_languages():
    try:
        from Foundation import NSLocale
        langs = [str(x) for x in NSLocale.preferredLanguages()]
        if langs:
            return langs
    except Exception:
        pass
    return [os.environ.get("LANG", "") or (locale.getlocale()[0] or "")]


def resolve(choice=AUTO):
    """The language code a config value stands for: a code if available, else the system's, else a fallback."""
    available = available_languages()
    if choice in available:
        return choice
    default = next((f for f in FALLBACKS if f in available), available[0] if available else "pt")
    return pick_language(system_languages(), available, default)


def set_language(choice=AUTO):
    global _language
    _language = resolve(choice)
    return _language


def current_language():
    return _language


def _chain():
    return [_language] + [f for f in FALLBACKS if f != _language]


def _lookup(key):
    for lang in _chain():
        entry = catalog(lang).get(key)
        if entry is not None:
            return lang, entry
    if key not in _reported:
        _reported.add(key)
        log.warning("missing translation: %s", key)
    return None, None


def _format(text, variables, key):
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        log.warning("bad placeholders in %s: %r with %s", key, text, sorted(variables))
        return text


def t(key, **variables):
    _, entry = _lookup(key)
    if entry is None:
        return key
    if isinstance(entry, dict):  # a plural entry used without a count: take the general form
        entry = entry.get("other", "")
    return _format(entry, variables, key)


def plural_category(language, n):
    """'one' or 'other' (enough for pt, en, es, fr; pt and fr count 0 as singular)."""
    if language in ("pt", "fr"):
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 else "other"


def tn(key, count, **variables):
    lang, entry = _lookup(key)
    if entry is None:
        return key
    if isinstance(entry, dict):
        entry = entry.get(plural_category(lang, count)) or entry.get("other", "")
    return _format(entry, {"n": count, **variables}, key)
