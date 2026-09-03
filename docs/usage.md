# Usage guide

## Normal workflow

1. Install and configure the project.
2. Keep `usb-audio-transcriber.timer` enabled.
3. Mount or plug in removable media containing audio directly inside the configured recorder directory, `RECORD` by default.
4. Wait for the next scan, normally within about one minute.
5. Watch the Zenity window for detected files, active file/model, percentage, and estimated time remaining.
6. Open the configured transcript directory after completion.

The pipeline scans `/media/$USER`, `/run/media/$USER`, and `/mnt`. It ignores audio outside a directory whose name exactly matches `RECORDER_DIR`.

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
meeting.json
meeting.txt
```

The Markdown note is written separately under `VAULT_DIR` and includes YAML metadata, an optional summary, and timestamped transcript segments.

For `WHISPER_MODEL_PROFILE=both`, artifacts are model-labelled:

```text
meeting.fast.json
meeting.fast.txt
meeting.accurate.json
meeting.accurate.txt
```

The transcript notes also include `fast` or `accurate` in their filenames.

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

By default, results go to `<recording-name>-whisper-ab/` beside the source. Use `--output-dir` to select another location. The benchmark does not modify the source, archive, queue, SQLite state, or transcript vault.

## Pause and resume scanning

```bash
systemctl --user stop usb-audio-transcriber.timer
systemctl --user start usb-audio-transcriber.timer
```

Stopping the timer does not terminate an already-running service. Inspect the service before stopping active transcription:

```bash
systemctl --user status usb-audio-transcriber.service
```
