# Development guide

## Repository layout

```text
bin/                 Python entry points and run-cycle shell script
systemd/             user service and timer units
tests/               standard-library unittest suite
docs/                project documentation
config.example.env   documented configuration template
install.sh            local user installation/update
uninstall.sh          remove installed program and units
```

## Local checks

The CI workflow uses Python 3.12 and runs:

```bash
python3 -m py_compile bin/*.py
bash -n install.sh uninstall.sh bin/run-cycle.sh
python3 -m unittest discover -s tests -v
```

Also run:

```bash
git diff --check
```

Tests mock model execution; they do not download model weights or transcribe real audio.

## Manual smoke checks

Commands that do not mutate the live queue:

```bash
python3 bin/model-cache.py status both
python3 bin/benchmark-models.py --help
```

A real benchmark requires the project's virtual environment and an audio file. Write results outside tracked repository paths.

## Design constraints

Changes should preserve these properties:

- never process audio outside the configured recorder directory
- never mark a copy imported before checksum verification
- deduplicate by file content
- keep purge opt-in
- avoid overlapping timer cycles
- leave transcript output available when optional summarization fails
- resume multi-profile work without redoing durable completed artifacts
- load only one Whisper model at a time
- keep `config.env`, runtime data, audio, and generated transcripts untracked

## Submitting changes

See [CONTRIBUTING.md](../CONTRIBUTING.md). Add or update tests for behavior changes and describe any real-audio validation separately from deterministic automated tests.
