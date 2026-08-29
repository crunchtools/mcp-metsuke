"""Report-output tools for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from .. import database as db
from ..errors import DefinitionNotFoundError, OutputNotFoundError


async def save_output(
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None = None,
    window_end: str | None = None,
    status: str = "ready",
    gatherer_run_ref: str | None = None,
) -> dict[str, Any]:
    """Persist a gathered report output.

    The gatherer writes findings here after sweeping the sources. Each finding
    in ``payload`` should carry its own source URL so the compiler can cite it.
    Raises DefinitionNotFoundError if the report is unknown.
    """
    if db.get_definition(report_name) is None:
        raise DefinitionNotFoundError(report_name)

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
