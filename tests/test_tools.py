"""Tests for mcp-metsuke-crunchtools tools (in-memory SQLite)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_metsuke_crunchtools.errors import (
    DefinitionNotFoundError,
    OutputNotFoundError,
)
from mcp_metsuke_crunchtools.server import mcp
from mcp_metsuke_crunchtools.tools.definitions import (
    metsuke_get_spec,
    metsuke_list_reports,
    metsuke_upsert_definition,
)
from mcp_metsuke_crunchtools.tools.outputs import (
    metsuke_get_output,
    metsuke_save_output,
)

if TYPE_CHECKING:
    import sqlite3

EXPECTED_TOOL_COUNT = 5

SAMPLE_PAYLOAD = [
    {"claim": "Shipped RHEL 11 beta", "source": "https://issues.redhat.com/browse/RHEL-1"},
    {"claim": "Customer call with ACME", "source": "https://mail.google.com/mail/u/0/#all/abc"},
]


class TestToolCount:
    @pytest.mark.asyncio
    async def test_tool_count(self) -> None:
        tools = await mcp.list_tools()
        assert len(tools) == EXPECTED_TOOL_COUNT


class TestDefinitionTools:
    @pytest.mark.asyncio
    async def test_upsert_and_get_spec(self, in_memory_db: sqlite3.Connection) -> None:
        stored = await metsuke_upsert_definition(
            name="core-platform-status",
            gather_prompt="Sweep sent email, Slack, calendar, Jira.",
            owner_agent="kagetora",
            schedule="0 6 * * 5",
            source_config={"sources": ["gmail", "slack"]},
        )
        assert stored["name"] == "core-platform-status"
        assert stored["owner_agent"] == "kagetora"
        assert stored["source_config"] == {"sources": ["gmail", "slack"]}

        spec = await metsuke_get_spec("core-platform-status")
        assert spec["gather_prompt"].startswith("Sweep")
        assert spec["schedule"] == "0 6 * * 5"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, in_memory_db: sqlite3.Connection) -> None:
        await metsuke_upsert_definition("r1", "first prompt")
        updated = await metsuke_upsert_definition("r1", "second prompt", owner_agent="takeda")
        assert updated["gather_prompt"] == "second prompt"
        assert updated["owner_agent"] == "takeda"

        reports = await metsuke_list_reports()
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_get_spec_missing(self, in_memory_db: sqlite3.Connection) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await metsuke_get_spec("does-not-exist")

    @pytest.mark.asyncio
    async def test_list_empty(self, in_memory_db: sqlite3.Connection) -> None:
        assert await metsuke_list_reports() == []

    @pytest.mark.asyncio
    async def test_default_owner_agent(self, in_memory_db: sqlite3.Connection) -> None:
        stored = await metsuke_upsert_definition("r2", "prompt")
        assert stored["owner_agent"] == "kagetora"


class TestOutputTools:
    @pytest.fixture(autouse=True)
    async def _seed_definition(self, in_memory_db: sqlite3.Connection) -> None:
        await metsuke_upsert_definition("core-platform-status", "gather it")

    @pytest.mark.asyncio
    async def test_save_and_get_latest(self) -> None:
        saved = await metsuke_save_output(
            report_name="core-platform-status",
            payload=SAMPLE_PAYLOAD,
            window_start="2026-08-22",
            window_end="2026-08-29",
            status="ready",
            gatherer_run_ref="run-123",
        )
        assert saved["status"] == "ready"
        assert saved["payload"] == SAMPLE_PAYLOAD
        assert saved["gatherer_run_ref"] == "run-123"

        latest = await metsuke_get_output("core-platform-status")
        assert latest["payload"] == SAMPLE_PAYLOAD
        assert latest["window_end"] == "2026-08-29"

    @pytest.mark.asyncio
    async def test_get_latest_returns_newest(self) -> None:
        await metsuke_save_output("core-platform-status", [{"claim": "old"}])
        await metsuke_save_output("core-platform-status", [{"claim": "new"}])
        latest = await metsuke_get_output("core-platform-status")
        assert latest["payload"] == [{"claim": "new"}]

    @pytest.mark.asyncio
    async def test_save_output_unknown_report(self) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await metsuke_save_output("nope", SAMPLE_PAYLOAD)

    @pytest.mark.asyncio
    async def test_get_output_none_yet(self) -> None:
        with pytest.raises(OutputNotFoundError):
            await metsuke_get_output("core-platform-status")

    @pytest.mark.asyncio
    async def test_get_output_by_date(self) -> None:
        await metsuke_save_output("core-platform-status", SAMPLE_PAYLOAD)
        latest = await metsuke_get_output("core-platform-status")
        on_date = latest["gathered_at"][:10]
        by_date = await metsuke_get_output("core-platform-status", gathered_date=on_date)
        assert by_date["id"] == latest["id"]

    @pytest.mark.asyncio
    async def test_get_output_wrong_date(self) -> None:
        await metsuke_save_output("core-platform-status", SAMPLE_PAYLOAD)
        with pytest.raises(OutputNotFoundError):
            await metsuke_get_output("core-platform-status", gathered_date="1999-01-01")
