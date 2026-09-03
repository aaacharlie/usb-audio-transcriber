import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config


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


if __name__ == "__main__":
    unittest.main()
