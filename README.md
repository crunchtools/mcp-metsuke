# mcp-metsuke-crunchtools

Stateful reports catalog MCP server. Metsuke (目付 — the Sengoku intelligence
officer who gathered field reports and compiled them for the daimyō) is the
durable, cross-agent home for **report definitions** and their gathered
**outputs**.

An autonomous gatherer reads a definition, sweeps the configured sources, and
writes findings — each carrying its own source URL. A compiler later reads the
freshest output to draft a fully cited report. This replaces ad-hoc,
run-scoped research caches with a real, queryable store.

## Features

- **Definitions + outputs** — separate what-to-gather from what-was-gathered
- **Built-in scheduler** — Metsuke fires each definition's gather when its cron schedule comes due, or on demand via `trigger_report` — no external timer
- **Run lifecycle** — every fire opens a durable *run* with a second-granularity `run_id`: a provisional row is recorded before the callback (a fire is never lost), and a per-report concurrency lock keeps two gathers from racing
- **Cited findings** — payloads carry per-finding source URLs for one-click checking
- **Re-homeable gathering** — `owner_agent` makes the gatherer identity data, not code
- **Local-first** — plain SQLite (WAL), no external services, no per-seat fees
- **Three transports** — stdio, SSE, streamable-http

## Install

### uvx (recommended)

```bash
uvx mcp-metsuke-crunchtools
```

### pip

```bash
pip install mcp-metsuke-crunchtools
```

### Container

```bash
podman run --rm -v ~/.local/share/mcp-metsuke:/data:Z \
  quay.io/crunchtools/mcp-metsuke \
  --transport streamable-http --host 0.0.0.0 --port 8009
```

## Claude Code Integration

```bash
claude mcp add mcp-metsuke-crunchtools -- uvx mcp-metsuke-crunchtools
```

## Tools (10)

### Definitions (4)

| Tool | Description |
|------|-------------|
| `list_reports` | List all report definitions in the catalog, each with its next scheduled fire time. |
| `get_spec` | Return the gather prompt + source config for one definition. |
| `upsert_definition` | Create or update a definition (name, prompt, owner, cron schedule, timezone, sources). |
| `trigger_report` | Fire a report gather right now, without waiting for its schedule. For a swept definition it returns at once with `dispatched: "after_sweep"`; the sweep and the callback follow in the background. |

### Outputs (6)

