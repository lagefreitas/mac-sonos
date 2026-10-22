"""Menu bar app. All UI work happens on the main thread; the Controller does the rest."""
import signal
import threading
import time

import rumps

from .. import config
from ..controller import CONNECTING, STREAMING, Controller
from ..core import output
from ..sonos import discovery
from . import loginitem, model


class MenuBarApp(rumps.App):
    def __init__(self):
        super().__init__("Sonos", title=model.TITLES["stopped"], quit_button=None)
        self.cfg = config.load()
        self.ctl = Controller()
        self.want_stream = self.cfg["autostart_stream"]
        self.last_attempt = 0.0
        self.discovered = None  # set by the discovery thread, consumed in _tick
        self.known_rooms = []
        self.ticks = 0

        self.status_item = rumps.MenuItem("Parado")
        self.warning_item = rumps.MenuItem("")
        self.warning_item._menuitem.setHidden_(True)
        self.toggle_item = rumps.MenuItem("Ligar", callback=self.on_toggle)
        self.rooms_menu = rumps.MenuItem("Cômodos")
        self.volume_menu = rumps.MenuItem("Volume")
        for v in model.VOLUME_STEPS:
            self.volume_menu.add(rumps.MenuItem(f"{v}%", callback=self.on_volume))
        self.format_menu = rumps.MenuItem("Formato")
        for f in model.FORMAT_CHOICES:
            self.format_menu.add(rumps.MenuItem(f, callback=self.on_format))
        self.autostart_item = rumps.MenuItem("Ligar ao abrir", callback=self.on_autostart)
        self.login_item = rumps.MenuItem("Abrir no login", callback=self.on_login_item)
        self.menu = [
            self.status_item, self.warning_item, self.toggle_item, None,
            self.rooms_menu, self.volume_menu, self.format_menu, None,
            self.autostart_item, self.login_item, rumps.MenuItem("Atualizar cômodos", callback=self.on_refresh),
            rumps.MenuItem("Sair", callback=self.on_quit),
        ]
        self._refresh_checks()
        self._rebuild_rooms()
        self.on_refresh(None)
        signal.signal(signal.SIGTERM, lambda *_: self.on_quit(None))  # runs on the next timer tick
        self.timer = rumps.Timer(self._tick, 1)
        self.timer.start()

    # --- actions --------------------------------------------------------------------------------

    def _active(self):
        return self.ctl.state.status in (CONNECTING, STREAMING)

    def _start(self):
        c = self.cfg
        self.want_stream = True
        self.last_attempt = time.time()
        self.ctl.start(c["rooms"], c["device"], c["format"], c["port"], None, False, c["volume"])

    def _save(self):
        config.save(self.cfg)
        self._refresh_checks()

    def on_toggle(self, _):
        if self._active():
            self.want_stream = False
            self.ctl.stop()
        else:
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

    def on_autostart(self, _):
        self.cfg["autostart_stream"] = not self.cfg["autostart_stream"]
        self._save()

    def on_login_item(self, _):
        current = loginitem.status()
        if current is None:
            rumps.alert("Abrir no login", "Disponível só no Mac Sonos.app (veja build-app.sh).")
            return
        new = loginitem.set_enabled(current != loginitem.ENABLED)
        if new == loginitem.REQUIRES_APPROVAL:
            rumps.alert("Abrir no login", "Aprove o Mac Sonos em Ajustes do Sistema > Geral > "
                                          "Itens de Início de Sessão.")
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
        self.ctl.shutdown()  # stops the stream and restores the Mac output
        rumps.quit_application()

    # --- view -----------------------------------------------------------------------------------

    def _refresh_checks(self):
        for item in self.volume_menu.values():
            item.state = int(item.title == f"{self.cfg['volume']}%")
        for item in self.format_menu.values():
            item.state = int(item.title == self.cfg["format"])
        self.autostart_item.state = int(self.cfg["autostart_stream"])
        self.login_item.state = int(loginitem.status() == loginitem.ENABLED)

    def _rebuild_rooms(self):
        if len(self.rooms_menu):  # rumps' clear() fails on a submenu that was never filled
            self.rooms_menu.clear()
        for name in model.room_choices(self.known_rooms, self.cfg["rooms"]):
            item = rumps.MenuItem(name, callback=self.on_room)
            item.state = int(name in self.cfg["rooms"])
            self.rooms_menu.add(item)

    def _tick(self, _):
        self.ticks += 1
        state = self.ctl.state
        if self.discovered is not None:
            self.known_rooms, self.discovered = self.discovered, None
            self._rebuild_rooms()
        if self.want_stream and self.ticks == 1 and state.status == "stopped":
            self._start()  # first tick: start once the menu is on screen
        if model.should_retry(state.status, self.want_stream, time.time() - self.last_attempt):
            self._start()
        self.title = model.TITLES[state.status]
        self.status_item.title = model.status_text(state)
        self.toggle_item.title = model.toggle_label(state.status)
        warning = model.volume_warning(output.mac_volume()) if state.status == STREAMING and self.ticks % 5 == 0 else None
        if state.status != STREAMING or self.ticks % 5 == 0:
            self.warning_item._menuitem.setHidden_(warning is None)
            self.warning_item.title = warning or ""
