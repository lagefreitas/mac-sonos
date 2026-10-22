"""The Mac side: capture device lookup and output switching."""
import shutil
import subprocess
from pathlib import Path

import sounddevice as sd

# Last real (non-capture) output, so a killed run can't make the capture device "the previous one".
REMEMBERED = Path.home() / "Library" / "Application Support" / "mac-sonos" / "previous_output"


class DeviceNotFound(Exception):
    pass


def input_devices():
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def device_exists(name):
    return name in input_devices()


def require_device(name):
    if not device_exists(name):
        raise DeviceNotFound(
            f"Audio device '{name}' not found. If BlackHole is installed, load it with "
            "`sudo killall coreaudiod` (or reboot); otherwise see `python -m mac_sonos devices`.")


def mac_volume():
    """The Mac's output volume (0-100), or None if it can't be read."""
    out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


class OutputSwitch:
    """Point the Mac output at the capture device; restore it on exit (needs SwitchAudioSource)."""

    def __init__(self, device):
        self.exe, self.device, self.previous = shutil.which("SwitchAudioSource"), device, None

    def __enter__(self):
        if self.exe:
            self.previous = self._previous()
            subprocess.run([self.exe, "-s", self.device], capture_output=True)
            # BlackHole scales the signal by the Mac volume (31% ~ -50 dB, near silence)
            subprocess.run(["osascript", "-e", "set volume output volume 100"], capture_output=True)
        else:
            print(f"Set the Mac's sound output to '{self.device}' (or: brew install switchaudio-osx).")
        return self

    def _previous(self):
        current = subprocess.run([self.exe, "-c"], capture_output=True, text=True).stdout.strip()
        if current != self.device:
            REMEMBERED.parent.mkdir(parents=True, exist_ok=True)
            REMEMBERED.write_text(current)
            return current
        try:  # a previous run was killed before it could restore the output
            return REMEMBERED.read_text().strip() or None
        except OSError:
            return None

    def __exit__(self, *exc):
        if self.exe and self.previous:
            subprocess.run([self.exe, "-s", self.previous], capture_output=True)
