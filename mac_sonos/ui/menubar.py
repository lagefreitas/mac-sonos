"""Menu bar app. All UI work happens on the main thread; the Controller does the rest."""
import logging
import signal
import threading
import time

import rumps

from .. import config, i18n, scenes
from ..controller import CONNECTING, ERROR, STREAMING, Controller
from ..core import capture as capture_mod, output
from ..i18n import t
from ..sonos import art, discovery
from ..sonos.alarms import AlarmManager, parse_time
from ..sonos.library import Library, default_title as library_default_title, valid_stream_url
from ..sonos.monitor import Monitor
from . import loginitem, model
from .mediakeys import MediaKeys

log = logging.getLogger("mac_sonos")


class MenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("Sonos", title=model.TITLES["stopped"], quit_button=None)
        self.cfg = config.load()
        self.ctl = Controller()
        self.want_stream = self.cfg["autostart_stream"]
        self.last_attempt = 0.0
        self.failures = 0           # attempts in a row that ended in an error, for the retry back-off
        self._failed_attempt = None  # the attempt (its last_attempt) already counted in `failures`
        self.discovered = None  # set by the discovery thread, consumed in _tick
        self.known_rooms = []
        self.ticks = 0
        self.groups = []        # latest from the monitor thread (a plain reference swap)
        self.settings = {}      # room -> RoomSettings, same
        self.monitor = Monitor(on_change=self._on_groups)
        self.library = Library(self.monitor.zone_for_group, self.monitor.any_zone,
                               refresh_group=lambda group: self.monitor.command(group, "refresh"))
        self._library_key = None
        self.alarm_manager = AlarmManager(self.monitor.any_zone, self.monitor.zone_for_room)
        self._alarms_key = None
        self._scenes_key = None
        self._rooms_dirty = False   # a scene changed the stream rooms from its own thread: redraw on the next tick
        self.scene_runner = scenes.Runner(_SceneActions(self))
        self.media_keys = MediaKeys(self._on_media_key)
        self.mac_sleep = None       # SleepTimer for the Mac's own stream, while one is running
        self._last_fade = None
        self._sleep_items = {}      # coordinator -> menu item showing the time left
        self._now_key = None
        self._time_items = {}   # coordinator -> menu item showing the position
        self._art_pending = set()
        self._art_ready = False
        self.interrupted = False
        self._house_key = None
        self._house_items = {}  # room -> (menu item, volume label)
        self._static = []       # (menu item, catalog key): retitled when the language changes
        i18n.set_language(self.cfg["language"])

        self.status_item = rumps.MenuItem(t("status.stopped"))
        self.warning_item = rumps.MenuItem("")
        self.warning_item._menuitem.setHidden_(True)
        self.toggle_item = rumps.MenuItem(t("toggle.on"), callback=self.on_toggle)
        self.track_item = rumps.MenuItem("")
        self.track_item._menuitem.setHidden_(True)
        self.now_menu = self._static_item("menu.now_playing")
        self.bar_item = self._static_item("menu.track_in_bar", self.on_track_in_bar)
        self.house_menu = self._static_item("menu.house")
        self.rooms_menu = self._static_item("menu.rooms")
        self.volume_menu = self._static_item("menu.volume")
        for v in model.VOLUME_STEPS:
            self.volume_menu.add(rumps.MenuItem(f"{v}%", callback=self.on_volume))
        self.format_menu = self._static_item("menu.format")
        self.capture_menu = self._static_item("menu.capture")
        self._capture_items = {}
        self.mute_mac_item = None
        self._rebuild_capture()
        for f in model.FORMAT_CHOICES:
            self.format_menu.add(rumps.MenuItem(f, callback=self.on_format))
        self.autostart_item = self._static_item("menu.autostart", self.on_autostart)
        self.login_item = self._static_item("menu.login", self.on_login_item)
        self.library_menu = self._static_item("menu.library")
        self.alarms_menu = self._static_item("menu.alarms")
        self.scenes_menu = self._static_item("menu.scenes")
        self.media_item = self._static_item("menu.media_keys", self.on_media_keys)
        self.language_menu = self._static_item("menu.language")
        self._rebuild_languages()
        self.menu = [
            self.status_item, self.warning_item, self.toggle_item, self.track_item, None,
            self.now_menu, self.house_menu, self.library_menu, self.alarms_menu, self.scenes_menu, self.rooms_menu, self.volume_menu, self.format_menu, self.capture_menu, None,
            self.autostart_item, self.bar_item, self.media_item, self.login_item, *([self.language_menu] if len(self.language_menu) else []),
            self._static_item("menu.refresh", self.on_refresh), self._static_item("menu.quit", self.on_quit),
        ]
        self._refresh_checks()
        self._rebuild_rooms()
        self._rebuild_now([])
        self._rebuild_house()
        self._rebuild_library()
        self._rebuild_alarms()
        self._rebuild_scenes()
        self.on_refresh(None)
        self.monitor.start()
        self.library.start()
        self.alarm_manager.start()
        signal.signal(signal.SIGTERM, lambda *_: self.on_quit(None))  # runs on the next timer tick
        self.timer = rumps.Timer(self._tick, 1)
        self.timer.start()

    def _static_item(self, key, callback=None):
        item = rumps.MenuItem(t(key), callback=callback)
        self._static.append((item, key))
        return item

    # --- language -------------------------------------------------------------------------------

    def _rebuild_languages(self):
        """The language submenu is only worth showing when there is more than one language."""
        codes = model.language_choices(i18n.available_languages())
        if len(self.language_menu):
            self.language_menu.clear()
        if not codes:
            return
        for choice, label in [(i18n.AUTO, t("menu.language_auto"))] + [(c, i18n.language_name(c)) for c in codes]:
            item = rumps.MenuItem(label, callback=lambda _, c=choice: self.on_language(c))
            item.state = int(self.cfg["language"] == choice)
            self.language_menu.add(item)

    def on_language(self, choice):
        self.cfg["language"] = choice
        config.save(self.cfg)
        i18n.set_language(choice)
        for item, key in self._static:
            item.title = t(key)
        self._rebuild_languages()
        self._rebuild_capture()
        self._rebuild_rooms()
        self._now_key = self._house_key = self._library_key = self._alarms_key = self._scenes_key = None  # redrawn next tick

    # --- actions --------------------------------------------------------------------------------

    def _active(self):
        return self.ctl.state.status in (CONNECTING, STREAMING)

    def _start(self):
        c = self.cfg
        self.want_stream = True
        self.last_attempt = time.time()
        self.ctl.start(c["rooms"], c["device"], c["format"], c["port"], None, False, c["volume"],
                       capture=c["capture"], mute_mac=c["mute_mac"])

    def _save(self):
        config.save(self.cfg)
        self._refresh_checks()

    def on_toggle(self, _):
        if self._active() and not self.interrupted:
            self.want_stream = False
            self.ctl.stop()
        else:
            self.failures = 0   # asked for by hand: no back-off left over from earlier failures
            self._start()

    def on_room(self, sender):
        self.cfg["rooms"] = model.toggle_room(self.cfg["rooms"], sender.title)
        self._save()
        self._rebuild_rooms()
        if self._active():
            self._start()

    def on_volume(self, sender):
        self.cfg["volume"] = int(sender.title.rstrip("%"))
        self._save()
        self.ctl.set_volume(self.cfg["volume"])

    def on_format(self, sender):
        self.cfg["format"] = sender.title
        self._save()
        if self._active():
            self._start()

    def _rebuild_capture(self):
        """The two sources (BlackHole, system audio) and the option to mute the Mac; titles follow the language."""
        menu = self.capture_menu
        if len(menu):
            menu.clear()
        self._capture_items = {}
        for kind, key in ((capture_mod.BLACKHOLE, "capture.blackhole"), (capture_mod.TAP, "capture.tap")):
            item = rumps.MenuItem(t(key), callback=lambda _, k=kind: self.on_capture(k))
            item.state = int(self.cfg["capture"] == kind)
            self._capture_items[kind] = item
            menu.add(item)
        menu.add(None)
        self.mute_mac_item = rumps.MenuItem(t("capture.mute_mac"), callback=self.on_mute_mac)
        self.mute_mac_item.state = int(self.cfg["mute_mac"])
        menu.add(self.mute_mac_item)

    def on_capture(self, kind):
        if kind == self.cfg["capture"]:
            return
        self.cfg["capture"] = kind
        self._save()
        if self._active():
            self._start()

    def on_mute_mac(self, _):
        self.cfg["mute_mac"] = not self.cfg["mute_mac"]
        self._save()
        if self._active() and self.cfg["capture"] == capture_mod.TAP:
            self._start()

    def on_autostart(self, _):
        self.cfg["autostart_stream"] = not self.cfg["autostart_stream"]
        self._save()

    def on_track_in_bar(self, _):
        self.cfg["track_in_bar"] = not self.cfg["track_in_bar"]
        self._save()

    def _transport(self, coordinator, key):
        action = {"back": ("seek_by", -model.BACK_SECONDS), "forward": ("seek_by", model.FORWARD_SECONDS)}
        self.monitor.command(coordinator, *action.get(key, (key, None)))

    def on_login_item(self, _):
        current = loginitem.status()
        if current is None:
            rumps.alert(t("login.title"), t("login.only_app"))
            return
        new = loginitem.set_enabled(current != loginitem.ENABLED)
        if new == loginitem.REQUIRES_APPROVAL:
            rumps.alert(t("login.title"), t("login.approve"))
        self._refresh_checks()

    def on_refresh(self, _):
        def work():
            try:
                self.discovered = [s.player_name for s in discovery.discover()]
            except Exception:
                self.discovered = []
        threading.Thread(target=work, daemon=True).start()

    def on_quit(self, _):
        self.timer.stop()
        self.media_keys.stop()
        self._cancel_mac_sleep()
        self.alarm_manager.stop()
        self.library.stop()
        self.monitor.stop()
        self.ctl.shutdown()  # stops the stream and restores the Mac output
        rumps.quit_application()

    # --- view -----------------------------------------------------------------------------------

    def _refresh_checks(self):
        for item in self.volume_menu.values():
            item.state = int(item.title == f"{self.cfg['volume']}%")
        for item in self.format_menu.values():
            item.state = int(item.title == self.cfg["format"])
        self.autostart_item.state = int(self.cfg["autostart_stream"])
        self.bar_item.state = int(self.cfg["track_in_bar"])
        for kind, item in self._capture_items.items():
            item.state = int(self.cfg["capture"] == kind)
        if self.mute_mac_item is not None:
            self.mute_mac_item.state = int(self.cfg["mute_mac"])
        self.login_item.state = int(loginitem.status() == loginitem.ENABLED)
        self.media_item.state = int(self.cfg["media_keys"])

    def _rebuild_rooms(self):
        if len(self.rooms_menu):  # rumps' clear() fails on a submenu that was never filled
            self.rooms_menu.clear()
        for name in model.room_choices(self.known_rooms, self.cfg["rooms"]):
            item = rumps.MenuItem(name, callback=self.on_room)
            item.state = int(name in self.cfg["rooms"])
            self.rooms_menu.add(item)

    def _on_groups(self, groups):  # monitor thread
        self.groups = groups
        self.settings = self.monitor.settings

    def _fetch_art(self, url):
        def work():
            if art.fetch(url):
                self._art_ready = True
            self._art_pending.discard(url)
        if url not in self._art_pending and not art.cached(url):
            self._art_pending.add(url)
            threading.Thread(target=work, daemon=True).start()

    def _rebuild_now(self, active):
        if len(self.now_menu):
            self.now_menu.clear()
        self._time_items, self._sleep_items = {}, {}
        if not active:
            self.now_menu.add(rumps.MenuItem(t("now.nothing")))
            return
        for g in active:
            header = rumps.MenuItem(f"{model.STATE_ICONS[g.state]} {model.group_name(g)}")
            header.add(rumps.MenuItem(model.shorten(model.track_text(g), 48)))
            if g.album:
                header.add(rumps.MenuItem(model.shorten(g.album, 48)))
            if g.duration:
                self._time_items[g.coordinator] = rumps.MenuItem(model.time_text(g))
                header.add(self._time_items[g.coordinator])
            header.add(None)
            for key, label in model.controls(g):
                header.add(rumps.MenuItem(label, callback=lambda _, c=g.coordinator, k=key: self._transport(c, k)))
            header.add(self._sleep_menu(g))
            cover = art.cached(g.art_url)
            if cover:
                header.set_icon(str(cover), dimensions=(18, 18))
            else:
                self._fetch_art(g.art_url)
            self.now_menu.add(header)

    # --- house: volume, mute, grouping ----------------------------------------------------------

    def _room(self, room, action, arg=None):
        self.monitor.room_command(room, action, arg)

    def _party(self, _):
        target = model.party_target(self.groups, self.cfg["rooms"])
        if target:
            self.monitor.command(target.coordinator, "partymode")

    def _rebuild_house(self):
        if len(self.house_menu):
            self.house_menu.clear()
        self._house_items = {}
        d = model.VOLUME_DELTA
        if not self.groups:
            self.house_menu.add(rumps.MenuItem(t("house.searching")))
            return
        for g in self.groups:
            if len(g.members) > 1:
                row = rumps.MenuItem(t("house.group", name=model.group_name(g)))
                row.add(rumps.MenuItem(t("house.volume_up", n=d), callback=lambda _, c=g.coordinator: self.monitor.command(c, "volume_by", d)))
                row.add(rumps.MenuItem(t("house.volume_down", n=d), callback=lambda _, c=g.coordinator: self.monitor.command(c, "volume_by", -d)))
                self.house_menu.add(row)
            for room, group, volume, muted in model.house_rooms([g]):
                text = model.level_text(volume, muted)
                item = rumps.MenuItem(f"{room}  ·  {text}")
                volume_label = rumps.MenuItem(t("house.volume_label", level=text))
                item.add(volume_label)
                item.add(rumps.MenuItem(t("house.volume_up", n=d), callback=lambda _, r=room: self._room(r, "volume_by", d)))
                item.add(rumps.MenuItem(t("house.volume_down", n=d), callback=lambda _, r=room: self._room(r, "volume_by", -d)))
                mute = rumps.MenuItem(t("house.mute"), callback=lambda _, r=room: self._room(r, "mute"))
                mute.state = int(bool(muted))
                item.add(mute)
                item.add(None)
                join = rumps.MenuItem(t("house.join"))
                targets = model.join_targets(self.groups, group)
                for target in targets:
                    join.add(rumps.MenuItem(target, callback=lambda _, r=room, to=target: self._room(r, "join", to)))
                if not targets:
                    join.add(rumps.MenuItem("—"))
                item.add(join)
                if len(group.members) > 1:
                    item.add(rumps.MenuItem(t("house.leave"), callback=lambda _, r=room: self._room(r, "unjoin")))
                sound = rumps.MenuItem(t("sound.title"))
                for sound_label, action, arg, checked in model.sound_items(self.settings.get(room)):
                    entry = rumps.MenuItem(sound_label, callback=None if action is None else
                                           (lambda _, r=room, a=action, v=arg: self._room(r, a, v)))
                    if checked is not None:
                        entry.state = int(checked)
                    sound.add(entry)
                if len(sound):
                    item.add(None)
                    item.add(sound)
                self.house_menu.add(item)
                self._house_items[room] = (item, volume_label)
        self.house_menu.add(None)
        self.house_menu.add(rumps.MenuItem(t("house.party"), callback=self._party))
        self.house_menu.add(rumps.MenuItem(t("house.split"), callback=lambda _: self._room(None, "ungroup_all")))

    def _update_house(self):
        key = model.house_key(self.groups, self.settings)
        if key != self._house_key:
            self._house_key = key
            self._rebuild_house()
            return
        for room, _, volume, muted in model.house_rooms(self.groups):
            if room in self._house_items:
                item, label = self._house_items[room]
                text = model.level_text(volume, muted)
                item.title, label.title = f"{room}  ·  {text}", t("house.volume_label", level=text)

    # --- scenes ---------------------------------------------------------------------------------

    def on_scene_save(self, _):
        name = self._ask(t("scene.name_prompt"), "", title=t("menu.scenes"))
        if not name:
            return
        state = self.ctl.state
        streaming = state.status == STREAMING and not self.interrupted
        scene = scenes.capture(name, self.groups, self.settings, list(state.rooms) if streaming else None,
                               scenes.RADIO_PREFIX + state.url if streaming and state.url else None)
        self.cfg["scenes"] = model.with_scene(self.cfg["scenes"], scene)
        self._save()
        self._scenes_key = None

    def on_scene(self, scene):
        problems = scenes.validate(scene)
        if problems:
            rumps.alert(t("menu.scenes"), t("scene.invalid", problems="; ".join(problems)))
        else:
            self.scene_runner.apply_async(scene, self.groups)

    def on_scene_remove(self, name):
        self.cfg["scenes"] = model.without_scene(self.cfg["scenes"], name)
        self._save()
        self._scenes_key = None

    def _rebuild_scenes(self):
        menu = self.scenes_menu
        if len(menu):
            menu.clear()
        names = model.scene_names(self.cfg["scenes"])
        if not names:
            menu.add(rumps.MenuItem(t("scene.none")))
        for scene in self.cfg["scenes"]:
            menu.add(rumps.MenuItem(model.shorten(scene["name"], 40), callback=lambda _, s=scene: self.on_scene(s)))
        menu.add(None)
        menu.add(rumps.MenuItem(t("scene.save"), callback=self.on_scene_save))
        if names:
            remove = rumps.MenuItem(t("scene.remove"))
            for name in names:
                remove.add(rumps.MenuItem(model.shorten(name, 40), callback=lambda _, n=name: self.on_scene_remove(n)))
            menu.add(remove)

    def _update_scenes(self):
        key = repr(self.cfg["scenes"])
        if key != self._scenes_key:
            self._scenes_key = key
            self._rebuild_scenes()
        if self._rooms_dirty:
            self._rooms_dirty = False
            self._rebuild_rooms()

    # --- media keys -----------------------------------------------------------------------------

    def on_media_keys(self, _):
        self.cfg["media_keys"] = not self.cfg["media_keys"]
        self._save()
        if self.cfg["media_keys"]:
            if not self.media_keys.start(prompt=True):
                rumps.alert(t("menu.media_keys"), t("media.permission"))
        else:
            self.media_keys.stop()

    def _on_media_key(self, key, repeat):
        """Called by the event tap on the main thread: True swallows the key."""
        state = self.ctl.state
        streaming = state.status == STREAMING and not self.interrupted
        stream_group = next((g for g in self.groups if state.rooms and state.rooms[0] in g.members), None)
        target = model.play_target(self.groups, self.cfg["rooms"], self.cfg["play_target"])
        action = model.decide_key(key, streaming, stream_group, target)
        if action is None:
            return False
        if repeat and key not in model.VOLUME_KEYS:
            return True  # a held key must not fire again, and must not leak to the system either
        kind = action[0]
        if kind == "volume_by":
            self.monitor.command(action[1], "volume_by", action[2])
        elif kind == "mute_group":
            group = next(g for g in self.groups if g.coordinator == action[1])
            all_muted = bool(group.levels) and all(muted for _, _, muted in group.levels)
            for room in group.members:
                self.monitor.room_command(room, "mute", not all_muted)
        elif kind == "transport":
            self.monitor.command(action[1], action[2])
        return True

    def _tick_media_keys(self):
        """Retry quietly while the option is on but macOS has not granted the permission yet."""
        if self.cfg["media_keys"] and not self.media_keys.active and (self.ticks == 1 or self.ticks % 5 == 0):
            self.media_keys.start(prompt=False)

    # --- sleep timer ----------------------------------------------------------------------------

    def _sleep_left(self, group):
        if model.is_mac_stream(group):
            return self.mac_sleep.remaining(time.time()) if self.mac_sleep else None
        return group.sleep_left(time.time())

    def _sleep_menu(self, group):
        menu = rumps.MenuItem(t("sleep.title"))
        left = self._sleep_left(group)
        if left is not None:
            label = rumps.MenuItem(model.sleep_text(left))
            menu.add(label)
            menu.add(None)
            self._sleep_items[group.coordinator] = label
        for n in model.SLEEP_MINUTES:
            menu.add(rumps.MenuItem(t("sleep.minutes", n=n), callback=lambda _, g=group, n=n: self._sleep(g, n)))
        if left is not None:
            menu.add(None)
            menu.add(rumps.MenuItem(t("sleep.cancel"), callback=lambda _, g=group: self._sleep_cancel(g)))
        return menu

    def _sleep(self, group, minutes):
        if model.is_mac_stream(group):  # our own stream: fade out, stop cleanly, restore the volume
            self._cancel_mac_sleep()
            base = next((v for _, v, muted in group.levels if v is not None and not muted), self.cfg["volume"])
            self.mac_sleep = model.SleepTimer(minutes, base, time.time())
            self._now_key = None
        else:
            self.monitor.command(group.coordinator, "sleep", minutes)

    def _sleep_cancel(self, group):
        if model.is_mac_stream(group):
            self._cancel_mac_sleep()
            self._now_key = None
        else:
            self.monitor.command(group.coordinator, "sleep_cancel")

    def _cancel_mac_sleep(self):
        if self.mac_sleep and self._last_fade is not None:  # in the middle of the fade: undo it
            self.ctl.set_volume(self.mac_sleep.base_volume)
        self.mac_sleep, self._last_fade = None, None

    def _tick_mac_sleep(self):
        timer, now = self.mac_sleep, time.time()
        if timer is None:
            return
        if self.ctl.state.status != STREAMING and now - timer.started > 5:  # the stream went away: no timer
            self.mac_sleep, self._last_fade, self._now_key = None, None, None
        elif timer.expired(now):
            log.info("sleep timer: stopping the stream")
            self.mac_sleep, self._last_fade, self._now_key = None, None, None
            self.want_stream = False
            self.ctl.stop(volume=timer.base_volume)
        else:
            volume = timer.fade_volume(now)
            if volume is not None and volume != self._last_fade:
                self._last_fade = volume
                self.ctl.set_volume(volume)

    # --- alarms ---------------------------------------------------------------------------------

    def on_alarm_new(self, _):
        room = model.target_room_name(self.groups, self.cfg["rooms"], self.cfg["play_target"])
        text = self._ask(t("alarm.time_prompt", room=room), "07:00", title=t("menu.alarms"))
        if text is None:
            return
        value = parse_time(text)
        if value is None:
            rumps.alert(t("menu.alarms"), t("alarm.invalid_time"))
        elif room:
            self.alarm_manager.add(room, value)

    def on_alarm_time(self, info):
        from ..sonos.alarms import format_time
        text = self._ask(t("alarm.new_time_prompt"), format_time(info.time), title=t("menu.alarms"))
        if text is None:
            return
        value = parse_time(text)
        if value is None:
            rumps.alert(t("menu.alarms"), t("alarm.invalid_time"))
        else:
            self.alarm_manager.change(info.alarm_id, start_time=value)

    def _rebuild_alarms(self):
        menu, manager, d = self.alarms_menu, self.alarm_manager, model.ALARM_VOLUME_DELTA
        if len(menu):
            menu.clear()
        if not manager.alarms:
            menu.add(rumps.MenuItem(t("alarm.none")))
        for alarm in manager.alarms:
            item = rumps.MenuItem(model.alarm_title(alarm))
            item.state = int(alarm.enabled)
            enabled = rumps.MenuItem(t("alarm.enabled"), callback=lambda _, a=alarm: manager.change(a.alarm_id, enabled=not a.enabled))
            enabled.state = int(alarm.enabled)
            item.add(enabled)
            item.add(rumps.MenuItem(t("alarm.set_time"), callback=lambda _, a=alarm: self.on_alarm_time(a)))
            repeat = rumps.MenuItem(t("alarm.repeat"))
            for choice in model.RECURRENCE_CHOICES:
                entry = rumps.MenuItem(model.recurrence_label(choice),
                                       callback=lambda _, a=alarm, c=choice: manager.change(a.alarm_id, recurrence=c))
                entry.state = int(alarm.recurrence == choice)
                repeat.add(entry)
            item.add(repeat)
            item.add(None)
            item.add(rumps.MenuItem(t("alarm.volume", volume=alarm.volume)))
            item.add(rumps.MenuItem(t("alarm.volume_up", n=d), callback=lambda _, a=alarm: manager.change(a.alarm_id, volume=min(100, a.volume + d))))
            item.add(rumps.MenuItem(t("alarm.volume_down", n=d), callback=lambda _, a=alarm: manager.change(a.alarm_id, volume=max(0, a.volume - d))))
            item.add(None)
            item.add(rumps.MenuItem(t("alarm.remove"), callback=lambda _, a=alarm: manager.remove(a.alarm_id)))
            menu.add(item)
        menu.add(None)
        menu.add(rumps.MenuItem(t("alarm.new"), callback=self.on_alarm_new))

    def _update_alarms(self):
        key = self.alarm_manager.version
        if key != self._alarms_key:
            self._alarms_key = key
            self._rebuild_alarms()

    # --- library: favorites, playlists, queue, radio URLs ---------------------------------------

    def _target(self):
        return model.play_target(self.groups, self.cfg["rooms"], self.cfg["play_target"])

    def _on_target(self, room):
        self.cfg["play_target"] = room
        self._save()
        self._library_key = None

    def _play(self, action, *args):
        """Run a Library action on the target group; nothing happens if there is no group yet."""
        target = self._target()
        if target:
            getattr(self.library, action)(target.coordinator, *args)

    def _ask(self, message, default="", title=None):
        """A one-line prompt; the text, or None if cancelled."""
        window = rumps.Window(message=message, title=title or t("menu.radios"), default_text=default,
                              ok=t("dialog.ok"), cancel=t("dialog.cancel"), dimensions=(380, 24))
        response = window.run()
        return response.text.strip() if response.clicked else None

    def _ask_url(self):
        url = self._ask(t("radio.url_prompt"), "https://")
        if url is None:
            return None
        if not valid_stream_url(url):
            rumps.alert(t("menu.radios"), t("radio.invalid_url"))
            return None
        return url

    def on_play_url(self, _):
        url = self._ask_url()
        if url:
            self._play("play_url", url)

    def on_add_radio(self, _):
        url = self._ask_url()
        if url:
            title = self._ask(t("radio.name_prompt"), library_default_title(url))
            if title is not None:
                self.cfg["radio_urls"] = model.add_radio(self.cfg["radio_urls"], title, url)
                self._save()
                self._library_key = None

    def on_remove_radio(self, url):
        self.cfg["radio_urls"] = model.remove_radio(self.cfg["radio_urls"], url)
        self._save()
        self._library_key = None

    def _library_state(self):
        target = self._target()
        room = model.target_room_name(self.groups, self.cfg["rooms"], self.cfg["play_target"])
        queue = self.library.queues.get(target.coordinator, ()) if target else ()
        total = self.library.queue_totals.get(target.coordinator) if target else None
        position = target.queue_position if target else 0
        return target, room, queue, total, position

    def _rebuild_library(self):
        target, room, entries, total, position = self._library_state()
        menu = self.library_menu
        if len(menu):
            menu.clear()
        # where things play
        where = rumps.MenuItem(t("library.play_on", room=room))
        for name in sorted({m for g in self.groups for m in g.members}):
            item = rumps.MenuItem(name, callback=lambda _, r=name: self._on_target(r))
            item.state = int(name == room)
            where.add(item)
        if not len(where):
            where.add(rumps.MenuItem(t("library.loading")))
        menu.add(where)
        menu.add(None)
        # favorites, by kind
        favorites = rumps.MenuItem(t("menu.favorites"))
        for title, items in model.favorite_sections(self.library.favorites):
            section = rumps.MenuItem(title)
            for fav in items:
                section.add(rumps.MenuItem(model.shorten(fav.title, 44), callback=lambda _, f=fav: self._play("play_favorite", f)))
            favorites.add(section)
        if not len(favorites):
            favorites.add(rumps.MenuItem(t("library.empty")))
        menu.add(favorites)
        playlists = rumps.MenuItem(t("menu.playlists"))
        for playlist in self.library.playlists:
            playlists.add(rumps.MenuItem(model.shorten(playlist.title, 44), callback=lambda _, p=playlist: self._play("play_playlist", p)))
        if not len(playlists):
            playlists.add(rumps.MenuItem(t("library.empty")))
        menu.add(playlists)
        # queue of the target group
        queue = rumps.MenuItem(t("menu.queue"))
        shown, before, after = model.queue_window(entries, position - 1, total=total)
        if before:
            queue.add(rumps.MenuItem(t("queue.before", n=before)))
        for entry in shown:
            row = rumps.MenuItem(model.queue_entry_text(entry, entry.index + 1 == position))
            row.add(rumps.MenuItem(t("queue.play"), callback=lambda _, i=entry.index: self._play("play_queue_index", i)))
            row.add(rumps.MenuItem(t("queue.remove"), callback=lambda _, i=entry.index: self._play("remove_from_queue", i)))
            queue.add(row)
        if after:
            queue.add(rumps.MenuItem(t("queue.after", n=after)))
        if entries:
            queue.add(None)
            queue.add(rumps.MenuItem(t("queue.clear"), callback=lambda _: self._play("clear_queue")))
        else:
            queue.add(rumps.MenuItem(t("queue.empty")))
        menu.add(queue)
        menu.add(None)
        # radio URLs
        radios = rumps.MenuItem(t("menu.radios"))
        for station in self.cfg["radio_urls"]:
            radios.add(rumps.MenuItem(model.shorten(station["title"], 44),
                                      callback=lambda _, s=station: self._play("play_url", s["url"], s["title"])))
        if len(radios):
            radios.add(None)
        radios.add(rumps.MenuItem(t("radio.play_url"), callback=self.on_play_url))
        radios.add(rumps.MenuItem(t("radio.add"), callback=self.on_add_radio))
        if self.cfg["radio_urls"]:
            remove = rumps.MenuItem(t("radio.remove"))
            for station in self.cfg["radio_urls"]:
                remove.add(rumps.MenuItem(model.shorten(station["title"], 44), callback=lambda _, u=station["url"]: self.on_remove_radio(u)))
            radios.add(remove)
        menu.add(radios)

    def _update_library(self):
        target, room, entries, total, position = self._library_state()
        if target:
            self.library.watch(target.coordinator, position)
        key = (self.library.version, room, position, target.coordinator if target else None,
               tuple(sorted({m for g in self.groups for m in g.members})), repr(self.cfg["radio_urls"]))
        if key != self._library_key:
            self._library_key = key
            self._rebuild_library()

    def _update_now(self):
        groups = self.groups
        active = [g for g in groups if model.is_active(g)]
        key = (model.structure_key(groups), self.mac_sleep is not None)
        if key != self._now_key or self._art_ready:
            self._now_key, self._art_ready = key, False
            self._rebuild_now(active)
        for g in active:
            item = self._time_items.get(g.coordinator)
            if item:
                item.title = model.time_text(g)
            left = self._sleep_left(g)
            if left is not None and g.coordinator in self._sleep_items:
                self._sleep_items[g.coordinator].title = model.sleep_text(left)
        primary = model.primary_group(groups, self.cfg["rooms"])
        show = primary is not None and not model.is_mac_stream(primary)
        self.track_item._menuitem.setHidden_(not show)
        if show:
            self.track_item.title = f"♪ {model.shorten(model.track_text(primary), 40)} · {model.group_name(primary)}"
        return primary

    def _count_failure(self, state):
        if state.status == STREAMING:
            self.failures = 0
        elif state.status == ERROR and self._failed_attempt != self.last_attempt:
            self.failures, self._failed_attempt = self.failures + 1, self.last_attempt

    def _rooms_back(self, state):
        """The rooms the last attempt could not find are seen by the monitor again: worth trying at once."""
        present = {m for g in self.groups for m in g.members}
        return state.error_key == "error.room_not_found" and bool(state.rooms) and set(state.rooms) <= present

    def _tick(self, _):
        try:
            self._tick_once()
        except Exception:  # rumps would only print this to a stderr nobody reads
            log.exception("menu refresh failed")

    def _tick_once(self):
        self.ticks += 1
        state = self.ctl.state
        if self.discovered is not None:
            self.known_rooms, self.discovered = self.discovered, None
            self._rebuild_rooms()
        if self.want_stream and self.ticks == 1 and state.status == "stopped":
            self._start()  # first tick: start once the menu is on screen
        self._count_failure(state)
        if model.should_retry(state.status, self.want_stream, time.time() - self.last_attempt, self.failures,
                              state.error_key, self._rooms_back(state)):
            self._start()
        interrupted = model.is_interrupted(state, self.groups, time.time())
        if interrupted != self.interrupted:
            log.info("stream %s", "interrupted on the Sonos side" if interrupted else "back on the Sonos side")
            self.interrupted = interrupted
        primary = self._update_now()
        self._update_house()
        self._update_library()
        self._update_alarms()
        self._update_scenes()
        self._tick_media_keys()
        self._tick_mac_sleep()
        icon = model.TITLES[model.INTERRUPTED if interrupted else state.status]
        self.title = model.bar_title(icon, primary) if self.cfg["track_in_bar"] else icon
        self.status_item.title = model.status_text(state, interrupted)
        self.toggle_item.title = model.toggle_label(state.status, interrupted)
        # the Mac's volume is the signal level only for BlackHole; the system audio tap ignores it
        blackhole = self.cfg["capture"] == capture_mod.BLACKHOLE
        warning = (model.volume_warning(output.mac_volume())
                   if state.status == STREAMING and blackhole and self.ticks % 5 == 0 else None)
        if state.status != STREAMING or self.ticks % 5 == 0:
            self.warning_item._menuitem.setHidden_(warning is None)
            self.warning_item.title = warning or ""


