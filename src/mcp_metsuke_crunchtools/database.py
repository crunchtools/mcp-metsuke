"""SQLite persistence for mcp-metsuke-crunchtools.

Two tables: report_definitions (what to gather, and when) and report_outputs
(what was gathered). Plain stdlib sqlite3 — no external services, no vector
extensions. The scheduler thread uses its own connection (new_connection);
the MCP server tools share the singleton (get_db).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import get_config

_db: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_definitions (
    name TEXT PRIMARY KEY,
    gather_prompt TEXT NOT NULL,
    owner_agent TEXT NOT NULL DEFAULT 'kagetora',
    schedule TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    source_config TEXT,
    last_fired_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS report_outputs (
    id INTEGER PRIMARY KEY,
    report_name TEXT NOT NULL REFERENCES report_definitions(name) ON DELETE CASCADE,
    gathered_at TEXT NOT NULL DEFAULT (datetime('now')),
    window_start TEXT,
    window_end TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'gathering',
    gatherer_run_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_outputs_report ON report_outputs(report_name);
CREATE INDEX IF NOT EXISTS idx_outputs_gathered ON report_outputs(gathered_at);
"""

_MIGRATIONS = (
    ("report_definitions", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
    ("report_definitions", "last_fired_at", "TEXT"),
)

_SELECT_ALL_DEFS = (
    "SELECT name, gather_prompt, owner_agent, schedule, timezone, "
    "source_config, last_fired_at, updated_at FROM report_definitions "
    "ORDER BY updated_at DESC"
)

_SELECT_ONE_DEF = (
    "SELECT name, gather_prompt, owner_agent, schedule, timezone, "
    "source_config, last_fired_at, updated_at FROM report_definitions "
    "WHERE name = ?"
)


def _configure(conn: sqlite3.Connection) -> None:
    """Apply row factory and pragmas shared by every connection."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing and apply column migrations."""
    conn.executescript(SCHEMA)
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, column, ddl in _MIGRATIONS:
        if table not in existing_tables:
            continue
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()


def _open_connection() -> sqlite3.Connection:
    """Open, configure, and schema-init a new connection to the configured DB."""
    path = get_config().db_path
    if path != ":memory:":
        get_config().ensure_db_dir()
    conn = sqlite3.connect(path)
    _configure(conn)
    _init_schema(conn)
    return conn


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Get or create the singleton database connection (for MCP server tools)."""
    global _db
    if _db is None:
        if db_path is not None:
            path = db_path
            if path != ":memory:":
                get_config().ensure_db_dir()
            _db = sqlite3.connect(path)
            _configure(_db)
            _init_schema(_db)
        else:
            _db = _open_connection()
    return _db


def new_connection() -> sqlite3.Connection:
    """Open a fresh, independent connection.

    The scheduler runs in its own thread, so it cannot share the singleton
    (sqlite3 connections are single-thread by default). WAL mode lets this
    connection read/write concurrently with the server's connection.
    """
    return _open_connection()


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a SELECT query and return results as dicts."""
    db = get_db()
    cursor = db.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute a SELECT query and return a single result or None."""
    db = get_db()
    cursor = db.execute(sql, params)
    row = cursor.fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    """Execute an INSERT/UPDATE/DELETE and return lastrowid."""
    db = get_db()
    cursor = db.execute(sql, params)
    db.commit()
    return cursor.lastrowid or 0


def list_definitions() -> list[dict[str, Any]]:
    """Return all report definitions, newest-updated first."""
    rows = query(_SELECT_ALL_DEFS)
    return [_decode_row(row, "source_config") for row in rows]


def get_definition(name: str) -> dict[str, Any] | None:
    """Return a single report definition, or None."""
    row = query_one(_SELECT_ONE_DEF, (name,))
    return _decode_row(row, "source_config") if row else None


def upsert_definition(
    name: str,
    gather_prompt: str,
    owner_agent: str,
    schedule: str | None,
    timezone: str,
    source_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Insert or update a report definition. Returns the stored definition."""
    source_json = json.dumps(source_config) if source_config is not None else None
    execute(
        "INSERT INTO report_definitions "
        "(name, gather_prompt, owner_agent, schedule, timezone, source_config, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(name) DO UPDATE SET "
        "gather_prompt = excluded.gather_prompt, "
        "owner_agent = excluded.owner_agent, "
        "schedule = excluded.schedule, "
        "timezone = excluded.timezone, "
        "source_config = excluded.source_config, "
        "updated_at = datetime('now')",
        (name, gather_prompt, owner_agent, schedule, timezone, source_json),
    )
    stored = get_definition(name)
    if stored is None:  # pragma: no cover - just-written row always exists
        raise RuntimeError(f"Failed to persist definition: {name}")
    return stored


def list_scheduled(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return definitions that carry a schedule (for the scheduler thread)."""
    cursor = conn.execute(
        "SELECT name, schedule, timezone, last_fired_at FROM report_definitions "
        "WHERE schedule IS NOT NULL AND schedule != ''"
    )
    return [dict(row) for row in cursor.fetchall()]


def set_last_fired(conn: sqlite3.Connection, name: str, fired_at: str) -> None:
    """Record the last time the scheduler fired a report (dedup across polls)."""
    conn.execute(
        "UPDATE report_definitions SET last_fired_at = ? WHERE name = ?",
        (fired_at, name),
    )
    conn.commit()


def insert_output(
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None,
    window_end: str | None,
    status: str,
    gatherer_run_ref: str | None,
) -> int:
    """Persist a gathered output. Returns the new output ID."""
    return execute(
        "INSERT INTO report_outputs "
        "(report_name, payload, window_start, window_end, status, gatherer_run_ref) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            report_name,
            json.dumps(payload),
            window_start,
            window_end,
            status,
            gatherer_run_ref,
        ),
    )


def get_latest_output(name: str) -> dict[str, Any] | None:
    """Return the most recent output for a report, or None."""
    row = query_one(
        "SELECT id, report_name, gathered_at, window_start, window_end, "
        "payload, status, gatherer_run_ref FROM report_outputs "
        "WHERE report_name = ? ORDER BY gathered_at DESC, id DESC LIMIT 1",
        (name,),
    )
    return _decode_row(row, "payload") if row else None


def get_output_on_date(name: str, on_date: str) -> dict[str, Any] | None:
    """Return the most recent output gathered on a given date (YYYY-MM-DD)."""
    row = query_one(
        "SELECT id, report_name, gathered_at, window_start, window_end, "
        "payload, status, gatherer_run_ref FROM report_outputs "
        "WHERE report_name = ? AND date(gathered_at) = date(?) "
        "ORDER BY gathered_at DESC, id DESC LIMIT 1",
        (name, on_date),
    )
    return _decode_row(row, "payload") if row else None


def list_outputs(report_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return saved-output metadata (no payloads), newest first.

    The payload column is deliberately excluded and replaced by a
    ``finding_count`` so callers can browse the full run history cheaply without
    pulling every finding into context. Filters to one report when given.
    """
    sql = (
        "SELECT id, report_name, gathered_at, window_start, window_end, status, "
        "gatherer_run_ref, json_array_length(payload) AS finding_count "
        "FROM report_outputs"
    )
    params: tuple[Any, ...] = ()
    if report_name is not None:
        sql += " WHERE report_name = ?"
        params = (report_name,)
    sql += " ORDER BY gathered_at DESC, id DESC LIMIT ?"
    return query(sql, (*params, limit))


def get_output_meta(output_id: int) -> dict[str, Any] | None:
    """Return one output's metadata (no payload) by id, or None."""
    return query_one(
        "SELECT id, report_name, gathered_at, window_start, window_end, status, "
        "gatherer_run_ref, json_array_length(payload) AS finding_count "
        "FROM report_outputs WHERE id = ?",
        (output_id,),
    )


def delete_output(output_id: int) -> dict[str, Any] | None:
    """Delete one output by id. Returns its metadata if it existed, else None."""
    meta = get_output_meta(output_id)
    if meta is None:
        return None
    execute("DELETE FROM report_outputs WHERE id = ?", (output_id,))
    return meta


def prune_outputs(
    report_name: str,
    keep_last: int | None = None,
    before_date: str | None = None,
) -> list[int]:
    """Bulk-delete a report's outputs, returning the deleted ids (newest first).

    Exactly one criterion applies (the tool layer enforces this): ``keep_last``
    retains the N most recent outputs and deletes the rest; ``before_date``
    deletes every output gathered strictly before that date (YYYY-MM-DD).
    """
    if keep_last is not None:
        rows = query(
            "SELECT id FROM report_outputs WHERE report_name = ? "
            "ORDER BY gathered_at DESC, id DESC LIMIT -1 OFFSET ?",
            (report_name, keep_last),
        )
    else:
        rows = query(
            "SELECT id FROM report_outputs WHERE report_name = ? "
            "AND date(gathered_at) < date(?) ORDER BY gathered_at DESC, id DESC",
            (report_name, before_date),
        )
    ids = [row["id"] for row in rows]
    for output_id in ids:
        execute("DELETE FROM report_outputs WHERE id = ?", (output_id,))
    return ids


def get_output_by_id(output_id: int) -> dict[str, Any] | None:
    """Return a single output by ID, or None."""
    row = query_one(
        "SELECT id, report_name, gathered_at, window_start, window_end, "
        "payload, status, gatherer_run_ref FROM report_outputs WHERE id = ?",
        (output_id,),
    )
    return _decode_row(row, "payload") if row else None


def _decode_row(row: dict[str, Any], json_column: str) -> dict[str, Any]:
    """Return a copy of row with json_column parsed from JSON text to a value."""
    decoded = dict(row)
    raw = decoded.get(json_column)
    decoded[json_column] = json.loads(raw) if raw else None
    return decoded
