"""The local authenticating proxy: a loopback listener that adds the upstream credentials.

Every agent child, MCP server and the gateway's own HTTP clients are pointed at
``http://crew:<token>@127.0.0.1:<port>``. A request is served only when its
``Proxy-Authorization`` carries that token, so another process on the machine
cannot borrow the operator's proxy account. The proxy then opens the
connection itself -- through the upstream proxy with its ``Proxy-Authorization``,
or straight to the host when the host is in ``no_proxy`` -- and copies bytes.

``CONNECT`` (HTTPS, WSS) tunnels end to end: TLS runs between the client and the
destination and is never terminated here. A plain-HTTP absolute-form request is
forwarded once, with ``Connection: close``. No header value is ever logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hmac
import logging
import urllib.parse
from dataclasses import dataclass

from kiro_crew.auth_proxy.handshake import (
    Builder,
    Exchange,
    UpstreamAuth,
    exchange,
    offered_schemes,
)
from kiro_crew.auth_proxy.upstream import (
    Upstream,
    bypasses_upstream,
    is_forbidden_target,
    split_authority,
)

logger = logging.getLogger("kiro_crew.auth_proxy")

#: Largest request or response head read, and the stream buffer limit.
HEAD_LIMIT = 64 * 1024
#: Bytes copied per read while tunnelling.
_CHUNK = 64 * 1024
#: How long the second direction of a tunnel may run after the first one ended.
_HALF_CLOSE_GRACE_SECS = 300.0
#: How long closing one connection may wait for the peer.
_CLOSE_TIMEOUT_SECS = 5.0
#: Largest plain-HTTP request body forwarded through the upstream; it is held so a
#: Windows sign-in can resend the request on each leg.
_MAX_FORWARD_BODY = 1024 * 1024
#: The realm named in the challenge a client without the token receives.
REALM = "Sao Khue local proxy"
#: Hop-by-hop headers dropped before a plain-HTTP request is forwarded.
_HOP_HEADERS = frozenset(
    {"proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "upgrade"}
)


@dataclass(frozen=True)
class ProxySettings:
    """Everything one proxy process serves with.

    ``allow_loopback_targets`` exists for tests that run their destination on
    loopback; production always refuses loopback and link-local destinations.
    """

    token: str
    upstream: Upstream | None
    auth: UpstreamAuth
    no_proxy: str = ""
    max_tunnels: int = 256
    connect_timeout: float = 10.0
    head_timeout: float = 30.0
    allow_loopback_targets: bool = False


@dataclass
class _Request:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return None


def _parse_head(data: bytes) -> _Request:
    """Parse a request head; ``ValueError`` when it is not HTTP/1.x."""
    text = data.decode("latin-1")
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise ValueError("not an HTTP/1.x request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip():
            raise ValueError("malformed header line")
        headers.append((name, value.strip()))
    return _Request(parts[0].upper(), parts[1], parts[2], headers)


async def _reply(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    body: str = "",
    extra: tuple[tuple[str, str], ...] = (),
) -> None:
    payload = body.encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {reason}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(payload)}",
        "Connection: close",
        *(f"{name}: {value}" for name, value in extra),
    ]
    with contextlib.suppress(ConnectionError, OSError):
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + payload)
        await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), _CLOSE_TIMEOUT_SECS)


async def _copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Copy until *src* ends, then half-close *dst* so the peer sees the end too."""
    try:
        while True:
            data = await src.read(_CHUNK)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if dst.can_write_eof():
                dst.write_eof()


