import fcntl
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


class SessionStepTests(unittest.TestCase):
    def fake_root(self, directory, transcribe_exit):
        root = Path(directory)
        bin_dir = root / "bin"
        venv_bin = root / "venv" / "bin"
        bin_dir.mkdir(parents=True)
        venv_bin.mkdir(parents=True)
        for name in ("ingest.py", "progress-popup.py", "transcribe.py", "sessions.py"):
            (bin_dir / name).touch()
        python = venv_bin / "python"
        log = root / "order"
        python.write_text(
            "#!/usr/bin/env bash\n"
            "case $1 in\n"
            f"  */transcribe.py) echo transcribe >> '{log}'; exit {transcribe_exit} ;;\n"
            f"  */sessions.py) echo sessions >> '{log}'; exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        return root, log

    def test_sessions_run_after_transcription(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log = self.fake_root(directory, transcribe_exit=0)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                capture_output=True, text=True, check=False,
                env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)},
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(log.read_text(encoding="utf-8").split(), ["transcribe", "sessions"])

    def test_sessions_do_not_run_after_a_transcription_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log = self.fake_root(directory, transcribe_exit=3)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                capture_output=True, text=True, check=False,
                env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)},
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(log.read_text(encoding="utf-8").split(), ["transcribe"])


class LockTests(unittest.TestCase):
    def fake_root(self, directory):
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
            f"  */transcribe.py) touch '{marker}'; exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        (root / "var" / "state").mkdir(parents=True)
        return root, marker

    def test_a_running_cycle_makes_a_timer_cycle_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root, marker = self.fake_root(directory)
            with open(root / "var" / "state" / "cycle.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run(
                    ["bash", str(ROOT / "bin" / "run-cycle.sh")],
                    capture_output=True, text=True, check=False,
                    env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)},
                )

            self.assertEqual(result.returncode, 0)
            self.assertIn("cycle already running, skipping", result.stdout)
            self.assertFalse(marker.exists())

    def test_a_plug_in_cycle_waits_for_the_lock_then_gives_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root, marker = self.fake_root(directory)
            with open(root / "var" / "state" / "cycle.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run(
                    ["bash", str(ROOT / "bin" / "run-cycle.sh"), "--wait"],
                    capture_output=True, text=True, check=False, timeout=30,
                    env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root),
                                      "USB_AUDIO_TRANSCRIBER_LOCK_WAIT": "1"},
                )

            self.assertEqual(result.returncode, 0)
            self.assertIn("cycle still running after 1s, skipping", result.stdout)
            self.assertFalse(marker.exists())

    def test_a_plug_in_cycle_runs_when_nothing_else_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root, marker = self.fake_root(directory)
            result = subprocess.run(
                ["bash", str(ROOT / "bin" / "run-cycle.sh"), "--wait"],
                capture_output=True, text=True, check=False, timeout=30,
                env=os.environ | {"USB_AUDIO_TRANSCRIBER_ROOT": str(root)},
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
