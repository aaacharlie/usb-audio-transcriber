import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_install_honors_xdg_data_home_in_service_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command in ("ffmpeg", "zenity", "systemctl"):
                (fake_bin / command).symlink_to("/usr/bin/true")
            python = fake_bin / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ $1 == -m && $2 == venv ]]; then\n"
                "  mkdir -p \"$3/bin\"\n"
                "  ln -s /usr/bin/true \"$3/bin/pip\"\n"
                "  ln -s /usr/bin/true \"$3/bin/python\"\n"
                "fi\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            data_home = root / "custom data & 100%"
            config_home = root / "custom config"
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(data_home),
                "XDG_CONFIG_HOME": str(config_home),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            service = (
                config_home / "systemd" / "user" /
                "usb-audio-transcriber.service"
            ).read_text(encoding="utf-8")
            escaped_root = str(data_home).replace("%", "%%")
            self.assertIn(
                f'ExecStart="{escaped_root}/usb-audio-transcriber/bin/run-cycle.sh"',
                service,
            )
            self.assertIn("UMask=0077", service)
            plug_service = (
                config_home / "systemd" / "user" /
                "usb-audio-transcriber-plug.service"
            ).read_text(encoding="utf-8")
            self.assertIn(
                f'ExecStart="{escaped_root}/usb-audio-transcriber/bin/run-cycle.sh" --wait',
                plug_service,
            )
            plug_path = (
                config_home / "systemd" / "user" / "usb-audio-transcriber-plug.path"
            ).read_text(encoding="utf-8")
            self.assertIn("PathChanged=/media/%u", plug_path)
            self.assertIn("Unit=usb-audio-transcriber-plug.service", plug_path)
            panel_service = (
                config_home / "systemd" / "user" / "usb-audio-transcriber-panel.service"
            ).read_text(encoding="utf-8")
            self.assertIn(
                f'"{escaped_root}/usb-audio-transcriber/bin/panel.py" serve', panel_service
            )
            desktop = (data_home / "applications" / "usb-audio-transcriber.desktop").read_text(
                encoding="utf-8"
            )
            self.assertIn(f'"{escaped_root}/usb-audio-transcriber/bin/panel.py" open', desktop)
            self.assertIn("Icon=usb-audio-transcriber", desktop)
            self.assertTrue(
                (data_home / "icons" / "hicolor" / "scalable" / "apps" /
                 "usb-audio-transcriber.svg").is_file()
            )
            self.assertTrue((data_home / "usb-audio-transcriber" / "panel" / "index.html").is_file())
            self.assertTrue((data_home / "usb-audio-transcriber" / "VERSION").is_file())
            config = data_home / "usb-audio-transcriber" / "config.env"
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)

    def test_install_enables_the_timer_and_the_plug_in_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command in ("ffmpeg", "zenity"):
                (fake_bin / command).symlink_to("/usr/bin/true")
            calls = root / "systemctl-calls"
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                f"#!/usr/bin/env bash\necho \"$*\" >> '{calls}'\n", encoding="utf-8"
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
            python = fake_bin / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ $1 == -m && $2 == venv ]]; then\n"
                "  mkdir -p \"$3/bin\"\n"
                "  ln -s /usr/bin/true \"$3/bin/pip\"\n"
                "  ln -s /usr/bin/true \"$3/bin/python\"\n"
                "fi\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            recorded = calls.read_text(encoding="utf-8").splitlines()
            self.assertIn("--user enable --now usb-audio-transcriber.timer", recorded)
            self.assertIn("--user enable --now usb-audio-transcriber-plug.path", recorded)
            self.assertIn("--user enable --now usb-audio-transcriber-panel.service", recorded)
            self.assertLess(
                recorded.index("--user daemon-reload"),
                recorded.index("--user enable --now usb-audio-transcriber-plug.path"),
            )

    def test_failed_dependency_install_preserves_deployed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command in ("ffmpeg", "zenity", "systemctl", "flock", "tee"):
                (fake_bin / command).symlink_to("/usr/bin/true")
            python = fake_bin / "python3"
            python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ $1 == -m && $2 == venv ]]; then\n"
                "  mkdir -p \"$3/bin\"\n"
                "  printf '#!/usr/bin/env bash\\nexit 1\\n' > \"$3/bin/pip\"\n"
                "  chmod +x \"$3/bin/pip\"\n"
                "  ln -sf /usr/bin/true \"$3/bin/python\"\n"
                "fi\n",
                encoding="utf-8",
            )
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            data_home = root / "data"
            config_home = root / "config"
            deployed = data_home / "usb-audio-transcriber" / "bin" / "ingest.py"
            deployed.parent.mkdir(parents=True)
            deployed.write_text("previous working deployment\n", encoding="utf-8")
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(data_home),
                "XDG_CONFIG_HOME": str(config_home),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                deployed.read_text(encoding="utf-8"),
                "previous working deployment\n",
            )
            self.assertFalse(
                (config_home / "systemd" / "user" /
                 "usb-audio-transcriber.service").exists()
            )

    def test_install_rejects_unsupported_python_before_writing_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command in ("ffmpeg", "zenity", "systemctl", "flock", "tee"):
                (fake_bin / command).symlink_to("/usr/bin/true")
            python = fake_bin / "python3"
            python.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            data_home = root / "data"
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(data_home),
                "XDG_CONFIG_HOME": str(root / "config"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Python 3.10 or newer is required", result.stderr)
            self.assertFalse((data_home / "usb-audio-transcriber").exists())


class UninstallTests(unittest.TestCase):
    def test_uninstall_preserves_configuration_and_data_under_install_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            (fake_bin / "systemctl").symlink_to("/usr/bin/true")
            data_home = root / "data"
            install_root = data_home / "usb-audio-transcriber"
            (install_root / "bin").mkdir(parents=True)
            (install_root / "venv").mkdir()
            recording = install_root / "archive" / "meeting.wav"
            recording.parent.mkdir()
            recording.write_bytes(b"recording")
            config = install_root / "config.env"
            config.write_text(
                f'ARCHIVE_DIR="{install_root / "archive"}"\n',
                encoding="utf-8",
            )
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(data_home),
                "XDG_CONFIG_HOME": str(root / "config"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "uninstall.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(config.exists())
            self.assertTrue(recording.exists())
            self.assertFalse((install_root / "bin").exists())
            self.assertFalse((install_root / "venv").exists())

    def test_uninstall_removes_the_plug_in_trigger_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            calls = root / "systemctl-calls"
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                f"#!/usr/bin/env bash\necho \"$*\" >> '{calls}'\n", encoding="utf-8"
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)
            unit_dir = root / "config" / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            for name in ("usb-audio-transcriber.service", "usb-audio-transcriber.timer",
                         "usb-audio-transcriber-plug.service",
                         "usb-audio-transcriber-plug.path",
                         "usb-audio-transcriber-panel.service"):
                (unit_dir / name).write_text("[Unit]\n", encoding="utf-8")
            desktop = root / "data" / "applications" / "usb-audio-transcriber.desktop"
            desktop.parent.mkdir(parents=True)
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            env = os.environ | {
                "HOME": str(root / "home"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            }

            result = subprocess.run(
                ["bash", str(ROOT / "uninstall.sh")],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(unit_dir.iterdir()), [])
            self.assertFalse(desktop.exists())
            self.assertIn(
                "--user disable --now usb-audio-transcriber-plug.path",
                calls.read_text(encoding="utf-8").splitlines(),
            )
            self.assertIn(
                "--user disable --now usb-audio-transcriber-panel.service",
                calls.read_text(encoding="utf-8").splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
