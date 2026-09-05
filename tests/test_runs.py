"""Tests for the report run lifecycle (Spec 002).

Covers the three run-lifecycle guarantees: a guaranteed-save provisional row on
begin_run, second-granularity run identity, and the per-report concurrency lock
(with TTL-based self-healing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mcp_metsuke_crunchtools import config as config_mod
from mcp_metsuke_crunchtools import database as db
from mcp_metsuke_crunchtools.errors import (
    DefinitionNotFoundError,
    OutputNotFoundError,
    RunInFlightError,
    RunNotFoundError,
)
from mcp_metsuke_crunchtools.tools.definitions import upsert_definition
from mcp_metsuke_crunchtools.tools.outputs import get_output, list_outputs, save_output

if TYPE_CHECKING:
    import sqlite3

SAMPLE_PAYLOAD = [{"claim": "Shipped it", "source": "https://example.com/1"}]


class _SeededCase:
    """Base for run-lifecycle cases that need one seeded definition + a DB."""

    @pytest.fixture(autouse=True)
    async def _seed(self, in_memory_db: sqlite3.Connection) -> None:
        await upsert_definition("core-platform-status", "gather it")


class TestBeginRun(_SeededCase):
    @pytest.mark.asyncio
    async def test_provisional_row_written_before_callback(self) -> None:
        run = db.begin_run("core-platform-status", "manual")
        assert run["run_id"].startswith("core-platform-status@")
        assert run["status"] == "gathering"

        rows = await list_outputs("core-platform-status")
        assert len(rows) == 1
        assert rows[0]["status"] == "gathering"
        assert rows[0]["finding_count"] == 0
        assert rows[0]["trigger"] == "manual"

    @pytest.mark.asyncio
    async def test_run_id_is_second_granular(self) -> None:
        run = db.begin_run("core-platform-status", "manual")
        stamp = run["run_id"].split("@", 1)[1]
        assert len(stamp) == 16
        assert stamp.endswith("Z")

    @pytest.mark.asyncio
    async def test_same_second_reruns_get_distinct_ids(self) -> None:
        run1 = db.begin_run("core-platform-status", "manual")
        await save_output("core-platform-status", SAMPLE_PAYLOAD, run_id=run1["run_id"])
        run2 = db.begin_run("core-platform-status", "manual")
        assert run1["run_id"] != run2["run_id"]


class TestConcurrencyLock(_SeededCase):
    @pytest.mark.asyncio
    async def test_second_run_refused_while_in_flight(self) -> None:
        first = db.begin_run("core-platform-status", "manual")
        with pytest.raises(RunInFlightError) as exc:
            db.begin_run("core-platform-status", "scheduled")
        assert first["run_id"] in str(exc.value)

    @pytest.mark.asyncio
    async def test_lock_released_after_completion(self) -> None:
        first = db.begin_run("core-platform-status", "manual")
        await save_output("core-platform-status", SAMPLE_PAYLOAD, run_id=first["run_id"])
        second = db.begin_run("core-platform-status", "manual")
        assert second["run_id"] != first["run_id"]

    @pytest.mark.asyncio
    async def test_lock_is_per_report(self) -> None:
        await upsert_definition("weekend-report", "gather it too")
        db.begin_run("core-platform-status", "manual")
        other = db.begin_run("weekend-report", "manual")
        assert other["status"] == "gathering"

    @pytest.mark.asyncio
    async def test_stale_run_expires_and_frees_lock(self) -> None:
        config_mod.get_config().run_lock_ttl_seconds = 0
        stale = db.begin_run("core-platform-status", "manual")
        fresh = db.begin_run("core-platform-status", "scheduled")
        assert fresh["run_id"] != stale["run_id"]

        rows = await list_outputs("core-platform-status")
        by_id = {row["run_id"]: row for row in rows}
        assert by_id[stale["run_id"]]["status"] == "failed"
        assert "expired" in by_id[stale["run_id"]]["detail"]


class TestCompleteRun(_SeededCase):
    @pytest.mark.asyncio
    async def test_save_with_run_id_completes_in_place(self) -> None:
        run = db.begin_run("core-platform-status", "manual")
        completed = await save_output(
            "core-platform-status",
            SAMPLE_PAYLOAD,
            window_start="2026-08-29",
            window_end="2026-09-05",
            run_id=run["run_id"],
        )
        assert completed["status"] == "ready"
        assert completed["payload"] == SAMPLE_PAYLOAD
        assert completed["run_id"] == run["run_id"]
        assert completed["finished_at"] is not None

        rows = await list_outputs("core-platform-status")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_save_unknown_run_id_raises(self) -> None:
        with pytest.raises(RunNotFoundError):
            await save_output(
                "core-platform-status", SAMPLE_PAYLOAD, run_id="core-platform-status@nope"
            )

    @pytest.mark.asyncio
    async def test_save_run_id_wrong_report_raises(self) -> None:
        await upsert_definition("weekend-report", "gather it too")
        run = db.begin_run("core-platform-status", "manual")
        with pytest.raises(RunNotFoundError):
            await save_output("weekend-report", SAMPLE_PAYLOAD, run_id=run["run_id"])

    @pytest.mark.asyncio
    async def test_direct_save_still_works(self) -> None:
        saved = await save_output("core-platform-status", SAMPLE_PAYLOAD)
        assert saved["status"] == "ready"
        assert saved["trigger"] == "direct"
        assert saved["run_id"].startswith("core-platform-status@")

    @pytest.mark.asyncio
    async def test_run_id_less_save_adopts_inflight_run(self) -> None:
        # A fire opens a run; the gatherer completes but forgets to echo run_id.
        opened = db.begin_run("core-platform-status", "manual")
        completed = await save_output("core-platform-status", SAMPLE_PAYLOAD)

        # The open run is adopted (not orphaned): same run_id, original trigger,
        # and exactly one row — no duplicate direct save alongside a stuck row.
        assert completed["run_id"] == opened["run_id"]
        assert completed["status"] == "ready"
        assert completed["trigger"] == "manual"
        assert completed["payload"] == SAMPLE_PAYLOAD
        rows = await list_outputs("core-platform-status")
        assert len(rows) == 1

        # The lock is released, so the report can be fired again immediately.
        nxt = db.begin_run("core-platform-status", "scheduled")
        assert nxt["run_id"] != opened["run_id"]


class TestGetOutputSkipsIncomplete(_SeededCase):
    @pytest.mark.asyncio
    async def test_get_output_ignores_inflight_run(self) -> None:
        db.begin_run("core-platform-status", "manual")
        with pytest.raises(OutputNotFoundError):
            await get_output("core-platform-status")

    @pytest.mark.asyncio
    async def test_get_output_ignores_failed_run(self) -> None:
        run = db.begin_run("core-platform-status", "manual")
        db.fail_run(run["run_id"], "boom")
        with pytest.raises(OutputNotFoundError):
            await get_output("core-platform-status")

    @pytest.mark.asyncio
    async def test_get_output_returns_completed_over_newer_inflight(self) -> None:
        first = db.begin_run("core-platform-status", "manual")
        await save_output("core-platform-status", SAMPLE_PAYLOAD, run_id=first["run_id"])
        db.begin_run("core-platform-status", "scheduled")
        latest = await get_output("core-platform-status")
        assert latest["run_id"] == first["run_id"]
        assert latest["payload"] == SAMPLE_PAYLOAD


class TestSaveOutputGuards(_SeededCase):
    @pytest.mark.asyncio
    async def test_save_unknown_report(self) -> None:
        with pytest.raises(DefinitionNotFoundError):
            await save_output("nope", SAMPLE_PAYLOAD)
