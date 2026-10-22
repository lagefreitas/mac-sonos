"""Media and volume keys through a Quartz event tap.

The tap sees the keyboard's system-defined events (play/pause, next, previous, volume) before the apps do and may
swallow them, so the Sonos can answer instead of the Mac. macOS only creates it for a process that was granted
Accessibility; without it nothing happens and `start()` says so, so the app can ask the user.
"""
import logging

log = logging.getLogger("mac_sonos")

# NX_KEYTYPE_* codes of the media keys (IOKit ev_keymap.h)
KEY_NAMES = {0: "volume_up", 1: "volume_down", 7: "mute", 16: "play", 17: "next", 18: "previous",
             19: "next", 20: "previous"}  # fast-forward / rewind keys behave like next / previous
NS_SYSTEM_DEFINED, MEDIA_SUBTYPE = 14, 8


def decode(data1):
    """(key name or None, pressed, repeat) from the `data1` field of a system-defined event."""
    code = (data1 & 0xFFFF0000) >> 16
    flags = data1 & 0x0000FFFF
    state = (flags & 0xFF00) >> 8
    return KEY_NAMES.get(code), state == 0xA, bool(flags & 0x1)


def is_trusted(prompt=False):
    """Whether this process has Accessibility; with `prompt`, ask macOS to show its permission dialog."""
    try:
        from ApplicationServices import (AXIsProcessTrusted, AXIsProcessTrustedWithOptions,
                                         kAXTrustedCheckOptionPrompt)
        if prompt:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except Exception:
        log.warning("could not check the Accessibility permission", exc_info=True)
        return False


class MediaKeys:
    def __init__(self, handler):
        """handler(key, repeat) -> True to swallow the key, False to let it through to the system."""
        self.handler = handler
        self._tap = self._source = None
        self._swallowed = set()   # keys whose press we swallowed: their release must be swallowed too

    @property
    def active(self):
        return self._tap is not None

    def start(self, prompt=False):
        """Install the tap; True when it is running, False when macOS refused (no permission yet)."""
        if self.active:
            return True
        if not is_trusted(prompt):
            return False
        import Quartz
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, Quartz.kCGEventTapOptionDefault,
            Quartz.CGEventMaskBit(NS_SYSTEM_DEFINED), self._callback, None)
        if self._tap is None:
            return False
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        log.info("media keys: tap installed")
        return True

    def stop(self):
        if not self.active:
            return
        import Quartz
        Quartz.CGEventTapEnable(self._tap, False)
        Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes)
        self._tap = self._source = None
        self._swallowed.clear()
        log.info("media keys: tap removed")

    def _callback(self, proxy, event_type, event, refcon):
        import Quartz
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            if self._tap is not None:  # macOS switches a slow tap off: switch it back on
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        if event_type != NS_SYSTEM_DEFINED:
            return event
        try:
            from AppKit import NSEvent
            ns = NSEvent.eventWithCGEvent_(event)
            if ns is None or ns.subtype() != MEDIA_SUBTYPE:
                return event
            key, pressed, repeat = decode(ns.data1())
            if key is None:
                return event
            if pressed:
                swallow = bool(self.handler(key, repeat))
                if swallow:
                    self._swallowed.add(key)
            else:
                swallow = key in self._swallowed
                self._swallowed.discard(key)
            return None if swallow else event
        except Exception:  # never break the keyboard because of a bug here
            log.exception("media key handler failed")
            return event
