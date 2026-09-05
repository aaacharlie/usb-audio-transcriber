import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import doctor
import pipeline_config

DAY = datetime(2026, 9, 5)


def at(hour, minute):
    return DAY.replace(hour=hour, minute=minute)


def load_search(config):
    spec = importlib.util.spec_from_file_location("search_for_test", ROOT / "bin" / "search.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class Fixture:
    """A fake archive, vault, and state database with completed recordings."""

    def __init__(self, root, **extra):
        self.archive = root / "archive"
        self.vault = root / "vault"
        self.state = root / "state.sqlite"
        self.archive.mkdir()
        self.vault.mkdir()
        self.config = {
            "ARCHIVE_DIR": str(self.archive),
            "QUEUE_DIR": str(root / "queue"),
            "VAULT_DIR": str(self.vault),
            "STATE_DB": str(self.state),
            "AUDIO_EXTS": "wav",
            "WHISPER_MODEL_PROFILE": "fast",
            **extra,
        }
        with closing(sqlite3.connect(self.state)) as con:
            con.execute(
                "CREATE TABLE seen (sha256 TEXT PRIMARY KEY, orig_name TEXT, "
                "archived_to TEXT, bytes INTEGER, imported_at TEXT, transcribed INTEGER)"
            )
            con.commit()
        self.counter = 0

    def recording(self, end, segments, complete=True):
        self.counter += 1
        audio = self.archive / f"{end:%Y%m%d-%H%M%S}_rec{self.counter}.wav"
        audio.write_bytes(b"audio" * 1024)
        with closing(sqlite3.connect(self.state)) as con:
            con.execute("INSERT INTO seen VALUES (?,?,?,?,?,?)",
                        (f"{self.counter:064x}", audio.name, str(audio), 5120, "x", 1))
            con.commit()
        if complete:
            self.finish(audio, end, segments)
        return audio

    def finish(self, audio, end, segments):
        note = self.vault / f"{end:%Y-%m-%d} {end:%H%M} transcript.md"
        note.write_text("# note", encoding="utf-8")
        (self.archive / f"{audio.name}.json").write_text(json.dumps({
            "duration": 600, "model": "m", "profile": "fast", "status": "complete",
            "segments": segments,
        }), encoding="utf-8")
        (self.archive / f"{audio.name}.txt").write_text("text", encoding="utf-8")
        (self.archive / f"{audio.name}.complete.json").write_text(
            json.dumps({"status": "complete", "note": str(note)}), encoding="utf-8"
        )
        os.utime(audio, (end.timestamp(), end.timestamp()))
        return note

    def connect(self):
        return sqlite3.connect(self.state)


def seg(start, text, speaker=None):
    segment = {"start": float(start), "end": float(start) + 4.0, "text": f" {text}"}
    if speaker:
        segment["speaker"] = speaker
    return segment


class IndexTests(unittest.TestCase):
    def test_refresh_indexes_completed_recordings_and_tracks_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            done = fixture.recording(at(9, 30), [seg(12, "The roof leak is back.")])
            fixture.recording(at(10, 0), [], complete=False)
            search = load_search(fixture.config)
            with closing(fixture.connect()) as con:
                self.assertEqual(search.refresh(con), (1, 0))
                self.assertEqual(search.refresh(con), (0, 0), "unchanged sidecars are skipped")

                fixture.finish(done, at(9, 30), [seg(12, "The gutter is fine now.")])
                os.utime(fixture.archive / f"{done.name}.json", (2e9, 2e9))
                self.assertEqual(search.refresh(con), (1, 0))
                self.assertEqual(search.search(con, ["roof"]), [])
                self.assertEqual(len(search.search(con, ["gutter"])), 1)

                done.unlink()
                self.assertEqual(search.refresh(con), (0, 1))
                self.assertEqual(search.search(con, ["gutter"]), [])

    def test_search_orders_newest_first_and_highlights_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), [
                seg(5, "Morning check.", "Speaker 1"),
                seg(723, "We need to fix the roof leak before winter.", "Speaker 2"),
            ])
            fixture.recording(at(14, 0), [seg(60, "The roof leaks again, call the roofer.")])
            search = load_search(fixture.config)
            with closing(fixture.connect()) as con:
                search.refresh(con)
                hits = search.search(con, ["roof", "leak"])

            self.assertEqual(len(hits), 2)
            self.assertEqual(hits[0]["recorded_at"], "2026-09-05T14:00:00")
            self.assertIn("[roof] [leaks]", hits[0]["text"], "stemming matches leaks")
            self.assertEqual(hits[1]["speaker"], "Speaker 2")
            self.assertEqual(hits[1]["start_seconds"], 723.0)
            line = search.format_hit(hits[1])
            self.assertTrue(line.startswith("2026-09-05 0930 transcript  [0:12:03] Speaker 2: "))
            self.assertIn("[roof] [leak]", line)
            self.assertIn("0930 transcript.md", line)

    def test_filters_and_prefix_and_awkward_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), [
                seg(1, "Don't forget the plumber's invoice.", "Speaker 1"),
                seg(9, "The invoice is paid.", "Speaker 2"),
            ])
            fixture.recording(datetime(2026, 8, 1, 9, 0), [seg(1, "Old invoice.", "Speaker 2")])
            search = load_search(fixture.config)
            with closing(fixture.connect()) as con:
                search.refresh(con)
                self.assertEqual(len(search.search(con, ["invoice"])), 3)
                self.assertEqual(len(search.search(con, ["invoice"], since="2026-09-01")), 2)
                by_two = search.search(con, ["invoice"], speaker="Speaker 2")
                self.assertEqual(len(by_two), 2)
                self.assertEqual(len(search.search(con, ["plumb*"])), 1)
                self.assertEqual(len(search.search(con, ["don't"])), 1)
                self.assertEqual(len(search.search(con, ["invoice", "paid"])), 1)
                raw = search.search(con, ["plumber OR paid"], raw=True)
                self.assertEqual(len(raw), 2)

    def test_fts_query_quotes_words_and_keeps_prefixes(self):
        search = load_search({"STATE_DB": "/x", "AUDIO_EXTS": "wav"})
        self.assertEqual(search.fts_query(["roof", "leak"]), '"roof" "leak"')
        self.assertEqual(search.fts_query(["plumb*"]), '"plumb"*')
        self.assertEqual(search.fts_query(['say "hi"']), '"say" """hi"""')
        self.assertEqual(search.fts_query(["a OR b"], raw=True), "a OR b")


