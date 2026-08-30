"""Tests for mcp-metsuke-crunchtools tools (in-memory SQLite)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_metsuke_crunchtools import config as config_mod
from mcp_metsuke_crunchtools import scheduler
from mcp_metsuke_crunchtools.errors import (
    CallbackNotConfiguredError,
    DefinitionNotFoundError,
    OutputNotFoundError,
)
from mcp_metsuke_crunchtools.server import mcp
from mcp_metsuke_crunchtools.tools.definitions import (
    get_spec,
    list_reports,
    trigger_report,
    upsert_definition,
)
from mcp_metsuke_crunchtools.tools.outputs import (
    get_output,
    save_output,
)

if TYPE_CHECKING:
    import sqlite3

EXPECTED_TOOL_COUNT = 6

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
        stored = await upsert_definition(
            name="core-platform-status",
            gather_prompt="Sweep sent email, Slack, calendar, Jira.",
            owner_agent="kagetora",
            schedule="0 6 * * 5",
            source_config={"sources": ["gmail", "slack"]},
        )
        assert stored["name"] == "core-platform-status"
        assert stored["owner_agent"] == "kagetora"
        assert stored["source_config"] == {"sources": ["gmail", "slack"]}

        spec = await get_spec("core-platform-status")
        assert spec["gather_prompt"].startswith("Sweep")
        assert spec["schedule"] == "0 6 * * 5"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, in_memory_db: sqlite3.Connection) -> None:
        await upsert_definition("r1", "first prompt")
        updated = await upsert_definition("r1", "second prompt", owner_agent="takeda")
        assert updated["gather_prompt"] == "second prompt"
        assert updated["owner_agent"] == "takeda"

        reports = await list_reports()
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_get_spec_missing(self, in_memory_db: sqlite3.Connection) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await get_spec("does-not-exist")

    @pytest.mark.asyncio
    async def test_list_empty(self, in_memory_db: sqlite3.Connection) -> None:
        assert await list_reports() == []

    @pytest.mark.asyncio
    async def test_default_owner_agent(self, in_memory_db: sqlite3.Connection) -> None:
        stored = await upsert_definition("r2", "prompt")
        assert stored["owner_agent"] == "kagetora"

    @pytest.mark.asyncio
    async def test_schedule_populates_next_fire_at(self, in_memory_db: sqlite3.Connection) -> None:
        stored = await upsert_definition(
            "scheduled", "prompt", schedule="0 6 * * 5", timezone="America/New_York"
        )
        assert stored["timezone"] == "America/New_York"
        assert stored["next_fire_at"] is not None

        reports = await list_reports()
        assert reports[0]["next_fire_at"] is not None

    @pytest.mark.asyncio
    async def test_unscheduled_has_no_next_fire(self, in_memory_db: sqlite3.Connection) -> None:
        stored = await upsert_definition("manual", "prompt")
        assert stored["next_fire_at"] is None


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, object], timeout: float) -> _FakeResponse:
        return _FakeResponse()


class TestTriggerReport:
    @pytest.mark.asyncio
    async def test_trigger_unknown_report(self, in_memory_db: sqlite3.Connection) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await trigger_report("nope")

    @pytest.mark.asyncio
    async def test_trigger_not_configured(
        self, in_memory_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRENTINA_ALERT_URL", raising=False)
        monkeypatch.delenv("METSUKE_ALERT_TOKEN", raising=False)
        config_mod._config = None
        await upsert_definition("core-platform-status", "gather it")
        with pytest.raises(CallbackNotConfiguredError):
            await trigger_report("core-platform-status")

    @pytest.mark.asyncio
    async def test_trigger_configured_dispatches(
        self, in_memory_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRENTINA_ALERT_URL", "http://trentina:8019")
        monkeypatch.setenv("METSUKE_ALERT_TOKEN", "test-token")
        config_mod._config = None
        monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda *_a, **_k: _FakeClient())
        await upsert_definition("core-platform-status", "gather it")
        result = await trigger_report("core-platform-status")
        assert result == {
            "report": "core-platform-status",
            "dispatched": True,
            "status_code": 200,
        }


class TestOutputTools:
    @pytest.fixture(autouse=True)
    async def _seed_definition(self, in_memory_db: sqlite3.Connection) -> None:
        await upsert_definition("core-platform-status", "gather it")

    @pytest.mark.asyncio
    async def test_save_and_get_latest(self) -> None:
        saved = await save_output(
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

        latest = await get_output("core-platform-status")
        assert latest["payload"] == SAMPLE_PAYLOAD
        assert latest["window_end"] == "2026-08-29"

    @pytest.mark.asyncio
    async def test_get_latest_returns_newest(self) -> None:
        await save_output("core-platform-status", [{"claim": "old"}])
        await save_output("core-platform-status", [{"claim": "new"}])
        latest = await get_output("core-platform-status")
        assert latest["payload"] == [{"claim": "new"}]

    @pytest.mark.asyncio
    async def test_save_output_unknown_report(self) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await save_output("nope", SAMPLE_PAYLOAD)

    @pytest.mark.asyncio
    async def test_get_output_none_yet(self) -> None:
        with pytest.raises(OutputNotFoundError):
            await get_output("core-platform-status")

    @pytest.mark.asyncio
    async def test_get_output_by_date(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        latest = await get_output("core-platform-status")
        on_date = latest["gathered_at"][:10]
        by_date = await get_output("core-platform-status", gathered_date=on_date)
        assert by_date["id"] == latest["id"]

    @pytest.mark.asyncio
    async def test_get_output_wrong_date(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        with pytest.raises(OutputNotFoundError):
            await get_output("core-platform-status", gathered_date="1999-01-01")
