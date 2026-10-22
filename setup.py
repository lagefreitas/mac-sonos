"""py2app bundle: use ./build-app.sh rather than calling this directly."""
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).parent
LANGUAGES = sorted(p.stem for p in (HERE / "mac_sonos" / "locales").glob("*.json"))

PLIST = {
    "CFBundleName": "Mac Sonos",
    "CFBundleDisplayName": "Mac Sonos",
    "CFBundleIdentifier": "io.github.lagefreitas.mac-sonos",
    "CFBundleDevelopmentRegion": "en",
    "CFBundleLocalizations": LANGUAGES,   # the permission texts below are localized in bundle/<lang>.lproj
    "CFBundleVersion": "0.2.0",
    "CFBundleShortVersionString": "0.2.0",
    "LSUIElement": True,  # menu bar only, no Dock icon
    "LSMinimumSystemVersion": "13.0",
    "NSLocalNetworkUsageDescription": "Mac Sonos finds your Sonos speakers and streams the Mac's audio to them.",
    "NSBonjourServices": ["_sonos._tcp"],
    "NSMicrophoneUsageDescription": "Mac Sonos captures the Mac's audio output (through BlackHole) to send it to Sonos.",
}

setup(
    app=["app.py"],
    options={"py2app": {
        "plist": PLIST,
        "resources": [f"bundle/{lang}.lproj" for lang in LANGUAGES],
        "packages": ["mac_sonos", "soco", "sounddevice", "_sounddevice_data", "rumps", "ServiceManagement", "Quartz", "ApplicationServices"],
        "includes": ["cffi", "_cffi_backend"],
    }},
    setup_requires=["py2app"],
)
