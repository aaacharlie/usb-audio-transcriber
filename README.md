# USB Audio Transcriber

[![CI](https://github.com/aaacharlie/usb-audio-transcriber/actions/workflows/ci.yml/badge.svg)](https://github.com/aaacharlie/usb-audio-transcriber/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational.svg)](#requirements)

A local-first Linux desktop utility that discovers recordings on mounted removable media, makes a checksum-verified archive, transcribes them with faster-whisper, and writes timestamped Markdown notes. A Zenity window shows detected files, active model, percentage, and a rolling completion estimate.

The safe default is `distil-large-v3` on CPU. Source recordings stay on the USB device, and transcription stays local unless optional OpenRouter text summarization is explicitly configured.

## Documentation

- [Documentation wiki](docs/README.md)
- [Usage guide](docs/usage.md)
- [Configuration reference](docs/configuration.md)
- [Whisper model profiles and measured trade-offs](docs/model-profiles.md)
- [Architecture and data lifecycle](docs/architecture.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Privacy and security](docs/privacy-and-security.md)
- [Development guide](docs/development.md)

## What it does

1. A user-level systemd timer scans mounted removable media approximately once per minute.
2. It finds supported audio files inside a configurable recorder directory (default: `RECORD`).
3. It copies new recordings to a local archive and verifies the copy with SHA-256.
4. It deduplicates future scans using SQLite, then transcribes queued recordings locally with faster-whisper.
5. It writes `.json` segments, `.txt` text, and a Markdown transcript note.
6. During transcription, it shows a Linux desktop progress dialog with the active file, number of files, percentage, and ETA.

## Privacy and safety

- Transcription is local by default. Audio is not uploaded by this project.
- Optional OpenRouter summarization is disabled by default. If you set `OPENROUTER_API_KEY`, raw transcript text—not audio—is sent to OpenRouter for summarization.
- Source audio on the USB drive is never deleted by default (`PURGE_DEVICE=0`).
- Do not commit `config.env`: it can contain an API key. The included `.gitignore` excludes it and all runtime data.

## Requirements

- Linux desktop with a running user systemd session
- Python 3.8+
- `ffmpeg` for audio decoding
- `zenity` for the progress window
- Internet access the first time faster-whisper downloads the configured model

On Ubuntu/Debian:

```bash
sudo apt install python3-venv ffmpeg zenity
```

## Quick start

```bash
git clone https://github.com/aaacharlie/usb-audio-transcriber.git
cd usb-audio-transcriber
./install.sh
$EDITOR ~/.local/share/usb-audio-transcriber/config.env
```

Set `VAULT_DIR` to the folder where Markdown notes should be written, then plug in or mount media containing recordings directly inside its `RECORD` directory. The user timer checks for new recordings approximately once per minute.

## Install

```bash
git clone https://github.com/aaacharlie/usb-audio-transcriber.git
cd usb-audio-transcriber
./install.sh
```

The installer deploys to `~/.local/share/usb-audio-transcriber`, creates a virtual environment, and enables `usb-audio-transcriber.timer`.

Edit configuration after installation:

```bash
$EDITOR ~/.local/share/usb-audio-transcriber/config.env
```

At minimum, set `VAULT_DIR` to wherever you want Markdown transcripts to be written. The defaults store data beneath `~/usb-audio-transcriber-data`.

### Update an existing installation

```bash
cd usb-audio-transcriber
git pull --ff-only
./install.sh
```

The installer replaces deployed program files but preserves an existing installed `config.env`.

## Status and logs

```bash
systemctl --user status usb-audio-transcriber.timer
systemctl --user status usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -f
```

The pipeline also logs to:

```text
~/.local/share/usb-audio-transcriber/var/logs/pipeline.log
```

## Configuration

`config.example.env` documents all options. Common settings:

- `AUDIO_EXTS`: comma-separated supported extensions.
- `RECORDER_DIR`: directory name on removable media that contains recordings.
- `WHISPER_MODEL_PROFILE`: `fast`, `accurate`, or `both` (A/B comparison).
- `WHISPER_MODEL`: legacy/custom model ID, used only when the profile is empty.
- `WHISPER_DEVICE` / `WHISPER_COMPUTE`: defaults are `cpu` / `int8`.
- `OPENROUTER_API_KEY`: optional cloud summarization; leave empty for fully local transcription.
- `PURGE_DEVICE`: leave at `0` unless you explicitly want copied recordings removed from the USB device.

### Whisper model choices

| Profile | Model | Pros | Cons |
| --- | --- | --- | --- |
| `fast` | `distil-large-v3` | Fastest supported option; lower disk, RAM, and CPU cost | Can be less reliable on distant, overlapping, or otherwise difficult speech |
| `accurate` | `large-v3` | Best accuracy-oriented option; more robust on difficult audio | Substantially slower on CPU; about 2.9 GiB of disk cache |
| `both` | both models | Produces a direct A/B comparison from the same recording | Takes the combined runtime and disk space of both models |

In one real CPU benchmark of a 57m 45s recording, `distil-large-v3` finished in 16m 56s while `large-v3` took 89m 57s: 5.31 times longer. Treat that as one hardware/audio data point, not a universal benchmark. See [Whisper model profiles](docs/model-profiles.md) for the complete result and interpretation.

With `both`, JSON and text artifacts are labelled `.fast` and `.accurate`, and
Markdown notes include the profile in their filenames. A queued recording is
marked complete only after both passes finish.

Models are loaded into RAM only while they are being used. Faster-whisper keeps
downloaded weights in the Hugging Face disk cache so future runs do not download
gigabytes again. Manage those independent caches explicitly:

```bash
python3 bin/model-cache.py status both
python3 bin/model-cache.py download accurate
python3 bin/model-cache.py remove accurate
```

Removing a cache frees disk space, but that model must be downloaded again before
its next use. It is not possible to keep a model available offline without
keeping its weights somewhere on disk.

Run an isolated comparison without importing the recording into the live queue:

```bash
~/.local/share/usb-audio-transcriber/venv/bin/python \
  ~/.local/share/usb-audio-transcriber/bin/benchmark-models.py \
  /path/to/recording.wav --profile both
```

The benchmark loads one model at a time, writes model-labelled JSON and text
files plus `comparison.json`, and does not modify the source recording, queue,
SQLite state, or transcript vault.

## Uninstall

```bash
./uninstall.sh
```

This removes the program and timer but deliberately preserves your configured archive and transcripts.

## Development checks

```bash
python3 -m py_compile bin/*.py
bash -n install.sh uninstall.sh bin/run-cycle.sh
python3 -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change. Report security issues privately according to [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
