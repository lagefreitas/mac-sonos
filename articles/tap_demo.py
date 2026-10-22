"""Minimal system-audio capture with a Core Audio process tap (macOS 14.2+).
Needs: pip install pyobjc-framework-CoreAudio     Prints how many callbacks arrived and the peak level."""
import ctypes
import ctypes.util
import time
import uuid

import CoreAudio as ca
import objc


def key(constant):                      # PyObjC hands these kAudio*Key constants over as bytes; Core Audio wants str
    return constant.decode() if isinstance(constant, bytes) else constant


# 1. a global stereo tap, private to this process
desc = objc.lookUpClass("CATapDescription").alloc().initStereoGlobalTapButExcludeProcesses_([])
desc.setName_("demo tap")
desc.setPrivate_(True)
desc.setMuteBehavior_(ca.CATapUnmuted)  # CATapMuted would silence the speakers while we listen
status, tap_id = ca.AudioHardwareCreateProcessTap(desc, None)
assert status == 0, f"tap: OSStatus {status}"

# 2. a private aggregate device that contains the tap (a private tap needs a private aggregate)
status, agg_id = ca.AudioHardwareCreateAggregateDevice({
    key(ca.kAudioAggregateDeviceNameKey): "demo tap",
    key(ca.kAudioAggregateDeviceUIDKey): str(uuid.uuid4()),
    key(ca.kAudioAggregateDeviceIsPrivateKey): True,
    key(ca.kAudioAggregateDeviceTapListKey): [{
        key(ca.kAudioSubTapUIDKey): str(desc.UUID().UUIDString()),
        key(ca.kAudioSubTapDriftCompensationKey): True,
    }],
}, None)
assert status == 0, f"aggregate: OSStatus {status}"


# 3. read it with an IOProc through ctypes (PyObjC cannot hand the proc ID back to AudioDeviceStart)
class Buffer(ctypes.Structure):
    _fields_ = [("channels", ctypes.c_uint32), ("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class BufferList(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("buffers", Buffer * 1)]


IOProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, *[ctypes.c_void_p] * 6)
lib = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
lib.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, IOProc, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
lib.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
lib.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
lib.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

stats = {"calls": 0, "peak": 0.0}


def on_audio(device, now, in_data, in_time, out_data, out_time, client):
    stats["calls"] += 1
    if in_data:
        abl = ctypes.cast(in_data, ctypes.POINTER(BufferList)).contents
        if abl.count and abl.buffers[0].data:
            b = abl.buffers[0]
            samples = (ctypes.c_float * (b.size // 4)).from_address(b.data)   # float32, interleaved
            stats["peak"] = max(stats["peak"], max(map(abs, samples), default=0.0))
    return 0


callback = IOProc(on_audio)             # keep a reference, or it is garbage collected while Core Audio calls it
proc = ctypes.c_void_p()
lib.AudioDeviceCreateIOProcID(agg_id, callback, None, ctypes.byref(proc))
lib.AudioDeviceStart(agg_id, proc)
time.sleep(2)
lib.AudioDeviceStop(agg_id, proc)
lib.AudioDeviceDestroyIOProcID(agg_id, proc)
ca.AudioHardwareDestroyAggregateDevice(agg_id)
ca.AudioHardwareDestroyProcessTap(tap_id)
print(f"{stats['calls']} callbacks in 2 s, peak {stats['peak']:.4f}")
