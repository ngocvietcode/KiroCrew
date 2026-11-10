"""Windows sign-in (NTLM / Negotiate) through the local proxy.

Two layers. Scripted contexts drive every rule of the multi-leg exchange against a
fake company proxy. On Windows, a second fake proxy checks the tokens with the
real SSPI server side (``AcceptSecurityContext``), so the real client side signs
in as this machine's account end to end, no domain needed. Every listener binds
port 0 on loopback and every wait is bounded.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging

import pytest

from kiro_crew.auth_proxy import sspi
from kiro_crew.auth_proxy.auth import WindowsAuth, resolve_auth_mode
from kiro_crew.auth_proxy.handshake import challenge_token, drain_body, offered_schemes
from kiro_crew.auth_proxy.probe import probe_upstream
from kiro_crew.auth_proxy.server import ProxyServer, ProxySettings
from kiro_crew.auth_proxy.upstream import Upstream

_TOKEN = "local-token"
_WAIT = 10.0


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _ScriptedContext:
    """Sends T1, then T3 after the server's T2; records what it was given."""

    def __init__(self, log: list[bytes | None]):
        self._log = log
        self.done = False

    def step(self, server_token: bytes | None) -> bytes:
        self._log.append(server_token)
        if server_token is None:
            return b"T1"
        self.done = True
        return b"T3:" + server_token

    def close(self) -> None:
        pass


def _scripted_factory(log: list[bytes | None], made: list[str]):
    def make(scheme: str, target: str):
        made.append(f"{scheme} {target}")
        return _ScriptedContext(log)

    return make


class _NtlmProxy:
    """A company proxy that wants a three-leg exchange on one connection.

    ``offers`` are the schemes of the bare 407; ``close_after`` names the round
    after which it drops the connection (``"bare"`` or ``"challenge"``);
    ``reject`` answers the third leg with another bare 407; ``chunked`` sends the
    challenge body chunked.
    """

    def __init__(
        self, offers=("Negotiate", "NTLM"), *, close_after="", reject=False, chunked=False
    ):
        self.offers = offers
        self.close_after = close_after
        self.reject = reject
        self.chunked = chunked
        self.connections = 0
        self.requests: list[tuple[int, str, str | None]] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    def _challenge(self, values, *, close: bool) -> bytes:
        lines = ["HTTP/1.1 407 Proxy Authentication Required"]
        lines += [f"Proxy-Authenticate: {v}" for v in values]
        body = b"auth needed"
        if close:
            lines.append("Connection: close")
        if self.chunked:
            lines.append("Transfer-Encoding: chunked")
            payload = b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body)
        else:
            lines.append(f"Content-Length: {len(body)}")
            payload = body
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + payload

    async def _handle(self, reader, writer):
        self.connections += 1
        conn = self.connections
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
                auth = next(
                    (
                        line.split(":", 1)[1].strip()
                        for line in head
                        if line.lower().startswith("proxy-authorization:")
                    ),
                    None,
                )
                self.requests.append((conn, head[0], auth))
                if auth is None:
                    writer.write(self._challenge(self.offers, close=self.close_after == "bare"))
                    await writer.drain()
                    if self.close_after == "bare":
                        return
                    continue
                scheme, _, token = auth.partition(" ")
                raw = base64.b64decode(token)
                if raw == b"T1":
                    writer.write(
                        self._challenge(
                            [f"{scheme} {_b64(b'T2')}"], close=self.close_after == "challenge"
                        )
                    )
                    await writer.drain()
                    if self.close_after == "challenge":
                        return
                    continue
                if raw == b"T3:T2" and not self.reject:
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    await writer.drain()
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                    return
                writer.write(self._challenge([scheme], close=False))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


class _Echo:
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


async def _tunnel(proxy_port: int, target: str, payload: bytes = b"ping") -> tuple[str, bytes]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", proxy_port), _WAIT)
    auth = _b64(f"crew:{_TOKEN}".encode())
    writer.write(f"CONNECT {target} HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode())
    await writer.drain()
    head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _WAIT)).decode("latin-1")
    body = b""
    if head.startswith("HTTP/1.1 200"):
        writer.write(payload)
        await writer.drain()
        body = await asyncio.wait_for(reader.readexactly(len(payload)), _WAIT)
    writer.close()
    return head, body


@contextlib.asynccontextmanager
async def _stack(company: _NtlmProxy, auth: WindowsAuth):
    echo = _Echo()
    echo_port = await echo.start()
    company_port = await company.start()
    proxy = ProxyServer(
        ProxySettings(
            token=_TOKEN,
            upstream=Upstream("127.0.0.1", company_port),
            auth=auth,
            allow_loopback_targets=True,
        )
    )
    port = await proxy.start()
    try:
        yield port, f"127.0.0.1:{echo_port}"
    finally:
        await proxy.close()
        await company.close()
        await echo.close()


