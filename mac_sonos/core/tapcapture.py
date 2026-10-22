"""EXPERIMENT (branch sprint-11-process-tap): capture the Mac's audio with a Core Audio process tap.

No BlackHole and no switching of the Mac's output: a tap listens to what every process plays (macOS 14.2+). It
needs the "System Audio Recording" permission, and macOS shows its purple indicator instead of the orange
microphone one. The tap is exposed through a Core Audio aggregate device so that PortAudio, which the app already
uses, can read it like any input device.
"""
import json
import logging
import subprocess
import uuid

log = logging.getLogger("mac_sonos")

NO_ERROR = 0


def key(constant):
    """PyObjC hands the kAudio*Key constants over as bytes; Core Audio wants CFString keys (bytes crash it)."""
    return constant.decode() if isinstance(constant, bytes) else constant


class TapError(Exception):
    def __init__(self, step, status=None):
        super().__init__(f"{step}" + (f" (OSStatus {status}: {status_text(status)})" if status is not None else ""))
        self.step, self.status = step, status


def status_text(status):
    """A readable form of an OSStatus (often a four-letter code)."""
    if status is None:
        return ""
    raw = status & 0xFFFFFFFF
    chars = raw.to_bytes(4, "big")
    return chars.decode("ascii") if all(32 <= b < 127 for b in chars) else hex(raw)


