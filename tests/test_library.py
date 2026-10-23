"""Favorites, playlists, queue and radio URLs without hardware: python -m unittest discover tests"""
import unittest
from types import SimpleNamespace
from unittest import mock

from mac_sonos import i18n
from mac_sonos.sonos import library as lib
from mac_sonos.sonos.library import Favorite, Library, PLAYLIST, QueueEntry, RADIO, TRACK
from mac_sonos.sonos.monitor import GroupInfo, PLAYING
from mac_sonos.ui import model

i18n.set_language("pt")


# Classes named like SoCo's: classify() goes by the class name of a favorite's reference.
class DidlAudioBroadcast: pass
class DidlMusicTrack: pass
class DidlPlaylistContainer: pass
class DidlMusicAlbum: pass
class DidlMusicArtist: pass
class DidlContainer: pass


def resources(uri):
    return [SimpleNamespace(uri=uri)] if uri else []


def fav_item(title, ref_class, uri, ref_uri="same", meta="<DIDL/>"):
    ref = ref_class()
    ref.resources = resources(uri if ref_uri == "same" else ref_uri)
    return SimpleNamespace(title=title, reference=ref, resources=resources(uri), resource_meta_data=meta)


class Classify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(lib.classify(fav_item("r", DidlAudioBroadcast, "x-sonosapi-stream:s1?sid=1")), RADIO)
        self.assertEqual(lib.classify(fav_item("r", DidlContainer, "x-rincon-mp3radio://h/s")), RADIO)
        self.assertEqual(lib.classify(fav_item("t", DidlMusicTrack, "x-sonos-spotify:track")), TRACK)
        self.assertEqual(lib.classify(fav_item("p", DidlPlaylistContainer, "x-rincon-cpcontainer:1")), PLAYLIST)
        self.assertEqual(lib.classify(fav_item("a", DidlMusicAlbum, "x-rincon-cpcontainer:2")), PLAYLIST)

    def test_what_cannot_be_played_is_left_out(self):
        self.assertIsNone(lib.classify(fav_item("hub", DidlContainer, None)))
        self.assertIsNone(lib.classify(fav_item("artist", DidlMusicArtist, None)))
        self.assertIsNone(lib.classify(fav_item("p", DidlPlaylistContainer, "x-rincon-cpcontainer:1", ref_uri=None)))
        self.assertIsNone(lib.classify(SimpleNamespace(title="x", reference=None, resources=resources("u"))))

    def test_favorites_from_keeps_order_and_drops_the_rest(self):
        items = [fav_item("A", DidlAudioBroadcast, "x-sonosapi-stream:1"), fav_item("hub", DidlContainer, None),
                 fav_item("B", DidlMusicTrack, "x-sonos-spotify:t")]
        self.assertEqual([(f.title, f.kind) for f in lib.favorites_from(items)], [("A", RADIO), ("B", TRACK)])

    def test_urls(self):
        for ok in ("http://a.com/s", "https://radio.example/stream.mp3", " https://x.org/a "):
            self.assertTrue(lib.valid_stream_url(ok), ok)
        for bad in ("", None, "ftp://a.com/s", "radio.com/stream", "http://", "javascript:alert(1)"):
            self.assertFalse(lib.valid_stream_url(bad), bad)
        self.assertEqual(lib.default_title(" http://ice1.somafm.com/x "), "ice1.somafm.com")


class FakeZone:
    def __init__(self, queue_titles=()):
        self.calls, self.queue = [], [SimpleNamespace(title=t, creator="Band") for t in queue_titles]

    def play_uri(self, uri, **kw):
        self.calls.append(("play_uri", uri, kw))

    def clear_queue(self):
        self.calls.append(("clear_queue",))
        self.queue = []

    def add_to_queue(self, item):
        self.calls.append(("add_to_queue", item))
        self.queue += [SimpleNamespace(title="added", creator="")]
        return 1

    def play_from_queue(self, index):
        self.calls.append(("play_from_queue", index))

    def remove_from_queue(self, index):
        self.calls.append(("remove_from_queue", index))

    def get_queue(self, start=0, max_items=100):
        page = QueuePage(self.queue[start:start + max_items])
        page.total_matches = len(self.queue)
        return page


