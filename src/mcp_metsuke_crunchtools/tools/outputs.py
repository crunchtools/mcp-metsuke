"""Report-output tools for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from .. import database as db
from ..errors import (
    DefinitionNotFoundError,
    OutputIdNotFoundError,
    OutputNotFoundError,
    RunNotFoundError,
)


async def save_output(
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None = None,
    window_end: str | None = None,
    status: str = "ready",
    gatherer_run_ref: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist a gathered report output.

    The gatherer writes findings here after sweeping the sources. Each finding
    in ``payload`` should carry its own source URL so the compiler can cite it.

    When ``run_id`` is given (the run was opened by trigger/scheduler), this
    completes that in-flight run in place — filling its findings and reaching a
    terminal status — so a fired report leaves exactly one row. Without a
    ``run_id`` it inserts a fresh, identity-stamped completed run (direct save).

    Raises DefinitionNotFoundError if the report is unknown, or RunNotFoundError
    if ``run_id`` names no open run for this report.
    """
    if db.get_definition(report_name) is None:
        raise DefinitionNotFoundError(report_name)

    if run_id is not None:
        completed = db.complete_run(
            run_id, report_name, payload, window_start, window_end, status, gatherer_run_ref
        )
        if completed is None:
            raise RunNotFoundError(run_id)
        return completed

    output_id = db.insert_output(
        report_name, payload, window_start, window_end, status, gatherer_run_ref
    )
    stored = db.get_output_by_id(output_id)
    if stored is None:  # pragma: no cover - just-written row always exists
        raise OutputNotFoundError(report_name)
    return stored


async def get_output(
    name: str,
    gathered_date: str | None = None,
) -> dict[str, Any]:
    """Read a gathered report output.

    Returns the most recent output by default, or the latest output gathered on
    ``gathered_date`` (YYYY-MM-DD) when given. This is what the compiler reads
    when drafting the status email. Raises OutputNotFoundError if none exists.
    """
    if gathered_date:
        output = db.get_output_on_date(name, gathered_date)
    else:
        output = db.get_latest_output(name)

    if output is None:
        raise OutputNotFoundError(name, gathered_date)
    return output


async def list_outputs(
    report_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List saved-output metadata (no payloads), newest first.

    This is the run history: one row per saved gather, with a ``finding_count``
    instead of the payload so the whole catalog stays cheap to browse. Filters
    to one report when given. Raises DefinitionNotFoundError if a report_name
    filter names an unknown report.
    """
    if report_name is not None and db.get_definition(report_name) is None:
        raise DefinitionNotFoundError(report_name)
    return db.list_outputs(report_name, limit)


async def delete_output(output_id: int) -> dict[str, Any]:
    """Delete one saved output by id. Returns the deleted output's metadata.

    Raises OutputIdNotFoundError if no output carries that id.
    """
    deleted = db.delete_output(output_id)
    if deleted is None:
        raise OutputIdNotFoundError(output_id)
    return {**deleted, "deleted": True}


async def prune_outputs(
    report_name: str,
    keep_last: int | None = None,
    before_date: str | None = None,
) -> dict[str, Any]:
    """Bulk-delete a report's outputs, keeping the N newest or dropping those
    gathered before a date. Returns the count and ids deleted.

    Raises DefinitionNotFoundError if the report is unknown.
    """
    if db.get_definition(report_name) is None:
        raise DefinitionNotFoundError(report_name)
    deleted_ids = db.prune_outputs(report_name, keep_last, before_date)
    return {
        "report_name": report_name,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }
