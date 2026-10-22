# mac-sonos

Use old Sonos speakers (Play:1, Playbar, no AirPlay) as the Mac's speaker.

Mac output -> BlackHole -> ffmpeg (MP3) -> HTTP stream on the LAN -> Sonos plays it as a radio stream.

## Setup

    ./setup.sh

## Use

    .venv/bin/python mac_sonos.py list                      # find rooms
    .venv/bin/python mac_sonos.py run Office --volume 15    # stream to one room
    .venv/bin/python mac_sonos.py run Office "TV Room"      # grouped; first is leader

While running, the Mac output is switched to BlackHole (restored on Ctrl-C). Control volume on the Sonos,
not the Mac: BlackHole ignores the Mac volume keys.

## Notes

- Latency is 3-10 s (Sonos buffers radio streams). Fine for music, not for video or calls.
- If ffmpeg can't open the device, check the name with `mac_sonos.py devices` and pass `--device`.
- Mac and Sonos must be on the same network; allow incoming connections for Python/ffmpeg if macOS asks.

## Run at login

    ./autostart.sh install      # adds a Terminal login item that runs run-at-login.command
    ./autostart.sh uninstall    # removes it and stops the stream

A launchd LaunchAgent does not work: macOS Local Network privacy blocks it ("No route to host" to the
Sonos), while Terminal has the permission. To change room/volume, edit `run-at-login.command`.