class QueuePage(list):
    """What SoCo's get_queue returns: a page of the queue that knows the length of the whole."""
    total_matches = 0


class Playing(unittest.TestCase):
    def test_radio_plays_by_uri_with_the_favorites_metadata(self):
        item = fav_item("LoungeFM", DidlAudioBroadcast, "x-sonosapi-stream:s1", meta="<meta/>")
        z = FakeZone()
        lib.play_favorite(z, Favorite("LoungeFM", RADIO, item))
        self.assertEqual(z.calls, [("play_uri", "x-sonosapi-stream:s1", {"meta": "<meta/>", "title": "LoungeFM"})])

    def test_track_and_playlist_replace_the_queue_and_start_it(self):
        for kind, cls in ((TRACK, DidlMusicTrack), (PLAYLIST, DidlPlaylistContainer)):
            item = fav_item("x", cls, "x-sonos-spotify:z")
            z = FakeZone(["old"])
            lib.play_favorite(z, Favorite("x", kind, item))
            self.assertEqual([c[0] for c in z.calls], ["clear_queue", "add_to_queue", "play_from_queue"])
            self.assertIs(z.calls[1][1], item.reference)
            self.assertEqual(z.calls[2], ("play_from_queue", 0))  # add_to_queue is 1-based, play_from_queue 0-based

    def test_sonos_playlist(self):
        z, playlist = FakeZone(), object()
        lib.play_playlist(z, playlist)
        self.assertEqual(z.calls, [("clear_queue",), ("add_to_queue", playlist), ("play_from_queue", 0)])

    def test_play_url_is_a_radio_stream_with_a_default_title(self):
        z = FakeZone()
        lib.play_url(z, " http://ice1.somafm.com/g ")
        self.assertEqual(z.calls, [("play_uri", "http://ice1.somafm.com/g", {"title": "ice1.somafm.com", "force_radio": True})])
        z = FakeZone()
        lib.play_url(z, "http://a.b/c", "My radio")
        self.assertEqual(z.calls[0][2]["title"], "My radio")


