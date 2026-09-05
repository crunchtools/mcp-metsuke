# Spec 002: Run Lifecycle

> **Status:** Accepted
> **Created:** 2026-09-05
> **Builds on:** Spec 001 (Built-in Scheduler + Trigger)

## Overview

Release 0.5.0 makes a report **run** a first-class, durable object so Scott can
rely on Metsuke for his Weekly/Monthly checklist work. Before this, a "run" only
existed once the gatherer chose to call `save_output`; if the gatherer crashed,
timed out, or never called back, there was no record that a run ever happened,
and two overlapping fires (scheduler + a manual `trigger_report`, or a slow
gather still running when the next slot came due) could both proceed and stomp
each other. This spec closes those three gaps:

1. **Guaranteed save** — firing a report writes a placeholder run row *before*
   the callback is dispatched, so every trigger leaves a durable record with a
   status, even if the gatherer never returns.
2. **Second-granularity identity** — each run carries a stable `run_id`
   (`<report>@<YYYYMMDDTHHMMSSZ>`, collision-suffixed) generated at fire time and
   threaded through the callback back into `save_output`, so a run is addressable
   to the second — no more date-only collapsing of same-day runs.
3. **Per-report concurrency lock** — at most one run per report may be in flight;
   a second concurrent fire is refused, race-free, at the database level.

No new tools; tool count stays 9. `save_output` gains an optional `run_id`.

## The run, on the outputs table

A run *is* a `report_outputs` row. Its lifecycle is its `status`:

```
gathering ──save_output(run_id)──▶ ready ──(compiler)──▶ compiled
    │
    └── TTL elapsed / dispatch failed ──▶ failed
```

- **`gathering`** — placeholder written at fire time (guaranteed save). Empty
  payload (`[]`). Holds the per-report lock.
- **`ready`** — the gatherer completed the run via `save_output` (findings filled
  in). This is what the compiler reads.
- **`compiled`** — a compiler has consumed it (unchanged from before).
- **`failed`** — the run did not complete: the callback dispatch failed, the run
  outlived the lock TTL (expired), or the gatherer saved with `status="failed"`.

`get_output` returns only `ready`/`compiled` rows, so the compiler never reads an
in-flight placeholder or a failed run.

## Data Model Changes

`report_outputs` gains four columns (guarded `ALTER TABLE ADD COLUMN` on existing
DBs; present in `CREATE TABLE` for fresh ones):

- `run_id` (TEXT) — second-granularity run identity
- `trigger` (TEXT) — how the run started: `scheduled`, `manual`, or `direct`
- `finished_at` (TEXT) — when the run reached a terminal status
- `detail` (TEXT) — human note for failed/expired runs

Two partial indexes (created after the column migrations):

- `UNIQUE (run_id) WHERE run_id IS NOT NULL` — run identity is unique; legacy
  NULL rows are unaffected.
- `UNIQUE (report_name) WHERE status='gathering'` — **the concurrency lock**: the
  database itself permits at most one in-flight run per report. A second
  `begin_run` fails with `IntegrityError`, surfaced as `RunInFlightError`.

## Lifecycle Semantics

- **begin_run** (scheduler `_tick` and `trigger_report`): expire this report's
  stale `gathering` rows, then insert a fresh `gathering` placeholder with a new
  `run_id`. If the lock is held by a live run, raise `RunInFlightError` (carrying
  the in-flight `run_id`). The callback body becomes `{"report", "run_id"}`.
- **complete_run** (`save_output` with `run_id`): fill payload/window/status and
  set `finished_at` on the matching `gathering` row. Unknown/already-terminal
  `run_id` → `RunNotFoundError`.
- **direct save** (`save_output` without `run_id`): if a run for this report is
  still in flight (a gatherer that completed but did not echo its `run_id`),
  **adopt** that open run — complete it in place, keeping its `run_id` and
  original `trigger` — so the concurrency lock is released rather than orphaned
  until TTL, and the fire leaves exactly one row. Only when no run is in flight
  does this insert a completed row with a freshly minted `run_id` and
  `trigger="direct"` (the pre-existing behavior, now also identity-stamped).
  Backward compatible. (Added in 0.5.1.)
- **expiry**: a `gathering` row older than `METSUKE_RUN_LOCK_TTL_SECONDS` is
  marked `failed` (`detail="expired: no save within lock TTL"`) the next time its
  report is fired, releasing the lock. This self-heals a dead gatherer.

## Environment Variables

- `METSUKE_RUN_LOCK_TTL_SECONDS` (default `1800`) — how long an in-flight run
  holds the per-report lock before it is considered stale and expired.

## Non-Goals (0.5.0)

- Retry/backoff of a failed run — a failed run is recorded; the next slot or a
  manual `trigger_report` starts a fresh run (unchanged from Spec 001).
- Cross-report (global) concurrency limits — the lock is per report only.
- A separate `runs` table — a run is a `report_outputs` row; run history is
  already browsable via `list_outputs` (now carrying `run_id`, `trigger`,
  `finished_at`, `detail`).
