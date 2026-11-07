"""One request to the upstream proxy, signed in with Basic, NTLM or Negotiate.

Basic is a header on the first request. NTLM and Negotiate are an exchange of
tokens through ``407`` answers that must all happen on ONE connection, so the
driver here reads each ``407`` to its end (the next leg follows on the same
socket), answers the proxy's challenge, and reconnects only where a fresh
connection is allowed: before the first token of an exchange.

The upstream is reached through a ``connect`` callable and every request is
built by a ``build(header)`` callable, so ``CONNECT``, plain HTTP and the
dashboard's connection test share this code.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

#: Most rounds one exchange may take: an unauthenticated try, then up to three legs.
MAX_ROUNDS = 4
#: Largest 407 body read and discarded to keep a connection for the next leg.
MAX_DRAINED_BODY = 1024 * 1024

Connector = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
Builder = Callable[[str | None], bytes]


class AuthSession(Protocol):
    """The exchange for one connection; ``scheme`` is the HTTP scheme it speaks."""

    scheme: str

    async def first(self) -> str | None:
        """The ``Proxy-Authorization`` value of the first leg, or ``None`` to give up."""
        ...

    async def answer(self, token: str) -> str | None:
        """The value answering the proxy's *token*, or ``None`` to give up."""
        ...

    def close(self) -> None: ...


class UpstreamAuth(Protocol):
    """How the local proxy signs in; ``scheme`` labels it in logs (``basic``, ``windows``)."""

    scheme: str

    def begin(self) -> AuthSession | None:
        """A session to send before any challenge (Basic; a scheme seen earlier), or ``None``."""
        ...

    def begin_from(self, offered: list[str]) -> AuthSession | None:
        """A session for a ``407`` offering *offered*, or ``None`` when none fits."""
        ...


@dataclass
class Exchange:
    """The upstream's final answer and the connection it came on."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    head: bytes
    scheme: str = ""

    @property
    def status(self) -> int:
        return status_of(self.head)


def _header_lines(head: bytes) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, sep, value = line.partition(":")
        if sep:
            pairs.append((name.strip().lower(), value.strip()))
    return pairs


def status_of(head: bytes) -> int:
    """The status code of a response head, or 0 when it has none."""
    first = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
    try:
        return int(first[1])
    except (IndexError, ValueError):
        return 0


def offered_schemes(head: bytes) -> list[str]:
    """The schemes a ``407`` offers, in order, each once (``["Negotiate", "NTLM"]``)."""
    schemes: list[str] = []
    for name, value in _header_lines(head):
        if name == "proxy-authenticate":
            scheme = value.split(" ", 1)[0].strip()
            if scheme and scheme.lower() not in (s.lower() for s in schemes):
                schemes.append(scheme)
    return schemes


def challenge_token(head: bytes, scheme: str) -> str | None:
    """The token a ``407`` carries for *scheme*, or ``None`` when it carries none.

    A challenge with parameters only (``Basic realm="x"``) or a bare scheme name
    is not a token: for an exchange in progress it means the proxy rejected it.
    """
    for name, value in _header_lines(head):
        if name != "proxy-authenticate":
            continue
        got, _, rest = value.partition(" ")
        rest = rest.strip()
        if got.lower() == scheme.lower() and rest and "=" not in rest.rstrip("="):
            return rest
    return None


def _connection_closes(head: bytes) -> bool:
    for name, value in _header_lines(head):
        if name in ("connection", "proxy-connection") and "close" in value.lower():
            return True
    return head.startswith(b"HTTP/1.0") and not any(
        name in ("connection", "proxy-connection") and "keep-alive" in value.lower()
        for name, value in _header_lines(head)
    )


async def drain_body(reader: asyncio.StreamReader, head: bytes) -> bool:
    """Read a response body to its end; ``True`` when the connection can carry another request."""
    if _connection_closes(head):
        return False
    headers = dict(_header_lines(head))
    if "chunked" in headers.get("transfer-encoding", "").lower():
        drained = 0
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            drained += size
            if drained > MAX_DRAINED_BODY:
                return False
            if size == 0:
                await reader.readuntil(b"\r\n")  # the empty trailer line
                return True
            await reader.readexactly(size + 2)
    length = headers.get("content-length")
    if length is None:
        return False  # delimited by close: nothing may follow on this connection
    try:
        count = int(length)
    except ValueError:
        return False
    if count > MAX_DRAINED_BODY:
        return False
    if count:
        await reader.readexactly(count)
    return True


async def exchange(
    connect: Connector, build: Builder, auth: UpstreamAuth, *, head_timeout: float
) -> Exchange:
    """Send ``build(header)`` until the upstream answers something other than an exchange 407.

    Returns the final answer: a non-407, or the 407 that ended the exchange (the
    proxy rejected it, or offered nothing *auth* can speak). Network errors
    propagate (``OSError``, ``asyncio.TimeoutError``, ``asyncio.IncompleteReadError``).
    """
    reader, writer = await connect()
    session = auth.begin()
    header = await session.first() if session else None
    head = b""
    try:
        for _ in range(MAX_ROUNDS):
            writer.write(build(header))
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), head_timeout)
            if status_of(head) != 407:
                return Exchange(reader, writer, head, session.scheme if session else "")
            reusable = await asyncio.wait_for(drain_body(reader, head), head_timeout)
            if session is None:
                session = auth.begin_from(offered_schemes(head))
                if session is None:
                    return Exchange(reader, writer, head)
                header = await session.first()
                first_leg = True
            else:
                token = challenge_token(head, session.scheme)
                if token is None:
                    return Exchange(reader, writer, head, session.scheme)
                header = await session.answer(token)
                first_leg = False
            if header is None:
                return Exchange(reader, writer, head, session.scheme)
            if not reusable:
                if not first_leg:
                    # The rest of an NTLM exchange is bound to the closed connection.
                    return Exchange(reader, writer, head, session.scheme)
                writer.close()
                reader, writer = await connect()
        return Exchange(reader, writer, head, session.scheme if session else "")
    finally:
        if session is not None:
            with contextlib.suppress(Exception):
                session.close()
