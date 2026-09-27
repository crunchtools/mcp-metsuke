"""Reporting windows, computed in code so no gatherer has to reason about dates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

SATURDAY = 5


class Window(NamedTuple):
    """A reporting window, both ends timezone-aware."""

    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, str]:
        """``{"start", "end"}`` as ISO-8601 strings with offsets, for storage."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


def previous_weekday_at(now: datetime, tzname: str, hour: int = 6) -> Window:
    """Window from ``hour``:00 local on the previous weekday until ``now``.

    Monday's window starts Friday, so the weekend is covered; a run on a
    Saturday or Sunday also reaches back to Friday.
    """
    local = now.astimezone(ZoneInfo(tzname))
    day = local.date() - timedelta(days=1)
    while day.weekday() >= SATURDAY:
        day -= timedelta(days=1)
    start = datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo(tzname))
    return Window(start=start, end=local)


def next_weekday(now: datetime, tzname: str) -> datetime:
    """Midnight local of today, or of the next weekday when today is a weekend."""
    local = now.astimezone(ZoneInfo(tzname))
    day = local.date()
    while day.weekday() >= SATURDAY:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tzname))
