#!/usr/bin/env bash
set -euo pipefail
ROOT="${USB_AUDIO_TRANSCRIBER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
mkdir -p "$ROOT/var/logs" "$ROOT/var/state"
exec 9>"$ROOT/var/state/cycle.lock"
flock -n 9 || { echo "cycle already running, skipping"; exit 0; }
{
  "$ROOT/venv/bin/python" "$ROOT/bin/ingest.py"
  "$ROOT/venv/bin/python" "$ROOT/bin/progress-popup.py" &
  "$ROOT/venv/bin/python" "$ROOT/bin/transcribe.py"
} 2>&1 | tee -a "$ROOT/var/logs/pipeline.log"
