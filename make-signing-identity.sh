#!/bin/sh
# Creates a local, self-signed code-signing identity in its own keychain, so that Mac Sonos.app keeps the
# same code identity across rebuilds. (An ad-hoc signature changes with every build, and macOS then forgets
# the Microphone / Local Network permissions.) Nothing is added to your login keychain or to trust settings.
set -e
DIR="$HOME/Library/Application Support/mac-sonos/signing"
KC="$DIR/build.keychain-db"
NAME="Mac Sonos Local Signing"
PW="mac-sonos"   # protects a throwaway local key; it only signs this app
[ -f "$KC" ] && { echo "Signing identity already exists: $KC"; exit 0; }
mkdir -p "$DIR"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$NAME" \
  -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
  -addext "basicConstraints=critical,CA:false" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
openssl pkcs12 -export -inkey "$DIR/key.pem" -in "$DIR/cert.pem" -out "$DIR/id.p12" -passout "pass:$PW" \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1   # what `security import` can read
security create-keychain -p "$PW" "$KC"
security set-keychain-settings "$KC"            # never auto-lock
security unlock-keychain -p "$PW" "$KC"
security import "$DIR/id.p12" -k "$KC" -P "$PW" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple: -s -k "$PW" "$KC" >/dev/null
rm -f "$DIR/key.pem" "$DIR/id.p12"
echo "Created signing identity '$NAME' in $KC"
