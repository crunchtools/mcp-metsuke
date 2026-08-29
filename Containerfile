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

# Stage 1: Build into a venv (Hummingbird FIPS builder — same family as runtime)
FROM quay.io/hummingbird/python:latest-fips-builder AS builder

USER 0
WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Stage 2: Distroless FIPS runtime (no shell, no dnf)
FROM quay.io/hummingbird/python:latest-fips

LABEL name="mcp-metsuke-crunchtools" \
      version="0.2.0" \
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
