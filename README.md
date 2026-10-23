# mac-sonos

Use old Sonos speakers (Play:1, Playbar, no AirPlay) as the Mac's speaker, and control the whole Sonos from the menu bar.

**Site: https://lagefreitas.github.io/mac-sonos/** (what it is, a tour of the menu, install steps; English and Portuguese).

Mac audio -> capture (a Core Audio system audio tap, or the BlackHole virtual device) -> ffmpeg (MP3/AAC) -> HTTP stream on the LAN -> Sonos plays it as a radio stream. See "Where the audio comes from" below for the two capture sources.

The BlackHole capture reads through PortAudio, not ffmpeg's avfoundation input: the latter drops chunks of audio from BlackHole (vinyl-like crackle).

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

## Where the audio comes from: BlackHole or the system audio tap

"Captura" in the menu (or `run --capture blackhole|tap`, `capture` in the config) picks the source:

- **BlackHole** (default): the Mac's output is switched to the BlackHole virtual device and read like a microphone.
  macOS shows its microphone indicator, and the Mac's volume is the signal level, so it must stay at 100% (the app warns).
- **System audio (no BlackHole)**: a Core Audio process tap listens to what the Mac plays (macOS 14.2+). Nothing is
  switched and BlackHole is not needed; the Mac's volume does not matter (the tap hears the audio before the volume);
  with "Silenciar o Mac ao transmitir" (on by default) the Mac stays silent while it streams, like with BlackHole.
  It needs the **System Audio Recording** permission (System Settings > Privacy & Security > Screen & System Audio
  Recording). The first time, macOS shows its permission dialog by itself when the capture starts and holds the audio
  back until you answer; after "Allow" it takes a moment to apply, so the app tries again a few times before giving up.
  If it is refused or unanswered the status line says so, and you can add "Mac Sonos" to that list by hand.

The tap is a private Core Audio tap and aggregate device (nothing appears in the system's device lists) read through an
IOProc; the sample rate comes from Core Audio. Asking for the permission uses a private system API (`TCC.framework`),
the only one that exists for it, so a future macOS may change that part. Diagnostics, run inside the bundle:
`tap-test --permission`, `tap-test --source`, `tap-test --ioproc`. To try a build without touching the app you use,
make a side-by-side bundle (it needs its own permissions):
`DEST=/some/dir MAC_SONOS_APP_NAME="Mac Sonos Test" MAC_SONOS_BUNDLE_ID=io.github.lagefreitas.mac-sonos.test ./build-app.sh`

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

The menu comes in Portuguese (`pt`), English (`en`), Spanish (`es`) and French (`fr`). "Idioma / Language" in the
menu offers **Automatic** (follows the first of your macOS preferred languages that the app has, else English) or a
fixed language, saved as `language` in the config; each language is named in its own tongue so it can always be found.
Note that Automatic uses macOS's *first* language: with English first and Portuguese second, the menu is in English;
pick Português to pin it. The Microphone and Local Network permission texts macOS shows are translated too
(`bundle/<lang>.lproj/InfoPlist.strings`, copied into the app by `build-app.sh`).

All UI text lives in `mac_sonos/locales/<code>.json` and is read with `t("key", name=value)` (`mac_sonos/i18n.py`);
nothing in the UI modules may contain text of its own. To add a language: copy `pt.json` to `<code>.json`, translate
the values (keep the keys and every `{placeholder}`), set `meta.language_name` to the language's own name, add
`bundle/<code>.lproj/InfoPlist.strings` with the two permission texts, and run `python -m unittest discover tests`.
The tests check that the new catalog has the same keys and placeholders as `pt.json`, that nothing was left untranslated
by accident (values equal to Portuguese must be listed on purpose in `tests/test_translations.py`), that menu entries
stay narrow, that no raw key or `{placeholder}` reaches any menu in any language, and that no UI text is hard-coded.
Lookup falls back to English, then Portuguese; a missing key shows the key and is logged once.

## The site (GitHub Pages)

`docs/` is the site, served by GitHub Pages from the `main` branch. `docs/index.html` is generated: edit
`tools/site/template.html` and run `.venv/bin/python tools/build_site.py`. The menu tour on the page is the real menu of
the app, rendered headless with made-up rooms in every language, so it changes when the menu changes; a test fails if
the committed page is out of date. The author's name and LinkedIn link live in `tools/site/author.json` (an empty
`linkedin` leaves the link out); the share image shown in link previews is `docs/og.png`, rendered from
`tools/site/og.html` by `tools/build_og.sh` (needs Google Chrome).

