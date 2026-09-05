import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import setup as wizard


def make_vault(path):
    (path / ".obsidian").mkdir(parents=True)
    return path


class ScriptedUI:
    """Answers questions from a list; None answers mean cancel."""

    def __init__(self, choices=(), answers=(), secrets=()):
        self.choices, self.answers, self.secrets = list(choices), list(answers), list(secrets)
        self.messages = []
        self.questions = []

    def choose(self, title, text, options):
        self.questions.append(("choose", title, list(options)))
        return self.choices.pop(0)

    def ask(self, title, default=""):
        self.questions.append(("ask", title, default))
        answer = self.answers.pop(0)
        return default if answer == "" else answer

    def secret(self, title):
        self.questions.append(("secret", title, None))
        return self.secrets.pop(0) if self.secrets else ""

    def info(self, text):
        self.messages.append(text)


class VaultDetectionTests(unittest.TestCase):
    def test_vaults_come_from_obsidians_own_list_across_package_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            native = make_vault(home / "Notes")
            flatpak_vault = make_vault(home / "Work")
            native_config = home / ".config" / "obsidian" / "obsidian.json"
            native_config.parent.mkdir(parents=True)
            native_config.write_text(json.dumps({"vaults": {
                "a": {"path": str(native), "ts": 1},
                "b": {"path": str(home / "Gone"), "ts": 2},
            }}), encoding="utf-8")
            flatpak_config = home / ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"
            flatpak_config.parent.mkdir(parents=True)
            flatpak_config.write_text(json.dumps({"vaults": {
                "c": {"path": str(flatpak_vault)},
                "d": {"path": str(native)},
            }}), encoding="utf-8")

            self.assertEqual(wizard.vaults_from_config(home), [native, flatpak_vault])
            self.assertEqual(wizard.find_vaults(home), [native, flatpak_vault])

    def test_search_is_bounded_and_skips_hidden_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            shallow = make_vault(home / "Documents" / "Notes")
            make_vault(home / "a" / "b" / "c" / "d" / "e" / "Deep")
            make_vault(home / ".hidden" / "Secret")
            (home / "Documents" / "Notes" / "Sub" / ".obsidian").mkdir(parents=True)

            self.assertEqual(wizard.vaults_by_search(home), [shallow])
            self.assertEqual(wizard.find_vaults(home), [shallow])

    def test_broken_obsidian_config_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / ".config" / "obsidian" / "obsidian.json"
            config.parent.mkdir(parents=True)
            config.write_text("{not json", encoding="utf-8")

            self.assertEqual(wizard.vaults_from_config(home), [])


class ConfigWritingTests(unittest.TestCase):
    def test_settings_are_replaced_in_place_and_comments_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.env"
            config.write_text(
                "# Where notes go\n"
                'VAULT_DIR="${HOME}/old"\n'
                'AUDIO_EXTS="wav"\n',
                encoding="utf-8",
            )

            wizard.write_config(config, {"VAULT_DIR": "/vault/Recordings", "SESSION_SUBJECT": "law"})

            self.assertEqual(
                config.read_text(encoding="utf-8"),
                "# Where notes go\n"
                'VAULT_DIR="/vault/Recordings"\n'
                'AUDIO_EXTS="wav"\n'
                'SESSION_SUBJECT="law"\n',
            )
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)


class WizardFlowTests(unittest.TestCase):
    def test_picking_a_vault_creates_the_folder_and_writes_the_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            vault = make_vault(home / "Notes")
            config = home / "config.env"
            config.write_text('VAULT_DIR="/default"\nOPENROUTER_API_KEY=""\n', encoding="utf-8")
            ui = ScriptedUI(choices=[0], answers=["", "landlord meetings"], secrets=["sk-test"])

            self.assertTrue(wizard.run(ui, config, home))

            self.assertTrue((vault / "Recordings").is_dir())
            text = config.read_text(encoding="utf-8")
            self.assertIn(f'VAULT_DIR="{vault / "Recordings"}"', text)
            self.assertIn('SESSION_SUBJECT="landlord meetings"', text)
            self.assertIn('OPENROUTER_API_KEY="sk-test"', text)
            self.assertEqual(ui.questions[0][2], [str(vault), wizard.OTHER])
            self.assertIn("AI summaries: on", ui.messages[-1])

    def test_cancelling_leaves_the_configuration_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            make_vault(home / "Notes")
            config = home / "config.env"
            config.write_text('VAULT_DIR="/default"\n', encoding="utf-8")
            ui = ScriptedUI(choices=[None])

            self.assertFalse(wizard.run(ui, config, home))

            self.assertEqual(config.read_text(encoding="utf-8"), 'VAULT_DIR="/default"\n')

    def test_without_a_vault_a_typed_path_is_used_and_the_key_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config.env"
            config.write_text('VAULT_DIR="/default"\nOPENROUTER_API_KEY="keep"\n', encoding="utf-8")
            ui = ScriptedUI(answers=[str(home / "Transcripts"), ""])

            self.assertTrue(wizard.run(ui, config, home))

            self.assertTrue((home / "Transcripts").is_dir())
            text = config.read_text(encoding="utf-8")
            self.assertIn(f'VAULT_DIR="{home / "Transcripts"}"', text)
            self.assertIn('OPENROUTER_API_KEY="keep"', text)
            self.assertFalse(any(q[0] == "secret" for q in ui.questions))

    def test_relative_paths_are_rejected_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            config = home / "config.env"
            config.write_text('VAULT_DIR="/default"\n', encoding="utf-8")
            ui = ScriptedUI(answers=["relative/notes"])

            self.assertFalse(wizard.run(ui, config, home))

            self.assertEqual(config.read_text(encoding="utf-8"), 'VAULT_DIR="/default"\n')
            self.assertIn("absolute path", ui.messages[-1])


