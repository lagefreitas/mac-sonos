"""Where the Mac's audio comes from. Two interchangeable sources:

* BlackHole: the Mac's output is switched to the BlackHole virtual device and read like a microphone (the macOS
  microphone indicator shows; the Mac's volume is the signal level, so it must stay at 100%).
* System audio tap: a Core Audio process tap listens to what the Mac plays; nothing is switched, the Mac's volume does
  not matter, and the Mac can be muted while it streams. Needs the System Audio Recording permission.

Both offer the same few things: prepare() fails early with a clear error, output_context() wraps the Mac's output
handling, start()/stop() bracket the capture, `rate` is the sample rate of the stereo int16 chunks, and open(push)
registers a reader for the length of one connection.
"""
import contextlib
import logging
from contextlib import contextmanager

from . import output
from .formats import RATE

log = logging.getLogger("mac_sonos")

BLACKHOLE, TAP = "blackhole", "tap"
KINDS = (BLACKHOLE, TAP)


class BlackHoleCapture:
    kind = BLACKHOLE
    rate = RATE

    def __init__(self, device="BlackHole 2ch"):
        self.device = device

    def prepare(self):
        output.require_device(self.device)

    def output_context(self):
        return output.OutputSwitch(self.device)

    def start(self):
        pass

    def stop(self):
        pass

    @contextmanager
    def open(self, push):
        import sounddevice as sd   # one PortAudio stream per connection: a stream starts at the live edge
        with sd.RawInputStream(device=self.device, samplerate=RATE, channels=2, dtype="int16", latency=0.1,
                               callback=lambda indata, frames, t, status: push(bytes(indata))):
            yield


class TapCapture:
    kind = TAP

    def __init__(self, mute_mac=True):
        self.mute_mac = mute_mac
        self.source = None
        self.rate = None

    def prepare(self):
        pass   # the permission is only known to be missing when no audio arrives: see start()

    def output_context(self):
        return contextlib.nullcontext()   # the Mac's output is left alone

    def start(self):
        from . import tapcapture as tc
        try:
            self._open(tc)
        except tc.TapNoAudio:
            # No audio: the permission is missing. Ask once if macOS has never been asked, then try again.
            state = tc.audio_permission()
            log.info("tap gave no audio; system audio permission is %s", state)
            if state == "denied":
                raise tc.TapPermissionError() from None
            if state == "undetermined" and tc.request_audio_permission(timeout=60) is not True:
                raise tc.TapPermissionError() from None
            try:
                self._open(tc)
            except tc.TapNoAudio:
                raise tc.TapPermissionError() from None

    def _open(self, tc):
        self.source = tc.TapSource(mute_local=self.mute_mac)
        self.source.start()
        self.rate = self.source.rate

    def stop(self):
        if self.source:
            self.source.stop()
            self.source = None

    def open(self, push):
        return self.source.open(push)


def make_capture(kind=BLACKHOLE, device="BlackHole 2ch", mute_mac=True):
    if kind == TAP:
        return TapCapture(mute_mac)
    if kind == BLACKHOLE:
        return BlackHoleCapture(device)
    raise ValueError(f"unknown capture {kind!r}; choose one of {KINDS}")
