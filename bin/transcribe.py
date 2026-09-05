#!/usr/bin/env python3
"""Transcribe queued audio with faster-whisper and write Markdown notes."""
import gc
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diarize
import notify
from llm import call_llm, split_windows
from model_profiles import artifact_path, artifacts_complete, profiles_for_config
from pipeline_config import load, log, sync_directory, write_progress

CFG = load()
QUEUE = Path(CFG["QUEUE_DIR"])
VAULT = Path(CFG["VAULT_DIR"])
STATE_DB = Path(CFG["STATE_DB"])
MODEL_PROFILES = profiles_for_config(CFG)
DEVICE = CFG.get("WHISPER_DEVICE", "cpu")
COMPUTE = CFG.get("WHISPER_COMPUTE", "int8")
LANG = CFG.get("WHISPER_LANG", "en") or None
TASK = CFG.get("WHISPER_TASK", "transcribe") or "transcribe"
VAD = CFG.get("VAD_ENABLED", "1") == "1"
VAD_MS = int(CFG.get("VAD_MIN_SILENCE_MS", "1200"))
OR_KEY = CFG.get("OPENROUTER_API_KEY", "").strip()
OR_MODEL = CFG.get("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")
FILE_SUMMARY = CFG.get("FILE_SUMMARY", "1").strip() == "1"
WINDOW = int(CFG.get("MAP_WINDOW_CHARS", "80000"))
DIARIZE = CFG.get("DIARIZATION", "0").strip() == "1"
HF_TOKEN = CFG.get("HF_TOKEN", "").strip()
DIARIZATION_MODEL = CFG.get("DIARIZATION_MODEL", "").strip() or diarize.DEFAULT_MODEL
DIARIZATION_MIN = CFG.get("DIARIZATION_MIN_SPEAKERS", "").strip()
DIARIZATION_MAX = CFG.get("DIARIZATION_MAX_SPEAKERS", "").strip()
DIARIZER = None
SECTIONS = (
    "## Summary\nA short paragraph.\n\n"
    "## Topics\nBullet list of distinct topics or conversations.\n\n"
    "## Action Items\nBullet list, or 'None identified'. Do not invent commitments.\n\n"
    "## People & Entities\nNames, companies, addresses and dollar figures mentioned."
)


def hhmmss(seconds):
    return str(timedelta(seconds=int(seconds or 0)))


def write_private_text(path, text):
    """Atomically and durably write sensitive text with user-only permissions."""
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.chmod(0o600)
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    sync_directory(path.parent)


def summarize(transcript):
    """Optionally summarize one recording via OpenRouter; no key keeps it local."""
    if not OR_KEY or not FILE_SUMMARY:
        return None
    try:
        if len(transcript) <= WINDOW:
            return call_llm(
                "You are summarizing a raw audio transcript. It may contain multiple "
                f"conversations. Produce markdown with exactly these sections:\n\n{SECTIONS}"
                f"\n\nTRANSCRIPT:\n{transcript}", OR_KEY, OR_MODEL,
            )
        partials = []
        windows = split_windows(transcript, WINDOW)
        log(f"  long transcript: map-reduce over {len(windows)} windows")
        for number, chunk in enumerate(windows, 1):
            partials.append(call_llm(
                f"Summarize part {number} of {len(windows)} of an audio transcript. "
                "State only topics, commitments, and entities actually mentioned.\n\n"
                f"PART {number}:\n{chunk}", OR_KEY, OR_MODEL, max_tokens=1200))
        joined = "\n\n---\n\n".join(
            f"PART {number} SUMMARY:\n{partial}"
            for number, partial in enumerate(partials, 1))
        return call_llm(
            f"Merge these sequential transcript summaries. Produce markdown with exactly "
            f"these sections:\n\n{SECTIONS}\n\n{joined}", OR_KEY, OR_MODEL,
            max_tokens=2500)
    except Exception as exc:
        log(f"  summarization failed ({exc}) - writing transcript only")
        return None


