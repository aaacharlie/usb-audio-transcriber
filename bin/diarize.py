"""Optional speaker labels ("who said what") using pyannote.audio.

pyannote is a separate, heavy install (it brings PyTorch), so everything here
is imported lazily and the transcriber keeps working without it.
"""
import importlib.util
import subprocess
import tempfile
from pathlib import Path

DEFAULT_MODEL = "pyannote/speaker-diarization-3.1"


def available():
    """Return whether pyannote.audio can be imported in this environment."""
    try:
        return importlib.util.find_spec("pyannote.audio") is not None
    except (ImportError, ValueError):
        return False


def load_pipeline(model_id, token):
    """Load a gated pyannote pipeline; works with both token keyword spellings."""
    from pyannote.audio import Pipeline
    try:
        pipeline = Pipeline.from_pretrained(model_id, token=token)
    except TypeError:
        pipeline = Pipeline.from_pretrained(model_id, use_auth_token=token)
    if pipeline is None:
        raise RuntimeError(
            f"could not load {model_id}: accept its terms on Hugging Face and "
            "check HF_TOKEN"
        )
    return pipeline


def decode_to_wav(audio, target):
    """Decode any input to 16 kHz mono WAV so pyannote does not need extra codecs."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(audio),
         "-ac", "1", "-ar", "16000", "-f", "wav", str(target)],
        check=True,
    )


def diarize(pipeline, audio, min_speakers=None, max_speakers=None):
    """Return speaker turns as dicts with start, end, and speaker, sorted by start."""
    kwargs = {}
    if min_speakers:
        kwargs["min_speakers"] = int(min_speakers)
    if max_speakers:
        kwargs["max_speakers"] = int(max_speakers)
    with tempfile.TemporaryDirectory() as directory:
        wav = Path(directory) / "audio.wav"
        decode_to_wav(audio, wav)
        annotation = pipeline(str(wav), **kwargs)
    turns = [
        {"start": float(turn.start), "end": float(turn.end), "speaker": str(label)}
        for turn, _track, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda turn: turn["start"])
    return turns


def _distance(turn, moment):
    if turn["start"] <= moment <= turn["end"]:
        return 0.0
    return min(abs(turn["start"] - moment), abs(turn["end"] - moment))


def assign_speakers(segments, turns, tolerance=2.0):
    """Label each transcript segment with the speaker who overlaps it most.

    A segment nobody overlaps takes the nearest turn within `tolerance` seconds;
    otherwise it stays unlabelled rather than guessing.
    """
    labelled = []
    for segment in segments:
        best, best_overlap = None, 0.0
        for turn in turns:
            overlap = (min(segment["end"], turn["end"])
                       - max(segment["start"], turn["start"]))
            if overlap > best_overlap:
                best, best_overlap = turn["speaker"], overlap
        if best is None and turns:
            midpoint = (segment["start"] + segment["end"]) / 2
            nearest = min(turns, key=lambda turn: _distance(turn, midpoint))
            if _distance(nearest, midpoint) <= tolerance:
                best = nearest["speaker"]
        entry = dict(segment)
        if best is not None:
            entry["speaker"] = best
        labelled.append(entry)
    return relabel(labelled)


def relabel(segments):
    """Rename SPEAKER_00-style ids to Speaker 1, 2, ... in order of first appearance."""
    names = {}
    for segment in segments:
        raw = segment.get("speaker")
        if raw is None:
            continue
        if raw not in names:
            names[raw] = f"Speaker {len(names) + 1}"
        segment["speaker"] = names[raw]
    return segments


def speaker_names(segments):
    """Distinct speaker labels in order of first appearance."""
    names = []
    for segment in segments:
        speaker = segment.get("speaker")
        if speaker and speaker not in names:
            names.append(speaker)
    return names


def plain_text(segments):
    """Join segment text; with labels, one line per speaker turn."""
    if not any(segment.get("speaker") for segment in segments):
        return " ".join(segment["text"].strip() for segment in segments)
    lines, current, buffer = [], None, []

    def flush():
        if buffer:
            prefix = f"{current}: " if current else ""
            lines.append(prefix + " ".join(buffer))

    for segment in segments:
        speaker = segment.get("speaker")
        if speaker != current:
            flush()
            current, buffer = speaker, []
        buffer.append(segment["text"].strip())
    flush()
    return "\n".join(lines)
