# MCP Metsuke CrunchTools Container
# Multi-stage build: install into a venv in the Hummingbird FIPS builder,
# copy the venv into the distroless FIPS runtime.
#
# Build:
#   podman build -t quay.io/crunchtools/mcp-metsuke .
#
# Run (rootless, persistent SQLite volume):
#   podman run --rm --userns=keep-id --user $(id -u):$(id -g) \
#     -v ~/.local/share/mcp-metsuke:/data:Z \
#     quay.io/crunchtools/mcp-metsuke \
#     --transport streamable-http --host 0.0.0.0 --port 8009

# Stage 1: Lock (turn uv.lock into the exact dependency set to install)
FROM quay.io/hummingbird/python:3.14-fips-builder AS lock
USER 0
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /lock
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project \
      --format requirements-txt -o /requirements.txt

# Stage 2: Build into a venv (Hummingbird FIPS builder — same family as runtime)
FROM quay.io/hummingbird/python:3.14-fips-builder AS builder

USER 0
WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Dependencies come from uv.lock, not from a fresh resolve. `pip install .`
# read only pyproject.toml, whose floors carry no upper bound, so the image
# shipped whatever had been released that morning -- a dependency set no test
# had ever run against (RT #1564, same bug as mcp-gemini / trentina #79).
# --no-deps stops pip re-resolving what the lock already decided.
COPY --from=lock /requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /tmp/requirements.txt
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

# Stage 3: Distroless FIPS runtime (no shell, no dnf)
FROM quay.io/hummingbird/python:3.14-fips

LABEL name="mcp-metsuke-crunchtools" \
      version="3.6.1" \
      summary="Stateful reports catalog MCP server (definitions + gathered outputs)" \
      description="Durable, cross-agent home for report definitions and their gathered outputs" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/mcp-metsuke" \
      io.k8s.display-name="MCP Metsuke CrunchTools" \
      io.openshift.tags="mcp,reports,catalog,sqlite" \
      org.opencontainers.image.source="https://github.com/crunchtools/mcp-metsuke" \
      org.opencontainers.image.description="Stateful reports catalog MCP server (definitions + gathered outputs)" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

WORKDIR /app
COPY --from=builder /app/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

ENV METSUKE_DB=/data/metsuke.db

EXPOSE 8009
ENTRYPOINT ["python", "-m", "mcp_metsuke_crunchtools"]
