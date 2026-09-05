#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one version; used by the release workflow."""
import re
import sys
from pathlib import Path

REPO = "https://github.com/aaacharlie/usb-audio-transcriber"


def section(text, version):
    heading = re.compile(rf"^## \[?{re.escape(version)}\]?\b.*$", re.M)
    match = heading.search(text)
    if not match:
        return None
    rest = text[match.end():]
    following = re.search(r"^## ", rest, re.M)
    body = rest[:following.start()] if following else rest
    return body.strip()


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: release-notes.py VERSION")
    version = argv[1].lstrip("v")
    body = section(Path("CHANGELOG.md").read_text(encoding="utf-8"), version)
    if body is None:
        sys.exit(f"CHANGELOG.md has no section for version {version}")
    print(body)
    print(f"\nFull changelog: {REPO}/blob/main/CHANGELOG.md")


if __name__ == "__main__":
    main(sys.argv)
