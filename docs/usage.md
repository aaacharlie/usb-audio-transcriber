# Usage guide

## Normal workflow

1. Install and configure the project.
2. Keep `usb-audio-transcriber.timer` enabled.
3. Mount or plug in removable media containing audio directly inside the configured recorder directory, `RECORD` by default.
4. Wait for the next scan, normally within about one minute.
5. Watch the Zenity window for detected files, active file/model, percentage, and estimated time remaining.
6. Open the configured transcript directory after completion.

The pipeline scans `/media/$USER`, `/run/media/$USER`, and `/mnt`. It ignores audio outside a directory whose name exactly matches `RECORDER_DIR`.

## Watch a folder instead of, or as well as, USB media

Set `WATCH_DIRS` in `config.env` to one or more absolute folders separated by colons:

```ini
WATCH_DIRS="${HOME}/Sync/VoiceMemos:/srv/audio"
```

Every cycle scans those folders recursively, so phone voice memos synced with Syncthing, Nextcloud, or Dropbox, files copied over the network, or anything dropped into the folder by hand are imported exactly like recorder files: verified copy, deduplication by content, queue, transcript. Files in watched folders are never deleted, even when `PURGE_DEVICE=1`, because deleting a synced file would delete it on every device. Hidden files and folders are ignored so partially synced files are not picked up.

## Run a cycle manually

```bash
systemctl --user start usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -f
```

Running the installed script directly is also possible:

```bash
~/.local/share/usb-audio-transcriber/bin/run-cycle.sh
```

Its lock causes a second overlapping invocation to exit safely.

## Output files

For a single model profile, an archived `meeting.wav` receives:

```text
meeting.wav
meeting.wav.json
meeting.wav.txt
meeting.wav.complete.json
```

The Markdown note is written separately under `VAULT_DIR` and includes YAML metadata, an optional summary, and timestamped transcript segments. The
`.complete.json` completion marker is written last, after the sidecars and the
note; a pass counts as finished—and its queue entry is removed—only once the
marker exists. Recordings with no detected speech still produce all four
outputs, with `"status": "no_speech"`. Delete a recording's `.complete.json`
(and any stale sidecars) to force a fresh transcription pass.

For `WHISPER_MODEL_PROFILE=both`, artifacts are model-labelled:

```text
meeting.wav.fast.json
meeting.wav.fast.txt
meeting.wav.fast.complete.json
meeting.wav.accurate.json
meeting.wav.accurate.txt
meeting.wav.accurate.complete.json
```

Keeping the source extension in every artifact prevents a `.wav` and `.mp3` with
the same stem from overwriting each other. Transcript notes also include `fast`
or `accurate` in their filenames.

## Manage model caches

Run cache commands with the installed virtual environment:

```bash
PYTHON=~/.local/share/usb-audio-transcriber/venv/bin/python
APP=~/.local/share/usb-audio-transcriber

$PYTHON $APP/bin/model-cache.py status both
$PYTHON $APP/bin/model-cache.py download fast
$PYTHON $APP/bin/model-cache.py remove accurate
```

`download` makes a model available offline. `remove` frees disk space, but the next use requires another download. Cache files do not mean the model is resident in RAM; weights are loaded into process memory only while that model is in use.

## Compare models without touching the live pipeline

```bash
PYTHON=~/.local/share/usb-audio-transcriber/venv/bin/python
APP=~/.local/share/usb-audio-transcriber

$PYTHON $APP/bin/benchmark-models.py /path/to/recording.wav --profile both
```

By default, results go to `<recording-file-name>-whisper-ab/` beside the source — for example `meeting.wav-whisper-ab/` — keeping the extension so benchmarks of `meeting.wav` and `meeting.mp3` never overwrite each other. Use `--output-dir` to select another location. The benchmark does not modify the source, archive, queue, SQLite state, or transcript vault.

## Pause and resume scanning

```bash
systemctl --user stop usb-audio-transcriber.timer
systemctl --user start usb-audio-transcriber.timer
```

Stopping the timer does not terminate an already-running service. Inspect the service before stopping active transcription:

```bash
systemctl --user status usb-audio-transcriber.service
```
