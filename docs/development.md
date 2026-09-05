# Development guide

## Repository layout

```text
bin/                    Python entry points and run-cycle shell script
usb_audio_transcriber/  the pip/pipx package: the usb-audio-transcriber command (cli.py)
panel/                  the control panel page served by bin/panel.py
prompts/                default AI prompt templates deployed with the program
share/                  app-menu entry and icon for the control panel
systemd/                user service, timer, and plug-in trigger unit templates
tests/                  standard-library unittest suite
docs/                   project documentation
config.example.env      documented configuration template
pyproject.toml          package metadata and build (hatchling; version from the git tag)
bootstrap.sh            one-command installer: clone or update, then install.sh
install.sh              local user installation/update
uninstall.sh            remove installed program and units
```

Two installation layouts share the same code:

- `install.sh` copies `bin/`, `prompts/`, `panel/`, `share/`, and `systemd/` next to a virtual environment, `config.env`, and `var/` under `~/.local/share/usb-audio-transcriber`, so the program files and the data root are the same folder.
- The wheel carries copies of those folders inside the `usb_audio_transcriber` package (`pyproject.toml`, `force-include`), and `usb-audio-transcriber` runs the scripts from there with `USB_AUDIO_TRANSCRIBER_ROOT` pointing at the data folder. `bin/pipeline_config.py` therefore has two anchors: `ASSETS` (prompts, templates, the panel page, beside `bin/`) and `ROOT` (`config.env`, `var/`). `bin/run-cycle.sh` takes `USB_AUDIO_TRANSCRIBER_PYTHON` and `USB_AUDIO_TRANSCRIBER_BIN` for the same reason.

The systemd and desktop templates have two placeholders, `@CYCLE_COMMAND@` and `@PANEL_COMMAND@`, that `install.sh` fills with the installed scripts and `usb-audio-transcriber install` fills with its own command.

## Local checks

The CI workflow uses Python 3.12 and runs:

```bash
python3 -m py_compile bin/*.py usb_audio_transcriber/*.py
bash -n install.sh uninstall.sh bootstrap.sh bin/run-cycle.sh
python3 -m unittest discover -s tests -v
```

CI also builds the package and installs the wheel with pipx. Locally:

```bash
python3 -m pip install build
python3 -m build                      # dist/*.whl and dist/*.tar.gz
pipx install dist/*.whl               # then: usb-audio-transcriber --root /tmp/uat paths
```

The version comes from the nearest `v*` tag (`git fetch --tags` in a fresh clone), so a build between releases is `1.0.1.devN+g<sha>`.

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
- keep speaker labelling optional and non-blocking: a diarization failure still produces a transcript
- keep `config.env`, runtime data, audio, and generated transcripts untracked

## Releases

1. Update `CHANGELOG.md`: give the top section the new version number and date.
2. Merge to `main`.
3. On GitHub open Actions, choose the Release workflow, click "Run workflow", and enter the version (for example `1.0.0`). The workflow runs the checks, tags `v1.0.0` on `main`, and publishes a GitHub release whose notes are that changelog section. Pushing a `v*` tag by hand publishes the release the same way.
4. The workflow's `publish` job then builds the package at that tag and uploads it to PyPI, once publishing is switched on (below). No version number lives in the code: the package version is the tag.

### Publishing to PyPI (one-time setup)

The `publish` job uses PyPI's trusted publishing (no API token to store) and only runs when the repository variable `PUBLISH_TO_PYPI` is `true`.

1. Create a PyPI account at <https://pypi.org/account/register/> and enable two-factor authentication (PyPI requires it).
2. Open <https://pypi.org/manage/account/publishing/> and, under "Add a new pending publisher", fill in: PyPI project name `usb-audio-transcriber`, owner `aaacharlie`, repository `usb-audio-transcriber`, workflow name `release.yml`, environment name `pypi`.
3. In the GitHub repository open Settings, Secrets and variables, Actions, the Variables tab, and add a repository variable `PUBLISH_TO_PYPI` with the value `true`.
4. Run the Release workflow. The first successful upload creates the PyPI project; from then on `pipx install usb-audio-transcriber` works, and the README's `git+https://...` line can become the plain package name.

## Submitting changes

See [CONTRIBUTING.md](../CONTRIBUTING.md). Add or update tests for behavior changes and describe any real-audio validation separately from deterministic automated tests.
