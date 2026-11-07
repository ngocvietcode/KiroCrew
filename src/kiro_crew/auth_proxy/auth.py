"""How the local proxy signs in to the upstream proxy: none, Basic, or the Windows account.

Each scheme is an :class:`~kiro_crew.auth_proxy.handshake.UpstreamAuth`: it opens
one :class:`~kiro_crew.auth_proxy.handshake.AuthSession` per upstream connection,
which :func:`~kiro_crew.auth_proxy.handshake.exchange` drives through the
proxy's ``407`` challenges.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections.abc import Callable
from typing import Any, Protocol

from kiro_crew.auth_proxy.handshake import AuthSession

logger = logging.getLogger("kiro_crew.auth_proxy")

#: Values of ``network.proxy_auth``.
AUTH_MODES = ("auto", "basic", "windows")


class NoAuth:
    """An upstream that asks for no credentials."""

    scheme = "none"

    def begin(self) -> AuthSession | None:
        return None

    def begin_from(self, offered: list[str]) -> AuthSession | None:
        return None

    def __repr__(self) -> str:
        return "NoAuth()"


class _BasicSession:
    scheme = "Basic"

    def __init__(self, value: str) -> None:
        self._value = value

    async def first(self) -> str | None:
        return self._value

    async def answer(self, token: str) -> str | None:
        return None  # Basic has one leg: a second 407 is a rejection

    def close(self) -> None:
        return None


class BasicAuth:
    """RFC 7617 Basic, sent preemptively on every upstream request.

    The password lives only in this object; ``repr`` and ``str`` never show it.
    """

    scheme = "basic"

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self._value = "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode(
            "ascii"
        )

    def header(self) -> str | None:
        return self._value

    def begin(self) -> AuthSession | None:
        return _BasicSession(self._value)

    def begin_from(self, offered: list[str]) -> AuthSession | None:
        return _BasicSession(self._value) if any(s.lower() == "basic" for s in offered) else None

    def __repr__(self) -> str:
        return f"BasicAuth(username={self.username!r}, password=****)"

    __str__ = __repr__


class SecurityContext(Protocol):
    """One client-side token exchange (``sspi.SspiContext`` or a test double)."""

    done: bool

    def step(self, server_token: bytes | None) -> bytes: ...

    def close(self) -> None: ...


#: Builds the context for one connection from the HTTP scheme and the target SPN.
ContextFactory = Callable[[str, str], SecurityContext]


class _SspiSession:
    def __init__(self, scheme: str, context: SecurityContext) -> None:
        self.scheme = scheme
        self._ctx = context

    async def _step(self, server_token: bytes | None) -> str | None:
        try:
            token = await asyncio.to_thread(self._ctx.step, server_token)
        except OSError as exc:
            logger.warning("auth-proxy: Windows sign-in (%s) failed: %s", self.scheme, exc)
            return None
        return f"{self.scheme} {base64.b64encode(token).decode('ascii')}" if token else None

    async def first(self) -> str | None:
        return await self._step(None)

    async def answer(self, token: str) -> str | None:
        if self._ctx.done:
            return None
        try:
            raw = base64.b64decode(token, validate=True)
        except (binascii.Error, ValueError):
            return None
        return await self._step(raw)

    def close(self) -> None:
        self._ctx.close()


def _sspi_factory() -> ContextFactory:
    from kiro_crew.auth_proxy import sspi

    credentials: dict[str, Any] = {}

    def make(scheme: str, target: str) -> SecurityContext:
        package = sspi.PACKAGE_FOR_SCHEME[scheme.lower()]
        if package not in credentials:
            credentials[package] = sspi.SspiCredentials(package)
        return sspi.SspiContext(credentials[package], target)

    return make


class WindowsAuth:
    """NTLM or Negotiate (Kerberos, else NTLM) as the Windows account this process runs as.

    Waits for the proxy to name its schemes, prefers Negotiate over NTLM, and
    remembers the choice so later connections send their first token at once.
    """

    scheme = "windows"
    _PREFERENCE = ("Negotiate", "NTLM")

    def __init__(self, proxy_host: str, *, context_factory: ContextFactory | None = None) -> None:
        self._target = f"HTTP/{proxy_host}"
        self._factory = context_factory
        self._preferred: str | None = None

    def _make(self, scheme: str) -> AuthSession | None:
        if self._factory is None:
            self._factory = _sspi_factory()
        try:
            context = self._factory(scheme, self._target)
        except OSError as exc:
            logger.warning("auth-proxy: Windows sign-in (%s) is unavailable: %s", scheme, exc)
            return None
        return _SspiSession(scheme, context)

    def begin(self) -> AuthSession | None:
        return self._make(self._preferred) if self._preferred else None

    def begin_from(self, offered: list[str]) -> AuthSession | None:
        lowered = {s.lower() for s in offered}
        for scheme in self._PREFERENCE:
            if scheme.lower() in lowered:
                self._preferred = scheme
                return self._make(scheme)
        return None

    def __repr__(self) -> str:
        return f"WindowsAuth(target={self._target!r})"


def resolve_auth_mode(mode: str, username: str, *, windows_available: bool) -> str:
    """The sign-in a ``network.proxy_auth`` value means here: ``basic``, ``windows`` or ``none``.

    ``auto`` keeps a configured user name on Basic, so a setup that already
    signs in with a password goes on doing so, and otherwise signs in as the
    Windows account where Windows is available.
    """
    if mode == "basic":
        return "basic"
    if mode == "windows":
        return "windows"
    if username:
        return "basic"
    return "windows" if windows_available else "none"


def build_auth(
    mode: str, *, proxy_host: str, username: str, password: str
) -> NoAuth | BasicAuth | WindowsAuth:
    """The :class:`UpstreamAuth` for a resolved mode (see :func:`resolve_auth_mode`)."""
    if mode == "windows":
        return WindowsAuth(proxy_host)
    if mode == "basic" and username:
        return BasicAuth(username, password)
    return NoAuth()
