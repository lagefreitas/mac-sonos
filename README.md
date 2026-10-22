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

**Som:** each room has a "Som" submenu with only what it supports: bass, treble and loudness (all rooms), and on
soundbars night mode, speech enhancement ("Realce de diálogo"), surround and "Áudio da TV" (switches the soundbar to
its TV input; if it was in a group, it leaves it). Line-in is not offered: the API cannot tell which players have one.

**Biblioteca:** "Tocar em: <cômodo>" chooses the group everything below plays on (the Mac's first room by default).
"Favoritos" lists your Sonos favorites by kind (radio stations, tracks, playlists; items the local API cannot play,
such as service hubs or artists without a URI, are left out). "Playlists do Sonos" plays a Sonos playlist, "Fila" shows
the group's queue (a window of 40 around the current track) with play and remove per track and "Limpar fila".
"Rádios por URL" plays any http(s) MP3/AAC stream and keeps a saved list. Playing a track or playlist replaces
the queue, like "play now" in the Sonos app; radios start from the Sonos side and can take several seconds.
Playing on a room that carries the Mac's stream replaces it (the icon turns ⏸; "Ligar" brings it back).

**Soneca:** every group that is playing has a "Soneca" submenu (15 to 120 min, with the time left and "Cancelar").
Normal groups use the speakers' own sleep timer. The Mac's own stream gets an app-side timer instead, because the
Sonos one would cut it abruptly: the volume fades out over the last 30 s, the stream stops cleanly (the Mac output
is restored) and the rooms' volume is put back. Cancelling during the fade undoes it. This timer lives in the app: it
is lost if the app quits.

**Alarmes:** list, create ("Novo alarme…" asks for HH:MM and uses the room chosen in "Tocar em"), turn on/off,
change the time, repeat (every day, weekdays, weekends, once; custom weekday sets made in the Sonos app are shown too)
and volume, remove. Alarms live in the speakers, play the Sonos chime and fire even if the Mac is off. They cannot
play the Mac's audio.

**Cenas:** "Salvar cena atual…" records the house as it is: grouping, every room's volume, mute and sound settings,
whether the Mac streams (and to which rooms), and a TV or URL-radio source if one is playing. Choosing a scene brings
it back: stream off, regroup, start the stream, then volumes, mute, sound settings and source, waiting where a step
depends on the previous one. A scene is plain JSON under `scenes` in the config file, so it can be edited by hand
(for example to add a favorite as the source):

    {"name": "Cinema", "stream": "off", "groups": [["TV Room"], ["Office"]],
     "rooms": {"TV Room": {"volume": 25, "mute": false, "dialog_mode": true, "surround": true}},
     "source": {"type": "tv", "room": "TV Room"}}

`stream` is `on` (with `stream_rooms`), `off` or `keep`; `groups` is the exact grouping or `null` to leave it alone;
a room may set `volume`, `mute`, `bass`, `treble`, `loudness`, `night_mode`, `dialog_mode`, `surround`; `source` is
`{"type": "tv"|"favorite"|"url", "room": ..., "title"/"url": ...}`. Invalid scenes are refused with a message.

**Teclas de mídia controlam o Sonos** (off by default; needs the Accessibility permission): while the Mac streams,
the volume and mute keys act on the Sonos group that plays the stream, and the Mac's own volume (the signal level
feeding it) stays at 100%. Play/pause, next and previous act on the group chosen in "Tocar em" when something real
plays there; for the Mac's own stream they still go to the Mac (YouTube, Spotify...), which is what a stream has no
transport for. With nothing playing on the Sonos every key goes to macOS as usual. Turn it on from the menu; macOS
asks to allow Mac Sonos in System Settings > Privacy & Security > Accessibility, and the option starts working by
itself once you do. `"Mac Sonos" media-keys` (in `Contents/MacOS`) says whether the permission is in place.

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

## Interface language

All UI text lives in `mac_sonos/locales/<code>.json` and is read with `t("key", name=value)` (`mac_sonos/i18n.py`);
nothing in the UI modules may contain text of its own. Portuguese (`pt`) is the base. To add a language:
copy `pt.json` to `<code>.json`, translate the values (keep the keys and every `{placeholder}`), and set
`meta.language_name` to the language's own name. `python -m unittest discover tests` checks that the new file has the
same keys and placeholders as `pt.json` and that no UI text is hard-coded. With two or more catalogs the menu gets an
"Idioma" submenu (Automático follows the system language; the choice is saved as `language` in the config).
Lookup falls back to English, then Portuguese; a missing key shows the key and is logged once.

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
