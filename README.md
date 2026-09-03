# USB Audio Transcriber

[![CI](https://github.com/aaacharlie/usb-audio-transcriber/actions/workflows/ci.yml/badge.svg)](https://github.com/aaacharlie/usb-audio-transcriber/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-informational.svg)](#requirements)

**Plug in a voice recorder. Walk away. Come back to timestamped, searchable notes.**

USB Audio Transcriber is a free, open-source Linux desktop utility that turns a cheap voice recorder into a hands-off note-taking system. Plug the recorder in, and within about a minute the app finds the new recordings, copies them to a checksum-verified archive, transcribes them locally with faster-whisper, and writes timestamped Markdown notes you can drop straight into Obsidian or any notes folder. A small desktop window shows the detected files, the active model, the percentage done, and a rolling time estimate while it works.

There is no subscription, no per-minute pricing, and no uploading of your audio. Transcription runs on your own CPU (`distil-large-v3` by default, no GPU required), source recordings stay on the USB device, and nothing leaves your machine unless you explicitly turn on the optional cloud summary.

## Why use it

- **Zero-touch workflow.** A user-level systemd timer does the watching. You never run a command after setup; new recordings are simply picked up.
- **Genuinely free.** MIT-licensed, runs on hardware you already own, and pairs with a recorder that costs about as much as a few months of a paid transcription service.
- **Private by default.** Audio never leaves your computer. The optional summarization step sends transcript text only, and only if you set an API key.
- **Never loses a recording.** Every copy is SHA-256 verified before it enters the queue, duplicates are detected by content rather than filename, and the USB source is never deleted unless you opt in.
- **Notes you can actually use.** Each note has YAML front matter (date, time, duration, model), a heading, and timestamped segments, so it is searchable and works in Obsidian out of the box.
- **Pick your speed.** `fast` transcribed a 58-minute recording in about 17 minutes on a plain CPU. `accurate` is there for hard audio, and `both` gives you an A/B comparison from the same file.

Good fits: lectures and classes, meetings and site visits, interviews, long phone calls on speaker, and voice memos you would otherwise never listen to again.

## Real-world test: a $50 recorder from Amazon

This project was built around, and tested with, an inexpensive magnetic voice-activated recorder that sells on Amazon for about $50. The listing is titled "136GB(9800H) Magnetic Voice Recorder - Zutiifeu Voice Activated Recorder with DSP5.0 Noise Cancellation HD Recording Device for Classe/Meeting/Lecture". It has been used with this pipeline with lots of success, and the combination is the whole point of the project: a budget recorder plus a Linux box you already own gives you a complete recording-to-notes system.

What it looks like in practice:

1. Record with the device. Voice activation means it can sit for hours and only capture the parts where someone is talking.
2. Plug it into your Linux machine's USB port. The recorder mounts like an ordinary flash drive.
3. Within about a minute the timer notices the new files, archives them, and starts transcribing. The progress window shows what it found and how long it expects to take.
4. Open your notes folder. Every recording now has a dated, timestamped Markdown note there, plus plain-text and JSON transcripts stored beside the archived audio.

Any recorder, phone, or SD card that mounts as a drive and saves into a folder should work the same way. Point `RECORDER_DIR` at the folder your device saves into (the default is `RECORD`) and add your device's file extension to `AUDIO_EXTS` if it is not `mp3`, `wav`, or `m4a`.

This project has no affiliation with the recorder's manufacturer or with Amazon. It is simply the hardware the pipeline was tested on.

## The transcript looks rough? Don't be discouraged

Raw Whisper output from a pocket recorder can look underwhelming at first glance: no paragraphs, misheard names, a sentence that trails off where the voice activation paused, and long sessions split across several files. That is normal, and it is not the finished product.

The raw transcript is the input to the last step. Give the transcript notes to a current frontier model (tested with GPT 5.6 Sol) and ask it to put them in order and summarize them. The model reads straight through the transcription noise, reconstructs the flow of the conversation, and returns a very high quality executive summary of the whole session.

How to do it:

1. Collect the Markdown notes for the session from your `VAULT_DIR`. Filenames start with each recording's date and time (for example `2026-09-03 1405 transcript.md`), so the correct order is already in the names.
2. Upload or paste all of them into one chat with the model.
3. Use a prompt like this one, replacing the bracketed part with the topic of the recording:

```text
You are the world's best transcript reader and interpreter, and you are very
knowledgeable in [insert subject matter]. Can you take all of these audio
transcript files and put them in the correct order and summarize it completely?
Make it an amazing, coherent transcript summary in the proper order. Make it
something better than other AI services like Otter.
```

Tips that make the summary better:

- Name the subject matter precisely ("commercial real estate financing", "organic chemistry lecture", "quarterly planning meeting"). It helps the model fix misheard jargon and names.
- Tell the model who was in the room, if you know, so it can attribute what was said.
- Ask for decisions, action items, and open questions as separate sections if you want a meeting-style report.
- Use a model with a large context window so a whole day's notes fit in one request.

Prefer fully hands-off? Set `OPENROUTER_API_KEY` in `config.env` and the pipeline adds a Summary / Topics / Action Items / People & Entities block to the top of every note automatically. Transcript text only is sent; audio never leaves your machine. The built-in summary is convenient, and the manual pass with a top-tier model is the way to get the best final result.

Either way, remember that pasting a transcript into a cloud AI sends its text to that provider. Keep sensitive recordings local.

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
5. It writes `.json` segments, `.txt` text, a Markdown transcript note, and a `.complete.json` marker that confirms all outputs finished.
6. During transcription, it shows a Linux desktop progress dialog with the active file, number of files, percentage, and ETA.

## Privacy and safety

- Transcription is local by default. Audio is not uploaded by this project.
- Optional OpenRouter summarization is disabled by default. If you set `OPENROUTER_API_KEY`, raw transcript text—not audio—is sent to OpenRouter for summarization.
- Source audio on the USB drive is never deleted by default (`PURGE_DEVICE=0`).
- Do not commit `config.env`: it can contain an API key. The included `.gitignore` excludes it and all runtime data.

## Requirements

- Linux desktop with a running user systemd session
- Python 3.10+
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

Run the built-in diagnostic after installation or whenever setup fails:

```bash
~/.local/share/usb-audio-transcriber/venv/bin/python \
  ~/.local/share/usb-audio-transcriber/bin/doctor.py
```

It checks configuration, required commands and Python packages, writable output
locations, and the user timer without creating recordings or transcript data.

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

This removes deployed code, the virtual environment, and user systemd units. It
deliberately preserves `config.env`, runtime state, configured archives and
transcripts, and Hugging Face model caches so uninstalling cannot silently erase
user data.

## Development checks

```bash
python3 -m py_compile bin/*.py
bash -n install.sh uninstall.sh bin/run-cycle.sh
python3 -m unittest discover -s tests -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change. Report security issues privately according to [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
