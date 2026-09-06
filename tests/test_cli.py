import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from usb_audio_transcriber import cli  # noqa: E402


class FakeMachine:
    """A temp HOME with fake commands, a fake interpreter, and a fake launcher beside it."""

    def __init__(self, directory, doctor_exit=0, data_home="data"):
        self.root = Path(directory)
        self.fakebin = self.root / "fakebin"
        self.fakebin.mkdir()
        for command in ("ffmpeg", "flock", "tee", "zenity"):
            (self.fakebin / command).symlink_to("/usr/bin/true")
        self.calls = self.root / "systemctl-calls"
        self.script(self.fakebin / "systemctl", f"echo \"$*\" >> '{self.calls}'\n")
        self.script(self.fakebin / "python3",
                    "case $1 in\n"
                    f"  */doctor.py) shift; echo doctor \"$@\" >> '{self.root / 'script-calls'}'; exit {doctor_exit} ;;\n"
                    f"  */setup.py) shift; echo setup \"$@\" >> '{self.root / 'script-calls'}'; exit 0 ;;\n"
                    "  *) exit 0 ;;\n"
                    "esac\n")
        self.launcher = self.fakebin / "usb-audio-transcriber"
        self.script(self.launcher, "exit 0\n")
        self.data_home = self.root / data_home
        self.config_home = self.root / "config"
        self.env = {
            "HOME": str(self.root / "home"),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "PATH": f"{self.fakebin}:/usr/bin:/bin",
        }

    @staticmethod
    def script(path, body):
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def patches(self):
        return [
            mock.patch.dict(os.environ, self.env),
            mock.patch.object(cli, "PYTHON", str(self.fakebin / "python3")),
        ]

    @property
    def data_root(self):
        return self.data_home / "usb-audio-transcriber"

    @property
    def units(self):
        return self.config_home / "systemd" / "user"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # No desktop and no terminal: the wizard must not be attempted.
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "USB_AUDIO_TRANSCRIBER_ROOT"):
            self.addCleanup(mock.patch.dict(os.environ, {}).stop)
        cleared = mock.patch.dict(os.environ, {})
        cleared.start()
        self.addCleanup(cleared.stop)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "USB_AUDIO_TRANSCRIBER_ROOT"):
            os.environ.pop(name, None)

    def start(self, machine):
        for patch in machine.patches():
            patch.start()
            self.addCleanup(patch.stop)

    def test_install_writes_units_and_menu_entry_pointing_at_the_command(self):
        machine = FakeMachine(self.tmp.name, data_home="custom data & 100%")
        self.start(machine)

        code, out, err = run_cli(["install"])

        self.assertEqual(code, 0, err)
        config = machine.data_root / "config.env"
        self.assertTrue(config.is_file())
        self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)
        self.assertEqual(config.read_text(encoding="utf-8"),
                         (ROOT / "config.example.env").read_text(encoding="utf-8"))
        quoted = '"' + str(machine.launcher).replace("%", "%%") + '"'
        service = (machine.units / "usb-audio-transcriber.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart={quoted} cycle\n", service)
        plug = (machine.units / "usb-audio-transcriber-plug.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart={quoted} cycle --wait\n", plug)
        panel = (machine.units / "usb-audio-transcriber-panel.service").read_text(encoding="utf-8")
        self.assertIn(f"ExecStart={quoted} panel serve\n", panel)
        self.assertTrue((machine.units / "usb-audio-transcriber.timer").is_file())
        self.assertIn("PathChanged=/media/%u",
                      (machine.units / "usb-audio-transcriber-plug.path").read_text(encoding="utf-8"))
        desktop = (machine.data_home / "applications" / "usb-audio-transcriber.desktop")
        self.assertIn(f"Exec={quoted} panel open\n", desktop.read_text(encoding="utf-8"))
        self.assertTrue((machine.data_home / "icons" / "hicolor" / "scalable" / "apps" /
                         "usb-audio-transcriber.svg").is_file())
        recorded = machine.calls.read_text(encoding="utf-8").splitlines()
        self.assertEqual(recorded[0], "--user daemon-reload")
        for unit in ("usb-audio-transcriber.timer", "usb-audio-transcriber-plug.path",
                     "usb-audio-transcriber-panel.service"):
            self.assertIn(f"--user enable --now {unit}", recorded)
        scripts = (machine.root / "script-calls").read_text(encoding="utf-8")
        self.assertIn(f"doctor --config {config} --skip-systemd", scripts)
        self.assertNotIn("setup", scripts, "no desktop and no terminal: the wizard is skipped")
        self.assertIn("usb-audio-transcriber setup", out)
        self.assertIn("Installed usb-audio-transcriber", out)

    def test_install_keeps_an_existing_config_and_notes_an_older_copy(self):
        machine = FakeMachine(self.tmp.name)
        self.start(machine)
        machine.data_root.mkdir(parents=True)
        config = machine.data_root / "config.env"
        config.write_text('VAULT_DIR="/home/me/Notes"\n', encoding="utf-8")
        (machine.data_root / "venv").mkdir()

        code, out, err = run_cli(["install", "--no-setup"])

        self.assertEqual(code, 0, err)
        self.assertEqual(config.read_text(encoding="utf-8"), 'VAULT_DIR="/home/me/Notes"\n')
        self.assertIn("older copy installed by install.sh", out)

    def test_a_failing_doctor_stops_the_install_before_any_unit_is_written(self):
        machine = FakeMachine(self.tmp.name, doctor_exit=1)
        self.start(machine)

        code, out, err = run_cli(["install"])

        self.assertEqual(code, 1)
        self.assertIn("doctor found blocking problems", err)
        self.assertFalse(machine.units.exists())
        self.assertFalse(machine.calls.exists(), "systemctl was never called")

    def test_install_needs_ffmpeg_and_the_command(self):
        machine = FakeMachine(self.tmp.name)
        self.start(machine)
        (machine.fakebin / "ffmpeg").unlink()
        code, out, err = run_cli(["install"])
        self.assertEqual(code, 1)
        self.assertIn("ffmpeg is required", err)

        (machine.fakebin / "ffmpeg").symlink_to("/usr/bin/true")
        machine.launcher.unlink()
        code, out, err = run_cli(["install"])
        self.assertEqual(code, 1)
        self.assertIn("pipx install usb-audio-transcriber", err)

    def test_uninstall_removes_units_and_menu_entry_but_keeps_data(self):
        machine = FakeMachine(self.tmp.name)
        self.start(machine)
        self.assertEqual(run_cli(["install", "--no-setup"])[0], 0)
        config = machine.data_root / "config.env"
        recording = machine.data_root / "var" / "logs" / "pipeline.log"
        recording.parent.mkdir(parents=True)
        recording.write_text("log\n", encoding="utf-8")

        code, out, err = run_cli(["uninstall"])

        self.assertEqual(code, 0, err)
        self.assertEqual(list(machine.units.iterdir()), [])
        self.assertFalse((machine.data_home / "applications" / "usb-audio-transcriber.desktop").exists())
        self.assertFalse((machine.data_home / "icons" / "hicolor" / "scalable" / "apps" /
                          "usb-audio-transcriber.svg").exists())
        self.assertTrue(config.is_file())
        self.assertTrue(recording.is_file())
        recorded = machine.calls.read_text(encoding="utf-8").splitlines()
        self.assertIn("--user disable --now usb-audio-transcriber-plug.path", recorded)
        self.assertIn("pipx uninstall usb-audio-transcriber", out)


