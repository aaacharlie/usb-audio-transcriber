#!/usr/bin/env bash
# One-command installer for USB Audio Transcriber:
#
#   curl -fsSL https://raw.githubusercontent.com/aaacharlie/usb-audio-transcriber/main/bootstrap.sh | bash
#
# Downloads (or updates) the source under the install root, then runs
# install.sh. Options are passed through, e.g.:
#
#   curl -fsSL .../bootstrap.sh | bash -s -- --with-diarization
set -euo pipefail

REPO="${USB_AUDIO_TRANSCRIBER_REPO:-https://github.com/aaacharlie/usb-audio-transcriber.git}"
BRANCH="${USB_AUDIO_TRANSCRIBER_BRANCH:-main}"
SRC="${USB_AUDIO_TRANSCRIBER_SRC:-${XDG_DATA_HOME:-$HOME/.local/share}/usb-audio-transcriber/src}"

command -v git >/dev/null || { echo "git is required; install it with: sudo apt install git" >&2; exit 1; }

if [ -d "$SRC/.git" ]; then
  echo "Updating $SRC"
  git -C "$SRC" fetch --quiet origin "$BRANCH"
  git -C "$SRC" checkout --quiet "$BRANCH"
  git -C "$SRC" merge --quiet --ff-only "origin/$BRANCH"
else
  echo "Downloading to $SRC"
  mkdir -p "$(dirname "$SRC")"
  git clone --quiet --branch "$BRANCH" "$REPO" "$SRC"
fi

# When piped from curl, stdin is the script itself. Hand the installer the
# terminal so the setup wizard can ask its questions; fall through otherwise.
if [ ! -t 0 ] && { exec 3</dev/tty; } 2>/dev/null; then
  exec bash "$SRC/install.sh" "$@" <&3
fi
exec bash "$SRC/install.sh" "$@"
