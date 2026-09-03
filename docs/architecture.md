# Architecture

USB Audio Transcriber is a user-level, timer-driven pipeline. It does not require a root daemon or a database server.

## Data flow

```text
mounted recorder media
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
```

`bin/run-cycle.sh` serializes each cycle with `flock`, runs ingestion, starts the Zenity progress reader, and then runs transcription. The user-level systemd timer starts that script approximately once per minute.

## Components

| Component | Responsibility |
| --- | --- |
| `bin/ingest.py` | Discover recorder files, wait for stable file size, hash, archive, verify, deduplicate, and enqueue |
| `bin/transcribe.py` | Load configured faster-whisper model profiles, transcribe queued recordings, optionally summarize, and write notes |
| `bin/progress-popup.py` | Read the atomic progress file and present file/model progress and ETA through Zenity |
| `bin/pipeline_config.py` | Parse `config.env`, log messages, and atomically read/write progress state |
| `bin/model_profiles.py` | Define supported model profiles, artifact naming, and Hugging Face cache paths |
| `bin/model-cache.py` | Inspect, download, or remove supported model disk caches |
| `bin/benchmark-models.py` | Compare models without touching the queue, database, archive, or transcript vault |
| `var/state/seen.sqlite` | Track imported recordings by SHA-256 and prevent duplicate imports |
| `var/state/progress.json` | Publish current progress to the desktop process |

## Safety boundaries

- A source recording is archived only after the copied file has the same SHA-256 digest.
- The database's primary key is the source digest, so renaming the same recording does not re-import it.
- Queue entries are symlinks to archived audio; transcription does not operate on USB media.
- `PURGE_DEVICE=0` is the default. Source deletion requires explicit configuration.
- `flock` prevents overlapping timer cycles.
- Model passes run sequentially, so `both` does not keep two models in RAM at once.

## Storage lifecycle

1. Source media remains untouched unless purge is explicitly enabled.
2. Archived recordings remain under `ARCHIVE_DIR` after transcription.
3. Each finished pass writes a `.complete.json` marker after its sidecars and note; queue symlinks are removed only after every configured pass has written durable outputs and its marker.
4. Transcript artifacts next to the archived audio and Markdown notes in `VAULT_DIR` remain until the user removes them.
5. Downloaded model weights remain in the Hugging Face cache until removed with `model-cache.py` or another cache-management tool.
