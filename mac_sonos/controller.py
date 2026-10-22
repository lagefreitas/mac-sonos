"""UI-independent controller: state plus commands, run serially on a worker thread.

The CLI, the menu bar app and any future panel are clients of this class. Commands return at once;
progress shows up in `state` (and through `on_change`), so a UI thread never blocks on the network.
"""
import contextlib
import queue
import threading
import time
from dataclasses import dataclass, field

from .core import output, session as session_mod
from .sonos import discovery

STOPPED, CONNECTING, STREAMING, ERROR = "stopped", "connecting", "streaming", "error"


@dataclass
class State:
    status: str = STOPPED
    rooms: list = field(default_factory=list)
    url: str = None
    error: str = None


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
              volume=None):
        self._queue.put(("start", dict(rooms=list(rooms), device=device, fmt=fmt, port=port,
                                       bitrate=bitrate, lowlat=lowlat, volume=volume)))

    def stop(self):
        self._queue.put(("stop", {}))

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
                self._teardown()
                self._set(status=ERROR, error=str(e))

    def _do_start(self, rooms, device, fmt, port, bitrate, lowlat, volume):
        self._do_stop()
        self._set(status=CONNECTING, rooms=list(rooms), url=None, error=None)
        output.require_device(device)
        speakers = discovery.find_rooms(rooms)
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(output.OutputSwitch(device))
        self._session = session_mod.Session(speakers, device, fmt, port, bitrate, lowlat, volume)
        url = self._session.start()
        self._set(status=STREAMING, url=url)

    def _do_set_volume(self, volume):
        if self._session:
            self._session.volume = volume
            for speaker in self._session.rooms:
                speaker.volume = volume

    def _do_stop(self):
        had_session = self._session is not None
        self._teardown()
        if had_session or self.state.status in (CONNECTING, STREAMING):
            self._set(status=STOPPED, url=None)

    def _teardown(self):
        session, stack, self._session, self._stack = self._session, self._stack, None, None
        if session:
            session.stop()
        if stack:
            stack.close()
