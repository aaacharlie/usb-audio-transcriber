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
from pipeline_config import load, log, sync_directory, write_progress

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
    try:
        STATE_DB.chmod(0o600)
        con.execute(
            """CREATE TABLE IF NOT EXISTS seen (
                   sha256 TEXT PRIMARY KEY, orig_name TEXT, archived_to TEXT,
                   bytes INTEGER, imported_at TEXT, transcribed INTEGER DEFAULT 0
               )"""
        )
        con.commit()
    except BaseException:
        con.close()
        raise
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


def sync_file_and_parent(path):
    """Flush copied file data and its directory entry to stable storage."""
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    sync_directory(path.parent)


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
                    if (path.is_file() and not path.is_symlink()
                            and path.parent.name == RECORDER_DIR
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


def ensure_queued(archived):
    """Create or validate the queue symlink for an archived recording."""
    link = QUEUE / archived.name
    if link.is_symlink():
        return link.resolve() == archived.resolve()
    if link.exists():
        return False
    os.symlink(archived, link)
    sync_directory(QUEUE)
    return True


def main():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    QUEUE.mkdir(parents=True, exist_ok=True)
    con = init_db()
    try:
        write_progress(active=False, phase="Scanning USB media", detected_files=0,
                       imported_files=0, files_completed=0)
        candidates = find_candidates()
        if not candidates:
            log("No removable audio found.")
            write_progress(active=False, phase="No removable audio found")
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
            seen = con.execute(
                "SELECT archived_to, transcribed FROM seen WHERE sha256=?", (digest,)
            ).fetchone()
            if seen:
                log(f"  dup  {src.name}")
                archived = Path(seen[0]) if seen[0] else None
                try:
                    verified = archived is not None and archived.is_file() \
                        and sha256(archived) == digest
                except OSError:
                    verified = False
                queue_ready = bool(seen[1])
                if verified and not seen[1]:
                    queue_ready = ensure_queued(archived)
                    if queue_ready:
                        log("       pending archive is queued")
                if PURGE:
                    if verified and queue_ready:
                        src.unlink(missing_ok=True)
                        log("       purged duplicate after archive verification")
                    else:
                        log("       NOT purged: archive or pending queue is unverified")
                continue

            destination = archive_path_for(src)
            log(f"  copy {src.name} -> {destination.name}")
            shutil.copy2(src, destination)
            destination.chmod(0o600)
            if sha256(destination) != digest:
                log("  !! checksum mismatch, discarding copy")
                destination.unlink(missing_ok=True)
                continue
            try:
                sync_file_and_parent(destination)
            except OSError:
                destination.unlink(missing_ok=True)
                raise
            con.execute(
                "INSERT INTO seen (sha256, orig_name, archived_to, bytes, imported_at) "
                "VALUES (?,?,?,?,?)",
                (digest, src.name, str(destination), destination.stat().st_size,
                 datetime.now().isoformat(timespec="seconds")),
            )
            con.commit()
            if not ensure_queued(destination):
                raise RuntimeError(f"queue path conflicts with archive: {destination.name}")
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
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
