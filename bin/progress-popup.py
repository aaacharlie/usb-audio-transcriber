#!/usr/bin/env python3
"""Show Linux desktop progress for the current USB audio transcription."""
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import has_display, load, read_progress


POLL_SECONDS = 1
STARTUP_TIMEOUT = 8
STALE_TIMEOUT = 20 * 60


def format_eta(seconds):
    if seconds is None:
        return "Estimating time remaining…"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    if minutes:
        return f"About {minutes}m {seconds:02d}s remaining"
    return f"About {seconds}s remaining"


def message(state):
    phase = state.get("phase", "Starting")
    detected = state.get("detected_files", 0)
    imported = state.get("imported_files", 0)
    total = state.get("total_files", 0)
    completed = state.get("files_completed", 0)
    current = state.get("current_file")
    lines = [phase]
    if detected:
        lines.append(f"USB: {detected} audio file(s) detected; {imported} new")
    if total:
        lines.append(f"Files: {completed}/{total} complete")
    if current:
        lines.append(f"Current: {current}")
    if state.get("active") and phase.startswith("Transcribing"):
        lines.append(format_eta(state.get("eta_seconds")))
    return "\n".join(lines)


def desktop_available(config=None, environ=None):
    """Decide whether a progress window can and should be shown at all."""
    headless = (config or {}).get("HEADLESS", "auto").strip().lower()
    if headless == "1":
        return False
    if shutil.which("zenity") is None:
        return False
    if headless == "0":
        return True
    return has_display(environ)


def main():
    try:
        config = load()
    except OSError:
        config = {}
    if not desktop_available(config):
        return 0
    deadline = time.monotonic() + STARTUP_TIMEOUT
    state = read_progress()
    while not state.get("active") and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        state = read_progress()
    if not state.get("active"):
        return 0

    zenity = subprocess.Popen(
        ["zenity", "--progress", "--title=USB Audio Transcription",
         "--text=Starting…", "--percentage=0", "--no-cancel"],
        stdin=subprocess.PIPE, text=True,
    )
    last_update = time.monotonic()
    last_marker = None
    try:
        while zenity.poll() is None:
            state = read_progress()
            marker = state.get("updated_at")
            if marker != last_marker:
                last_marker = marker
                last_update = time.monotonic()
            percent = int(state.get("current_percent") or 0)
            total = int(state.get("total_files") or 0)
            completed = int(state.get("files_completed") or 0)
            if total:
                percent = int(((completed + percent / 100) / total) * 100)
            zenity.stdin.write(f"{min(100, percent)}\n#{message(state)}\n")
            zenity.stdin.flush()
            if not state.get("active"):
                time.sleep(4)
                break
            if time.monotonic() - last_update > STALE_TIMEOUT:
                break
            time.sleep(POLL_SECONDS)
    except (BrokenPipeError, OSError):
        pass
    finally:
        if zenity.poll() is None:
            zenity.terminate()
        try:
            zenity.wait(timeout=2)
        except subprocess.TimeoutExpired:
            zenity.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
