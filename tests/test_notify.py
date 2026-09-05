import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import notify
import pipeline_config


def load_transcriber(config):
    spec = importlib.util.spec_from_file_location(
        "transcribe_for_notify_test", ROOT / "bin" / "transcribe.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class EnabledTests(unittest.TestCase):
    def test_disabled_by_setting_or_headless_mode(self):
        with mock.patch.object(notify.shutil, "which", return_value="/usr/bin/notify-send"):
            self.assertFalse(notify.enabled({"NOTIFY": "0"}, {"DISPLAY": ":0"}))
            self.assertFalse(notify.enabled({"HEADLESS": "1"}, {"DISPLAY": ":0"}))

    def test_auto_needs_notify_send_and_a_display(self):
        with mock.patch.object(notify.shutil, "which", return_value=None):
            self.assertFalse(notify.enabled({}, {"DISPLAY": ":0"}))
        with mock.patch.object(notify.shutil, "which", return_value="/usr/bin/notify-send"):
            self.assertFalse(notify.enabled({}, {}))
            self.assertTrue(notify.enabled({}, {"DISPLAY": ":0"}))
            self.assertTrue(notify.enabled({"NOTIFY": "1"}, {}))


class SendTests(unittest.TestCase):
    def test_plain_notification_when_actions_are_unsupported(self):
        with mock.patch.object(notify.shutil, "which", return_value="/usr/bin/notify-send"), \
                mock.patch.object(notify, "supports_actions", return_value=False), \
                mock.patch.object(notify.subprocess, "Popen") as popen:
            self.assertTrue(notify.send("Ready", "note", open_path=Path("/v/note.md"),
                                        config={"NOTIFY": "1"}))

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "notify-send")
        self.assertIn("Ready", command)
        self.assertIn("note", command)
        self.assertIn("--app-name=USB Audio Transcriber", command)
        self.assertTrue(popen.call_args.kwargs["close_fds"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_click_to_open_uses_a_detached_helper(self):
        with mock.patch.object(notify.shutil, "which", return_value="/usr/bin/notify-send"), \
                mock.patch.object(notify, "supports_actions", return_value=True), \
                mock.patch.object(notify.subprocess, "Popen") as popen:
            notify.send("Ready", "note", open_path=Path("/v/note.md"), config={"NOTIFY": "1"})

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("notify.py"))
        self.assertIn("--wait", command)
        self.assertEqual(command[command.index("--open") + 1], "/v/note.md")
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_nothing_is_sent_when_disabled(self):
        with mock.patch.object(notify.shutil, "which", return_value=None), \
                mock.patch.object(notify.subprocess, "Popen") as popen:
            self.assertFalse(notify.send("Ready", "note", config={"NOTIFY": "1"}))

        popen.assert_not_called()


class WaitAndOpenTests(unittest.TestCase):
    def test_clicking_the_notification_opens_the_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "opened"
            (fake_bin / "notify-send").write_text(
                "#!/usr/bin/env bash\necho default\n", encoding="utf-8"
            )
            (fake_bin / "xdg-open").write_text(
                f"#!/usr/bin/env bash\nprintf '%s' \"$1\" > '{marker}'\n",
                encoding="utf-8",
            )
            for script in fake_bin.iterdir():
                script.chmod(script.stat().st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}:/usr/bin:/bin"}):
                self.assertEqual(
                    notify.wait_and_open("Ready", "note", root / "note.md"), 0
                )
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertEqual(marker.read_text(encoding="utf-8"), str(root / "note.md"))

    def test_dismissed_notification_opens_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (fake_bin / "notify-send").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            (fake_bin / "notify-send").chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{fake_bin}:/usr/bin:/bin"}), \
                    mock.patch.object(notify, "detach") as detach:
                notify.wait_and_open("Ready", "note", root / "note.md")

            detach.assert_not_called()


class TranscriberIntegrationTests(unittest.TestCase):
    def prepare(self, root):
        import sqlite3
        from contextlib import closing
        queue = root / "queue"
        queue.mkdir()
        audio = queue / "meeting.wav"
        audio.touch()
        state_db = root / "state.sqlite"
        with closing(sqlite3.connect(state_db)) as connection:
            connection.execute("CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)")
            connection.execute("INSERT INTO seen VALUES (?, 0)", (str(audio),))
            connection.commit()
        return load_transcriber({
            "QUEUE_DIR": str(queue),
            "VAULT_DIR": str(root / "vault"),
            "STATE_DB": str(state_db),
            "AUDIO_EXTS": "wav",
            "WHISPER_MODEL_PROFILE": "fast",
        })

    def test_finished_note_is_announced_with_click_to_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.prepare(root)

            class FakeSegment:
                start = 0
                end = 1
                text = " Hello."

            class FakeModel:
                def __init__(self, model_id, **kwargs):
                    pass

                def transcribe(self, path, **kwargs):
                    return iter([FakeSegment()]), types.SimpleNamespace(duration=1)

            fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
            progress = []
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress",
                                      side_effect=lambda **u: progress.append(u)), \
                    mock.patch.object(module.notify, "send") as send:
                self.assertEqual(module.run(), 0)

            note = next((root / "vault").glob("*.md"))
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0], "Transcript ready")
            self.assertEqual(send.call_args.kwargs["open_path"], note)
            self.assertEqual(progress[-1]["phase"], "Transcription complete",
                             "a successful run must not be recorded as failed")

    def test_failure_is_announced_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.prepare(root)

            class FailingModel:
                def __init__(self, model_id, **kwargs):
                    pass

                def transcribe(self, path, **kwargs):
                    raise RuntimeError("model crashed")

            fake_module = types.SimpleNamespace(WhisperModel=FailingModel)
            progress = []
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress",
                                      side_effect=lambda **u: progress.append(u)), \
                    mock.patch.object(module.notify, "send") as send:
                with self.assertRaisesRegex(RuntimeError, "model crashed"):
                    module.run()

            self.assertEqual(progress[-1]["phase"], "Transcription failed")
            self.assertEqual(send.call_args.args[0], "Transcription failed")


if __name__ == "__main__":
    unittest.main()
