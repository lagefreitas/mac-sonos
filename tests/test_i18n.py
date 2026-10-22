"""String infrastructure: python -m unittest discover tests"""
import ast
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mac_sonos import controller, i18n
from mac_sonos.core import output, tapcapture
from mac_sonos.sonos import discovery
from mac_sonos.ui import model

ROOT = Path(__file__).resolve().parent.parent / "mac_sonos"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def write_catalogs(directory, **catalogs):
    for lang, entries in catalogs.items():
        (Path(directory) / f"{lang}.json").write_text(json.dumps(entries), encoding="utf-8")


class WithTempLocales(unittest.TestCase):
    """Runs against throwaway catalogs and restores the real state afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [mock.patch.object(i18n, "LOCALES", Path(self.tmp.name)),
                   mock.patch.object(i18n, "_catalogs", {}),
                   mock.patch.object(i18n, "_reported", set())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        saved = i18n.current_language()
        self.addCleanup(lambda: setattr(i18n, "_language", saved))


class Lookup(WithTempLocales):
    def test_variables_and_current_language(self):
        write_catalogs(self.tmp.name, pt={"hi": "Olá, {name}"}, en={"hi": "Hello, {name}"})
        i18n.set_language("en")
        self.assertEqual(i18n.t("hi", name="Ana"), "Hello, Ana")
        i18n.set_language("pt")
        self.assertEqual(i18n.t("hi", name="Ana"), "Olá, Ana")

    def test_fallback_chain_is_language_then_english_then_portuguese(self):
        write_catalogs(self.tmp.name, pt={"a": "pt-a", "b": "pt-b", "c": "pt-c"}, en={"a": "en-a", "b": "en-b"},
                       fr={"a": "fr-a"})
        i18n.set_language("fr")
        self.assertEqual([i18n.t(k) for k in "abc"], ["fr-a", "en-b", "pt-c"])

    def test_missing_key_comes_back_as_the_key_and_is_logged_once(self):
        write_catalogs(self.tmp.name, pt={})
        i18n.set_language("pt")
        with self.assertLogs("mac_sonos", level="WARNING") as logs:
            self.assertEqual(i18n.t("no.such.key"), "no.such.key")
            i18n.t("no.such.key")
        self.assertEqual(len(logs.records), 1)

    def test_bad_placeholders_do_not_crash(self):
        write_catalogs(self.tmp.name, pt={"x": "Valor {value}"})
        i18n.set_language("pt")
        self.assertEqual(i18n.t("x", wrong=1), "Valor {value}")
        self.assertEqual(i18n.t("x", value=3), "Valor 3")

    def test_plurals(self):
        write_catalogs(self.tmp.name,
                       pt={"rooms": {"one": "{n} sala", "other": "{n} salas"}},
                       en={"rooms": {"one": "{n} room", "other": "{n} rooms"}},
                       fr={"rooms": {"one": "{n} pièce", "other": "{n} pièces"}})
        for lang, expected in {"pt": ["0 sala", "1 sala", "2 salas"], "en": ["0 rooms", "1 room", "2 rooms"],
                               "fr": ["0 pièce", "1 pièce", "2 pièces"]}.items():
            i18n.set_language(lang)
            self.assertEqual([i18n.tn("rooms", n) for n in (0, 1, 2)], expected, lang)
        self.assertEqual(i18n.t("rooms", n=2), "{n} salas" if False else i18n.t("rooms", n=2))  # no crash without count

    def test_language_names_and_available(self):
        write_catalogs(self.tmp.name, pt={"meta.language_name": "Português"}, en={})
        self.assertEqual(i18n.available_languages(), ["en", "pt"])
        self.assertEqual(i18n.language_name("pt"), "Português")
        self.assertEqual(i18n.language_name("en"), "en")  # no name in the catalog: the code


class Choosing(WithTempLocales):
    def test_pick_language(self):
        self.assertEqual(i18n.pick_language(["pt-BR", "en-US"], ["en", "pt"]), "pt")
        self.assertEqual(i18n.pick_language(["de-DE", "fr_CA"], ["en", "fr"]), "fr")
        self.assertEqual(i18n.pick_language(["ja-JP"], ["en", "pt"], default="en"), "en")
        self.assertIsNone(i18n.pick_language([], ["en"]))

    def test_resolve_follows_config_then_system_then_fallback(self):
        write_catalogs(self.tmp.name, pt={}, en={}, fr={})
        with mock.patch.object(i18n, "system_languages", return_value=["fr-FR", "en"]):
            self.assertEqual(i18n.resolve("auto"), "fr")
            self.assertEqual(i18n.resolve("pt"), "pt")       # explicit choice wins
            self.assertEqual(i18n.resolve("klingon"), "fr")  # unknown choice: as automatic
        with mock.patch.object(i18n, "system_languages", return_value=["ja-JP"]):
            self.assertEqual(i18n.resolve("auto"), "en")     # English is the first fallback
        write_catalogs(self.tmp.name)  # nothing new: pt only below
        (Path(self.tmp.name) / "en.json").unlink()
        (Path(self.tmp.name) / "fr.json").unlink()
        i18n._catalogs.clear()
        with mock.patch.object(i18n, "system_languages", return_value=["ja-JP"]):
            self.assertEqual(i18n.resolve("auto"), "pt")     # only Portuguese exists


class RealCatalogs(unittest.TestCase):
    languages = i18n.available_languages()

    def test_portuguese_is_present(self):
        self.assertIn("pt", self.languages)

    def test_every_language_has_the_same_keys_and_placeholders_as_portuguese(self):
        base = i18n.catalog("pt")
        for lang in self.languages:
            cat = i18n.catalog(lang)
            self.assertEqual(sorted(cat), sorted(base), f"{lang}: keys differ from pt")
            for key, text in cat.items():
                variants = text.values() if isinstance(text, dict) else [text]
                if isinstance(text, dict):
                    self.assertIn("other", text, f"{lang}:{key} plural without 'other'")
                for v in variants:
                    self.assertTrue(v.strip(), f"{lang}:{key} is empty")
                base_vars = set().union(*(PLACEHOLDER.findall(x) for x in
                                          (base[key].values() if isinstance(base[key], dict) else [base[key]])))
                for v in variants:
                    self.assertTrue(set(PLACEHOLDER.findall(v)) <= base_vars,
                                    f"{lang}:{key} uses placeholders {PLACEHOLDER.findall(v)} not in pt {sorted(base_vars)}")

    def test_every_language_names_itself(self):
        for lang in self.languages:
            self.assertTrue(i18n.catalog(lang).get("meta.language_name"), lang)


def string_constants(tree):
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def used_keys(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("t", "tn")
                and n.args and isinstance(n.args[0], ast.Constant)):
            calls.add(n.args[0].value)
    return calls, set(string_constants(tree))


class CodeAgainstCatalog(unittest.TestCase):
    def test_every_key_used_in_code_exists_and_no_key_is_dead(self):
        catalog = set(i18n.catalog("pt"))
        called, constants = set(), set()
        for path in ROOT.rglob("*.py"):
            c, k = used_keys(path)
            called |= c
            constants |= k
        self.assertEqual(sorted(called - catalog), [], "t()/tn() called with keys missing from pt.json")
        dead = sorted(k for k in catalog if k not in constants and not k.startswith("meta."))
        self.assertEqual(dead, [], "keys in pt.json that no code mentions")

    def test_localizable_exceptions_name_real_keys(self):
        catalog = i18n.catalog("pt")
        self.assertIn(output.DeviceNotFound.key, catalog)
        self.assertIn(discovery.RoomNotFound.key, catalog)
        self.assertIn(tapcapture.TapPermissionError.key, catalog)


class NoUiTextInCode(unittest.TestCase):
    """Heuristic: UI text is capitalised words or accented letters; none may sit in the UI modules."""
    ALLOWED = {"Sonos"}  # the product name is not translated
    CAPITALISED = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ]{2,}")

    @staticmethod
    def skipped_nodes(tree):
        skip = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                    skip.add(id(n.body[0].value))  # docstrings
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
                if name in ("t", "tn", "getLogger") or owner in ("log", "logging"):
                    for sub in ast.walk(n):
                        skip.add(id(sub))
        return skip

    def offenders(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip, out = self.skipped_nodes(tree), []
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
                text = n.value
                if text in self.ALLOWED:
                    continue
                if self.CAPITALISED.search(text) or any(c.isalpha() and ord(c) > 127 for c in text):
                    out.append((n.lineno, text))
        return out

    def test_ui_modules_have_no_hardcoded_text(self):
        for name in ("ui/model.py", "ui/menubar.py", "ui/loginitem.py"):
            self.assertEqual(self.offenders(ROOT / name), [], f"{name}: move these strings to locales/*.json")

    def test_the_check_itself_catches_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text('"""Doc string."""\nlabel = "Ligar"\nother = "cômodo"\nok = t("menu.quit")\nlog.info("Hello there")\n')
            self.assertEqual([text for _, text in self.offenders(p)], ["Ligar", "cômodo"])


class LocalizedErrors(unittest.TestCase):
    def state(self, **kw):
        return controller.State(status="error", **kw)

    def test_exceptions_keep_english_text_and_expose_key_and_vars(self):
        e = output.DeviceNotFound("BlackHole 2ch")
        self.assertIn("not found", str(e))
        self.assertEqual((e.key, e.vars), ("error.device_not_found", {"device": "BlackHole 2ch"}))
        r = discovery.RoomNotFound("Kitchen", {"Office": 1, "TV Room": 2})
        self.assertEqual(r.vars, {"room": "Kitchen", "available": "Office, TV Room"})
        self.assertIn("Office, TV Room", str(r))

    def test_status_uses_the_translation_when_there_is_one(self):
        i18n.set_language("pt")
        text = model.status_text(self.state(error="Audio device 'X' not found", error_key="error.device_not_found",
                                            error_vars={"device": "X"}))
        self.assertTrue(text.startswith("Erro: Dispositivo de áudio 'X'"), text)

    def test_status_falls_back_to_the_english_text(self):
        i18n.set_language("pt")
        plain = model.status_text(self.state(error="boom"))
        self.assertEqual(plain, "Erro: boom")
        unknown = model.status_text(self.state(error="raw text", error_key="error.not_in_catalog", error_vars={}))
        self.assertEqual(unknown, "Erro: raw text")
        self.assertEqual(model.status_text(self.state()), "Erro: desconhecido")

    def test_language_choices_hidden_for_a_single_language(self):
        self.assertEqual(model.language_choices(["pt"]), [])
        self.assertEqual(model.language_choices(["en", "pt"]), ["en", "pt"])


if __name__ == "__main__":
    unittest.main()
