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
from types import SimpleNamespace

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
def probe_ioproc(seconds=2.0, output_name="MacBook Air Speakers", mute_local=False, tap_only=False, **tap_options):
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
        uid = output_uid_by_name(output_name) if output_name and not tap_only else None
        with SystemAudioTap(mute_local=mute_local, output_uid=uid,
                            clock_from_output=not output_name and not tap_only, **tap_options) as tap:
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


# --- the capture used by the app: one private tap read through an IOProc, shared by every connection -------------
class TapNoAudio(TapError):
    """The tap was created but no audio buffers arrived: almost always the missing permission."""


class TapPermissionError(Exception):
    """English text for the CLI; `key` and `vars` let the UI show it in the user's language."""
    key = "error.tap_permission"

    def __init__(self):
        super().__init__("Mac Sonos needs the System Audio Recording permission (System Settings > Privacy & Security).")
        self.vars = {}


def _core_audio():
    """CoreAudio through ctypes, with the signatures the tap code needs."""
    import ctypes
    import ctypes.util

    class Address(ctypes.Structure):
        _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

    class StreamFormat(ctypes.Structure):   # AudioStreamBasicDescription
        _fields_ = [("rate", ctypes.c_double), ("format_id", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                    ("bytes_per_packet", ctypes.c_uint32), ("frames_per_packet", ctypes.c_uint32),
                    ("bytes_per_frame", ctypes.c_uint32), ("channels", ctypes.c_uint32),
                    ("bits", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]

    class AudioBuffer(ctypes.Structure):
        _fields_ = [("channels", ctypes.c_uint32), ("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]

    class AudioBufferList(ctypes.Structure):
        _fields_ = [("count", ctypes.c_uint32), ("buffers", AudioBuffer * 1)]

    proc_type = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    lib = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
    lib.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    lib.AudioObjectGetPropertyData.restype = ctypes.c_int32
    lib.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, proc_type, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
    lib.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.AudioDeviceStart.restype = ctypes.c_int32
    lib.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    return SimpleNamespace(lib=lib, Address=Address, StreamFormat=StreamFormat, AudioBuffer=AudioBuffer,
                           AudioBufferList=AudioBufferList, proc_type=proc_type)


def _fourcc(text):
    return int.from_bytes(text.encode(), "big")


FLOAT_FLAG, NON_INTERLEAVED_FLAG = 1, 1 << 5


def float_to_int16(samples):
    """Interleaved float32 samples (an array('f')) -> little-endian int16 bytes, clipped."""
    from array import array
    return array("h", [32767 if x >= 1.0 else -32768 if x <= -1.0 else int(x * 32767) for x in samples]).tobytes()


def interleave_stereo(buffers):
    """Float32 buffers as delivered by the IOProc -> one interleaved stereo array('f').
    `buffers` is a list of (channels, array('f')): one interleaved buffer, or one mono buffer per channel."""
    from array import array
    if len(buffers) == 1:
        channels, data = buffers[0]
        if channels == 2:
            return data
        if channels == 1:                       # mono: both ears
            out = array("f", bytes(8 * len(data)))
            out[0::2] = data
            out[1::2] = data
            return out
        out = array("f", bytes(8 * (len(data) // channels)))   # more than two: keep the first two
        out[0::2], out[1::2] = data[0::channels], data[1::channels]
        return out
    left, right = buffers[0][1], buffers[1][1]  # non-interleaved: one buffer per channel
    out = array("f", bytes(8 * min(len(left), len(right))))
    out[0::2], out[1::2] = left[:len(out) // 2], right[:len(out) // 2]
    return out


class TapSource:
    """The system audio as stereo int16 chunks, for any number of readers.

    One private tap and private aggregate device (so nothing shows up in the system's device lists) feed one IOProc;
    each connection to the Sonos registers a reader with open(). The sample rate comes from CoreAudio, not from a guess.
    """

    def __init__(self, mute_local=True, name="Mac Sonos Tap", first_audio_timeout=2.0):
        self.mute_local, self.name, self.first_audio_timeout = mute_local, name, first_audio_timeout
        self.rate = None
        self._tap = None
        self._readers = []
        self._lock = None
        self._proc = None
        self._keep = None
        self._ca = None
        self._first = None

    # --- lifecycle ------------------------------------------------------------------------------

    def start(self):
        import ctypes
        import threading
        self._lock, self._first = threading.Lock(), threading.Event()
        self._ca = ca = _core_audio()
        self._tap = SystemAudioTap(mute_local=self.mute_local, name=self.name)
        self._tap.open()
        try:
            self.rate = self._read_rate(ca)

            def callback(device, now, in_data, in_time, out_data, out_time, client):
                try:
                    self._on_buffers(in_data)
                except Exception:  # an exception must never unwind into Core Audio's thread
                    log.exception("tap callback failed")
                return 0
            self._keep = ca.proc_type(callback)
            self._proc = ctypes.c_void_p()
            status = ca.lib.AudioDeviceCreateIOProcID(self._tap.aggregate_id, self._keep, None, ctypes.byref(self._proc))
            if status != NO_ERROR:
                raise TapError("create the IOProc", status)
            status = ca.lib.AudioDeviceStart(self._tap.aggregate_id, self._proc)
            if status != NO_ERROR:
                raise TapError("start the device", status)
            if not self._first.wait(self.first_audio_timeout):
                raise TapNoAudio("no audio arrived from the tap")
            log.info("tap capture running at %s Hz", self.rate)
        except Exception:
            self.stop()
            raise

    def stop(self):
        ca, tap, proc = self._ca, self._tap, self._proc
        self._proc = self._tap = None
        if ca and tap and proc and proc.value is not None and tap.aggregate_id is not None:
            ca.lib.AudioDeviceStop(tap.aggregate_id, proc)
            ca.lib.AudioDeviceDestroyIOProcID(tap.aggregate_id, proc)
        if tap:
            tap.close()
        self._keep = None
        if self._lock:
            with self._lock:
                self._readers.clear()

    def _read_rate(self, ca):
        """The aggregate's nominal rate: what the IOProc really delivers."""
        import ctypes
        rate = ctypes.c_double()
        size = ctypes.c_uint32(ctypes.sizeof(rate))
        address = ca.Address(_fourcc("nsrt"), _fourcc("glob"), 0)
        status = ca.lib.AudioObjectGetPropertyData(self._tap.aggregate_id, ctypes.byref(address), 0, None,
                                                   ctypes.byref(size), ctypes.byref(rate))
        if status != NO_ERROR or rate.value <= 0:
            raise TapError("read the sample rate", status)
        fmt = ca.StreamFormat()
        size = ctypes.c_uint32(ctypes.sizeof(fmt))
        address = ca.Address(_fourcc("tfmt"), _fourcc("glob"), 0)
        status = ca.lib.AudioObjectGetPropertyData(self._tap.tap_id, ctypes.byref(address), 0, None,
                                                   ctypes.byref(size), ctypes.byref(fmt))
        if status == NO_ERROR and not (fmt.flags & FLOAT_FLAG and fmt.bits == 32):
            raise TapError(f"the tap delivers an unsupported format ({fmt.bits}-bit, flags {fmt.flags})")
        return float(rate.value)

    # --- readers --------------------------------------------------------------------------------

    def open(self, push):
        """Context manager: while open, push(bytes) gets every chunk of stereo int16 audio."""
        from contextlib import contextmanager

        @contextmanager
        def reader():
            with self._lock:
                self._readers.append(push)
            try:
                yield
            finally:
                with self._lock:
                    if push in self._readers:
                        self._readers.remove(push)
        return reader()

    def _on_buffers(self, in_data):
        import ctypes
        from array import array
        if not in_data:
            return
        ca = self._ca
        abl = ctypes.cast(in_data, ctypes.POINTER(ca.AudioBufferList)).contents
        n = abl.count
        if not n:
            return
        raw = (ca.AudioBuffer * n).from_address(ctypes.addressof(abl.buffers))
        buffers = []
        for b in raw:
            data = array("f")
            data.frombytes(ctypes.string_at(b.data, b.size) if b.data and b.size else b"")
            buffers.append((b.channels, data))
        self._first.set()
        with self._lock:
            readers = list(self._readers)
        if readers:
            chunk = float_to_int16(interleave_stereo(buffers))
            for push in readers:
                push(chunk)