def output_uid_by_name(name):
    """UID of the output device called `name` (SwitchAudioSource lists them), or None."""
    try:
        out = subprocess.run(["SwitchAudioSource", "-a", "-t", "output", "-f", "json"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            info = json.loads(line)
            if info.get("name") == name:
                return info.get("uid")
    except Exception:
        pass
    return None


def default_output_uid():
    """UID of the current default output device (SwitchAudioSource knows it), or None."""
    try:
        out = subprocess.run(["SwitchAudioSource", "-c", "-t", "output", "-f", "json"], capture_output=True,
                             text=True, timeout=5).stdout
        return json.loads(out).get("uid")
    except Exception:
        return None


class SystemAudioTap:
    """A global stereo tap plus the aggregate device that exposes it. Use as a context manager."""

    def __init__(self, mute_local=False, name="Mac Sonos Tap", clock_from_output=False, output_uid=None,
                 private_tap=True, private_aggregate=True, stacked=None):
        self.mute_local, self.name, self.clock_from_output = mute_local, name, clock_from_output
        self.output_uid, self.private_tap, self.private_aggregate, self.stacked = output_uid, private_tap, private_aggregate, stacked
        self.tap_id = self.aggregate_id = None
        self.tap_uuid = self.aggregate_uid = None

    @property
    def device_name(self):
        return self.name

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        import CoreAudio as ca
        import objc
        description = objc.lookUpClass("CATapDescription").alloc().initStereoGlobalTapButExcludeProcesses_([])
        description.setName_(self.name)
        description.setPrivate_(self.private_tap)
        description.setMuteBehavior_(ca.CATapMuted if self.mute_local else ca.CATapUnmuted)
        status, self.tap_id = ca.AudioHardwareCreateProcessTap(description, None)
        if status != NO_ERROR:
            raise TapError("create the process tap", status)
        self.tap_uuid = str(description.UUID().UUIDString())

        self.aggregate_uid = str(uuid.uuid4())
        spec = {
            key(ca.kAudioAggregateDeviceNameKey): self.name,
            key(ca.kAudioAggregateDeviceUIDKey): self.aggregate_uid,
            key(ca.kAudioAggregateDeviceIsPrivateKey): self.private_aggregate,   # visible to PortAudio when False
            key(ca.kAudioAggregateDeviceTapAutoStartKey): True,
            key(ca.kAudioAggregateDeviceTapListKey): [{key(ca.kAudioSubTapUIDKey): self.tap_uuid,
                                                       key(ca.kAudioSubTapDriftCompensationKey): True}],
        }
        if self.stacked is not None:
            spec[key(ca.kAudioAggregateDeviceIsStackedKey)] = self.stacked
        output = self.output_uid or (default_output_uid() if self.clock_from_output else None)
        if output:   # clock the aggregate from the real output so the tap does not drift
            spec[key(ca.kAudioAggregateDeviceMainSubDeviceKey)] = output
            spec[key(ca.kAudioAggregateDeviceSubDeviceListKey)] = [{key(ca.kAudioSubDeviceUIDKey): output}]
        status, self.aggregate_id = ca.AudioHardwareCreateAggregateDevice(spec, None)
        if status != NO_ERROR:
            self.close()
            raise TapError("create the aggregate device", status)
        return self

    def close(self):
        import CoreAudio as ca
        if self.aggregate_id is not None:
            ca.AudioHardwareDestroyAggregateDevice(self.aggregate_id)
            self.aggregate_id = None
        if self.tap_id is not None:
            ca.AudioHardwareDestroyProcessTap(self.tap_id)
            self.tap_id = None


def measure(seconds=3.0, mute_local=False, output_name=None):
    """Open a tap, read `seconds` of audio through PortAudio and report levels. A dict of facts, never raises."""
    import math
    import time
    result = {"steps": []}

    def step(text):
        result["steps"].append(text)
        print(text, flush=True)
    try:
        uid = output_uid_by_name(output_name) if output_name else None
        with SystemAudioTap(mute_local=mute_local, output_uid=uid, clock_from_output=not output_name) as tap:
            step(f"tap {tap.tap_id} and aggregate {tap.aggregate_id} created ({tap.tap_uuid})")
            import sounddevice as sd
            sd._terminate()          # PortAudio lists devices once; make it see the new aggregate
            sd._initialize()
            names = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
            step(f"input devices now: {names}")
            found = next((d for d in sd.query_devices() if d["name"] == tap.device_name), None)
            if not found:
                raise TapError("PortAudio does not list the aggregate device")
            step(f"aggregate seen by PortAudio: {found['max_input_channels']} ch @ {found['default_samplerate']} Hz")
            channels = found["max_input_channels"]
            frames = []
            with sd.RawInputStream(device=tap.device_name, samplerate=int(found["default_samplerate"]),
                                   channels=channels, dtype="int16",
                                   callback=lambda indata, n, t, s: frames.append(bytes(indata))):
                time.sleep(seconds)
            data = b"".join(frames)
            samples = memoryview(data).cast("h")
            per_channel = []
            for c in range(channels):
                ch = samples[c::channels]
                peak = max((abs(x) for x in ch), default=0)
                rms = math.sqrt(sum(x * x for x in ch) / max(len(ch), 1))
                per_channel.append({"peak": peak, "rms": round(rms, 1),
                                    "peak_db": round(20 * math.log10(peak / 32768), 1) if peak else None})
            result.update(bytes=len(data), channels=per_channel, output_uid=uid)
            step(f"captured {len(data)} bytes in {seconds}s over {channels} channels (output clock: {output_name or 'default'})")
            for c, info in enumerate(per_channel):
                step(f"  channel {c}: peak {info['peak']} ({info['peak_db']} dBFS), rms {info['rms']}")
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        step(f"FAILED: {result['error']}")
    return result


# --- reading the tap with an IOProc, through ctypes (PyObjC cannot hand the opaque proc ID back) -----------------
def probe_ioproc(seconds=2.0, output_name="MacBook Air Speakers", mute_local=False, **tap_options):
    """Create a tap, register an IOProc on its aggregate device and see whether audio buffers arrive.
    Returns a dict of facts; never raises."""
    import ctypes
    import ctypes.util
    import math
    import time
    result = {"calls": 0, "input_buffers": None, "channels": None, "bytes": 0, "peak": 0.0}

    class AudioBuffer(ctypes.Structure):
        _fields_ = [("mNumberChannels", ctypes.c_uint32), ("mDataByteSize", ctypes.c_uint32), ("mData", ctypes.c_void_p)]

    class AudioBufferList(ctypes.Structure):
        _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", AudioBuffer * 1)]

    proc_type = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    core = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    core.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, proc_type, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    core.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
    core.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    core.AudioDeviceStart.restype = ctypes.c_int32
    core.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    core.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

    def callback(device, now, in_data, in_time, out_data, out_time, client):
        result["calls"] += 1
        if in_data:
            abl = ctypes.cast(in_data, ctypes.POINTER(AudioBufferList)).contents
            n = abl.mNumberBuffers
            result["input_buffers"] = n
            if n:
                buffers = (AudioBuffer * n).from_address(ctypes.addressof(abl.mBuffers))
                result["channels"] = [b.mNumberChannels for b in buffers]
                for b in buffers:
                    if b.mData and b.mDataByteSize:
                        floats = (ctypes.c_float * (b.mDataByteSize // 4)).from_address(b.mData)
                        result["bytes"] += b.mDataByteSize
                        result["peak"] = max(result["peak"], max((abs(x) for x in floats), default=0.0))
        return 0

    keep = proc_type(callback)   # must stay alive while the device runs
    try:
        uid = output_uid_by_name(output_name) if output_name else None
        with SystemAudioTap(mute_local=mute_local, output_uid=uid, clock_from_output=not output_name, **tap_options) as tap:
            proc = ctypes.c_void_p()
            status = core.AudioDeviceCreateIOProcID(tap.aggregate_id, keep, None, ctypes.byref(proc))
            result["create_status"] = status
            if status != NO_ERROR:
                raise TapError("create the IOProc", status)
            status = core.AudioDeviceStart(tap.aggregate_id, proc)
            result["start_status"] = status
            if status != NO_ERROR:
                raise TapError("start the device", status)
            time.sleep(seconds)
            core.AudioDeviceStop(tap.aggregate_id, proc)
            core.AudioDeviceDestroyIOProcID(tap.aggregate_id, proc)
        result["peak_db"] = round(20 * math.log10(result["peak"]), 1) if result["peak"] > 0 else None
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


# --- the "System Audio Recording" permission (private TCC SPI: there is no public API for it) ------------------------
TCC_SERVICE = "kTCCServiceAudioCapture"
TCC_PATH = "/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC"
PERMISSION_NAMES = {0: "granted", 1: "denied", 2: "undetermined"}


def _cfstring(text):
    import ctypes
    import ctypes.util
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    return cf.CFStringCreateWithCString(None, text.encode(), 0x08000100)   # kCFStringEncodingUTF8


def audio_permission():
    """'granted', 'denied' or 'undetermined' for this process's responsible app, or 'unavailable'."""
    import ctypes
    try:
        tcc = ctypes.CDLL(TCC_PATH)
        tcc.TCCAccessPreflight.restype = ctypes.c_int
        tcc.TCCAccessPreflight.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return PERMISSION_NAMES.get(tcc.TCCAccessPreflight(_cfstring(TCC_SERVICE), None), "unavailable")
    except Exception:
        log.warning("could not read the system audio permission", exc_info=True)
        return "unavailable"


def request_audio_permission(timeout=90.0):
    """Ask macOS to show its permission dialog and wait for the answer: True, False, or None on timeout/error."""
    import ctypes
    import threading
    answer, done = [], threading.Event()

    class Descriptor(ctypes.Structure):
        _fields_ = [("reserved", ctypes.c_ulong), ("size", ctypes.c_ulong)]

    class Block(ctypes.Structure):   # an Objective-C global block, enough to be called back with one bool
        _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int), ("reserved", ctypes.c_int),
                    ("invoke", ctypes.c_void_p), ("descriptor", ctypes.c_void_p)]

    invoke_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool)

    def invoke(block, granted):
        answer.append(bool(granted))
        done.set()
    keep_invoke = invoke_type(invoke)
    try:
        isa = ctypes.c_void_p.in_dll(ctypes.CDLL(None), "_NSConcreteGlobalBlock")
        descriptor = Descriptor(0, ctypes.sizeof(Block))
        block = Block(ctypes.addressof(isa), 1 << 28, 0, ctypes.cast(keep_invoke, ctypes.c_void_p).value,
                      ctypes.addressof(descriptor))
        tcc = ctypes.CDLL(TCC_PATH)
        tcc.TCCAccessRequest.restype = None
        tcc.TCCAccessRequest.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        tcc.TCCAccessRequest(_cfstring(TCC_SERVICE), None, ctypes.addressof(block))
        return answer[0] if done.wait(timeout) else None
    except Exception:
        log.warning("could not ask for the system audio permission", exc_info=True)
        return None
