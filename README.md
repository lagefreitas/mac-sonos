# mac-sonos

Use old Sonos speakers (Play:1, Playbar, no AirPlay) as the Mac's speaker.

Mac output -> BlackHole -> PortAudio capture (sounddevice) -> ffmpeg (MP3) -> HTTP stream on the LAN -> Sonos plays it as a radio stream.

Capture uses PortAudio, not ffmpeg's avfoundation input: the latter drops chunks of audio from BlackHole (vinyl-like crackle).

## Setup

    ./setup.sh

## Use

    .venv/bin/python -m mac_sonos list                      # find rooms
    .venv/bin/python -m mac_sonos run Office --volume 15    # stream to one room
    .venv/bin/python -m mac_sonos run Office "TV Room"      # grouped; first is leader
    .venv/bin/python -m mac_sonos run                       # rooms/volume/format from the saved config

While running, the Mac output is switched to BlackHole and its volume set to 100% (output restored on Ctrl-C).
Control volume on the Sonos, not the Mac: BlackHole uses the Mac volume as signal gain, so lowering it with
the volume keys makes the stream near-silent.

Options: `--format mp3|aac|flac|wav` (default mp3; aac/flac/wav are untested for latency, wav may not play),
`--lowlat` (low-latency ffmpeg input flags; no measurable gain), `--bitrate`, `--volume`.

## Latency benchmark (experimental)

    .venv/bin/python -m mac_sonos bench Office --runs 3

Measures the time from Sonos connecting to reporting PLAYING for each format. Results were inconsistent
(except mp3, ~6 s), so treat it as a rough probe, not a reliable latency measurement.

## Layout and tests

`mac_sonos/core` (capture, HTTP stream, session, Mac output), `mac_sonos/sonos` (discovery),
`controller.py` (state and commands for any UI), `config.py`, `cli.py`. See `PLAN.md` for the roadmap.

    .venv/bin/python -m unittest discover tests

Settings live in `~/Library/Application Support/mac-sonos/config.json` (rooms, volume, format, device, port).

## Notes

- Latency is 3-10 s (Sonos buffers radio streams). Fine for music, not for video or calls.
- If the capture device is not found, check the name with `python -m mac_sonos devices` and pass `--device`.
- Mac and Sonos must be on the same network; allow incoming connections for Python/ffmpeg if macOS asks.

## Run at login

    ./autostart.sh install      # adds a Terminal login item that runs run-at-login.command
    ./autostart.sh uninstall    # removes it and stops the stream

A launchd LaunchAgent does not work: macOS Local Network privacy blocks it ("No route to host" to the
Sonos), while Terminal has the permission. To change room/volume, edit `run-at-login.command`.
