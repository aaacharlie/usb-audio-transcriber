# Configuration reference

The installed configuration is:

```text
~/.local/share/usb-audio-transcriber/config.env
```

`install.sh` creates it from `config.example.env` only when it does not already exist, so reinstalling does not overwrite local settings.

The installer validates the configuration before enabling the timer. Re-run the
same validation after editing settings:

```bash
~/.local/share/usb-audio-transcriber/venv/bin/python \
  ~/.local/share/usb-audio-transcriber/bin/doctor.py
```

## Paths

| Setting | Default | Meaning |
| --- | --- | --- |
| `ARCHIVE_DIR` | `${HOME}/usb-audio-transcriber-data/archive` | Checksum-verified local copies of imported recordings |
| `QUEUE_DIR` | `${HOME}/usb-audio-transcriber-data/queue` | Symlinks for recordings waiting to be transcribed |
| `STATE_DB` | `${HOME}/usb-audio-transcriber-data/state/seen.sqlite` | SQLite deduplication and completion state |
| `VAULT_DIR` | `${HOME}/usb-audio-transcriber-data/transcripts` | Markdown transcript notes; may point at an Obsidian vault folder |

Environment variables such as `${HOME}` and a leading `~` are expanded while
loading the file. The doctor requires each of these paths to be absolute after
expansion and requires the four settings to name distinct locations.

## Discovery and source safety

| Setting | Default | Meaning |
| --- | --- | --- |
| `AUDIO_EXTS` | `mp3,wav,m4a` | Comma-separated extensions, without leading dots |
| `RECORDER_DIR` | `RECORD` | Directory name that must directly contain a candidate recording |
| `PURGE_DEVICE` | `0` | Set to `1` to remove a source from the recorder after verified import |
| `WATCH_DIRS` | empty | Colon-separated extra folders scanned recursively for audio; sources found here are never deleted |

Keep `PURGE_DEVICE=0` unless device cleanup is intentional. A matching file is still deduplicated by content rather than name.

`WATCH_DIRS` is for recordings that arrive through a sync tool (Syncthing, Nextcloud, Dropbox), a phone export, or a network share. Hidden files and folders are skipped so partially synced files are not imported, symlinks are ignored, and the pipeline's own archive and queue are excluded. Every entry must be an absolute path after `~` expansion. A folder that does not exist yet only produces a doctor warning, so a share that is mounted later still works.

## Transcription

| Setting | Default | Meaning |
| --- | --- | --- |
| `WHISPER_MODEL_PROFILE` | `fast` | `fast`, `accurate`, or `both` |
| `WHISPER_MODEL` | `distil-large-v3` | Legacy/custom model ID, used only if the profile setting is empty |
| `WHISPER_DEVICE` | `cpu` | faster-whisper device, such as `cpu` or `cuda` |
| `WHISPER_COMPUTE` | `int8` | CTranslate2 compute type supported by the selected device |
| `WHISPER_LANG` | `en` | Language code; an empty value enables language detection |
| `VAD_ENABLED` | `1` | Enable voice activity detection |
| `VAD_MIN_SILENCE_MS` | `1200` | Silence threshold used by VAD |

The stock CPU configuration is deliberately conservative. GPU users must select a compatible device and compute type for their local CTranslate2 installation.

## Optional summarization

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | empty | Enables OpenRouter only when populated |
| `OPENROUTER_MODEL` | `anthropic/claude-haiku-4.5` | OpenRouter model identifier |
| `MAP_WINDOW_CHARS` | `80000` | Character window used before map-reduce summarization |

With no API key, transcription and note generation remain local. With a key, transcript text is sent to OpenRouter; audio is not sent by this project. Do not commit `config.env`.

## Applying changes

The next timer cycle loads the current file. To run immediately:

```bash
systemctl --user start usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -f
```

If you edit the systemd unit itself rather than `config.env`, run:

```bash
systemctl --user daemon-reload
systemctl --user restart usb-audio-transcriber.timer
```

The installer renders the service with the actual installation path, including
custom `XDG_DATA_HOME` locations.
