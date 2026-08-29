"""SQLite persistence for mcp-metsuke-crunchtools.

Two tables: report_definitions (what to gather) and report_outputs (what was
gathered). Plain stdlib sqlite3 — no external services, no vector extensions.
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
    source_config TEXT,
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


def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Get or create the singleton database connection."""
    global _db
    if _db is None:
        path = db_path or get_config().db_path
        if path != ":memory:":
            get_config().ensure_db_dir()
        _db = sqlite3.connect(path)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA foreign_keys=ON")
        _db.executescript(SCHEMA)
        _db.commit()
    return _db


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
    rows = query(
        "SELECT name, gather_prompt, owner_agent, schedule, source_config, "
        "updated_at FROM report_definitions ORDER BY updated_at DESC"
    )
    return [_decode_row(row, "source_config") for row in rows]


def get_definition(name: str) -> dict[str, Any] | None:
    """Return a single report definition, or None."""
    row = query_one(
        "SELECT name, gather_prompt, owner_agent, schedule, source_config, "
        "updated_at FROM report_definitions WHERE name = ?",
        (name,),
    )
    return _decode_row(row, "source_config") if row else None


def upsert_definition(
    name: str,
    gather_prompt: str,
    owner_agent: str,
    schedule: str | None,
    source_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Insert or update a report definition. Returns the stored definition."""
    source_json = json.dumps(source_config) if source_config is not None else None
    execute(
        "INSERT INTO report_definitions "
        "(name, gather_prompt, owner_agent, schedule, source_config, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(name) DO UPDATE SET "
        "gather_prompt = excluded.gather_prompt, "
        "owner_agent = excluded.owner_agent, "
        "schedule = excluded.schedule, "
        "source_config = excluded.source_config, "
        "updated_at = datetime('now')",
        (name, gather_prompt, owner_agent, schedule, source_json),
    )
    stored = get_definition(name)
    if stored is None:  # pragma: no cover - just-written row always exists
        raise RuntimeError(f"Failed to persist definition: {name}")
    return stored


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
