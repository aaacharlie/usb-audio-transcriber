#!/usr/bin/env python3
"""Group transcribed recordings into sessions and write one note per session.

Voice-activated recorders split a meeting or a class into many files. A session
note stitches them back together in order, links every transcript note, and,
with an OpenRouter key, adds an AI summary of the whole session.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify
from llm import backend_from_config, split_windows
from model_profiles import artifact_path, artifacts_complete, profiles_for_config
from pipeline_config import ROOT, load, log, sync_directory

CFG = load()
VAULT = Path(CFG["VAULT_DIR"])
QUEUE = Path(CFG["QUEUE_DIR"])
STATE_DB = Path(CFG["STATE_DB"])
PROFILES = profiles_for_config(CFG)
COMPARISON = len(PROFILES) > 1
ENABLED = CFG.get("SESSION_NOTES", "1").strip() == "1"
GAP = timedelta(minutes=int(CFG.get("SESSION_GAP_MIN", "20") or 20))
# On an installation with history, sessions that ended more than this many
# days ago get a note but no automatic AI summary (empty = summarize all).
BACKFILL_DAYS = CFG.get("SESSION_BACKFILL_DAYS", "7").strip()
BACKFILL = timedelta(days=int(BACKFILL_DAYS)) if BACKFILL_DAYS else None
BACKEND = backend_from_config(
    CFG, model_override=CFG.get("SESSION_SUMMARY_MODEL", "").strip() or None)
SUMMARY_MODEL = BACKEND.describe() if BACKEND else "none"
SUMMARIZE = CFG.get("SESSION_SUMMARY", "1").strip() == "1" and BACKEND is not None
SUBJECT = CFG.get("SESSION_SUBJECT", "").strip() or "the subject matter of these recordings"
PROMPT_FILE = Path(CFG.get("SESSION_PROMPT_FILE", "").strip()
                   or ROOT / "prompts" / "session-summary.md")
WINDOW = int(CFG.get("MAP_WINDOW_CHARS", "80000"))
NO_BACKEND_HINT = (
    "> No AI summary was generated because no summary backend is configured. "
    "Paste the combined transcript below into an AI model together with the "
    "prompt in prompts/session-summary.md, or run setup.py (or set "
    "SUMMARY_BACKEND in config.env) to have it done automatically."
)


def call_llm(prompt, max_tokens=2000):
    """Send one prompt to the configured session summary backend."""
    return BACKEND.complete(prompt, max_tokens=max_tokens)


def hhmmss(seconds):
    return str(timedelta(seconds=int(seconds or 0)))


def transcript_profile():
    """Session notes use the most accurate configured pass."""
    return PROFILES[-1]


def init_db(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, members TEXT,
               note TEXT, summarized INTEGER DEFAULT 0, created_at TEXT
           )"""
    )
    con.commit()


def locked_digests(con):
    """Recordings already placed in a session note stay there."""
    locked = set()
    for (members,) in con.execute("SELECT members FROM sessions"):
        locked.update(json.loads(members))
    return locked