class _SceneActions:
    """What a scene's steps do in this app. They run on the scene's own thread, so they only queue work and never
    touch the menu (the stream rooms are redrawn by the next tick)."""

    def __init__(self, app):
        self.app = app

    def room(self, room, action, arg):
        self.app.monitor.room_command(room, action, arg)

    def groups(self):
        return self.app.groups

    def streaming(self):
        return self.app.ctl.state.status == STREAMING

    def stream_on(self, rooms, volume):
        app = self.app
        app.cfg["rooms"] = list(rooms)
        config.save(app.cfg)
        app._rooms_dirty = True
        app.want_stream = True
        app.last_attempt = time.time()
        c = app.cfg
        app.ctl.start(rooms, c["device"], c["format"], c["port"], None, False, c["volume"] if volume is None else volume,
                      capture=c["capture"], mute_mac=c["mute_mac"])

    def stream_off(self):
        self.app.want_stream = False
        self.app.ctl.stop()

    def tv(self, room):
        self.app.monitor.room_command(room, "tv")

    def _coordinator(self, room):
        return next((g.coordinator for g in self.app.groups if room in g.members), None)

    def favorite(self, room, title):
        fav = next((f for f in self.app.library.favorites if f.title.casefold() == title.casefold()), None)
        coordinator = self._coordinator(room)
        if fav is None or coordinator is None:
            log.warning("scene: favorite %r or room %r not found", title, room)
            return
        self.app.library.play_favorite(coordinator, fav)

    def url(self, room, url, title):
        coordinator = self._coordinator(room)
        if coordinator is None:
            log.warning("scene: room %r not found", room)
            return
        self.app.library.play_url(coordinator, url, title)
