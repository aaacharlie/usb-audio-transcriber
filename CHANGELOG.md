# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

Findings of the 2026-09-08 bug hunt (eleven reviewing agents, every finding reproduced or traced before it was fixed); the rest are tracked in issue #26.

- pipx install: the session-summary prompt and the panel's web page were looked up in the data folder instead of the program's, so every AI summary failed and the web page crashed (`bin/sessions.py`, `bin/panel.py` now use `ASSETS`).
- `config.env` values that contain a double quote (the Gemini command recipe, a subject with a quoted title) now survive saving and loading: the wizard and the panel write `\"` for a quote and `\\` for a backslash, and `load()` reads them back; a value with a line break is refused instead of becoming extra settings. The doctor reports a summary command with unbalanced quotes and a `PANEL_BIND` that is not an IPv4 address.
- Switching the model profile to or from `both` no longer makes earlier transcripts vanish from search or block their session notes: completeness is checked under both naming layouts.
- A Summarize, Retry, or Rebuild run that produces no summary (no usable backend, failure) keeps the existing note; summarizing with a backend that is not set up stops with a message and exit 1 instead of erasing summaries.
- `PURGE_DEVICE=1` purges only from removable drives; a backup disk or a symlink under `/mnt` is imported but never deleted from. Symlinked mount points are not scanned.
- A copy interrupted by an unplug, an I/O error, or a full disk leaves nothing behind (copies go to a temporary name and are renamed after verification), the cycle continues with the next file, and leftover partial copies are swept at the start of a cycle.
- A file name that is not valid UTF-8 (a zip of memos made on Windows) is skipped with a message instead of crashing every cycle; hidden `._name.wav` shadow files are ignored; `AUDIO_EXTS` accepts dots and spaces the same way everywhere.
- `sessions.py` takes the cycle lock when started from the panel or a terminal (and waits for a running cycle), so a Rebuild or Summarize overlapping the timer no longer writes duplicate notes or pays for a summary twice.
- `install.sh` refuses to run from a clone placed at the install folder (it would delete its own files); `uninstall.sh` leaves a clone's files alone and no longer stops when the user bus is unavailable.
- The panel's private token no longer appears on the browser's command line or in the journal: the web page opens through a one-time link, and the long-lived link is printed only on request.
- Desktop window: names and paths containing `&` or `<` render (Pango markup is escaped), the second note viewed is formatted, `--verbose` no longer prints API keys, a failed jobs poll is retried, and folding a job row is respected while a job runs. Web page: wikilinks with `&` resolve, the backend picker follows the configured backend.
- `search.py` waits for a busy database instead of failing, and rejects a `--since` that is not a date; words starting with a dash no longer act as options from the panel. The panel refuses a non-ASCII token and a bad `Content-Length` cleanly and shows its status even with an invalid `SUMMARY_*` value.
- The click-to-open notification helper survives the end of the cycle unit (its own systemd scope); the progress window shows its whole message; the wizard's vault search survives an unreadable folder; a duplicated key in `config.env` is written once; a custom data root given to `usb-audio-transcriber install` is baked into the units and the menu entry; `MAP_WINDOW_CHARS=0` no longer hangs; `HUGGINGFACE_HUB_CACHE` is honoured; the release notes never take a pre-release section for the final version; the sdist carries the release-notes script its tests need.

## [1.1.0] - 2026-09-06

The control panel becomes a real desktop window, summaries can use a tool you already pay for, and the program is a pipx package.

### Added

