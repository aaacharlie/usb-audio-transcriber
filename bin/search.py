#!/usr/bin/env python3
"""Full-text search across every transcript segment, from the command line.

    search.py roof leak            words are ANDed; add * for a prefix: plumb*
    search.py --since 2026-09-01 --speaker "Speaker 2" invoice
    search.py --index              refresh the index without searching

The index is an FTS5 table inside the state database, built from the JSON
sidecars next to the archived audio. It is refreshed at the end of every cycle
and before each search, and it never touches the queue or the archive.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_profiles import artifact_path, artifacts_complete, profiles_for_config
from pipeline_config import load, log

CFG = load()
STATE_DB = Path(CFG["STATE_DB"])
PROFILES = profiles_for_config(CFG)
COMPARISON = len(PROFILES) > 1
DEFAULT_LIMIT = 50


def fts5_available():
    """Whether this Python's SQLite can create FTS5 tables."""
    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        finally:
            probe.close()
        return True
    except sqlite3.OperationalError:
        return False


def transcript_profile():
    """Search the most accurate configured pass, like session notes do."""
    return PROFILES[-1]


def init_index(con):
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5("
        "text, speaker, audio UNINDEXED, note UNINDEXED, segment_index UNINDEXED, "
        "start_seconds UNINDEXED, recorded_at UNINDEXED, tokenize='porter unicode61')"
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS search_index (
               audio TEXT PRIMARY KEY, sidecar TEXT, sidecar_mtime REAL,
               segments INTEGER, indexed_at TEXT
           )"""
    )
    con.commit()


def recordings(con):
    """Yield (audio, sidecar, note, recorded_at) for every completed recording."""
    try:
        rows = con.execute(
            "SELECT archived_to FROM seen WHERE archived_to IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        return
    profile = transcript_profile()
    for (archived,) in rows:
        audio = Path(archived)
        if not audio.is_file() or not artifacts_complete(audio, profile, COMPARISON):
            continue
        sidecar = artifact_path(audio, profile, ".json", COMPARISON)
        marker = artifact_path(audio, profile, ".complete.json", COMPARISON)
        try:
            note = json.loads(marker.read_text(encoding="utf-8")).get("note") or ""
        except (OSError, json.JSONDecodeError):
            note = ""
        recorded_at = datetime.fromtimestamp(audio.stat().st_mtime)
        yield audio, sidecar, note, recorded_at


def refresh(con):
    """Index new or changed sidecars and forget vanished recordings.

    Returns (indexed, removed) counts. Re-running is cheap: a recording is
    re-read only when its sidecar's modification time changed.
    """
    init_index(con)
    known = dict(con.execute("SELECT audio, sidecar_mtime FROM search_index"))
    present = set()
    indexed = 0
    for audio, sidecar, note, recorded_at in recordings(con):
        key = str(audio)
        present.add(key)
        try:
            mtime = sidecar.stat().st_mtime
        except OSError:
            continue
        if known.get(key) == mtime:
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        segments = data.get("segments") or []
        con.execute("DELETE FROM segments_fts WHERE audio=?", (key,))
        con.executemany(
            "INSERT INTO segments_fts (text, speaker, audio, note, segment_index, "
            "start_seconds, recorded_at) VALUES (?,?,?,?,?,?,?)",
            [
                ((segment.get("text") or "").strip(), segment.get("speaker") or "",
                 key, note, index, float(segment.get("start") or 0),
                 recorded_at.isoformat(timespec="seconds"))
                for index, segment in enumerate(segments)
            ],
        )
        con.execute(
            "INSERT OR REPLACE INTO search_index VALUES (?,?,?,?,?)",
            (key, str(sidecar), mtime, len(segments),
             datetime.now().isoformat(timespec="seconds")),
        )
        indexed += 1
    removed = 0
    for key in set(known) - present:
        con.execute("DELETE FROM segments_fts WHERE audio=?", (key,))
        con.execute("DELETE FROM search_index WHERE audio=?", (key,))
        removed += 1
    con.commit()
    return indexed, removed


def fts_query(words, raw=False):
    """Turn plain words into a safe FTS5 query: every word must match, and a
    trailing * asks for a prefix. --raw passes FTS5 syntax through untouched."""
    text = " ".join(words).strip()
    if raw:
        return text
    parts = []
    for token in text.split():
        prefix = token.endswith("*") and len(token) > 1
        core = token[:-1] if prefix else token
        parts.append('"' + core.replace('"', '""') + '"' + ("*" if prefix else ""))
    return " ".join(parts)


def search(con, words, limit=DEFAULT_LIMIT, since=None, speaker=None, raw=False):
    """Return matching segments, newest recording first, in time order within it."""
    init_index(con)
    con.row_factory = sqlite3.Row
    sql = (
        "SELECT audio, note, segment_index, start_seconds, recorded_at, speaker, "
        "highlight(segments_fts, 0, '[', ']') AS text "
        "FROM segments_fts WHERE segments_fts MATCH ?"
    )
    params = [fts_query(words, raw)]
    if since:
        sql += " AND recorded_at >= ?"
        params.append(since)
    if speaker:
        sql += " AND speaker = ?"
        params.append(speaker)
    sql += " ORDER BY recorded_at DESC, start_seconds ASC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in con.execute(sql, params)]


def format_hit(hit):
    label = Path(hit["note"]).stem if hit["note"] else Path(hit["audio"]).name
    stamp = str(timedelta(seconds=int(hit["start_seconds"] or 0)))
    who = f"{hit['speaker']}: " if hit["speaker"] else ""
    return f"{label}  [{stamp}] {who}{hit['text']}\n    {hit['note'] or hit['audio']}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("words", nargs="*", help="words to search for")
    parser.add_argument("--index", action="store_true",
                        help="refresh the index and exit")
    parser.add_argument("--no-refresh", action="store_true",
                        help="search the existing index without refreshing it")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="only recordings on or after this date")
    parser.add_argument("--speaker", help='only segments by this label, e.g. "Speaker 2"')
    parser.add_argument("--raw", action="store_true",
                        help="pass FTS5 query syntax through unchanged")
    parser.add_argument("--json", action="store_true", help="print hits as JSON")
    args = parser.parse_args(argv)

    if not fts5_available():
        message = "SQLite was built without FTS5, so transcript search is unavailable."
        if args.index:
            log(message + " Skipping index refresh.")
            return 0
        print(message, file=sys.stderr)
        return 2
    if not args.index and not args.words:
        parser.error("give some words to search for, or --index")

    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE_DB)
    try:
        if not args.no_refresh:
            indexed, removed = refresh(con)
            if args.index:
                log(f"Search index: {indexed} recording(s) indexed, {removed} removed.")
                return 0
        try:
            hits = search(con, args.words, args.limit, args.since, args.speaker, args.raw)
        except sqlite3.OperationalError as exc:
            print(f"Search failed: {exc}", file=sys.stderr)
            return 2
    finally:
        con.close()

    if args.json:
        print(json.dumps(hits, indent=2))
    else:
        for hit in hits:
            print(format_hit(hit))
        if not hits:
            print("No matches.", file=sys.stderr)
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
