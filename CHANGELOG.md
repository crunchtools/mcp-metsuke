# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

Entries prior to 2026-09-19 are back-filled from GitHub Release notes (RT #1484).

## [Unreleased]

## [3.0.0] - 2026-10-01

### Changed
- The gather callback sends the alert token as `Authorization: Bearer` to
  `<TRENTINA_ALERT_URL>/alert`, not in the URL path (Trentina #333). The URL
  is in every access log on the way, and httpx names it in the error text a
  failed callback carries.

**Upgrade:** breaking: needs Trentina 0.52.0 or later, which accepts only the
header. Deploy the two together.

## [2.2.0] - 2026-09-29

### Added
- `jira_issues` collector: runs up to six named JQL queries per sweep, with
  `{since}` substituted by the window start, and turns each issue into a record
  carrying its key, link, status, components, age, and — for issues filed by a
  web intake form — the form's contact fields and specs parsed out of the
  description. A failing query is recorded and the rest still run; a flagged
  result keeps only its non-text fields. Requires the sweep profile to grant
  the Jira backend `jira_search` (read-only).

## [2.1.1] - 2026-09-28

### Fixed
- `feed_entries` no longer marks the section partial when a category has no
  entries. The gateway returns an empty list as empty text, which was reported
  as "unparseable result (0 chars)".
- `feed_entries` anchors its lookback on the sweep window's start (or
  `since_days` ago, whichever is earlier) via `published_after`. A rolling
  `since_days` let a Monday-afternoon re-run drop Friday morning's entries.
- The container image's `version` label read 0.5.1; it now matches the release,
  and a test keeps the label, package, and `server.json` versions in sync.

## [2.1.0] - 2026-09-28

### Added
- `slack_waiting` option `first_name`: in group DMs and channels, a question or
  request that names the user ("Scott, can you review?") is an ask.

### Fixed
- Group DMs no longer treat any question as an ask of the user. Only an
  @-mention or a `first_name` address counts there; a question to the group
  ("isn't Aquasec on the west coast?") does not. One-to-one DMs are unchanged.
- `slack_is_ask` ignores quoted text (double-quoted spans and blockquote
  lines), so relaying someone else's question ("my PO was like \"Why does
  Scott want...?\"") is not an ask.

## [2.0.2] - 2026-09-28

### Fixed
- A channel @-mention that is not in a thread is now read from channel history
  (from the first such mention, 15 messages a page), not as a one-message
  thread. The user's later top-level reply in the channel, or a reply in the
  ask's own thread (`reply_users`), answers it. Before, a reply in an unthreaded
  channel was never seen, and each mention was listed separately.
- Every ask in a conversation is checked, not only the latest: an earlier ask
  stays open when only a later one got a reaction or thread reply. A later post
  by the user still answers every earlier ask, by design. For channel history, a record's `tail` starts at the ask
  rather than showing the channel's latest, possibly unrelated, messages.

## [2.0.1] - 2026-09-28

### Fixed
- `slack_is_ask` no longer treats a bare "feedback", "thoughts" or "review" as a
  request: a DM remark such as "feedback to you, emotions run high" was listed
  as an ask. Those words now count only as "your/any feedback|thoughts|input|review",
  or as an imperative "Review ..." opening a sentence (not "Review of X ...").

## [2.0.0] - 2026-09-28

### Changed (breaking)
Both changes alter `slack_waiting`'s default output for existing definitions.
- `slack_waiting` counts only direct asks: an @-mention, or a DM message that
  reads as a question or request (`slack_is_ask`). DM chatter ("nice!", "no
  worries") no longer opens a conversation, and chatter after the user's reply
  no longer reopens one (`dropped_no_ask`).
- A reaction from the user now answers an ask. The `acknowledged` state is gone.

### Added
- `gmail_waiting` options `lookback_days`, `min_age_hours` (`dropped_fresh`)
  and `priority_senders` (`priority: true` on matching records).

### Fixed
- Slack's system user is recognized as `USLACK` (Enterprise Grid) as well as
  `USLACKBOT`, or by its `slack` profile. Its notices are not asks, and it is
  no longer sent to `slack_get_user_info`, whose error marked the section
  `partial` on every run.

## [1.2.3] - 2026-09-27

### Fixed
- `gmail_waiting` drops threads whose last message came from the mailbox
  owner (`dropped_self`): the first live sweep listed the user's own sent
  reports, which carry no ownership analysis.

## [1.2.2] - 2026-09-27

### Fixed
- Slackbot (`USLACKBOT`) DMs such as channel-removal notices no longer count
  as asks waiting on the user; the first live sweep listed one.

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
