"""USB Audio Transcriber as a Python package.

The program itself is the set of scripts under ``bin/`` (copied into this
package by the wheel build); :mod:`usb_audio_transcriber.cli` is the
``usb-audio-transcriber`` command that runs them.
"""
from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("usb-audio-transcriber")
except PackageNotFoundError:  # a git checkout that is not installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