class ForwardingTests(unittest.TestCase):
    def capture(self, argv):
        calls = []
        with mock.patch.object(cli, "execute", side_effect=lambda p, a, e: calls.append((p, a, e))):
            code, out, err = run_cli(argv)
        return code, calls, out, err

    def test_script_commands_pass_their_arguments_through_with_the_data_root(self):
        code, calls, out, err = self.capture(
            ["--root", "/data/elsewhere", "sessions", "list", "--backend", "openai"])
        self.assertEqual(code, 0, err)
        program, argv, env = calls[0]
        self.assertEqual(program, cli.PYTHON)
        self.assertEqual(argv, [cli.PYTHON, cli.BIN / "sessions.py", "list", "--backend", "openai"])
        self.assertEqual(env["USB_AUDIO_TRANSCRIBER_ROOT"], "/data/elsewhere")
        self.assertEqual(env["USB_AUDIO_TRANSCRIBER_PYTHON"], cli.PYTHON)
        self.assertEqual(env["USB_AUDIO_TRANSCRIBER_BIN"], str(cli.BIN))
        self.assertTrue((cli.BIN / "sessions.py").is_file())

    def test_cycle_runs_the_shell_script_with_the_same_environment(self):
        with mock.patch.dict(os.environ, {"USB_AUDIO_TRANSCRIBER_ROOT": "/data/env-root"}):
            code, calls, out, err = self.capture(["cycle", "--wait"])
        self.assertEqual(code, 0, err)
        program, argv, env = calls[0]
        self.assertTrue(str(program).endswith("bash"))
        self.assertEqual(argv[1:], [cli.BIN / "run-cycle.sh", "--wait"])
        self.assertEqual(env["USB_AUDIO_TRANSCRIBER_ROOT"], "/data/env-root")

    def test_every_script_name_exists(self):
        for name in cli.SCRIPTS.values():
            self.assertTrue((cli.BIN / name).is_file(), name)

    def test_help_version_paths_and_unknown_commands(self):
        code, out, err = run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("usb-audio-transcriber install", out)
        code, out, err = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())
        code, out, err = run_cli(["--root=/data/x", "paths"])
        self.assertEqual(code, 0)
        self.assertIn("Data root:     /data/x", out)
        self.assertIn("Settings:      /data/x/config.env", out)
        code, out, err = run_cli(["launch-missiles"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err)

    def test_render_quotes_the_command_and_doubles_percent_signs(self):
        rendered = cli.render("ExecStart=@CYCLE_COMMAND@ --wait\nExec=@PANEL_COMMAND@ open\n",
                              Path("/home/me/100% mine/bin/usb-audio-transcriber"))
        self.assertEqual(rendered,
                         'ExecStart="/home/me/100%% mine/bin/usb-audio-transcriber" cycle --wait\n'
                         'Exec="/home/me/100%% mine/bin/usb-audio-transcriber" panel open\n')

    def test_default_data_root_matches_install_sh(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg/data"}):
            os.environ.pop("USB_AUDIO_TRANSCRIBER_ROOT", None)
            self.assertEqual(cli.data_root(), Path("/xdg/data/usb-audio-transcriber"))
            self.assertEqual(cli.data_root("~/elsewhere"), Path("~/elsewhere").expanduser())


if __name__ == "__main__":
    unittest.main()
