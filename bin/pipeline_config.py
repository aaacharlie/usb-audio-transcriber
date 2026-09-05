"""Shared config loader and runtime state helpers for the audio pipeline."""
import json
import os
from datetime import datetime
from pathlib import Path

# ASSETS holds the program files that ship together: bin/ (this file),
# prompts/, panel/, share/, systemd/. ROOT holds what belongs to the person:
# config.env and var/ (logs, state). install.sh keeps both in the same folder;
# a pipx install keeps the program inside the package and points ROOT at the
# data folder through USB_AUDIO_TRANSCRIBER_ROOT.
ASSETS = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("USB_AUDIO_TRANSCRIBER_ROOT", "").strip() or ASSETS).expanduser()
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


def version(version_file=None):
    """The program version: package metadata for a pipx install, else the VERSION
    file install.sh writes, else "dev"."""
    if (ASSETS / "__init__.py").is_file():  # running from inside the installed package
        try:
            from importlib.metadata import version as dist_version
            return dist_version("usb-audio-transcriber")
        except Exception:
            pass
    try:
        text = Path(version_file or ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return text or "dev"


def sync_directory(path):
    """Flush directory metadata so renames and unlinks survive power loss."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def has_display(environ=None):
    """Return whether a graphical session appears reachable from this process."""
    environ = os.environ if environ is None else environ
    return bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))


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
