#!/bin/sh
# Open Mac Sonos at login. Usage: autostart.sh install|uninstall
# (The app can do the same from its menu: "Abrir no login".)
APP="$HOME/Applications/Mac Sonos.app"
BIN="$APP/Contents/MacOS/Mac Sonos"
case "$1" in
  install)
    [ -x "$BIN" ] || { echo "Build the app first: ./build-app.sh"; exit 1; }
    "$BIN" login-item enable ;;
  uninstall)
    [ -x "$BIN" ] && "$BIN" login-item disable
    # login item of the old Terminal-based setup, if still there
    osascript -e 'tell application "System Events" to delete login item "run-at-login.command"' 2>/dev/null
    pkill -f "Mac Sonos.app"; pkill -f "mac_sonos (run|menubar)"; pkill -f run-at-login.command ;;
  *) echo "usage: $0 install|uninstall"; exit 1 ;;
esac
