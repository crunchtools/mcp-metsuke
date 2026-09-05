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

## Tools (9)

### Definitions (4)

| Tool | Description |
|------|-------------|
| `list_reports` | List all report definitions in the catalog, each with its next scheduled fire time. |
| `get_spec` | Return the gather prompt + source config for one definition. |
| `upsert_definition` | Create or update a definition (name, prompt, owner, cron schedule, timezone, sources). |
| `trigger_report` | Fire a report gather right now, without waiting for its schedule. |

### Outputs (5)

| Tool | Description |
|------|-------------|
| `save_output` | Persist gathered findings for a report (each ideally carrying a source URL). |
| `get_output` | Read the freshest output (or a specific day's) for compiling a report. |
| `list_outputs` | Browse the run history — metadata + `finding_count` per saved gather, no payloads. |
| `delete_output` | Delete one saved output by id. |
| `prune_outputs` | Bulk-prune a report's outputs — keep the N newest, or drop those before a date. |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METSUKE_DB` | `~/.local/share/mcp-metsuke/metsuke.db` | SQLite database path |
| `METSUKE_DB_FILE` | (none) | Path whose contents override `METSUKE_DB` (container secret-file convention) |
| `TRENTINA_ALERT_URL` | (none) | Base URL of the Trentina alert endpoint the scheduler POSTs gather callbacks to |
| `METSUKE_ALERT_TOKEN` | (none) | Alert token identifying the reports profile; enables the scheduler when set with `TRENTINA_ALERT_URL` |
| `METSUKE_ALERT_TOKEN_FILE` | (none) | Path whose contents override `METSUKE_ALERT_TOKEN` (container secret-file convention) |
| `METSUKE_SCHEDULER_POLL_SECONDS` | `60` | How often the scheduler checks for due reports |
| `METSUKE_SCHEDULER_ENABLED` | (auto) | Force the scheduler on/off; defaults to on when the callback is configured |

The scheduler runs only under the `sse` and `streamable-http` transports (the long-lived production processes), never under `stdio`.

## Data Model

- **report_definitions** — `name` (PK), `gather_prompt`, `owner_agent`, `schedule` (cron), `timezone` (IANA), `source_config` (JSON), `last_fired_at`, `updated_at`
- **report_outputs** — `id`, `report_name` (FK), `gathered_at`, `window_start`/`window_end`, `payload` (JSON findings with source URLs), `status` (`gathering`/`ready`/`compiled`), `gatherer_run_ref`

## MCP Registry

`io.github.crunchtools/metsuke`

## License

AGPL-3.0-or-later