def load_recordings(con, digests=None):
    """Return recordings as dicts sorted by start time.

    Incomplete recordings (still queued or mid-transcription) are included with
    ``complete=False`` so a session is not closed while part of it is pending.
    """
    try:
        rows = con.execute(
            "SELECT sha256, archived_to FROM seen WHERE archived_to IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    profile = transcript_profile()
    recordings = []
    for digest, archived in rows:
        if digests is not None and digest not in digests:
            continue
        audio = Path(archived)
        try:
            end = datetime.fromtimestamp(audio.stat().st_mtime)
        except OSError:
            continue
        record = {"digest": digest, "audio": audio, "note": None, "segments": [],
                  "duration": 0.0, "status": "pending", "complete": False}
        if artifacts_complete(audio, profile, COMPARISON):
            try:
                data = json.loads(artifact_path(audio, profile, ".json", COMPARISON)
                                  .read_text(encoding="utf-8"))
                marker = json.loads(
                    artifact_path(audio, profile, ".complete.json", COMPARISON)
                    .read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data, marker = {}, {}
            else:
                record.update(
                    note=Path(marker["note"]) if marker.get("note") else None,
                    segments=data.get("segments", []),
                    duration=float(data.get("duration") or 0),
                    status=data.get("status", "complete"),
                    complete=True,
                )
        record["end"] = end
        record["start"] = end - timedelta(seconds=record["duration"])
        recordings.append(record)
    recordings.sort(key=lambda item: (item["start"], item["end"]))
    return recordings


def group_sessions(recordings, gap=None):
    """Split recordings into sessions wherever the silence between them exceeds gap."""
    gap = GAP if gap is None else gap
    sessions, current, current_end = [], [], None
    for record in recordings:
        if current and record["start"] - current_end > gap:
            sessions.append(current)
            current, current_end = [], None
        current.append(record)
        current_end = record["end"] if current_end is None else max(current_end, record["end"])
    if current:
        sessions.append(current)
    return sessions


def session_id(members):
    joined = "\n".join(sorted(member["digest"] for member in members))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def note_title(record):
    return record["note"].stem if record["note"] else record["audio"].name


def llm_input(members):
    """Plain, compact text for the model: one block per recording, in order."""
    blocks = []
    for number, record in enumerate(members, 1):
        header = (f"=== Recording {number}: {record['start']:%Y-%m-%d %H:%M} to "
                  f"{record['end']:%H:%M} ({note_title(record)}) ===")
        lines = [header]
        if record["status"] == "no_speech" or not record["segments"]:
            lines.append("(no speech detected)")
        for segment in record["segments"]:
            speaker = segment.get("speaker")
            label = f"{speaker}: " if speaker else ""
            lines.append(f"[{hhmmss(segment['start'])}] {label}{segment['text'].strip()}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def load_template():
    template = PROMPT_FILE.read_text(encoding="utf-8")
    return template.replace("{subject}", SUBJECT)


def summarize_session(text):
    """Summarize one session's transcripts; long sessions go through map-reduce."""
    template = load_template()
    if len(text) <= WINDOW:
        return call_llm(f"{template}\n\nTRANSCRIPTS:\n\n{text}", max_tokens=4000)
    windows = split_windows(text, WINDOW)
    log(f"  long session: map-reduce over {len(windows)} windows")
    partials = []
    for number, chunk in enumerate(windows, 1):
        partials.append(call_llm(
            f"This is part {number} of {len(windows)} of the transcripts from one "
            "session. Summarize this part in detail and in order, keeping names, "
            "numbers, dates, decisions, and open questions. Output Markdown bullet "
            f"points only.\n\nPART {number}:\n{chunk}", max_tokens=2000))
    joined = "\n\n".join(f"PART {number} NOTES:\n{partial}"
                         for number, partial in enumerate(partials, 1))
    return call_llm(
        f"{template}\n\nThe transcripts were too long to send at once, so below are "
        "detailed, in-order notes on each part instead of the raw text. Treat them "
        f"as the transcripts.\n\nPART NOTES:\n\n{joined}", max_tokens=4000)


def write_private_text(path, text):
    """Atomically and durably write sensitive text with user-only permissions."""
    import os
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


def unique_note_path(start):
    base = f"{start:%Y-%m-%d} {start:%H%M} session"
    note = VAULT / f"{base}.md"
    number = 1
    while note.exists():
        note = VAULT / f"{base} {number}.md"
        number += 1
    return note


def render_note(members, summary_md, summary_status, ident):
    start = min(member["start"] for member in members)
    end = max(member["end"] for member in members)
    duration = sum(member["duration"] for member in members)
    speech = sum(segment["end"] - segment["start"]
                 for member in members for segment in member["segments"])
    lines = [
        "---",
        f"date: {start:%Y-%m-%d}",
        f"time: {start:%H:%M}",
        "type: session",
        f"recordings: {len(members)}",
        f"duration_min: {round(duration / 60, 1)}",
        f"speech_min: {round(speech / 60, 1)}",
        f"session_id: {ident}",
        f"summary_model: {SUMMARY_MODEL if summary_status == 'ok' else 'none'}",
        "tags: [session, inbox]",
        "---",
        "",
        f"# Session - {start:%A, %B %d %Y}, {start:%H:%M} to {end:%H:%M}",
        "",
        "## Summary",
        "",
    ]
    if summary_status == "ok":
        lines += [summary_md, ""]
    elif summary_status == "failed":
        lines += [f"> AI summary failed ({summary_md}). Run "
                  "`sessions.py retry` to try again once the problem is fixed.", ""]
    elif summary_status == "disabled":
        lines += ["> AI summaries are turned off (SESSION_SUMMARY=0).", ""]
    elif summary_status == "backfill":
        lines += [f"> AI summary skipped: this session ended more than {BACKFILL_DAYS} "
                  "days before it was written (SESSION_BACKFILL_DAYS). Run "
                  "`sessions.py retry` to summarize older sessions on demand.", ""]
    else:
        lines += [NO_BACKEND_HINT, ""]
    lines += ["## Recordings", ""]
    for member in members:
        minutes = round(member["duration"] / 60)
        link = f"[[{member['note'].stem}]]" if member["note"] else member["audio"].name
        lines.append(f"- {link} {member['start']:%H:%M} to {member['end']:%H:%M} "
                     f"({minutes} min)")
    lines += ["", "---", "", "## Combined transcript", ""]
    for member in members:
        lines += [f"### {member['start']:%H:%M} - {note_title(member)}", ""]
        if member["status"] == "no_speech" or not member["segments"]:
            lines += ["> No speech was detected in this recording.", ""]
        for segment in member["segments"]:
            speaker = segment.get("speaker")
            label = f" {speaker}:" if speaker else ""
            lines += [f"**[{hhmmss(segment['start'])}]{label}** "
                      f"{segment['text'].strip()}", ""]
    return "\n".join(lines)


def build_summary(members, force=False):
    """Return (markdown, status): ok, failed, disabled, no_backend, or backfill."""
    if BACKEND is None:
        return None, "no_backend"
    if not SUMMARIZE:
        return None, "disabled"
    if not force and BACKFILL is not None:
        ended = max(member["end"] for member in members)
        if ended < datetime.now() - BACKFILL:
            return None, "backfill"
    try:
        return summarize_session(llm_input(members)), "ok"
    except Exception as exc:  # network, quota, or model errors must not lose the note
        log(f"  session summary failed ({exc}) - writing the note without it")
        return str(exc), "failed"


def write_session(con, members, note=None, force=False):
    ident = session_id(members)
    start = min(member["start"] for member in members)
    end = max(member["end"] for member in members)
    summary_md, status = build_summary(members, force)
    VAULT.mkdir(parents=True, exist_ok=True)
    note = note or unique_note_path(start)
    write_private_text(note, render_note(members, summary_md, status, ident))
    con.execute(
        "INSERT OR REPLACE INTO sessions "
        "(id, started_at, ended_at, members, note, summarized, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (ident, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"),
         json.dumps([member["digest"] for member in members]), str(note),
         1 if status == "ok" else 0, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    log(f"  session note ({len(members)} recording(s), summary: {status}) -> {note.name}")
    return note


def auto(con):
    """Write notes for every session whose recordings are all transcribed."""
    locked = locked_digests(con)
    recordings = [record for record in load_recordings(con)
                  if record["digest"] not in locked]
    written = []
    for members in group_sessions(recordings):
        if not all(member["complete"] for member in members):
            log(f"  session starting {members[0]['start']:%Y-%m-%d %H:%M} is still "
                "being transcribed; waiting")
            continue
        written.append(write_session(con, members))
    if len(written) > 3:
        # A first run over history can write dozens of notes; one notification.
        notify.send("Session notes written", f"{len(written)} session notes in {VAULT.name}",
                    open_path=VAULT, config=CFG)
    else:
        for note in written:
            notify.send("Session note ready", note.stem, open_path=note, config=CFG)
    if not written:
        log("No new sessions to write.")
    return written


def retry(con):
    """Add summaries to session notes that were written without one."""
    if not SUMMARIZE:
        log("Session summaries are not enabled (need SESSION_SUMMARY=1 and an API key).")
        return []
    rows = con.execute(
        "SELECT id, members, note FROM sessions WHERE summarized=0"
    ).fetchall()
    written = []
    for ident, members_json, note in rows:
        digests = set(json.loads(members_json))
        members = load_recordings(con, digests)
        if len(members) != len(digests) or not all(m["complete"] for m in members):
            log(f"  session {ident}: recordings missing or incomplete, skipping")
            continue
        written.append(write_session(con, members, note=Path(note), force=True))
    if not written:
        log("No session notes were waiting for a summary.")
    return written


def rebuild(con, date):
    """Forget the sessions that started on a date and regenerate them."""
    rows = con.execute(
        "SELECT id, note FROM sessions WHERE started_at LIKE ?", (f"{date}%",)
    ).fetchall()
    for ident, note in rows:
        Path(note).unlink(missing_ok=True)
        con.execute("DELETE FROM sessions WHERE id=?", (ident,))
    con.commit()
    log(f"Forgot {len(rows)} session(s) starting on {date}; regenerating.")
    return auto(con)


def test_backend():
    """Send a tiny prompt through the configured backend and show what came back."""
    if BACKEND is None:
        print("No summary backend is configured (SUMMARY_BACKEND). Summaries are off.")
        return 1
    print(f"Backend: {BACKEND.describe()}")
    try:
        reply = BACKEND.complete("Reply with exactly the word OK and nothing else.",
                                 max_tokens=20)
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Reply: {reply[:200]}")
    return 0


def list_sessions(con):
    rows = con.execute(
        "SELECT started_at, ended_at, members, summarized, note FROM sessions "
        "ORDER BY started_at"
    ).fetchall()
    for started, ended, members, summarized, note in rows:
        count = len(json.loads(members))
        print(f"{started} to {ended[11:16]}  {count:2} recording(s)  "
              f"summary: {'yes' if summarized else 'no'}  {Path(note).name}")
    if not rows:
        print("No session notes yet.")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="auto",
                        choices=("auto", "list", "retry", "rebuild", "test-backend"))
    parser.add_argument("--date", help="YYYY-MM-DD, required for rebuild")
    args = parser.parse_args(argv)
    if args.command == "rebuild" and not args.date:
        parser.error("rebuild needs --date YYYY-MM-DD")
    if args.command == "auto" and not ENABLED:
        log("Session notes are disabled (SESSION_NOTES=0).")
        return 0
    if args.command == "test-backend":
        return test_backend()
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE_DB)
    try:
        init_db(con)
        if args.command == "auto":
            auto(con)
        elif args.command == "list":
            list_sessions(con)
        elif args.command == "retry":
            retry(con)
        else:
            rebuild(con, args.date)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
