#!/usr/bin/env bash
set -euo pipefail

APP_NAME=usb-audio-transcriber
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now "$APP_NAME.timer" 2>/dev/null || true
rm -f "$UNIT_DIR/$APP_NAME.service" "$UNIT_DIR/$APP_NAME.timer"
systemctl --user daemon-reload
rm -rf "$INSTALL_ROOT"
echo "Uninstalled program files and user systemd units. Your archive, transcripts, and other configured data were not deleted."