class LibraryJobs(unittest.TestCase):
    def setUp(self):
        self.zone = FakeZone(["Song A", "Song B"])
        self.zone.music_library = SimpleNamespace(get_sonos_favorites=lambda complete_result=True: [
            fav_item("Radio", DidlAudioBroadcast, "x-sonosapi-stream:1"), fav_item("hub", DidlContainer, None)])
        self.zone.get_sonos_playlists = lambda complete_result=True: [SimpleNamespace(title="Mix")]
        self.refreshed, self.changes = [], []
        self.lib = Library(lambda name: self.zone if name == "Office" else None, lambda: self.zone,
                           on_change=lambda: self.changes.append(1), refresh_group=self.refreshed.append)

    def test_library_loads_once_and_only_bumps_on_change(self):
        self.lib._step(None, 1000.0)
        self.assertEqual([f.title for f in self.lib.favorites], ["Radio"])
        self.assertEqual([p.title for p in self.lib.playlists], ["Mix"])
        self.assertEqual(self.lib.version, 1)
        self.lib._step(None, 1000.0 + lib.LIBRARY_EVERY + 1)  # reload finds the same thing
        self.assertEqual(self.lib.version, 1)
        self.zone.get_sonos_playlists = lambda complete_result=True: [SimpleNamespace(title="Mix"), SimpleNamespace(title="New")]
        self.lib._step(None, 2000.0 + 2 * lib.LIBRARY_EVERY)
        self.assertEqual(self.lib.version, 2)

    def test_no_zone_yet_is_not_an_error(self):
        empty = Library(lambda n: None, lambda: None)
        empty._step(None, 1000.0)
        empty._step(("queue", "Office"), 1000.0)
        self.assertEqual(empty.favorites, [])

    def test_queue_is_loaded_for_the_watched_group_and_refreshed_periodically(self):
        self.lib.watch("Office")
        self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        self.assertEqual(self.lib.queues["Office"], (QueueEntry(0, "Song A", "Band"), QueueEntry(1, "Song B", "Band")))
        self.zone.queue.append(SimpleNamespace(title="Song C", creator=""))
        self.lib._step(None, 1000.0 + 1)  # not due yet
        self.assertEqual(len(self.lib.queues["Office"]), 2)
        self.lib._step(None, 1000.0 + lib.QUEUE_EVERY + 1)
        self.assertEqual(len(self.lib.queues["Office"]), 3)

    def test_favorites_are_in_place_when_the_change_is_announced(self):
        seen = []
        self.lib._on_change = lambda: seen.append([f.title for f in self.lib.favorites])
        self.lib._step(None, 1000.0)
        self.assertEqual(seen, [["Radio"]])

    def test_a_long_queue_is_read_around_the_current_track(self):
        self.zone.queue = [SimpleNamespace(title=f"T{i}", creator="") for i in range(300)]
        self.lib.watch("Office", 150)
        self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        entries = self.lib.queues["Office"]
        self.assertEqual((entries[0].index, entries[-1].index, len(entries)), (139, 238, lib.QUEUE_LIMIT))
        self.assertEqual(entries[10], QueueEntry(149, "T149", ""))   # the current track, 1-based 150
        self.assertEqual(self.lib.queue_totals["Office"], 300)

    def test_the_window_is_read_again_only_when_the_track_leaves_it(self):
        self.zone.queue = [SimpleNamespace(title=f"T{i}", creator="") for i in range(300)]
        self.lib.watch("Office", 1)
        self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        self.lib.watch("Office", 30)    # still well inside the first 100
        self.assertEqual(self.lib._jobs.qsize(), 0)
        self.lib.watch("Office", 80)    # too close to the end of what was read
        self.lib.watch("Office", 81)    # a second tick before the job ran: no second job
        self.assertEqual(self.lib._jobs.qsize(), 1)
        self.lib._step(self.lib._jobs.get_nowait(), 1001.0)
        self.assertEqual(self.lib.queues["Office"][0].index, 70)
        self.lib.watch("Office", 299)   # near the end of the queue: covered once the last entry is in
        self.lib._step(self.lib._jobs.get_nowait(), 1002.0)
        self.lib.watch("Office", 300)
        self.assertEqual(self.lib._jobs.qsize(), 0)

    def test_watching_the_same_group_twice_queues_one_job(self):
        self.lib.watch("Office")
        self.lib.watch("Office")
        self.assertEqual(self.lib._jobs.qsize(), 1)

    def test_actions_run_on_the_group_then_refresh_it_and_its_queue(self):
        self.lib.remove_from_queue("Office", 1)
        self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        self.assertEqual(self.zone.calls, [("remove_from_queue", 1)])
        self.assertEqual(self.refreshed, ["Office"])
        self.assertIn("Office", self.lib.queues)

    def test_every_action_maps_to_the_right_zone_call(self):
        fav = Favorite("x", RADIO, fav_item("x", DidlAudioBroadcast, "x-sonosapi-stream:1"))
        for call, expected in ((lambda: self.lib.play_queue_index("Office", 3), "play_from_queue"),
                               (lambda: self.lib.clear_queue("Office"), "clear_queue"),
                               (lambda: self.lib.play_favorite("Office", fav), "play_uri"),
                               (lambda: self.lib.play_url("Office", "http://a.b/c"), "play_uri"),
                               (lambda: self.lib.play_playlist("Office", object()), "clear_queue")):
            self.zone.calls.clear()
            call()
            self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
            self.assertEqual(self.zone.calls[0][0], expected)

    def test_unknown_group_is_logged_not_raised_and_a_failing_action_still_refreshes(self):
        self.lib.clear_queue("Nowhere")
        with self.assertLogs("mac_sonos", level="WARNING"):
            self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        self.zone.remove_from_queue = mock.Mock(side_effect=OSError("down"))
        self.lib.remove_from_queue("Office", 0)
        with self.assertRaises(OSError):
            self.lib._step(self.lib._jobs.get_nowait(), 1000.0)
        self.assertEqual(self.refreshed, ["Office"])

    def test_worker_thread_starts_and_stops(self):
        self.lib.start()
        self.lib.reload()
        self.lib.stop()
        self.assertFalse(self.lib._thread.is_alive())


