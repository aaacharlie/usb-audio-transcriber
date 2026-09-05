# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
