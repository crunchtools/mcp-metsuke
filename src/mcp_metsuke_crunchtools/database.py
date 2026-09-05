"""SQLite persistence for mcp-metsuke-crunchtools.

Two tables: report_definitions (what to gather, and when) and report_outputs
(what was gathered — one row per run). Plain stdlib sqlite3 — no external
services, no vector extensions. The scheduler thread uses its own connection
(new_connection); the MCP server tools share the singleton (get_db).

A report *run* is a report_outputs row. Firing a report writes a ``gathering``
provisional row before the callback is dispatched (guaranteed save), stamped with
a second-granularity ``run_id``; the gatherer later completes that same row via
save_output. A partial unique index on ``(report_name) WHERE status='gathering'``
is the per-report concurrency lock: the database permits at most one in-flight
run per report.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from .config import get_config
from .errors import RunInFlightError

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
    run_id TEXT,
    trigger TEXT,
    gathered_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    window_start TEXT,
    window_end TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'gathering',
    gatherer_run_ref TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_outputs_report ON report_outputs(report_name);
CREATE INDEX IF NOT EXISTS idx_outputs_gathered ON report_outputs(gathered_at);
"""

_RUN_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_outputs_run_id
    ON report_outputs(run_id) WHERE run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_outputs_inflight
    ON report_outputs(report_name) WHERE status = 'gathering';
