# The control panel

The control panel is a desktop window: a native GTK 4 / libadwaita app (`bin/app.py`) that looks and behaves like the other apps on a Linux desktop, no browser involved. It talks to a small local server the pipeline runs (`bin/panel.py`), and the same server also serves a web page with the same features, for machines without the GTK bindings and for your phone. Every button runs the same script you could type in a terminal, so nothing lives only in the panel and nothing in the panel is required.

## Open it

- From the app menu: the **USB Audio Transcriber** entry that the installer adds.
- From a terminal: `~/.local/share/usb-audio-transcriber/bin/panel.py open` (`usb-audio-transcriber panel open` with pipx) starts the server if needed and opens the window. `bin/app.py --page settings` opens straight on a page. Add `--web` for the web page in a browser window (Chrome, Chromium, Brave, Edge) or tab, `--browser` for a plain tab, or `--no-browser` to only print the private link.
- From another device, such as your phone: in Settings set "Panel listens on" to `0.0.0.0` (or `PANEL_BIND` in `config.env`), then copy the link from the Tools page (or run `bin/panel.py url`) and open it on the other device.

Opening the entry again while the window is up raises that window instead of starting a second one (`--page` in a second launch switches its page). The window needs the GTK bindings for Python, which most desktops already have; on Debian and Ubuntu they are `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`. Without them `panel.py open` says so once and opens the web page instead, so nothing is lost. The window runs with the system's `python3` (that is where the bindings live), while the pipeline keeps its own environment.

The `usb-audio-transcriber-panel.service` user unit keeps the server running in the background; the doctor reports its state, and `panel.py open` starts a server itself if the service is not running.

## What is on it

- **Home.** Whether the timer and the plug-in trigger are active, the last activity and phase, how many recordings are queued and how many files are visible on a plugged-in recorder, library counts, the summary backend with a Test button, the Whisper model cache, free disk, and the notes folder. Buttons run a cycle now and pause or resume automatic runs. Below that, recent recordings and an activity list where every started job shows its output.

Every button that starts something shows its result right where you pressed it: the newest job is expanded in an **Activity** (or **Latest action**) box on that page, its output streams in while it runs, the status line at the bottom of the window names the running job, and a message says when it finished or failed. Failed jobs keep their output so you can read what went wrong.
- **Sessions.** Every session, newest first. View reads the note inside the panel; Summarize sends that session's combined transcript to the backend picked in the toolbar (the configured one, a command-line tool such as Codex or Claude Code, a local Ollama model, or OpenRouter); tick several and summarize them in one go; "Summarize all without a summary" runs `sessions.py retry`. Notes are rewritten in place with the new summary.
- **Recordings.** Recent imports, whether each is transcribed, and its note.
- **Search.** The same search as `search.py`, with date and speaker filters. Click a result to read the note.
- **Settings.** Every setting from `config.env` as a form, grouped and explained: notes folder, recorder folder, watched folders, model, language, translate, summary backend and its command or model, subject, session gap, backfill guard, speaker labels, desktop behaviour, and the panel's own address. Secrets show as saved or not set and are never displayed. "Find my Obsidian vault" fills the notes folder from the vaults Obsidian knows about. Save runs the doctor's checks first and refuses invalid values, so the file cannot be broken from the panel. Changes apply on the next cycle.
- **Tools.** Run the doctor, refresh the search index, download or remove Whisper models, rebuild a day's sessions, and read the log. The doctor's report appears in the Activity box directly under the buttons, `OK`, `WARN`, and `FAIL` lines included.

## Terminal equivalents

| Panel action | Command |
| --- | --- |
| Run a cycle now | `systemctl --user start usb-audio-transcriber.service` |
| Pause / resume automatic runs | `systemctl --user stop` / `start usb-audio-transcriber.timer usb-audio-transcriber-plug.path` |
| Summarize selected sessions | `bin/sessions.py summarize --id ID [--id ID] [--backend command\|openai\|openrouter]` |
| Summarize all without a summary | `bin/sessions.py retry` |
| Test the summary backend | `bin/sessions.py test-backend` |
| Search | `bin/search.py words --since DATE --speaker NAME` |
| Save settings | edit `config.env`, then `bin/doctor.py` |
| Find the Obsidian vault | `bin/setup.py` |
| Doctor, index, models, rebuild | `bin/doctor.py`, `bin/search.py --index`, `bin/model-cache.py`, `bin/sessions.py rebuild --date DATE` |

## Security

- The panel listens on `127.0.0.1` unless `PANEL_BIND` says otherwise, so nothing outside this machine can reach it by default.
- A private token is created at first start (`var/state/panel-token`, readable by you only). Every request needs it: the window reads it from that file, the private link stores it as a cookie, and the page sends it with each call. Without it the panel shows a short "needs its private link" page and no data.
- Requests that change anything also need a marker that only the panel's own page sends, so another website open in your browser cannot drive it.
- Only notes and sidecars under the notes folder or the archive can be read or opened from the panel.
- Actions are a fixed list of scripts with validated arguments. The panel never runs text from the page as a command. The summary command in Settings is executed only by the summary step, exactly as when it is set in `config.env` by hand.
- With `PANEL_BIND=0.0.0.0`, anyone on your network who has the link can read transcripts and change settings. Share the link only with your own devices.

## Headless machines

The panel service runs on a server or Raspberry Pi too, and it is the easiest way to use search and sessions there: set `PANEL_BIND=0.0.0.0` and open the link from `panel.py url` on your laptop or phone. Pause, resume, and "run a cycle now" need systemd; where it is missing, those buttons are disabled and everything else works.
