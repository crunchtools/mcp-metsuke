# Spec 001: Built-in Scheduler + Trigger

> **Status:** Accepted
> **Created:** 2026-08-29
> **Supersedes:** the Scheduling non-goal in Spec 000

## Overview

Release 0.3.0 makes report firing a first-class, API-driven feature of Metsuke
itself. Spec 000 deferred scheduling to an external systemd timer and treated
the `schedule` field as descriptive only. This spec reverses that: Metsuke owns
its own schedule. A background scheduler reads each definition's cron schedule
and fires the gather callback when a slot comes due, and a new `trigger_report`
tool fires one on demand. Tool count grows from 5 to 6 (definitions: 3 → 4).

## Firing Path

Metsuke never signs or forwards the gather itself. When a report is due (or
triggered), Metsuke POSTs `{"report": <name>}` to the Trentina alert endpoint
(`{TRENTINA_ALERT_URL}/alert/{METSUKE_ALERT_TOKEN}`). Trentina resolves the
reports profile by that alert token, HMAC-signs the body, and forwards it to the
owning gatherer's webhook. Metsuke therefore holds only the alert URL and token,
never the HMAC forward secret.

## New Tool

### Definitions (now 4)
- `trigger_report` — fire a report gather now, without waiting for its schedule.
  Requires the callback to be configured; returns `{report, dispatched, status_code}`.

`list_reports` now enriches each definition with `next_fire_at`, and
`upsert_definition` accepts a live cron `schedule` plus an IANA `timezone`.

## Data Model Changes

`report_definitions` gains two columns (applied to pre-existing DBs via guarded
`ALTER TABLE ADD COLUMN` migrations):
- `timezone` (TEXT, NOT NULL, default `UTC`) — IANA zone the schedule runs in
- `last_fired_at` (TEXT, nullable) — last slot the scheduler fired, for dedup

The `schedule` field is now a live 5-field cron expression, validated on upsert.

## Scheduler Semantics

- Runs as a daemon thread under the `sse` and `streamable-http` transports only,
  never under `stdio` (short-lived, per-session).
- Polls every `METSUKE_SCHEDULER_POLL_SECONDS` (default 60) on its own SQLite
  connection, independent of the MCP request path.
- Fires at most once per cron slot during a continuous run, and never replays a
  slot that elapsed before the scheduler started (restart-safe), using
  `last_fired_at` plus a startup guard.
- Naive `last_fired_at` values are treated as UTC.

## Environment Variables

- `TRENTINA_ALERT_URL` — base URL of the Trentina alert endpoint
- `METSUKE_ALERT_TOKEN` (+ `_FILE`) — alert token for the reports profile
- `METSUKE_SCHEDULER_POLL_SECONDS` (default `60`) — poll interval
- `METSUKE_SCHEDULER_ENABLED` — force on/off; defaults on when the callback is configured

## Architecture

- `scheduler.py` — `next_fire_at` (informational), `_is_due` (slot/dedup logic),
  `_post_alert` (callback dispatch), `trigger_now` (manual path), and the poll loop
- `config.py` — adds `trentina_alert_url`, `alert_token` (SecretStr), poll interval,
  and `scheduler_enabled` (override, else derived from `callback_configured`)
- `database.py` — `new_connection` (for the scheduler thread), `list_scheduled`,
  `set_last_fired`, and the column migrations

## Non-Goals (0.3.0)

- Sub-minute scheduling — cron granularity is one minute.
- Retry/backoff on dispatch failure — a failed fire is logged; the next due slot
  or a manual `trigger_report` recovers it.
