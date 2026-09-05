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

## Speaker labels

| Setting | Default | Meaning |
| --- | --- | --- |
| `DIARIZATION` | `0` | Set to `1` to label speakers with pyannote.audio (needs `./install.sh --with-diarization`) |
| `HF_TOKEN` | empty | Hugging Face read token; required when `DIARIZATION=1` because the models are gated |
| `DIARIZATION_MODEL` | `pyannote/speaker-diarization-3.1` | pyannote pipeline identifier |
| `DIARIZATION_MIN_SPEAKERS` | empty | Optional lower bound on the number of speakers |
| `DIARIZATION_MAX_SPEAKERS` | empty | Optional upper bound on the number of speakers |

Diarization runs locally; the token is used only to download the gated models. Keep it in `config.env` like the OpenRouter key.

## Desktop integration

| Setting | Default | Meaning |
| --- | --- | --- |
| `HEADLESS` | `auto` | `auto` shows the progress window only when a graphical session and `zenity` exist; `1` never shows it; `0` always tries |
| `NOTIFY` | `auto` | Desktop notification when notes are ready, with click-to-open where `notify-send` supports actions; `1` always tries, `0` never |

Headless machines also need `loginctl enable-linger "$USER"` so the user timer keeps running without a login session; the doctor warns when lingering is off.

## Optional summarization

| Setting | Default | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | empty | Enables OpenRouter only when populated |
| `OPENROUTER_MODEL` | `anthropic/claude-haiku-4.5` | OpenRouter model identifier |
| `MAP_WINDOW_CHARS` | `80000` | Character window used before map-reduce summarization |

| `FILE_SUMMARY` | `1` | Per-recording summary block at the top of each transcript note when a key is set |

With no API key, transcription and note generation remain local. With a key, transcript text is sent to OpenRouter; audio is not sent by this project. Do not commit `config.env`.

## Session notes

| Setting | Default | Meaning |
| --- | --- | --- |
| `SESSION_NOTES` | `1` | Write one note per session after each cycle |
| `SESSION_GAP_MIN` | `20` | Recordings separated by a longer silence start a new session |
| `SESSION_SUMMARY` | `1` | Add an AI summary to session notes when `OPENROUTER_API_KEY` is set |
| `SESSION_SUMMARY_MODEL` | empty | OpenRouter model for session summaries; empty means `OPENROUTER_MODEL` |
| `SESSION_SUBJECT` | empty | Subject matter inserted into the summary prompt |
| `SESSION_PROMPT_FILE` | empty | Custom prompt template containing `{subject}`; empty uses `prompts/session-summary.md` |

Session notes work without any key: the combined transcript and the wikilinks need no network. Only the summary uses OpenRouter, and it sends the combined transcript text of the whole session.

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
