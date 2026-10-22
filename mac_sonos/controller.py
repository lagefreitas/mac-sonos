"""UI-independent controller: state plus commands, run serially on a worker thread.

The CLI, the menu bar app and any future panel are clients of this class. Commands return at once;
progress shows up in `state` (and through `on_change`), so a UI thread never blocks on the network.
"""
import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from .core import capture as capture_mod, output, session as session_mod
from .sonos import discovery

log = logging.getLogger("mac_sonos")

STOPPED, CONNECTING, STREAMING, ERROR = "stopped", "connecting", "streaming", "error"


@dataclass
class State:
    status: str = STOPPED
    rooms: list = field(default_factory=list)
    url: str = None
    error: str = None
    started_at: float = 0.0  # when the stream began (time.time())
    error_key: str = None    # catalog key and variables of the error, when it has them (see i18n)
    error_vars: dict = None


class Controller:
    def __init__(self, on_change=None):
        self.state = State()
        self._on_change = on_change
        self._cond = threading.Condition()
        self._queue = queue.Queue()
        self._session = None
        self._stack = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="controller")
        self._thread.start()

    # --- commands (thread-safe, non-blocking) ---------------------------------------------------

    def start(self, rooms, device="BlackHole 2ch", fmt="mp3", port=8000, bitrate=None, lowlat=False,
              volume=None, capture=capture_mod.BLACKHOLE, mute_mac=True):
        """`capture`: "blackhole" (the `device`) or "tap" (system audio, no BlackHole; `mute_mac` silences the Mac)."""
        self._queue.put(("start", dict(rooms=list(rooms), device=device, fmt=fmt, port=port, bitrate=bitrate,
                                       lowlat=lowlat, volume=volume, capture=capture, mute_mac=mute_mac)))

    def stop(self, volume=None):
        """Stop streaming; with `volume`, set the rooms to it afterwards (e.g. undo a fade-out)."""
        self._queue.put(("stop", {"volume": volume}))

    def set_volume(self, volume):
        """Change the volume of the streaming rooms; remembered for the next start."""
        self._queue.put(("set_volume", dict(volume=volume)))

    def shutdown(self):
        """Stop streaming, restore the Mac output and end the worker thread."""
        self._queue.put(("stop", {}))
        self._queue.put(None)
        self._thread.join()

    def wait_for(self, *statuses, timeout=None):
        """Block until the status is one of `statuses`; returns the state, or None on timeout."""
        deadline = None if timeout is None else time.time() + timeout
        with self._cond:
            while self.state.status not in statuses:
                left = None if deadline is None else deadline - time.time()
                if left is not None and left <= 0:
                    return None
                self._cond.wait(left)
            return self.state

    # --- worker thread --------------------------------------------------------------------------

    def _set(self, **changes):
        log.info("state: %s", {k: v for k, v in changes.items() if v is not None})
        with self._cond:
            for k, v in changes.items():
                setattr(self.state, k, v)
            self._cond.notify_all()
        if self._on_change:
            self._on_change(self.state)

    def _loop(self):
        while (item := self._queue.get()) is not None:
            name, kwargs = item
            try:
                getattr(self, "_do_" + name)(**kwargs)
            except Exception as e:  # keep the worker alive whatever a command does
                log.exception("%s failed", name)
                self._teardown()
                self._set(status=ERROR, error=str(e), error_key=getattr(e, "key", None),
                          error_vars=getattr(e, "vars", None))

    def _do_start(self, rooms, device, fmt, port, bitrate, lowlat, volume, capture, mute_mac):
        self._do_stop()
        self._set(status=CONNECTING, rooms=list(rooms), url=None, error=None, error_key=None, error_vars=None)
        source = capture_mod.make_capture(capture, device, mute_mac)
        source.prepare()
        speakers = discovery.find_rooms(rooms)
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(source.output_context())
        source.start()
        self._stack.callback(source.stop)   # undone after the session stops, before the output is restored
        self._session = session_mod.Session(speakers, source, fmt, port, bitrate, lowlat, volume)
        url = self._session.start()
        self._set(status=STREAMING, url=url, started_at=time.time())

    def _do_set_volume(self, volume):
        if self._session:
            self._session.volume = volume
            for speaker in self._session.rooms:
                speaker.volume = volume

    def _do_stop(self, volume=None):
        had_session = self._session is not None
        rooms = list(self._session.rooms) if had_session and volume is not None else []  # to restore after
        self._teardown()
        if had_session or self.state.status in (CONNECTING, STREAMING):
            self._set(status=STOPPED, url=None)
        if volume is not None:
            for speaker in rooms:
                speaker.volume = volume

    def _teardown(self):
        session, stack, self._session, self._stack = self._session, self._stack, None, None
        if session:
            session.stop()
        if stack:
            stack.close()
