"""Stream formats and the ffmpeg command that encodes them."""

# name -> (ffmpeg codec args, content type, default bitrate, play as mp3 radio)
FORMATS = {
    "mp3": (["-c:a", "libmp3lame", "-f", "mp3"], "audio/mpeg", "192k", True),
    "aac": (["-c:a", "aac", "-f", "adts"], "audio/aac", "256k", True),
    "flac": (["-c:a", "flac", "-compression_level", "0", "-f", "flac"], "audio/flac", None, False),
    "wav": (["-c:a", "pcm_s16le", "-f", "wav"], "audio/wav", None, False),
}

DIDL = (
    '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
    'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/">'
    '<item id="R:0/0/0" parentID="R:0/0" restricted="true"><dc:title>Mac</dc:title>'
    "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
    '<res protocolInfo="http-get:*:{mime}:*">{url}</res></item></DIDL-Lite>'
)

RATE = 48000  # BlackHole's native rate


def ffmpeg_cmd(cfg):
    codec, _, default_br, _ = FORMATS[cfg["fmt"]]
    cmd = ["ffmpeg", "-loglevel", "error"]
    if cfg["lowlat"]:
        cmd += ["-fflags", "+nobuffer", "-flags", "+low_delay", "-probesize", "32"]
    cmd += ["-f", "s16le", "-ar", str(RATE), "-ac", "2", "-i", "pipe:0", "-ar", "44100"]
    bitrate = cfg["bitrate"] or default_br
    if bitrate:
        cmd += ["-b:a", bitrate]
    return cmd + codec + ["-flush_packets", "1", "pipe:1"]
