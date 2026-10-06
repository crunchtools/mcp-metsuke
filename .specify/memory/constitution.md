# mcp-metsuke-crunchtools Constitution

> **Version:** 1.1.0
> **Ratified:** 2026-08-29
> **Amended:** 2026-10-02
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.20.0
> **Profile:** MCP Server

This file holds what is specific to mcp-metsuke. The fleet rules and the MCP
Server profile (five-layer security model, two-layer tools, distribution
channels, transports, quality gates, Gourmand) apply at the inherited version
and are checked against this repo's files by `constitution.yml`. They are not
restated here.

## Purpose

A stateful reports catalog: durable, cross-agent storage for report
definitions (what to gather, owner agent, cron schedule, sources) and their
gathered outputs (one row per run, findings carrying their source URLs). A
compiler reads the freshest output to draft a cited report.

## Security Model Specifics

- **Credentials:** no third-party API credentials. Metsuke carries two
  Trentina bearer tokens, both `SecretStr` and both honoring the `<VAR>_FILE`
  convention (file contents stripped, precedence over the plain variable):
  `METSUKE_ALERT_TOKEN` for gather callbacks to `TRENTINA_ALERT_URL`, and
  `METSUKE_SWEEP_TOKEN` for sweeps through `TRENTINA_GATEWAY_URL`. The sweep
  gateway URL may be plain HTTP only for internal hosts (single-label service
  names, localhost, private IPs); anything else must be HTTPS.
- **Input limits:** every tool validates through a Pydantic v2 model with
  `extra="forbid"`; strings are length-bounded (names 200, prompts 20,000),
  payloads are capped at 2,000 items, and `status` is a `Literal`
  (`gathering`, `ready`, `compiled`, `failed`).
- **Storage:** parameterized SQL only; `PRAGMA foreign_keys=ON`, outputs
  cascade-delete with their definition.
- **Surface:** no shell execution or code evaluation, no filesystem writes
  outside the database. Sweep results Trentina flags are stored as metadata
  only, with their text withheld.

## Storage and Persistence

- `METSUKE_DB` sets the SQLite path (default
  `~/.local/share/mcp-metsuke/metsuke.db`; `/data/metsuke.db` in the
  container, on a volume mounted at `/data`). Created on first run with WAL
  and foreign keys enabled.
- A partial unique index on `(report_name) WHERE status='gathering'` is the
  per-report concurrency lock; a run holds it for at most
  `METSUKE_RUN_LOCK_TTL_SECONDS` (default 1800) so a dead gatherer
  self-heals.

## Scheduler and Sweeps

- The built-in scheduler fires due definitions by POSTing to the Trentina
  alert endpoint. It runs only under the `sse` and `streamable-http`
  transports, never `stdio`, and is on by default only when the callback
  URL and token are set (`METSUKE_SCHEDULER_ENABLED` overrides).
- Every fire records a provisional run row before the callback, so a fire is
  never lost.
- A definition can opt into a sweep: Metsuke runs each collector step through
  the Trentina gateway as its own read-only profile, bounded by
  `METSUKE_SWEEP_TIMEOUT_SECONDS` (default 1200), and the gatherer reads the
  records with `get_sweep`. A failing step is recorded and the sweep goes on.

## Tests Use In-Memory SQLite

Instead of mocked HTTP, tool tests run against an in-memory SQLite database
with config and database singletons reset per test, and call the pure tool
function rather than the `_tool` wrapper. `test_tool_count` is updated
whenever a tool is added or removed.

## Version Locations

The release version is kept in sync in four places: `pyproject.toml`,
`server.py` (FastMCP constructor), `__init__.py` (`__version__`) and
`server.json`.

## Instance

| Context | Name |
|---------|------|
| GitHub repo | `crunchtools/mcp-metsuke` |
| PyPI package | `mcp-metsuke-crunchtools` |
| Container image | `quay.io/crunchtools/mcp-metsuke` |
| systemd service | `mcp-metsuke.service` |
| Gateway backend key | `metsuke` (tools surface as `mcp__trentina__metsuke__*`) |
| MCP Registry | `io.github.crunchtools/metsuke` |
| HTTP port | 8009 |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-08-29 | Initial constitution (inherits universal v1.10.0, MCP Server profile) |
| 1.0.1 | 2026-09-25 | Inherit constitution v1.17.0 (Gatehouse gates) |
| 1.1.0 | 2026-10-02 | Manifest under constitution v1.18.0: profile restatement removed; credentials, scheduler and sweep rules updated to match the code |
