#!/usr/bin/env python3
"""First-run setup: find your Obsidian vault and write the essentials to config.env.

install.sh runs this on a fresh configuration. Run it again at any time to
change where notes go. It uses Zenity dialogs on a desktop and plain terminal
prompts otherwise, and it never touches anything except the settings it asks
about.
"""
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import ROOT, has_display, load

OBSIDIAN_CONFIGS = (
    ".config/obsidian/obsidian.json",                                 # native package
    ".var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json",    # Flatpak
    "snap/obsidian/current/.config/obsidian/obsidian.json",           # Snap
)
DEFAULT_FOLDER = "Recordings"
SEARCH_DEPTH = 4
SKIP_DIRS = {"node_modules", "snap", "venv", ".venv", "Trash"}
OTHER = "Somewhere else (type a path)"


def vaults_from_config(home=None):
    """Vaults Obsidian itself knows about, in the order it lists them."""
    home = Path.home() if home is None else Path(home)
    found = []
    for relative in OBSIDIAN_CONFIGS:
        path = home / relative
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for entry in (data.get("vaults") or {}).values():
            vault = Path(entry.get("path", "")) if isinstance(entry, dict) else None
            if vault and vault.is_dir() and (vault / ".obsidian").is_dir() \
                    and vault not in found:
                found.append(vault)
    return found


def vaults_by_search(home=None, depth=SEARCH_DEPTH):
    """Bounded search for `.obsidian` folders when Obsidian's own list is empty."""
    home = Path.home() if home is None else Path(home)
    found = []

    def walk(directory, level):
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        if any(entry.name == ".obsidian" and entry.is_dir() for entry in entries):
            found.append(directory)
            return
        if level >= depth:
            return
        for entry in sorted(entries):
            if (entry.is_dir() and not entry.is_symlink()
                    and not entry.name.startswith(".") and entry.name not in SKIP_DIRS):
                walk(entry, level + 1)

    walk(home, 0)
    return found


def find_vaults(home=None):
    vaults = vaults_from_config(home)
    return vaults or vaults_by_search(home)


def write_config(path, updates):
    """Set KEY="value" lines in place, keeping every comment and other setting."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    output = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in remaining:
            output.append(f'{key}="{remaining.pop(key)}"')
        else:
            output.append(line)
    for key, value in remaining.items():
        output.append(f'{key}="{value}"')
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    path.chmod(0o600)


class Terminal:
    """Plain prompts for shells and SSH sessions."""

    def choose(self, title, text, options):
        print(f"\n{title}\n{text}")
        for number, option in enumerate(options, 1):
            print(f"  {number}. {option}")
        while True:
            answer = input(f"Choose 1-{len(options)} (Enter for 1, q to cancel): ").strip()
            if answer.lower() == "q":
                return None
            if not answer:
                return 0
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1

    def ask(self, title, default=""):
        suffix = f" [{default}]" if default else ""
        answer = input(f"{title}{suffix}: ").strip()
        return answer or default

    def secret(self, title):
        return getpass.getpass(f"{title}: ").strip()

    def info(self, text):
        print(text)


class Zenity:
    """Dialogs for the desktop."""

    def _run(self, *args):
        result = subprocess.run(
            ["zenity", "--title=USB Audio Transcriber setup", *args],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0, result.stdout.strip()

    def choose(self, title, text, options):
        ok, answer = self._run(
            "--list", f"--text={title}\n{text}", "--column=Option", "--hide-header",
            "--width=640", "--height=320", *options,
        )
        if not ok or answer not in options:
            return None
        return options.index(answer)

    def ask(self, title, default=""):
        ok, answer = self._run("--entry", f"--text={title}", f"--entry-text={default}",
                               "--width=520")
        return answer if ok else None

    def secret(self, title):
        ok, answer = self._run("--entry", "--hide-text", f"--text={title}", "--width=520")
        return answer if ok else None

    def info(self, text):
        self._run("--info", f"--text={text}", "--width=480")


def run(ui, config_path, home=None):
    """Ask the questions and write the answers. Returns False when cancelled."""
    config = load(config_path) if Path(config_path).exists() else {}
    vaults = find_vaults(home)
    vault_dir = None
    if vaults:
        options = [str(vault) for vault in vaults] + [OTHER]
        picked = ui.choose(
            "Where should transcript notes go?",
            "These Obsidian vaults were found. Pick one and a folder is created inside it.",
            options,
        )
        if picked is None:
            return False
        if picked < len(vaults):
            folder = ui.ask("Folder inside the vault for the notes", DEFAULT_FOLDER)
            if folder is None:
                return False
            vault_dir = vaults[picked] / (folder.strip().strip("/") or DEFAULT_FOLDER)
    if vault_dir is None:
        default = config.get("VAULT_DIR") or str(Path.home() / "usb-audio-transcriber-data" / "transcripts")
        answer = ui.ask("Folder for transcript notes (an Obsidian vault folder works well)", default)
        if answer is None:
            return False
        vault_dir = Path(os.path.expanduser(answer.strip()))
    if not vault_dir.is_absolute():
        ui.info(f"The notes folder must be an absolute path, not {vault_dir}. Nothing was changed.")
        return False
    try:
        vault_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        ui.info(f"Could not create {vault_dir}: {exc}. Nothing was changed.")
        return False

    updates = {"VAULT_DIR": str(vault_dir)}
    subject = ui.ask(
        "What are your recordings mostly about? Used to sharpen AI summaries (optional)",
        config.get("SESSION_SUBJECT", ""),
    )
    if subject is not None:
        updates["SESSION_SUBJECT"] = subject.strip()
    if not config.get("OPENROUTER_API_KEY", "").strip():
        key = ui.secret(
            "OpenRouter API key for AI summaries. Leave empty to keep everything local"
        )
        if key:
            updates["OPENROUTER_API_KEY"] = key.strip()
    write_config(config_path, updates)
    summary_line = ("AI summaries: on" if updates.get("OPENROUTER_API_KEY")
                    or config.get("OPENROUTER_API_KEY", "").strip()
                    else "AI summaries: off (local only)")
    ui.info(f"Notes will be written to:\n{vault_dir}\n{summary_line}\n\n"
            "Plug in your recorder to start. Settings live in "
            f"{Path(config_path)}.")
    return True


def pick_ui(force_terminal=False):
    if not force_terminal and has_display() and shutil.which("zenity"):
        return Zenity()
    if sys.stdin.isatty():
        return Terminal()
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.env")
    parser.add_argument("--terminal", action="store_true",
                        help="use terminal prompts even on a desktop")
    parser.add_argument("--non-interactive", action="store_true",
                        help="do nothing (for scripted installs)")
    parser.add_argument("--reconfigure", action="store_true",
                        help="accepted for clarity; the wizard always asks again")
    args = parser.parse_args(argv)
    if args.non_interactive:
        return 0
    ui = pick_ui(args.terminal)
    if ui is None:
        print("Setup skipped: no desktop or terminal available. Run "
              f"{Path(__file__).name} --terminal from a shell, or edit {args.config}.")
        return 0
    try:
        changed = run(ui, args.config)
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled; configuration unchanged.")
        return 0
    if not changed:
        print("Setup cancelled; configuration unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
