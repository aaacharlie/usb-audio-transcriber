# Troubleshooting

## First checks

```bash
~/.local/share/usb-audio-transcriber/venv/bin/python \
  ~/.local/share/usb-audio-transcriber/bin/doctor.py
systemctl --user status usb-audio-transcriber.timer
systemctl --user status usb-audio-transcriber-plug.path
systemctl --user status usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -n 100 --no-pager
journalctl --user-unit=usb-audio-transcriber-plug.service -n 100 --no-pager
```

The doctor exits nonzero for blocking configuration, dependency, or path
problems. Inactive or disabled timer states are warnings so the command remains
useful while the service is intentionally paused.

The application log is:

```text
~/.local/share/usb-audio-transcriber/var/logs/pipeline.log
```

## No recordings are detected

- Confirm the device is mounted under `/media/$USER`, `/run/media/$USER`, or `/mnt`.
- Confirm each recording is directly inside a directory matching `RECORDER_DIR` exactly.
- Confirm its extension is listed in `AUDIO_EXTS`.
- For `WATCH_DIRS`, confirm each entry is an absolute path and the folder exists. Hidden files and folders and symlinks are skipped there.
- Files smaller than 4097 bytes are ignored.
- A file whose size changes during the stability check is deferred to a later cycle.
- Check permissions with `namei -l /path/to/recording` without changing them blindly.

## A recording is reported as a duplicate

Deduplication uses SHA-256, not the filename. The same bytes are imported only once even if renamed. Inspect the state database:

```bash
sqlite3 ~/usb-audio-transcriber-data/state/seen.sqlite \
  'select orig_name, archived_to, imported_at, transcribed from seen order by imported_at desc limit 20;'
```

Adjust the path if `STATE_DB` is customized. Deleting database rows can cause re-imports; back up the database before modifying it.

## The timer is active but transcription did not start

```bash
systemctl --user list-timers usb-audio-transcriber.timer
systemctl --user start usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -f
```

A log message saying `cycle already running, skipping` means another cycle holds the lock. Check the service before killing anything. A plug-in triggered cycle (`usb-audio-transcriber-plug.service`) waits up to five minutes for the lock instead of skipping and logs `cycle still running after 300s, skipping` if it gives up.

## Plugging in the recorder does not start a cycle immediately

- Check `systemctl --user status usb-audio-transcriber-plug.path`. It watches `/media/$USER`, `/run/media/$USER`, and `/mnt`; a drive mounted elsewhere is only seen by the timer.
- The trigger starts about five seconds after the mount point appears. The one-minute timer still runs as a fallback, so recordings are never missed, only delayed.

## No progress window appears

- Confirm a graphical user session and `zenity` are available. With `HEADLESS="auto"` the window is skipped when neither `DISPLAY` nor `WAYLAND_DISPLAY` reaches the service; `HEADLESS="1"` skips it always.
- On a machine without a desktop this is expected. See "Run on a headless machine" in the usage guide, and run `loginctl enable-linger "$USER"` so the timer survives logout.
- The window exits quietly when no work becomes active during its startup timeout.
- Inspect `~/.local/share/usb-audio-transcriber/var/state/progress.json` for the most recent phase.
- The service can continue successfully even if the desktop window cannot be shown.

## No notification appears

- Notifications need `notify-send` (package `libnotify-bin` on Debian/Ubuntu) and a graphical session reachable from the service, exactly like the progress window.
- `NOTIFY="0"` or `HEADLESS="1"` disables them.
- Click-to-open needs a `notify-send` that supports `--action` (libnotify 0.8 or newer) and `xdg-open`. Older versions still show the notification without the action.
- A failed run also sends a "Transcription failed" notification; the log has the details.

## Model download or load fails

```bash
PYTHON=~/.local/share/usb-audio-transcriber/venv/bin/python
APP=~/.local/share/usb-audio-transcriber
$PYTHON $APP/bin/model-cache.py status both
$PYTHON $APP/bin/model-cache.py download fast
```

Check free disk space and network access. For GPU configurations, verify that the selected `WHISPER_DEVICE` and `WHISPER_COMPUTE` are supported by the installed CTranslate2 stack. Switching back to `cpu` / `int8` is the conservative diagnostic baseline.

## A session note is missing or has no summary

- Session notes are written at the end of a cycle, after transcription. `sessions.py list` shows what exists.
- A session whose recordings are still queued waits for the next cycle; the log says `still being transcribed; waiting`.
- Recordings farther apart than `SESSION_GAP_MIN` become separate sessions on purpose. A recording that arrives after its session was written starts a new session; `sessions.py rebuild --date YYYY-MM-DD` regroups the whole day.
- A note without a summary means no key, `SESSION_SUMMARY=0`, or a failed request (the note says which). After fixing the cause, run `sessions.py retry`.

## Speaker labels are missing

- `DIARIZATION=1` needs `./install.sh --with-diarization` and a Hugging Face token whose account accepted the terms of both `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`. The doctor checks the package and the token; the log line `speaker labelling failed` names the actual error.
- A pipeline that loads as `None` means the model terms were not accepted for that token.
- Labelling is slow on CPU and needs memory on top of the Whisper model; the recording is still transcribed if it fails.

## OpenRouter summarization fails

Transcription should still be written; the log records the summarization failure. Verify the key, model ID, account availability, and network access. Clear `OPENROUTER_API_KEY` to restore local-only operation.

## A service is stuck or failed

Inspect before restarting:

```bash
systemctl --user status usb-audio-transcriber.service
journalctl --user-unit=usb-audio-transcriber.service -n 200 --no-pager
```

Then, if no healthy transcription is active:

```bash
systemctl --user reset-failed usb-audio-transcriber.service
systemctl --user start usb-audio-transcriber.service
```

Do not delete the queue, archive, or state database as a first response. Preserve them and diagnose the failing recording or configuration.

## Reinstall without losing configuration

Running `./install.sh`, or the one-line installer again, updates deployed
program files and preserves an existing installed `config.env`. The one-line
installer keeps its checkout under `~/.local/share/usb-audio-transcriber/src`. Dependencies are installed into the virtual environment
before deployed files are replaced, so a failed download cannot damage a
working installation. The uninstaller removes deployed code, the virtual
environment, and user units while preserving configuration, runtime state,
archives, transcripts, and model caches. Reinstalling later reuses the preserved
configuration.
