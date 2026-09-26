"""Tests for mcp-metsuke-crunchtools tools (in-memory SQLite)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, cast

import pytest

from mcp_metsuke_crunchtools import config as config_mod
from mcp_metsuke_crunchtools import scheduler
from mcp_metsuke_crunchtools.errors import (
    CallbackNotConfiguredError,
    DefinitionNotFoundError,
    OutputIdNotFoundError,
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
    delete_output,
    get_output,
    list_outputs,
    prune_outputs,
    save_output,
)

if TYPE_CHECKING:
    import sqlite3

    import httpx

EXPECTED_TOOL_COUNT = 9

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
    posted: ClassVar[list[dict[str, object]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, object], timeout: float) -> _FakeResponse:
        _FakeClient.posted.append(json)
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
        assert result["report"] == "core-platform-status"
        assert result["dispatched"] is True
        assert result["status_code"] == 200
        assert result["run_id"].startswith("core-platform-status@")

        rows = await list_outputs("core-platform-status")
        assert len(rows) == 1
        assert rows[0]["status"] == "gathering"
        assert rows[0]["run_id"] == result["run_id"]
        assert rows[0]["trigger"] == "manual"

    @pytest.mark.asyncio
    async def test_trigger_carries_spec_and_run_id(
        self, in_memory_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # RT #1505: the spec rides with the trigger, as strings, with the run_id.
        monkeypatch.setenv("TRENTINA_ALERT_URL", "http://trentina:8019")
        monkeypatch.setenv("METSUKE_ALERT_TOKEN", "test-token")
        config_mod._config = None
        monkeypatch.setattr(scheduler.httpx, "AsyncClient", lambda *_a, **_k: _FakeClient())
        _FakeClient.posted.clear()
        await upsert_definition("weekend-report", "gather it", source_config={"window_days": 7})
        result = await trigger_report("weekend-report")
        body = _FakeClient.posted[-1]
        assert body["report"] == "weekend-report"
        assert body["run_id"] == result["run_id"]
        assert body["gather_prompt"] == "gather it"
        assert json.loads(cast("str", body["source_config"])) == {"window_days": 7}


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


class TestOutputHistoryTools:
    @pytest.fixture(autouse=True)
    async def _seed_definitions(self, in_memory_db: sqlite3.Connection) -> None:
        await upsert_definition("core-platform-status", "gather it")
        await upsert_definition("weekend-report", "gather it too")

    @pytest.mark.asyncio
    async def test_list_outputs_excludes_payload(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        rows = await list_outputs("core-platform-status")
        assert len(rows) == 1
        assert "payload" not in rows[0]
        assert rows[0]["finding_count"] == len(SAMPLE_PAYLOAD)

    @pytest.mark.asyncio
    async def test_list_outputs_newest_first_and_limit(self) -> None:
        for _ in range(3):
            await save_output("core-platform-status", SAMPLE_PAYLOAD)
        rows = await list_outputs("core-platform-status", limit=2)
        assert len(rows) == 2
        assert rows[0]["id"] > rows[1]["id"]

    @pytest.mark.asyncio
    async def test_list_outputs_filters_by_report(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        await save_output("weekend-report", SAMPLE_PAYLOAD)
        assert len(await list_outputs("core-platform-status")) == 1
        assert len(await list_outputs()) == 2

    @pytest.mark.asyncio
    async def test_list_outputs_unknown_report(self) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await list_outputs("nope")

    @pytest.mark.asyncio
    async def test_delete_output(self) -> None:
        saved = await save_output("core-platform-status", SAMPLE_PAYLOAD)
        deleted = await delete_output(saved["id"])
        assert deleted["deleted"] is True
        assert deleted["id"] == saved["id"]
        assert await list_outputs("core-platform-status") == []

    @pytest.mark.asyncio
    async def test_delete_output_missing(self) -> None:
        with pytest.raises(OutputIdNotFoundError):
            await delete_output(9999)

    @pytest.mark.asyncio
    async def test_prune_keep_last(self) -> None:
        for _ in range(4):
            await save_output("core-platform-status", SAMPLE_PAYLOAD)
        result = await prune_outputs("core-platform-status", keep_last=1)
        assert result["deleted_count"] == 3
        assert len(await list_outputs("core-platform-status")) == 1

    @pytest.mark.asyncio
    async def test_prune_keep_last_zero_deletes_all(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        result = await prune_outputs("core-platform-status", keep_last=0)
        assert result["deleted_count"] == 1
        assert await list_outputs("core-platform-status") == []

    @pytest.mark.asyncio
    async def test_prune_before_date(self) -> None:
        await save_output("core-platform-status", SAMPLE_PAYLOAD)
        future = "2999-01-01"
        result = await prune_outputs("core-platform-status", before_date=future)
        assert result["deleted_count"] == 1
        assert await list_outputs("core-platform-status") == []

    @pytest.mark.asyncio
    async def test_prune_unknown_report(self) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await prune_outputs("nope", keep_last=1)


class TestSaveOutputToolWiring:
    @pytest.mark.asyncio
    async def test_forwards_findings_and_unset_optionals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # RT #1505: what the tool hands storage after validation, not just the model.
        from mcp_metsuke_crunchtools import server

        captured: dict[str, object] = {}

        async def fake_save_output(*args: object) -> dict[str, object]:
            captured["args"] = args
            return {"ok": True}

        monkeypatch.setattr(server, "save_output", fake_save_output)
        tool = await server.mcp.get_tool("save_output_tool")
        await tool.fn(
            report_name="weekend-report",
            payload=[{"summary": "s", "source_url": "https://e", "theme": None}],
            window_start="",
            window_end="2026-09-26",
            gatherer_run_ref=" ",
            run_id="",
        )
        assert captured["args"] == (
            "weekend-report",
            [{"summary": "s", "source_url": "https://e", "theme": None}],
            None,
            "2026-09-26",
            "ready",
            None,
            None,
        )

    @pytest.mark.asyncio
    async def test_registered_schema_declares_finding_fields(self) -> None:
        # What a tool-calling model actually sees over MCP.
        from mcp_metsuke_crunchtools import server

        tool = await server.mcp.get_tool("save_output_tool")
        schema = tool.parameters
        assert schema["properties"]["payload"]["items"] == {"$ref": "#/$defs/Finding"}
        finding = schema["$defs"]["Finding"]
        assert finding["required"] == ["summary"]
        assert finding["additionalProperties"] is False
        assert {"summary", "source_url", "section", "theme", "actors"} <= set(finding["properties"])
        assert all("description" in prop for prop in finding["properties"].values())


class TestScheduledCallbackSpec:
    def test_get_gather_spec(self, in_memory_db: sqlite3.Connection) -> None:
        from mcp_metsuke_crunchtools import database as db

        db.upsert_definition("r", "the prompt", "kagetora", "0 9 * * 6", "UTC", {"k": 1})
        assert db.get_gather_spec(in_memory_db, "r") == {
            "gather_prompt": "the prompt",
            "source_config": {"k": 1},
        }
        assert db.get_gather_spec(in_memory_db, "missing") is None

    @pytest.mark.asyncio
    async def test_post_alert_without_spec_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRENTINA_ALERT_URL", "http://trentina:8019")
        monkeypatch.setenv("METSUKE_ALERT_TOKEN", "test-token")
        config_mod._config = None
        _FakeClient.posted.clear()
        client = _FakeClient()
        await scheduler._post_alert(
            cast("httpx.AsyncClient", client), config_mod.get_config(), "r", "r@1"
        )
        assert _FakeClient.posted[-1] == {"report": "r", "run_id": "r@1"}

    @pytest.mark.asyncio
    async def test_scheduled_tick_sends_spec(
        self, in_memory_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The automated path, not just the manual trigger.
        from mcp_metsuke_crunchtools import database as db

        monkeypatch.setenv("TRENTINA_ALERT_URL", "http://trentina:8019")
        monkeypatch.setenv("METSUKE_ALERT_TOKEN", "test-token")
        config_mod._config = None
        monkeypatch.setattr(scheduler, "_is_due", lambda *_a: True)
        db.upsert_definition("r", "the prompt", "kagetora", "0 9 * * 6", "UTC", {"k": 1})
        _FakeClient.posted.clear()
        await scheduler._tick(
            in_memory_db,
            config_mod.get_config(),
            cast("httpx.AsyncClient", _FakeClient()),
            datetime.now(UTC),
        )
        body = _FakeClient.posted[-1]
        assert body["report"] == "r"
        assert cast("str", body["run_id"]).startswith("r@")
        assert body["gather_prompt"] == "the prompt"
        assert json.loads(cast("str", body["source_config"])) == {"k": 1}

    def test_gather_spec_of_picks_callback_fields(self) -> None:
        row = {"name": "r", "gather_prompt": "p", "source_config": None, "schedule": "x"}
        assert scheduler.gather_spec_of(row) == {"gather_prompt": "p", "source_config": None}
