# Configuration reference

The installed configuration is:

```text
~/.local/share/usb-audio-transcriber/config.env
```

`install.sh` creates it from `config.example.env` only when it does not already exist, so reinstalling does not overwrite local settings. On that first install the setup wizard (`bin/setup.py`) fills in `VAULT_DIR`, `SESSION_SUBJECT`, and optionally `OPENROUTER_API_KEY`; run it again whenever you want to change them without editing the file.

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
| `WHISPER_TASK` | `transcribe` | `transcribe` (default) or `translate` (translate speech directly into English) |
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

## AI summaries

Transcription is always local. Summaries are optional, and `SUMMARY_BACKEND` decides how they are made:

| Backend | What it uses | Cost |
| --- | --- | --- |
| `none` | nothing; notes get the combined transcript only | free |
| `command` | a command-line AI tool you already have: Codex with a ChatGPT plan, Claude Code, Gemini CLI, or your own agent | covered by the subscription |
| `openai` | any OpenAI-compatible server, normally Ollama on this machine | free, runs locally |
| `openrouter` | OpenRouter | pay per use |

An empty `SUMMARY_BACKEND` means `openrouter` when `OPENROUTER_API_KEY` is set and `none` otherwise, so older configurations behave exactly as before. The setup wizard asks this question and can be re-run at any time; `sessions.py test-backend` sends a one-word prompt through the configured backend and prints what came back.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SUMMARY_BACKEND` | empty | `none`, `command`, `openai`, or `openrouter` |
| `SUMMARY_COMMAND` | empty | Command for the `command` backend; the prompt arrives on stdin, the reply is read from stdout or from `{output_file}` when the command mentions it, and `{prompt_file}` holds the prompt for tools that want it as an argument |
| `SUMMARY_COMMAND_TIMEOUT` | `900` | Seconds to wait for the command |
| `LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible server for the `openai` backend (Ollama's default) |
| `LLM_MODEL` | empty | Model name for the `openai` backend, for example `llama3.1:8b` |
| `LLM_API_KEY` | empty | Optional key for the `openai` backend; Ollama needs none |
| `OPENROUTER_API_KEY` | empty | Key for the `openrouter` backend |
| `OPENROUTER_MODEL` | `anthropic/claude-haiku-4.5` | OpenRouter model identifier |
| `MAP_WINDOW_CHARS` | `80000` | Character window used before map-reduce summarization |
| `FILE_SUMMARY` | `1` | Per-recording summary block at the top of each transcript note |

Command recipes, taken from each tool's documentation; confirm with `sessions.py test-backend` and use full paths if the tool is not on the background service's `PATH`:

```ini
SUMMARY_COMMAND="codex exec --skip-git-repo-check --sandbox read-only --output-last-message {output_file}"
SUMMARY_COMMAND="claude -p --output-format text"
SUMMARY_COMMAND="gemini -p \"$(cat {prompt_file})\""
```

The command runs through `bash -c` with the prompt on standard input, in a temporary directory, with the timeout above. Whatever the tool does with the text is between you and that provider; see the privacy page.

## Session notes

| Setting | Default | Meaning |
| --- | --- | --- |
| `SESSION_NOTES` | `1` | Write one note per session after each cycle |
| `SESSION_GAP_MIN` | `20` | Recordings separated by a longer silence start a new session |
| `SESSION_BACKFILL_DAYS` | `7` | Sessions that ended more than this many days ago get a note without an automatic AI summary; `sessions.py retry` summarizes them on demand; empty summarizes everything |
| `SESSION_SUMMARY` | `1` | Add an AI summary to session notes when a summary backend is configured |
| `SESSION_SUMMARY_MODEL` | empty | Model for session summaries on the `openrouter` and `openai` backends; empty means the backend's usual model |
| `SESSION_SUBJECT` | empty | Subject matter inserted into the summary prompt |
| `SESSION_PROMPT_FILE` | empty | Custom prompt template containing `{subject}`; empty uses `prompts/session-summary.md` |

Session notes work without any backend: the combined transcript and the wikilinks need no network. Only the summary uses the backend, and it sends the combined transcript text of the whole session.

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
