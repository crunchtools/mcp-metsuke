"""MCP server registration for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .models import (
    DeleteOutputParams,
    GetOutputParams,
    GetSpecParams,
    ListOutputsParams,
    PruneOutputsParams,
    SaveOutputParams,
    Status,
    TriggerReportParams,
    UpsertDefinitionParams,
)
from .tools import (
    delete_output,
    get_output,
    get_spec,
    list_outputs,
    list_reports,
    prune_outputs,
    save_output,
    trigger_report,
    upsert_definition,
)

mcp = FastMCP(
    "mcp-metsuke-crunchtools",
    version="0.5.0",
    instructions=(
        "Stateful reports catalog with a built-in scheduler and run lifecycle. "
        "Metsuke stores report DEFINITIONS (what to gather, which agent owns the "
        "gather, and the cron schedule) and their gathered OUTPUTS (findings, "
        "each carrying a source URL for citation). Firing a definition — on its "
        "schedule or on demand via trigger_report — opens a RUN: Metsuke records "
        "a provisional row immediately (so a fire is never lost), hands the gatherer "
        "a run_id, and lets only one run per report be in flight at a time. The "
        "gatherer reads the spec with get_spec, sweeps the sources, and completes "
        "its run by calling save_output with that run_id; a compiler later reads "
        "the freshest completed output with get_output to draft a cited report."
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
    run_id: str | None = None,
) -> dict[str, Any]:
    """Persist a gathered report output, completing the run that opened it.

    The gatherer writes findings here after sweeping the sources. Each finding
    in payload should carry its own source URL so the compiler can cite it. Pass
    the run_id Metsuke handed you in the fire callback so this completes that
    exact in-flight run (one row per fire). Omit run_id for an ad-hoc direct
    save; Metsuke stamps a fresh run identity either way.

    Args:
        report_name: The report definition this output belongs to
        payload: List of finding objects, each ideally carrying a source URL
        window_start: Start of the reporting window (ISO date/datetime)
        window_end: End of the reporting window (ISO date/datetime)
        status: One of "gathering", "ready", "compiled", "failed" (default: "ready")
        gatherer_run_ref: Opaque reference to the gatherer run that produced this
        run_id: The run to complete (from the fire callback); None = direct save
    """
    params = SaveOutputParams(
        report_name=report_name,
        payload=payload,
        window_start=window_start,
        window_end=window_end,
        status=status,
        gatherer_run_ref=gatherer_run_ref,
        run_id=run_id,
    )
    return await save_output(
        params.report_name,
        params.payload,
        params.window_start,
        params.window_end,
        params.status,
        params.gatherer_run_ref,
        params.run_id,
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


@mcp.tool()
async def list_outputs_tool(
    report_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List saved report outputs (the run history), newest first.

    Returns one lightweight row per saved gather — id, report_name, gathered_at,
    reporting window, status, and a finding_count — but NOT the payloads, so the
    full history stays cheap to browse. Use get_output_tool to pull one run's
    findings, and delete_output_tool / prune_outputs_tool to prune.

    Args:
        report_name: Optional report to filter to; None lists across all reports
        limit: Max rows to return, newest first (default 50, max 500)
    """
    params = ListOutputsParams(report_name=report_name, limit=limit)
    return await list_outputs(params.report_name, params.limit)


@mcp.tool()
async def delete_output_tool(output_id: int) -> dict[str, Any]:
    """Delete one saved report output by id.

    Returns the deleted output's metadata (with deleted=True). Find ids with
    list_outputs_tool. Raises if no output carries that id.

    Args:
        output_id: The id of the output to delete (from list_outputs_tool)
    """
    params = DeleteOutputParams(output_id=output_id)
    return await delete_output(params.output_id)


@mcp.tool()
async def prune_outputs_tool(
    report_name: str,
    keep_last: int | None = None,
    before_date: str | None = None,
) -> dict[str, Any]:
    """Bulk-prune a report's saved outputs. Returns the count and ids deleted.

    Give exactly one criterion: keep_last retains the N most recent outputs and
    deletes the rest (keep_last=0 deletes them all); before_date deletes every
    output gathered strictly before that date.

    Args:
        report_name: The report whose outputs to prune
        keep_last: Retain this many newest outputs, delete older ones
        before_date: Delete outputs gathered before this date (YYYY-MM-DD)
    """
    params = PruneOutputsParams(
        report_name=report_name,
        keep_last=keep_last,
        before_date=before_date,
    )
    return await prune_outputs(params.report_name, params.keep_last, params.before_date)
