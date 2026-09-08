#!/usr/bin/env python3
"""Import audio from removable media and watched folders, then enqueue it."""
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
# "mp3, .wav" and "mp3,wav" mean the same thing here and in transcribe.py.
EXTS = {entry.strip().lstrip(".").lower() for entry in CFG["AUDIO_EXTS"].split(",") if entry.strip()}
RECORDER_DIR = CFG.get("RECORDER_DIR", "RECORD")
PURGE = CFG.get("PURGE_DEVICE", "0") == "1"
# Extra folders to scan recursively (sync folders, phone exports, network
# shares). Sources found here are never purged: deleting a synced file would
# propagate the deletion to every other device.
WATCH_DIRS = [
    Path(os.path.expandvars(os.path.expanduser(entry.strip())))
    for entry in CFG.get("WATCH_DIRS", "").split(":")
    if entry.strip()
]
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


# Folders never worth descending into on a mounted drive.
SKIP_DIRS = {"lost+found", "System Volume Information", "$RECYCLE.BIN", "node_modules", ".git"}
# A recorder keeps its folder at the top of the drive; the cap keeps a large
# external disk from being walked whole on every cycle and status refresh.
MAX_DEPTH = 4


def usable_name(name):
    """Linux allows file names that are not valid UTF-8; Python carries them as
    surrogate escapes that SQLite refuses. Such a file is skipped, not imported."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        log(f"  SKIP {name!r}: file name is not valid UTF-8, rename it to import it")
        return False
    return True


def find_candidates():
    found = []
    for root in MOUNT_ROOTS:
        if not root.exists():
            continue
        try:
            # A symlink under /mnt that points into a home folder is not a drive.
            mounts = sorted(mount for mount in root.iterdir()
                            if mount.is_dir() and not mount.is_symlink())
        except OSError:
            continue
        for mount in mounts:
            found.extend(scan_mount(mount))
    return found


def removable_device(path, sysfs="/sys", device=None):
    """Whether `path` sits on a removable block device (a USB recorder): True, False,
    or None when sysfs cannot say. Only recordings on removable drives are purged."""
    try:
        if device is None:
            device = os.stat(path).st_dev
        node = (Path(sysfs) / "dev" / "block" / f"{os.major(device)}:{os.minor(device)}").resolve()
    except OSError:
        return None
    for candidate in (node, node.parent):  # a partition's flag lives on its disk
        flag = candidate / "removable"
        if flag.is_file():
            try:
                return flag.read_text(encoding="utf-8").strip() == "1"
            except OSError:
                return None
    return None


def scan_mount(mount, max_depth=MAX_DEPTH):
    """Audio directly inside a RECORDER_DIR folder on one mounted drive, looking at
    most max_depth levels down and skipping hidden and system folders."""
    found = []
    pending = [(Path(mount), 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if (depth < max_depth and not entry.name.startswith(".")
                                    and entry.name not in SKIP_DIRS):
                                pending.append((Path(entry.path), depth + 1))
                        elif (entry.is_file(follow_symlinks=False)
                                and directory.name == RECORDER_DIR
                                and not entry.name.startswith(".")  # macOS ._name.wav shadows
                                and Path(entry.name).suffix.lstrip(".").lower() in EXTS
                                and entry.stat(follow_symlinks=False).st_size > 4096
                                and usable_name(entry.name)):
                            found.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(found)


def find_watch_candidates():
    """Discover audio anywhere inside the explicitly configured watch folders."""
    found = []
    excluded = [ARCHIVE.resolve(), QUEUE.resolve()]
    for root in WATCH_DIRS:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.rglob("*"))
        except (PermissionError, OSError):
            continue
        for path in entries:
            try:
                relative_parts = path.relative_to(root).parts
                if any(part.startswith(".") for part in relative_parts):
                    continue  # sync tools keep partial downloads in dot-files
                if path.is_symlink() or not path.is_file():
                    continue
                if path.suffix.lstrip(".").lower() not in EXTS:
                    continue
                if path.stat().st_size <= 4096:
                    continue
                if not usable_name(path.name):
                    continue
                resolved = path.resolve()
                if any(resolved == item or item in resolved.parents
                       for item in excluded):
                    continue  # never re-import our own archive or queue
            except OSError:
                continue
            found.append(path)
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


PARTIAL_SUFFIX = ".partial"


def archive_from_source(src, digest):
    """Copy a recording into the archive, checksum it, and make it durable.

    The copy is written under a temporary name and moved to its final name only
    once verified and flushed, so an unplugged drive or a full disk never leaves
    a truncated file under a real recording's name. Returns the archived path,
    or None when the copy failed verification; raises OSError when the copy
    itself failed (nothing is left behind).
    """
    destination = archive_path_for(src)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    log(f"  copy {src.name} -> {destination.name}")
    try:
        shutil.copy2(src, partial)
        partial.chmod(0o600)
        if sha256(partial) != digest:
            log("  !! checksum mismatch, discarding copy")
            partial.unlink(missing_ok=True)
            return None
        sync_file_and_parent(partial)
        os.replace(partial, destination)
        sync_directory(destination.parent)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
    return destination


def sweep_partials():
    """Remove copies a crashed or unplugged earlier cycle left half-written."""
    removed = 0
    try:
        for leftover in ARCHIVE.rglob(f"*{PARTIAL_SUFFIX}"):
            if leftover.is_file():
                leftover.unlink(missing_ok=True)
                removed += 1
    except OSError:
        pass
    if removed:
        log(f"Removed {removed} partial copy(ies) left by an interrupted cycle.")


def ensure_queued(archived):
    """Create or validate the queue symlink for an archived recording."""
    link = QUEUE / archived.name
    if link.is_symlink():
        if link.resolve() == archived.resolve():
            return True
        if link.exists():
            return False
        link.unlink()  # stale dangling entry may be replaced safely
    elif link.exists():
        return False
    os.symlink(archived, link)
    sync_directory(QUEUE)
    return True


def main():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    QUEUE.mkdir(parents=True, exist_ok=True)
    con = init_db()
    try:
        write_progress(active=False, phase="Scanning for recordings", detected_files=0,
                       imported_files=0, files_completed=0)
        sweep_partials()
        candidates = find_candidates()
        watched = find_watch_candidates()
        if not candidates and not watched:
            log("No new audio found on removable media or watched folders.")
            write_progress(active=False, phase="No recordings found")
            return 0

        log(f"Found {len(candidates)} audio file(s) on mounted media"
            f" and {len(watched)} in watched folders.")
        # Purging is for the recorder only: a backup disk or a network share
        # mounted under the same roots keeps everything, whatever PURGE_DEVICE says.
        purgeable = set()
        kept_drives = set()
        if PURGE:
            for src in candidates:
                if removable_device(src) is True:
                    purgeable.add(src)
                else:
                    drive = src.parents[-2] if len(src.parents) > 1 else src.parent
                    if drive not in kept_drives:
                        kept_drives.add(drive)
                        log(f"  {drive}: not a removable drive, its recordings are imported but never purged")
        imported = 0
        for src in candidates + watched:
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
                queue_ready = bool(seen[1])
                verified = False
                if PURGE or not queue_ready:
                    archived = Path(seen[0]) if seen[0] else None
                    try:
                        verified = archived is not None and archived.is_file() \
                            and sha256(archived) == digest
                    except OSError:
                        verified = False
                    if verified and not queue_ready:
                        queue_ready = ensure_queued(archived)
                        if queue_ready:
                            log("       pending archive is queued")
                    elif not queue_ready:
                        # Lost/corrupt archive but the source is still on
                        # the device: restore it. Purge waits for a later
                        # run's re-verification, so `verified` stays False.
                        try:
                            restored = archive_from_source(src, digest)
                        except OSError as exc:
                            log(f"  SKIP {src.name}: copy failed ({exc})")
                            continue
                        if restored is not None:
                            con.execute(
                                "UPDATE seen SET archived_to=?, bytes=?, "
                                "imported_at=? WHERE sha256=?",
                                (str(restored), restored.stat().st_size,
                                 datetime.now().isoformat(timespec="seconds"),
                                 digest),
                            )
                            con.commit()
                            queue_ready = ensure_queued(restored)
                            if queue_ready:
                                log("       re-archived lost duplicate "
                                    "from source")
                            else:
                                log(f"  !! queue name conflict for "
                                    f"{restored.name}: not queued")
                if PURGE and src not in purgeable:
                    log("       kept: only recordings on a removable drive are purged")
                elif PURGE:
                    if verified and queue_ready:
                        src.unlink(missing_ok=True)
                        log("       purged duplicate after archive verification")
                    else:
                        log("       NOT purged: archive or pending queue is unverified")
                continue

            try:
                destination = archive_from_source(src, digest)
            except OSError as exc:
                log(f"  SKIP {src.name}: copy failed ({exc})")
                continue
            if destination is None:
                continue
            con.execute(
                "INSERT INTO seen (sha256, orig_name, archived_to, bytes, imported_at) "
                "VALUES (?,?,?,?,?)",
                (digest, src.name, str(destination), destination.stat().st_size,
                 datetime.now().isoformat(timespec="seconds")),
            )
            con.commit()
            if not ensure_queued(destination):
                log(f"  !! queue name conflict for {destination.name}: "
                    "not queued, source kept")
                continue
            imported += 1
            if PURGE and src not in purgeable:
                log("       kept: only recordings on a removable drive are purged")
            elif PURGE:
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
                       detected_files=len(candidates) + len(watched),
                       imported_files=imported,
                       total_files=queued, files_completed=0)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