| Tool | Description |
|------|-------------|
| `save_output` | Complete a run by persisting its gathered findings (pass the `run_id` from the fire callback; each finding ideally carrying a source URL). |
| `get_output` | Read the freshest output (or a specific day's) for compiling a report. |
| `list_outputs` | Browse the run history — metadata + `finding_count` per saved gather, no payloads. |
| `delete_output` | Delete one saved output by id. |
| `prune_outputs` | Bulk-prune a report's outputs — keep the N newest, or drop those before a date. |
| `get_sweep` | Read a swept run's pre-gathered records: the index, or one page of one section. |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METSUKE_DB` | `~/.local/share/mcp-metsuke/metsuke.db` | SQLite database path |
| `METSUKE_DB_FILE` | (none) | Path whose contents override `METSUKE_DB` (container secret-file convention) |
| `TRENTINA_ALERT_URL` | (none) | Base URL of the Trentina alert endpoint the scheduler POSTs gather callbacks to |
| `METSUKE_ALERT_TOKEN` | (none) | Alert token identifying the reports profile; enables the scheduler when set with `TRENTINA_ALERT_URL` |
| `METSUKE_ALERT_TOKEN_FILE` | (none) | Path whose contents override `METSUKE_ALERT_TOKEN` (container secret-file convention) |
| `TRENTINA_GATEWAY_URL` | (none) | Trentina gateway MCP endpoint for the sweep profile, e.g. `http://mcp-trentina:8019/gateway/metsuke-sweep/mcp`. Plain HTTP is accepted only for internal hosts (single-label service names, localhost, private IPs); anything else must be HTTPS |
| `METSUKE_SWEEP_TOKEN` | (none) | Bearer token for that profile; `METSUKE_SWEEP_TOKEN_FILE` overrides it |
| `METSUKE_SWEEP_TIMEOUT_SECONDS` | `1200` | Upper bound on one run's sweep |
| `METSUKE_SCHEDULER_POLL_SECONDS` | `60` | How often the scheduler checks for due reports |
| `METSUKE_SCHEDULER_ENABLED` | (auto) | Force the scheduler on/off; defaults to on when the callback is configured |
| `METSUKE_RUN_LOCK_TTL_SECONDS` | `1800` | How long an in-flight run holds the per-report lock before it is expired (self-heals a dead gatherer) |

The scheduler runs only under the `sse` and `streamable-http` transports (the long-lived production processes), never under `stdio`.

## Data Model

- **report_definitions** — `name` (PK), `gather_prompt`, `owner_agent`, `schedule` (cron), `timezone` (IANA), `source_config` (JSON), `last_fired_at`, `updated_at`
- **report_outputs** (one row per run) — `id`, `report_name` (FK), `run_id` (second-granularity identity), `trigger` (`scheduled`/`manual`/`direct`), `gathered_at`, `finished_at`, `window_start`/`window_end`, `payload` (JSON findings with source URLs), `status` (`gathering`/`ready`/`compiled`/`failed`), `gatherer_run_ref`, `detail`. A partial unique index on `(report_name) WHERE status='gathering'` is the per-report concurrency lock.

## MCP Registry

`io.github.crunchtools/metsuke`

## License

AGPL-3.0-or-later

## Sweeps

A definition can opt into a deterministic pre-gather by adding `sweep` to its
`source_config`:

```json
{"sweep": {"timezone": "America/New_York", "window_hour": 6, "steps": [
  {"section": "slack", "collector": "slack_waiting",
   "options": {"user_id": "U9VN3S1ST", "handle": "smccarty"}},
  {"section": "work_email", "collector": "gmail_waiting",
   "options": {"backend": "gw-work", "account": "smccarty@redhat.com"}},
  {"section": "calendar", "collector": "calendar_day",
   "options": {"backend": "gw-work", "account": "smccarty@redhat.com"}},
  {"section": "rss", "collector": "feed_entries",
   "options": {"categories": {"1": 20, "5": 20}}}]}}
```

On fire, Metsuke runs each step in order through the Trentina gateway (as its
own read-only profile), stores the results on the run, then dispatches the
callback with `sweep_status`. The gatherer reads records with `get_sweep`
instead of calling the sources, so the gathering LLM only selects, phrases and
delivers. A failing step is recorded and the sweep continues. Results Trentina
flags are stored as metadata only, with their text withheld.

| Collector | What it produces |
|-----------|------------------|
| `slack_waiting` | Direct asks over a lookback: an @-mention, or a DM message that reads as a question or request (DM chatter and Slack system notices are not asks). Each thread or DM is read for up to three pages (longer or partly unreadable ones are marked `unverified`). An ask the user replied to or reacted to is `answered` and dropped; the rest are `waiting`, with `in_window` separating new asks from still-open ones |
| `gmail_waiting` | Inbox threads in the window (or `lookback_days`) where the backend's ownership analysis says the ball is in the user's court; automated mail, bare calendar notices and threads newer than `min_age_hours` dropped, invitations kept, `priority_senders` marked `priority` |
| `calendar_day` | The report day's meetings, pending invites over a lookahead, and hard overlaps |
| `feed_entries` | Recent entries per feed category (read or unread), with a longer window after a weekend |

### Collector options

Every step is `{"section": "<name>", "collector": "<collector>", "options": {...}}`.
Section names are `[a-z0-9_-]`, unique, up to 12 steps. Top-level `timezone`
(IANA, default `America/New_York`) and `window_hour` (0-23, default 6) set the
window: from that hour on the previous weekday until the run.

| Collector | Option | Required | Default | Bounds |
|-----------|--------|----------|---------|--------|
| `slack_waiting` | `user_id` | yes | | 2-32 chars |
| | `handle` | yes | | 1-64 chars |
| | `backend` | | `slack` | ≤64 chars |
| | `self_label` | | `you` | ≤64 chars |
| | `lookback_days` | | 7 | 1-30 |
| | `max_conversations` | | 40 | 1-100 |
| | `workspace_url` | | `https://redhat-internal.slack.com` | ≤200 chars |
| `gmail_waiting` | `backend` | yes | | e.g. `gw-work`, `gw-personal` |
| | `account` | yes | | the mailbox address |
| | `query_extra` | | `""` | ≤500 chars, appended to `in:inbox after:<window>` |
| | `max_threads` | | 60 | 1-200 |
| | `body_chars` | | 1200 | 0-4000 (0 = no body) |
| | `link_template` | | Gmail `#all/{thread_id}` | ≤200 chars, or null |
| | `lookback_days` | | report window | 1-30, search the last N days instead |
| | `min_age_hours` | | 0 | 0-168, drop threads active more recently |
| | `priority_senders` | | `[]` | ≤20 From-header substrings (1-100 chars, case-insensitive) |
| `calendar_day` | `backend` | yes | | |
| | `account` | yes | | |
| | `timezone` | | `America/New_York` | IANA zone |
| | `lookahead_days` | | 3 | 0-14 (pending invites) |
| `feed_entries` | `categories` | yes | | 1-12 entries of `"<id>": <limit>`, id ≤9 digits, limit 1-100 |
| | `backend` | | `feeds` | |
| | `since_days` | | 1 | 1-30 |
| | `since_days_after_weekend` | | 3 | 1-30 (Mondays and weekend runs) |

Collectors are code in `sweep/collectors.py`, not configuration: a definition
can only pick and parameterize them. A bad spec is rejected at
`upsert_definition` time.
