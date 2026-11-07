"""The local proxy end to end, in process: a fake company proxy, an echo destination, real clients.

Every listener binds port 0 on loopback and every wait is bounded.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import urllib.request

import aiohttp
import pytest

from kiro_crew.auth_proxy.auth import BasicAuth, NoAuth
from kiro_crew.auth_proxy.handshake import UpstreamAuth
from kiro_crew.auth_proxy.server import ProxyServer, ProxySettings
from kiro_crew.auth_proxy.upstream import Upstream
from kiro_crew.testing.wait import FALLBACK_TIMEOUT_SECS

pytestmark = pytest.mark.asyncio

_TOKEN = "local-token-123"
_WAIT = 10.0
_GOOD_AUTH = "Basic " + base64.b64encode(b"alice:p@ss").decode("ascii")


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


class _Echo:
    """A destination that echoes every byte back."""

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        with contextlib.suppress(ConnectionError, OSError):
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


class _CompanyProxy:
    """Answers like a company proxy: Basic or 407, then tunnels CONNECT or answers GET itself."""

    def __init__(self, expected_auth: str = _GOOD_AUTH):
        self.expected_auth = expected_auth
        self.seen: list[tuple[str, str | None]] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            lines = head.split("\r\n")
            auth = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in lines
                    if line.lower().startswith("proxy-authorization:")
                ),
                None,
            )
            self.seen.append((lines[0], auth))
            if auth != self.expected_auth:
                writer.write(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: NTLM\r\nProxy-Authenticate: Basic realm="corp"\r\n'
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            method, target, _ = lines[0].split(" ")
            if method == "CONNECT":
                host, port = target.rsplit(":", 1)
                remote_r, remote_w = await asyncio.open_connection(host, int(port))
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()

                async def copy(src, dst):
                    with contextlib.suppress(ConnectionError, OSError):
                        while data := await src.read(65536):
                            dst.write(data)
                            await dst.drain()
                    with contextlib.suppress(Exception):
                        dst.write_eof()

                await asyncio.gather(copy(reader, remote_w), copy(remote_r, writer))
                remote_w.close()
            else:
                body = f"upstream saw {target}".encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                    % len(body)
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


@contextlib.asynccontextmanager
async def _stack(*, auth: UpstreamAuth | None = None, no_proxy: str = "", upstream_up=True, **kw):
    echo, company = _Echo(), _CompanyProxy()
    echo_port = await echo.start()
    company_port = await company.start()
    if not upstream_up:
        await company.close()
    settings = ProxySettings(
        token=_TOKEN,
        upstream=Upstream("127.0.0.1", company_port),
        auth=auth or BasicAuth("alice", "p@ss"),
        no_proxy=no_proxy,
        allow_loopback_targets=True,
        **kw,
    )
    proxy = ProxyServer(settings)
    port = await proxy.start()
    try:
        yield port, echo_port, company
    finally:
        await proxy.close()
        if upstream_up:
            await company.close()
        await echo.close()


async def _connect(proxy_port: int, target: str, auth: str | None = _basic("crew", _TOKEN)):
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", proxy_port), _WAIT)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if auth:
        lines.append(f"Proxy-Authorization: {auth}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    await writer.drain()
    head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _WAIT)).decode("latin-1")
    return reader, writer, head


async def _roundtrip(reader, writer, payload: bytes) -> bytes:
    writer.write(payload)
    await writer.drain()
    received = await asyncio.wait_for(reader.readexactly(len(payload)), _WAIT)
    writer.close()
    return received


async def test_connect_goes_through_the_company_proxy_with_basic_and_tunnels_bytes():
    async with _stack() as (port, echo_port, company):
        reader, writer, head = await _connect(port, f"127.0.0.1:{echo_port}")
        assert head.startswith("HTTP/1.1 200")
        payload = bytes(range(256)) * 4096  # 1 MiB, both directions
        assert await _roundtrip(reader, writer, payload) == payload
        assert company.seen == [(f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1", _GOOD_AUTH)]


@pytest.mark.parametrize(
    "auth", [None, _basic("crew", "wrong"), "Bearer " + _TOKEN, "Basic !!notbase64"]
)
async def test_a_client_without_the_token_is_challenged_and_nothing_goes_upstream(auth):
    async with _stack() as (port, echo_port, company):
        _, writer, head = await _connect(port, f"127.0.0.1:{echo_port}", auth=auth)
        writer.close()
        assert head.startswith("HTTP/1.1 407")
        assert 'Proxy-Authenticate: Basic realm="Sao Khue local proxy"' in head
        assert company.seen == []


async def test_a_company_proxy_rejection_is_reported_without_relaying_its_challenge(caplog):
    with caplog.at_level("WARNING", logger="kiro_crew.auth_proxy"):
        async with _stack(auth=BasicAuth("alice", "stale-password")) as (port, echo_port, company):
            _, writer, head = await _connect(port, f"127.0.0.1:{echo_port}")
            writer.close()
    assert head.startswith("HTTP/1.1 407")
    assert "Proxy-Authenticate" not in head
    assert "rejected basic credentials for user alice (it offers: NTLM, Basic)" in caplog.text
    assert "stale-password" not in caplog.text


async def test_a_no_proxy_host_is_reached_directly():
    async with _stack(no_proxy="127.0.0.1") as (port, echo_port, company):
        reader, writer, head = await _connect(port, f"127.0.0.1:{echo_port}")
        assert head.startswith("HTTP/1.1 200")
        assert await _roundtrip(reader, writer, b"direct") == b"direct"
        assert company.seen == []


async def test_without_an_upstream_every_host_is_direct():
    echo = _Echo()
    echo_port = await echo.start()
    proxy = ProxyServer(
        ProxySettings(token=_TOKEN, upstream=None, auth=NoAuth(), allow_loopback_targets=True)
    )
    port = await proxy.start()
    try:
        reader, writer, head = await _connect(port, f"127.0.0.1:{echo_port}")
        assert head.startswith("HTTP/1.1 200")
        assert await _roundtrip(reader, writer, b"hi") == b"hi"
    finally:
        await proxy.close()
        await echo.close()


async def test_loopback_destinations_are_refused_by_default():
    proxy = ProxyServer(ProxySettings(token=_TOKEN, upstream=None, auth=NoAuth()))
    port = await proxy.start()
    try:
        _, writer, head = await _connect(port, "127.0.0.1:22")
        writer.close()
        assert head.startswith("HTTP/1.1 403")
    finally:
        await proxy.close()


async def test_an_unreachable_company_proxy_is_a_502(caplog):
    with caplog.at_level("WARNING", logger="kiro_crew.auth_proxy"):
        async with _stack(upstream_up=False) as (port, echo_port, _company):
            _, writer, head = await _connect(port, f"127.0.0.1:{echo_port}")
            writer.close()
    assert head.startswith("HTTP/1.1 502")
    assert "cannot reach upstream proxy" in caplog.text


async def test_a_full_tunnel_table_answers_503():
    async with _stack(max_tunnels=1) as (port, echo_port, _company):
        held_r, held_w, head = await _connect(port, f"127.0.0.1:{echo_port}")
        assert head.startswith("HTTP/1.1 200")
        _, writer, second = await _connect(port, f"127.0.0.1:{echo_port}")
        writer.close()
        assert second.startswith("HTTP/1.1 503")
        assert await _roundtrip(held_r, held_w, b"still open") == b"still open"


async def test_plain_http_is_forwarded_with_company_credentials_and_without_the_local_token():
    async with _stack() as (port, _echo_port, company):
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), _WAIT)
        writer.write(
            (
                "GET http://intranet.example/path?q=1 HTTP/1.1\r\n"
                "Host: intranet.example\r\n"
                f"Proxy-Authorization: {_basic('crew', _TOKEN)}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), _WAIT)
        writer.close()
    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b"upstream saw http://intranet.example/path?q=1")
    assert company.seen == [("GET http://intranet.example/path?q=1 HTTP/1.1", _GOOD_AUTH)]


async def test_real_clients_reach_the_company_proxy_through_the_loopback_url():
    async with _stack() as (port, _echo_port, company):
        url = f"http://crew:{_TOKEN}@127.0.0.1:{port}"

        async def fetch_aiohttp() -> tuple[int, str]:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://site.example/a", proxy=url) as resp:
                    return resp.status, await resp.text()

        assert await asyncio.wait_for(fetch_aiohttp(), _WAIT) == (
            200,
            "upstream saw http://site.example/a",
        )

        def fetch() -> bytes:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": url}))
            with opener.open("http://site.example/b", timeout=FALLBACK_TIMEOUT_SECS) as resp:
                return resp.read()

        assert await asyncio.to_thread(fetch) == b"upstream saw http://site.example/b"
    assert [line for line, _ in company.seen] == [
        "GET http://site.example/a HTTP/1.1",
        "GET http://site.example/b HTTP/1.1",
    ]
    assert all(auth == _GOOD_AUTH for _, auth in company.seen)
