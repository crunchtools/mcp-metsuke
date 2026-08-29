"""mcp-metsuke-crunchtools: A stateful reports catalog MCP server.

Metsuke (目付) — the Sengoku intelligence officer who gathered field reports
and compiled them for the daimyo. This server is the durable, cross-agent home
for report definitions and their gathered outputs.
"""

from __future__ import annotations

import argparse

__version__ = "0.1.0"


def main() -> None:
    """Entry point for mcp-metsuke-crunchtools."""
    parser = argparse.ArgumentParser(
        prog="mcp-metsuke-crunchtools",
        description="Stateful reports catalog MCP server (definitions + outputs)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8009,
        help="HTTP port (default: 8009)",
    )

    args = parser.parse_args()

    from .database import get_db
    from .server import mcp

    get_db()

    match args.transport:
        case "stdio":
            mcp.run(transport="stdio")
        case "sse":
            mcp.run(transport="sse", host=args.host, port=args.port)
        case _:
            mcp.run(transport="streamable-http", host=args.host, port=args.port)
