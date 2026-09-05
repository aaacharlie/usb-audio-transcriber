# Architecture

USB Audio Transcriber is a user-level, timer-driven pipeline. It does not require a root daemon or a database server.

## Data flow

```text
mounted recorder media, WATCH_DIRS folders
        |
        v
bin/ingest.py -- SHA-256 deduplication and verified copy
        |
        +--> ARCHIVE_DIR/YYYY/MM/DD/<timestamp>_<name>.<ext>
        |
        +--> QUEUE_DIR/<symlink to archived audio>
                         |
                         v
                  bin/transcribe.py
                         |
                         +--> adjacent JSON segments and plain text
                         +--> VAULT_DIR/<timestamp> transcript.md
                         +--> optional OpenRouter summary
                         |
                         v
                  bin/sessions.py -- group by time gap
                         |
                         +--> VAULT_DIR/<start> session.md
                              (links, combined transcript, optional AI summary)
```

`bin/run-cycle.sh` serializes each cycle with `flock`, runs ingestion, starts the Zenity progress reader, runs transcription, and finally writes session notes. Two user-level systemd units start it: `usb-audio-transcriber.timer` about once per minute, and `usb-audio-transcriber-plug.path`, which fires `usb-audio-transcriber-plug.service` when a mount point appears under `/media/$USER`, `/run/media/$USER`, or `/mnt`. The plug-in service sleeps briefly so the mount can settle, then runs `run-cycle.sh --wait`, which waits for a timer-started cycle to finish instead of skipping, so freshly mounted recordings are handled immediately.

## Components

| Component | Responsibility |
| --- | --- |
| `bin/ingest.py` | Discover recorder files and watched-folder audio, wait for stable file size, hash, archive, verify, deduplicate, and enqueue |
| `bin/transcribe.py` | Load configured faster-whisper model profiles, transcribe queued recordings, optionally summarize, and write notes |
| `bin/sessions.py` | Group completed recordings into sessions by time gap, write session notes with wikilinks and a combined transcript, and optionally summarize the session |
| `bin/llm.py` | Shared OpenRouter client and text windowing used by per-file and session summaries |
| `prompts/session-summary.md` | Default session summary prompt; `{subject}` is filled from `SESSION_SUBJECT` |
| `bin/progress-popup.py` | Read the atomic progress file and present file/model progress and ETA through Zenity |
| `bin/notify.py` | Send desktop notifications when notes are ready or a run fails; a detached helper waits for the click and opens the note |
| `bin/pipeline_config.py` | Parse `config.env`, log messages, and atomically read/write progress state |
| `bin/model_profiles.py` | Define supported model profiles, artifact naming, and Hugging Face cache paths |
| `bin/model-cache.py` | Inspect, download, or remove supported model disk caches |
| `bin/benchmark-models.py` | Compare models without touching the queue, database, archive, or transcript vault |
| `systemd/*.timer`, `systemd/*-plug.path` | Start cycles on a schedule and when removable media is mounted |
| `var/state/seen.sqlite` | Track imported recordings by SHA-256, prevent duplicate imports, and remember which recordings belong to which session note |
| `var/state/progress.json` | Publish current progress to the desktop process |

## Safety boundaries

- A source recording is archived only after the copied file has the same SHA-256 digest.
- The database's primary key is the source digest, so renaming the same recording does not re-import it.
- Queue entries are symlinks to archived audio; transcription does not operate on USB media.
- `PURGE_DEVICE=0` is the default. Source deletion requires explicit configuration.
- Sources found in `WATCH_DIRS` are never deleted, whatever `PURGE_DEVICE` says.
- `flock` prevents overlapping timer cycles.
- Model passes run sequentially, so `both` does not keep two models in RAM at once.

## Storage lifecycle

1. Source media remains untouched unless purge is explicitly enabled.
2. Archived recordings remain under `ARCHIVE_DIR` after transcription.
3. Each finished pass writes a `.complete.json` marker after its sidecars and note; queue symlinks are removed only after every configured pass has written durable outputs and its marker.
4. Transcript artifacts next to the archived audio and Markdown notes in `VAULT_DIR` remain until the user removes them.
5. Session notes in `VAULT_DIR` are written once per session and never rewritten automatically; `sessions.py retry` fills in a missing summary in place and `sessions.py rebuild --date` regenerates a day on request.
6. Downloaded model weights remain in the Hugging Face cache until removed with `model-cache.py` or another cache-management tool.
