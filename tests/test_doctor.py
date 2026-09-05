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


class DoctorWatchDirTests(unittest.TestCase):
    def base_config(self, **extra):
        return {
            "ARCHIVE_DIR": "/archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "/state/seen.sqlite",
            "VAULT_DIR": "/transcripts",
            "AUDIO_EXTS": "wav",
            **extra,
        }

    def test_check_config_rejects_relative_watch_dirs(self):
        failures = doctor.check_config(self.base_config(WATCH_DIRS="Sync/Memos:/srv/audio"))

        self.assertIn("WATCH_DIRS entry must be an absolute path: Sync/Memos", failures)
        self.assertEqual(len([f for f in failures if "WATCH_DIRS" in f]), 1)

    def test_check_config_rejects_watching_the_archive_itself(self):
        failures = doctor.check_config(self.base_config(WATCH_DIRS="/archive"))

        self.assertIn(
            "WATCH_DIRS entry must not be one of the pipeline's own paths: /archive",
            failures,
        )

    def test_missing_watch_dir_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            present = Path(directory) / "present"
            present.mkdir()
            missing = Path(directory) / "missing"
            config = self.base_config(WATCH_DIRS=f"{present}:{missing}")

            self.assertEqual(doctor.check_config(config), [])
            self.assertEqual(
                doctor.check_watch_dirs(config),
                [f"WATCH_DIRS folder is not a directory right now: {missing}"],
            )


class DoctorHeadlessTests(unittest.TestCase):
    def test_check_config_rejects_unknown_headless_values(self):
        failures = doctor.check_config({
            "ARCHIVE_DIR": "/archive",
            "QUEUE_DIR": "/queue",
            "STATE_DB": "/state/seen.sqlite",
            "VAULT_DIR": "/transcripts",
            "AUDIO_EXTS": "wav",
            "HEADLESS": "sometimes",
        })

        self.assertIn("HEADLESS must be auto, 0, or 1", failures)

    def test_missing_zenity_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.env"
            config_path.write_text(
                f'ARCHIVE_DIR="{directory}/archive"\n'
                f'QUEUE_DIR="{directory}/queue"\n'
                f'STATE_DB="{directory}/state/seen.sqlite"\n'
                f'VAULT_DIR="{directory}/transcripts"\n'
                'AUDIO_EXTS="wav"\n',
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch(
                        "shutil.which",
                        side_effect=lambda command:
                        None if command in {"zenity", "notify-send"} else "/usr/bin/tool",
                    ), \
                    mock.patch("importlib.util.find_spec", return_value=object()), \
                    mock.patch("sys.stdout", output):
                result = doctor.main([
                    "--config", str(config_path), "--skip-systemd"
                ])

            self.assertEqual(result, 0)
            self.assertIn("WARN command: zenity not found", output.getvalue())
            self.assertIn("WARN command: notify-send not found", output.getvalue())

    def test_linger_warning_explains_headless_timers(self):
        completed = mock.Mock(returncode=0, stdout="no\n")
        with mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/loginctl"), \
                mock.patch.object(doctor.subprocess, "run", return_value=completed):
            warning = doctor.linger_warning("pi")

        self.assertIn("loginctl enable-linger pi", warning)

    def test_linger_warning_is_silent_when_lingering_is_on_or_unknown(self):
        with mock.patch.object(doctor.shutil, "which", return_value="/usr/bin/loginctl"):
            with mock.patch.object(
                doctor.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="yes\n")
            ):
                self.assertIsNone(doctor.linger_warning("pi"))
            with mock.patch.object(
                doctor.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")
            ):
                self.assertIsNone(doctor.linger_warning("pi"))
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            self.assertIsNone(doctor.linger_warning("pi"))


class DoctorPathTests(unittest.TestCase):
    def test_writable_parent_checks_dotted_directories_themselves(self):
        with tempfile.TemporaryDirectory() as directory:
            dotted = Path(directory) / "notes.d"
            dotted.mkdir()

            self.assertEqual(doctor.writable_parent(dotted), dotted)

    def test_writable_parent_checks_the_parent_of_file_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()

            self.assertEqual(
                doctor.writable_parent(state / "seen.sqlite", is_file=True),
                state,
            )


class DoctorCliTests(unittest.TestCase):
    def test_main_fails_when_a_runtime_command_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.env"
            config_path.write_text(
                f'ARCHIVE_DIR="{directory}/archive"\n'
                f'QUEUE_DIR="{directory}/queue"\n'
                f'STATE_DB="{directory}/state/seen.sqlite"\n'
                f'VAULT_DIR="{directory}/transcripts"\n'
                'AUDIO_EXTS="wav"\n',
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch(
                        "shutil.which",
                        side_effect=lambda command:
                        None if command == "flock" else "/usr/bin/tool",
                    ), \
                    mock.patch("importlib.util.find_spec", return_value=object()), \
                    mock.patch("sys.stdout", output):
                result = doctor.main([
                    "--config", str(config_path), "--skip-systemd"
                ])

            self.assertEqual(result, 1)
            self.assertIn("FAIL command: flock not found", output.getvalue())

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
