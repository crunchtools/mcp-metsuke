"""Report-definition tools for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from .. import database as db
from .. import scheduler
from ..config import get_config
from ..errors import CallbackNotConfiguredError, DefinitionNotFoundError


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

    Verifies the definition exists and the callback is configured, then POSTs
    the gather trigger. Raises DefinitionNotFoundError or
    CallbackNotConfiguredError. Callback dispatch failures raise
    CallbackDispatchError.
    """
    if db.get_definition(name) is None:
        raise DefinitionNotFoundError(name)
    if not get_config().callback_configured:
        raise CallbackNotConfiguredError
    status_code = await scheduler.trigger_now(name)
    return {"report": name, "dispatched": True, "status_code": status_code}