# ── the exchange, with scripted contexts ──


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True], ids=["content-length", "chunked"])
async def test_three_legs_on_one_connection_prefer_negotiate(chunked):
    log: list[bytes | None] = []
    made: list[str] = []
    company = _NtlmProxy(chunked=chunked)
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory(log, made))
    async with _stack(company, auth) as (port, target):
        head, body = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 200") and body == b"ping"
    assert made == ["Negotiate HTTP/proxy.corp.test"]
    assert log == [None, b"T2"]
    assert company.connections == 1
    assert [auth for _, _, auth in company.requests] == [
        None,
        f"Negotiate {_b64(b'T1')}",
        f"Negotiate {_b64(b'T3:T2')}",
    ]


@pytest.mark.asyncio
async def test_ntlm_is_used_when_the_proxy_offers_only_ntlm():
    made: list[str] = []
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], made))
    async with _stack(_NtlmProxy(offers=("NTLM", 'Basic realm="corp"')), auth) as (port, target):
        head, _ = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 200")
    assert made == ["NTLM HTTP/proxy.corp.test"]


@pytest.mark.asyncio
async def test_the_chosen_scheme_is_sent_at_once_on_the_next_connection():
    company = _NtlmProxy()
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], []))
    async with _stack(company, auth) as (port, target):
        await _tunnel(port, target)
        head, _ = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 200")
    second = [auth for conn, _, auth in company.requests if conn == 2]
    assert second == [f"Negotiate {_b64(b'T1')}", f"Negotiate {_b64(b'T3:T2')}"]


@pytest.mark.asyncio
async def test_a_proxy_that_closes_after_the_bare_407_gets_the_first_token_on_a_new_connection():
    company = _NtlmProxy(close_after="bare")
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], []))
    async with _stack(company, auth) as (port, target):
        head, body = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 200") and body == b"ping"
    assert company.connections == 2
    assert [auth is None for conn, _, auth in company.requests if conn == 1] == [True]


@pytest.mark.asyncio
async def test_a_proxy_that_closes_mid_exchange_is_a_rejection(caplog):
    company = _NtlmProxy(close_after="challenge")
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], []))
    with caplog.at_level(logging.WARNING, logger="kiro_crew.auth_proxy"):
        async with _stack(company, auth) as (port, target):
            head, _ = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 407")
    assert company.connections == 1
    assert "rejected windows credentials" in caplog.text


@pytest.mark.asyncio
async def test_a_rejected_third_leg_is_a_407_without_the_upstream_challenge():
    auth = WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], []))
    async with _stack(_NtlmProxy(reject=True), auth) as (port, target):
        head, _ = await _tunnel(port, target)
    assert head.startswith("HTTP/1.1 407")
    assert "Proxy-Authenticate" not in head


@pytest.mark.asyncio
async def test_the_connection_test_reports_the_negotiated_scheme():
    company = _NtlmProxy(offers=("NTLM",))
    port = await company.start()
    try:
        result = await probe_upstream(
            Upstream("127.0.0.1", port),
            WindowsAuth("proxy.corp.test", context_factory=_scripted_factory([], [])),
        )
    finally:
        await company.close()
    assert result.to_json() == {
        "ok": True,
        "outcome": "ok",
        "status": 200,
        "schemes": [],
        "scheme": "NTLM",
    }


# ── parsing and rules ──


def test_challenge_parsing():
    head = (
        b"HTTP/1.1 407 x\r\nProxy-Authenticate: Negotiate\r\nProxy-Authenticate: NTLM VDI=\r\n"
        b'Proxy-Authenticate: Basic realm="corp"\r\nProxy-Authenticate: ntlm dup\r\n\r\n'
    )
    assert offered_schemes(head) == ["Negotiate", "NTLM", "Basic"]
    assert challenge_token(head, "NTLM") == "VDI="
    assert challenge_token(head, "Negotiate") is None
    assert challenge_token(head, "Basic") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head, body, reusable",
    [
        (b"HTTP/1.1 407 x\r\nContent-Length: 3\r\n\r\n", b"abcNEXT", True),
        (
            b"HTTP/1.1 407 x\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"3\r\nabc\r\n0\r\n\r\nNEXT",
            True,
        ),
        (b"HTTP/1.1 407 x\r\nContent-Length: 3\r\nConnection: close\r\n\r\n", b"abcNEXT", False),
        (b"HTTP/1.1 407 x\r\n\r\n", b"abcNEXT", False),
        (b"HTTP/1.0 407 x\r\nContent-Length: 3\r\n\r\n", b"abcNEXT", False),
    ],
)
async def test_drain_body(head, body, reusable):
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    assert await drain_body(reader, head) is reusable
    if reusable:
        assert await reader.read() == b"NEXT"


