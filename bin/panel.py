#!/usr/bin/env python3
"""The control panel: a small local web app over the same scripts you can run
in a terminal.

    panel.py serve   run the server (usb-audio-transcriber-panel.service does this)
    panel.py open    make sure the server is running, then open the panel as its
                     own window (Chrome, Chromium, Brave, or Edge) or in the browser
    panel.py url     print the private link, for another device on your network

Every button in the panel maps to a script under bin/. The server listens on
this machine only unless PANEL_BIND says otherwise, and every request needs
the private token created at first start.
"""
import argparse
import json
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor as doctor_module
import setup as setup_module
from llm import backend_choice, backend_from_config
from model_profiles import artifact_path, artifacts_complete, cache_path_for, \
    directory_size, hub_cache_root, profiles_for, profiles_for_config
from pipeline_config import ROOT, load, log, read_progress, version

CFG_PATH = ROOT / "config.env"
BIN = Path(__file__).resolve().parent
PYTHON = sys.executable
PAGE = ROOT / "panel" / "index.html"
TOKEN_FILE = ROOT / "var" / "state" / "panel-token"
LOG_FILE = ROOT / "var" / "logs" / "pipeline.log"
VERSION_FILE = ROOT / "VERSION"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8765
UNITS = {
    "timer": "usb-audio-transcriber.timer",
    "plug": "usb-audio-transcriber-plug.path",
    "service": "usb-audio-transcriber.service",
    "panel": "usb-audio-transcriber-panel.service",
}
SECRET_KEYS = ("OPENROUTER_API_KEY", "LLM_API_KEY", "HF_TOKEN")
# Browsers that can show a page as a plain window: no tabs, no address bar.
APP_WINDOW_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                       "brave-browser", "microsoft-edge", "microsoft-edge-stable", "vivaldi",
                       "vivaldi-stable")
SECRET_PLACEHOLDER = "********"
ALLOWED_NOTE_SUFFIXES = {".md", ".txt", ".json"}

