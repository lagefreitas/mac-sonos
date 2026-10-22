#!/bin/sh
# Start mac-sonos at login via a Terminal login item (Terminal already has the macOS
# "Local Network" permission that a launchd job lacks). Usage: autostart.sh install|uninstall
DIR="$(cd "$(dirname "$0")" && pwd)"
CMD="$DIR/run-at-login.command"
case "$1" in
  install)
    chmod +x "$CMD"
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$CMD\", hidden:true, name:\"mac-sonos\"}" ;;
  uninstall)
    osascript -e 'tell application "System Events" to delete login item "mac-sonos"'
    pkill -f "mac_sonos (run|menubar)"; pkill -f run-at-login.command ;;
  *) echo "usage: $0 install|uninstall"; exit 1 ;;
esac
