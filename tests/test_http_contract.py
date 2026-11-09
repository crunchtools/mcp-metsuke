"""The outbound HTTP contract (constitution v1.36.0, MCP Server profile Layer 3).

A real `httpx.AsyncClient` over a `MockTransport`: the agent string is a header
the client adds and the ceiling is enforced while the client reads, so a mock
standing in for `post` could see neither.

Two outbound paths here. This one is the gather callback, whose client this
code builds. The other is the Trentina gateway, whose body is read inside
fastmcp's own httpx2 client; what this code owns there is the header and the
declared SSE event ceiling, and `TestTheGatewayTransport` pins both.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import version
from typing import Any

import httpx
import pytest

from mcp_metsuke_crunchtools import config as config_mod
from mcp_metsuke_crunchtools import scheduler
from mcp_metsuke_crunchtools.errors import CallbackDispatchError
from mcp_metsuke_crunchtools.sweep import client as sweep_client

Handler = Callable[[httpx.Request], httpx.Response]
_REAL_ASYNC_CLIENT = httpx.AsyncClient


async def _chunks(chunk: bytes, times: int) -> AsyncIterator[bytes]:
    """A chunked body, which carries no content-length -- what a header check misses."""
    for _ in range(times):
        yield chunk


@contextmanager
def _serving(handler: Handler, monkeypatch: pytest.MonkeyPatch) -> Iterator[httpx.AsyncClient]:
    """A client built the way production builds it, over a mock transport."""
    monkeypatch.setenv("TRENTINA_ALERT_URL", "http://trentina:8019")
    monkeypatch.setenv("METSUKE_ALERT_TOKEN", "test-token")  # gatehouse: allow-secret
    config_mod._config = None

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = httpx.AsyncClient(headers={"User-Agent": scheduler.USER_AGENT})
    try:
        yield client
    finally:
        config_mod._config = None


async def _fire(client: httpx.AsyncClient) -> int:
    """One callback dispatch, with everything but the HTTP call already decided."""
    return await scheduler._post_alert(client, config_mod.get_config(), "r", "r@1")


class TestUserAgent:
    """A request says what it is, and the version cannot drift from the release."""

    @pytest.mark.asyncio
    async def test_the_callback_names_the_product_and_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["User-Agent"])
            return httpx.Response(200, json={"ok": True})

        with _serving(handler, monkeypatch) as client:
            await _fire(client)

        expected = (
            f"mcp-metsuke-crunchtools/{version('mcp-metsuke-crunchtools')} "
            "(+https://github.com/crunchtools/mcp-metsuke)"
        )
        assert seen == [expected]

    def test_the_version_comes_from_package_metadata(self) -> None:
        """Not a literal: a literal drifts from pyproject.toml at the next release."""
        assert version("mcp-metsuke-crunchtools") in scheduler.USER_AGENT

    def test_both_paths_send_the_same_string(self) -> None:
        """Two modules, one agent: a log cannot tell them apart and should not."""
        assert scheduler.USER_AGENT == sweep_client.USER_AGENT


class TestResponseCeiling:
    """There was no limit at all: `client.post` read whatever answered, whole."""

    @pytest.mark.asyncio
    async def test_a_chunked_body_over_the_cap_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        over = scheduler.MAX_RESPONSE_SIZE // 1024 + 1

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_chunks(b"x" * 1024, over))

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "byte limit" in str(caught.value)

    @pytest.mark.asyncio
    async def test_one_oversized_chunk_is_refused_before_it_is_copied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`aiter_bytes()` promises nothing about chunk size: one can be the body."""
        whole = b"y" * (scheduler.MAX_RESPONSE_SIZE + 1)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_chunks(whole, 1))

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "byte limit" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_declared_length_over_the_cap_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            response = httpx.Response(200, json={"ok": True})
            response.headers["content-length"] = str(scheduler.MAX_RESPONSE_SIZE + 1)
            return response

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "declared" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_malformed_declared_length_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three repos in this fleet called `int()` on it unguarded."""

        def handler(_request: httpx.Request) -> httpx.Response:
            response = httpx.Response(200, json={"ok": True})
            response.headers["content-length"] = "not-a-number"
            return response

        with _serving(handler, monkeypatch) as client:
            assert await _fire(client) == 200


class TestErrorNamesTheSite:
    """RT #1596: the error named neither the site nor the body for an afternoon."""

    @pytest.mark.asyncio
    async def test_a_login_page_is_quoted_with_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"content-type": "text/html"},
                text="<title>Sign in</title>",
            )

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        message = str(caught.value)
        assert "POST http://trentina:8019/alert" in message
        assert "HTTP 302" in message
        assert "text/html" in message
        assert "Sign in" in message

    @pytest.mark.asyncio
    async def test_an_empty_body_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"")

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "<empty body>" in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_token_is_not_in_the_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Trentina #333: the token rides in a header, so no error can echo it.

        The body is echoed into the message, and the request body carries no
        credential -- only the report name, the run id and the gather spec.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            # A debug page that quotes the request it received, headers aside.
            return httpx.Response(400, text=request.content.decode())

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "test-token" not in str(caught.value)