def group(coordinator, members, **kw):
    return GroupInfo(coordinator, tuple(members), PLAYING, **kw)


class LibraryModel(unittest.TestCase):
    def test_play_target(self):
        a, b = group("Office", ["Office"]), group("TV Room", ["TV Room", "Sala"])
        self.assertIs(model.play_target([a, b], ["Office"], "Sala"), b)       # the chosen room's group
        self.assertIs(model.play_target([a, b], ["Office"], None), a)         # the Mac's first room
        self.assertIs(model.play_target([a, b], ["Gone"], "Gone too"), a)     # fall back to the first group
        self.assertIsNone(model.play_target([], ["Office"], None))

    def test_target_room_name(self):
        a, b = group("Office", ["Office"]), group("TV Room", ["TV Room"])
        self.assertEqual(model.target_room_name([a, b], ["Office"], "TV Room"), "TV Room")
        self.assertEqual(model.target_room_name([a, b], ["Office"], "Gone"), "Office")
        self.assertEqual(model.target_room_name([a, b], ["Gone"], None), "Office")
        self.assertEqual(model.target_room_name([], ["Office"], None), "")

    def test_favorite_sections_are_ordered_titled_and_skip_empty_kinds(self):
        favs = [Favorite("p", PLAYLIST), Favorite("r1", RADIO), Favorite("r2", RADIO)]
        self.assertEqual([(t, [f.title for f in items]) for t, items in model.favorite_sections(favs)],
                         [("Rádios (2)", ["r1", "r2"]), ("Playlists (1)", ["p"])])
        self.assertEqual(model.favorite_sections([]), [])

    def test_queue_window(self):
        entries = [QueueEntry(i, f"s{i}") for i in range(100)]
        self.assertEqual(model.queue_window(entries[:10], 3), (entries[:10], 0, 0))
        shown, before, after = model.queue_window(entries, 0)
        self.assertEqual((shown[0].index, len(shown), before, after), (0, 40, 0, 60))
        shown, before, after = model.queue_window(entries, 50)
        self.assertIn(50, [e.index for e in shown])
        self.assertEqual(before + len(shown) + after, 100)
        shown, before, after = model.queue_window(entries, 99)
        self.assertEqual((shown[-1].index, after), (99, 0))
        shown, _, _ = model.queue_window(entries, -1)  # no current track
        self.assertEqual(shown[0].index, 0)

    def test_queue_window_of_a_part_of_the_queue(self):
        part = [QueueEntry(i, f"t{i}") for i in range(139, 239)]   # what the library read, of 300
        shown, before, after = model.queue_window(part, 149, total=300)
        self.assertEqual(shown[0].index, 144)                     # size // 8 = 5 before the current one
        self.assertEqual((len(shown), before, after), (40, 144, 300 - 184))
        shown, before, after = model.queue_window(part[:30], 149, total=300)
        self.assertEqual((len(shown), before, after), (30, 139, 300 - 169))

    def test_queue_entry_text(self):
        self.assertEqual(model.queue_entry_text(QueueEntry(0, "Bubbly", "Eliza"), False), "1. Bubbly – Eliza")
        self.assertEqual(model.queue_entry_text(QueueEntry(2, "Solo"), True), "▶ 3. Solo")

    def test_saved_radios(self):
        saved = model.add_radio([], " My ", " http://a.b/c ")
        self.assertEqual(saved, [{"title": "My", "url": "http://a.b/c"}])
        self.assertEqual(model.add_radio(saved, "", "http://x.y/z")[1], {"title": "x.y", "url": "http://x.y/z"})
        renamed = model.add_radio(saved, "Renamed", "http://a.b/c")
        self.assertEqual(renamed, [{"title": "Renamed", "url": "http://a.b/c"}])  # same URL: replaced, no duplicate
        self.assertEqual(model.remove_radio(saved, "http://a.b/c"), [])
        self.assertEqual(saved, [{"title": "My", "url": "http://a.b/c"}])  # inputs untouched


if __name__ == "__main__":
    unittest.main()
