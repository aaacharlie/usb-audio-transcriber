#!/usr/bin/env python3
"""Import audio from mounted removable media and enqueue it for transcription."""
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import load, log, write_progress

CFG = load()
ARCHIVE = Path(CFG["ARCHIVE_DIR"])
QUEUE = Path(CFG["QUEUE_DIR"])
STATE_DB = Path(CFG["STATE_DB"])
EXTS = {entry.lower() for entry in CFG["AUDIO_EXTS"].split(",")}
RECORDER_DIR = CFG.get("RECORDER_DIR", "RECORD")
PURGE = CFG.get("PURGE_DEVICE", "0") == "1"
MOUNT_ROOTS = [
    Path("/media") / os.environ.get("USER", "root"),
    Path("/run/media") / os.environ.get("USER", "root"),
    Path("/mnt"),
]


def init_db():
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS seen (
               sha256 TEXT PRIMARY KEY, orig_name TEXT, archived_to TEXT,
               bytes INTEGER, imported_at TEXT, transcribed INTEGER DEFAULT 0
           )"""
    )
    con.commit()
    return con


def sha256(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def stable(path, settle=3.0):
    """Do not copy a recorder file still being written."""
    try:
        first = path.stat().st_size
        time.sleep(settle)
        return first == path.stat().st_size and first > 4096
    except OSError:
        return False


def find_candidates():
    found = []
    for root in MOUNT_ROOTS:
        if not root.exists():
            continue
        for mount in root.iterdir():
            if not mount.is_dir():
                continue
            try:
                for path in mount.rglob("*"):
                    if (path.is_file() and path.parent.name == RECORDER_DIR
                            and path.suffix.lstrip(".").lower() in EXTS
                            and path.stat().st_size > 4096):
                        found.append(path)
            except (PermissionError, OSError):
                continue
    return found


def archive_path_for(src):
    timestamp = datetime.fromtimestamp(src.stat().st_mtime)
    directory = ARCHIVE / f"{timestamp:%Y}" / f"{timestamp:%m}" / f"{timestamp:%d}"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{timestamp:%Y%m%d-%H%M%S}_{src.stem}{src.suffix.lower()}"
    number = 1
    while destination.exists():
        destination = directory / f"{timestamp:%Y%m%d-%H%M%S}_{src.stem}_{number}{src.suffix.lower()}"
        number += 1
    return destination


def main():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    QUEUE.mkdir(parents=True, exist_ok=True)
    con = init_db()
    write_progress(active=False, phase="Scanning USB media", detected_files=0,
                   imported_files=0, files_completed=0)
    candidates = find_candidates()
    if not candidates:
        log("No removable audio found.")
        write_progress(active=False, phase="No removable audio found")
        con.close()
        return 0

    log(f"Found {len(candidates)} audio file(s) on mounted media.")
    imported = 0
    for src in candidates:
        if not stable(src):
            log(f"  wait {src.name} (still changing)")
            continue
        try:
            digest = sha256(src)
        except OSError as exc:
            log(f"  SKIP {src.name}: {exc}")
            continue
        if con.execute("SELECT 1 FROM seen WHERE sha256=?", (digest,)).fetchone():
            log(f"  dup  {src.name}")
            if PURGE:
                src.unlink(missing_ok=True)
            continue

        destination = archive_path_for(src)
        log(f"  copy {src.name} -> {destination.name}")
        shutil.copy2(src, destination)
        if sha256(destination) != digest:
            log("  !! checksum mismatch, discarding copy")
            destination.unlink(missing_ok=True)
            continue
        con.execute(
            "INSERT INTO seen (sha256, orig_name, archived_to, bytes, imported_at) "
            "VALUES (?,?,?,?,?)",
            (digest, src.name, str(destination), destination.stat().st_size,
             datetime.now().isoformat(timespec="seconds")),
        )
        con.commit()
        link = QUEUE / destination.name
        if not link.exists():
            os.symlink(destination, link)
        imported += 1
        if PURGE:
            try:
                src.unlink()
                log("       purged from recorder")
            except OSError as exc:
                log(f"       purge failed: {exc}")

    queued = sum(1 for path in QUEUE.iterdir()
                 if (path.is_file() or path.is_symlink())
                 and path.suffix.lstrip(".").lower() in EXTS)
    log(f"Imported {imported} new file(s).")
    write_progress(active=bool(queued), phase="Queued for transcription",
                   detected_files=len(candidates), imported_files=imported,
                   total_files=queued, files_completed=0)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
