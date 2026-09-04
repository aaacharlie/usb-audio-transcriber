import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config


def load_ingest(config):
    spec = importlib.util.spec_from_file_location(
        "ingest_for_test", ROOT / "bin" / "ingest.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class DuplicatePurgeTests(unittest.TestCase):
    def test_duplicate_source_is_preserved_when_archived_copy_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "1",
            }
            ingest = load_ingest(config)
            digest = ingest.sha256(source)
            with closing(ingest.init_db()) as connection:
                connection.execute(
                    "INSERT INTO seen "
                    "(sha256, orig_name, archived_to, bytes, imported_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (digest, source.name, str(root / "missing.wav"),
                     source.stat().st_size, "2026-09-02T00:00:00"),
                )
                connection.commit()
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            self.assertTrue(source.exists())

    def test_duplicate_source_is_purged_after_archived_copy_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            archived = root / "archive" / "meeting.wav"
            archived.parent.mkdir()
            archived.write_bytes(source.read_bytes())
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "1",
            }
            ingest = load_ingest(config)
            digest = ingest.sha256(source)
            with closing(ingest.init_db()) as connection:
                connection.execute(
                    "INSERT INTO seen "
                    "(sha256, orig_name, archived_to, bytes, imported_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (digest, source.name, str(archived), source.stat().st_size,
                     "2026-09-02T00:00:00"),
                )
                connection.commit()
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            self.assertFalse(source.exists())

    def test_untranscribed_duplicate_is_requeued_before_source_is_purged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "1",
            }
            ingest = load_ingest(config)
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch.object(
                        ingest.os, "symlink", side_effect=OSError("disk full")
                    ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    ingest.main()

            self.assertTrue(source.exists())
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            queued = list((root / "queue").iterdir())
            self.assertEqual(len(queued), 1)
            self.assertTrue(queued[0].is_symlink())
            self.assertFalse(source.exists())

    def test_untranscribed_duplicate_is_requeued_when_purge_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            archived = root / "archive" / "meeting.wav"
            archived.parent.mkdir()
            archived.write_bytes(source.read_bytes())
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            }
            ingest = load_ingest(config)
            digest = ingest.sha256(source)
            with closing(ingest.init_db()) as connection:
                connection.execute(
                    "INSERT INTO seen "
                    "(sha256, orig_name, archived_to, bytes, imported_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (digest, source.name, str(archived), source.stat().st_size,
                     "2026-09-02T00:00:00"),
                )
                connection.commit()
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            self.assertTrue(source.exists())
            self.assertEqual(len(list((root / "queue").iterdir())), 1)


class DuplicateRepairTests(unittest.TestCase):
    def test_untranscribed_duplicate_with_lost_archive_is_rearchived_and_requeued(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            }
            ingest = load_ingest(config)
            digest = ingest.sha256(source)
            with closing(ingest.init_db()) as connection:
                connection.execute(
                    "INSERT INTO seen "
                    "(sha256, orig_name, archived_to, bytes, imported_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (digest, source.name, str(root / "archive" / "missing.wav"),
                     source.stat().st_size, "2026-09-02T00:00:00"),
                )
                connection.commit()
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            self.assertTrue(source.exists())
            rearchived = list((root / "archive").rglob("*.wav"))
            self.assertEqual(len(rearchived), 1)
            self.assertEqual(ingest.sha256(rearchived[0]), digest)
            self.assertEqual(os.stat(rearchived[0]).st_mode & 0o777, 0o600)
            queued = list((root / "queue").iterdir())
            self.assertEqual(len(queued), 1)
            self.assertTrue(queued[0].is_symlink())
            self.assertEqual(queued[0].resolve(), rearchived[0].resolve())
            with closing(ingest.init_db()) as connection:
                row = connection.execute(
                    "SELECT archived_to, bytes, imported_at, transcribed "
                    "FROM seen WHERE sha256=?", (digest,)
                ).fetchone()
            self.assertEqual(row[0], str(rearchived[0]))
            self.assertEqual(row[1], rearchived[0].stat().st_size)
            self.assertNotEqual(row[2], "2026-09-02T00:00:00")
            self.assertEqual(row[3], 0)


class SensitiveFileModeTests(unittest.TestCase):
    def test_import_restricts_archive_and_database_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            source.chmod(0o777)
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            }
            ingest = load_ingest(config)
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            archived = next((root / "archive").rglob("*.wav"))
            database = root / "state" / "seen.sqlite"
            self.assertEqual(os.stat(archived).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(database).st_mode & 0o777, 0o600)

    def test_purge_never_runs_when_archive_durability_check_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "1",
            }
            ingest = load_ingest(config)
            with mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch.object(
                        ingest.os, "fsync", side_effect=OSError("sync failed")
                    ):
                with self.assertRaisesRegex(OSError, "sync failed"):
                    ingest.main()

            self.assertTrue(source.exists())


