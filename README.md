# USB Audio Transcriber

A Linux desktop utility that detects recordings on mounted removable media, makes a checksum-verified local archive, transcribes them with faster-whisper, and writes timestamped Markdown notes. A Zenity window shows file counts, transcription progress, and a rolling estimate of time remaining.

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

## License

MIT. See [LICENSE](LICENSE).
