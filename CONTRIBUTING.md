# Contributing

Contributions are welcome when they preserve the project's local-first and source-safe behavior.

## Before opening a change

1. Search existing issues and pull requests.
2. Keep the change focused; avoid unrelated refactors.
3. Do not include recordings, transcripts, API keys, local configuration, model weights, caches, or runtime state.
4. For destructive or privacy-affecting behavior, explain the safety boundary explicitly.

## Development workflow

```bash
git switch -c fix/short-description
# make the change
python3 -m py_compile bin/*.py usb_audio_transcriber/*.py
bash -n install.sh uninstall.sh bootstrap.sh bin/run-cycle.sh
python3 -m unittest discover -s tests -v
git diff --check
```

Use a conventional commit subject such as `fix: preserve queue item after model failure` or `docs: clarify recorder discovery`.

## Tests

Behavior changes should include deterministic tests under `tests/`. Mock expensive model execution and external APIs. Never make CI depend on private audio, API keys, model downloads, desktop access, or a mounted USB device.

If you perform a real-audio test, report hardware, model, device/compute configuration, audio duration, and elapsed time. Do not publish the source or transcript unless you have permission.

## Pull requests

Include:

- what changed and why
- safety/privacy implications
- compatibility implications for existing `config.env` files
- exact verification commands and results
- screenshots only when the desktop UI changed and the image contains no private information

By contributing, you agree that your contribution is licensed under the repository's MIT license.
