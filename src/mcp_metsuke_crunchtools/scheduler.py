"""Built-in report scheduler for mcp-metsuke-crunchtools.

Metsuke owns its own schedule. A background thread polls the report
definitions and, when a definition's cron schedule comes due, fires the gather
callback by POSTing the report name, run_id and gather spec to the Trentina
alert endpoint.
Trentina resolves the profile by alert token, HMAC-signs the body, and forwards
it to the owning gatherer agent's webhook. Metsuke therefore needs only the
alert URL and token — never the HMAC secret.

A definition with a ``source_config.sweep`` block is swept first: Metsuke runs
its fixed collector steps through the Trentina gateway, stores the result on the
run, and only then dispatches the callback (carrying ``sweep_status``). The
gatherer then reads pre-shaped records with get_sweep instead of calling the
sources itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast
from zoneinfo import ZoneInfo

import httpx
from croniter import croniter

from . import database as db
from .config import Config, get_config
from .errors import CallbackDispatchError, CallbackNotConfiguredError, RunInFlightError
from .sweep import run_sweep, sweep_spec_of
from .sweep.client import connect_gateway

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger("mcp_metsuke.scheduler")

_background: set[asyncio.Task[None]] = set()
MAX_DETAIL_CHARS = 500
MAX_CONCURRENT_SWEEPS = 2
# One semaphore per event loop: the scheduler thread and the MCP server each run
# their own loop, and an asyncio.Semaphore is bound to the loop that first uses it.
_sweep_slots: dict[int, asyncio.Semaphore] = {}


def _sweep_slot() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    return _sweep_slots.setdefault(loop_id, asyncio.Semaphore(MAX_CONCURRENT_SWEEPS))


_HTTP_TIMEOUT = 30.0


class GatherSpec(TypedDict):
    """The part of a definition the gather callback carries (RT #1505)."""

    gather_prompt: str
    source_config: dict[str, Any] | None


def gather_spec_of(definition: dict[str, Any]) -> GatherSpec:
    """Pick the callback fields out of a stored definition row."""
    return GatherSpec(
        gather_prompt=definition["gather_prompt"],
        source_config=definition["source_config"],
    )


def next_fire_at(schedule: str | None, tzname: str, base: datetime | None = None) -> str | None:
    """Return the next scheduled fire time as an ISO string, or None.

    Purely informational — used to enrich ``list_reports`` so callers can see
    when each report will next run.
    """
    if not schedule:
        return None
    try:
        tz = ZoneInfo(tzname or "UTC")
        anchor = base or datetime.now(tz)
        nxt = cast("datetime", croniter(schedule, anchor).get_next(datetime))
    except (ValueError, KeyError):
        return None
    return nxt.isoformat()


async def _post_alert(
    client: httpx.AsyncClient,
    cfg: Config,
    name: str,
    run_id: str | None = None,
    spec: GatherSpec | None = None,
    sweep_status: str | None = None,
) -> int:
    """POST the gather callback to the Trentina alert endpoint. Returns status.

    The body carries the run_id so the gatherer echoes it back into save_output,
    completing the exact run that this fire opened. It also carries the gather
    spec (``gather_prompt``, and ``source_config`` as a JSON string) so the
    gatherer's instructions arrive with the trigger rather than as a tool result:
    a tool result is untrusted content to Trentina, and an instruction-heavy spec
    read that way was refused by the L3 judge often enough to kill gathers
    (RT #1505). Both are strings because webhook templates render a string whole
    but truncate structured values.

    Args:
        client: HTTP client to post with.
        cfg: Config carrying the alert URL and token.
        name: Report definition name.
        run_id: The run this fire opened; omitted from the body when None.
        spec: ``{"gather_prompt", "source_config"}`` from the definition;
            omitted from the body when None.
        sweep_status: ``ready``/``partial``/``error`` when the run was swept;
            omitted when the definition has no sweep.
    """
    token = cfg.alert_token
    if not (cfg.trentina_alert_url and token):
        raise CallbackNotConfiguredError
    url = f"{cfg.trentina_alert_url}/alert/{token.get_secret_value()}"
    body: dict[str, str] = {"report": name}
    if run_id is not None:
        body["run_id"] = run_id
    if spec is not None:
        body["gather_prompt"] = spec["gather_prompt"]
        body["source_config"] = json.dumps(spec["source_config"] or {})
    if sweep_status is not None:
        body["sweep_status"] = sweep_status
    try:
        resp = await client.post(url, json=body, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise CallbackDispatchError(name, str(exc)) from exc
    return resp.status_code


def _sweep_failure(detail: str) -> dict[str, Any]:
    return {
        "status": "error",
        "generated_at": datetime.now(UTC).isoformat(),
        "window": None,
        "errors": [detail[:500]],
        "sections": {},
    }


async def sweep_run(
    run_id: str,
    source_config: dict[str, Any] | None,
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """Run the definition's sweep (if any), store it on the run, return its status.

    Returns None for a definition without a sweep. Never raises: a bad spec, a
    missing gateway config, a timeout or a transport failure are all stored as
    an ``error`` sweep so the gatherer can say which source was unavailable.
    """
    try:
        spec = sweep_spec_of(source_config)
    except ValueError as exc:
        sweep = _sweep_failure(f"invalid sweep spec: {exc}")
        db.set_sweep(run_id, sweep, conn=conn)
        return "error"
    if spec is None:
        return None
    cfg = get_config()
    token = cfg.sweep_token
    if not (cfg.sweep_configured and token):
        sweep = _sweep_failure("sweep not configured (TRENTINA_GATEWAY_URL / METSUKE_SWEEP_TOKEN)")
    else:
        try:
            async with connect_gateway(cfg.trentina_gateway_url, token.get_secret_value()) as gw:
                sweep = await asyncio.wait_for(
                    run_sweep(spec, gw), timeout=cfg.sweep_timeout_seconds
                )
        except TimeoutError:
            sweep = _sweep_failure(f"sweep timed out after {cfg.sweep_timeout_seconds}s")
        except Exception as exc:
            logger.exception("sweep for run %s failed", run_id)
            sweep = _sweep_failure(f"{type(exc).__name__}: {exc}")
    db.set_sweep(run_id, sweep, conn=conn)
    logger.info("sweep for run %s finished: %s", run_id, sweep["status"])
    return str(sweep["status"])


async def sweep_and_dispatch(
    name: str,
    run_id: str,
    definition: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    """Sweep a run, then dispatch its callback. Runs as a background task.

    Both the manual trigger and the scheduler use it, so one slow sweep never
    holds up the trigger response or other due reports. At most
    ``MAX_CONCURRENT_SWEEPS`` sweeps run at once per loop; the rest wait. A
    failure marks the run failed with the cause in its detail.
    """
    try:
        # Waiting here is bounded: the per-report in-flight lock (begin_run)
        # allows one open run per report, so queued sweeps never outnumber
        # swept definitions.
        async with _sweep_slot():
            if not db.run_is_open(run_id, conn=conn):
                logger.warning("run %s expired while queued for a sweep; dropped", run_id)
                return
            status = await sweep_run(run_id, definition.get("source_config"), conn=conn)
        if not db.run_is_open(run_id, conn=conn):
            # The lock TTL lapsed mid-sweep, so a newer run may own the report:
            # dispatching now could produce a duplicate callback.
            logger.warning("run %s expired during its sweep; not dispatching", run_id)
            return
        await trigger_now(name, run_id, gather_spec_of(definition), status)
    except CallbackDispatchError as exc:
        db.fail_run(run_id, f"callback dispatch failed: {exc}"[:MAX_DETAIL_CHARS], conn=conn)
        logger.exception("failed to dispatch report '%s' after sweep", name)
    except Exception as exc:
        detail = f"sweep/dispatch crashed: {type(exc).__name__}: {exc}"
        db.fail_run(run_id, detail[:MAX_DETAIL_CHARS], conn=conn)
        logger.exception("sweep_and_dispatch crashed for '%s'", name)


def dispatch_after_sweep(
    name: str,
    run_id: str,
    definition: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Start sweep-then-dispatch in the background if the definition has a sweep.

    Returns True when it did (the caller must not dispatch), False for a
    definition without a sweep. Shared by the scheduler and trigger_report.
    """
    if not _has_sweep(definition):
        return False
    start_background(sweep_and_dispatch(name, run_id, definition, conn=conn))
    return True


def start_background(coro: Any) -> None:
    """Run a coroutine on the current loop, holding a reference until it ends."""
    task: asyncio.Task[None] = asyncio.get_running_loop().create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def trigger_now(
    name: str,
    run_id: str | None = None,
    spec: GatherSpec | None = None,
    sweep_status: str | None = None,
) -> int:
    """Fire a report gather immediately (the manual, API-driven path).

    Args:
        name: Report definition name.
        run_id: The run this fire opened.
        spec: The definition's gather_prompt and source_config, sent with the
            callback so the gatherer does not have to read them back.
        sweep_status: The run's sweep status, when it was swept.
    """
    cfg = get_config()
    async with httpx.AsyncClient() as client:
        return await _post_alert(client, cfg, name, run_id, spec, sweep_status)


def _has_sweep(row: dict[str, Any]) -> bool:
    return "sweep" in (row.get("source_config") or {})


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
            run = db.begin_run(name, "scheduled", conn=conn)
        except RunInFlightError:
            logger.warning("skipping scheduled report '%s' — a run is already in flight", name)
            continue
        run_id = run["run_id"]
        if dispatch_after_sweep(name, run_id, row, conn=conn):
            # Sweeps take minutes: the slot is fired now and the sweep runs in
            # the background so later due reports are not held up.
            db.set_last_fired(conn, name, datetime.now(UTC).isoformat())
            logger.info("fired scheduled report '%s' (run %s) -> sweeping", name, run_id)
            continue
        try:
            code = await _post_alert(client, cfg, name, run_id, gather_spec_of(row))
            db.set_last_fired(conn, name, datetime.now(UTC).isoformat())
            logger.info("fired scheduled report '%s' (run %s) -> HTTP %s", name, run_id, code)
        except CallbackDispatchError:
            db.fail_run(run_id, "callback dispatch failed", conn=conn)
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
