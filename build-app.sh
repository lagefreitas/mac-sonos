#!/bin/sh
# Builds a self-contained ~/Applications/Mac Sonos.app (code and libraries are copied into the bundle,
# so it does not depend on this folder, which may live in iCloud Drive).
set -e
cd "$(dirname "$0")"
DEST="$HOME/Applications"
mkdir -p "$DEST"
rm -rf build "$DEST/Mac Sonos.app"
.venv/bin/python setup.py py2app --dist-dir "$DEST" >/dev/null
rm -rf build
# A stable identity keeps the Microphone / Local Network permissions across rebuilds (ad-hoc does not).
./make-signing-identity.sh >/dev/null
KC="$HOME/Library/Application Support/mac-sonos/signing/build.keychain-db"
security unlock-keychain -p mac-sonos "$KC"
codesign --force --deep --sign "Mac Sonos Local Signing" --keychain "$KC" "$DEST/Mac Sonos.app"
echo "Built $DEST/Mac Sonos.app"
