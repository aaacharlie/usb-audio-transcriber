#!/usr/bin/env bash
set -euo pipefail

APP_NAME=usb-audio-transcriber
SOURCE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg is required; install it with: sudo apt install ffmpeg" >&2; exit 1; }
command -v zenity >/dev/null || { echo "zenity is required; install it with: sudo apt install zenity" >&2; exit 1; }

mkdir -p "$INSTALL_ROOT" "$UNIT_DIR"
for directory in bin systemd; do
  rm -rf "$INSTALL_ROOT/$directory"
  cp -a "$SOURCE_ROOT/$directory" "$INSTALL_ROOT/$directory"
done
cp "$SOURCE_ROOT/requirements.txt" "$INSTALL_ROOT/requirements.txt"
cp "$SOURCE_ROOT/config.example.env" "$INSTALL_ROOT/config.example.env"
if [ ! -f "$INSTALL_ROOT/config.env" ]; then
  cp "$SOURCE_ROOT/config.example.env" "$INSTALL_ROOT/config.env"
  echo "Created $INSTALL_ROOT/config.env; edit it to select your output locations."
fi

python3 -m venv "$INSTALL_ROOT/venv"
"$INSTALL_ROOT/venv/bin/pip" install --upgrade pip
"$INSTALL_ROOT/venv/bin/pip" install -r "$INSTALL_ROOT/requirements.txt"
chmod +x "$INSTALL_ROOT/bin/run-cycle.sh" "$INSTALL_ROOT/bin/model-cache.py" \
  "$INSTALL_ROOT/bin/benchmark-models.py"

cp "$SOURCE_ROOT/systemd/$APP_NAME.service" "$UNIT_DIR/$APP_NAME.service"
cp "$SOURCE_ROOT/systemd/$APP_NAME.timer" "$UNIT_DIR/$APP_NAME.timer"
systemctl --user daemon-reload
systemctl --user enable --now "$APP_NAME.timer"

cat <<EOF
Installed $APP_NAME to $INSTALL_ROOT
Timer status: systemctl --user status $APP_NAME.timer
Edit settings: $INSTALL_ROOT/config.env
EOF
