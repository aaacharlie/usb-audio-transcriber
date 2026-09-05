import importlib.util
import json
import os
import sqlite3
import stat
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config
import model_profiles
from model_profiles import cache_path_for, profiles_for

spec = importlib.util.spec_from_file_location("progress_popup", ROOT / "bin" / "progress-popup.py")
progress_popup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress_popup)


def load_transcriber(config):
    spec = importlib.util.spec_from_file_location(
        "transcribe_for_test", ROOT / "bin" / "transcribe.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class ProgressStateTests(unittest.TestCase):
    def test_state_round_trip_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            original = pipeline_config.PROGRESS_PATH
            pipeline_config.PROGRESS_PATH = Path(directory) / "state" / "progress.json"
            try:
                pipeline_config.write_progress(active=True, current_percent=50)
                self.assertEqual(pipeline_config.read_progress()["current_percent"], 50)
                self.assertFalse((pipeline_config.PROGRESS_PATH.parent / "progress.tmp").exists())
            finally:
                pipeline_config.PROGRESS_PATH = original

    def test_popup_message_contains_progress_details(self):
        text = progress_popup.message({
            "active": True, "phase": "Transcribing", "detected_files": 2,
            "imported_files": 1, "total_files": 1, "files_completed": 0,
            "current_file": "recording.wav", "eta_seconds": 75,
        })
        self.assertIn("USB: 2 audio file(s) detected; 1 new", text)
        self.assertIn("Files: 0/1 complete", text)
        self.assertIn("About 1m 15s remaining", text)

    def test_popup_shows_eta_for_profile_labelled_transcription(self):
        text = progress_popup.message({
            "active": True,
            "phase": "Transcribing (Accurate — Whisper Large v3)",
            "eta_seconds": 75,
        })
        self.assertIn("About 1m 15s remaining", text)


class HeadlessTests(unittest.TestCase):
    def test_headless_setting_disables_the_window_even_with_a_display(self):
        with mock.patch.object(progress_popup.shutil, "which", return_value="/usr/bin/zenity"):
            self.assertFalse(
                progress_popup.desktop_available({"HEADLESS": "1"}, {"DISPLAY": ":0"})
            )

    def test_auto_mode_needs_a_display_and_zenity(self):
        with mock.patch.object(progress_popup.shutil, "which", return_value="/usr/bin/zenity"):
            self.assertFalse(progress_popup.desktop_available({"HEADLESS": "auto"}, {}))
            self.assertTrue(
                progress_popup.desktop_available({}, {"WAYLAND_DISPLAY": "wayland-0"})
            )
        with mock.patch.object(progress_popup.shutil, "which", return_value=None):
            self.assertFalse(progress_popup.desktop_available({}, {"DISPLAY": ":0"}))
            self.assertFalse(progress_popup.desktop_available({"HEADLESS": "0"}, {}))

    def test_forced_desktop_mode_ignores_missing_display_variables(self):
        with mock.patch.object(progress_popup.shutil, "which", return_value="/usr/bin/zenity"):
            self.assertTrue(progress_popup.desktop_available({"HEADLESS": "0"}, {}))

    def test_main_exits_quietly_without_a_desktop(self):
        with mock.patch.object(progress_popup, "load", return_value={}), \
                mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(progress_popup.shutil, "which", return_value="/usr/bin/zenity"), \
                mock.patch.object(progress_popup.subprocess, "Popen") as popen:
            self.assertEqual(progress_popup.main(), 0)

        popen.assert_not_called()


class ModelProfileTests(unittest.TestCase):
    def test_fast_profile_selects_distilled_model(self):
        profile = profiles_for("fast")[0]
        self.assertEqual(profile.model_id, "distil-large-v3")
        self.assertEqual(profile.key, "fast")

    def test_accurate_profile_selects_full_model(self):
        profile = profiles_for("accurate")[0]
        self.assertEqual(profile.model_id, "large-v3")
        self.assertEqual(profile.key, "accurate")

    def test_both_profile_selects_the_two_supported_models(self):
        self.assertEqual(
            [profile.key for profile in profiles_for("both")],
            ["fast", "accurate"],
        )

    def test_cache_path_is_scoped_to_the_faster_whisper_model(self):
        path = cache_path_for(profiles_for("accurate")[0], Path("/cache"))
        self.assertEqual(
            path,
            Path("/cache/models--Systran--faster-whisper-large-v3"),
        )

    def test_fast_cache_path_uses_the_published_repository_name(self):
        path = cache_path_for(profiles_for("fast")[0], Path("/cache"))
        self.assertEqual(
            path,
            Path("/cache/models--Systran--faster-distil-whisper-large-v3"),
        )

    def test_config_selects_the_accuracy_model(self):
        self.assertEqual(
            [profile.model_id for profile in model_profiles.profiles_for_config(
                {"WHISPER_MODEL_PROFILE": "accurate"}
            )],
            ["large-v3"],
        )

    def test_config_can_select_both_models_for_comparison(self):
        self.assertEqual(
            [profile.key for profile in model_profiles.profiles_for_config(
                {"WHISPER_MODEL_PROFILE": "both"}
            )],
            ["fast", "accurate"],
        )

    def test_legacy_model_setting_remains_supported(self):
        self.assertEqual(
            [profile.model_id for profile in model_profiles.profiles_for_config(
                {"WHISPER_MODEL": "medium.en"}
            )],
            ["medium.en"],
        )

    def test_comparison_artifacts_include_the_profile_key(self):
        audio = Path("meeting.wav")
        profile = profiles_for("accurate")[0]
        self.assertEqual(
            model_profiles.artifact_path(audio, profile, ".txt", comparison=True),
            Path("meeting.wav.accurate.txt"),
        )

    def test_artifact_names_preserve_source_extensions(self):
        profile = profiles_for("fast")[0]
        wav = model_profiles.artifact_path(
            Path("meeting.wav"), profile, ".txt"
        )
        mp3 = model_profiles.artifact_path(
            Path("meeting.mp3"), profile, ".txt"
        )

        self.assertEqual(wav, Path("meeting.wav.txt"))
        self.assertEqual(mp3, Path("meeting.mp3.txt"))
        self.assertNotEqual(wav, mp3)

    def test_comparison_completion_requires_json_and_text_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "meeting.wav"
            audio.touch()
            profile = profiles_for("fast")[0]
            model_profiles.artifact_path(
                audio, profile, ".json", comparison=True
            ).touch()
            self.assertFalse(
                model_profiles.artifacts_complete(audio, profile, comparison=True)
            )
            model_profiles.artifact_path(
                audio, profile, ".txt", comparison=True
            ).touch()
            self.assertFalse(
                model_profiles.artifacts_complete(audio, profile, comparison=True)
            )
            model_profiles.artifact_path(
                audio, profile, ".complete.json", comparison=True
            ).touch()
            self.assertTrue(
                model_profiles.artifacts_complete(audio, profile, comparison=True)
            )

    def test_cache_size_does_not_double_count_snapshot_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blob = root / "blobs" / "model.bin"
            blob.parent.mkdir()
            blob.write_bytes(b"weights")
            snapshot = root / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "model.bin").symlink_to(blob)
            self.assertEqual(model_profiles.directory_size(root), len(b"weights"))

    def test_default_cache_root_honors_xdg_cache_home(self):
        self.assertEqual(
            model_profiles.hub_cache_root(
                {"XDG_CACHE_HOME": "/cache"}, home=Path("/home/test")
            ),
            Path("/cache/huggingface/hub"),
        )


