import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import pipeline_config

DAY = datetime(2026, 9, 5)


def at(hour, minute):
    return DAY.replace(hour=hour, minute=minute)


def load_sessions(config):
    spec = importlib.util.spec_from_file_location(
        "sessions_for_test", ROOT / "bin" / "sessions.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class Fixture:
    """A fake archive, vault, queue, and state database."""

    def __init__(self, root, **extra):
        self.archive = root / "archive"
        self.vault = root / "vault"
        self.queue = root / "queue"
        self.state = root / "state.sqlite"
        for directory in (self.archive, self.vault, self.queue):
            directory.mkdir()
        self.config = {
            "ARCHIVE_DIR": str(self.archive),
            "QUEUE_DIR": str(self.queue),
            "VAULT_DIR": str(self.vault),
            "STATE_DB": str(self.state),
            "AUDIO_EXTS": "wav",
            "WHISPER_MODEL_PROFILE": "fast",
            "SESSION_BACKFILL_DAYS": "",  # tests use fixed dates; guard tested separately
            **extra,
        }
        with closing(sqlite3.connect(self.state)) as con:
            con.execute(
                "CREATE TABLE seen (sha256 TEXT PRIMARY KEY, orig_name TEXT, "
                "archived_to TEXT, bytes INTEGER, imported_at TEXT, "
                "transcribed INTEGER DEFAULT 0)"
            )
            con.commit()
        self.counter = 0

    def recording(self, end, minutes, complete=True, queued=False, **finish):
        """Archive one recording that ended at `end` and lasted `minutes`."""
        self.counter += 1
        digest = f"{self.counter:064x}"
        audio = self.archive / f"{end:%Y%m%d-%H%M%S}_rec{self.counter}.wav"
        audio.write_bytes(b"audio" * 1024)
        os.utime(audio, (end.timestamp(), end.timestamp()))
        with closing(sqlite3.connect(self.state)) as con:
            con.execute(
                "INSERT INTO seen VALUES (?,?,?,?,?,?)",
                (digest, audio.name, str(audio), 5120, "2026-09-05T00:00:00",
                 1 if complete else 0),
            )
            con.commit()
        if queued:
            (self.queue / audio.name).symlink_to(audio)
        if complete:
            self.finish(audio, end, minutes, **finish)
        return audio

    def finish(self, audio, end, minutes, text="Hello there.", profile="fast",
               comparison=False, status="complete", speaker=None):
        """Write the sidecars, note, and completion marker for one pass."""
        suffix = f".{profile}" if comparison else ""
        duration = minutes * 60
        segments = [] if status == "no_speech" else [
            {"start": 0.0, "end": 5.0, "text": f" {text}"},
            {"start": 10.0, "end": float(duration), "text": " More."},
        ]
        if speaker:
            for segment in segments:
                segment["speaker"] = speaker
        (self.archive / f"{audio.name}{suffix}.json").write_text(json.dumps({
            "duration": duration, "model": "m", "profile": profile,
            "status": status, "segments": segments,
        }), encoding="utf-8")
        (self.archive / f"{audio.name}{suffix}.txt").write_text(text, encoding="utf-8")
        label = f" {profile}" if comparison else ""
        note = self.vault / f"{end:%Y-%m-%d} {end:%H%M} transcript{label}.md"
        note.write_text("# note", encoding="utf-8")
        (self.archive / f"{audio.name}{suffix}.complete.json").write_text(
            json.dumps({"status": status, "note": str(note)}), encoding="utf-8"
        )
        os.utime(audio, (end.timestamp(), end.timestamp()))

    def run(self, module, command="auto", **kwargs):
        with closing(sqlite3.connect(self.state)) as con:
            module.init_db(con)
            with mock.patch.object(module.notify, "send") as send:
                result = getattr(module, command)(con, **kwargs)
            self.notifications = send.call_args_list
        return result

    def rows(self):
        with closing(sqlite3.connect(self.state)) as con:
            return con.execute(
                "SELECT id, members, note, summarized FROM sessions ORDER BY started_at"
            ).fetchall()


class GroupingTests(unittest.TestCase):
    def test_recordings_split_into_sessions_at_long_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)   # 09:00 to 09:30
            fixture.recording(at(9, 50), 15)   # 09:35 to 09:50
            fixture.recording(at(10, 10), 15)  # 09:55 to 10:10
            fixture.recording(at(14, 30), 30)  # 14:00 to 14:30
            sessions = load_sessions(fixture.config)

            written = fixture.run(sessions)

            self.assertEqual(
                sorted(note.name for note in written),
                ["2026-09-05 0900 session.md", "2026-09-05 1400 session.md"],
            )
            first = (fixture.vault / "2026-09-05 0900 session.md").read_text(encoding="utf-8")
            self.assertIn("recordings: 3", first)
            self.assertIn("# Session - Saturday, September 05 2026, 09:00 to 10:10", first)
            self.assertLess(
                first.index("[[2026-09-05 0930 transcript]]"),
                first.index("[[2026-09-05 0950 transcript]]"),
            )
            self.assertIn("[[2026-09-05 1010 transcript]]", first)
            self.assertNotIn("1430 transcript", first)
            self.assertIn("## Combined transcript", first)
            self.assertIn("**[0:00:00]** Hello there.", first)
            self.assertIn("OPENROUTER_API_KEY is not set", first)
            self.assertEqual(
                os.stat(fixture.vault / "2026-09-05 0900 session.md").st_mode & 0o777,
                0o600,
            )
            self.assertEqual(len(fixture.notifications), 2)
            self.assertEqual(fixture.notifications[0].args[0], "Session note ready")
            self.assertEqual([row[3] for row in fixture.rows()], [0, 0])

    def test_sessions_are_not_regrouped_once_written(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            fixture.recording(at(9, 50), 15)
            sessions = load_sessions(fixture.config)
            self.assertEqual(len(fixture.run(sessions)), 1)

            self.assertEqual(fixture.run(sessions), [])

            self.assertEqual(len(list(fixture.vault.glob("*session*.md"))), 1)
            self.assertEqual(len(fixture.rows()), 1)

    def test_a_late_recording_becomes_its_own_session(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            sessions = load_sessions(fixture.config)
            fixture.run(sessions)
            fixture.recording(at(9, 45), 10)  # would have joined, but the session is closed

            written = fixture.run(sessions)

            self.assertEqual([note.name for note in written], ["2026-09-05 0935 session.md"])
            self.assertEqual(len(fixture.rows()), 2)

    def test_a_session_waits_while_one_recording_is_still_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            pending = fixture.recording(at(9, 50), 15, complete=False, queued=True)
            fixture.recording(at(14, 30), 30)
            sessions = load_sessions(fixture.config)

            written = fixture.run(sessions)
            self.assertEqual([note.name for note in written], ["2026-09-05 1400 session.md"])

            fixture.finish(pending, at(9, 50), 15)
            (fixture.queue / pending.name).unlink()
            written = fixture.run(sessions)

            self.assertEqual([note.name for note in written], ["2026-09-05 0900 session.md"])
            self.assertIn(
                "recordings: 2",
                (fixture.vault / "2026-09-05 0900 session.md").read_text(encoding="utf-8"),
            )

    def test_comparison_runs_use_the_accurate_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), WHISPER_MODEL_PROFILE="both")
            audio = fixture.recording(at(9, 30), 30, complete=False)
            fixture.finish(audio, at(9, 30), 30, text="fast words", profile="fast",
                           comparison=True)
            sessions = load_sessions(fixture.config)

            self.assertEqual(fixture.run(sessions), [], "accurate pass still missing")

            fixture.finish(audio, at(9, 30), 30, text="accurate words",
                           profile="accurate", comparison=True)
            written = fixture.run(sessions)

            body = written[0].read_text(encoding="utf-8")
            self.assertIn("accurate words", body)
            self.assertNotIn("fast words", body)
            self.assertIn("[[2026-09-05 0930 transcript accurate]]", body)

    def test_speaker_labels_are_kept_in_the_combined_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key")
            fixture.recording(at(9, 30), 30, speaker="Speaker 1")
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm", return_value="## Executive summary\nOk.") as llm:
                written = fixture.run(sessions)

            self.assertIn("**[0:00:00] Speaker 1:** Hello there.",
                          written[0].read_text(encoding="utf-8"))
            self.assertIn("[0:00:00] Speaker 1: Hello there.", llm.call_args.args[0])

    def test_no_speech_recordings_are_listed_without_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30, status="no_speech")
            sessions = load_sessions(fixture.config)

            written = fixture.run(sessions)

            body = written[0].read_text(encoding="utf-8")
            self.assertIn("No speech was detected", body)
            self.assertIn("speech_min: 0", body)


