"""Trentina gateway client for the sweep stage.

The sweep calls source backends (Slack, Gmail, Calendar, feeds) through the
Trentina gateway as its own profile (``metsuke-sweep``), never directly. Trentina
keeps the credentials, the audit trail and the L1-L3 judging; Metsuke holds one
bearer token, the same shape as its alert token.

The profile runs in ``flag`` mode with ``short_names: false``, so every result
arrives verbatim and tools are addressed as ``<backend>__<tool>``. A flagged
result carries a trailing ``[TRENTINA WARNING]`` text block, which is the one
channel Trentina guarantees survives a strict MCP client (see Trentina
``gateway/router.py``). ``GatewayResult.flagged`` records it so collectors can
withhold flagged bodies from the LLM.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ClientError
from mcp.shared.exceptions import McpError

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger("mcp_metsuke.sweep.client")

WARNING_PREFIX = "[TRENTINA WARNING]"
DEFAULT_CALL_TIMEOUT = 120.0
MAX_ERROR_CHARS = 500

# What one tool call can fail with once the session is up. Anything else is a
# bug in Metsuke and should crash loudly rather than be recorded as a source
# being "unavailable".
_SERVICE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

CALL_FAILURES = (McpError, ClientError, httpx.HTTPError, TimeoutError, ConnectionError)


@dataclass(frozen=True)
class GatewayResult:
    """One tool result: its text, whether Trentina flagged it, and any error."""

    text: str
    flagged: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the call succeeded; flagged results are still ok."""
        return self.error is None

    def json(self) -> Any:
        """Decode the text as JSON; raises ValueError when it is not JSON."""
        return json.loads(self.text)


class Gateway(Protocol):
    """What collectors need from a gateway: one call at a time."""

    async def call(self, backend: str, tool: str, args: dict[str, Any]) -> GatewayResult: ...


def result_from_blocks(texts: list[str], is_error: bool) -> GatewayResult:
    """Build a GatewayResult from a tool result's text blocks."""
    flagged = False
    body: list[str] = []
    for text in texts:
        if text.startswith(WARNING_PREFIX):
            flagged = True
        else:
            body.append(text)
    joined = "\n".join(body)
    if is_error:
        # A flagged error keeps its status, never its text.
        error = "tool error (flagged; text withheld)" if flagged else joined[:MAX_ERROR_CHARS]
        return GatewayResult(text="", flagged=flagged, error=error or "tool error")
    return GatewayResult(text=joined, flagged=flagged)


class TrentinaGateway:
    """MCP client session for one Trentina gateway profile.

    Use as an async context manager so one session serves the whole sweep.
    Calls are strictly sequential; the sweep never fans out. Build it with
    ``connect_gateway`` for the real gateway; tests hand in any fastmcp
    ``Client``.
    """

    def __init__(self, client: Client[Any], timeout: float = DEFAULT_CALL_TIMEOUT) -> None:
        self._client = client
        self._timeout = timeout
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> TrentinaGateway:
        await self._stack.enter_async_context(self._client)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stack.aclose()

    async def call(self, backend: str, tool: str, args: dict[str, Any]) -> GatewayResult:
        """Call ``<backend>__<tool>``; call failures come back as an error result."""
        name = f"{backend}__{tool}"
        try:
            result = await self._client.call_tool(
                name, args, raise_on_error=False, timeout=self._timeout
            )
        except CALL_FAILURES as exc:
            logger.warning("gateway call %s failed: %s", name, exc)
            return GatewayResult(text="", error=f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS])
        texts = [getattr(block, "text", "") for block in result.content]
        return result_from_blocks([t for t in texts if t], bool(result.is_error))


def check_gateway_url(url: str) -> None:
    """Refuse to send the bearer token anywhere it could be read in transit.

    HTTPS is always fine. Plain HTTP is allowed only to hosts that cannot be
    on the public internet: a single-label container/service name (e.g.
    ``mcp-trentina`` on a podman network), ``localhost``, or a loopback or
    private IP address. Raises ValueError otherwise.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme == "https":
        return
    if parts.scheme != "http" or not host:
        raise ValueError(f"gateway URL must be http(s) with a host: {url!r}")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        # IP literals first: a public IPv6 address has no dot and must not pass
        # as a "single-label service name".
        if address.is_private or address.is_loopback:
            return
        raise ValueError(f"plain-HTTP gateway URL must be internal, got host {host!r}")
    # Only plain ASCII names qualify as internal: a Unicode dot such as "。"
    # would otherwise pass as "no dot" and be normalised to "." on the wire.
    if host == "localhost" or _SERVICE_NAME.fullmatch(host):
        return
    raise ValueError(f"plain-HTTP gateway URL must be internal, got host {host!r}")


def connect_gateway(url: str, token: str, timeout: float = DEFAULT_CALL_TIMEOUT) -> TrentinaGateway:
    """A gateway over streamable HTTP, authenticating with the profile's bearer token.

    Args:
        url: The profile's gateway endpoint, ``.../gateway/<profile>/mcp``.
        token: The profile's bearer token, sent as ``Authorization: Bearer``.
        timeout: Seconds allowed for EACH tool call, not for the whole sweep
            (the sweep as a whole is bounded by ``METSUKE_SWEEP_TIMEOUT_SECONDS``).

    Returns:
        An unopened ``TrentinaGateway``; use it as ``async with``.

    Raises:
        ValueError: ``url`` fails ``check_gateway_url``.
    """
    check_gateway_url(url)
    transport = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {token}"})
    return TrentinaGateway(Client(transport, timeout=timeout), timeout)