def label_speakers(audio, segments):
    """Attach speaker labels when enabled; a failure never blocks transcription."""
    global DIARIZER
    if not DIARIZE or not segments:
        return segments
    try:
        if DIARIZER is None:
            log(f"Loading diarization pipeline '{DIARIZATION_MODEL}' ...")
            DIARIZER = diarize.load_pipeline(DIARIZATION_MODEL, HF_TOKEN)
        turns = diarize.diarize(DIARIZER, audio, DIARIZATION_MIN, DIARIZATION_MAX)
        labelled = diarize.assign_speakers(segments, turns)
        log(f"  speakers found: {len(diarize.speaker_names(labelled))}")
        return labelled
    except Exception as exc:
        log(f"  speaker labelling failed ({exc}) - continuing without speakers")
        return segments


def write_note(audio, segments, duration, summary_md, profile, comparison=False,
               status="complete", task=None):
    timestamp = datetime.fromtimestamp(audio.stat().st_mtime)
    VAULT.mkdir(parents=True, exist_ok=True)
    profile_suffix = f" {profile.key}" if comparison else ""
    note = VAULT / f"{timestamp:%Y-%m-%d} {timestamp:%H%M} transcript{profile_suffix}.md"
    number = 1
    while note.exists():
        note = VAULT / (
            f"{timestamp:%Y-%m-%d} {timestamp:%H%M} "
            f"transcript{profile_suffix} {number}.md"
        )
        number += 1
    speech = sum(segment["end"] - segment["start"] for segment in segments)
    speakers = diarize.speaker_names(segments)
    effective_task = TASK if task is None else task
    lines = ["---", f"date: {timestamp:%Y-%m-%d}", f"time: {timestamp:%H:%M}",
             "type: transcript", "source: recorder", f"audio: {audio}",
             f"duration_min: {round(duration / 60, 1)}",
             f"speech_min: {round(speech / 60, 1)}", f"model: {profile.model_id}",
             f"model_profile: {profile.key}",
             f"task: {effective_task}",
             f"transcription_status: {status}"]
    if speakers:
        lines.append(f"speakers: {len(speakers)}")
    lines += ["tags: [transcript, inbox]", "---", "",
              f"# {timestamp:%A, %B %d %Y} - {timestamp:%H:%M}", ""]
    if summary_md:
        lines += [summary_md, "", "---", ""]
    if status == "no_speech":
        lines += ["> No speech was detected in this recording.", ""]
    lines += ["## Transcript", ""]
    for segment in segments:
        speaker = segment.get("speaker")
        label = f" {speaker}:" if speaker else ""
        lines.extend([f"**[{hhmmss(segment['start'])}]{label}** {segment['text'].strip()}",
                      ""])
    write_private_text(note, "\n".join(lines))
    return note


def queue_mtime(item):
    """Sort queued recordings newest-first without failing on dangling symlinks."""
    try:
        return item.resolve().stat().st_mtime
    except OSError:
        return 0.0


