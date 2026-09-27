# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

Entries prior to 2026-09-19 are back-filled from GitHub Release notes (RT #1484).

## [Unreleased]

## [1.2.1] - 2026-09-27

### Fixed
- Pin `fastmcp>=3.4,<4` and `mcp>=1.29,<2`. The container installs with plain
  pip rather than from `uv.lock`, so the 1.2.0 image resolved fastmcp 4.0.10 /
  mcp 2.2.0, where `mcp.shared.exceptions.McpError` no longer exists, and
  crash-looped on import. Rolled back to 1.1.0 within minutes; no data touched.
- CI's container check now imports the server inside the built image; it used
  to end in `|| true` and could not fail.

## [1.2.0] - 2026-09-27

### Added
- Sweep stage. A definition with `source_config.sweep` is gathered by Metsuke
  before the callback: fixed collector steps (`slack_waiting`, `gmail_waiting`,
  `calendar_day`, `feed_entries`) run sequentially through the Trentina gateway
  as a read-only `metsuke-sweep` profile, and their compact records are stored
  on the run. The callback carries `sweep_status`, and the gatherer reads
  records with the new `get_sweep_tool` instead of calling the sources itself.
  The daily-briefing test runs of 2026-09-27 showed why: a gathering LLM left
  to drive the sweep drifted every run, fired unbounded or refused calls, and
  tripped its MCP client's breaker (RT #1469, RT #1505).
- Reply-state logic in code: Slack threads and DMs are read up to a three-page
  cap (longer or partly unreadable ones are marked `unverified`), an ask the
  user already answered is dropped, and a reaction-only acknowledgement is kept.
  Email uses the backend's own ownership analysis.
- Results Trentina flags are stored as metadata only, text withheld.
- `TRENTINA_GATEWAY_URL`, `METSUKE_SWEEP_TOKEN` (+ `_FILE`),
  `METSUKE_SWEEP_TIMEOUT_SECONDS`.
- `report_outputs.sweep_status` and `sweep_data` columns (additive migration).

### Changed
- `trigger_report_tool` on a swept definition returns immediately with
  `dispatched: "after_sweep"`; the sweep and dispatch run in the background.
  The scheduler does the same for swept definitions, so one slow sweep never
  holds up other due reports.
- The sweep refuses to send its bearer token over plain HTTP to anything but an
  internal host (single-label service name, localhost, private IP).
- `upsert_definition_tool` rejects an invalid sweep spec at save time.

## [1.1.0] - 2026-09-26

### Added
- The gather callback carries the definition's `gather_prompt` and
  `source_config` (as a JSON string) alongside `report` and `run_id`, on both
  the scheduled and the manual path. The gatherer gets its instructions with
  the trigger instead of reading them back through `get_spec_tool`, where
  Trentina judges them as untrusted content; an instruction-heavy spec was
  refused by the L3 judge often enough to kill gathers (RT #1505).

## [1.0.0] - 2026-09-26

### Changed
- `save_output_tool` payload items are a typed `Finding` (`summary` required and
  non-blank; `source_url`, `section`, `theme`, `title`, `category`, `source_type`,
  `date`, `actors`, `outcome_ref` optional; unknown keys rejected). A bare
  `object` item schema led strict tool-calling models to save `[{}, {}]` as a
  "ready" report (RT #1505). Empty findings are now rejected. **Breaking:**
  payloads without a `summary`, or with keys outside that set, no longer save.
- Blank `run_id`, `gatherer_run_ref`, `window_start` and `window_end` mean
  "not given" instead of a lookup for a run named "".

## [0.5.0] - 2026-09-05

Run lifecycle (Spec 002): a report *run* is now a durable, addressable entity.
There is no `v0.4.0` tag; the release notes describe the bump as 0.4.0 → 0.5.0.

### Added
- **Guaranteed save** — a `gathering` row is written before the gather callback
  dispatches; a fire is never lost even if the gatherer never calls back.
- **Second-granularity identity** — each run gets a `run_id`
  (`<report>@<YYYYMMDDTHHMMSSZ>`), suffixed on same-second collisions.
- **Per-report concurrency lock** — a partial unique index
  (`WHERE status='gathering'`) refuses a second in-flight run per report, with a
  self-healing TTL (`METSUKE_RUN_LOCK_TTL_SECONDS`, default 1800s).
- New `report_outputs` columns: `run_id`, `trigger`, `finished_at`, `detail`.

### Changed
- `save_output` accepts `run_id` to complete an open run. Schema migrations
  auto-apply on startup and are safe on existing databases.

## [0.3.1] - 2026-08-30

### Fixed
- Hardened `next_fire_at` against invalid stored schedules/timezones so a legacy
  descriptive schedule can never crash `list_reports`. No API changes.

## [0.3.0] - 2026-08-30

Metsuke now owns its own schedule. A background daemon thread reads each
definition's cron schedule and fires the gather callback when a slot comes due —
POSTing `{"report": <name>}` to the Trentina alert endpoint, which HMAC-signs and
forwards it to the owning gatherer. This replaces the external systemd timer
entirely: firing is API-driven, all in Metsuke.

### Added
- New `trigger_report` tool for on-demand firing (tools 5 → 6).
- `report_definitions` gains `timezone` and `last_fired_at` (guarded migrations
  for existing DBs).
- Live cron `schedule` and IANA `timezone` validated on upsert.
- New environment variables: `TRENTINA_ALERT_URL`, `METSUKE_ALERT_TOKEN`
  (+ `_FILE`), `METSUKE_SCHEDULER_POLL_SECONDS` (default 60),
  `METSUKE_SCHEDULER_ENABLED` (defaults on when a callback is configured).

### Changed
- `list_reports` enriches each definition with `next_fire_at`.
- Scheduler runs only under `sse`/`streamable-http`, never `stdio`.
- Restart-safe: at most one fire per cron slot, no stale-slot replay.
- Spec `001-builtin-scheduler` supersedes the Scheduling non-goal in Spec 000.

## [0.2.0] - 2026-08-29

### Changed
- **Breaking rename** (safe pre-consumer): dropped the redundant `metsuke_`
  prefix from tool names — tools now surface as
  `mcp__trentina__metsuke__<tool>` (`list_reports_tool`, `get_spec_tool`,
  `upsert_definition_tool`, `save_output_tool`, `get_output_tool`).

## [0.1.0] - 2026-08-29

Initial release of mcp-metsuke-crunchtools — a stateful reports catalog MCP
server.

### Added
- Stores report **definitions** (name, gather_prompt, owner_agent, schedule,
  source_config) and their gathered **outputs** (payload with per-finding source
  URLs, status) in SQLite.
- Five tools across two categories — Definitions: `metsuke_list_reports`,
  `metsuke_get_spec`, `metsuke_upsert_definition`; Outputs:
  `metsuke_save_output`, `metsuke_get_output`.
- Distroless FIPS Hummingbird container, port 8009, three transports
  (stdio/sse/streamable-http). Surfaces through the Trentina gateway as
  `mcp__trentina__metsuke__*`.
