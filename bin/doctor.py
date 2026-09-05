#!/usr/bin/env python3
"""Diagnose USB Audio Transcriber configuration and installation problems."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
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
    for name in ("PURGE_DEVICE", "VAD_ENABLED", "SESSION_NOTES", "SESSION_SUMMARY",
                 "FILE_SUMMARY", "DIARIZATION"):
        if config.get(name, "0").strip() not in {"0", "1"}:
            failures.append(f"{name} must be 0 or 1")
    for name in ("HEADLESS", "NOTIFY"):
        if config.get(name, "auto").strip().lower() not in {"auto", "0", "1"}:
            failures.append(f"{name} must be auto, 0, or 1")
    # An empty value means the default, exactly as bin/transcribe.py treats it.
    task = config.get("WHISPER_TASK", "transcribe").strip() or "transcribe"
    if task not in {"transcribe", "translate"}:
        failures.append("WHISPER_TASK must be transcribe or translate")
    for name, default in (
        ("VAD_MIN_SILENCE_MS", "1200"),
        ("MAP_WINDOW_CHARS", "80000"),
        ("SESSION_GAP_MIN", "20"),
        ("SUMMARY_COMMAND_TIMEOUT", "900"),
    ):
        try:
            valid = int(config.get(name, default).strip()) > 0
        except ValueError:
            valid = False
        if not valid:
            failures.append(f"{name} must be a positive integer")
    if config.get("DIARIZATION", "0").strip() == "1" and not config.get("HF_TOKEN", "").strip():
        failures.append(
            "HF_TOKEN is required when DIARIZATION=1 (pyannote models are gated on "
            "Hugging Face)"
        )
    for name in ("DIARIZATION_MIN_SPEAKERS", "DIARIZATION_MAX_SPEAKERS"):
        value = config.get(name, "").strip()
        if value:
            try:
                valid = int(value) > 0
            except ValueError:
                valid = False
            if not valid:
                failures.append(f"{name} must be a positive integer or empty")
    backend = config.get("SUMMARY_BACKEND", "").strip().lower()
    if backend not in {"", "none", "openrouter", "openai", "command"}:
        failures.append("SUMMARY_BACKEND must be none, openrouter, openai, or command")
    if backend == "openrouter" and not config.get("OPENROUTER_API_KEY", "").strip():
        failures.append("SUMMARY_BACKEND=openrouter needs OPENROUTER_API_KEY")
    if backend == "openai":
        if not config.get("LLM_MODEL", "").strip():
            failures.append("SUMMARY_BACKEND=openai needs LLM_MODEL (for example llama3.1:8b)")
        base_url = config.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1").strip()
        if base_url and not base_url.startswith(("http://", "https://")):
            failures.append("LLM_BASE_URL must start with http:// or https://")
    if backend == "command" and not config.get("SUMMARY_COMMAND", "").strip():
        failures.append("SUMMARY_BACKEND=command needs SUMMARY_COMMAND")
    backfill = config.get("SESSION_BACKFILL_DAYS", "7").strip()
    if backfill:
        try:
            valid = int(backfill) > 0
        except ValueError:
            valid = False
        if not valid:
            failures.append("SESSION_BACKFILL_DAYS must be a positive integer or empty")
    prompt_file = config.get("SESSION_PROMPT_FILE", "").strip()
    if prompt_file and not Path(prompt_file).expanduser().is_file():
        failures.append(f"SESSION_PROMPT_FILE is not a readable file: {prompt_file}")
    for entry in watch_dirs(config):
        if not entry.is_absolute():
            failures.append(f"WATCH_DIRS entry must be an absolute path: {entry}")
        elif any(
            entry == Path(config.get(name, "")).expanduser()
            for name in PATH_SETTINGS if config.get(name, "").strip()
        ):
            failures.append(
                f"WATCH_DIRS entry must not be one of the pipeline's own paths: {entry}"
            )
    return failures


def watch_dirs(config):
    """Expand the optional colon-separated WATCH_DIRS setting."""
    return [
        Path(os.path.expandvars(os.path.expanduser(entry.strip())))
        for entry in config.get("WATCH_DIRS", "").split(":")
        if entry.strip()
    ]


def check_watch_dirs(config):
    """Return warnings for watch folders that are not currently available."""
    return [
        f"WATCH_DIRS folder is not a directory right now: {entry}"
        for entry in watch_dirs(config)
        if entry.is_absolute() and not entry.is_dir()
    ]


def writable_parent(path, is_file=False):
    """Return the nearest existing parent so checks do not create user data."""
    candidate = Path(path).expanduser()
    if is_file:
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def fts5_warning():
    """Transcript search needs an SQLite built with FTS5; most are."""
    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        finally:
            probe.close()
    except sqlite3.OperationalError:
        return ("SQLite has no FTS5 support: transcript search (bin/search.py) "
                "is unavailable on this Python")
    return None


def linger_warning(user=None):
    """Explain that user timers stop at logout, which matters on headless machines."""
    if shutil.which("loginctl") is None:
        return None
    user = user or os.environ.get("USER") or ""
    if not user:
        return None
    result = subprocess.run(
        ["loginctl", "show-user", user, "--property=Linger", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "no":
        return None
    return (
        f"lingering is off for {user}: the timer only runs while you are logged in. "
        f"For a headless machine or Raspberry Pi run: loginctl enable-linger {user}"
    )


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

    for command in ("ffmpeg", "flock", "tee"):
        location = shutil.which(command)
        if location:
            print(f"OK  command: {command} ({location})")
        else:
            failures.append(f"command: {command} not found")
    for command, purpose in (
        ("zenity", "desktop progress window"),
        ("notify-send", "desktop notifications"),
    ):
        location = shutil.which(command)
        if location:
            print(f"OK  command: {command} ({location})")
        else:
            warnings.append(
                f"command: {command} not found; the {purpose} will be skipped"
            )

    for package in ("faster_whisper", "requests"):
        if importlib.util.find_spec(package) is not None:
            print(f"OK  Python package: {package}")
        else:
            failures.append(f"Python package: {package} not importable")
    fts = fts5_warning()
    if fts:
        warnings.append(fts)
    if config.get("DIARIZATION", "0").strip() == "1":
        try:
            found = importlib.util.find_spec("pyannote.audio") is not None
        except (ImportError, ValueError):
            found = False
        if found:
            print("OK  Python package: pyannote.audio")
        else:
            failures.append(
                "Python package: pyannote.audio not importable "
                "(run ./install.sh --with-diarization)"
            )

    if config:
        warnings.extend(check_watch_dirs(config))
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
            for kind, unit in (
                ("timer", "usb-audio-transcriber.timer"),
                ("plug-in trigger", "usb-audio-transcriber-plug.path"),
            ):
                for operation in ("is-enabled", "is-active"):
                    ok, detail = systemd_state(unit, operation)
                    if ok:
                        print(f"OK  {kind} {operation}: {detail or 'yes'}")
                    else:
                        warnings.append(
                            f"{kind} {operation}: {detail or 'not available'}"
                        )
            linger = linger_warning()
            if linger:
                warnings.append(linger)

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
