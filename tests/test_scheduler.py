"""Tests for the built-in report scheduler logic."""

from __future__ import annotations

from datetime import UTC, datetime

from mcp_metsuke_crunchtools import scheduler


class TestNextFireAt:
    def test_none_schedule(self) -> None:
        assert scheduler.next_fire_at(None, "UTC") is None

    def test_empty_schedule(self) -> None:
        assert scheduler.next_fire_at("", "UTC") is None

    def test_weekly_cron(self) -> None:
        base = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
        nxt = scheduler.next_fire_at("0 6 * * 5", "UTC", base=base)
        assert nxt is not None
        parsed = datetime.fromisoformat(nxt)
        assert parsed.hour == 6
        assert parsed.weekday() == 4


class TestIsDue:
    def test_fires_new_slot(self) -> None:
        started = datetime(2026, 1, 1, tzinfo=UTC)
        assert scheduler._is_due("* * * * *", "UTC", None, started) is True

    def test_skips_when_recently_fired(self) -> None:
        started = datetime(2026, 1, 1, tzinfo=UTC)
        fired = datetime.now(UTC).isoformat()
        assert scheduler._is_due("* * * * *", "UTC", fired, started) is False

    def test_skips_slot_before_startup(self) -> None:
        started = datetime(2999, 1, 1, tzinfo=UTC)
        assert scheduler._is_due("* * * * *", "UTC", None, started) is False

    def test_naive_last_fired_treated_as_utc(self) -> None:
        started = datetime(2026, 1, 1, tzinfo=UTC)
        naive_now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        assert scheduler._is_due("* * * * *", "UTC", naive_now, started) is False
