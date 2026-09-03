#!/usr/bin/env python3
"""Diagnose USB Audio Transcriber configuration and installation problems."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_profiles import profiles_for_config
from pipeline_config import ROOT, load


PATH_SETTINGS = (
    "ARCHIVE_DIR",
    "QUEUE_DIR",
    "STATE_DB",
    "VAULT_DIR",
)
REQUIRED_SETTINGS = PATH_SETTINGS + ("AUDIO_EXTS",)


def check_config(config):
    """Return configuration failures that prevent the pipeline from running."""
    failures = [
        f"missing setting: {name}"
        for name in REQUIRED_SETTINGS
        if not config.get(name, "").strip()
    ]
    paths = {}
    for name in PATH_SETTINGS:
        value = config.get(name, "").strip()
        if not value:
            continue
        if not Path(value).is_absolute():
            failures.append(f"{name} must be an absolute path")
        paths.setdefault(os.path.normpath(value), []).append(name)
    for names in paths.values():
        if len(names) > 1:
            failures.append(f"{' and '.join(names)} must be distinct paths")
    try:
        profiles_for_config(config)
    except ValueError as exc:
        failures.append(f"invalid WHISPER_MODEL_PROFILE: {exc}")
    for name in ("PURGE_DEVICE", "VAD_ENABLED"):
        if config.get(name, "0").strip() not in {"0", "1"}:
            failures.append(f"{name} must be 0 or 1")
    for name, default in (
        ("VAD_MIN_SILENCE_MS", "1200"),
        ("MAP_WINDOW_CHARS", "80000"),
    ):
        try:
            valid = int(config.get(name, default).strip()) > 0
        except ValueError:
            valid = False
        if not valid:
            failures.append(f"{name} must be a positive integer")
    return failures


def writable_parent(path, is_file=False):
    """Return the nearest existing parent so checks do not create user data."""
    candidate = Path(path).expanduser()
    if is_file:
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def systemd_state(unit, operation):
    result = subprocess.run(
        ["systemctl", "--user", operation, unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.env")
    parser.add_argument(
        "--skip-systemd", action="store_true",
        help="skip user service/timer checks (useful before installation)",
    )
    args = parser.parse_args(argv)
    failures = []
    warnings = []

    try:
        config = load(args.config)
    except OSError as exc:
        failures.append(f"configuration: cannot read {args.config}: {exc}")
        config = {}
    else:
        config_failures = check_config(config)
        if config_failures:
            failures.extend(f"configuration: {failure}" for failure in config_failures)
        else:
            print(f"OK  configuration: {args.config}")

    for command in ("ffmpeg", "zenity", "flock", "tee"):
        location = shutil.which(command)
        if location:
            print(f"OK  command: {command} ({location})")
        else:
            failures.append(f"command: {command} not found")

    for package in ("faster_whisper", "requests"):
        if importlib.util.find_spec(package) is not None:
            print(f"OK  Python package: {package}")
        else:
            failures.append(f"Python package: {package} not importable")

    if config:
        for setting in PATH_SETTINGS:
            value = config.get(setting, "").strip()
            if not value:
                continue
            parent = writable_parent(value, is_file=setting == "STATE_DB")
            if parent is not None and os.access(parent, os.W_OK | os.X_OK):
                print(f"OK  writable path for {setting}: {parent}")
            else:
                failures.append(f"path: no writable parent for {setting}={value}")

    if not args.skip_systemd:
        if shutil.which("systemctl") is None:
            failures.append("command: systemctl not found")
        else:
            for operation in ("is-enabled", "is-active"):
                ok, detail = systemd_state(
                    "usb-audio-transcriber.timer", operation
                )
                if ok:
                    print(f"OK  timer {operation}: {detail or 'yes'}")
                else:
                    warnings.append(
                        f"timer {operation}: {detail or 'not available'}"
                    )

    for warning in warnings:
        print(f"WARN {warning}")
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        print(f"Doctor found {len(failures)} blocking problem(s).")
        return 1
    print("Doctor found no blocking problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
