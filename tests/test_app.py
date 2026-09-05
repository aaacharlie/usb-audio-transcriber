import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


def load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_for_test", ROOT / "bin" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoteParsingTests(unittest.TestCase):
    def setUp(self):
        self.app = load("app")

    def test_the_module_imports_without_gtk(self):
        self.assertNotIn("gi", sys.modules.keys() & {"gi"}, "GTK is imported only when the window runs")

    def test_front_matter_headings_items_and_links(self):
        text = ("---\ndate: 2026-09-05\nsummary_model: command:codex\n---\n\n# Session\n\n"
                "## Summary\n\nThe **roof** leaks, see [[2026-09-05 0930 transcript]].\n"
                "- first `item`\n> a hint\n---\nplain\n")
        blocks = self.app.parse_note(text)
        self.assertEqual(blocks[0], ("meta", [("date", "2026-09-05"), ("summary_model", "command:codex")]))
        self.assertEqual(blocks[1], ("heading", 1, [("text", "Session")]))
        self.assertEqual(blocks[2], ("heading", 2, [("text", "Summary")]))
        self.assertEqual(blocks[3], ("paragraph", [("text", "The "), ("bold", "roof"), ("text", " leaks, see "),
                                                   ("link", "2026-09-05 0930 transcript"), ("text", ".")]))
        self.assertEqual(blocks[4], ("item", [("text", "first "), ("code", "item")]))
        self.assertEqual(blocks[5], ("quote", [("text", "a hint")]))
        self.assertEqual(blocks[6], ("rule",))
        self.assertEqual(blocks[7], ("paragraph", [("text", "plain")]))

    def test_helpers(self):
        self.assertEqual(self.app.when("2026-09-05T09:30:12"), "2026-09-05 09:30")
        self.assertEqual(self.app.stamp(3725.9), "1:02:05")
        self.assertEqual(self.app.unit_pill(None), ("no systemd", ""))
        self.assertEqual(self.app.unit_pill({"active": "active"}), ("active", "ok"))
        self.assertEqual(self.app.unit_pill({"active": "inactive"}), ("inactive", "warn"))
        self.assertEqual(self.app.note_label({"note": "/v/2026-09-05 0930 transcript.md"}),
                         "2026-09-05 0930 transcript")
        self.assertEqual(self.app.note_label({"note": None, "audio": "/a/x.wav"}), "x.wav")
        escape = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
        self.assertEqual(self.app.highlight_markup("fix the [roof] <now>", escape),
                         "fix the <b>roof</b> &lt;now>")


class ApiTests(unittest.TestCase):
    def test_requests_carry_the_token_and_the_marker(self):
        app = load("app")
        api = app.Api("http://127.0.0.1:1/", "secret")
        seen = {}

        class Reply:
            status = 200

            def read(self):
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["headers"] = dict(request.header_items())
            seen["method"] = request.get_method()
            seen["data"] = request.data
            return Reply()

        with mock.patch.object(app.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(api.request("/api/search", params={"q": "roof leak", "since": ""}), {"ok": True})
            self.assertEqual(seen["url"], "http://127.0.0.1:1/api/search?q=roof+leak")
            self.assertEqual(seen["method"], "GET")
            self.assertEqual(seen["headers"]["X-panel-token"], "secret")
            self.assertEqual(seen["headers"]["X-requested-with"], "panel")
            api.request("/api/jobs", body={"kind": "doctor", "params": {}})
            self.assertEqual(seen["method"], "POST")
            self.assertIn(b'"kind": "doctor"', seen["data"])

    def test_connection_errors_become_one_message(self):
        app = load("app")
        api = app.Api("http://127.0.0.1:1/", "secret")
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(app.ApiError) as caught:
                api.request("/api/status")
        self.assertIn("not answering", str(caught.exception))


class OpenFlowTests(unittest.TestCase):
    """panel.py open prefers the desktop window, then a browser window, then a tab."""

    def test_gui_python_probes_candidates_in_order(self):
        panel = load("panel")
        probed = []

        def fake_run(command, **kwargs):
            probed.append(command[0])
            return mock.Mock(returncode=0 if command[0] == "/usr/bin/python3" else 1)

        with mock.patch.object(panel, "PYTHON", "/app/venv/bin/python"), \
                mock.patch.object(panel.subprocess, "run", fake_run), \
                mock.patch.object(panel.shutil, "which", return_value="/usr/local/bin/python3"):
            self.assertEqual(panel.gui_python(), "/usr/bin/python3")
        self.assertEqual(probed, ["/app/venv/bin/python", "/usr/bin/python3"])
        with mock.patch.object(panel.subprocess, "run", return_value=mock.Mock(returncode=1)):
            self.assertIsNone(panel.gui_python())

    def test_open_launches_the_window_with_the_token_file_not_the_token(self):
        panel = load("panel")
        launched = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(panel, "ensure_server", return_value="http://127.0.0.1:8765/"), \
                    mock.patch.object(panel, "TOKEN_FILE", Path(directory) / "token"), \
                    mock.patch.object(panel, "gui_python", return_value="/usr/bin/python3"), \
                    mock.patch.object(panel, "detached", launched.append):
                code = panel.open_panel(mock.Mock(no_browser=False, browser=False, web=False))
        self.assertEqual(code, 0)
        command = launched[0]
        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertTrue(command[1].endswith("app.py"))
        self.assertEqual(command[2:4], ["--connect", "http://127.0.0.1:8765/"])
        self.assertEqual(command[4], "--token-file")
        self.assertNotIn(panel.token(), " ".join(command))

    def test_open_falls_back_to_the_web_page_without_gtk(self):
        panel = load("panel")
        opened = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(panel, "ensure_server", return_value="http://127.0.0.1:8765/"), \
                    mock.patch.object(panel, "TOKEN_FILE", Path(directory) / "token"), \
                    mock.patch.object(panel, "gui_python", return_value=None), \
                    mock.patch.object(panel, "open_web", lambda base, tab=False: opened.append((base, tab)) or 0):
                panel.open_panel(mock.Mock(no_browser=False, browser=False, web=False))
                panel.open_panel(mock.Mock(no_browser=False, browser=True, web=False))
        self.assertEqual(opened, [("http://127.0.0.1:8765/", False), ("http://127.0.0.1:8765/", True)])


if __name__ == "__main__":
    unittest.main()
