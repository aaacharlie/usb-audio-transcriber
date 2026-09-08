"""Regression tests for the findings of the 2026-09-08 bug hunt (B-01 to B-10 and the
quick fixes that rode along)."""
import fcntl
import importlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT))
import pipeline_config  # noqa: E402
import model_profiles  # noqa: E402


def load_script(name, config, module_name=None):
    """Load bin/<name>.py with pipeline_config.load returning `config`."""
    spec = importlib.util.spec_from_file_location(module_name or f"{name}_bughunt", ROOT / "bin" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


def base_config(root, **extra):
    config = {
        "ARCHIVE_DIR": str(root / "archive"), "QUEUE_DIR": str(root / "queue"),
        "STATE_DB": str(root / "state" / "seen.sqlite"), "VAULT_DIR": str(root / "vault"),
        "AUDIO_EXTS": "wav", "RECORDER_DIR": "RECORD", "WHISPER_MODEL_PROFILE": "fast",
        "SUMMARY_BACKEND": "none",
    }
    config.update(extra)
    return config


class B01BundledFilesTests(unittest.TestCase):
    def test_prompt_and_page_come_from_the_program_files_not_the_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            data_root.mkdir()
            with mock.patch.dict(os.environ, {"USB_AUDIO_TRANSCRIBER_ROOT": str(data_root)}):
                moved = importlib.reload(pipeline_config)
                self.assertEqual(moved.ROOT, data_root)
                sessions = load_script("sessions", base_config(data_root))
                panel = load_script("panel", base_config(data_root))
            importlib.reload(pipeline_config)
        self.assertEqual(sessions.PROMPT_FILE, ROOT / "prompts" / "session-summary.md")
        self.assertTrue(sessions.PROMPT_FILE.is_file())
        self.assertEqual(panel.PAGE, ROOT / "panel" / "index.html")
        self.assertEqual(sessions.LOCK_FILE, data_root / "var" / "state" / "cycle.lock")


class B02ConfigQuotingTests(unittest.TestCase):
    def test_values_with_quotes_survive_the_wizard_and_load(self):
        setup = load_script("setup", {})
        command = 'gemini -p "$(cat {prompt_file})"'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.env"
            path.write_text('# comment\nSESSION_SUBJECT="old"\nSESSION_SUBJECT="dup"\n', encoding="utf-8")
            setup.write_config(path, {"SUMMARY_COMMAND": command, "SESSION_SUBJECT": 'the "Roofers" podcast',
                                      "DIARIZATION_MODEL": "back\\slash"})
            loaded = pipeline_config.load(path)
            text = path.read_text(encoding="utf-8")
        self.assertEqual(loaded["SUMMARY_COMMAND"], command)
        self.assertEqual(loaded["SESSION_SUBJECT"], 'the "Roofers" podcast')
        self.assertEqual(loaded["DIARIZATION_MODEL"], "back\\slash")
        self.assertEqual(text.count("SESSION_SUBJECT="), 1, "a duplicated key is written once")
        self.assertTrue(text.startswith("# comment\n"))

    def test_a_line_break_is_refused_everywhere(self):
        setup = load_script("setup", {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.env"
            path.write_text('SESSION_SUBJECT="x"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                setup.write_config(path, {"SESSION_SUBJECT": 'x"\nPURGE_DEVICE="1'})
            self.assertEqual(pipeline_config.load(path), {"SESSION_SUBJECT": "x"})

    def test_a_command_with_unbalanced_quotes_is_named_and_diagnosed(self):
        llm = load_script("llm", {})
        doctor = load_script("doctor", {})
        backend = llm.CommandBackend('gemini -p "$(cat x)')
        self.assertEqual(backend.describe(), "command:gemini")
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/a", "QUEUE_DIR": "/q", "STATE_DB": "/s/db", "VAULT_DIR": "/v",
            "AUDIO_EXTS": "wav", "SUMMARY_BACKEND": "command", "SUMMARY_COMMAND": 'gemini -p "$(cat x)',
            "PANEL_BIND": "::",
        })
        self.assertTrue(any("unbalanced quotes" in f for f in failures), failures)
        self.assertTrue(any("PANEL_BIND" in f for f in failures), failures)


class B03ProfileSwitchTests(unittest.TestCase):
    def finished(self, audio, profile, comparison):
        for suffix in (".json", ".txt", ".complete.json"):
            model_profiles.artifact_path(audio, profile, suffix, comparison).write_text("{}", encoding="utf-8")

    def test_a_recording_stays_complete_after_switching_to_or_from_both(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "rec.wav"
            audio.write_bytes(b"x")
            self.assertIsNone(model_profiles.completed_layout(audio, model_profiles.FAST, False))
            self.finished(audio, model_profiles.FAST, comparison=False)  # transcribed under "fast"
            self.assertIs(model_profiles.completed_layout(audio, model_profiles.ACCURATE, True), False,
                          "switched to both: the unlabelled files still count")
            self.assertTrue(model_profiles.artifacts_complete(audio, model_profiles.ACCURATE, True))
            other = Path(directory) / "both.wav"
            other.write_bytes(b"x")
            self.finished(other, model_profiles.FAST, comparison=True)  # transcribed under "both"
            self.assertIs(model_profiles.completed_layout(other, model_profiles.FAST, False), True,
                          "switched back to fast: the .fast files still count")

    def test_search_indexes_recordings_from_the_other_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            audio = root / "rec.wav"
            audio.write_bytes(b"x")
            for suffix, body in ((".json", json.dumps({"segments": [{"start": 1, "text": "roof leak"}]})),
                                 (".txt", "roof leak"), (".complete.json", json.dumps({"note": "/v/n.md"}))):
                model_profiles.artifact_path(audio, model_profiles.FAST, suffix, True).write_text(body, encoding="utf-8")
            search = load_script("search", base_config(root, WHISPER_MODEL_PROFILE="fast"))
            con = sqlite3.connect(root / "state" / "seen.sqlite")
            con.execute("CREATE TABLE seen (sha256 TEXT PRIMARY KEY, orig_name TEXT, archived_to TEXT, "
                        "bytes INTEGER, imported_at TEXT, transcribed INTEGER DEFAULT 0)")
            con.execute("INSERT INTO seen VALUES ('d', 'rec.wav', ?, 1, '2026-09-05T09:00:00', 1)", (str(audio),))
            con.commit()
            self.assertEqual(search.refresh(con), (1, 0))
            self.assertEqual(len(search.search(con, ["roof"])), 1)
            con.close()


class B04KeepSummaryTests(unittest.TestCase):
    def test_summarizing_with_an_unusable_backend_changes_nothing_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = load_script("sessions", base_config(root, SUMMARY_BACKEND="none", SESSION_GAP_MIN="20"))
            (root / "vault").mkdir()
            note = root / "vault" / "2026-09-05 0900 session.md"
            note.write_text("# Session\n\n## Summary\n\nA paid summary.\n", encoding="utf-8")
            con = sqlite3.connect(":memory:")
            sessions.init_db(con)
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                        ("abc", "2026-09-05T09:00:00", "2026-09-05T10:00:00", json.dumps(["d1"]),
                         str(note), 1, "2026-09-05T10:01:00"))
            members = [{"digest": "d1", "audio": root / "a.wav", "note": None, "segments": [],
                        "duration": 60.0, "status": "complete", "complete": True,
                        "start": sessions.datetime(2026, 9, 5, 9, 0), "end": sessions.datetime(2026, 9, 5, 9, 1)}]
            with mock.patch.object(sessions, "load_recordings", return_value=members), \
                    mock.patch.object(sessions, "BACKEND", None):
                self.assertIsNone(sessions.summarize_selected(con, ["abc"]))
                self.assertIsNone(sessions.retry(con))
                self.assertEqual(sessions.write_session(con, members, note=note, force=True, manual=True), note)
            self.assertIn("A paid summary.", note.read_text(encoding="utf-8"))
            self.assertEqual(con.execute("SELECT summarized FROM sessions").fetchone()[0], 1)
            with mock.patch.object(sessions, "BACKEND", None), \
                    mock.patch.object(sessions, "hold_cycle_lock", return_value=None), \
                    mock.patch.object(sessions, "STATE_DB", root / "state" / "seen.sqlite"):
                (root / "state").mkdir(exist_ok=True)
                self.assertEqual(sessions.main(["summarize", "--id", "abc"]), 1)


class B05PurgeScopeTests(unittest.TestCase):
    def test_only_removable_drives_are_purged_and_symlinked_mounts_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "BACKUP" / "RECORD" / "rec.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            (root / "media" / "home-link").symlink_to(root)  # a convenience symlink under /mnt
            ingest = load_script("ingest", base_config(root, PURGE_DEVICE="1"))
            ingest.MOUNT_ROOTS = [root / "media"]
            self.assertEqual(ingest.find_candidates(), [source], "the symlink is not a drive")
            with mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch.object(ingest, "removable_device", return_value=False), \
                    mock.patch("builtins.print") as printed:
                self.assertEqual(ingest.main(), 0)
            self.assertTrue(source.exists(), "a fixed disk keeps its files")
            self.assertEqual(len(list((root / "archive").rglob("*.wav"))), 1, "but they are imported")
            self.assertTrue(any("never purged" in str(c) for c in printed.call_args_list))

    def test_sysfs_flag_is_read_for_the_partition_and_its_disk(self):
        ingest = load_script("ingest", base_config(Path("/tmp")))
        with tempfile.TemporaryDirectory() as directory:
            sys_root = Path(directory)
            disk = sys_root / "devices" / "sdb"
            (disk / "sdb1").mkdir(parents=True)
            (disk / "removable").write_text("1\n", encoding="utf-8")
            (sys_root / "dev" / "block").mkdir(parents=True)
            (sys_root / "dev" / "block" / "8:17").symlink_to(disk / "sdb1")
            device = os.makedev(8, 17)
            self.assertIs(ingest.removable_device(None, sysfs=sys_root, device=device), True)
            (disk / "removable").write_text("0\n", encoding="utf-8")
            self.assertIs(ingest.removable_device(None, sysfs=sys_root, device=device), False)
            self.assertIsNone(ingest.removable_device(None, sysfs=sys_root, device=os.makedev(8, 33)),
                              "an unknown device is not purged")


class B06PartialCopyTests(unittest.TestCase):
    def test_a_copy_that_dies_halfway_leaves_nothing_and_the_next_cycle_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "REC001.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 4096)
            ingest = load_script("ingest", base_config(root))
            leftover = root / "archive" / "old.wav.partial"
            leftover.parent.mkdir()
            leftover.write_bytes(b"junk")

            def die_halfway(src, dst, **kwargs):
                Path(dst).write_bytes(b"recording" * 100)
                raise OSError(5, "Input/output error")

            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch.object(ingest.shutil, "copy2", side_effect=die_halfway):
                self.assertEqual(ingest.main(), 0)
            self.assertFalse(leftover.exists(), "an old partial copy is swept")
            self.assertEqual(list((root / "archive").rglob("*")), [] if not (root / "archive").exists() else
                             [p for p in (root / "archive").rglob("*") if p.is_dir()], "no file left behind")
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)
            archived = [p for p in (root / "archive").rglob("*.wav")]
            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0].name.endswith("_REC001.wav"), "the real name, not _1")
            self.assertTrue(source.exists())


