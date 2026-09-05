#!/usr/bin/env bash
set -euo pipefail

APP_NAME=usb-audio-transcriber
SOURCE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Python 3.10 or newer is required" >&2
  exit 1
}
command -v ffmpeg >/dev/null || { echo "ffmpeg is required; install it with: sudo apt install ffmpeg" >&2; exit 1; }
command -v zenity >/dev/null || echo "zenity not found: the desktop progress window will be skipped (headless mode). Install it with: sudo apt install zenity" >&2
command -v flock >/dev/null || { echo "flock is required (usually provided by util-linux)" >&2; exit 1; }
command -v tee >/dev/null || { echo "tee is required (usually provided by coreutils)" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 1; }

mkdir -p "$INSTALL_ROOT" "$UNIT_DIR"

# Dependencies install and the doctor gate both run before deployed program
# files are replaced, so neither a pip failure nor a rejected configuration
# can leave an existing installation half upgraded.
python3 -m venv "$INSTALL_ROOT/venv"
"$INSTALL_ROOT/venv/bin/pip" install --upgrade pip
"$INSTALL_ROOT/venv/bin/pip" install -r "$SOURCE_ROOT/requirements.txt"

if [ ! -f "$INSTALL_ROOT/config.env" ]; then
  cp "$SOURCE_ROOT/config.example.env" "$INSTALL_ROOT/config.env"
  echo "Created $INSTALL_ROOT/config.env; edit it to select your output locations."
fi
chmod 600 "$INSTALL_ROOT/config.env"
"$INSTALL_ROOT/venv/bin/python" "$SOURCE_ROOT/bin/doctor.py" \
  --config "$INSTALL_ROOT/config.env" --skip-systemd

for directory in bin systemd prompts; do
  rm -rf "$INSTALL_ROOT/$directory"
  cp -a "$SOURCE_ROOT/$directory" "$INSTALL_ROOT/$directory"
done
cp "$SOURCE_ROOT/requirements.txt" "$INSTALL_ROOT/requirements.txt"
cp "$SOURCE_ROOT/config.example.env" "$INSTALL_ROOT/config.example.env"
chmod +x "$INSTALL_ROOT/bin/run-cycle.sh" "$INSTALL_ROOT/bin/model-cache.py" \
  "$INSTALL_ROOT/bin/benchmark-models.py" "$INSTALL_ROOT/bin/doctor.py" \
  "$INSTALL_ROOT/bin/sessions.py" "$INSTALL_ROOT/bin/notify.py"

# systemd expands % specifiers in unit files, and an unquoted patsub
# replacement expands & on bash 5.2+, so escape/quote both.
rendered_root="${INSTALL_ROOT//\%/%%}"
for unit in "$APP_NAME.service" "$APP_NAME-plug.service"; do
  template=$(<"$SOURCE_ROOT/systemd/$unit")
  printf '%s\n' "${template//@INSTALL_ROOT@/"$rendered_root"}" > "$UNIT_DIR/$unit"
done
cp "$SOURCE_ROOT/systemd/$APP_NAME.timer" "$UNIT_DIR/$APP_NAME.timer"
cp "$SOURCE_ROOT/systemd/$APP_NAME-plug.path" "$UNIT_DIR/$APP_NAME-plug.path"
systemctl --user daemon-reload
systemctl --user enable --now "$APP_NAME.timer"
systemctl --user enable --now "$APP_NAME-plug.path"

cat <<EOF
Installed $APP_NAME to $INSTALL_ROOT
Timer status: systemctl --user status $APP_NAME.timer
Plug-in trigger: systemctl --user status $APP_NAME-plug.path
Edit settings: $INSTALL_ROOT/config.env
EOF