@pytest.mark.parametrize(
    "mode, username, windows, expected",
    [
        ("auto", "alice", True, "basic"),
        ("auto", "", True, "windows"),
        ("auto", "", False, "none"),
        ("basic", "", True, "basic"),
        ("windows", "alice", True, "windows"),
    ],
)
def test_resolve_auth_mode(mode, username, windows, expected):
    assert resolve_auth_mode(mode, username, windows_available=windows) == expected


# ── the real SSPI client against the real SSPI server side ──


class _SspiCheckingProxy:
    """A company proxy that verifies each connection's tokens with AcceptSecurityContext."""

    def __init__(self, offers=("Negotiate", "NTLM")):
        self.offers = offers
        self.accepted: list[str] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        contexts: dict[str, sspi.SspiServerContext] = {}
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
                auth = next(
                    (
                        line.split(":", 1)[1].strip()
                        for line in head
                        if line.lower().startswith("proxy-authorization:")
                    ),
                    None,
                )
                if auth is None:
                    challenge = "".join(f"Proxy-Authenticate: {o}\r\n" for o in self.offers)
                    writer.write(
                        f"HTTP/1.1 407 Need auth\r\n{challenge}Content-Length: 0\r\n\r\n".encode()
                    )
                    await writer.drain()
                    continue
                scheme, _, token = auth.partition(" ")
                if scheme not in contexts:
                    creds = sspi.SspiCredentials(
                        sspi.PACKAGE_FOR_SCHEME[scheme.lower()], inbound=True
                    )
                    contexts[scheme] = sspi.SspiServerContext(creds)
                ctx = contexts[scheme]
                reply = await asyncio.to_thread(ctx.step, base64.b64decode(token))
                if ctx.done:
                    self.accepted.append(scheme)
                    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    await writer.drain()
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                    return
                writer.write(
                    f"HTTP/1.1 407 Continue\r\nProxy-Authenticate: {scheme} {_b64(reply)}\r\nContent-Length: 0\r\n\r\n".encode()
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            for ctx in contexts.values():
                ctx.close()
            writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


windows_only = pytest.mark.skipif(not sspi.available(), reason="SSPI is Windows only")


@windows_only
@pytest.mark.asyncio
@pytest.mark.parametrize("offers", [("Negotiate", "NTLM"), ("NTLM",)], ids=["negotiate", "ntlm"])
async def test_real_sspi_signs_in_as_this_account_end_to_end(offers):
    echo = _Echo()
    echo_port = await echo.start()
    company = _SspiCheckingProxy(offers)
    company_port = await company.start()
    proxy = ProxyServer(
        ProxySettings(
            token=_TOKEN,
            upstream=Upstream("127.0.0.1", company_port),
            auth=WindowsAuth("localhost"),
            allow_loopback_targets=True,
        )
    )
    port = await proxy.start()
    try:
        head, body = await _tunnel(port, f"127.0.0.1:{echo_port}", b"real sspi")
    finally:
        await proxy.close()
        await company.close()
        await echo.close()
    assert head.startswith("HTTP/1.1 200") and body == b"real sspi"
    assert company.accepted == [offers[0]]


@windows_only
@pytest.mark.asyncio
async def test_real_sspi_connection_test():
    company = _SspiCheckingProxy(("NTLM",))
    port = await company.start()
    try:
        result = await probe_upstream(Upstream("127.0.0.1", port), WindowsAuth("localhost"))
    finally:
        await company.close()
    assert result.ok and result.scheme == "NTLM"


def test_the_token_is_completed_before_its_buffer_is_freed(monkeypatch):
    """CompleteAuthToken reads the output buffer, so it must run before FreeContextBuffer."""
    import ctypes

    calls: list[str] = []
    payload = ctypes.create_string_buffer(b"token", 5)

    class FakeSecur32:
        def InitializeSecurityContextW(self, *args):  # noqa: N802 - the SSPI name
            out_buf = ctypes.cast(args[9], ctypes.POINTER(sspi.SecBufferDesc)).contents.pBuffers[0]
            out_buf.pvBuffer = ctypes.cast(payload, ctypes.c_void_p).value
            out_buf.cbBuffer = 5
            calls.append("initialize")
            return sspi.SEC_I_COMPLETE_NEEDED

        def CompleteAuthToken(self, *args):  # noqa: N802
            calls.append("complete")
            return sspi.SEC_E_OK

        def FreeContextBuffer(self, *args):  # noqa: N802
            calls.append("free")
            return sspi.SEC_E_OK

    monkeypatch.setattr(sspi, "_secur32", lambda: FakeSecur32())

    class Credentials:
        handle = sspi.SecHandle()

    context = sspi.SspiContext(Credentials(), "HTTP/proxy.test")
    assert context.step(None) == b"token"
    assert context.done
    assert calls == ["initialize", "complete", "free"]
    context._started = False  # the fake holds no context to delete
