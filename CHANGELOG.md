# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- pipx and PyPI packaging (#10): `pyproject.toml` builds a wheel whose `usb-audio-transcriber` command wraps every script (`install`, `uninstall`, `update`, `cycle`, `panel`, `doctor`, `setup`, `sessions`, `search`, `model-cache`, `benchmark`, `paths`). `usb-audio-transcriber install` writes the same units and menu entry as `install.sh`, against the same `~/.local/share/usb-audio-transcriber/config.env`, so the two install paths are interchangeable. The version is the git tag, CI installs the built wheel with pipx, and the Release workflow gains a PyPI `publish` job that runs once trusted publishing is configured. `USB_AUDIO_TRANSCRIBER_ROOT` moves the data folder; `install.sh` and `bootstrap.sh` are unchanged for users.
- The control panel (`bin/panel.py`, an app-menu entry, and `usb-audio-transcriber-panel.service`): a token-protected local web app with the pipeline's state, sessions with a Summarize button and backend picker, recordings, search, every setting as a validated form (with "Find my Obsidian vault"), and tools for the doctor, search index, model cache, session rebuilds, and the log. `sessions.py summarize --id` and `--backend` back its actions from the terminal. Every button shows its result, streamed while it runs, in an Activity box on the page where it was pressed, and the panel opens as its own window when Chrome, Chromium, Brave, or Edge is installed (`panel.py open --browser` forces a tab).
- Summary backends (`SUMMARY_BACKEND`): summaries can now come from a command-line AI tool you already pay for (`command`: Codex, Claude Code, Gemini CLI, your own agent), any OpenAI-compatible server such as a local Ollama (`openai`), or OpenRouter (`openrouter`). The setup wizard asks which, `sessions.py test-backend` checks it, and empty keeps the old OpenRouter-if-key behaviour.
- `SESSION_BACKFILL_DAYS` (default 7): on an installation with history, sessions that ended more than that many days ago get notes without an automatic AI summary, and a cycle that writes more than three session notes sends one notification instead of one per note. `sessions.py retry` summarizes older sessions on demand.
- Full-text search across all transcripts (`bin/search.py`): an FTS5 index in the state database, refreshed every cycle, with `--since`, `--speaker`, prefix, `--raw`, and `--json` options. (#11)
- `WHISPER_TASK="translate"` translates speech in other languages straight into English instead of transcribing it; the task is validated by the doctor and recorded in each note's front matter (#13, contributed by @anni-x1).

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
