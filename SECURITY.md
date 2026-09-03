# Security policy

## Supported version

Security fixes are applied to the current `main` branch. The project does not currently publish versioned releases with separate support windows.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository when available. If it is unavailable, contact the repository owner privately through their GitHub profile rather than opening a public issue.

Include:

- affected component and commit
- reproduction steps using non-sensitive test data
- realistic impact
- suggested mitigation, if known

Do not attach API keys, private recordings, transcripts, local configuration, filesystem listings containing sensitive names, or other personal data.

## Expected response

This is a small open-source project without a guaranteed response SLA. Reports will be acknowledged and evaluated as availability permits. Confirmed issues should be fixed privately before coordinated disclosure when practical.

## Scope reminders

A report is especially relevant when it concerns unintended audio/transcript disclosure, unsafe source deletion, command execution, credential exposure, path traversal, or bypass of deduplication/archive integrity guarantees. General upstream issues in faster-whisper, CTranslate2, requests, Hugging Face, OpenRouter, Python, systemd, or Zenity should also be reported to the affected upstream project.