class B07UndecodableNameTests(unittest.TestCase):
    def test_a_file_name_that_is_not_utf8_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watched = root / "memos"
            watched.mkdir()
            good = watched / "good.wav"
            good.write_bytes(b"recording" * 1024)
            bad = os.fsdecode(b"R\xe9union.wav")  # Latin-1 bytes, surrogate-escaped by Python
            with open(watched / bad, "wb") as handle:
                handle.write(b"recording" * 1024)
            ingest = load_script("ingest", base_config(root, WATCH_DIRS=str(watched)))
            ingest.MOUNT_ROOTS = []
            with mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch("builtins.print") as printed:
                self.assertEqual(ingest.main(), 0)
            archived = list((root / "archive").rglob("*.wav"))
            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0].name.endswith("_good.wav"))
            self.assertTrue(any("not valid UTF-8" in str(c) for c in printed.call_args_list))


class B08SessionsLockTests(unittest.TestCase):
    def test_a_manual_run_waits_for_the_cycle_and_gives_up_politely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = load_script("sessions", base_config(root))
            lock_file = root / "cycle.lock"
            with mock.patch.object(sessions, "LOCK_FILE", lock_file), \
                    mock.patch.object(sessions, "LOCK_WAIT", 1), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("USB_AUDIO_TRANSCRIBER_IN_CYCLE", None)
                with open(lock_file, "w") as holder:
                    fcntl.flock(holder, fcntl.LOCK_EX)
                    with self.assertRaises(SystemExit) as stop:
                        sessions.hold_cycle_lock()
                    self.assertIn("still running", str(stop.exception))
                handle = sessions.hold_cycle_lock()
                self.assertIsNotNone(handle, "free lock: taken")
                handle.close()
                with mock.patch.dict(os.environ, {"USB_AUDIO_TRANSCRIBER_IN_CYCLE": "1"}):
                    self.assertIsNone(sessions.hold_cycle_lock(), "inside run-cycle.sh the parent holds it")

    def test_run_cycle_tells_its_steps_the_lock_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            (root / "venv" / "bin").mkdir(parents=True)
            for name in ("ingest.py", "progress-popup.py", "transcribe.py", "sessions.py", "search.py"):
                (root / "bin" / name).touch()
            python = root / "venv" / "bin" / "python"
            seen = root / "env-seen"
            python.write_text("#!/usr/bin/env bash\ncase $1 in */sessions.py) echo \"cycle=$USB_AUDIO_TRANSCRIBER_IN_CYCLE\" > '"
                              + str(seen) + "' ;; esac\nexit 0\n", encoding="utf-8")
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            result = subprocess.run(["bash", str(ROOT / "bin" / "run-cycle.sh")], capture_output=True, text=True,
                                    env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)}, check=False)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(seen.read_text(encoding="utf-8").strip(), "cycle=1")