- The control panel is a desktop window: `bin/app.py`, a native GTK 4 / libadwaita app with the same pages as the web panel (home, sessions, recordings, search, settings, tools), talking to the panel server's local API. The app-menu entry and `panel.py open` start it when the GTK bindings are installed (`python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`) and fall back to the web page otherwise; `--page` opens on a given page, `--web` and `--browser` choose the web page. `GET /api/link` returns the private link for another device.
- pipx and PyPI packaging (#10): `pyproject.toml` builds a wheel whose `usb-audio-transcriber` command wraps every script (`install`, `uninstall`, `update`, `cycle`, `panel`, `doctor`, `setup`, `sessions`, `search`, `model-cache`, `benchmark`, `paths`). `usb-audio-transcriber install` writes the same units and menu entry as `install.sh`, against the same `~/.local/share/usb-audio-transcriber/config.env`, so the two install paths are interchangeable. The version is the git tag, CI installs the built wheel with pipx, and the Release workflow gains a PyPI `publish` job that runs once trusted publishing is configured. `USB_AUDIO_TRANSCRIBER_ROOT` moves the data folder; `install.sh` and `bootstrap.sh` are unchanged for users.
- The control panel (`bin/panel.py`, an app-menu entry, and `usb-audio-transcriber-panel.service`): a token-protected local web app with the pipeline's state, sessions with a Summarize button and backend picker, recordings, search, every setting as a validated form (with "Find my Obsidian vault"), and tools for the doctor, search index, model cache, session rebuilds, and the log. `sessions.py summarize --id` and `--backend` back its actions from the terminal. Every button shows its result, streamed while it runs, in an Activity box on the page where it was pressed, and the panel opens as its own window when Chrome, Chromium, Brave, or Edge is installed (`panel.py open --browser` forces a tab).
- Summary backends (`SUMMARY_BACKEND`): summaries can now come from a command-line AI tool you already pay for (`command`: Codex, Claude Code, Gemini CLI, your own agent), any OpenAI-compatible server such as a local Ollama (`openai`), or OpenRouter (`openrouter`). The setup wizard asks which, `sessions.py test-backend` checks it, and empty keeps the old OpenRouter-if-key behaviour.
- `SESSION_BACKFILL_DAYS` (default 7): on an installation with history, sessions that ended more than that many days ago get notes without an automatic AI summary, and a cycle that writes more than three session notes sends one notification instead of one per note. `sessions.py retry` summarizes older sessions on demand.
- Full-text search across all transcripts (`bin/search.py`): an FTS5 index in the state database, refreshed every cycle, with `--since`, `--speaker`, prefix, `--raw`, and `--json` options. (#11)
- `WHISPER_TASK="translate"` translates speech in other languages straight into English instead of transcribing it; the task is validated by the doctor and recorded in each note's front matter (#13, contributed by @anni-x1).

### Fixed

- The desktop window keeps one instance per session (a second launch raises the window), carries the app icon in the dock (the menu entry and icon are now named after the window's id, `io.github.aaacharlie.UsbAudioTranscriber`), and got a face: a status card on Home with the icon, the pipeline's state in one line, a progress bar while transcribing, and icons on every row; page titles use the desktop's accent colour.
- Clicking the app while its window is buried now raises the window. The menu entry asks the desktop for an activation token (`StartupNotify=true`), `panel.py open` becomes the window process instead of spawning it, and the window carries its id as its X11 class, so the desktop treats the click as permission to take focus rather than showing "USB Audio Transcriber is ready".
- The menu entry names the icon by its full path, the installer bumps the icon theme folder and refreshes a stale `icon-theme.cache`, so the microphone shows in the menu and the dock right after an update; a leftover cache had turned it into a gear.
- Updating restarts a running panel server (`systemctl --user try-restart`), so the new code is what the window talks to straight away.
- The panel's status no longer walks every mounted drive on every refresh: the "on the recorder" count is refreshed in the background at most every 30 seconds, `bin/ingest.py` skips hidden and system folders and looks at most four levels deep for the recorder's folder, and the window never stacks status requests, keeps the last good values, and reports an outage only after two failures in a row. Before, an external drive with many files made `/api/status` time out ("Panel unreachable") and kept the disk busy.

## [1.0.0] - 2026-09-05

First tagged release. Everything below is new relative to the initial public code.

### Added

- One-command installer (`bootstrap.sh`) that clones or updates the source and runs `install.sh`.
- First-run setup wizard (`bin/setup.py`) that finds Obsidian vaults (native, Flatpak, and Snap installs) and writes `VAULT_DIR`, the summary subject, and an optional OpenRouter key into `config.env` without touching anything else.
- Session notes (`bin/sessions.py`): recordings less than `SESSION_GAP_MIN` minutes apart become one note with `[[wikilinks]]` to every transcript and a combined transcript in order; with a key, an AI summary generated from `prompts/session-summary.md`. `list`, `retry`, and `rebuild --date` commands.
- Plug-in trigger: a systemd user path unit starts a cycle seconds after a drive mounts; `run-cycle.sh --wait` waits for a running cycle instead of skipping.
- Folder watching (`WATCH_DIRS`): recursive scanning of synced or shared folders; sources found there are never deleted.
- Desktop notifications with click-to-open (`NOTIFY`), including a failure notice.
- Headless mode (`HEADLESS`): the progress window is skipped without a display, `zenity` is optional, and the doctor warns when user lingering is off.
- Optional speaker labels with pyannote.audio (`DIARIZATION`, `./install.sh --with-diarization`).
- `FILE_SUMMARY` switch for per-recording summaries, and a shared OpenRouter client in `bin/llm.py`.
- Documentation: Obsidian guide, session notes, headless and Raspberry Pi setup, speaker labels, a README rewrite with the recorder test and the summary workflow, and hero and social preview images.
- Release workflow (`.github/workflows/release.yml`) and this changelog.

### Changed

- `zenity` is optional at install time; missing desktop tools are warnings in the doctor.
- The doctor reports the plug-in trigger and validates every new setting.

### Fixed

- Every successful run was recorded as "Transcription failed" in the progress state because the catch-all handler also caught `SystemExit`, so the desktop window could show the failure text at the end of a good run.
