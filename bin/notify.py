#!/usr/bin/env python3
"""Desktop notifications for finished transcripts, with click-to-open."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import has_display, load

APP_NAME = "USB Audio Transcriber"
ICON = "audio-input-microphone"
WAIT_TIMEOUT = 30 * 60  # seconds a click-to-open helper may wait for a click
_ACTIONS_SUPPORTED = None


def enabled(config=None, environ=None):
    """Decide whether notifications can and should be sent."""
    config = config or {}
    setting = config.get("NOTIFY", "auto").strip().lower()
    if setting == "0" or config.get("HEADLESS", "auto").strip() == "1":
        return False
    if shutil.which("notify-send") is None:
        return False
    if setting == "1":
        return True
    return has_display(environ)


def supports_actions():
    """notify-send gained --action and --wait in libnotify 0.8 (2022)."""
    global _ACTIONS_SUPPORTED
    if _ACTIONS_SUPPORTED is None:
        try:
            result = subprocess.run(
                ["notify-send", "--help"], capture_output=True, text=True, check=False
            )
            _ACTIONS_SUPPORTED = "--action" in result.stdout
        except OSError:
            _ACTIONS_SUPPORTED = False
    return _ACTIONS_SUPPORTED


def base_command(title, body):
    return ["notify-send", f"--app-name={APP_NAME}", f"--icon={ICON}", title, body]


def detach(command):
    """Start a process that outlives the pipeline without inheriting its
    pipes or the run-cycle lock descriptor (close_fds), so a lingering
    notification can neither keep tee open nor block the next cycle."""
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def send(title, body, open_path=None, config=None, environ=None):
    """Send a notification. Clicking it opens open_path when the desktop allows."""
    if not enabled(config, environ):
        return False
    if open_path is not None and supports_actions():
        detach([
            sys.executable, str(Path(__file__).resolve()),
            "--wait", "--open", str(open_path), title, body,
        ])
        return True
    detach(base_command(title, body))
    return True


def wait_and_open(title, body, open_path):
    """Helper mode: show the notification, wait for a click, then open the path."""
    command = base_command(title, body) + ["--action=default=Open", "--wait"]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=WAIT_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.stdout.strip() == "default" and open_path is not None:
        opener = shutil.which("xdg-open")
        if opener:
            detach([opener, str(open_path)])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", action="store_true",
                        help="helper mode: wait for a click, then open --open")
    parser.add_argument("--open", type=Path, help="file or folder to open on click")
    parser.add_argument("title")
    parser.add_argument("body")
    args = parser.parse_args(argv)
    if args.wait:
        return wait_and_open(args.title, args.body, args.open)
    try:
        config = load()
    except OSError:
        config = {}
    send(args.title, args.body, args.open, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
