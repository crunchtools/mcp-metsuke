"""Report-definition tools for mcp-metsuke-crunchtools."""

from __future__ import annotations

from typing import Any

from .. import database as db
from ..errors import DefinitionNotFoundError


async def metsuke_list_reports() -> list[dict[str, Any]]:
    """List all report definitions in the catalog."""
    return db.list_definitions()


async def metsuke_get_spec(name: str) -> dict[str, Any]:
    """Return the gather prompt and source config for a report definition.

    This is what the autonomous gatherer calls on callback to learn what to
    collect. Raises DefinitionNotFoundError if the report is unknown.
    """
    definition = db.get_definition(name)
    if definition is None:
        raise DefinitionNotFoundError(name)
    return definition


async def metsuke_upsert_definition(
    name: str,
    gather_prompt: str,
    owner_agent: str = "kagetora",
    schedule: str | None = None,
    source_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a report definition. Returns the stored definition."""
    return db.upsert_definition(name, gather_prompt, owner_agent, schedule, source_config)
