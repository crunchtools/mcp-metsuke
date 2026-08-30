# mcp-metsuke-crunchtools

Stateful reports catalog MCP server. Stores report **definitions** (what to
gather) and their gathered **outputs** (findings with source URLs).

## Quick Start

```bash
uv sync --all-extras
uv run mcp-metsuke-crunchtools                 # stdio (default)
uv run mcp-metsuke-crunchtools --transport streamable-http --port 8009
```

## Environment Variables

- `METSUKE_DB` — SQLite database path (default: `~/.local/share/mcp-metsuke/metsuke.db`).
  A `METSUKE_DB_FILE` path takes precedence (container secret-file convention).
- `TRENTINA_ALERT_URL` — base URL of the Trentina alert endpoint the scheduler POSTs callbacks to.
- `METSUKE_ALERT_TOKEN` — alert token for the reports profile; enables the scheduler when set with
  `TRENTINA_ALERT_URL`. A `METSUKE_ALERT_TOKEN_FILE` path takes precedence (secret-file convention).
- `METSUKE_SCHEDULER_POLL_SECONDS` — poll interval (default: `60`).
- `METSUKE_SCHEDULER_ENABLED` — force the scheduler on/off; defaults to on when the callback is configured.

## Tools (6)

### Definitions (4)
- `list_reports_tool` — list all report definitions (each with its next fire time)
- `get_spec_tool` — gather prompt + source config for one definition
- `upsert_definition_tool` — create/update a definition (cron schedule + timezone)
- `trigger_report_tool` — fire a report gather now, without waiting for its schedule

### Outputs (2)
- `save_output_tool` — gatherer writes findings here
- `get_output_tool` — compiler reads freshest (or by-date) output

## Development

```bash
uv run ruff check src tests     # Lint
uv run mypy src                 # Type check
uv run pytest -v                # Test (in-memory SQLite)
gourmand --full .               # Slop detection
podman build -f Containerfile . # Container
```

## Architecture

- `config.py` — env-driven config (DB path, alert URL/token), SecretStr, `<VAR>_FILE` support
- `database.py` — stdlib SQLite (WAL, FK on): `report_definitions` + `report_outputs`, with schema migrations
- `models.py` — Pydantic v2 input validation (`extra="forbid"`, field limits, cron + timezone checks)
- `scheduler.py` — background thread that fires due reports via the Trentina alert callback
- `tools/` — pure async functions (definitions, outputs)
- `server.py` — thin `@mcp.tool()` wrappers that validate then delegate

## Role in the fleet

Metsuke replaces the RT-comment research cache. Metsuke's built-in scheduler
fires each definition when its cron schedule comes due (or on demand via
`trigger_report`) by POSTing the callback to the Trentina alert endpoint, which
HMAC-signs and forwards it to the owning gatherer. That gatherer (Kagetora
today; re-homeable via `owner_agent`) reads a definition with `get_spec`, sweeps
the sources, and writes findings with `save_output`. The compiler (Josui) reads
the freshest output with `get_output` to draft a cited weekly status email.
Surfaces through Trentina as `mcp__trentina__metsuke__*`.