"""

_MIGRATIONS = (
    ("report_definitions", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
    ("report_definitions", "last_fired_at", "TEXT"),
    ("report_outputs", "run_id", "TEXT"),
    ("report_outputs", "trigger", "TEXT"),
    ("report_outputs", "finished_at", "TEXT"),
    ("report_outputs", "detail", "TEXT"),
)

_SQL_LATEST_OUTPUT = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, payload, status, gatherer_run_ref, detail "
    "FROM report_outputs WHERE report_name = ? AND status IN ('ready', 'compiled') "
    "ORDER BY gathered_at DESC, id DESC LIMIT 1"
)
_SQL_OUTPUT_ON_DATE = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, payload, status, gatherer_run_ref, detail "
    "FROM report_outputs WHERE report_name = ? AND status IN ('ready', 'compiled') "
    "AND date(gathered_at) = date(?) ORDER BY gathered_at DESC, id DESC LIMIT 1"
)
_SQL_OUTPUT_BY_ID = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, payload, status, gatherer_run_ref, detail "
    "FROM report_outputs WHERE id = ?"
)
_SQL_OUTPUT_BY_RUN_ID = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, payload, status, gatherer_run_ref, detail "
    "FROM report_outputs WHERE run_id = ?"
)
_SQL_OUTPUT_META_BY_ID = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, status, gatherer_run_ref, detail, "
    "json_array_length(payload) AS finding_count FROM report_outputs WHERE id = ?"
)
_SQL_LIST_OUTPUTS_ALL = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, status, gatherer_run_ref, detail, "
    "json_array_length(payload) AS finding_count FROM report_outputs "
    "ORDER BY gathered_at DESC, id DESC LIMIT ?"
)
_SQL_LIST_OUTPUTS_ONE = (
    "SELECT id, report_name, run_id, trigger, gathered_at, finished_at, "
    "window_start, window_end, status, gatherer_run_ref, detail, "
    "json_array_length(payload) AS finding_count FROM report_outputs "
    "WHERE report_name = ? ORDER BY gathered_at DESC, id DESC LIMIT ?"
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
    """Create tables, apply column migrations, then create run-lifecycle indexes.

    Indexes come last because they reference columns (run_id, status) that the
    migrations may only just have added to a pre-existing database.
    """
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
    conn.executescript(_RUN_INDEXES)
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


def _make_run_id(conn: sqlite3.Connection, report_name: str) -> str:
    """Mint a second-granularity run id, suffixing on same-second collisions."""
    base = f"{report_name}@{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_id = base
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM report_outputs WHERE run_id = ? LIMIT 1", (run_id,)
    ).fetchone():
        run_id = f"{base}-{suffix}"
        suffix += 1
    return run_id


def _expire_stale_runs(conn: sqlite3.Connection, report_name: str) -> None:
    """Fail this report's in-flight runs that outlived the lock TTL.

    Releases the per-report lock a dead gatherer would otherwise hold forever.
    """
    ttl = get_config().run_lock_ttl_seconds
    conn.execute(
        "UPDATE report_outputs SET status = 'failed', finished_at = datetime('now'), "
        "detail = 'expired: no save within lock TTL' "
        "WHERE report_name = ? AND status = 'gathering' "
        "AND gathered_at <= datetime('now', ?)",
        (report_name, f"-{ttl} seconds"),
    )
    conn.commit()


def begin_run(
    report_name: str,
    trigger: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Open a run: write a ``gathering`` row before dispatching a fire.

    Acquires the per-report concurrency lock via the partial unique index. Raises
    RunInFlightError if a live run for this report already holds the lock. Returns
    ``{run_id, output_id, report_name, status}``. Guarantees a durable record of
    the fire even if the gatherer never calls back.
    """
    conn = conn or get_db()
    _expire_stale_runs(conn, report_name)
    run_id = _make_run_id(conn, report_name)
    try:
        cursor = conn.execute(
            "INSERT INTO report_outputs (report_name, run_id, trigger, payload, status) "
            "VALUES (?, ?, ?, '[]', 'gathering')",
            (report_name, run_id, trigger),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        held = conn.execute(
            "SELECT run_id FROM report_outputs WHERE report_name = ? AND status = 'gathering' "
            "ORDER BY gathered_at DESC, id DESC LIMIT 1",
            (report_name,),
        ).fetchone()
        raise RunInFlightError(report_name, held["run_id"] if held else None) from exc
    return {
        "run_id": run_id,
        "output_id": cursor.lastrowid or 0,
        "report_name": report_name,
        "status": "gathering",
    }


def complete_run(
    run_id: str,
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None,
    window_end: str | None,
    status: str,
    gatherer_run_ref: str | None,
) -> dict[str, Any] | None:
    """Complete an in-flight run: fill its findings and reach a terminal status.

    Updates only a ``gathering`` row whose run_id and report match. Returns the
    updated output, or None if no such open run exists (unknown or already
    terminal run_id).
    """
    db = get_db()
    cursor = db.execute(
        "UPDATE report_outputs SET payload = ?, window_start = ?, window_end = ?, "
        "status = ?, gatherer_run_ref = ?, finished_at = datetime('now') "
        "WHERE run_id = ? AND report_name = ? AND status = 'gathering'",
        (
            json.dumps(payload),
            window_start,
            window_end,
            status,
            gatherer_run_ref,
            run_id,
            report_name,
        ),
    )
    db.commit()
    if cursor.rowcount == 0:
        return None
    return get_output_by_run_id(run_id)


def fail_run(
    run_id: str,
    detail: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Mark an in-flight run failed (e.g. callback dispatch failed)."""
    conn = conn or get_db()
    conn.execute(
        "UPDATE report_outputs SET status = 'failed', finished_at = datetime('now'), "
        "detail = ? WHERE run_id = ? AND status = 'gathering'",
        (detail, run_id),
    )
    conn.commit()


def insert_output(
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None,
    window_end: str | None,
    status: str,
    gatherer_run_ref: str | None,
    trigger: str = "direct",
) -> int:
    """Persist a completed output directly (no pre-opened run). Returns its ID.

    Stamps a fresh second-granularity run_id so even a direct save is an
    addressable run. Used by save_output when no run_id is supplied.
    """
    db = get_db()
    run_id = _make_run_id(db, report_name)
    cursor = db.execute(
        "INSERT INTO report_outputs "
        "(report_name, run_id, trigger, payload, window_start, window_end, "
        "status, gatherer_run_ref, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            report_name,
            run_id,
            trigger,
            json.dumps(payload),
            window_start,
            window_end,
            status,
            gatherer_run_ref,
        ),
    )
    db.commit()
    return cursor.lastrowid or 0


def get_latest_output(name: str) -> dict[str, Any] | None:
    """Return the most recent *readable* output for a report, or None.

    Skips in-flight and failed runs so a compiler only ever reads a completed
    gather.
    """
    row = query_one(_SQL_LATEST_OUTPUT, (name,))
    return _decode_row(row, "payload") if row else None


def get_output_on_date(name: str, on_date: str) -> dict[str, Any] | None:
    """Return the most recent *readable* output gathered on a date (YYYY-MM-DD)."""
    row = query_one(_SQL_OUTPUT_ON_DATE, (name, on_date))
    return _decode_row(row, "payload") if row else None


def list_outputs(report_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Return saved-run metadata (no payloads), newest first.

    This is the run history: one row per run — including in-flight ``gathering``
    and ``failed`` runs — with a ``finding_count`` in place of the payload so the
    whole catalog stays cheap to browse. Filters to one report when given.
    """
    if report_name is not None:
        return query(_SQL_LIST_OUTPUTS_ONE, (report_name, limit))
    return query(_SQL_LIST_OUTPUTS_ALL, (limit,))


def get_output_meta(output_id: int) -> dict[str, Any] | None:
    """Return one output's metadata (no payload) by id, or None."""
    return query_one(_SQL_OUTPUT_META_BY_ID, (output_id,))


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
    row = query_one(_SQL_OUTPUT_BY_ID, (output_id,))
    return _decode_row(row, "payload") if row else None


def get_output_by_run_id(run_id: str) -> dict[str, Any] | None:
    """Return a single output by its run_id, or None."""
    row = query_one(_SQL_OUTPUT_BY_RUN_ID, (run_id,))
    return _decode_row(row, "payload") if row else None


def _decode_row(row: dict[str, Any], json_column: str) -> dict[str, Any]:
    """Return a copy of row with json_column parsed from JSON text to a value."""
    decoded = dict(row)
    raw = decoded.get(json_column)
    decoded[json_column] = json.loads(raw) if raw else None
    return decoded
