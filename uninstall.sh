#!/usr/bin/env bash
set -euo pipefail

APP_NAME=usb-audio-transcriber
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now "$APP_NAME.timer" 2>/dev/null || true
systemctl --user disable --now "$APP_NAME-plug.path" 2>/dev/null || true
systemctl --user disable --now "$APP_NAME-panel.service" 2>/dev/null || true
rm -f "$UNIT_DIR/$APP_NAME.service" "$UNIT_DIR/$APP_NAME.timer" \
  "$UNIT_DIR/$APP_NAME-plug.service" "$UNIT_DIR/$APP_NAME-plug.path" \
  "$UNIT_DIR/$APP_NAME-panel.service"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$DATA_HOME/applications/$APP_NAME.desktop" \
  "$DATA_HOME/icons/hicolor/scalable/apps/$APP_NAME.svg"
systemctl --user daemon-reload
rm -rf "$INSTALL_ROOT/bin" "$INSTALL_ROOT/systemd" "$INSTALL_ROOT/prompts" \
  "$INSTALL_ROOT/panel" "$INSTALL_ROOT/share" "$INSTALL_ROOT/venv"
rm -f "$INSTALL_ROOT/VERSION"
rm -f "$INSTALL_ROOT/requirements.txt" "$INSTALL_ROOT/requirements-diarization.txt" \
  "$INSTALL_ROOT/config.example.env"
rmdir "$INSTALL_ROOT" 2>/dev/null || true
echo "Uninstalled program files and user systemd units. Local configuration, runtime state, archives, transcripts, and model caches were preserved."
