import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
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
            Path("meeting.accurate.txt"),
        )

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
            with sqlite3.connect(state_db) as connection:
                connection.execute(
                    "CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)"
                )
                connection.execute(
                    "INSERT INTO seen VALUES (?, 0)", (str(audio),)
                )
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
            self.assertTrue((queue / "meeting.fast.txt").exists())
            self.assertTrue((queue / "meeting.accurate.txt").exists())
            self.assertFalse(audio.exists())
            with sqlite3.connect(state_db) as connection:
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


if __name__ == "__main__":
    unittest.main()
