#!/usr/bin/env bash
set -euo pipefail
ROOT="${USB_AUDIO_TRANSCRIBER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCK_WAIT="${USB_AUDIO_TRANSCRIBER_LOCK_WAIT:-300}"
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
  "$ROOT/venv/bin/python" "$ROOT/bin/ingest.py"
  "$ROOT/venv/bin/python" "$ROOT/bin/progress-popup.py" \
    >>"$ROOT/var/logs/pipeline.log" 2>&1 9>&- &
  "$ROOT/venv/bin/python" "$ROOT/bin/transcribe.py"
} 2>&1 | tee -a "$ROOT/var/logs/pipeline.log"
