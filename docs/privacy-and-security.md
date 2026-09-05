# Privacy and security

## Data that remains local

By default, the project processes all of the following locally:

- source audio discovery
- SHA-256 hashing and archive verification
- faster-whisper transcription
- JSON and text transcript artifacts
- Markdown note generation
- SQLite deduplication state

Optional speaker labelling (pyannote.audio) also runs locally. Its Hugging Face token is used only to download the gated models, and it lives in `config.env` alongside the OpenRouter key.

No telemetry is implemented by this project.

## Optional cloud processing

Cloud processing is enabled only when `OPENROUTER_API_KEY` is populated. In that mode, transcript text is sent to the configured OpenRouter model for summarization: each recording's text for the per-file summary (`FILE_SUMMARY`), and the combined text of a whole session for the session summary (`SESSION_SUMMARY`). This project does not send raw audio to OpenRouter. Session notes themselves (links and the combined transcript) are generated locally.

Treat transcripts as potentially sensitive. Before enabling summarization, verify that sending their text to the configured provider is acceptable for the recording's participants and your jurisdiction.

## Credentials

- Store the OpenRouter key only in the installed `config.env`.
- Never commit `config.env`; the repository ignores it.
- Avoid placing secrets in systemd unit files, shell history, issue reports, or logs.
- If a key is exposed, revoke it at the provider and replace it.

## Source deletion

`PURGE_DEVICE=0` is the safe default. With `PURGE_DEVICE=1`, a newly imported
source is removed only after a checksum-verified archive copy has been created.
A duplicate source is removed only when the archive path recorded in SQLite
still exists and matches the same SHA-256 digest. This is still destructive
behavior: verify archive paths, backups, and available storage before enabling
it.

## File permissions and backups

The application uses the invoking user's normal permissions. Protect `ARCHIVE_DIR`, `VAULT_DIR`, `STATE_DB`, and `config.env` according to the sensitivity of the recordings. The project does not encrypt files or create backups; use an encrypted filesystem and a separate tested backup process when required.

## Dependency and model downloads

Installation downloads pinned Python dependencies from the configured package index. First model use downloads model weights through faster-whisper/Hugging Face. Review dependency and model provenance before use in high-assurance environments.

## Reporting a vulnerability

Do not disclose secrets or private recordings in a public GitHub issue. Follow the private reporting process in [SECURITY.md](../SECURITY.md).
