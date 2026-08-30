"""Built-in report scheduler for mcp-metsuke-crunchtools.

Metsuke owns its own schedule. A background thread polls the report
definitions and, when a definition's cron schedule comes due, fires the gather
callback by POSTing ``{"report": <name>}`` to the Trentina alert endpoint.
Trentina resolves the profile by alert token, HMAC-signs the body, and forwards
it to the owning gatherer agent's webhook. Metsuke therefore needs only the
alert URL and token — never the HMAC secret.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import httpx
from croniter import croniter

from . import database as db
from .config import Config, get_config
from .errors import CallbackDispatchError, CallbackNotConfiguredError

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger("mcp_metsuke.scheduler")

_HTTP_TIMEOUT = 30.0


def next_fire_at(schedule: str | None, tzname: str, base: datetime | None = None) -> str | None:
    """Return the next scheduled fire time as an ISO string, or None.

    Purely informational — used to enrich ``list_reports`` so callers can see
    when each report will next run.
    """
    if not schedule:
        return None
    tz = ZoneInfo(tzname or "UTC")
    anchor = base or datetime.now(tz)
    nxt = cast("datetime", croniter(schedule, anchor).get_next(datetime))
    return nxt.isoformat()


async def _post_alert(client: httpx.AsyncClient, cfg: Config, name: str) -> int:
    """POST the gather callback to the Trentina alert endpoint. Returns status."""
    token = cfg.alert_token
    if not (cfg.trentina_alert_url and token):
        raise CallbackNotConfiguredError
    url = f"{cfg.trentina_alert_url}/alert/{token.get_secret_value()}"
    try:
        resp = await client.post(url, json={"report": name}, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise CallbackDispatchError(name, str(exc)) from exc
    return resp.status_code


async def trigger_now(name: str) -> int:
    """Fire a report gather immediately (the manual, API-driven path)."""
    cfg = get_config()
    async with httpx.AsyncClient() as client:
        return await _post_alert(client, cfg, name)


def _is_due(schedule: str, tzname: str, last_fired_at: str | None, started_at: datetime) -> bool:
    """Whether a scheduled slot has passed since startup and was not yet fired.

    Fires at most once per cron slot during a continuous run, and never fires a
    slot that elapsed before the scheduler started (so a restart doesn't replay
    stale slots).
    """
    tz = ZoneInfo(tzname or "UTC")
    prev_slot = croniter(schedule, datetime.now(tz)).get_prev(datetime).astimezone(UTC)
    if prev_slot <= started_at:
        return False
    if last_fired_at:
        last = datetime.fromisoformat(last_fired_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last.astimezone(UTC) >= prev_slot:
            return False
    return True


async def _tick(
    conn: sqlite3.Connection,
    cfg: Config,
    client: httpx.AsyncClient,
    started_at: datetime,
) -> None:
    """One scheduler poll: fire any due reports."""
    for row in db.list_scheduled(conn):
        name = row["name"]
        try:
            due = _is_due(row["schedule"], row["timezone"], row["last_fired_at"], started_at)
        except (ValueError, KeyError):
            logger.exception("skipping report '%s' — bad schedule/timezone", name)
            continue
        if not due:
            continue
        try:
            code = await _post_alert(client, cfg, name)
            db.set_last_fired(conn, name, datetime.now(UTC).isoformat())
            logger.info("fired scheduled report '%s' -> HTTP %s", name, code)
        except CallbackDispatchError:
            logger.exception("failed to dispatch scheduled report '%s'", name)


async def _loop() -> None:
    """Poll forever, firing due reports."""
    cfg = get_config()
    conn = db.new_connection()
    started_at = datetime.now(UTC)
    logger.info(
        "metsuke scheduler running (poll=%ss, target=%s)",
        cfg.scheduler_poll_seconds,
        cfg.trentina_alert_url,
    )
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await _tick(conn, cfg, client, started_at)
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(cfg.scheduler_poll_seconds)


def run_scheduler() -> None:
    """Blocking entry point for the scheduler thread."""
    try:
        asyncio.run(_loop())
    except Exception:
        logger.exception("metsuke scheduler crashed")
