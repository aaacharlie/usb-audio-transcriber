# Usage guide

## Normal workflow

1. Install and configure the project.
2. Keep `usb-audio-transcriber.timer` enabled.
3. Mount or plug in removable media containing audio directly inside the configured recorder directory, `RECORD` by default.
4. A cycle starts a few seconds after the drive mounts (the plug-in trigger watches the mount folders). The timer is the fallback and scans about once a minute, which also covers folders in `WATCH_DIRS`.
5. Watch the Zenity window for detected files, active file/model, percentage, and estimated time remaining.
6. A desktop notification announces finished notes. Click it to open the note, or the transcript folder when several notes were written.
7. Open the configured transcript directory after completion.

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

## Session notes and AI summaries

Voice-activated recorders split one meeting, class, or site visit into many files. After each cycle, `bin/sessions.py` groups transcribed recordings whose gaps are shorter than `SESSION_GAP_MIN` (20 minutes by default) into a session and writes one note per session:

```text
2026-09-05 0900 session.md
```

The note has YAML front matter (`type: session`, recording count, duration, speech minutes), a `## Summary` section, a `## Recordings` list that links every transcript note with `[[wikilinks]]`, and a `## Combined transcript` that stitches all the recordings together in order. Without an API key the summary section holds a short hint and the combined transcript is the thing to paste into an AI model together with `prompts/session-summary.md`.

With `OPENROUTER_API_KEY` set, the pipeline sends the ordered transcripts to the model named by `SESSION_SUMMARY_MODEL` (falling back to `OPENROUTER_MODEL`) using the bundled prompt, and writes the result into the summary section. Set `SESSION_SUBJECT` to the topic of your recordings so the model can fix misheard jargon and names, and pick a strong model for sessions even if the per-file `OPENROUTER_MODEL` stays small. Sessions longer than `MAP_WINDOW_CHARS` are summarized in windows and then merged. Set `FILE_SUMMARY=0` to keep only session summaries.

A session is closed when its note is written. Recordings that arrive later start a new session, even if they would have fitted the gap rule. A session whose recordings are still being transcribed waits for the next cycle.

Manual commands, run with the installed virtual environment:

```bash
PYTHON=~/.local/share/usb-audio-transcriber/venv/bin/python
APP=~/.local/share/usb-audio-transcriber

$PYTHON $APP/bin/sessions.py list                        # what has been written
$PYTHON $APP/bin/sessions.py retry                       # add summaries to notes that lack one
$PYTHON $APP/bin/sessions.py rebuild --date 2026-09-05   # forget and regenerate one day
```

`retry` is the command to run after adding an API key, or after a summary failed because the network or the provider was down. `rebuild` regroups a day from scratch, which is how to merge a late recording into the session it belongs to.

To change the prompt, copy `prompts/session-summary.md` somewhere, edit it (keep the `{subject}` placeholder), and point `SESSION_PROMPT_FILE` at it.

## Speaker labels (optional)

Speaker labels turn `**[0:12:03]** text` into `**[0:12:03] Speaker 2:** text` and give the summaries a much better sense of who said what. They use [pyannote.audio](https://github.com/pyannote/pyannote-audio), which runs locally but is a large install (PyTorch) and slow on CPU, so it is off by default.

1. Install the extra dependencies: `./install.sh --with-diarization` (safe to re-run on an existing installation).
2. On huggingface.co, accept the terms of `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`, then create a read token under Settings.
3. In `config.env` set `DIARIZATION=1` and `HF_TOKEN="hf_..."`. `DIARIZATION_MIN_SPEAKERS` and `DIARIZATION_MAX_SPEAKERS` are optional hints when you know how many people were in the room.
4. Run the doctor. It checks the token and the package.

Each transcript segment takes the speaker who overlaps it most; segments nobody overlaps stay unlabelled rather than guessed. Labels are `Speaker 1`, `Speaker 2`, ... in order of first appearance, and they appear in the note, the JSON sidecar, the plain-text file, the session note, and the text sent for summaries. If labelling fails (missing terms acceptance, bad token, out of memory), the recording is still transcribed and the log says why.

The first run downloads the pyannote models into the Hugging Face cache. Expect a one-hour recording to take a similar order of time to label as to transcribe on a CPU.

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

## Run on a headless machine or Raspberry Pi

The pipeline does not need a desktop. On a server, a NAS, or a Raspberry Pi with a USB port:

1. Install as usual. `zenity` is optional; without it the progress window is simply skipped.
2. Allow your user's timers to run without a login session:

   ```bash
   loginctl enable-linger "$USER"
   ```

   The doctor warns when this is off.
3. Optionally set `HEADLESS="1"` in `config.env` to skip the desktop window and notifications explicitly. The default `auto` already skips them when no graphical session is detected.
4. Point `VAULT_DIR` at a folder that syncs to your laptop or phone, or read the notes over the network. Progress is logged to `var/logs/pipeline.log`.

## Pause and resume scanning

```bash
systemctl --user stop usb-audio-transcriber.timer
systemctl --user start usb-audio-transcriber.timer
```

Stopping the timer does not terminate an already-running service. Inspect the service before stopping active transcription:

```bash
systemctl --user status usb-audio-transcriber.service
```
