"""Shared config loader and runtime state helpers for the audio pipeline."""
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_PATH = ROOT / "var" / "state" / "progress.json"


def load(path=None):
    cfg = {}
    path = ROOT / "config.env" if path is None else Path(path)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        value = os.path.expandvars(val.strip().strip('"').strip("'"))
        cfg[key.strip()] = os.path.expanduser(value)
    return cfg


def sync_directory(path):
    """Flush directory metadata so renames and unlinks survive power loss."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
