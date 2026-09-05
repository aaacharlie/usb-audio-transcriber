import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]


def make_source_repo(path):
    """A throwaway git repository holding the installer and program files."""
    path.mkdir()
    for name in ("install.sh", "bootstrap.sh", "requirements.txt",
                 "requirements-diarization.txt", "config.example.env"):
        shutil.copy(ROOT / name, path / name)
    for directory in ("bin", "systemd", "prompts", "panel", "share"):
        shutil.copytree(ROOT / directory, path / directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(GIT + ["init", "--quiet", str(path)], check=True)
    subprocess.run(GIT + ["-C", str(path), "checkout", "--quiet", "-b", "main"], check=True)
    subprocess.run(GIT + ["-C", str(path), "add", "-A"], check=True)
    subprocess.run(GIT + ["-C", str(path), "commit", "--quiet", "-m", "initial"], check=True)


class BootstrapTests(unittest.TestCase):
    def environment(self, root):
        fake_bin = root / "fakebin"
        fake_bin.mkdir()
        for command in ("ffmpeg", "zenity", "systemctl"):
            (fake_bin / command).symlink_to("/usr/bin/true")
        python = fake_bin / "python3"
        python.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ $1 == -m && $2 == venv ]]; then\n"
            "  mkdir -p \"$3/bin\"\n"
            "  ln -sf /usr/bin/true \"$3/bin/pip\"\n"
            "  ln -sf /usr/bin/true \"$3/bin/python\"\n"
            "fi\n",
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        return os.environ | {
            "HOME": str(root / "home"),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "USB_AUDIO_TRANSCRIBER_REPO": str(root / "origin"),
            "USB_AUDIO_TRANSCRIBER_SRC": str(root / "data" / "usb-audio-transcriber" / "src"),
        }

    def test_bootstrap_clones_installs_and_later_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin"
            make_source_repo(origin)
            env = self.environment(root)

            first = subprocess.run(
                ["bash", str(origin / "bootstrap.sh"), "--no-setup"],
                capture_output=True, text=True, env=env, check=False,
                stdin=subprocess.DEVNULL,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("Downloading to", first.stdout)
            src = root / "data" / "usb-audio-transcriber" / "src"
            self.assertTrue((src / ".git").is_dir())
            self.assertTrue((root / "data" / "usb-audio-transcriber" / "bin" / "ingest.py").is_file())
            self.assertTrue(
                (root / "config" / "systemd" / "user" / "usb-audio-transcriber.timer").is_file()
            )

            (origin / "NEWS.md").write_text("update\n", encoding="utf-8")
            subprocess.run(GIT + ["-C", str(origin), "add", "-A"], check=True)
            subprocess.run(GIT + ["-C", str(origin), "commit", "--quiet", "-m", "update"], check=True)
            second = subprocess.run(
                ["bash", str(origin / "bootstrap.sh"), "--no-setup"],
                capture_output=True, text=True, env=env, check=False,
                stdin=subprocess.DEVNULL,
            )

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Updating", second.stdout)
            self.assertTrue((src / "NEWS.md").is_file())

    def test_bootstrap_requires_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty_bin = root / "empty"
            empty_bin.mkdir()
            (empty_bin / "bash").symlink_to("/bin/bash")
            result = subprocess.run(
                ["bash", str(ROOT / "bootstrap.sh")],
                capture_output=True, text=True, check=False,
                env={"PATH": str(empty_bin), "HOME": str(root)},
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("git is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
