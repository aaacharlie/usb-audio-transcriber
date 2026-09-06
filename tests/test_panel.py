import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config


def load_panel():
    spec = importlib.util.spec_from_file_location("panel_for_test", ROOT / "bin" / "panel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_SCRIPT = """import json, sys
from pathlib import Path
Path(sys.argv[0]).with_suffix(".argv").write_text(json.dumps(sys.argv[1:]))
print("fake ran")
"""
FAKE_SEARCH = """import json, sys
from pathlib import Path
Path(sys.argv[0]).with_suffix(".argv").write_text(json.dumps(sys.argv[1:]))
print(json.dumps([{"audio": "/a/x.wav", "note": "/v/2026-09-05 0930 transcript.md",
                   "segment_index": 0, "start_seconds": 12.0, "recorded_at": "2026-09-05T09:30:00",
                   "speaker": "Speaker 1", "text": "the [roof] [leak]"}]))
"""


class PanelFixture:
    """A temp installation: config, vault, archive, state database, fake scripts."""

    def __init__(self, directory, **extra):
        self.root = Path(directory)
        self.vault = self.root / "vault"
        self.archive = self.root / "archive"
        self.queue = self.root / "queue"
        self.state = self.root / "state" / "seen.sqlite"
        for d in (self.vault, self.archive, self.queue, self.state.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root / "config.env"
        lines = {
            "ARCHIVE_DIR": str(self.archive), "QUEUE_DIR": str(self.queue),
            "STATE_DB": str(self.state), "VAULT_DIR": str(self.vault),
            "AUDIO_EXTS": "wav", "WHISPER_MODEL_PROFILE": "fast",
            "OPENROUTER_API_KEY": "sk-secret", "SESSION_SUBJECT": "",
        }
        lines.update(extra)
        self.config_path.write_text(
            "# test config\n" + "".join(f'{k}="{v}"\n' for k, v in lines.items()), encoding="utf-8")
        self.config_path.chmod(0o600)
        self.bin = self.root / "fakebin"
        self.bin.mkdir()
        for name in ("sessions.py", "search.py", "doctor.py", "model-cache.py"):
            (self.bin / name).write_text(FAKE_SEARCH if name == "search.py" else FAKE_SCRIPT,
                                         encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as con:
            con.execute("CREATE TABLE seen (sha256 TEXT PRIMARY KEY, orig_name TEXT, archived_to TEXT, "
                        "bytes INTEGER, imported_at TEXT, transcribed INTEGER DEFAULT 0)")
            con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
                        "members TEXT, note TEXT, summarized INTEGER DEFAULT 0, created_at TEXT)")
            con.commit()

    def add_session(self, ident="abc123", summarized=1, model="openrouter:m"):
        note = self.vault / "2026-09-05 0900 session.md"
        note.write_text(f"---\ndate: 2026-09-05\nsummary_model: {model}\n---\n\n# Session\n\n## Summary\n\nGreat.\n",
                        encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as con:
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                        (ident, "2026-09-05T09:00:00", "2026-09-05T10:10:00", json.dumps(["d1", "d2"]),
                         str(note), summarized, "2026-09-05T10:11:00"))
            con.commit()
        return note

    def add_recording(self, complete=True):
        audio = self.archive / "20260905-093000_rec1.wav"
        audio.write_bytes(b"audio" * 10)
        note = self.vault / "2026-09-05 0930 transcript.md"
        note.write_text("# note", encoding="utf-8")
        if complete:
            for suffix, body in ((".json", "{}"), (".txt", "hi"),
                                 (".complete.json", json.dumps({"status": "complete", "note": str(note)}))):
                (self.archive / f"{audio.name}{suffix}").write_text(body, encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as con:
            con.execute("INSERT INTO seen VALUES (?,?,?,?,?,?)",
                        ("d1", "rec1.wav", str(audio), 50, "2026-09-05T09:31:00", 1))
            con.commit()
        return audio, note


class PanelServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = PanelFixture(self.tmp.name)
        self.panel = load_panel()
        real_load = pipeline_config.load
        self.patches = [
            mock.patch.object(self.panel, "load",
                              side_effect=lambda path=None: real_load(path or self.fx.config_path)),
            mock.patch.object(self.panel, "CFG_PATH", self.fx.config_path),
            mock.patch.object(self.panel, "TOKEN_FILE", self.fx.root / "state" / "panel-token"),
            mock.patch.object(self.panel, "LOG_FILE", self.fx.root / "pipeline.log"),
            mock.patch.object(self.panel, "VERSION_FILE", self.fx.root / "VERSION"),
            mock.patch.object(self.panel, "BIN", self.fx.bin),
            mock.patch.object(self.panel.shutil, "which", return_value=None),
        ]
        for p in self.patches:
            p.start()
        (self.fx.root / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
        self.server = self.panel.make_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = self.server.token

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None, token=True, extra=None):
        headers = {"X-Requested-With": "panel"}
        if token:
            headers["X-Panel-Token"] = self.token
        headers.update(extra or {})
        data = json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as reply:
                return reply.status, reply.read().decode("utf-8"), reply.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), error.headers

    def wait_jobs(self):
        for _ in range(100):
            status, body, _ = self.request("/api/jobs")
            jobs = json.loads(body)
            if jobs and all(j["status"] != "running" for j in jobs):
                return jobs
            time.sleep(0.05)
        self.fail("jobs did not finish")

    def test_token_gate(self):
        status, body, _ = self.request("/api/status", token=False)
        self.assertEqual(status, 401)
        status, body, _ = self.request("/", token=False)
        self.assertEqual(status, 401)
        self.assertIn("private link", body)
        status, body, _ = self.request("/health", token=False)
        self.assertEqual(status, 200)
        status, body, _ = self.request("/", extra={"X-Panel-Token": "wrong"}, token=False)
        self.assertEqual(status, 401)
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("USB Audio Transcriber", body)
        self.assertIn("view-settings", body)
        self.assertEqual(os.stat(self.fx.root / "state" / "panel-token").st_mode & 0o777, 0o600)

    def test_token_in_the_link_becomes_a_cookie(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/?token={self.token}")
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(req, timeout=10)
            self.fail("expected a redirect")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 303)
            self.assertIn("panel_token=", error.headers.get("Set-Cookie", ""))
            self.assertIn("HttpOnly", error.headers.get("Set-Cookie", ""))
        status, body, _ = self.request("/", token=False, extra={"Cookie": f"panel_token={self.token}"})
        self.assertEqual(status, 200)

    def test_status_reports_the_installation(self):
        self.fx.add_recording()
        self.fx.add_session()
        status, body, _ = self.request("/api/status")

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["version"], "v9.9.9")
        self.assertEqual(data["counts"], {"recordings": 1, "sessions": 1, "summarized": 1})
        self.assertIsNone(data["units"]["timer"])  # no systemctl here
        self.assertFalse(data["systemd"])
        self.assertEqual(data["summaries"]["backend"], "openrouter")
        self.assertTrue(data["summaries"]["ready"])
        self.assertEqual(data["vault"], str(self.fx.vault))
        self.assertEqual(len(data["models"]), 2)

    def test_config_form_masks_secrets_and_saves_through_the_doctor(self):
        status, body, _ = self.request("/api/config")
        data = json.loads(body)
        self.assertEqual(data["values"]["OPENROUTER_API_KEY"], "********")
        self.assertEqual(data["values"]["LLM_API_KEY"], "")
        self.assertEqual(data["values"]["VAULT_DIR"], str(self.fx.vault))
        self.assertTrue(any(s["title"] == "AI summaries" for s in data["settings"]))

        status, body, _ = self.request("/api/config", "POST", {"values": {"ARCHIVE_DIR": "relative/archive"}})
        self.assertEqual(status, 400)
        self.assertTrue(any("absolute" in f for f in json.loads(body)["failures"]))
        self.assertIn(f'ARCHIVE_DIR="{self.fx.archive}"', self.fx.config_path.read_text(encoding="utf-8"))

        status, body, _ = self.request("/api/config", "POST", {"values": {
            "SESSION_SUBJECT": "property law", "OPENROUTER_API_KEY": "********",
            "SUMMARY_BACKEND": "openrouter", "NOT_A_KEY": "ignored"}})
        self.assertEqual(status, 200, body)
        text = self.fx.config_path.read_text(encoding="utf-8")
        self.assertIn('SESSION_SUBJECT="property law"', text)
        self.assertIn('OPENROUTER_API_KEY="sk-secret"', text)
        self.assertIn('SUMMARY_BACKEND="openrouter"', text)
        self.assertNotIn("NOT_A_KEY", text)
        self.assertIn("# test config", text)
        self.assertEqual(os.stat(self.fx.config_path).st_mode & 0o777, 0o600)

    def test_untouched_defaults_are_never_written_and_blank_numbers_are_refused(self):
        status, body, _ = self.request("/api/config")
        values = json.loads(body)["values"]
        self.assertEqual(values["SESSION_NOTES"], "1")       # default, absent from the file
        self.assertEqual(values["VAD_MIN_SILENCE_MS"], "1200")
        self.assertEqual(values["SESSION_BACKFILL_DAYS"], "7")

        # the page sends every field back; nothing it did not change may be written
        status, body, _ = self.request("/api/config", "POST", {"values": values})
        self.assertEqual(status, 200, body)
        text = self.fx.config_path.read_text(encoding="utf-8")
        self.assertNotIn("SESSION_NOTES", text)
        self.assertNotIn("VAD_MIN_SILENCE_MS", text)

        values["VAD_MIN_SILENCE_MS"] = ""
        status, body, _ = self.request("/api/config", "POST", {"values": values})
        self.assertEqual(status, 400)
        self.assertTrue(any("VAD_MIN_SILENCE_MS" in f for f in json.loads(body)["failures"]))

        values["VAD_MIN_SILENCE_MS"] = "1500"
        values["SESSION_NOTES"] = "0"
        status, body, _ = self.request("/api/config", "POST", {"values": values})
        self.assertEqual(status, 200, body)
        text = self.fx.config_path.read_text(encoding="utf-8")
        self.assertIn('VAD_MIN_SILENCE_MS="1500"', text)
        self.assertIn('SESSION_NOTES="0"', text)

    def test_posts_need_the_request_marker(self):
        status, body, _ = self.request("/api/config", "POST", {"values": {}}, extra={"X-Requested-With": "browser"})
        self.assertEqual(status, 403)

    def test_notes_are_readable_only_inside_the_vault_or_archive(self):
        note = self.fx.add_session()
        status, body, _ = self.request("/api/note?path=" + urllib.request.quote(str(note)))
        self.assertEqual(status, 200)
        self.assertIn("Great.", json.loads(body)["text"])
        outside = self.fx.root / "secret.md"
        outside.write_text("no", encoding="utf-8")
        status, body, _ = self.request("/api/note?path=" + urllib.request.quote(str(outside)))
        self.assertEqual(status, 403)
        script = self.fx.vault / "evil.py"
        script.write_text("print(1)", encoding="utf-8")
        status, body, _ = self.request("/api/note?path=" + urllib.request.quote(str(script)))
        self.assertEqual(status, 403)
        status, body, _ = self.request("/api/open", "POST", {"path": str(outside)})
        self.assertEqual(status, 403)

    def test_sessions_and_recordings_listings(self):
        self.fx.add_session()
        self.fx.add_recording()
        status, body, _ = self.request("/api/sessions")
        rows = json.loads(body)
        self.assertEqual(rows[0]["id"], "abc123")
        self.assertEqual(rows[0]["recordings"], 2)
        self.assertTrue(rows[0]["summarized"])
        self.assertEqual(rows[0]["summary_model"], "openrouter:m")
        self.assertTrue(rows[0]["note_exists"])
        status, body, _ = self.request("/api/recordings")
        items = json.loads(body)
        self.assertEqual(items[0]["name"], "rec1.wav")
        self.assertTrue(items[0]["complete"])
        self.assertEqual(items[0]["note_name"], "2026-09-05 0930 transcript")

    def test_search_uses_the_search_script(self):
        status, body, _ = self.request("/api/search?q=roof+leak&since=2026-09-01")
        self.assertEqual(status, 200)
        hits = json.loads(body)
        self.assertEqual(hits[0]["speaker"], "Speaker 1")
        self.assertEqual(json.loads((self.fx.bin / "search.argv").read_text()),
                         ["--json", "--limit", "100", "--since", "2026-09-01", "roof", "leak"])
        status, body, _ = self.request("/api/search?q=")
        self.assertEqual(json.loads(body), [])

    def test_jobs_run_the_matching_script(self):
        status, body, _ = self.request("/api/jobs", "POST",
                                       {"kind": "summarize", "params": {"ids": ["abc123", "def"], "backend": "command"}})
        self.assertEqual(status, 202, body)
        jobs = self.wait_jobs()
        self.assertEqual(jobs[0]["status"], "done")
        self.assertIn("fake ran", jobs[0]["output"])
        self.assertEqual(json.loads((self.fx.bin / "sessions.argv").read_text()),
                         ["summarize", "--id", "abc123", "--id", "def", "--backend", "command"])

        status, body, _ = self.request("/api/jobs", "POST", {"kind": "model-cache", "params": {"action": "status", "profile": "fast"}})
        self.assertEqual(status, 202)
        self.wait_jobs()
        self.assertEqual(json.loads((self.fx.bin / "model-cache.argv").read_text()), ["status", "fast"])

        status, body, _ = self.request("/api/jobs", "POST", {"kind": "launch-missiles", "params": {}})
        self.assertEqual(status, 400)
        status, body, _ = self.request("/api/jobs", "POST", {"kind": "summarize", "params": {"ids": []}})
        self.assertEqual(status, 400)
        status, body, _ = self.request("/api/jobs", "POST", {"kind": "timer", "params": {"action": "pause"}})
        self.assertEqual(status, 400)  # no systemd in the test environment
        status, body, _ = self.request("/api/jobs", "POST", {"kind": "rebuild", "params": {"date": "yesterday"}})
        self.assertEqual(status, 400)

    def test_job_output_streams_while_the_job_runs(self):
        (self.fx.bin / "doctor.py").write_text(
            "import sys, time\nprint('OK  first line', flush=True)\ntime.sleep(1.5)\n"
            "print('FAIL last line', flush=True)\nsys.exit(1)\n", encoding="utf-8")
        status, body, _ = self.request("/api/jobs", "POST", {"kind": "doctor", "params": {}})
        self.assertEqual(status, 202, body)
        for _ in range(100):
            jobs = json.loads(self.request("/api/jobs")[1])
            if "first line" in jobs[0]["output"]:
                break
            time.sleep(0.05)
        self.assertEqual(jobs[0]["status"], "running", "the first line shows before the job ends")
        self.assertNotIn("last line", jobs[0]["output"])
        jobs = self.wait_jobs()
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["returncode"], 1)
        self.assertIn("FAIL last line", jobs[0]["output"])

    def test_log_and_vaults_endpoints(self):
        (self.fx.root / "pipeline.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
        status, body, _ = self.request("/api/log?lines=2")
        self.assertEqual(json.loads(body)["text"], "two\nthree")
        with mock.patch.object(self.panel.setup_module, "find_vaults", return_value=[Path("/home/me/Notes")]):
            status, body, _ = self.request("/api/vaults")
        self.assertEqual(json.loads(body), ["/home/me/Notes"])


class HelperTests(unittest.TestCase):
    def test_job_commands_are_exactly_what_a_person_would_type(self):
        panel = load_panel()
        with mock.patch.object(panel, "BIN", Path("/app/bin")), \
                mock.patch.object(panel, "PYTHON", "/app/venv/bin/python"), \
                mock.patch.object(panel.shutil, "which", return_value="/usr/bin/systemctl"):
            command, label = panel.job_command("cycle", {})
            self.assertEqual(command, ["systemctl", "--user", "start", "usb-audio-transcriber.service"])
            command, label = panel.job_command("timer", {"action": "resume"})
            self.assertEqual(command[:3], ["systemctl", "--user", "start"])
            command, label = panel.job_command("index", {})
            self.assertEqual(command, ["/app/venv/bin/python", "/app/bin/search.py", "--index"])
            command, label = panel.job_command("test-backend", {"backend": "openai"})
            self.assertEqual(command[-2:], ["--backend", "openai"])
        with mock.patch.object(panel.shutil, "which", return_value=None):
            command, label = panel.job_command("cycle", {})
            self.assertEqual(command[0], "bash")
            with self.assertRaises(ValueError):
                panel.job_command("summarize", {"backend": "telepathy", "ids": ["a"]})

    def test_the_panel_opens_as_its_own_window_where_a_browser_allows_it(self):
        panel = load_panel()
        url = "http://127.0.0.1:8765/?token=abc"
        with mock.patch.object(panel.shutil, "which",
                               side_effect=lambda name: "/usr/bin/brave-browser" if name == "brave-browser" else None):
            command = panel.app_window_command(url)
        self.assertEqual(command[:2], ["/usr/bin/brave-browser", f"--app={url}"])
        with mock.patch.object(panel.shutil, "which", return_value=None):
            self.assertIsNone(panel.app_window_command(url), "no such browser: fall back to a tab")

    def test_private_link_carries_the_token(self):
        panel = load_panel()
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(panel, "TOKEN_FILE", Path(directory) / "token"), \
                    mock.patch.object(panel, "load", return_value={"PANEL_BIND": "127.0.0.1", "PANEL_PORT": "9000"}):
                url = panel.panel_url()
                self.assertTrue(url.startswith("http://127.0.0.1:9000/?token="))
                self.assertEqual(url, panel.panel_url(), "the token must be stable")


if __name__ == "__main__":
    unittest.main()
