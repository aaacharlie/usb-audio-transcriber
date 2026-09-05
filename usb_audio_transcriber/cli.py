#!/usr/bin/env python3
"""usb-audio-transcriber: the whole pipeline behind one command.

    usb-audio-transcriber install            set this user account up: data folder,
                                             config.env, systemd units, app-menu entry
    usb-audio-transcriber panel open         the control panel
    usb-audio-transcriber doctor             check the installation
    usb-audio-transcriber setup              the setup wizard (notes folder, summaries)
    usb-audio-transcriber sessions ...       session notes: list, retry, rebuild,
                                             summarize, test-backend
    usb-audio-transcriber search WORDS       full-text search across every transcript
    usb-audio-transcriber model-cache ...    Whisper models: status, download, remove
    usb-audio-transcriber benchmark FILE     compare model profiles on one recording
    usb-audio-transcriber cycle [--wait]     one ingest-and-transcribe cycle now
                                             (what the timer and the plug-in trigger run)
    usb-audio-transcriber update             pipx upgrade, then install again
    usb-audio-transcriber uninstall          remove the units and the menu entry; data stays
    usb-audio-transcriber paths              where everything is
    usb-audio-transcriber --version

Every command after the first word is passed to the matching script under
bin/ unchanged, so `usb-audio-transcriber sessions --help` shows that script's
own help.

Installed with pipx (`pipx install usb-audio-transcriber`) the program files
live inside this package, and the data root (config.env, logs, state) is
~/.local/share/usb-audio-transcriber, the same folder install.sh uses, so an
existing config.env carries over. USB_AUDIO_TRANSCRIBER_ROOT or --root moves
the data root.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "usb-audio-transcriber"
DIST_NAME = "usb-audio-transcriber"
PACKAGE = Path(__file__).resolve().parent
# Installed with pipx or pip, the program files (bin/, prompts/, panel/, share/,
# systemd/) sit inside the package; in a git checkout they sit beside it.
ASSETS = PACKAGE if (PACKAGE / "bin").is_dir() else PACKAGE.parent
BIN = ASSETS / "bin"
PYTHON = sys.executable
SCRIPTS = {
    "panel": "panel.py",
    "doctor": "doctor.py",
    "setup": "setup.py",
    "sessions": "sessions.py",
    "search": "search.py",
    "model-cache": "model-cache.py",
    "benchmark": "benchmark-models.py",
    "transcribe": "transcribe.py",
    "ingest": "ingest.py",
    "notify": "notify.py",
    "progress-popup": "progress-popup.py",
}
RENDERED_UNITS = (f"{APP_NAME}.service", f"{APP_NAME}-plug.service", f"{APP_NAME}-panel.service")
COPIED_UNITS = (f"{APP_NAME}.timer", f"{APP_NAME}-plug.path")
ENABLED_UNITS = (f"{APP_NAME}.timer", f"{APP_NAME}-plug.path", f"{APP_NAME}-panel.service")
USAGE = __doc__.split("\n\n")[1]


class Failure(Exception):
    """A message for the person and a nonzero exit."""


# --------------------------------------------------------------------------- where things are

def data_root(override=None):
    """config.env, var/ (logs, state) and, for install.sh installs, the program too."""
    chosen = override or os.environ.get("USB_AUDIO_TRANSCRIBER_ROOT", "").strip()
    if chosen:
        return Path(chosen).expanduser()
    return data_home() / APP_NAME


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def unit_dir():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "systemd" / "user"


def launcher():
    """The usb-audio-transcriber command as an absolute path, for units and the menu entry.

    pipx puts it beside the interpreter of its private environment, which is
    the stable path; PATH is the fallback for other installs.
    """
    beside_python = Path(PYTHON).parent / APP_NAME
    if beside_python.is_file():
        return beside_python
    found = shutil.which(APP_NAME)
    if found:
        return Path(found)
    return None


def child_env(root):
    """What every script needs to find the data root, this interpreter, and bin/."""
    return os.environ | {
        "USB_AUDIO_TRANSCRIBER_ROOT": str(root),
        "USB_AUDIO_TRANSCRIBER_PYTHON": PYTHON,
        "USB_AUDIO_TRANSCRIBER_BIN": str(BIN),
    }


def version():
    try:
        from importlib.metadata import version as dist_version
        return dist_version(DIST_NAME)
    except Exception:  # not installed: a git checkout
        pass
    try:
        result = subprocess.run(["git", "-C", str(ASSETS), "describe", "--tags", "--always"],
                                capture_output=True, text=True, check=False, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "dev"


# --------------------------------------------------------------------------- running scripts

def execute(program, argv, env):
    """Replace this process (tests replace this function)."""
    os.execve(str(program), [str(a) for a in argv], env)


def exec_script(name, args, root):
    execute(PYTHON, [PYTHON, BIN / name, *args], child_env(root))


def exec_cycle(args, root):
    bash = shutil.which("bash") or "/bin/bash"
    execute(bash, [bash, BIN / "run-cycle.sh", *args], child_env(root))


def run_script(name, args, root):
    """Run a script to completion and return its exit code."""
    return subprocess.run([PYTHON, str(BIN / name), *args], env=child_env(root),
                          check=False).returncode


def systemctl(*args, check=True):
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                            check=False)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise Failure(f"systemctl --user {' '.join(args)} failed: {detail}\n"
                      "Units need a running user session; on a headless machine log in "
                      "once or run: loginctl enable-linger $USER")
    return result


# --------------------------------------------------------------------------- install

def render(template, command):
    """Fill a unit or desktop template with this installation's launcher.

    systemd and desktop entries both treat % specially, so it is doubled, and
    the path is quoted because home folders can contain spaces.
    """
    quoted = '"' + str(command).replace("%", "%%") + '"'
    return (template.replace("@CYCLE_COMMAND@", f"{quoted} cycle")
                    .replace("@PANEL_COMMAND@", f"{quoted} panel"))


def has_display():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def install(args, root):
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME} install",
        description="Set this user account up: the data folder, config.env, the systemd "
                    "units (timer, plug-in trigger, control panel), and the app-menu entry. "
                    "Safe to run again after an update.")
    parser.add_argument("--with-diarization", action="store_true",
                        help="add pyannote.audio for speaker labels (uses pipx inject)")
    parser.add_argument("--no-setup", action="store_true",
                        help="do not run the setup wizard on a fresh config.env")
    opts = parser.parse_args(args)

    if shutil.which("ffmpeg") is None:
        raise Failure("ffmpeg is required; install it with: sudo apt install ffmpeg")
    for command in ("flock", "tee"):
        if shutil.which(command) is None:
            raise Failure(f"{command} is required (usually provided by util-linux and coreutils)")
    if shutil.which("systemctl") is None:
        raise Failure("systemctl is required: the timer, plug-in trigger, and panel are user units")
    if shutil.which("zenity") is None:
        print("zenity not found: the desktop progress window will be skipped (headless mode). "
              "Install it with: sudo apt install zenity", file=sys.stderr)
    command = launcher()
    if command is None:
        raise Failure(f"the {APP_NAME} command was not found next to {PYTHON} or on PATH; "
                      f"install the package with pipx first: pipx install {DIST_NAME}")

    root.mkdir(parents=True, exist_ok=True)
    (root / "var").mkdir(exist_ok=True)
    config = root / "config.env"
    fresh = not config.exists()
    if fresh:
        shutil.copy(ASSETS / "config.example.env", config)
        print(f"Created {config}.")
    config.chmod(0o600)

    if opts.with_diarization:
        inject_diarization()

    # The doctor gate runs before any unit is written, so a rejected
    # configuration cannot leave a half-installed set of units behind.
    if run_script("doctor.py", ["--config", str(config), "--skip-systemd"], root) != 0:
        raise Failure("the doctor found blocking problems (listed above); "
                      "fix them and run install again")
    if fresh and not opts.no_setup:
        if sys.stdin.isatty() or has_display():
            run_script("setup.py", ["--config", str(config)], root)
            run_script("doctor.py", ["--config", str(config), "--skip-systemd"], root)
        else:
            print(f"Run `{APP_NAME} setup` later to point the notes at your Obsidian vault.")

    units = unit_dir()
    units.mkdir(parents=True, exist_ok=True)
    for unit in RENDERED_UNITS:
        template = (ASSETS / "systemd" / unit).read_text(encoding="utf-8")
        (units / unit).write_text(render(template, command), encoding="utf-8")
    for unit in COPIED_UNITS:
        shutil.copy(ASSETS / "systemd" / unit, units / unit)
    systemctl("daemon-reload")
    for unit in ENABLED_UNITS:
        systemctl("enable", "--now", unit)

    applications = data_home() / "applications"
    icons = data_home() / "icons" / "hicolor" / "scalable" / "apps"
    applications.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    desktop = (ASSETS / "share" / f"{APP_NAME}.desktop").read_text(encoding="utf-8")
    (applications / f"{APP_NAME}.desktop").write_text(render(desktop, command), encoding="utf-8")
    shutil.copy(ASSETS / "share" / f"{APP_NAME}.svg", icons / f"{APP_NAME}.svg")
    refresher = shutil.which("update-desktop-database")
    if refresher:
        subprocess.run([refresher, str(applications)], capture_output=True, check=False)

    leftovers = [name for name in ("bin", "venv", "src") if (root / name).is_dir()]
    if leftovers:
        print(f"Note: an older copy installed by install.sh is still under {root} "
              f"({', '.join(leftovers)}). The units now run the pipx command instead; "
              "delete those folders whenever you like to free the space.")
    print(f"Installed {APP_NAME} {version()}\n"
          f"Command: {command}\n"
          f"Settings: {config}\n"
          f"Timer status: systemctl --user status {APP_NAME}.timer\n"
          f"Plug-in trigger: systemctl --user status {APP_NAME}-plug.path\n"
          f"Control panel: \"USB Audio Transcriber\" in your app menu, or {APP_NAME} panel open\n"
          f"Change where notes go: the panel's Settings page, or {APP_NAME} setup")
    return 0


def inject_diarization():
    pipx = shutil.which("pipx")
    spec = "pyannote.audio>=3.1,<4"
    if pipx is None:
        raise Failure(f"pipx not found; for speaker labels install the extra yourself: "
                      f"pipx install \"{DIST_NAME}[diarization]\" or pip install \"{spec}\" "
                      f"into the environment of {PYTHON}")
    print(f"Adding {spec} with pipx inject (this installs PyTorch, which is large)...")
    if subprocess.run([pipx, "inject", DIST_NAME, spec], check=False).returncode != 0:
        raise Failure("pipx inject failed; speaker labels are not installed")


# --------------------------------------------------------------------------- uninstall, update, paths

def uninstall(args, root):
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME} uninstall",
        description="Remove the user units and the app-menu entry. Settings, recordings, notes, "
                    "state, and model caches are kept. The package itself is removed with "
                    f"`pipx uninstall {DIST_NAME}`.")
    parser.parse_args(args)
    if shutil.which("systemctl"):
        for unit in ENABLED_UNITS:
            systemctl("disable", "--now", unit, check=False)
    units = unit_dir()
    for unit in RENDERED_UNITS + COPIED_UNITS:
        (units / unit).unlink(missing_ok=True)
    (data_home() / "applications" / f"{APP_NAME}.desktop").unlink(missing_ok=True)
    (data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_NAME}.svg").unlink(missing_ok=True)
    if shutil.which("systemctl"):
        systemctl("daemon-reload", check=False)
    print(f"Removed the user units and the app-menu entry. Settings, state, and logs stay in {root}; "
          "recordings, notes, and model caches were not touched.\n"
          f"To remove the program too: pipx uninstall {DIST_NAME}")
    return 0


def update(args, root):
    parser = argparse.ArgumentParser(prog=f"{APP_NAME} update",
                                     description="pipx upgrade, then install again so the units "
                                                 "and the menu entry match the new version.")
    parser.parse_args(args)
    pipx = shutil.which("pipx")
    if pipx is None:
        raise Failure("pipx not found. Update by hand with the tool that installed the package, "
                      f"then run: {APP_NAME} install --no-setup")
    if subprocess.run([pipx, "upgrade", DIST_NAME], check=False).returncode != 0:
        raise Failure("pipx upgrade failed; the installed version is unchanged")
    command = launcher()
    if command is None:
        raise Failure(f"the {APP_NAME} command disappeared during the upgrade; "
                      f"run: pipx install {DIST_NAME}")
    # The upgraded version writes its own units.
    execute(command, [command, "install", "--no-setup"], child_env(root))


def paths(args, root):
    parser = argparse.ArgumentParser(prog=f"{APP_NAME} paths",
                                     description="Print where everything is.")
    parser.parse_args(args)
    print(f"Version:       {version()}\n"
          f"Data root:     {root}\n"
          f"Settings:      {root / 'config.env'}\n"
          f"Log:           {root / 'var' / 'logs' / 'pipeline.log'}\n"
          f"Program files: {ASSETS}\n"
          f"Python:        {PYTHON}\n"
          f"Command:       {launcher() or '(not on PATH)'}\n"
          f"Units:         {unit_dir()}")
    return 0


# --------------------------------------------------------------------------- entry point

COMMANDS = {"install": install, "uninstall": uninstall, "update": update, "paths": paths}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root_override = None
    if argv and argv[0].startswith("--root="):
        root_override = argv.pop(0).split("=", 1)[1]
    elif len(argv) >= 2 and argv[0] == "--root":
        root_override = argv[1]
        argv = argv[2:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("--version", "-V", "version"):
        print(version())
        return 0
    command, rest = argv[0], argv[1:]
    root = data_root(root_override)
    try:
        if command in SCRIPTS:
            exec_script(SCRIPTS[command], rest, root)
            return 0  # only reached when execute() is replaced
        if command == "cycle":
            exec_cycle(rest, root)
            return 0
        if command in COMMANDS:
            return COMMANDS[command](rest, root) or 0
    except Failure as failure:
        print(f"{APP_NAME}: {failure}", file=sys.stderr)
        return 1
    print(f"{APP_NAME}: unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
