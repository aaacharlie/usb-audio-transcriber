#!/usr/bin/env bash
set -euo pipefail
# ROOT holds config.env and var/. With install.sh the program lives there too
# (bin/ and venv/); a pipx install passes its own interpreter and bin/ folder.
ROOT="${USB_AUDIO_TRANSCRIBER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BIN="${USB_AUDIO_TRANSCRIBER_BIN:-$ROOT/bin}"
PYTHON="${USB_AUDIO_TRANSCRIBER_PYTHON:-$ROOT/venv/bin/python}"
LOCK_WAIT="${USB_AUDIO_TRANSCRIBER_LOCK_WAIT:-300}"
export USB_AUDIO_TRANSCRIBER_ROOT="$ROOT"
mkdir -p "$ROOT/var/logs" "$ROOT/var/state"
exec 9>"$ROOT/var/state/cycle.lock"
if [ "${1:-}" = "--wait" ]; then
  # A plug-in trigger waits for a running cycle instead of skipping, so the
  # recordings that were just mounted are picked up by this run.
  flock -w "$LOCK_WAIT" 9 || { echo "cycle still running after ${LOCK_WAIT}s, skipping"; exit 0; }
else
  flock -n 9 || { echo "cycle already running, skipping"; exit 0; }
fi
{
  "$PYTHON" "$BIN/ingest.py"
  "$PYTHON" "$BIN/progress-popup.py" \
    >>"$ROOT/var/logs/pipeline.log" 2>&1 9>&- &
  "$PYTHON" "$BIN/transcribe.py"
  "$PYTHON" "$BIN/sessions.py"
  "$PYTHON" "$BIN/search.py" --index
} 2>&1 | tee -a "$ROOT/var/logs/pipeline.log"