class TestTheNamedUrlCarriesNoCredential:
    """The alert URL comes from configuration, so it can carry anything."""

    @pytest.mark.asyncio
    async def test_userinfo_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, headers={"content-type": "text/html"}, text="bad gateway")

        with _serving(handler, monkeypatch) as client:
            monkeypatch.setenv("TRENTINA_ALERT_URL", "http://alice:letmein@trentina:8019")
            config_mod._config = None
            with pytest.raises(CallbackDispatchError) as caught:
                await _fire(client)

        assert "letmein" not in str(caught.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "configured",
        [
            "http://trentina:8019?token=the-secret",
            "http://trentina:8019?api_key=the-secret",
            "http://trentina:8019?anything_at_all=the-secret",
            "http://trentina:8019/services/the-secret",
            "http://trentina:8019#the-secret",
        ],
    )
    async def test_nothing_a_configured_url_can_hide_a_secret_in_survives(
        self, configured: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not a list of credential names: this URL's parts are not ours to know.

        A by-name query list was the first version of this and it was wrong --
        it misses `api_key` and `client_secret`, and a webhook URL whose PATH
        is the credential is an ordinary pattern. So the query, the fragment
        and the configured path segments all go.
        """

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, headers={"content-type": "text/html"}, text="bad gateway")

        with _serving(handler, monkeypatch) as client:
            monkeypatch.setenv("TRENTINA_ALERT_URL", configured)
            config_mod._config = None
            with pytest.raises(CallbackDispatchError) as caught:
                await _fire(client)

        message = str(caught.value)
        assert "the-secret" not in message
        # The host and the endpoint survive, which is what makes it diagnosable.
        assert "trentina:8019" in message
        assert "alert" in message

    @pytest.mark.asyncio
    async def test_a_transport_failure_redacts_it_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The path with no response to read the URL off, so it builds its own."""

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        with _serving(handler, monkeypatch) as client:
            monkeypatch.setenv("TRENTINA_ALERT_URL", "http://alice:letmein@trentina:8019")
            config_mod._config = None
            with pytest.raises(CallbackDispatchError) as caught:
                await _fire(client)

        message = str(caught.value)
        assert "letmein" not in message
        assert "trentina:8019/alert" in message


class TestTransportFailuresNameTheRequest:
    """A timeout with no endpoint in it is the hardest failure to place."""

    @pytest.mark.asyncio
    async def test_a_timeout_names_the_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timeout")

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert str(caught.value) == (
            "Failed to dispatch callback for report 'r': POST http://trentina:8019/alert: timeout"
        )

    @pytest.mark.asyncio
    async def test_a_failure_mid_stream_is_still_a_dispatch_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The streaming read is a new failure point: the old code read eagerly."""

        async def failing() -> AsyncIterator[bytes]:
            yield b'{"ok"'
            raise httpx.ReadTimeout("died")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=failing())

        with (
            _serving(handler, monkeypatch) as client,
            pytest.raises(CallbackDispatchError) as caught,
        ):
            await _fire(client)

        assert "died" in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_body_still_goes_out_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Streaming changed how the reply is read, not what is sent."""
        seen: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})

        with _serving(handler, monkeypatch) as client:
            await _fire(client)

        assert seen == [{"report": "r", "run_id": "r@1"}]


class TestTheGatewayTransport:
    """The gateway's body is read inside fastmcp; the header and cap are ours."""

    def test_the_transport_carries_the_agent_and_a_declared_cap(self) -> None:
        gateway = sweep_client.connect_gateway("http://mcp-trentina/gateway/x/mcp", "tok")
        transport = gateway._client.transport

        assert transport.headers["User-Agent"] == sweep_client.USER_AGENT
        assert transport.headers["Authorization"] == "Bearer tok"
        # Declared rather than defaulted: a fastmcp release that changes its
        # default cannot change what this server accepts without a diff saying
        # so. Read off the transport, not off our own constant, so the test
        # fails if the keyword is ever renamed out from under us.
        assert transport.max_sse_event_size == sweep_client.MAX_SSE_EVENT_BYTES
