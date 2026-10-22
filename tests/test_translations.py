"""Quality of the translations: python -m unittest discover tests"""
import re
import subprocess
import unittest
from pathlib import Path

from mac_sonos import i18n

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "es", "fr")

# Values that are legitimately the same as the Portuguese ones (shared words, loanwords, formats). A new key left
# untranslated would show up as a difference from these lists, and the test asks for a decision.
SAME_AS_PT = {
    "en": {"menu.volume", "library.playlists", "library.group", "dialog.ok", "sleep.minutes", "alarm.title",
           "alarm.volume", "alarm.volume_up", "alarm.volume_down", "transport.back", "transport.forward", "track.tv",
           "track.line_in", "house.volume_label", "house.volume_up", "house.volume_down", "sound.loudness",
           "sound.surround"},
    "es": {"status.connecting", "menu.house", "menu.format", "menu.language", "menu.language_auto", "menu.library",
           "menu.favorites", "library.group", "dialog.ok", "dialog.cancel", "sleep.minutes", "alarm.title",
           "alarm.repeat", "alarm.day.0", "alarm.day.6", "transport.previous", "transport.pause", "transport.back",
           "transport.forward", "track.tv", "house.group", "sound.bass", "sound.bass_up", "sound.bass_down",
           "sound.treble", "sound.treble_up", "sound.treble_down"},
    "fr": {"menu.volume", "library.playlists", "library.group", "dialog.ok", "sleep.minutes", "menu.alarms",
           "alarm.title", "alarm.volume_up", "alarm.volume_down", "transport.back", "transport.forward", "track.tv",
           "house.volume_up", "house.volume_down", "sound.bass_up", "sound.bass_down", "sound.loudness",
           "sound.surround"},
}
LONG_TEXT = {"alarm.time_prompt", "alarm.new_time_prompt", "alarm.invalid_time", "radio.url_prompt", "radio.name_prompt",
             "radio.invalid_url", "scene.invalid", "scene.name_prompt", "media.permission", "login.only_app",
             "login.approve", "warning.mac_volume", "error.device_not_found", "error.room_not_found"}
MENU_PREFIXES = ("menu.", "transport.", "house.", "sound.", "sleep.", "alarm.", "scene.", "library.", "queue.",
                 "radio.", "now.", "status.", "toggle.", "level.")
MENU_WIDTH_LIMIT = 42   # characters; the longest one today is 37 (es/fr), 33 in Portuguese


class Catalogs(unittest.TestCase):
    def test_all_four_languages_exist(self):
        self.assertEqual(i18n.available_languages(), ["en", "es", "fr", "pt"])

    def test_nothing_was_left_untranslated_by_accident(self):
        pt = i18n.catalog("pt")
        for lang in LANGS:
            cat = i18n.catalog(lang)
            same = {k for k, v in cat.items() if v == pt[k] and k != "meta.language_name"}
            self.assertEqual(same, SAME_AS_PT[lang],
                             f"{lang}: values identical to Portuguese changed; new: {sorted(same - SAME_AS_PT[lang])}, "
                             f"now translated: {sorted(SAME_AS_PT[lang] - same)}")

    def test_no_portuguese_leaked_into_the_others(self):
        for lang in LANGS:
            for key, text in i18n.catalog(lang).items():
                self.assertFalse(any(ch in text for ch in "ãõ"), f"{lang}:{key}: {text}")

    def test_menu_entries_stay_narrow_in_every_language(self):
        for lang in ("pt",) + LANGS:
            for key, text in i18n.catalog(lang).items():
                if key.startswith(MENU_PREFIXES) and key not in LONG_TEXT and key != "meta.language_name":
                    self.assertLessEqual(len(text), MENU_WIDTH_LIMIT, f"{lang}:{key} is {len(text)} chars: {text}")

    def test_placeholders_are_identical_not_just_a_subset(self):
        pt = i18n.catalog("pt")
        for lang in LANGS:
            for key, text in i18n.catalog(lang).items():
                self.assertEqual(sorted(re.findall(r"\{(\w+)\}", text)), sorted(re.findall(r"\{(\w+)\}", pt[key])),
                                 f"{lang}:{key}")

    def test_every_text_formats_with_the_variables_the_code_gives_it(self):
        sample = {"rooms": "A, B", "error": "x", "pct": 31, "room": "Office", "kind": "k", "n": 3, "time": "1:00",
                  "repeat": "r", "volume": 20, "problems": "p", "name": "n", "level": "20%", "value": "+1",
                  "device": "d", "available": "a"}
        for lang in ("pt",) + LANGS:
            for key, text in i18n.catalog(lang).items():
                try:
                    text.format(**sample)
                except (KeyError, IndexError, ValueError) as e:
                    self.fail(f"{lang}:{key} does not format: {e}")

    def test_the_french_prompts_follow_french_punctuation(self):
        fr = i18n.catalog("fr")
        for key in ("radio.url_prompt", "radio.name_prompt", "alarm.time_prompt", "alarm.new_time_prompt",
                    "scene.name_prompt", "status.error", "library.play_on", "house.group", "sound.bass", "sound.treble"):
            self.assertRegex(fr[key], r" :(?!\S)|\s:\s|\s:$", f"{key}: {fr[key]}")


def parse_strings(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r'"(\w+)" = "(.*)";', line.strip())
        if line.strip():
            assert match, f"{path}: cannot parse {line!r}"
            out[match.group(1)] = match.group(2)
    return out


class PermissionTexts(unittest.TestCase):
    """The texts macOS shows when it asks for the Microphone and Local Network permissions."""

    def test_every_language_has_its_lproj_with_both_texts(self):
        for lang in i18n.available_languages():
            strings = parse_strings(ROOT / "bundle" / f"{lang}.lproj" / "InfoPlist.strings")
            self.assertEqual(sorted(strings), ["NSLocalNetworkUsageDescription", "NSMicrophoneUsageDescription"], lang)
            for key, text in strings.items():
                self.assertGreater(len(text), 20, f"{lang}:{key}")

    def test_the_texts_differ_between_languages(self):
        texts = {lang: parse_strings(ROOT / "bundle" / f"{lang}.lproj" / "InfoPlist.strings")["NSMicrophoneUsageDescription"]
                 for lang in i18n.available_languages()}
        self.assertEqual(len(set(texts.values())), len(texts))

    def test_the_files_are_valid_for_macos(self):
        for lang in i18n.available_languages():
            path = ROOT / "bundle" / f"{lang}.lproj" / "InfoPlist.strings"
            result = subprocess.run(["plutil", "-lint", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{lang}: {result.stdout}{result.stderr}")


if __name__ == "__main__":
    unittest.main()
