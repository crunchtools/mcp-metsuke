"""MCP server registration for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .models import (
    GetOutputParams,
    GetSpecParams,
    SaveOutputParams,
    Status,
    TriggerReportParams,
    UpsertDefinitionParams,
)
from .tools import (
    get_output,
    get_spec,
    list_reports,
    save_output,
    trigger_report,
    upsert_definition,
)

mcp = FastMCP(
    "mcp-metsuke-crunchtools",
    version="0.3.0",
    instructions=(
        "Stateful reports catalog with a built-in scheduler. Metsuke stores "
        "report DEFINITIONS (what to gather, which agent owns the gather, and "
        "the cron schedule) and their gathered OUTPUTS (findings, each carrying "
        "a source URL for citation). Metsuke fires each definition when its "
        "schedule comes due (or on demand via trigger_report) by calling back "
        "the owning gatherer, which reads the spec with get_spec, sweeps the "
        "sources, and writes findings with save_output; a compiler later reads "
        "the freshest output with get_output to draft a cited report."
    ),
)


# --- Definition Tools ---


@mcp.tool()
async def list_reports_tool() -> list[dict[str, Any]]:
    """List all report definitions in the catalog.

    Returns each definition with its gather prompt, owner agent, schedule,
    source config, and last-updated time.
    """
    return await list_reports()


@mcp.tool()
async def get_spec_tool(name: str) -> dict[str, Any]:
    """Return the gather spec (prompt + source config) for a report definition.

    This is what the autonomous gatherer calls on callback to learn what to
    collect for the named report.

    Args:
        name: The report definition name (e.g. "core-platform-status")
    """
    params = GetSpecParams(name=name)
    return await get_spec(params.name)


@mcp.tool()
async def upsert_definition_tool(
    name: str,
    gather_prompt: str,
    owner_agent: str = "kagetora",
    schedule: str | None = None,
    timezone: str = "UTC",
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a report definition.

    The owner_agent field makes the gatherer identity data, not code, so
    gathering can be re-homed to another agent without a rebuild. The schedule
    is a live cron expression: Metsuke's built-in scheduler fires the report
    when it comes due, in the given timezone.

    Args:
        name: Unique report name (e.g. "core-platform-status")
        gather_prompt: The instruction the gatherer runs to collect findings
        owner_agent: Which agent gathers this report (default: "kagetora")
        schedule: Cron expression for when to fire (e.g. "0 6 * * 5"); None = manual only
        timezone: IANA timezone the schedule runs in (default: "UTC")
        source_config: Which sources to sweep, as a JSON object
    """
    params = UpsertDefinitionParams(
        name=name,
        gather_prompt=gather_prompt,
        owner_agent=owner_agent,
        schedule=schedule,
        timezone=timezone,
        source_config=source_config,
    )
    return await upsert_definition(
        params.name,
        params.gather_prompt,
        params.owner_agent,
        params.schedule,
        params.timezone,
        params.source_config,
    )


@mcp.tool()
async def trigger_report_tool(name: str) -> dict[str, Any]:
    """Fire a report gather right now, without waiting for its schedule.

    Dispatches the same callback the scheduler uses: Metsuke POSTs the trigger
    to the Trentina alert endpoint, which forwards it to the owning gatherer.
    Requires the callback to be configured (TRENTINA_ALERT_URL +
    METSUKE_ALERT_TOKEN).

    Args:
        name: The report definition name to fire (e.g. "core-platform-status")
    """
    params = TriggerReportParams(name=name)
    return await trigger_report(params.name)


# --- Output Tools ---


@mcp.tool()
async def save_output_tool(
    report_name: str,
    payload: list[dict[str, Any]],
    window_start: str | None = None,
    window_end: str | None = None,
    status: Status = "ready",
    gatherer_run_ref: str | None = None,
) -> dict[str, Any]:
    """Persist a gathered report output.

    The gatherer writes findings here after sweeping the sources. Each finding
    in payload should carry its own source URL so the compiler can cite it.

    Args:
        report_name: The report definition this output belongs to
        payload: List of finding objects, each ideally carrying a source URL
        window_start: Start of the reporting window (ISO date/datetime)
        window_end: End of the reporting window (ISO date/datetime)
        status: One of "gathering", "ready", "compiled" (default: "ready")
        gatherer_run_ref: Opaque reference to the gatherer run that produced this
    """
    params = SaveOutputParams(
        report_name=report_name,
        payload=payload,
        window_start=window_start,
        window_end=window_end,
        status=status,
        gatherer_run_ref=gatherer_run_ref,
    )
    return await save_output(
        params.report_name,
        params.payload,
        params.window_start,
        params.window_end,
        params.status,
        params.gatherer_run_ref,
    )


@mcp.tool()
async def get_output_tool(
    name: str,
    gathered_date: str | None = None,
) -> dict[str, Any]:
    """Read a gathered report output for compiling a report.

    Returns the most recent output by default, or the latest output gathered on
    a specific date when gathered_date is given.

    Args:
        name: The report definition name
        gathered_date: Optional YYYY-MM-DD to fetch that day's output instead
    """
    params = GetOutputParams(name=name, gathered_date=gathered_date)
    return await get_output(params.name, params.gathered_date)
