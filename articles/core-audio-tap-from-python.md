# Capturing macOS system audio from Python with Core Audio taps

*...and the traps I fell into on the way*

**TL;DR:** since macOS 14.2 you can listen to everything the Mac plays without installing a virtual audio driver, using a *Core Audio process tap*. It works from Python through PyObjC and `ctypes`, but several things fail silently or crash the
process with no traceback. Here is a working 70-line example and the list of traps.

## Why I needed it

I wanted my old Sonos speakers (a few Play:1s and the first Playbar, which can't do AirPlay) to act as my Mac's speakers.
My first version routed the Mac's output into [BlackHole](https://existential.audio/blackhole/) and read it like a
microphone. It worked, but it came with costs: the Mac's volume became the signal level (it had to stay at 100%), the
output device had to be switched and restored, and macOS lit its microphone indicator the whole time.

A process tap removes all three. This post is about getting one working from Python.

## What a process tap is

A tap (`CATapDescription`, macOS 14.2+) is a *listener* on the audio that processes send to the output. You create one,
put it inside an aggregate device, and read that device like any other, with an IOProc. Three properties surprised me,
each measured:

- **It hears the audio before the Mac's volume.** I played the same tone with the output volume at 100%, 50% and 100%
  again: the captured peak was -49.0 dBFS every time. So the volume keys no longer affect what you capture.
- **It can silence the speakers while it listens.** With `CATapMuted`, the BlackHole input I was watching went to digital
  silence while the tap kept hearing the tone; closing the tap brought it back.
- **It delivers buffers even when nothing plays**, with zeros. "No callbacks" therefore means something is wrong, not that
  the Mac is quiet.

## A minimal example

```python
import ctypes
import ctypes.util
import time
import uuid

import CoreAudio as ca            # pip install pyobjc-framework-CoreAudio
import objc


def key(constant):                # PyObjC hands these kAudio*Key constants over as bytes; Core Audio wants str
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


callback = IOProc(on_audio)       # keep a reference, or it is garbage collected while Core Audio calls it
proc = ctypes.c_void_p()
lib.AudioDeviceCreateIOProcID(agg_id, callback, None, ctypes.byref(proc))
lib.AudioDeviceStart(agg_id, proc)
time.sleep(2)
lib.AudioDeviceStop(agg_id, proc)
lib.AudioDeviceDestroyIOProcID(agg_id, proc)
ca.AudioHardwareDestroyAggregateDevice(agg_id)
ca.AudioHardwareDestroyProcessTap(tap_id)
print(f"{stats['calls']} callbacks in 2 s, peak {stats['peak']:.4f}")
```

The same script is in the repository as [`articles/tap_demo.py`](https://github.com/lagefreitas/mac-sonos/blob/main/articles/tap_demo.py).

On my machine this prints `188 callbacks in 2 s, peak 0.0035` with a quiet tone playing (that is about -49 dBFS) and a
peak of `0.0000` in silence. 188 callbacks in 2 s is about 94 a second: 512-frame buffers at 48 kHz.

## The traps

**1. A segfault with no traceback.** The `kAudioAggregateDevice*Key` constants arrive from PyObjC as `bytes` (`b'name'`,
`b'uid'`...). Put them in the dictionary as they are and `AudioHardwareCreateAggregateDevice` takes the whole process down:
exit code 139, nothing on stderr. `python -X faulthandler` pointed at the line; the fix is to decode them to `str`.

**2. A private tap needs a private aggregate.** I first made the aggregate public so that PortAudio could list it. With a
private tap and a public aggregate, everything succeeded (`OSStatus` 0, IOProc called ~94 times a second) but every call
carried **zero input buffers**. No error anywhere. If you want a normal input device that PortAudio can open, make *both*
the tap and the aggregate public; I measured 2 channels at 48 kHz that way. Otherwise keep both private, which also keeps
the device out of the system's sound settings.

**3. PyObjC can create the IOProc but not hand it back.** `AudioDeviceCreateIOProcIDWithBlock` returns a status and an
opaque pointer; passing that pointer to `AudioDeviceStart` raises `ValueError: calling method/function with 'undefined'
argument`. Calling `AudioDeviceCreateIOProcID` through `ctypes`, as above, avoids the problem. Do wrap your callback body
in `try/except` in real code: an exception cannot unwind into Core Audio's thread.

**4. Without the permission it fails silently.** In a fresh app identity the whole chain succeeded and the IOProc started
(`OSStatus` 0 both times), but it was called **zero** times in three seconds. There is no error to catch. The permission
is "System Audio Recording", and your `Info.plist` needs `NSAudioCaptureUsageDescription`.

**5. The permission has no public API.** What I found: the dialog appears by itself the first time audio I/O starts, and
`TCCAccessPreflight` in the private `TCC.framework` reads the state (granted, denied or undetermined). There is also a
`TCCAccessRequest` that takes an Objective-C block, which you can fake from `ctypes` with a global block structure; in my
tests it never called back, so I rely on the automatic dialog and use the preflight only to *tell the user what is
missing*. I would not build a product that depends on a private framework surviving a macOS update.

**6. "Granted" takes a moment to apply.** In my first real run the dialog appeared while the first attempt was waiting for
audio, which is expected. But the next attempt, made right after I clicked Allow, also got nothing; the third, a few seconds
later, worked. The app now waits for the answer and retries three times, 1.5 s apart, before reporting a permission
problem.

**7. An ad-hoc signature forgets the permission on every build.** TCC identifies an app by its code requirement. An
ad-hoc signature is a hash of the code, so each rebuild is a "new" app and the log says `Failed to match existing code
requirement`. Signing with a self-signed certificate (kept in its own keychain, so nothing touches your login keychain)
gives a stable requirement (`identifier "..." and certificate leaf = H"..."`) and the grant survives rebuilds.

## Getting the format and rate from Core Audio instead of guessing

The tap's stream format is available as `kAudioTapPropertyFormat` (`'tfmt'`, a 40-byte
`AudioStreamBasicDescription`), and the rate the aggregate really runs at as `kAudioDevicePropertyNominalSampleRate`
(`'nsrt'`). Mine reported 48 000 Hz, and the samples were 32-bit float. My app reads both at start-up, refuses a format it
does not handle, and converts to 16-bit integers for ffmpeg. In a 20-second run I received 47 923 frames a second against 48 000
expected (99.8%), with no gaps.

## Caveats

- macOS 14.2 or later; tested only on Apple silicon, on macOS 27.
- The permission calls rely on a private framework.
- Whether the macOS "recording" indicator for system audio differs from the microphone one is something I did not verify
  on my own screen, so I make no claim about it.

## Where this is used

It is the optional "system audio" capture mode of [Mac Sonos](https://github.com/lagefreitas/mac-sonos), a menu bar app
that plays the Mac's audio on Sonos speakers without AirPlay (MIT licensed). The code is in `mac_sonos/core/tapcapture.py`,
with tests for the audio conversion and the permission retry logic.

*This project was built with [Claude Code](https://claude.com/claude-code) (Claude Sonnet 5.5): I decided what to build and
tested every step on my own speakers; it wrote most of the code. The measurements above are from my own machine. Claude Code Sonnet 5.5 also wrote this article, reviewed by me.*
