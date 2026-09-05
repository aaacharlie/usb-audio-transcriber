# USB Audio Transcriber documentation

This directory is the project's in-repository wiki. It is versioned and reviewed with the code so documentation stays available without depending on GitHub's separate wiki repository.

## Start here

- [Usage guide](usage.md) — normal workflow, outputs, folder watching, session notes and AI summaries, headless machines, manual runs, cache commands, and isolated benchmarks
- [Configuration reference](configuration.md) — every supported setting and how changes are applied
- [Whisper model profiles](model-profiles.md) — model trade-offs, measured results, disk/RAM behavior, and selection advice

## Understand the system

- [Architecture](architecture.md) — component map, data flow, safety boundaries, and storage lifecycle
- [Privacy and security](privacy-and-security.md) — local/cloud boundaries, credentials, deletion, permissions, and reporting

## Diagnose or contribute

- [Troubleshooting](troubleshooting.md) — inspect-first checks for discovery, services, progress, models, and summarization
- [Development guide](development.md) — repository layout, checks, design constraints, and contribution workflow
- [Contributing](../CONTRIBUTING.md) — pull-request and testing expectations
- [Security policy](../SECURITY.md) — private vulnerability reporting

## Project guarantees

The documentation and implementation are organized around these guarantees:

1. USB source files are not deleted by default, and watched-folder sources are never deleted.
2. Imported copies are verified by SHA-256 before entering the queue.
3. Duplicate content is not re-imported under a different filename.
4. Transcription is local unless OpenRouter text summarization is explicitly enabled.
5. Only one Whisper model is loaded at a time.
6. Runtime data, audio, transcripts, model caches, and local secrets remain outside version control.