def main():
    from faster_whisper import WhisperModel
    QUEUE.mkdir(parents=True, exist_ok=True)
    audio_exts = {"." + entry.strip().lower() for entry in CFG["AUDIO_EXTS"].split(",")}
    pending = sorted(
        (path for path in QUEUE.iterdir()
         if (path.is_file() or path.is_symlink()) and path.suffix.lower() in audio_exts),
        key=queue_mtime, reverse=True)
    if not pending:
        log("Queue empty.")
        write_progress(active=False, phase="Queue empty", total_files=0, files_completed=0)
        return 0

    comparison = len(MODEL_PROFILES) > 1
    total_work = len(pending) * len(MODEL_PROFILES)
    completed = 0
    notes_written = []
    con = sqlite3.connect(STATE_DB)
    try:
        for profile in MODEL_PROFILES:
            phase = f"Loading {profile.label}"
            write_progress(active=True, phase=phase, total_files=total_work,
                           files_completed=completed, current_file=pending[0].name,
                           current_percent=0)
            log(f"Loading '{profile.model_id}' on {DEVICE}/{COMPUTE} ...")
            model = WhisperModel(profile.model_id, device=DEVICE, compute_type=COMPUTE,
                                 cpu_threads=os.cpu_count() or 8)
            for item in pending:
                audio = item.resolve()
                if not audio.exists():
                    completed += 1
                    continue
                if artifacts_complete(audio, profile, comparison):
                    log(f"Skipping completed {profile.key} pass for {audio.name}")
                    completed += 1
                    continue
                log(f"Transcribing {audio.name} with {profile.model_id} ...")
                started = time.time()
                write_progress(active=True, phase=f"Transcribing ({profile.label})",
                               total_files=total_work, files_completed=completed,
                               current_file=audio.name,
                               current_percent=0,
                               eta_seconds=None)
                kwargs = {"language": LANG, "task": TASK, "beam_size": 5}
                if VAD:
                    kwargs.update(vad_filter=True,
                                  vad_parameters={"min_silence_duration_ms": VAD_MS})
                segment_iter, info = model.transcribe(str(audio), **kwargs)
                segments, last_update = [], 0.0
                for segment in segment_iter:
                    segments.append({"start": segment.start, "end": segment.end,
                                     "text": segment.text})
                    now = time.time()
                    if now - last_update >= 1:
                        file_percent = min(99, int((segment.end / info.duration) * 100)) \
                            if info.duration else 0
                        elapsed = now - started
                        eta = int(elapsed * (100 - file_percent) / file_percent) \
                            if file_percent else None
                        write_progress(active=True,
                                       phase=f"Transcribing ({profile.label})",
                                       total_files=total_work, files_completed=completed,
                                       current_file=audio.name, current_percent=file_percent,
                                       eta_seconds=eta)
                        last_update = now
                if segments and DIARIZE:
                    write_progress(active=True, phase="Labelling speakers",
                                   total_files=total_work, files_completed=completed,
                                   current_file=audio.name, current_percent=99,
                                   eta_seconds=None)
                    segments = label_speakers(audio, segments)
                if not segments:
                    write_private_text(
                        artifact_path(audio, profile, ".json", comparison),
                        json.dumps({"duration": info.duration,
                                    "model": profile.model_id,
                                    "profile": profile.key,
                                    "status": "no_speech", "segments": []}, indent=2),
                    )
                    write_private_text(
                        artifact_path(audio, profile, ".txt", comparison), ""
                    )
                    note = write_note(audio, [], info.duration, None, profile,
                                      comparison, status="no_speech", task=TASK)
                    write_private_text(
                        artifact_path(audio, profile, ".complete.json", comparison),
                        json.dumps({"status": "no_speech", "note": str(note)}, indent=2),
                    )
                    log(f"  no speech detected -> {note.name}")
                    notes_written.append(note)
                    completed += 1
                    continue
                write_private_text(
                    artifact_path(audio, profile, ".json", comparison),
                    json.dumps({"duration": info.duration, "model": profile.model_id,
                                "profile": profile.key, "segments": segments}, indent=2),
                )
                full_text = diarize.plain_text(segments)
                write_private_text(
                    artifact_path(audio, profile, ".txt", comparison), full_text
                )
                note = write_note(audio, segments, info.duration, summarize(full_text),
                                  profile, comparison, task=TASK)
                write_private_text(
                    artifact_path(audio, profile, ".complete.json", comparison),
                    json.dumps({"status": "complete", "note": str(note)}, indent=2),
                )
                elapsed = time.time() - started
                log(f"  {profile.key} done in {hhmmss(elapsed)} "
                    f"({info.duration / elapsed:.1f}x realtime) -> {note.name}")
                notes_written.append(note)
                completed += 1
            del model
            gc.collect()

        for item in pending:
            audio = item.resolve()
            if audio.exists():
                con.execute("UPDATE seen SET transcribed=1 WHERE archived_to=?",
                            (str(audio),))
                con.commit()
            item.unlink(missing_ok=True)
    finally:
        con.close()
    write_progress(active=False, phase="Transcription complete", total_files=total_work,
                   files_completed=completed, current_percent=100, eta_seconds=0)
    announce(notes_written)
    return 0


def announce(notes):
    """Tell the desktop that notes are ready; clicking opens the note or folder."""
    if not notes:
        return
    if len(notes) == 1:
        notify.send("Transcript ready", notes[0].stem, open_path=notes[0], config=CFG)
    else:
        notify.send("Transcripts ready", f"{len(notes)} new notes in {VAULT.name}",
                    open_path=VAULT, config=CFG)


def run():
    """Run main() and record a failure for the desktop before propagating it."""
    try:
        return main()
    except BaseException as exc:
        write_progress(active=False, phase="Transcription failed", error=type(exc).__name__)
        notify.send("Transcription failed",
                    f"{type(exc).__name__}: see var/logs/pipeline.log", config=CFG)
        raise


if __name__ == "__main__":
    sys.exit(run())