# The settings form is generated from this. Every key in config.example.env
# should appear here so nothing needs hand-editing.
SETTINGS = [
    {"title": "Where things go", "fields": [
        {"key": "VAULT_DIR", "label": "Notes folder", "type": "path",
         "help": "Transcript and session notes are written here. A folder inside your Obsidian vault works best."},
        {"key": "ARCHIVE_DIR", "label": "Archive folder", "type": "path",
         "help": "Checksum-verified copies of every recording."},
        {"key": "QUEUE_DIR", "label": "Queue folder", "type": "path",
         "help": "Recordings waiting to be transcribed."},
        {"key": "STATE_DB", "label": "State database", "type": "path",
         "help": "Deduplication, sessions, and the search index."},
    ]},
    {"title": "Recorder and folders", "fields": [
        {"key": "RECORDER_DIR", "label": "Folder name on the recorder", "type": "text",
         "help": "Only audio directly inside a folder with this name is imported from removable media."},
        {"key": "AUDIO_EXTS", "label": "Audio file types", "type": "text",
         "help": "Comma-separated, without dots."},
        {"key": "WATCH_DIRS", "label": "Extra folders to watch", "type": "text",
         "help": "Colon-separated absolute paths, scanned recursively. Files here are never deleted."},
        {"key": "PURGE_DEVICE", "label": "Delete recordings from the recorder after a verified copy", "type": "bool",
         "help": "Off keeps every source file on the device."},
    ]},
    {"title": "Transcription", "fields": [
        {"key": "WHISPER_MODEL_PROFILE", "label": "Model", "type": "choice",
         "choices": ["fast", "accurate", "both"],
         "help": "fast is distil-large-v3, accurate is large-v3, both runs an A/B comparison."},
        {"key": "WHISPER_LANG", "label": "Language code", "type": "text",
         "help": "For example en. Empty means detect automatically."},
        {"key": "WHISPER_TASK", "label": "Task", "type": "choice",
         "choices": ["transcribe", "translate"],
         "help": "translate turns other languages straight into English."},
        {"key": "WHISPER_DEVICE", "label": "Device", "type": "text", "help": "cpu, or cuda with a compatible setup."},
        {"key": "WHISPER_COMPUTE", "label": "Compute type", "type": "text", "help": "int8 on CPU."},
        {"key": "VAD_ENABLED", "label": "Skip silence (voice activity detection)", "type": "bool"},
        {"key": "VAD_MIN_SILENCE_MS", "label": "Silence threshold (ms)", "type": "int"},
    ]},
    {"title": "AI summaries", "fields": [
        {"key": "SUMMARY_BACKEND", "label": "How summaries are made", "type": "choice",
         "choices": ["", "none", "command", "openai", "openrouter"],
         "help": "command: a tool you already pay for (Codex, Claude Code, Gemini CLI). openai: a local Ollama model. openrouter: pay per use. Empty: OpenRouter if a key is set, otherwise none."},
        {"key": "SUMMARY_COMMAND", "label": "Command (for the command backend)", "type": "text",
         "help": "The prompt arrives on stdin; the reply is read from stdout or from {output_file}. Use full paths."},
        {"key": "SUMMARY_COMMAND_TIMEOUT", "label": "Command timeout (seconds)", "type": "int"},
        {"key": "LLM_BASE_URL", "label": "Server URL (for the openai backend)", "type": "text",
         "help": "Ollama's default is http://127.0.0.1:11434/v1"},
        {"key": "LLM_MODEL", "label": "Model name (for the openai backend)", "type": "text",
         "help": "For example llama3.1:8b, pulled with ollama pull."},
        {"key": "LLM_API_KEY", "label": "Server API key (optional)", "type": "secret"},
        {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API key", "type": "secret"},
        {"key": "OPENROUTER_MODEL", "label": "OpenRouter model", "type": "text"},
        {"key": "SESSION_SUMMARY_MODEL", "label": "Model for session summaries (optional)", "type": "text",
         "help": "Pick a stronger model for whole-session summaries; empty uses the backend's usual model."},
        {"key": "SESSION_SUBJECT", "label": "What your recordings are about", "type": "text",
         "help": "Goes into the summary prompt so the model can fix misheard jargon and names."},
        {"key": "FILE_SUMMARY", "label": "Summary at the top of every transcript note", "type": "bool"},
        {"key": "SESSION_SUMMARY", "label": "Summarize sessions automatically", "type": "bool"},
        {"key": "SESSION_PROMPT_FILE", "label": "Custom prompt file (optional)", "type": "path",
         "help": "Must contain {subject}. Empty uses the bundled prompt."},
        {"key": "MAP_WINDOW_CHARS", "label": "Characters per summary window", "type": "int"},
    ]},
    {"title": "Session notes", "fields": [
        {"key": "SESSION_NOTES", "label": "Write session notes", "type": "bool"},
        {"key": "SESSION_GAP_MIN", "label": "Minutes of silence that start a new session", "type": "int"},
        {"key": "SESSION_BACKFILL_DAYS", "label": "Skip automatic summaries for sessions older than (days)", "type": "text",
         "help": "Protects you from summarizing months of history at once. Empty summarizes everything."},
    ]},
    {"title": "Speaker labels", "fields": [
        {"key": "DIARIZATION", "label": "Label speakers (needs the optional install)", "type": "bool"},
        {"key": "HF_TOKEN", "label": "Hugging Face token", "type": "secret"},
        {"key": "DIARIZATION_MODEL", "label": "Diarization model", "type": "text"},
        {"key": "DIARIZATION_MIN_SPEAKERS", "label": "Minimum speakers (optional)", "type": "text"},
        {"key": "DIARIZATION_MAX_SPEAKERS", "label": "Maximum speakers (optional)", "type": "text"},
    ]},
    {"title": "Desktop and panel", "fields": [
        {"key": "HEADLESS", "label": "Progress window", "type": "choice", "choices": ["auto", "0", "1"],
         "help": "auto shows it when a desktop is available, 1 never, 0 always."},
        {"key": "NOTIFY", "label": "Desktop notifications", "type": "choice", "choices": ["auto", "0", "1"]},
        {"key": "PANEL_BIND", "label": "Panel listens on", "type": "text",
         "help": "127.0.0.1 keeps it on this machine. 0.0.0.0 allows other devices on your network (the private link still applies)."},
        {"key": "PANEL_PORT", "label": "Panel port", "type": "int"},
    ]},
]
KNOWN_KEYS = {field["key"] for section in SETTINGS for field in section["fields"]}
# What the pipeline assumes when a key is absent from config.env; the form shows
# these so an untouched field is never written back as an empty value.
DEFAULTS = {
    'VAULT_DIR': '${HOME}/usb-audio-transcriber-data/transcripts',
    'ARCHIVE_DIR': '${HOME}/usb-audio-transcriber-data/archive',
    'QUEUE_DIR': '${HOME}/usb-audio-transcriber-data/queue',
    'STATE_DB': '${HOME}/usb-audio-transcriber-data/state/seen.sqlite',
    'RECORDER_DIR': 'RECORD',
    'AUDIO_EXTS': 'mp3,wav,m4a',
    'WATCH_DIRS': '',
    'PURGE_DEVICE': '0',
    'WHISPER_MODEL_PROFILE': 'fast',
    'WHISPER_LANG': 'en',
    'WHISPER_TASK': 'transcribe',
    'WHISPER_DEVICE': 'cpu',
    'WHISPER_COMPUTE': 'int8',
    'VAD_ENABLED': '1',
    'VAD_MIN_SILENCE_MS': '1200',
    'SUMMARY_BACKEND': '',
    'SUMMARY_COMMAND': '',
    'SUMMARY_COMMAND_TIMEOUT': '900',
    'LLM_BASE_URL': 'http://127.0.0.1:11434/v1',
    'LLM_MODEL': '',
    'LLM_API_KEY': '',
    'OPENROUTER_API_KEY': '',
    'OPENROUTER_MODEL': 'anthropic/claude-haiku-4.5',
    'SESSION_SUMMARY_MODEL': '',
    'SESSION_SUBJECT': '',
    'FILE_SUMMARY': '1',
    'SESSION_SUMMARY': '1',
    'SESSION_PROMPT_FILE': '',
    'MAP_WINDOW_CHARS': '80000',
    'SESSION_NOTES': '1',
    'SESSION_GAP_MIN': '20',
    'SESSION_BACKFILL_DAYS': '7',
    'DIARIZATION': '0',
    'HF_TOKEN': '',
    'DIARIZATION_MODEL': 'pyannote/speaker-diarization-3.1',
    'DIARIZATION_MIN_SPEAKERS': '',
    'DIARIZATION_MAX_SPEAKERS': '',
    'HEADLESS': 'auto',
    'NOTIFY': 'auto',
    'PANEL_BIND': '127.0.0.1',
    'PANEL_PORT': '8765',
}
assert set(DEFAULTS) == KNOWN_KEYS


# --------------------------------------------------------------------------- config

def raw_config(path=None):
    """KEY=value pairs exactly as written, without variable expansion."""
    path = CFG_PATH if path is None else Path(path)
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def config_for_form():
    raw = raw_config()
    values = {}
    for key in KNOWN_KEYS:
        value = raw.get(key, DEFAULTS[key])
        values[key] = (SECRET_PLACEHOLDER if value else "") if key in SECRET_KEYS else value
    return {"settings": SETTINGS, "values": values, "path": str(CFG_PATH)}


def save_config(updates):
    """Validate with the doctor, then rewrite only the changed lines. Returns failures."""
    raw = raw_config()
    cleaned = {}
    for key, value in (updates or {}).items():
        if key not in KNOWN_KEYS:
            continue
        value = ("" if value is None else str(value)).strip()
        if key in SECRET_KEYS and value == SECRET_PLACEHOLDER:
            continue
        current = raw.get(key, DEFAULTS[key])
        if key in SECRET_KEYS and key not in raw and value == "":
            continue
        if value == current:
            continue  # untouched: never rewrite a default into the file
        cleaned[key] = value
    if not cleaned:
        return []
    trial = CFG_PATH.with_name("config.env.panel-check")
    shutil.copy2(CFG_PATH, trial)
    try:
        setup_module.write_config(trial, cleaned)
        failures = doctor_module.check_config(load(trial))
    finally:
        trial.unlink(missing_ok=True)
    if failures:
        return failures
    setup_module.write_config(CFG_PATH, cleaned)
    return []


# --------------------------------------------------------------------------- state

def systemctl_available():
    return shutil.which("systemctl") is not None


def unit_state(unit):
    if not systemctl_available():
        return None
    state = {}
    for operation in ("is-active", "is-enabled"):
        try:
            result = subprocess.run(["systemctl", "--user", operation, unit],
                                    capture_output=True, text=True, check=False, timeout=10)
            state[operation.split("-")[1]] = result.stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            state[operation.split("-")[1]] = "unknown"
    return state


def open_db():
    config = load()
    path = Path(config["STATE_DB"])
    if not path.exists():
        return None
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def count(con, sql):
    try:
        return con.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def status():
    config = load()
    progress = read_progress()
    queue = Path(config["QUEUE_DIR"])
    exts = {"." + e.strip().lower() for e in config.get("AUDIO_EXTS", "").split(",") if e.strip()}
    queued = sum(1 for p in queue.iterdir() if p.suffix.lower() in exts) if queue.is_dir() else 0
    try:
        import ingest
        detected = len(ingest.find_candidates())
    except Exception:  # discovery must never break the panel
        detected = None
    con = open_db()
    counts = {"recordings": 0, "sessions": 0, "summarized": 0}
    if con is not None:
        with con:
            counts = {
                "recordings": count(con, "SELECT COUNT(*) FROM seen"),
                "sessions": count(con, "SELECT COUNT(*) FROM sessions"),
                "summarized": count(con, "SELECT COUNT(*) FROM sessions WHERE summarized=1"),
            }
        con.close()
    root = hub_cache_root()
    models = []
    for profile in profiles_for("both"):
        path = cache_path_for(profile, root)
        models.append({"key": profile.key, "model": profile.model_id,
                       "cached": path.exists(),
                       "gib": round(directory_size(path) / 1024 ** 3, 2) if path.exists() else 0})
    try:
        usage = shutil.disk_usage(config["ARCHIVE_DIR"] if Path(config["ARCHIVE_DIR"]).exists() else ROOT)
        disk = {"free_gib": round(usage.free / 1024 ** 3, 1), "total_gib": round(usage.total / 1024 ** 3, 1)}
    except OSError:
        disk = None
    backend = backend_choice(config)
    return {
        "version": version(VERSION_FILE),
        "progress": progress,
        "queued": queued,
        "detected": detected,
        "units": {name: unit_state(unit) for name, unit in UNITS.items()},
        "systemd": systemctl_available(),
        "counts": counts,
        "models": models,
        "disk": disk,
        "summaries": {"backend": backend, "ready": backend_from_config(config) is not None,
                      "subject": config.get("SESSION_SUBJECT", "")},
        "profile": config.get("WHISPER_MODEL_PROFILE", "fast"),
        "vault": config["VAULT_DIR"],
        "config_path": str(CFG_PATH),
        "now": datetime.now().isoformat(timespec="seconds"),
    }


def note_summary_model(note):
    """Read summary_model from a note's front matter without loading the whole file."""
    try:
        with open(note, encoding="utf-8") as handle:
            for _ in range(20):
                line = handle.readline()
                if line.startswith("summary_model:"):
                    return line.split(":", 1)[1].strip()
                if line.strip() == "---" and _ > 0:
                    break
    except OSError:
        pass
    return None


def sessions():
    con = open_db()
    if con is None:
        return []
    rows = []
    try:
        for row in con.execute(
            "SELECT id, started_at, ended_at, members, note, summarized, created_at "
            "FROM sessions ORDER BY started_at DESC"
        ):
            note = Path(row["note"]) if row["note"] else None
            rows.append({
                "id": row["id"], "started_at": row["started_at"], "ended_at": row["ended_at"],
                "recordings": len(json.loads(row["members"])),
                "note": str(note) if note else None,
                "note_name": note.stem if note else None,
                "note_exists": bool(note and note.exists()),
                "summarized": bool(row["summarized"]),
                "summary_model": note_summary_model(note) if note and note.exists() else None,
                "created_at": row["created_at"],
            })
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return rows


def recordings(limit=40):
    config = load()
    profiles = profiles_for_config(config)
    profile, comparison = profiles[-1], len(profiles) > 1
    con = open_db()
    if con is None:
        return []
    items = []
    try:
        rows = con.execute(
            "SELECT orig_name, archived_to, imported_at, transcribed FROM seen "
            "ORDER BY imported_at DESC LIMIT ?", (limit,)
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    for row in rows:
        audio = Path(row["archived_to"] or "")
        complete = audio.is_file() and artifacts_complete(audio, profile, comparison)
        note = None
        if complete:
            try:
                note = json.loads(artifact_path(audio, profile, ".complete.json", comparison)
                                  .read_text(encoding="utf-8")).get("note")
            except (OSError, json.JSONDecodeError):
                note = None
        items.append({"name": row["orig_name"], "audio": str(audio), "imported_at": row["imported_at"],
                      "complete": complete, "note": note,
                      "note_name": Path(note).stem if note else None})
    return items


def allowed_path(candidate):
    """Only notes and sidecars under the vault or the archive may be read or opened."""
    config = load()
    try:
        target = Path(candidate).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    roots = [Path(config["VAULT_DIR"]).resolve(), Path(config["ARCHIVE_DIR"]).resolve()]
    if not any(root == target or root in target.parents for root in roots):
        return None
    if target.is_dir():
        return target
    if target.suffix.lower() not in ALLOWED_NOTE_SUFFIXES or not target.is_file():
        return None
    return target


def search(params):
    words = params.get("q", [""])[0].split()
    if not words:
        return []
    command = [PYTHON, str(BIN / "search.py"), "--json", "--limit", "100"]
    since = params.get("since", [""])[0].strip()
    speaker = params.get("speaker", [""])[0].strip()
    if since:
        command += ["--since", since]
    if speaker:
        command += ["--speaker", speaker]
    command += words
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode == 2:
        raise RuntimeError(result.stderr.strip() or "search failed")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        return []


def log_tail(lines=200):
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# --------------------------------------------------------------------------- jobs

class Jobs:
    """Background commands started from the panel, with their output kept for the page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}
        self.counter = 0

    def start(self, kind, params):
        command, label = job_command(kind, params or {})
        with self.lock:
            self.counter += 1
            ident = self.counter
            job = {"id": ident, "kind": kind, "label": label, "status": "running",
                   "started": datetime.now().isoformat(timespec="seconds"),
                   "finished": None, "returncode": None, "output": ""}
            self.items[ident] = job
        threading.Thread(target=self._run, args=(job, command), daemon=True).start()
        return job

    def _run(self, job, command):
        # Output is published line by line so the page shows a long job (a model
        # download, a summary) progressing instead of a bare "Running...".
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, errors="replace",
                                       cwd=ROOT)
            with process.stdout:
                for line in process.stdout:
                    with self.lock:
                        job["output"] = (job["output"] + line)[-6000:]
            code = process.wait()
        except Exception as exc:  # the page must always learn what happened
            with self.lock:
                job["output"] = (job["output"] + f"{type(exc).__name__}: {exc}")[-6000:]
            code = -1
        with self.lock:
            job["returncode"] = code
            job["status"] = "done" if code == 0 else "failed"
            job["finished"] = datetime.now().isoformat(timespec="seconds")

    def snapshot(self):
        with self.lock:
            return sorted(self.items.values(), key=lambda j: j["id"], reverse=True)[:50]


def script(name):
    return [PYTHON, str(BIN / name)]


def job_command(kind, params):
    """Translate a panel action into the exact command a person could type."""
    backend = params.get("backend") or ""
    if backend and backend not in ("command", "openai", "openrouter"):
        raise ValueError("unknown backend")
    extra = ["--backend", backend] if backend else []
    if kind == "summarize":
        ids = [str(i) for i in params.get("ids", []) if str(i).strip()]
        if not ids:
            raise ValueError("choose at least one session")
        command = script("sessions.py") + ["summarize"]
        for ident in ids:
            command += ["--id", ident]
        return command + extra, f"Summarize {len(ids)} session(s)"
    if kind == "retry":
        return script("sessions.py") + ["retry"] + extra, "Summarize sessions without a summary"
    if kind == "rebuild":
        date = str(params.get("date", "")).strip()
        datetime.strptime(date, "%Y-%m-%d")
        return script("sessions.py") + ["rebuild", "--date", date], f"Rebuild sessions for {date}"
    if kind == "test-backend":
        return script("sessions.py") + ["test-backend"] + extra, "Test the summary backend"
    if kind == "index":
        return script("search.py") + ["--index"], "Refresh the search index"
    if kind == "doctor":
        return script("doctor.py"), "Run the doctor"
    if kind == "model-cache":
        action, profile = params.get("action"), params.get("profile", "both")
        if action not in ("status", "download", "remove") or profile not in ("fast", "accurate", "both"):
            raise ValueError("unknown model cache action")
        return script("model-cache.py") + [action, profile], f"Model cache: {action} {profile}"
    if kind == "cycle":
        if systemctl_available():
            return ["systemctl", "--user", "start", UNITS["service"]], "Run a cycle now"
        return ["bash", str(BIN / "run-cycle.sh")], "Run a cycle now"
    if kind == "timer":
        action = params.get("action")
        if action not in ("pause", "resume") or not systemctl_available():
            raise ValueError("pause and resume need systemd")
        verb = "stop" if action == "pause" else "start"
        return (["systemctl", "--user", verb, UNITS["timer"], UNITS["plug"]],
                "Pause automatic runs" if action == "pause" else "Resume automatic runs")
    raise ValueError(f"unknown job kind {kind}")


JOBS = Jobs()


# --------------------------------------------------------------------------- server

def token():
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(value + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    return value


def bind_settings(config=None):
    config = load() if config is None else config
    host = config.get("PANEL_BIND", "").strip() or DEFAULT_BIND
    try:
        port = int(config.get("PANEL_PORT", "").strip() or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return host, port


class Handler(BaseHTTPRequestHandler):
    server_version = "usb-audio-transcriber-panel"

    def log_message(self, format, *args):  # keep the journal quiet
        pass

    # -- helpers
    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_page(self, html, status=HTTPStatus.OK, cookie=None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"panel_token={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def presented_token(self):
        header = self.headers.get("X-Panel-Token")
        if header:
            return header.strip()
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "panel_token":
                return value.strip()
        return ""

    def authorized(self):
        presented = self.presented_token()
        return bool(presented) and secrets.compare_digest(presented, self.server.token)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    # -- routes
    def do_GET(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        if url.path == "/health":
            return self.send_json({"ok": True})
        if url.path == "/":
            supplied = params.get("token", [""])[0]
            if supplied and secrets.compare_digest(supplied, self.server.token):
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"panel_token={supplied}; HttpOnly; SameSite=Strict; Path=/")
                self.end_headers()
                return None
            if not self.authorized():
                return self.send_page(LOCKED_PAGE, HTTPStatus.UNAUTHORIZED)
            return self.send_page(PAGE.read_text(encoding="utf-8"))
        if not url.path.startswith("/api/"):
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if not self.authorized():
            return self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        try:
            if url.path == "/api/status":
                return self.send_json(status())
            if url.path == "/api/sessions":
                return self.send_json(sessions())
            if url.path == "/api/recordings":
                return self.send_json(recordings())
            if url.path == "/api/note":
                target = allowed_path(params.get("path", [""])[0])
                if target is None or target.is_dir():
                    return self.send_json({"error": "that file is not a note of this pipeline"},
                                          HTTPStatus.FORBIDDEN)
                return self.send_json({"path": str(target), "text": target.read_text(encoding="utf-8")})
            if url.path == "/api/search":
                return self.send_json(search(params))
            if url.path == "/api/config":
                return self.send_json(config_for_form())
            if url.path == "/api/jobs":
                return self.send_json(JOBS.snapshot())
            if url.path == "/api/log":
                return self.send_json({"text": log_tail(int(params.get("lines", ["200"])[0]))})
            if url.path == "/api/vaults":
                return self.send_json([str(v) for v in setup_module.find_vaults()])
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        url = urlsplit(self.path)
        if not self.authorized():
            return self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        if self.headers.get("X-Requested-With") != "panel":
            return self.send_json({"error": "missing X-Requested-With"}, HTTPStatus.FORBIDDEN)
        body = self.read_json()
        try:
            if url.path == "/api/config":
                failures = save_config(body.get("values", {}))
                if failures:
                    return self.send_json({"ok": False, "failures": failures}, HTTPStatus.BAD_REQUEST)
                return self.send_json({"ok": True})
            if url.path == "/api/jobs":
                job = JOBS.start(body.get("kind", ""), body.get("params", {}))
                return self.send_json(job, HTTPStatus.ACCEPTED)
            if url.path == "/api/open":
                target = allowed_path(body.get("path", ""))
                if target is None:
                    return self.send_json({"error": "not allowed"}, HTTPStatus.FORBIDDEN)
                opener = shutil.which("xdg-open")
                if not opener:
                    return self.send_json({"ok": False, "error": "xdg-open is not available here"})
                subprocess.Popen([opener, str(target)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, start_new_session=True)
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)


LOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>USB Audio Transcriber</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222}
code{background:#eee;padding:.1rem .3rem;border-radius:.2rem}</style>
<h1>This panel needs its private link</h1>
<p>Open it from the USB Audio Transcriber entry in your app menu, or run
<code>bin/panel.py open</code> from the installation folder. For another device on your
network, <code>bin/panel.py url</code> prints the link.</p>"""


def make_server(host, port):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.token = token()
    return server


def panel_url(host=None, port=None, for_network=False):
    config_host, config_port = bind_settings()
    host = host or config_host
    port = port or config_port
    if host in ("0.0.0.0", "::") or for_network:
        host = lan_address() if host in ("0.0.0.0", "::", "") else host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}/?token={token()}"


def lan_address():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def is_up(port, host="127.0.0.1"):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as reply:
            return reply.status == 200
    except Exception:
        return False


def serve(argv):
    host, port = bind_settings()
    server = make_server(host, port)
    log(f"Panel listening on http://{host}:{port}/ (private link: panel.py url)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def app_window_command(url):
    """The command that shows the panel as its own window, or None without such a browser."""
    for name in APP_WINDOW_BROWSERS:
        found = shutil.which(name)
        if found:
            return [found, f"--app={url}", "--window-size=1180,840",
                    "--class=usb-audio-transcriber"]
    return None


def open_panel(argv):
    import webbrowser
    host, port = bind_settings()
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if not is_up(port, probe_host):
        subprocess.Popen([PYTHON, str(Path(__file__).resolve()), "serve"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        for _ in range(50):
            if is_up(port, probe_host):
                break
            time.sleep(0.1)
    url = f"http://{probe_host}:{port}/?token={token()}"
    print(url)
    if argv.no_browser:
        return 0
    command = None if argv.browser else app_window_command(url)
    if command:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
    else:
        webbrowser.open(url)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the panel server in the foreground")
    opener = sub.add_parser("open", help="start the server if needed and open the panel")
    opener.add_argument("--no-browser", action="store_true", help="only print the link")
    opener.add_argument("--browser", action="store_true",
                        help="open a browser tab even when a browser could show it as a window")
    sub.add_parser("url", help="print the private link (network address when PANEL_BIND allows it)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args)
    if args.command == "open":
        return open_panel(args)
    print(panel_url(for_network=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
