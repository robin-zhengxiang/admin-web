import glob
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "usage.db")
PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")
SCAN_INTERVAL_SECONDS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT,
    title TEXT,
    first_ts TEXT,
    last_ts TEXT
);

CREATE TABLE IF NOT EXISTS usage_events (
    uuid TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT,
    ts TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    is_sidechain INTEGER DEFAULT 0,
    parent_uuid TEXT,
    tools TEXT,
    text_preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(model);
CREATE INDEX IF NOT EXISTS idx_usage_project ON usage_events(project);

CREATE TABLE IF NOT EXISTS index_state (
    file_path TEXT PRIMARY KEY,
    offset INTEGER,
    mtime REAL
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _upsert_session_title(cur, session_id, project, title):
    cur.execute(
        """INSERT INTO sessions(session_id, project, title) VALUES (?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             title=excluded.title,
             project=COALESCE(excluded.project, sessions.project)""",
        (session_id, project, title),
    )


def _upsert_session_span(cur, session_id, project, ts):
    cur.execute(
        """INSERT INTO sessions(session_id, project, first_ts, last_ts) VALUES (?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             project=COALESCE(excluded.project, sessions.project),
             first_ts=CASE
               WHEN sessions.first_ts IS NULL THEN excluded.first_ts
               WHEN excluded.first_ts IS NULL THEN sessions.first_ts
               ELSE MIN(sessions.first_ts, excluded.first_ts)
             END,
             last_ts=CASE
               WHEN sessions.last_ts IS NULL THEN excluded.last_ts
               WHEN excluded.last_ts IS NULL THEN sessions.last_ts
               ELSE MAX(sessions.last_ts, excluded.last_ts)
             END""",
        (session_id, project, ts, ts),
    )


def _process_line(cur, line):
    line = line.strip()
    if not line:
        return
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return

    session_id = d.get("sessionId")
    if not session_id:
        return
    cwd = d.get("cwd")
    ts = d.get("timestamp")
    dtype = d.get("type")

    if dtype == "ai-title":
        _upsert_session_title(cur, session_id, cwd, d.get("aiTitle"))
        return

    if ts:
        _upsert_session_span(cur, session_id, cwd, ts)

    if dtype != "assistant":
        return
    msg = d.get("message") or {}
    usage = msg.get("usage")
    uuid_ = d.get("uuid")
    if not usage or not uuid_:
        return

    content = msg.get("content")
    tools = []
    text_preview = None
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                tools.append(c.get("name") or "")
            elif c.get("type") == "text" and text_preview is None:
                text_preview = (c.get("text") or "")[:200]

    cur.execute(
        """INSERT OR IGNORE INTO usage_events
           (uuid, session_id, project, ts, model, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, is_sidechain, parent_uuid,
            tools, text_preview)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            uuid_,
            session_id,
            cwd,
            ts,
            msg.get("model"),
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cache_read_input_tokens", 0),
            usage.get("cache_creation_input_tokens", 0),
            1 if d.get("isSidechain") else 0,
            d.get("parentUuid"),
            ",".join(t for t in tools if t),
            text_preview,
        ),
    )


def scan_once(conn=None):
    """Incrementally parse new lines from all session jsonl files. Returns rows processed."""
    owns_conn = conn is None
    conn = conn or get_conn()
    cur = conn.cursor()
    processed = 0

    for path in glob.glob(PROJECTS_GLOB, recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue

        row = cur.execute(
            "SELECT offset, mtime FROM index_state WHERE file_path = ?", (path,)
        ).fetchone()
        offset = row["offset"] if row else 0
        if st.st_size < offset:
            offset = 0  # file was truncated/rotated
        if row and st.st_size == offset and st.st_mtime == row["mtime"]:
            continue  # unchanged since last scan

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                _process_line(cur, line)
                processed += 1
            new_offset = f.tell()

        cur.execute(
            """INSERT INTO index_state(file_path, offset, mtime) VALUES (?, ?, ?)
               ON CONFLICT(file_path) DO UPDATE SET offset=excluded.offset, mtime=excluded.mtime""",
            (path, new_offset, st.st_mtime),
        )

    conn.commit()
    if owns_conn:
        conn.close()
    return processed


def _indexer_loop(stop_event):
    while not stop_event.is_set():
        try:
            scan_once()
        except Exception as e:
            print(f"[db] indexer error: {e}")
        stop_event.wait(SCAN_INTERVAL_SECONDS)


def start_indexer_thread():
    stop_event = threading.Event()
    thread = threading.Thread(target=_indexer_loop, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event


if __name__ == "__main__":
    init_db()
    start = time.time()
    n = scan_once()
    print(f"indexed {n} new lines in {time.time() - start:.2f}s")
