# mcp-metsuke-crunchtools Constitution

> **Version:** 1.0.0
> **Ratified:** 2026-08-29
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.10.0
> **Profile:** MCP Server

This constitution establishes the core principles, constraints, and workflows that govern all development on mcp-metsuke-crunchtools.

---

## I. Core Principles

### 1. Five-Layer Security Model

Every change MUST preserve all five security layers. No exceptions.

**Layer 1 — Credential Protection:**
- Metsuke has no external API credentials — it is a self-contained local SQLite service reached through the Trentina gateway.
- No tokens, API keys, or passwords are required or stored.
- The `_api_token` field in `config.py` is typed `SecretStr | None` for compliance and future extensibility. Any future secret MUST also honor the `<VAR>_FILE` container convention (stripped, precedence over the plain var).

**Layer 2 — Input Validation:**
- Every tool validates input through a Pydantic v2 model with `extra="forbid"`.
- String fields are length-bounded; payloads are item-count-bounded.
- The `status` field is a constrained `Literal` (`gathering`/`ready`/`compiled`).

**Layer 3 — Storage Hardening:**
- Parameterized SQL only — no string interpolation into statements.
- `PRAGMA foreign_keys=ON`; outputs cascade-delete with their definition.
- Database path is env-driven (`METSUKE_DB`) and confined to the mounted `/data` volume in containers.

**Layer 4 — Dangerous Operation Prevention:**
- No shell execution or code evaluation. No `eval()`/`exec()`. No filesystem writes outside the database.

**Layer 5 — Supply Chain Security:**
- Weekly automated CVE scanning via GitHub Actions.
- Hummingbird distroless FIPS container base (minimal CVE surface).
- Gourmand AI slop detection gating all PRs.

### 2. Two-Layer Tool Architecture

Tools follow a strict two-layer pattern:
- `server.py` — `@mcp.tool()` decorated functions that validate args (via `models.py`) and delegate.
- `tools/*.py` — pure async functions that call `database.py`.

Never put business logic in `server.py`. Never put MCP registration in `tools/*.py`.

### 3. Self-Contained Operation

The server MUST work without any external service accounts:
- `METSUKE_DB` configurable (default: `~/.local/share/mcp-metsuke/metsuke.db`).
- SQLite database auto-created on first run (WAL, foreign keys enabled).
- No authentication required.

### 4. Three Distribution Channels

Every release MUST be available through all three channels simultaneously:

| Channel | Command | Use Case |
|---------|---------|----------|
| uvx | `uvx mcp-metsuke-crunchtools` | Zero-install, Claude Code |
| pip | `pip install mcp-metsuke-crunchtools` | Virtual environments |
| Container | `podman run quay.io/crunchtools/mcp-metsuke` | Isolated, systemd |

### 5. Three Transport Modes

The server MUST support all three MCP transports:
- **stdio** (default) — spawned per-session by Claude Code.
- **SSE** — legacy HTTP transport.
- **streamable-http** — production HTTP, systemd-managed containers.

### 6. Semantic Versioning

