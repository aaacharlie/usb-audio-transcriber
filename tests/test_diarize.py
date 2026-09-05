import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import diarize
import pipeline_config


def load_transcriber(config):
    spec = importlib.util.spec_from_file_location(
        "transcribe_for_diarize_test", ROOT / "bin" / "transcribe.py"
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(pipeline_config, "load", return_value=config):
        spec.loader.exec_module(module)
    return module


class AssignmentTests(unittest.TestCase):
    def test_segments_take_the_speaker_who_overlaps_them_most(self):
        segments = [
            {"start": 0.0, "end": 5.0, "text": " Hi."},
            {"start": 5.0, "end": 10.0, "text": " Hello."},
            {"start": 10.0, "end": 12.0, "text": " Bye."},
        ]
        turns = [
            {"start": 0.0, "end": 6.0, "speaker": "SPEAKER_01"},
            {"start": 6.0, "end": 12.0, "speaker": "SPEAKER_00"},
        ]

        labelled = diarize.assign_speakers(segments, turns)

        self.assertEqual([s["speaker"] for s in labelled],
                         ["Speaker 1", "Speaker 2", "Speaker 2"])
        self.assertEqual(segments[0].get("speaker"), None, "input must not be mutated")

    def test_unoverlapped_segments_use_the_nearest_turn_within_tolerance(self):
        turns = [{"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"}]
        near = diarize.assign_speakers([{"start": 5.0, "end": 6.0, "text": "x"}], turns)
        far = diarize.assign_speakers([{"start": 30.0, "end": 31.0, "text": "x"}], turns)

        self.assertEqual(near[0]["speaker"], "Speaker 1")
        self.assertNotIn("speaker", far[0])

    def test_no_turns_leaves_segments_unlabelled(self):
        labelled = diarize.assign_speakers([{"start": 0.0, "end": 1.0, "text": "x"}], [])

        self.assertNotIn("speaker", labelled[0])

    def test_plain_text_groups_consecutive_turns_per_speaker(self):
        segments = [
            {"start": 0, "end": 1, "text": " One.", "speaker": "Speaker 1"},
            {"start": 1, "end": 2, "text": " Two.", "speaker": "Speaker 1"},
            {"start": 2, "end": 3, "text": " Three.", "speaker": "Speaker 2"},
        ]

        self.assertEqual(diarize.plain_text(segments),
                         "Speaker 1: One. Two.\nSpeaker 2: Three.")
        self.assertEqual(
            diarize.plain_text([{"start": 0, "end": 1, "text": " Plain."}]), "Plain."
        )

    def test_decode_uses_mono_16k_wav(self):
        with mock.patch.object(diarize.subprocess, "run") as run:
            diarize.decode_to_wav(Path("/a/rec.mp3"), Path("/tmp/x.wav"))

        command = run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertTrue(run.call_args.kwargs["check"])

    def test_diarize_collects_sorted_turns_from_the_pipeline(self):
        class Segment:
            def __init__(self, start, end):
                self.start, self.end = start, end

        class Annotation:
            def itertracks(self, yield_label=False):
                yield Segment(5.0, 9.0), "t2", "SPEAKER_01"
                yield Segment(0.0, 4.0), "t1", "SPEAKER_00"

        calls = []

        def pipeline(path, **kwargs):
            calls.append((path, kwargs))
            return Annotation()

        with mock.patch.object(diarize, "decode_to_wav"):
            turns = diarize.diarize(pipeline, Path("/a/rec.wav"), "2", "")

        self.assertEqual([t["speaker"] for t in turns], ["SPEAKER_00", "SPEAKER_01"])
        self.assertEqual(calls[0][1], {"min_speakers": 2})
        self.assertTrue(calls[0][0].endswith("audio.wav"))


class TranscriberIntegrationTests(unittest.TestCase):
    def prepare(self, root, **extra):
        queue = root / "queue"
        queue.mkdir()
        audio = queue / "meeting.wav"
        audio.touch()
        state_db = root / "state.sqlite"
        with closing(sqlite3.connect(state_db)) as connection:
            connection.execute("CREATE TABLE seen (archived_to TEXT, transcribed INTEGER)")
            connection.execute("INSERT INTO seen VALUES (?, 0)", (str(audio),))
            connection.commit()
        module = load_transcriber({
            "QUEUE_DIR": str(queue),
            "VAULT_DIR": str(root / "vault"),
            "STATE_DB": str(state_db),
            "AUDIO_EXTS": "wav",
            "WHISPER_MODEL_PROFILE": "fast",
            "DIARIZATION": "1",
            "HF_TOKEN": "hf_test",
            **extra,
        })

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeModel:
            def __init__(self, model_id, **kwargs):
                pass

            def transcribe(self, path, **kwargs):
                return (iter([FakeSegment(0, 4, " Hello."), FakeSegment(5, 9, " Hi.")]),
                        types.SimpleNamespace(duration=10))

        return module, queue, types.SimpleNamespace(WhisperModel=FakeModel)

    def test_speaker_labels_reach_the_note_sidecar_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, queue, fake_whisper = self.prepare(root)
            turns = [{"start": 0.0, "end": 4.5, "speaker": "SPEAKER_00"},
                     {"start": 4.5, "end": 10.0, "speaker": "SPEAKER_01"}]
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_whisper}), \
                    mock.patch.object(module, "write_progress"), \
                    mock.patch.object(module.notify, "send"), \
                    mock.patch.object(module.diarize, "load_pipeline", return_value="pipe") as load, \
                    mock.patch.object(module.diarize, "diarize", return_value=turns) as run:
                self.assertEqual(module.main(), 0)

            load.assert_called_once_with("pyannote/speaker-diarization-3.1", "hf_test")
            self.assertEqual(run.call_args.args[0], "pipe")
            note = next((root / "vault").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("**[0:00:00] Speaker 1:** Hello.", note)
            self.assertIn("**[0:00:05] Speaker 2:** Hi.", note)
            self.assertIn("speakers: 2", note)
            sidecar = json.loads((queue / "meeting.wav.json").read_text(encoding="utf-8"))
            self.assertEqual([s["speaker"] for s in sidecar["segments"]],
                             ["Speaker 1", "Speaker 2"])
            self.assertEqual((queue / "meeting.wav.txt").read_text(encoding="utf-8"),
                             "Speaker 1: Hello.\nSpeaker 2: Hi.")

    def test_diarization_failure_still_completes_the_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, queue, fake_whisper = self.prepare(root)
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_whisper}), \
                    mock.patch.object(module, "write_progress"), \
                    mock.patch.object(module.notify, "send"), \
                    mock.patch.object(module.diarize, "load_pipeline",
                                      side_effect=RuntimeError("terms not accepted")):
                self.assertEqual(module.main(), 0)

            note = next((root / "vault").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("**[0:00:00]** Hello.", note)
            self.assertNotIn("Speaker", note)
            self.assertTrue((queue / "meeting.wav.complete.json").exists())

    def test_diarization_is_not_attempted_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, queue, fake_whisper = self.prepare(root, DIARIZATION="0")
            with mock.patch.dict(sys.modules, {"faster_whisper": fake_whisper}), \
                    mock.patch.object(module, "write_progress"), \
                    mock.patch.object(module.notify, "send"), \
                    mock.patch.object(module.diarize, "load_pipeline") as load:
                self.assertEqual(module.main(), 0)

            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
