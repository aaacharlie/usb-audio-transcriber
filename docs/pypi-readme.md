# USB Audio Transcriber

**Plug in a voice recorder. Walk away. Come back to timestamped, searchable notes and a summary of the whole session.**

A free, open-source Linux utility that turns a cheap USB voice recorder (or any synced folder of voice memos) into a hands-off note-taking system. Plug the recorder in and, within seconds, it finds the new recordings, copies them to a checksum-verified archive, transcribes them locally with faster-whisper, and writes timestamped Markdown notes straight into your Obsidian vault or any notes folder. Recordings that a voice-activated recorder split into a dozen files are stitched back into one session note in the right order, with an optional AI summary from a tool you already pay for (Codex, Claude Code), a local Ollama model, or OpenRouter.

Nothing is uploaded: transcription runs on your own CPU, no GPU required.

## Install

```bash
sudo apt install pipx ffmpeg zenity libnotify-bin      # Debian/Ubuntu names
pipx install usb-audio-transcriber
usb-audio-transcriber install
```

`install` creates `~/.local/share/usb-audio-transcriber/config.env`, asks where your notes should go (it finds your Obsidian vaults), enables the background service and the plug-in trigger, and adds **USB Audio Transcriber** to your app menu: a control panel with the pipeline's state, sessions with a Summarize button, search, and every setting as a form. Everything in the panel is also a command:

```bash
usb-audio-transcriber doctor            # check the installation
usb-audio-transcriber sessions list     # session notes
usb-audio-transcriber search roof leak  # every matching moment, newest first
usb-audio-transcriber panel open        # the control panel
usb-audio-transcriber update            # pipx upgrade, then install again
```

Speaker labels need the optional extra: `pipx install "usb-audio-transcriber[diarization]"`.

Full documentation, the recorder that was tested, and the summary workflow: <https://github.com/aaacharlie/usb-audio-transcriber>.
