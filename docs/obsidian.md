# Obsidian

USB Audio Transcriber writes plain Markdown, so it works with any notes app, but it is built with Obsidian in mind.

## Point the notes at your vault

The setup wizard runs on first install and can be run again any time:

```bash
~/.local/share/usb-audio-transcriber/venv/bin/python \
  ~/.local/share/usb-audio-transcriber/bin/setup.py
```

It reads the vault list Obsidian keeps for the native, Flatpak, and Snap packages, falls back to a short search of your home folder for `.obsidian` directories, and offers to create a folder such as `Recordings` inside the vault you pick. On a desktop it shows dialogs; over SSH it asks in the terminal. Nothing in the vault is touched except that folder.

To do it by hand, set `VAULT_DIR` in `config.env`:

```ini
VAULT_DIR="${HOME}/Documents/MyVault/Recordings"
```

## What lands in the vault

- **Transcript notes** (`2026-09-05 0930 transcript.md`): front matter with `date`, `time`, `duration_min`, `speech_min`, the model, and `tags: [transcript, inbox]`, followed by timestamped segments and, with a key, a per-recording summary.
- **Session notes** (`2026-09-05 0900 session.md`): one per meeting, class, or site visit. They link every transcript with `[[wikilinks]]`, stitch the recordings into one combined transcript, and carry the AI summary of the whole session when a key is configured. Because they use wikilinks, each transcript note shows its session under Backlinks, and the graph view connects them.

Audio, JSON sidecars, and plain-text files stay under `ARCHIVE_DIR`, not in the vault, so the vault only ever contains notes.

## Useful queries

The `inbox` tag marks notes you have not processed yet; remove it as you file them. With the Dataview plugin, a table of sessions:

```text
TABLE date, time, recordings, duration_min
FROM "Recordings"
WHERE type = "session"
SORT date DESC, time DESC
```

## Sync: Obsidian Sync, Syncthing, iCloud, Dropbox

The transcriber writes files into the vault folder on the Linux machine. Whatever keeps that vault in sync carries the notes to your phone and other computers. Obsidian Sync needs nothing extra: keep the notes folder inside a synced vault. There is no Obsidian cloud API to integrate with, and none is needed.

Sync also works in the other direction. If your phone's voice memos land in a synced folder, add that folder to `WATCH_DIRS` and they are transcribed like recorder files. See the usage guide.
