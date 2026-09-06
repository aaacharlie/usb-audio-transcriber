import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config  # noqa: E402


class ConfigurationTests(unittest.TestCase):
    def test_load_accepts_an_explicit_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "custom.env"
            config_path.write_text(
                'ARCHIVE_DIR="${HOME}/archive"\nAUDIO_EXTS="wav,mp3"\n',
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"HOME": "/home/test"}):
                config = pipeline_config.load(config_path)

            self.assertEqual(config["ARCHIVE_DIR"], "/home/test/archive")
            self.assertEqual(config["AUDIO_EXTS"], "wav,mp3")

    def test_load_expands_tilde_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "custom.env"
            config_path.write_text('VAULT_DIR="~/Transcripts"\n', encoding="utf-8")
            with mock.patch.dict("os.environ", {"HOME": "/home/test"}):
                config = pipeline_config.load(config_path)

            self.assertEqual(config["VAULT_DIR"], "/home/test/Transcripts")


class LayoutTests(unittest.TestCase):
    def test_the_data_root_can_be_moved_away_from_the_program_files(self):
        with mock.patch.dict("os.environ", {"USB_AUDIO_TRANSCRIBER_ROOT": "/data/elsewhere"}):
            moved = importlib.reload(pipeline_config)
            self.assertEqual(moved.ROOT, Path("/data/elsewhere"))
            self.assertEqual(moved.ASSETS, ROOT, "prompts and templates stay with the code")
            self.assertEqual(moved.PROGRESS_PATH,
                             Path("/data/elsewhere/var/state/progress.json"))
        restored = importlib.reload(pipeline_config)
        self.assertEqual(restored.ROOT, ROOT)

    def test_version_comes_from_the_version_file_when_not_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            version_file = Path(directory) / "VERSION"
            self.assertEqual(pipeline_config.version(version_file), "dev")
            version_file.write_text("v1.2.3-4-gabcdef\n", encoding="utf-8")
            self.assertEqual(pipeline_config.version(version_file), "v1.2.3-4-gabcdef")


if __name__ == "__main__":
    unittest.main()
