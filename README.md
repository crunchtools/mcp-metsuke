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

## Tools (5)

### Definitions (3)

| Tool | Description |
|------|-------------|
| `list_reports` | List all report definitions in the catalog. |
| `get_spec` | Return the gather prompt + source config for one definition. |
| `upsert_definition` | Create or update a definition (name, prompt, owner, schedule, sources). |

### Outputs (2)

| Tool | Description |
|------|-------------|
| `save_output` | Persist gathered findings for a report (each ideally carrying a source URL). |
| `get_output` | Read the freshest output (or a specific day's) for compiling a report. |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METSUKE_DB` | `~/.local/share/mcp-metsuke/metsuke.db` | SQLite database path |
| `METSUKE_DB_FILE` | (none) | Path whose contents override `METSUKE_DB` (container secret-file convention) |

## Data Model

- **report_definitions** — `name` (PK), `gather_prompt`, `owner_agent`, `schedule`, `source_config` (JSON), `updated_at`
- **report_outputs** — `id`, `report_name` (FK), `gathered_at`, `window_start`/`window_end`, `payload` (JSON findings with source URLs), `status` (`gathering`/`ready`/`compiled`), `gatherer_run_ref`

## MCP Registry

`io.github.crunchtools/metsuke`

## License

AGPL-3.0-or-later
