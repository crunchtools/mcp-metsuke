# mcp-metsuke-crunchtools

Stateful reports catalog MCP server. Stores report **definitions** (what to
gather) and their gathered **outputs** (findings with source URLs).

## Quick Start

```bash
uv sync --all-extras
uv run mcp-metsuke-crunchtools                 # stdio (default)
uv run mcp-metsuke-crunchtools --transport streamable-http --port 8024
```

## Environment Variables

- `METSUKE_DB` — SQLite database path (default: `~/.local/share/mcp-metsuke/metsuke.db`).
  A `METSUKE_DB_FILE` path takes precedence (container secret-file convention).

## Tools (5)

### Definitions (3)
- `metsuke_list_reports_tool` — list all report definitions
- `metsuke_get_spec_tool` — gather prompt + source config for one definition
- `metsuke_upsert_definition_tool` — create/update a definition

### Outputs (2)
- `metsuke_save_output_tool` — gatherer writes findings here
- `metsuke_get_output_tool` — compiler reads freshest (or by-date) output

## Development

```bash
uv run ruff check src tests     # Lint
uv run mypy src                 # Type check
uv run pytest -v                # Test (in-memory SQLite)
gourmand --full .               # Slop detection
podman build -f Containerfile . # Container
```

## Architecture

- `config.py` — env-driven config (`METSUKE_DB`), SecretStr-ready, `<VAR>_FILE` support
- `database.py` — stdlib SQLite (WAL, FK on): `report_definitions` + `report_outputs`
- `models.py` — Pydantic v2 input validation (`extra="forbid"`, field limits)
- `tools/` — pure async functions (definitions, outputs)
- `server.py` — thin `@mcp.tool()` wrappers that validate then delegate

## Role in the fleet

Metsuke replaces the RT-comment research cache. An autonomous gatherer
(Kagetora today; re-homeable via `owner_agent`) reads a definition with
`get_spec`, sweeps the sources, and writes findings with `save_output`. The
compiler (Josui) reads the freshest output with `get_output` to draft a cited
weekly status email. Surfaces through Trentina as `mcp__trentina__metsuke__*`.
