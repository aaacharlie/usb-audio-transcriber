## Summary

- 

## Safety and privacy

- Source deletion behavior changed: no
- Cloud data flow changed: no
- Credential handling changed: no
- Existing `config.env` compatibility changed: no

## Verification

- [ ] `python3 -m py_compile bin/*.py`
- [ ] `bash -n install.sh uninstall.sh bin/run-cycle.sh`
- [ ] `python3 -m unittest discover -s tests -v`
- [ ] `git diff --check`

## Manual testing

Describe any USB, desktop, model, or real-audio validation. Do not attach private recordings or transcripts.