class QueueConflictTests(unittest.TestCase):
    def test_stale_dangling_queue_entry_is_replaced_on_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            ingest = load_ingest({
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            })
            queue = root / "queue"
            queue.mkdir()
            with mock.patch.object(ingest, "archive_path_for") as naming:
                naming.side_effect = lambda src: (
                    root / "archive" / f"20260903-000000_{src.name}"
                )
                (root / "archive").mkdir()
                stale = queue / "20260903-000000_meeting.wav"
                stale.symlink_to(root / "archive" / "gone.wav")
                with mock.patch.object(
                            ingest, "find_candidates", return_value=[source]
                        ), \
                        mock.patch.object(ingest, "stable", return_value=True):
                    self.assertEqual(ingest.main(), 0)

            self.assertTrue(stale.is_symlink())
            self.assertEqual(
                stale.resolve(),
                root / "archive" / "20260903-000000_meeting.wav",
            )


class DuplicateEfficiencyTests(unittest.TestCase):
    def test_transcribed_duplicate_is_not_rehashed_when_purge_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            archived = root / "archive" / "meeting.wav"
            archived.parent.mkdir()
            archived.write_bytes(source.read_bytes())
            config = {
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            }
            ingest = load_ingest(config)
            digest = ingest.sha256(source)
            with closing(ingest.init_db()) as connection:
                connection.execute(
                    "INSERT INTO seen "
                    "(sha256, orig_name, archived_to, bytes, imported_at, transcribed) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (digest, source.name, str(archived), source.stat().st_size,
                     "2026-09-02T00:00:00"),
                )
                connection.commit()
            hashed = []
            real_sha256 = ingest.sha256

            def counting_sha256(path, *args, **kwargs):
                hashed.append(Path(path))
                return real_sha256(path, *args, **kwargs)

            with mock.patch.object(ingest, "sha256", counting_sha256), \
                    mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True):
                self.assertEqual(ingest.main(), 0)

            self.assertEqual(hashed, [source])
            self.assertTrue(source.exists())


class ConnectionLifetimeTests(unittest.TestCase):
    def tracking_connect(self, connections):
        real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        return connect

    def assert_all_closed(self, connections):
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_state_database_is_closed_when_ingest_fails_midway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "media" / "RECORD" / "meeting.wav"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"recording" * 1024)
            ingest = load_ingest({
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
                "PURGE_DEVICE": "0",
            })
            connections = []
            with mock.patch.object(
                        ingest.sqlite3, "connect",
                        self.tracking_connect(connections)
                    ), \
                    mock.patch.object(ingest, "find_candidates", return_value=[source]), \
                    mock.patch.object(ingest, "stable", return_value=True), \
                    mock.patch.object(
                        ingest.os, "symlink", side_effect=OSError("disk full")
                    ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    ingest.main()

            self.assertEqual(len(connections), 1)
            self.assert_all_closed(connections)

    def test_init_db_closes_connection_when_setup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ingest = load_ingest({
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
            })
            connections = []
            with mock.patch.object(
                        ingest.sqlite3, "connect",
                        self.tracking_connect(connections)
                    ), \
                    mock.patch.object(
                        type(ingest.STATE_DB), "chmod",
                        side_effect=OSError("chmod denied"),
                    ):
                with self.assertRaisesRegex(OSError, "chmod denied"):
                    ingest.init_db()

            self.assertEqual(len(connections), 1)
            self.assert_all_closed(connections)


class DiscoverySafetyTests(unittest.TestCase):
    def test_discovery_rejects_audio_symlinks_that_escape_the_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "private.wav"
            outside.write_bytes(b"private recording" * 1024)
            mount_root = root / "media"
            recorder = mount_root / "USB" / "RECORD"
            recorder.mkdir(parents=True)
            (recorder / "linked.wav").symlink_to(outside)
            ingest = load_ingest({
                "ARCHIVE_DIR": str(root / "archive"),
                "QUEUE_DIR": str(root / "queue"),
                "STATE_DB": str(root / "state" / "seen.sqlite"),
                "AUDIO_EXTS": "wav",
                "RECORDER_DIR": "RECORD",
            })
            ingest.MOUNT_ROOTS = [mount_root]

            self.assertEqual(ingest.find_candidates(), [])


if __name__ == "__main__":
    unittest.main()