Follow [Semantic Versioning 2.0.0](https://semver.org/) strictly.

**MAJOR** (breaking): removed/renamed tools, changed parameter names or types, renamed env vars, changed default behavior.
**MINOR** (additive): new tools, new optional parameters, new tool groups.
**PATCH** (fixes): bug fixes, test improvements, security dependency updates.
**No bump** (not shipped): CI/CD, docs, issue templates, governance files.

Version bump happens at release time, synced across all four locations: `pyproject.toml`, `server.py` (FastMCP constructor), `__init__.py` (`__version__`), and `server.json`.

### 7. AI Code Quality

All code MUST pass Gourmand checks before merge. Zero violations required.

---

## II. Technology Stack

| Layer | Technology | Version |
|-------|------------|---------|
| Language | Python | 3.11+ |
| MCP Framework | FastMCP | Latest |
| Database | SQLite (WAL, FTS-free) | Built-in |
| Validation | Pydantic | v2 |
| Container Base | Hummingbird (distroless FIPS) | Latest |
| Package Manager | uv | Latest |
| Build System | hatchling | Latest |
| Linter | ruff | Latest |
| Type Checker | mypy (strict) | Latest |
| Tests | pytest + pytest-asyncio | Latest |
| Slop Detector | gourmand | Latest |

---

## III. Testing Standards

### In-Memory SQLite Tests (MANDATORY)

Every tool MUST have a corresponding test using an in-memory (`:memory:`) SQLite database — no disk I/O, no cleanup, fast CI.

**Pattern:**
1. Reset the config + database singletons before each test (autouse fixture).
2. Create an in-memory database with schema applied.
3. Seed definitions/outputs as needed.
4. Call the pure tool function directly (not the `_tool` wrapper).
5. Assert response structure and values.

**Required coverage:**

| Tool Group | Test Class | Minimum Tests |
|------------|-----------|---------------|
| Definition tools | `TestDefinitionTools` | upsert, get_spec, list, missing |
| Output tools | `TestOutputTools` | save, get latest, by-date, missing |
| Tool count | `TestToolCount` | `test_tool_count` assertion |

**Tool count assertion:** `test_tool_count` MUST be updated whenever tools are added or removed.

### Input Validation Tests

Every Pydantic model in `models.py` MUST have tests in `test_validation.py`: valid minimal input, valid full input, and rejected inputs (empty strings, over-long values, bad enums, extra fields).

---

## IV. Gourmand (AI Slop Detection)

All code MUST pass `gourmand --full .` with **zero violations** before merge. Gourmand is a CI gate in GitHub Actions.

- `gourmand.toml` — check settings, excluded paths.
- `gourmand-exceptions.toml` — documented exceptions with justifications.
- `.gourmand-cache/` — MUST be in `.gitignore`.

### Exception Policy

Exceptions MUST have documented justifications in `gourmand-exceptions.toml`. Acceptable reasons: standard API patterns, test-specific patterns, framework requirements (CLAUDE.md). Unacceptable: "the code is special", "the threshold is too strict".

---

## V. Code Quality Gates

Every code change must pass through these gates in order:

1. **Lint** — `uv run ruff check src tests`
2. **Type Check** — `uv run mypy src`
3. **Tests** — `uv run pytest -v` (all passing, in-memory SQLite)
4. **Gourmand** — `gourmand --full .` (zero violations)
5. **Gatehouse** — automated review gate
6. **Container Build** — `podman build -f Containerfile .`

---

## VI. Container Conventions

- Use **Containerfile** (not Dockerfile).
- Multi-stage, same base-image family builder→runtime: `quay.io/hummingbird/python:latest-fips-builder` → `quay.io/hummingbird/python:latest-fips`.
- Runtime is distroless — no shell/dnf, no shell-form `RUN`; copy the venv from the builder.
- Required LABELs: `maintainer`, `description`.
- Required OCI labels:
  ```
  org.opencontainers.image.source=https://github.com/crunchtools/mcp-metsuke
  org.opencontainers.image.description=Stateful reports catalog MCP server (definitions + gathered outputs)
  org.opencontainers.image.licenses=AGPL-3.0-or-later
  ```

### Dual-Push CI Architecture

Container CI workflows MUST use two separate jobs:

1. **`build-and-push-quay`** — builds and pushes to Quay.io; includes Trivy scan.
2. **`build-and-push-ghcr`** — builds and pushes to GHCR; `needs: build-and-push-quay`; gated with `if: github.event_name != 'pull_request'`.

GHA build cache is mandatory (`cache-from/to: type=gha`); never `--no-cache`.

---

## VII. Naming Conventions

| Context | Name |
|---------|------|
| GitHub repo | `crunchtools/mcp-metsuke` |
| PyPI package | `mcp-metsuke-crunchtools` |
| CLI command | `mcp-metsuke-crunchtools` |
| Python module | `mcp_metsuke_crunchtools` |
| Container image | `quay.io/crunchtools/mcp-metsuke` |
| systemd service | `mcp-metsuke.service` |
| Gateway backend key | `metsuke` (tools surface as `mcp__trentina__metsuke__*`) |
| HTTP port | 8009 |
| License | AGPL-3.0-or-later |

---

## VIII. Development Workflow

### Adding a New Tool

1. Add the async function to the appropriate `tools/*.py` file.
2. Export it from `tools/__init__.py`.
3. Add a Pydantic input model in `models.py`.
4. Import it in `server.py` and register with `@mcp.tool()` (validate then delegate).
5. Add an in-memory SQLite test in `tests/test_tools.py` and a validation test in `tests/test_validation.py`.
6. Update the tool count in `test_tool_count`.
7. Run all quality gates.
8. Update CLAUDE.md and README.md tool listings.

---

## IX. Governance

### Amendment Process

1. Create a PR with proposed changes to this constitution.
2. Document rationale in the PR description.
3. Require maintainer approval.
4. Update the version number upon merge.

### Ratification History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-08-29 | Initial constitution (inherits universal v1.10.0, MCP Server profile) |
