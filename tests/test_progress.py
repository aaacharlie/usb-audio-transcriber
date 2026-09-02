import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config

spec = importlib.util.spec_from_file_location("progress_popup", ROOT / "bin" / "progress-popup.py")
progress_popup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress_popup)


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


if __name__ == "__main__":
    unittest.main()