class SummaryTests(unittest.TestCase):
    def test_summary_is_requested_with_subject_and_transcripts(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(
                Path(directory), OPENROUTER_API_KEY="key", SESSION_SUBJECT="real estate",
                SESSION_SUMMARY_MODEL="big/model",
            )
            fixture.recording(at(9, 30), 30, text="Rent goes up.")
            fixture.recording(at(9, 50), 15, text="Roof leaks.")
            sessions = load_sessions(fixture.config)
            with mock.patch.object(
                sessions, "call_llm", return_value="## Executive summary\nGreat meeting."
            ) as llm:
                written = fixture.run(sessions)

            body = written[0].read_text(encoding="utf-8")
            self.assertIn("Great meeting.", body)
            self.assertIn("summary_model: big/model", body)
            llm.assert_called_once()
            prompt = llm.call_args.args[0]
            self.assertIn("very knowledgeable in real estate", prompt)
            self.assertLess(prompt.index("Rent goes up."), prompt.index("Roof leaks."))
            self.assertIn("=== Recording 1: 2026-09-05 09:00 to 09:30", prompt)
            self.assertEqual(llm.call_args.args[1:3], ("key", "big/model"))
            self.assertEqual([row[3] for row in fixture.rows()], [1])

    def test_summary_failure_keeps_the_note(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key")
            fixture.recording(at(9, 30), 30)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm", side_effect=RuntimeError("quota")):
                written = fixture.run(sessions)

            body = written[0].read_text(encoding="utf-8")
            self.assertIn("AI summary failed (quota)", body)
            self.assertIn("## Combined transcript", body)
            self.assertEqual([row[3] for row in fixture.rows()], [0])

    def test_retry_adds_the_missing_summary_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            fixture.run(load_sessions(fixture.config))
            with_key = load_sessions(fixture.config | {"OPENROUTER_API_KEY": "key"})
            with mock.patch.object(with_key, "call_llm", return_value="## Executive summary\nDone."):
                written = fixture.run(with_key, "retry")

            self.assertEqual([note.name for note in written], ["2026-09-05 0900 session.md"])
            self.assertEqual(len(list(fixture.vault.glob("*session*.md"))), 1)
            self.assertIn("Done.", written[0].read_text(encoding="utf-8"))
            self.assertEqual([row[3] for row in fixture.rows()], [1])

    def test_rebuild_regenerates_one_day(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            fixture.recording(datetime(2026, 9, 6, 9, 30), 30)
            sessions = load_sessions(fixture.config)
            fixture.run(sessions)
            before = {row[0] for row in fixture.rows()}

            written = fixture.run(sessions, "rebuild", date="2026-09-05")

            self.assertEqual([note.name for note in written], ["2026-09-05 0900 session.md"])
            self.assertEqual({row[0] for row in fixture.rows()}, before)
            self.assertEqual(len(list(fixture.vault.glob("*session*.md"))), 2)

    def test_long_sessions_are_summarized_in_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key", MAP_WINDOW_CHARS="120")
            fixture.recording(at(9, 30), 30, text="Sentence one. " * 20)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm", return_value="notes") as llm:
                fixture.run(sessions)

            self.assertGreater(llm.call_count, 2)
            self.assertIn("PART NOTES", llm.call_args.args[0])

    def test_summaries_can_be_switched_off_with_a_key_present(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key", SESSION_SUMMARY="0")
            fixture.recording(at(9, 30), 30)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm") as llm:
                written = fixture.run(sessions)

            llm.assert_not_called()
            self.assertIn("turned off", written[0].read_text(encoding="utf-8"))


class BackfillTests(unittest.TestCase):
    def test_old_sessions_get_notes_without_an_automatic_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key",
                              SESSION_BACKFILL_DAYS="7")
            fixture.recording(datetime(2026, 8, 1, 9, 30), 30)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm") as llm:
                written = fixture.run(sessions)

            llm.assert_not_called()
            body = written[0].read_text(encoding="utf-8")
            self.assertIn("AI summary skipped", body)
            self.assertIn("## Combined transcript", body)
            self.assertEqual([row[3] for row in fixture.rows()], [0])

            with mock.patch.object(sessions, "call_llm", return_value="## Executive summary\nLate.") as llm:
                retried = fixture.run(sessions, "retry")

            llm.assert_called_once()
            self.assertIn("Late.", retried[0].read_text(encoding="utf-8"))
            self.assertEqual([row[3] for row in fixture.rows()], [1])

    def test_recent_sessions_are_still_summarized_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), OPENROUTER_API_KEY="key",
                              SESSION_BACKFILL_DAYS="7")
            recent = (datetime.now() - timedelta(days=1)).replace(
                hour=9, minute=30, second=0, microsecond=0)
            fixture.recording(recent, 30)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions, "call_llm", return_value="## Executive summary\nFresh.") as llm:
                written = fixture.run(sessions)

            llm.assert_called_once()
            self.assertIn("Fresh.", written[0].read_text(encoding="utf-8"))

    def test_many_notes_in_one_cycle_send_a_single_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            for hour in (8, 10, 12, 14, 16):
                fixture.recording(at(hour, 30), 30)
            sessions = load_sessions(fixture.config)

            written = fixture.run(sessions)

            self.assertEqual(len(written), 5)
            self.assertEqual(len(fixture.notifications), 1)
            self.assertEqual(fixture.notifications[0].args[0], "Session notes written")
            self.assertIn("5 session notes", fixture.notifications[0].args[1])
            self.assertEqual(fixture.notifications[0].kwargs["open_path"], fixture.vault)


class CliTests(unittest.TestCase):
    def test_auto_is_skipped_when_session_notes_are_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), SESSION_NOTES="0")
            fixture.recording(at(9, 30), 30)
            sessions = load_sessions(fixture.config)

            self.assertEqual(sessions.main([]), 0)

            self.assertEqual(list(fixture.vault.glob("*session*.md")), [])

    def test_list_prints_written_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            fixture.recording(at(9, 30), 30)
            sessions = load_sessions(fixture.config)
            with mock.patch.object(sessions.notify, "send"):
                self.assertEqual(sessions.main(["auto"]), 0)
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                self.assertEqual(sessions.main(["list"]), 0)

            self.assertIn("1 recording(s)  summary: no  2026-09-05 0900 session.md",
                          output.getvalue())

    def test_rebuild_requires_a_date(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory))
            sessions = load_sessions(fixture.config)
            with mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    sessions.main(["rebuild"])


if __name__ == "__main__":
    unittest.main()