class CommandTests(unittest.TestCase):
    def test_index_flag_refreshes_and_search_reports_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), [seg(3, "Deposit returned in full.", "Speaker 1")])
            search = load_search(fixture.config)
            out, err = io.StringIO(), io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                self.assertEqual(search.main(["--index"]), 0)
                self.assertEqual(search.main(["deposit"]), 0)
                self.assertEqual(search.main(["--no-refresh", "unicorn"]), 1)
                self.assertEqual(search.main(["--json", "deposit"]), 0)
                self.assertEqual(search.main(["--raw", "deposit AND ("]), 2)

            self.assertIn("[Deposit] returned in full.", out.getvalue())
            self.assertIn("No matches.", err.getvalue())
            self.assertIn("Search failed", err.getvalue())
            self.assertIn('"speaker": "Speaker 1"', out.getvalue())

    def test_missing_fts5_is_quiet_for_index_and_loud_for_search(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            search = load_search(fixture.config)
            err = io.StringIO()
            with mock.patch.object(search, "fts5_available", return_value=False), \
                    mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", err):
                self.assertEqual(search.main(["--index"]), 0)
                self.assertEqual(search.main(["roof"]), 2)

            self.assertIn("without FTS5", err.getvalue())

    def test_words_are_required_unless_indexing(self):
        search = load_search({"STATE_DB": "/tmp/none.sqlite", "AUDIO_EXTS": "wav"})
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                search.main([])


class DoctorSearchTests(unittest.TestCase):
    def test_fts5_warning_only_when_sqlite_lacks_it(self):
        self.assertIsNone(doctor.fts5_warning())
        with mock.patch.object(doctor.sqlite3, "connect",
                               side_effect=sqlite3.OperationalError("no fts5")):
            self.assertIn("FTS5", doctor.fts5_warning())


if __name__ == "__main__":
    unittest.main()
