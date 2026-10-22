"""Sonos alarms: list, create, enable, retime, repeat, remove.

Alarms live in the speakers and fire without the Mac. They play the Sonos chime (no program is set).
Like the library, every call is a network round trip, so jobs run on a worker thread and the UI reads a cache.
"""
import datetime
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field

from soco import alarms as soco_alarms

log = logging.getLogger("mac_sonos")

RELOAD_EVERY = 60.0
FIXED = ("DAILY", "ONCE", "WEEKDAYS", "WEEKENDS")
DEFAULT_VOLUME = 20


@dataclass(frozen=True)
class AlarmInfo:
    alarm_id: str
    room: str
    time: datetime.time
    recurrence: str          # DAILY, ONCE, WEEKDAYS, WEEKENDS or ON_<digits> (0 = Sunday)
    enabled: bool
    volume: int
    alarm: object = field(default=None, compare=False, repr=False)   # the SoCo Alarm, to change it


def parse_time(text):
    """'7:30' / '07:30' / '07:30:00' -> datetime.time, or None if it is not a valid time of day."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", text or "")
    if not match:
        return None
    h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    return datetime.time(h, m, s) if h < 24 and m < 60 and s < 60 else None


def format_time(value):
    return f"{value.hour:02d}:{value.minute:02d}"


def valid_recurrence(value):
    return value in FIXED or bool(re.fullmatch(r"ON_[0-6]{1,7}", value or ""))


def days_of(recurrence):
    """Weekday numbers (0 = Sunday) of an ON_<digits> recurrence, in order; [] for the named ones."""
    match = re.fullmatch(r"ON_([0-6]+)", recurrence or "")
    return sorted({int(c) for c in match.group(1)}) if match else []


def infos_from(alarms):
    out = [AlarmInfo(str(a.alarm_id), a.zone.player_name, a.start_time, a.recurrence, bool(a.enabled),
                     int(a.volume), a) for a in alarms]
    return sorted(out, key=lambda i: (i.time, i.room, i.alarm_id))


class AlarmManager:
    def __init__(self, any_zone, zone_for_room, on_change=None):
        self._any_zone, self._zone_for_room, self._on_change = any_zone, zone_for_room, on_change
        self.alarms = []
        self.version = 0
        self._jobs = queue.Queue()
        self._next_reload = 0.0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alarms")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._jobs.put(None)
        if self._thread:
            self._thread.join(timeout=10)

    # --- requests from the UI (non-blocking) ----------------------------------------------------

    def reload(self):
        self._jobs.put(("reload", None))

    def add(self, room, start_time, recurrence="DAILY", volume=DEFAULT_VOLUME, enabled=True):
        self._jobs.put(("add", (room, start_time, recurrence, volume, enabled)))

    def change(self, alarm_id, **fields):
        """fields: enabled, start_time, recurrence, volume."""
        self._jobs.put(("change", (alarm_id, fields)))

    def remove(self, alarm_id):
        self._jobs.put(("remove", alarm_id))

    # --- worker ---------------------------------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=1.0)
            except queue.Empty:
                job = None
            if self._stop.is_set():
                return
            try:
                self._step(job, time.time())
            except Exception:
                log.exception("alarm job failed")

    def _step(self, job, now):
        if job:
            kind, arg = job
            try:
                getattr(self, "_do_" + kind)(arg)
            finally:
                self._next_reload = 0.0  # whatever happened, read the alarms back
        if now >= self._next_reload and self._any_zone():
            self._load(now)

    def _load(self, now):
        self._next_reload = now + RELOAD_EVERY
        zone = self._any_zone()
        if zone is None:
            return
        infos = infos_from(soco_alarms.get_alarms(zone))
        if infos != self.alarms:
            self.alarms = infos
            self.version += 1
            if self._on_change:
                self._on_change()

    def _do_reload(self, _):
        pass

    def _do_add(self, args):
        room, start_time, recurrence, volume, enabled = args
        zone = self._zone_for_room(room)
        if zone is None:
            raise LookupError(f"room {room!r} not found")
        if not valid_recurrence(recurrence):
            raise ValueError(f"bad recurrence {recurrence!r}")
        soco_alarms.Alarm(zone, start_time=start_time, recurrence=recurrence, volume=int(volume),
                          enabled=bool(enabled)).save()

    def _find(self, alarm_id):
        info = next((a for a in self.alarms if a.alarm_id == str(alarm_id)), None)
        if info is None:
            raise LookupError(f"alarm {alarm_id!r} not found")
        return info.alarm

    def _do_change(self, args):
        alarm_id, fields = args
        alarm = self._find(alarm_id)
        if "recurrence" in fields and not valid_recurrence(fields["recurrence"]):
            raise ValueError(f"bad recurrence {fields['recurrence']!r}")
        for name, value in fields.items():
            setattr(alarm, name, value)
        alarm.save()

    def _do_remove(self, alarm_id):
        self._find(alarm_id).remove()
