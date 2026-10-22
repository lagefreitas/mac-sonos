# mac-sonos

Use old Sonos speakers (Play:1, Playbar, no AirPlay) as the Mac's speaker.

Mac output -> BlackHole -> PortAudio capture (sounddevice) -> ffmpeg (MP3) -> HTTP stream on the LAN -> Sonos plays it as a radio stream.

Capture uses PortAudio, not ffmpeg's avfoundation input: the latter drops chunks of audio from BlackHole (vinyl-like crackle).

## Setup

    ./setup.sh

## Menu bar app

    .venv/bin/python -m mac_sonos menubar

An icon in the menu bar (🔈 stopped, 🔄 connecting, 🔊 streaming, ⚠️ error) with: on/off, rooms (check several
to group; the first is the leader), Sonos volume in steps (changes live), stream format, "Ligar ao abrir"
(start streaming when the app opens), and Quit (stops the stream and restores the Mac output).
**Agora tocando:** every Sonos group that is playing or paused (not just ours) gets an entry with the track,
album, cover art, position and only the controls that group supports right now (previous, pause/play, next,
−10 s, +30 s; a stream like ours offers none). A summary line shows the track of the featured group, and
"Faixa na barra" puts it next to the icon. The state is refreshed from Sonos events (about 0.1 s) with polling
as a fallback. `python -m mac_sonos nowplaying` prints the same live view in the terminal.

**Casa:** every room of the house (not only the Mac's), with its volume and mute: +5 / −5, "Mudo", "Juntar a" another
group and "Sair do grupo"; grouped rooms also get a group volume row. "Juntar todos (modo festa)" joins every room
to the active group and "Separar todos" splits them. Stereo pairs show up as one room and are controlled through
their visible player. Changes made elsewhere (the Sonos app, physical buttons) show up within a few seconds.

**Interrompido:** if someone stops the Mac stream outside this app, the icon turns ⏸, the status says
"Interrompido no Sonos", and the main button becomes "Ligar" to start it again (nothing restarts by itself).

It warns if the Mac volume is not 100% and retries by itself every 10 s after an error (Wi-Fi/Sonos/BlackHole
not ready yet). Choices are saved in the config file below.

## Command line

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

## The app (Mac Sonos.app)

    ./build-app.sh         # builds ~/Applications/Mac Sonos.app, self-contained (no dependency on this folder)
    open -g ~/Applications/"Mac Sonos.app"

Open it once, then allow **Microphone** (it captures the BlackHole device) and, if asked, **Local Network**.
Enable "Abrir no login" in its menu (or `./autostart.sh install`) to start it at every login.

Notes:
- The app is signed with a local self-signed identity (`make-signing-identity.sh`, kept in its own keychain under
  `~/Library/Application Support/mac-sonos/signing`). An ad-hoc signature changes on every build, and macOS then
  forgets the permissions. With the stable identity you allow them once, and they survive rebuilds.
- It is a bundle (py2app) instead of running from this folder because the folder may live in iCloud Drive, which
  is protected and can evict files.
- Quit from the menu, or `pkill -TERM -f "Mac Sonos.app"`: that stops the stream and restores the Mac output.
  Never `kill -9`.
- Log: `~/Library/Logs/mac-sonos.log`. Debug commands run inside the bundle:
  `"~/Applications/Mac Sonos.app/Contents/MacOS/Mac Sonos" login-item status`.
- `./autostart.sh uninstall` removes the login item and stops the app.
