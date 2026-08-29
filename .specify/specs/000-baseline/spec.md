# Spec 000: Baseline

> **Status:** Accepted
> **Created:** 2026-08-29

## Overview

Initial release of mcp-metsuke-crunchtools: a stateful reports catalog with 5
tools across 2 categories — report definitions (3) and report outputs (2).

Metsuke is the durable, cross-agent home for report definitions and their
gathered outputs, replacing the run-scoped RT-comment research cache. An
autonomous gatherer reads a definition, sweeps the sources, and writes cited
findings; a compiler later reads the freshest output to draft a report.

## Tools

### Definitions (3)
- `metsuke_list_reports` — list all report definitions
- `metsuke_get_spec` — return gather prompt + source config for one definition
- `metsuke_upsert_definition` — create or update a definition

### Outputs (2)
- `metsuke_save_output` — persist gathered findings for a report
- `metsuke_get_output` — read the freshest (or a specific day's) output

## Data Model

**report_definitions**
- `name` (TEXT, PK) — e.g. `core-platform-status`
- `gather_prompt` (TEXT) — the instruction the gatherer runs
- `owner_agent` (TEXT, default `kagetora`) — which agent gathers this report
- `schedule` (TEXT, nullable) — descriptive cron/time metadata
- `source_config` (TEXT JSON, nullable) — which sources to sweep
- `updated_at` (TEXT)

**report_outputs**
- `id` (INTEGER, PK)
- `report_name` (TEXT, FK → report_definitions.name, ON DELETE CASCADE)
- `gathered_at` (TEXT)
- `window_start` / `window_end` (TEXT, nullable) — reporting window
- `payload` (TEXT JSON) — structured findings, each carrying a source URL
- `status` (TEXT) — `gathering` / `ready` / `compiled`
- `gatherer_run_ref` (TEXT, nullable)

## Environment Variables

- `METSUKE_DB` (default `~/.local/share/mcp-metsuke/metsuke.db`) — SQLite path
- `METSUKE_DB_FILE` — path whose contents override `METSUKE_DB`

## Architecture

- Plain stdlib SQLite (WAL journal, foreign keys on) — no vector/FTS extensions
- Pydantic v2 input validation (`extra="forbid"`, field limits, `Literal` status)
- Two-layer tools: pure functions in `tools/` called by `server.py` wrappers
- Three transports: stdio (default), SSE, streamable-http (port 8024)
- Surfaces through the Trentina gateway as `mcp__trentina__metsuke__*`

## Non-Goals (v0.1.0)

- Gathering itself — Metsuke stores specs and outputs; the gather is performed
  by an external agent (Kagetora) triggered via a Trentina alert callback.
- Scheduling — the actual 6AM Friday firing is an external systemd timer; the
  `schedule` field is descriptive only.