class TranscriberProfileTests(unittest.TestCase):
    def test_private_write_keeps_previous_file_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = load_transcriber({
                "QUEUE_DIR": str(root / "queue"),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(root / "state.sqlite"),
            })
            target = root / "transcript.txt"
            target.write_text("complete", encoding="utf-8")
            original_write_text = Path.write_text

            def interrupted_write(path, text, *args, **kwargs):
                original_write_text(path, "partial", encoding="utf-8")
                raise OSError("interrupted")

            with mock.patch.object(Path, "write_text", new=interrupted_write):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    module.write_private_text(target, "replacement")

            self.assertEqual(target.read_text(encoding="utf-8"), "complete")
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_private_write_flushes_data_before_and_directory_after_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = load_transcriber({
                "QUEUE_DIR": str(root / "queue"),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(root / "state.sqlite"),
            })
            target = root / "transcript.txt"
            events = []
            real_fsync = os.fsync

            def recording_fsync(descriptor):
                kind = "dir" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
                events.append((kind, target.exists()))
                return real_fsync(descriptor)

            with mock.patch.object(module.os, "fsync", recording_fsync):
                module.write_private_text(target, "durable")

            self.assertEqual(target.read_text(encoding="utf-8"), "durable")
            self.assertEqual(events, [("file", False), ("dir", True)])

    def test_transcriber_loads_both_configured_profiles(self):
        module = load_transcriber({
            "QUEUE_DIR": "/queue",
            "VAULT_DIR": "/vault",
            "STATE_DB": "/state.sqlite",
            "WHISPER_MODEL_PROFILE": "both",
        })
        self.assertEqual(
            [profile.key for profile in module.MODEL_PROFILES],
            ["fast", "accurate"],
        )

    def test_comparison_notes_are_labelled_with_the_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = load_transcriber({
                "QUEUE_DIR": str(root / "queue"),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(root / "state.sqlite"),
            })
            audio = root / "meeting.wav"
            audio.touch()
            profile = profiles_for("accurate")[0]
            note = module.write_note(
                audio,
                [{"start": 0, "end": 1, "text": " Hello."}],
                1,
                None,
                profile,
                comparison=True,
            )
            self.assertIn("transcript accurate", note.name)
            self.assertIn("model: large-v3", note.read_text(encoding="utf-8"))

    def test_both_profile_runs_both_models_before_dequeuing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue"
            queue.mkdir()
            audio = queue / "meeting.wav"
            audio.touch()
            state_db = root / "state.sqlite"
            with closing(sqlite3.connect(state_db)) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
                connection.commit()
            module = load_transcriber({
                "QUEUE_DIR": str(queue),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(state_db),
                "AUDIO_EXTS": "wav",
                "WHISPER_MODEL_PROFILE": "both",
            })
            loaded = []

            class FakeSegment:
                start = 0
                end = 1
                text = " Hello."

            class FakeModel:
                def __init__(self, model_id, **kwargs):
                    loaded.append(model_id)

                def transcribe(self, path, **kwargs):
                    return iter([FakeSegment()]), types.SimpleNamespace(duration=1)

            fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
            progress_updates = []
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(
                        module,
                        "write_progress",
                        side_effect=lambda **update: progress_updates.append(update),
                    ):
                self.assertEqual(module.main(), 0)

            self.assertEqual(loaded, ["distil-large-v3", "large-v3"])
            self.assertTrue((queue / "meeting.wav.fast.txt").exists())
            self.assertTrue((queue / "meeting.wav.accurate.txt").exists())
            self.assertEqual(
                os.stat(queue / "meeting.wav.fast.txt").st_mode & 0o777,
                0o600,
            )
            note = next((root / "vault").glob("*.md"))
            self.assertEqual(os.stat(note).st_mode & 0o777, 0o600)
            self.assertFalse(audio.exists())
            with closing(sqlite3.connect(state_db)) as connection:
                transcribed = connection.execute(
                    "SELECT transcribed FROM seen"
                ).fetchone()[0]
            self.assertEqual(transcribed, 1)
            accurate_start = next(
                update for update in progress_updates
                if update.get("phase", "").startswith("Transcribing (Accurate")
            )
            self.assertEqual(accurate_start["files_completed"], 1)
            self.assertEqual(accurate_start["current_percent"], 0)

    def test_dangling_queue_symlink_does_not_block_other_recordings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue"
            queue.mkdir()
            audio = queue / "meeting.wav"
            audio.touch()
            dangling = queue / "deleted.wav"
            dangling.symlink_to(root / "archive" / "deleted.wav")
            state_db = root / "state.sqlite"
            with closing(sqlite3.connect(state_db)) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)",
                    (str(root / "archive" / "deleted.wav"),),
                )
                connection.commit()
            module = load_transcriber({
                "QUEUE_DIR": str(queue),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(state_db),
                "AUDIO_EXTS": "wav",
                "WHISPER_MODEL_PROFILE": "fast",
            })

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
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress"):
                self.assertEqual(module.main(), 0)

            self.assertTrue((queue / "meeting.wav.txt").exists())
            self.assertFalse(dangling.is_symlink())
            with closing(sqlite3.connect(state_db)) as connection:
                rows = dict(connection.execute(
                    "SELECT archived_to, transcribed FROM seen"
                ).fetchall())
            self.assertEqual(rows[str(audio)], 1)
            self.assertEqual(
                rows[str(root / "archive" / "deleted.wav")], 0,
                "an untranscribed recording must not be marked transcribed "
                "just because its archive target was unreachable",
            )

    def test_state_database_is_closed_when_transcription_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue"
            queue.mkdir()
            audio = queue / "meeting.wav"
            audio.touch()
            state_db = root / "state.sqlite"
            with closing(sqlite3.connect(state_db)) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
                connection.commit()
            module = load_transcriber({
                "QUEUE_DIR": str(queue),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(state_db),
                "AUDIO_EXTS": "wav",
                "WHISPER_MODEL_PROFILE": "fast",
            })

            class FailingModel:
                def __init__(self, model_id, **kwargs):
                    pass

                def transcribe(self, path, **kwargs):
                    raise RuntimeError("model crashed")

            connections = []
            real_connect = sqlite3.connect

            def tracking_connect(*args, **kwargs):
                connection = real_connect(*args, **kwargs)
                connections.append(connection)
                return connection

            fake_module = types.SimpleNamespace(WhisperModel=FailingModel)
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress"), \
                    mock.patch.object(module.sqlite3, "connect", tracking_connect):
                with self.assertRaisesRegex(RuntimeError, "model crashed"):
                    module.main()

            self.assertEqual(len(connections), 1)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            self.assertTrue(audio.exists())

    def test_interrupted_completion_marker_keeps_recording_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue"
            queue.mkdir()
            audio = queue / "meeting.wav"
            audio.touch()
            state_db = root / "state.sqlite"
            with closing(sqlite3.connect(state_db)) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
                connection.commit()
            module = load_transcriber({
                "QUEUE_DIR": str(queue),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(state_db),
                "AUDIO_EXTS": "wav",
                "WHISPER_MODEL_PROFILE": "fast",
            })

            class FakeSegment:
                start = 0
                end = 1
                text = " Hello."

            class FakeModel:
                def __init__(self, model_id, **kwargs):
                    pass

                def transcribe(self, path, **kwargs):
                    return iter([FakeSegment()]), types.SimpleNamespace(duration=1)

            real_write = module.write_private_text

            def failing_marker_write(path, text):
                if path.name.endswith(".complete.json"):
                    raise OSError("interrupted before completion marker")
                return real_write(path, text)

            fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress"), \
                    mock.patch.object(
                        module, "write_private_text", failing_marker_write
                    ):
                with self.assertRaisesRegex(OSError, "completion marker"):
                    module.main()

            profile = profiles_for("fast")[0]
            self.assertTrue(audio.is_symlink() or audio.exists())
            self.assertFalse(
                module.artifacts_complete(audio, profile, comparison=False)
            )
            with closing(sqlite3.connect(state_db)) as connection:
                transcribed = connection.execute(
                    "SELECT transcribed FROM seen"
                ).fetchone()[0]
            self.assertEqual(transcribed, 0)

    def test_no_speech_writes_auditable_output_before_dequeuing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue"
            queue.mkdir()
            audio = queue / "silence.wav"
            audio.touch()
            state_db = root / "state.sqlite"
            with closing(sqlite3.connect(state_db)) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
                connection.commit()
            module = load_transcriber({
                "QUEUE_DIR": str(queue),
                "VAULT_DIR": str(root / "vault"),
                "STATE_DB": str(state_db),
                "AUDIO_EXTS": "wav",
                "WHISPER_MODEL_PROFILE": "fast",
            })

            class SilentModel:
                def __init__(self, model_id, **kwargs):
                    pass

                def transcribe(self, path, **kwargs):
                    return iter([]), types.SimpleNamespace(duration=30)

            fake_module = types.SimpleNamespace(WhisperModel=SilentModel)
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                    mock.patch.object(module, "write_progress"):
                self.assertEqual(module.main(), 0)

            result = json.loads(
                (queue / "silence.wav.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["status"], "no_speech")
            self.assertEqual(
                (queue / "silence.wav.txt").read_text(encoding="utf-8"), ""
            )
            note = next((root / "vault").glob("*.md"))
            self.assertIn("No speech was detected", note.read_text(encoding="utf-8"))
            self.assertFalse(audio.exists())


if __name__ == "__main__":
    unittest.main()