Articles live in `articles/*.md` and are listed in `tools/site/articles.json`; the generator converts them with
`tools/minimd.py`, a small Markdown converter that refuses what it does not support (tables, block quotes, raw HTML...)
instead of rendering it wrongly. To add one: write the Markdown, add an entry to `articles.json`, run the generator.
`articles/tap_demo.py` is the runnable script from the first article (system audio capture from Python with a Core Audio
tap), and a test keeps it identical to the code shown on the page. The generator also writes `docs/sitemap.xml` and
`docs/robots.txt`.

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

## About

Made by André Lage-Freitas. Most of the code was written by [Claude Code](https://claude.com/claude-code) (Claude Sonnet 5.5)
under the author's direction; the author decided what to build and tested every step on their own speakers.

## License

[MIT](LICENSE). Mac Sonos is an independent project, not affiliated with Sonos, Inc.; "Sonos" is their trademark.

What it builds on keeps its own licenses. The Python libraries it uses are permissively licensed (SoCo, sounddevice,
PyObjC and py2app: MIT; rumps: BSD). `ffmpeg` (GPL-3.0-or-later in the Homebrew build), BlackHole (see its authors' site,
existential.audio/blackhole) and SwitchAudioSource (MIT) are installed separately by `setup.sh` and run as separate
programs; they are not part of this repository or of the app bundle. If you ever redistribute a bundle that includes
ffmpeg or BlackHole, their licenses apply to what you ship.

## TODO

Not started; see `PLAN.md` for the reasoning behind each.

- **Control panel window (sprint 9).** One window (`pywebview`) with the whole house at a glance: rooms, groups, cover
  art, queue and scenes, for when the menu gets cramped. Only worth it if the menu proves too narrow in daily use.
- **Sonos cloud API and search (sprint 10).** Register an integration at Sonos Developer, local OAuth with the token
  in the Keychain, then search and play artists and albums from the music services (what the Sonos MCP connector does),
  and reach the favorites the local API cannot play (service hubs, artists). Only if the favorites turn out not to be
  enough.

### From the code review (October 2026)

Phase 1 of the review is done (stalled stream handler, volume errors, scene regrouping, long queues, atomic config,
retry back-off and log rotation, silent tap). What is left:

**Needs testing on the speakers**

- **"Interrompido" must not stop other people's music.** `Session.stop` (`core/session.py`) calls `stop()` and
  `unjoin()` without checking that the Sonos still plays our stream, so quitting, a scene with `"stream": "off"` or the
  end of the sleep timer stops what someone put on the Office from the Sonos app. Changing room, format or capture
  while interrupted (`ui/menubar.py`) restarts the stream over it too. Stop only if the leader's URI is ours, unjoin
  only rooms still in its group, and while interrupted only save those choices.
- **Restore the Mac's volume.** `OutputSwitch` (`core/output.py`) sets it to 100 for BlackHole and never puts it back,
  so the Mac's speakers or headphones are left at 100%. Save it on enter, restore it on exit (and remember it next to
  `previous_output` in case the app is killed).
- **Event subscriptions after the Mac sleeps.** A failed renewal leaves a dead `Subscription` (`is_subscribed` false,
  not reusable) in `Monitor._subs`, and nothing subscribes again, so the app falls back to polling until it is reopened.
  Drop and redo subscriptions that are no longer subscribed.
- **A leader inside another group.** SoCo's `play_uri` only works on a coordinator: if the Office is grouped under
  another room the stream fails at every retry. In `Session.start`, `unjoin()` the leader first when it is not a
  coordinator.
- **Scene with the stream on while already streaming.** `wait_stream` (`scenes.py`) is satisfied by the old session,
  so per-room volumes are sent before the restart and then overwritten by `Session.start`. Wait for a new session
  (a generation counter or `started_at` in the controller's state).
- **Start the tap in silence.** A tap delivers nothing until something plays, so in a silent Mac the stream starts
  only on a later retry (`TapSilent`). With the permission granted, accept the tap without audio and send silence to
  the Sonos until sound arrives; check that the Sonos accepts a stream that starts silent.

**Later**

- **Who can listen.** The stream is served on `0.0.0.0:8000` to any client and path, one ffmpeg per connection; with the
  tap that is all of the Mac's audio. Use a random path per session (keep recognizing `/mac.mp3` in
  `model.is_mac_stream` for saved scenes), 404 for anything else, a limit on connections, optionally only player IPs.
- **Tap callback work.** The float-to-int16 conversion runs in Core Audio's real-time IOProc and competes for the GIL;
  move it to a thread if the tap ever glitches. A change of the aggregate's sample rate (the Mac's output switched) is
  not followed either.
- **Clean-up.** The "EXPERIMENT" notes in `core/tapcapture.py` and the `tap-test` help; pin `soco`, `rumps` and
  `pyobjc-*` in `requirements.txt`; read the Mac's volume (`osascript`) off the main thread; make `scenes.validate`
  report hand-edited `rooms`/`source` that are not objects instead of raising; prune old album art from the cache.