class EntryPointTests(unittest.TestCase):
    def test_non_interactive_flag_does_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.env"
            config.write_text('VAULT_DIR="/default"\n', encoding="utf-8")

            self.assertEqual(wizard.main(["--config", str(config), "--non-interactive"]), 0)

            self.assertEqual(config.read_text(encoding="utf-8"), 'VAULT_DIR="/default"\n')

    def test_no_desktop_and_no_terminal_skips_with_a_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.env"
            config.write_text('VAULT_DIR="/default"\n', encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(wizard, "has_display", return_value=False), \
                    mock.patch.object(wizard.sys.stdin, "isatty", return_value=False), \
                    mock.patch("sys.stdout", output):
                self.assertEqual(wizard.main(["--config", str(config)]), 0)

            self.assertIn("Setup skipped", output.getvalue())

    def test_zenity_list_maps_the_answer_back_to_an_index(self):
        completed = mock.Mock(returncode=0, stdout="/home/me/Work\n")
        with mock.patch.object(wizard.subprocess, "run", return_value=completed) as run:
            index = wizard.Zenity().choose("t", "x", ["/home/me/Notes", "/home/me/Work"])

        self.assertEqual(index, 1)
        self.assertIn("--list", run.call_args.args[0])
        cancelled = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(wizard.subprocess, "run", return_value=cancelled):
            self.assertIsNone(wizard.Zenity().choose("t", "x", ["a"]))
            self.assertIsNone(wizard.Zenity().ask("t", "d"))

    def test_terminal_choice_accepts_enter_number_and_q(self):
        ui = wizard.Terminal()
        with mock.patch("builtins.input", side_effect=["", "2", "q"]), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(ui.choose("t", "x", ["a", "b"]), 0)
            self.assertEqual(ui.choose("t", "x", ["a", "b"]), 1)
            self.assertIsNone(ui.choose("t", "x", ["a", "b"]))


class InstallerIntegrationTests(unittest.TestCase):
    def install(self, root, args=(), env_extra=None, prior_config=False):
        fake_bin = root / "bin"
        fake_bin.mkdir(exist_ok=True)
        for command in ("ffmpeg", "zenity", "systemctl"):
            link = fake_bin / command
            if not link.exists():
                link.symlink_to("/usr/bin/true")
        calls = root / "python-calls"
        python = fake_bin / "python3"
        python.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ $1 == -m && $2 == venv ]]; then\n"
            "  mkdir -p \"$3/bin\"\n"
            "  ln -sf /usr/bin/true \"$3/bin/pip\"\n"
            f"  printf '#!/usr/bin/env bash\\necho \"$*\" >> {calls}\\n' > \"$3/bin/python\"\n"
            "  chmod +x \"$3/bin/python\"\n"
            "fi\n",
            encoding="utf-8",
        )
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        data_home = root / "data"
        if prior_config:
            (data_home / "usb-audio-transcriber").mkdir(parents=True, exist_ok=True)
            (data_home / "usb-audio-transcriber" / "config.env").write_text(
                'VAULT_DIR="/kept"\n', encoding="utf-8"
            )
        env = os.environ | {
            "HOME": str(root / "home"),
            "XDG_DATA_HOME": str(data_home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "DISPLAY": ":0",
        } | (env_extra or {})
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), *args],
            capture_output=True, text=True, env=env, check=False, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    def test_fresh_install_runs_the_wizard_then_rechecks_the_config(self):
        with tempfile.TemporaryDirectory() as directory:
            recorded = self.install(Path(directory))

            setup_calls = [line for line in recorded if "setup.py" in line]
            doctor_calls = [line for line in recorded if "doctor.py" in line]
            self.assertEqual(len(setup_calls), 1)
            self.assertIn("--config", setup_calls[0])
            self.assertEqual(len(doctor_calls), 2)
            self.assertLess(recorded.index(doctor_calls[0]), recorded.index(setup_calls[0]))

    def test_existing_config_and_no_setup_skip_the_wizard(self):
        with tempfile.TemporaryDirectory() as directory:
            recorded = self.install(Path(directory), prior_config=True)
            self.assertFalse(any("setup.py" in line for line in recorded))
        with tempfile.TemporaryDirectory() as directory:
            recorded = self.install(Path(directory), args=["--no-setup"])
            self.assertFalse(any("setup.py" in line for line in recorded))

    def test_unknown_install_option_is_rejected(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--bogus"],
            capture_output=True, text=True, check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)


if __name__ == "__main__":
    unittest.main()
