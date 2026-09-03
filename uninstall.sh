#!/usr/bin/env bash
set -euo pipefail

APP_NAME=usb-audio-transcriber
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now "$APP_NAME.timer" 2>/dev/null || true
rm -f "$UNIT_DIR/$APP_NAME.service" "$UNIT_DIR/$APP_NAME.timer"
systemctl --user daemon-reload
rm -rf "$INSTALL_ROOT/bin" "$INSTALL_ROOT/systemd" "$INSTALL_ROOT/venv"
rm -f "$INSTALL_ROOT/requirements.txt" "$INSTALL_ROOT/config.example.env"
rmdir "$INSTALL_ROOT" 2>/dev/null || true
echo "Uninstalled program files and user systemd units. Local configuration, runtime state, archives, transcripts, and model caches were preserved."