async def _pump(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    remote_r: asyncio.StreamReader,
    remote_w: asyncio.StreamWriter,
) -> None:
    """Copy both directions until both end, bounding a peer that never closes."""
    tasks = {
        asyncio.ensure_future(_copy(client_r, remote_w)),
        asyncio.ensure_future(_copy(remote_r, client_w)),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if pending:
            await asyncio.wait(pending, timeout=_HALF_CLOSE_GRACE_SECS)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ProxyServer:
    """One listener; :meth:`start` binds it, :meth:`close` stops it and its connections."""

    def __init__(self, settings: ProxySettings) -> None:
        self._s = settings
        self._slots = asyncio.Semaphore(settings.max_tunnels)
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Bind *host*:*port* (0 picks a free port) and return the bound port."""
        self._server = await asyncio.start_server(self._handle, host, port, limit=HEAD_LIMIT)
        return int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), _CLOSE_TIMEOUT_SECS)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ── one client connection ──

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve(reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("auth-proxy: request failed")
        finally:
            await _close(writer)
            if task is not None:
                self._tasks.discard(task)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self._s.head_timeout)
        except asyncio.LimitOverrunError:
            await _reply(writer, 431, "Request Header Fields Too Large")
            return
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
            return
        try:
            req = _parse_head(head)
        except ValueError:
            await _reply(writer, 400, "Bad Request")
            return
        if not self._client_authorized(req):
            await _reply(
                writer,
                407,
                "Proxy Authentication Required",
                extra=(("Proxy-Authenticate", f'Basic realm="{REALM}"'),),
            )
            return
        if self._slots.locked():
            await _reply(writer, 503, "Service Unavailable", "too many open tunnels")
            return
        async with self._slots:
            if req.method == "CONNECT":
                await self._connect(req, reader, writer)
            else:
                await self._forward(req, reader, writer)

    def _client_authorized(self, req: _Request) -> bool:
        value = req.header("proxy-authorization") or ""
        scheme, _, credentials = value.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(credentials.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False
        _, sep, password = decoded.partition(":")
        if not sep:
            return False
        return hmac.compare_digest(password.encode("utf-8"), self._s.token.encode("utf-8"))

    def _refused(self, host: str) -> bool:
        return not self._s.allow_loopback_targets and is_forbidden_target(host)

    def _direct(self, host: str) -> bool:
        return self._s.upstream is None or bypasses_upstream(host, self._s.no_proxy)

    async def _open(
        self, host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=HEAD_LIMIT), self._s.connect_timeout
        )

    async def _upstream_exchange(
        self, build: Builder, writer: asyncio.StreamWriter
    ) -> Exchange | None:
        """Send ``build(header)`` through the upstream, signing in; ``None`` after answering.

        A 407 that ends the exchange is answered 407 WITHOUT the upstream's
        challenge: the client would answer it with the local token, which the
        upstream rejects again, in a loop.
        """
        upstream = self._s.upstream
        assert upstream is not None
        connected = False

        async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            nonlocal connected
            pair = await self._open(upstream.host, upstream.port)
            connected = True
            return pair

        try:
            done = await exchange(connect, build, self._s.auth, head_timeout=self._s.head_timeout)
        except (
            OSError,
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ValueError,
        ) as exc:
            if not connected:
                logger.warning(
                    "auth-proxy: cannot reach upstream proxy %s (%s)",
                    upstream.authority,
                    type(exc).__name__,
                )
                await _reply(
                    writer, 502, "Bad Gateway", f"cannot reach upstream proxy {upstream.authority}"
                )
            else:
                logger.warning(
                    "auth-proxy: upstream proxy %s gave no answer (%s)",
                    upstream.authority,
                    type(exc).__name__,
                )
                await _reply(writer, 502, "Bad Gateway", "upstream proxy gave no answer")
            return None
        if done.status == 407:
            logger.warning(
                "auth-proxy: upstream proxy %s rejected %s credentials%s (it offers: %s)",
                upstream.authority,
                self._s.auth.scheme,
                self._user_suffix(),
                ", ".join(offered_schemes(done.head)) or "none",
            )
            await _close(done.writer)
            await _reply(
                writer,
                407,
                "Proxy Authentication Required",
                "the upstream proxy rejected the configured credentials",
            )
            return None
        return done

    def _user_suffix(self) -> str:
        username = getattr(self._s.auth, "username", "")
        return f" for user {username}" if username else ""

    # ── CONNECT ──

    async def _connect(
        self, req: _Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            host, port = split_authority(req.target, 443)
        except ValueError:
            await _reply(writer, 400, "Bad Request")
            return
        if self._refused(host):
            await _reply(writer, 403, "Forbidden", "destination refused by the local proxy")
            return
        if self._direct(host):
            try:
                remote_r, remote_w = await self._open(host, port)
            except (OSError, asyncio.TimeoutError) as exc:
                logger.info(
                    "auth-proxy: CONNECT %s direct failed (%s)", req.target, type(exc).__name__
                )
                await _reply(writer, 502, "Bad Gateway")
                return
        else:
            authority = req.target

            def build(header: str | None) -> bytes:
                lines = [f"CONNECT {authority} HTTP/1.1", f"Host: {authority}"]
                if header:
                    lines.append(f"Proxy-Authorization: {header}")
                return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

            done = await self._upstream_exchange(build, writer)
            if done is None:
                return
            if done.status != 200:
                logger.info(
                    "auth-proxy: upstream proxy answered CONNECT %s with %s",
                    authority,
                    done.status,
                )
                await _close(done.writer)
                await _reply(writer, 502, "Bad Gateway", f"upstream proxy answered {done.status}")
                return
            remote_r, remote_w = done.reader, done.writer
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await _pump(reader, writer, remote_r, remote_w)
        finally:
            await _close(remote_w)

    # ── plain HTTP ──

    async def _read_body(
        self, req: _Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bytes | None:
        """The whole request body, held for resending; ``None`` after answering the client."""
        if "chunked" in (req.header("transfer-encoding") or "").lower():
            await _reply(writer, 411, "Length Required", "send a Content-Length through the proxy")
            return None
        raw = req.header("content-length")
        if not raw:
            return b""
        try:
            length = int(raw)
        except ValueError:
            await _reply(writer, 400, "Bad Request")
            return None
        if length > _MAX_FORWARD_BODY:
            await _reply(writer, 413, "Payload Too Large")
            return None
        try:
            return await asyncio.wait_for(reader.readexactly(length), self._s.head_timeout)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):
            return None

    async def _forward(
        self, req: _Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        parts = urllib.parse.urlsplit(req.target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            await _reply(writer, 400, "Bad Request", "only absolute http:// requests are forwarded")
            return
        host, port = parts.hostname, parts.port or 80
        if self._refused(host):
            await _reply(writer, 403, "Forbidden", "destination refused by the local proxy")
            return
        headers = [(k, v) for k, v in req.headers if k.lower() not in _HOP_HEADERS]
        if not any(k.lower() == "host" for k, _ in headers):
            headers.insert(0, ("Host", parts.netloc.rpartition("@")[2]))
        headers.append(("Connection", "close"))
        if self._direct(host):
            target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
            try:
                remote_r, remote_w = await self._open(host, port)
            except (OSError, asyncio.TimeoutError) as exc:
                logger.info(
                    "auth-proxy: %s %s direct failed (%s)", req.method, host, type(exc).__name__
                )
                await _reply(writer, 502, "Bad Gateway")
                return
            head = f"{req.method} {target} {req.version}\r\n" + "".join(
                f"{k}: {v}\r\n" for k, v in headers
            )
            try:
                remote_w.write((head + "\r\n").encode("latin-1"))
                await remote_w.drain()
                await _pump(reader, writer, remote_r, remote_w)
            finally:
                await _close(remote_w)
            return
        body = await self._read_body(req, reader, writer)
        if body is None:
            return

        def build(header: str | None) -> bytes:
            lines = [f"{req.method} {req.target} {req.version}", *(f"{k}: {v}" for k, v in headers)]
            if header:
                lines.append(f"Proxy-Authorization: {header}")
            return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

        done = await self._upstream_exchange(build, writer)
        if done is None:
            return
        try:
            writer.write(done.head)
            await writer.drain()
            await _pump(reader, writer, done.reader, done.writer)
        finally:
            await _close(done.writer)
