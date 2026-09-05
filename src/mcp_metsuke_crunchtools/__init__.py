"""mcp-metsuke-crunchtools: A stateful reports catalog MCP server.

Metsuke (目付) — the Sengoku intelligence officer who gathered field reports
and compiled them for the daimyo. This server is the durable, cross-agent home
for report definitions and their gathered outputs, and it fires each report's
gather on its own schedule.
"""

from __future__ import annotations

import argparse
import logging
import threading

__version__ = "0.5.0"


def _maybe_start_scheduler() -> None:
    """Start the background report scheduler when it is configured/enabled."""
    from .config import get_config

    cfg = get_config()
    if not cfg.scheduler_enabled:
        logging.getLogger("mcp_metsuke").info(
            "scheduler disabled (no callback configured); running as passive catalog"
        )
        return
    from .scheduler import run_scheduler

    thread = threading.Thread(target=run_scheduler, name="metsuke-scheduler", daemon=True)
    thread.start()


def main() -> None:
    """Entry point for mcp-metsuke-crunchtools."""
    parser = argparse.ArgumentParser(
        prog="mcp-metsuke-crunchtools",
        description="Stateful reports catalog MCP server (definitions + outputs + scheduler)",
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

    logging.basicConfig(level=logging.INFO)

    from .database import get_db
    from .server import mcp

    get_db()

    match args.transport:
        case "stdio":
            mcp.run(transport="stdio")
        case "sse":
            _maybe_start_scheduler()
            mcp.run(transport="sse", host=args.host, port=args.port)
        case _:
            _maybe_start_scheduler()
            mcp.run(transport="streamable-http", host=args.host, port=args.port)
