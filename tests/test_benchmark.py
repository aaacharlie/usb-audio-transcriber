import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
from model_profiles import ACCURATE, FAST

spec = importlib.util.spec_from_file_location(
    "benchmark_models", ROOT / "bin" / "benchmark-models.py"
)
benchmark_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark_models)


class BenchmarkNamingTests(unittest.TestCase):
    def test_default_output_dir_keeps_source_extension(self):
        self.assertEqual(
            benchmark_models.default_output_dir(Path("/recordings/meeting.wav")),
            Path("/recordings/meeting.wav-whisper-ab"),
        )

    def test_profile_artifacts_keep_source_extension_and_profile(self):
        json_path, txt_path = benchmark_models.profile_artifacts(
            Path("/out"), Path("/recordings/meeting.wav"), FAST
        )
        self.assertEqual(json_path, Path("/out/meeting.wav.fast.json"))
        self.assertEqual(txt_path, Path("/out/meeting.wav.fast.txt"))

    def test_wav_and_mp3_sources_never_share_paths(self):
        def paths_for(name):
            audio = Path("/recordings") / name
            directory = benchmark_models.default_output_dir(audio)
            collected = {directory}
            for profile in (FAST, ACCURATE):
                collected.update(
                    benchmark_models.profile_artifacts(directory, audio, profile)
                )
            return collected

        self.assertFalse(paths_for("meeting.wav") & paths_for("meeting.mp3"))


if __name__ == "__main__":
    unittest.main()
