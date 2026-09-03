import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("doctor", ROOT / "bin" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorConfigTests(unittest.TestCase):
    def test_check_config_reports_missing_required_settings(self):
        failures = doctor.check_config({"ARCHIVE_DIR": "/archive"})

        self.assertIn("missing setting: QUEUE_DIR", failures)
        self.assertIn("missing setting: STATE_DB", failures)
        self.assertIn("missing setting: VAULT_DIR", failures)
        self.assertIn("missing setting: AUDIO_EXTS", failures)

    def test_check_config_reports_invalid_model_profile(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "/state/seen.sqlite",
            "VAULT_DIR": "/transcripts",
            "AUDIO_EXTS": "wav",
            "WHISPER_MODEL_PROFILE": "fastest",
        })

        self.assertIn(
            "invalid WHISPER_MODEL_PROFILE: model profile must be fast, accurate, or both",
            failures,
        )

    def test_check_config_rejects_ambiguous_purge_setting(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "/state/seen.sqlite",
            "VAULT_DIR": "/transcripts",
            "AUDIO_EXTS": "wav",
            "PURGE_DEVICE": "yes",
        })

        self.assertIn("PURGE_DEVICE must be 0 or 1", failures)

    def test_check_config_rejects_relative_paths(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "state/seen.sqlite",
            "VAULT_DIR": "~transcripts",
            "AUDIO_EXTS": "wav",
        })

        self.assertIn("ARCHIVE_DIR must be an absolute path", failures)
        self.assertIn("STATE_DB must be an absolute path", failures)
        self.assertIn("VAULT_DIR must be an absolute path", failures)
        self.assertNotIn("QUEUE_DIR must be an absolute path", failures)

    def test_check_config_rejects_shared_paths_between_settings(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/data/audio",
            "QUEUE_DIR": "/data/audio/",
            "STATE_DB": "/data/state/seen.sqlite",
            "VAULT_DIR": "/data/transcripts",
            "AUDIO_EXTS": "wav",
        })

        self.assertIn(
            "ARCHIVE_DIR and QUEUE_DIR must be distinct paths", failures
        )

    def test_check_config_rejects_nonpositive_numeric_settings(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "/state/seen.sqlite",
            "VAULT_DIR": "/transcripts",
            "AUDIO_EXTS": "wav",
            "VAD_MIN_SILENCE_MS": "0",
            "MAP_WINDOW_CHARS": "many",
        })

        self.assertIn("VAD_MIN_SILENCE_MS must be a positive integer", failures)
        self.assertIn("MAP_WINDOW_CHARS must be a positive integer", failures)


class DoctorCliTests(unittest.TestCase):
    def test_main_reports_a_healthy_local_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.env"
            config_path.write_text(
                "\n".join((
                    f'ARCHIVE_DIR="{root / "archive"}"',
                    f'QUEUE_DIR="{root / "queue"}"',
                    f'STATE_DB="{root / "state" / "seen.sqlite"}"',
                    f'VAULT_DIR="{root / "transcripts"}"',
                    'AUDIO_EXTS="wav,mp3"',
                    'WHISPER_MODEL_PROFILE="fast"',
                )),
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch("shutil.which", return_value="/usr/bin/tool"), \
                    mock.patch("importlib.util.find_spec", return_value=object()), \
                    mock.patch("sys.stdout", output):
                result = doctor.main([
                    "--config", str(config_path), "--skip-systemd"
                ])

            self.assertEqual(result, 0)
            self.assertIn("OK  configuration", output.getvalue())
            self.assertIn("Doctor found no blocking problems.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