class B09InstallerGuardTests(unittest.TestCase):
    def fake_tools(self, root):
        fake_bin = root / "fakebin"
        fake_bin.mkdir()
        for command in ("ffmpeg", "zenity", "systemctl", "flock", "tee", "python3"):
            (fake_bin / command).symlink_to("/usr/bin/true")
        return fake_bin

    def test_install_refuses_to_run_from_a_clone_at_the_install_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_home = root / "data"
            clone = data_home / "usb-audio-transcriber"
            clone.mkdir(parents=True)
            shutil.copy(ROOT / "install.sh", clone / "install.sh")
            (clone / "bin").mkdir()
            (clone / "bin" / "ingest.py").write_text("the clone's own file\n", encoding="utf-8")
            fake_bin = self.fake_tools(root)
            result = subprocess.run(["bash", str(clone / "install.sh")], capture_output=True, text=True, check=False,
                                    env={"HOME": str(root / "home"), "XDG_DATA_HOME": str(data_home),
                                         "XDG_CONFIG_HOME": str(root / "config"), "PATH": f"{fake_bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 1)
            self.assertIn("Move the clone somewhere else", result.stderr)
            self.assertTrue((clone / "bin" / "ingest.py").is_file())

    def test_uninstall_leaves_a_clone_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_home = root / "data"
            clone = data_home / "usb-audio-transcriber"
            (clone / ".git").mkdir(parents=True)
            (clone / "bin").mkdir()
            (clone / "bin" / "ingest.py").write_text("keep me\n", encoding="utf-8")
            shutil.copy(ROOT / "uninstall.sh", clone / "uninstall.sh")
            fake_bin = self.fake_tools(root)
            result = subprocess.run(["bash", str(clone / "uninstall.sh")], capture_output=True, text=True, check=False,
                                    env={"HOME": str(root / "home"), "XDG_DATA_HOME": str(data_home),
                                         "XDG_CONFIG_HOME": str(root / "config"), "PATH": f"{fake_bin}:/usr/bin:/bin"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((clone / "bin" / "ingest.py").is_file())
            self.assertIn("git clone", result.stdout)


class B10NonceTests(unittest.TestCase):
    def test_open_web_uses_a_one_time_link_and_never_the_token(self):
        panel = load_script("panel", {})
        launched = []
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(panel, "TOKEN_FILE", Path(directory) / "token"), \
                    mock.patch.object(panel, "request_nonce", return_value="one-time"), \
                    mock.patch.object(panel, "detached", launched.append), \
                    mock.patch.object(panel, "app_window_command", lambda url: ["browser", f"--app={url}"]):
                panel.open_web("http://127.0.0.1:8765/")
                secret = panel.token()
        self.assertEqual(launched, [["browser", "--app=http://127.0.0.1:8765/?nonce=one-time"]])
        self.assertNotIn(secret, " ".join(launched[0]))

    def test_open_prints_the_link_only_when_asked_not_to_open_anything(self):
        panel = load_script("panel", {})
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(panel, "TOKEN_FILE", Path(directory) / "token"), \
                    mock.patch.object(panel, "ensure_server", return_value="http://127.0.0.1:8765/"), \
                    mock.patch.object(panel, "gui_python", return_value="/usr/bin/python3"), \
                    mock.patch.object(panel, "launch", lambda command: None):
                out = io.StringIO()
                with redirect_stdout(out):
                    panel.open_panel(mock.Mock(no_browser=False, browser=False, web=False))
                self.assertEqual(out.getvalue(), "", "the token stays out of the journal")
                out = io.StringIO()
                with redirect_stdout(out):
                    panel.open_panel(mock.Mock(no_browser=True, browser=False, web=False))
                self.assertIn("?token=", out.getvalue())


class QuickFixTests(unittest.TestCase):
    def test_release_notes_do_not_take_a_prerelease_section_for_the_final(self):
        script = importlib.util.spec_from_file_location("release_notes_bughunt", ROOT / ".github" / "release-notes.py")
        module = importlib.util.module_from_spec(script)
        script.loader.exec_module(module)
        text = "## [1.1.0-rc1] - 2026-09-01\n\nrc notes\n\n## [1.0.0] - 2026-08-01\n\nold\n"
        self.assertIsNone(module.section(text, "1.1.0"))
        self.assertEqual(module.section(text, "1.1.0-rc1"), "rc notes")
        self.assertEqual(module.section("## [1.1.0] - 2026-09-06\n\nfinal\n", "1.1.0"), "final")

    def test_search_rejects_a_since_that_is_not_a_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            search = load_script("search", base_config(root))
            with mock.patch.object(search, "fts5_available", return_value=True):
                with self.assertRaises(SystemExit):
                    search.main(["--since", "yesterday", "roof"])

    def test_vault_search_survives_an_unreadable_entry(self):
        setup = load_script("setup", {})
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "Notes" / ".obsidian").mkdir(parents=True)
            broken = home / "stale-mount"
            broken.mkdir()
            real_is_dir = Path.is_dir

            def flaky(self):
                if self.name == "stale-mount":
                    raise OSError(107, "Transport endpoint is not connected")
                return real_is_dir(self)
            with mock.patch.object(Path, "is_dir", flaky):
                self.assertEqual(setup.vaults_by_search(home), [home / "Notes"])

    def test_the_cli_bakes_a_custom_data_root_into_units_and_the_menu_entry(self):
        from usb_audio_transcriber import cli
        rendered = cli.render("[Service]\nExecStart=@CYCLE_COMMAND@\n", Path("/opt/x/usb-audio-transcriber"),
                              root=Path("/srv/audio data"))
        self.assertIn('[Service]\nEnvironment="USB_AUDIO_TRANSCRIBER_ROOT=/srv/audio data"\n', rendered)
        desktop = cli.render("[Desktop Entry]\nExec=@PANEL_COMMAND@ open\n", Path("/opt/x/usb-audio-transcriber"),
                             root=Path("/srv/audio data"))
        self.assertIn('Exec=env "USB_AUDIO_TRANSCRIBER_ROOT=/srv/audio data" "/opt/x/usb-audio-transcriber" panel open',
                      desktop)
        plain = cli.render("[Service]\nExecStart=@CYCLE_COMMAND@\n", Path("/opt/x/usb-audio-transcriber"))
        self.assertNotIn("Environment=", plain)

    def test_split_windows_tolerates_a_zero_size(self):
        llm = load_script("llm", {})
        self.assertEqual(llm.split_windows("a. b. c.", 0), ["a. b. c."])

    def test_notification_helper_gets_its_own_scope_under_systemd(self):
        notify = load_script("notify", {})
        with mock.patch.object(notify.subprocess, "Popen") as popen, \
                mock.patch.object(notify.shutil, "which", return_value="/usr/bin/systemd-run"):
            notify.detach(["notify-send", "hi"], environ={"INVOCATION_ID": "abc"})
            self.assertEqual(popen.call_args[0][0][:4], ["systemd-run", "--user", "--scope", "--quiet"])
            notify.detach(["notify-send", "hi"], environ={})
            self.assertEqual(popen.call_args[0][0], ["notify-send", "hi"])


if __name__ == "__main__":
    unittest.main()
