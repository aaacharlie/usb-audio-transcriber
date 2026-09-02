"""Shared config loader and runtime state helpers for the audio pipeline."""
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_PATH = ROOT / "var" / "state" / "progress.json"


def load():
    cfg = {}
    for line in (ROOT / "config.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        cfg[key.strip()] = os.path.expandvars(val.strip().strip('"').strip("'"))
    return cfg


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def read_progress():
    """Return the last progress update, tolerating a missing/corrupt state file."""
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_progress(**update):
    """Atomically publish progress for the desktop status window."""
    state = read_progress()
    state.update(update)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROGRESS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace(PROGRESS_PATH)
