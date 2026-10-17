"""Report-definition tools for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from .. import database as db
from .. import scheduler
from ..config import get_config
from ..errors import (
    CallbackDispatchError,
    CallbackNotConfiguredError,
    DefinitionNotFoundError,
)


async def list_reports() -> list[dict[str, Any]]:
    """List all report definitions, each enriched with its next fire time."""
    definitions = db.list_definitions()
    for definition in definitions:
        definition["next_fire_at"] = scheduler.next_fire_at(
            definition.get("schedule"), definition.get("timezone") or "UTC"
        )
    return definitions


async def get_spec(name: str) -> dict[str, Any]:
    """Return the gather prompt and source config for a report definition.

    This is what the autonomous gatherer calls on callback to learn what to
    collect. Raises DefinitionNotFoundError if the report is unknown.
    """
    definition = db.get_definition(name)
    if definition is None:
        raise DefinitionNotFoundError(name)
    return definition


async def upsert_definition(
    name: str,
    gather_prompt: str,
    owner_agent: str = "kagetora",
    schedule: str | None = None,
    timezone: str = "UTC",
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a report definition. Returns the stored definition."""
    stored = db.upsert_definition(
        name, gather_prompt, owner_agent, schedule, timezone, source_config
    )
    stored["next_fire_at"] = scheduler.next_fire_at(
        stored.get("schedule"), stored.get("timezone") or "UTC"
    )
    return stored


async def trigger_report(name: str) -> dict[str, Any]:
    """Fire a report gather now via the Trentina alert callback.

    Verifies the definition exists and the callback is configured, opens a run
    (guaranteed-save provisional row + per-report concurrency lock), then POSTs the
    gather trigger with the run's ``run_id`` so the gatherer completes that same
    run via save_output. Raises DefinitionNotFoundError,
    CallbackNotConfiguredError, or RunInFlightError (a run is already in flight).
    A dispatch failure marks the run failed and raises CallbackDispatchError.

    A definition with a sweep returns immediately with ``dispatched:
    "after_sweep"``: the sweep runs in the background and the callback follows
    it. Watch the run with get_sweep.

    A definition owned by a pull owner (``METSUKE_PULL_OWNERS``) needs no
    callback: the run is opened, swept if it has a sweep, and left for the owner
    to collect (``dispatched: "pull"``).
    """
    definition = db.get_definition(name)
    if definition is None:
        raise DefinitionNotFoundError(name)
    pull = scheduler.is_pull(definition)
    if not pull and not get_config().callback_configured:
        raise CallbackNotConfiguredError
    run = db.begin_run(name, "manual")
    run_id = run["run_id"]
    swept = scheduler.dispatch_after_sweep(name, run_id, definition)
    if pull:
        return {"report": name, "run_id": run_id, "dispatched": "pull", "sweep": swept}
    if swept:
        return {"report": name, "run_id": run_id, "dispatched": "after_sweep", "sweep": True}
    try:
        status_code = await scheduler.trigger_now(
            name, run_id, scheduler.gather_spec_of(definition)
        )
    except CallbackDispatchError:
        db.fail_run(run_id, "callback dispatch failed")
        raise
    return {"report": name, "run_id": run_id, "dispatched": True, "status_code": status_code}
