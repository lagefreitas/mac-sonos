"""Command line: list rooms, run the stream, benchmark latency."""
import argparse
import logging
import os
import signal
import sys
import time

from . import config
from .controller import ERROR, STREAMING, Controller
from .core import output
from .core.formats import FORMATS
from .core.session import Session
from .sonos import discovery


def cmd_list(_):
    for s in discovery.discover():
        print(f"{s.player_name:20} {s.ip_address}  {s.get_speaker_info().get('model_name', '')}")


def cmd_devices(_):
    print("\n".join(output.input_devices()))


def cmd_run(args):
    cfg = config.load()
    rooms = args.rooms or cfg["rooms"]
    device = args.device or cfg["device"]
    fmt = args.format or cfg["format"]
    volume = cfg["volume"] if args.volume is None else args.volume
    ctl = Controller()
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # pkill / logout restore the output too
    try:
        ctl.start(rooms, device, fmt, args.port or cfg["port"], args.bitrate, args.lowlat, volume)
        state = ctl.wait_for(STREAMING, ERROR)
        if state.status == ERROR:
            sys.exit(state.error)
        print(f"Streaming Mac audio to {', '.join(rooms)} ({state.url}, {fmt})")
        print("Ctrl-C to stop.")
        while ctl.state.status == STREAMING:
            time.sleep(1)
        if ctl.state.status == ERROR:
            sys.exit(ctl.state.error)
    except KeyboardInterrupt:
        pass
    finally:
        ctl.shutdown()


def cmd_nowplaying(_):
    from .sonos.monitor import Monitor
    from .ui import model

    def show(groups):
        print("-" * 60)
        for g in groups:
            print(f"{model.STATE_ICONS.get(g.state, '⏹')} {model.group_name(g):30} {model.track_text(g)} "
                  f"[{model.time_text(g) or g.state}] actions={sorted(g.actions)}", flush=True)

    monitor = Monitor(on_change=show)
    monitor.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()


def cmd_media_keys(_):
    from .ui import mediakeys
    trusted = mediakeys.is_trusted()
    print("Accessibility: " + ("granted" if trusted else
          "NOT granted (System Settings > Privacy & Security > Accessibility)"))
    try:
        import Quartz  # noqa: F401
        from AppKit import NSEvent  # noqa: F401
        print("Quartz and AppKit: available")
    except Exception as e:
        print("Quartz/AppKit problem:", e)


def cmd_login_item(args):
    from .ui import loginitem
    names = {0: "not registered", 1: "enabled", 2: "requires approval (System Settings > General > Login Items)",
             3: "not found"}
    if args.action != "status":
        loginitem.set_enabled(args.action == "enable")
    code = loginitem.status()
    print("n/a (not running from Mac Sonos.app)" if code is None else names[code])


LOG_PATH = os.path.expanduser("~/Library/Logs/mac-sonos.log")


def cmd_menubar(_):
    from .ui.menubar import MenuBarApp  # AppKit is only needed here
    logging.basicConfig(filename=LOG_PATH, encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("mac_sonos").info("menu bar app starting")
    MenuBarApp().run()


# --- latency benchmark -------------------------------------------------------------------------
# The source is live (1 s of audio per second), so the time Sonos waits between connecting to the
# stream and reporting PLAYING is the depth of its start-up buffer, i.e. the latency it adds.

def measure(session, timeout=30):
    """Seconds from Sonos's first HTTP GET to the transport reporting PLAYING (None on timeout)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if session.server.gets and session.playing():
            return time.time() - session.server.gets[0]
        time.sleep(0.2)
    return None


CONFIGS = {  # name -> Session kwargs
    "mp3": dict(fmt="mp3"),
    "mp3-lowlat": dict(fmt="mp3", lowlat=True),
    "aac-lowlat": dict(fmt="aac", lowlat=True),
    "flac-lowlat": dict(fmt="flac", lowlat=True),
    "wav-lowlat": dict(fmt="wav", lowlat=True),
}


def cmd_bench(args):
    output.require_device(args.device)
    rooms = discovery.find_rooms(args.rooms)
    results = {}
    with output.OutputSwitch(args.device):
        for name in args.configs or list(CONFIGS):
            lat = []
            for _ in range(args.runs):
                session = Session(rooms, args.device, port=args.port, volume=args.volume, **CONFIGS[name])
                try:
                    session.start()
                    m = measure(session)
                except Exception as e:
                    print(f"[{name}] failed: {e}", flush=True)
                    m = None
                finally:
                    session.stop()
                print(f"[{name}] {'timeout' if m is None else f'{m:.2f}s'} ({len(session.server.gets)} GETs)", flush=True)
                if m is not None:
                    lat.append(m)
                time.sleep(3)
            results[name] = lat
    print("\nSummary (median seconds from connect to PLAYING):")
    for name, lat in results.items():
        print(f"  {name:12} " + (f"{sorted(lat)[len(lat) // 2]:.2f}s  {['%.1f' % x for x in lat]}" if lat else "n/a"))


def main():
    # Children (ffmpeg...) inherit stdin; if it is a tty, a background job gets stopped (SIGTTIN).
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    p = argparse.ArgumentParser(prog="mac_sonos", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list Sonos rooms").set_defaults(fn=cmd_list)
    sub.add_parser("devices", help="list Mac audio input devices").set_defaults(fn=cmd_devices)
    r = sub.add_parser("run", help="stream Mac audio to Sonos")
    r.add_argument("rooms", nargs="*", help="room names (first is the group leader); default: saved config")
    r.add_argument("--device")
    r.add_argument("--port", type=int)
    r.add_argument("--format", choices=FORMATS)
    r.add_argument("--bitrate", help="mp3/aac bitrate, e.g. 192k")
    r.add_argument("--lowlat", action="store_true", help="low-latency ffmpeg input flags")
    r.add_argument("--volume", type=int, help="set Sonos volume (0-100) on start")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("nowplaying", help="live view of what every Sonos group is playing").set_defaults(fn=cmd_nowplaying)
    sub.add_parser("media-keys", help="can this process capture media keys? (Accessibility permission)").set_defaults(fn=cmd_media_keys)
    li = sub.add_parser("login-item", help="open at login (only inside Mac Sonos.app)")
    li.add_argument("action", choices=["status", "enable", "disable"])
    li.set_defaults(fn=cmd_login_item)
    sub.add_parser("menubar", help="menu bar app").set_defaults(fn=cmd_menubar)
    b = sub.add_parser("bench", help="measure Sonos start-up buffer (latency) per stream config")
    b.add_argument("rooms", nargs="+")
    b.add_argument("--configs", nargs="+", choices=CONFIGS)
    b.add_argument("--device", default="BlackHole 2ch")
    b.add_argument("--port", type=int, default=8000)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--volume", type=int, default=15)
    b.set_defaults(fn=cmd_bench)
    args = p.parse_args()
    try:
        args.fn(args)
    except (output.DeviceNotFound, discovery.RoomNotFound) as e:
        sys.exit(str(e))
