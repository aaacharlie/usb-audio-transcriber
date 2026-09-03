import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RunCycleTests(unittest.TestCase):
    def test_ingest_failure_stops_cycle_and_propagates_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            venv_bin = root / "venv" / "bin"
            bin_dir.mkdir(parents=True)
            venv_bin.mkdir(parents=True)
            for name in ("ingest.py", "progress-popup.py", "transcribe.py"):
                (bin_dir / name).touch()
            python = venv_bin / "python"
            marker = root / "transcribe-ran"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "case $1 in\n"
                "  */ingest.py) echo 'ingest failed'; exit 9 ;;\n"
                f"  */transcribe.py) touch '{marker}'; exit 0 ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            env = os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)}

            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 9)
            self.assertFalse(marker.exists())
            self.assertIn("ingest failed", result.stdout)


    def test_cycle_does_not_wait_for_a_lingering_popup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            venv_bin = root / "venv" / "bin"
            bin_dir.mkdir(parents=True)
            venv_bin.mkdir(parents=True)
            for name in ("ingest.py", "progress-popup.py", "transcribe.py"):
                (bin_dir / name).touch()
            python = venv_bin / "python"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "case $1 in\n"
                "  */progress-popup.py) sleep 30 ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            env = os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)}

            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0)

    def test_headless_popup_failure_does_not_stop_transcription(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            venv_bin = root / "venv" / "bin"
            bin_dir.mkdir(parents=True)
            venv_bin.mkdir(parents=True)
            for name in ("ingest.py", "progress-popup.py", "transcribe.py"):
                (bin_dir / name).touch()
            python = venv_bin / "python"
            marker = root / "transcribe-ran"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "case $1 in\n"
                "  */progress-popup.py) echo 'no display'; exit 7 ;;\n"
                f"  */transcribe.py) touch '{marker}'; exit 0 ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            env = os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)}

            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
