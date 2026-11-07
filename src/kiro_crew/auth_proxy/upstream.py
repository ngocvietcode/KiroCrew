"""Where the local proxy sends a request: the corporate proxy, or straight to the host.

Pure functions only, so every rule here is tested without a socket.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
from dataclasses import dataclass

#: The port an upstream URL without one is assumed to listen on.
_DEFAULT_PROXY_PORT = 8080

#: Hosts every child is told to reach directly. The local proxy itself is one of
#: them: a client that sent its own loopback traffic to the proxy would loop.
LOOPBACK_NO_PROXY = ("localhost", "127.0.0.1", "::1")


@dataclass(frozen=True)
class Upstream:
    """The corporate proxy, as ``host:port`` plus the credentials found in its URL.

    ``username``/``password`` are set only when the URL carried them (an
    ``HTTPS_PROXY=http://user:pass@host:port`` the operator exported). ``repr``
    never shows the password.
    """

    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def __repr__(self) -> str:
        user = f"{self.username}:****@" if self.username else ""
        return f"Upstream({user}{self.authority})"


class UpstreamError(ValueError):
    """The upstream proxy setting cannot be used; the message names why."""


def parse_upstream(value: str) -> Upstream:
    """Parse ``http://[user:pass@]host[:port]`` or a bare ``host[:port]``.

    Only a plain-HTTP proxy is accepted: the local proxy speaks HTTP to the
    upstream and lets TLS run end to end inside ``CONNECT``. Credentials in the
    URL are percent-decoded, the way every HTTP client reads them.
    """
    raw = (value or "").strip()
    if not raw:
        raise UpstreamError("no upstream proxy is set")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme.lower() != "http":
        raise UpstreamError(
            f"upstream proxy scheme {parts.scheme!r} is not supported; use http://host:port"
        )
    if not parts.hostname:
        raise UpstreamError("upstream proxy URL has no host")
    try:
        port = parts.port or _DEFAULT_PROXY_PORT
    except ValueError as exc:
        raise UpstreamError("upstream proxy URL has an invalid port") from exc
    return Upstream(
        host=parts.hostname,
        port=port,
        username=urllib.parse.unquote(parts.username or ""),
        password=urllib.parse.unquote(parts.password or ""),
    )


def merge_no_proxy(*lists: str) -> str:
    """One comma-separated ``NO_PROXY`` value: every entry once, loopback always in."""
    seen: list[str] = []
    for value in (*lists, ",".join(LOOPBACK_NO_PROXY)):
        for entry in (value or "").replace(";", ",").split(","):
            entry = entry.strip()
            if entry and entry.lower() not in (e.lower() for e in seen):
                seen.append(entry)
    return ",".join(seen)


def bypasses_upstream(host: str, no_proxy: str) -> bool:
    """Whether *host* is reached directly rather than through the upstream proxy.

    The rule the standard library's ``no_proxy`` matcher applies, so a host the
    gateway's Python clients reach directly is reached directly here too: ``*``
    matches every host, and ``example.com`` or ``.example.com`` matches the domain
    and every name under it. An entry's ``:port`` suffix is ignored.
    """
    name = host.strip("[]").lower()
    for raw in (no_proxy or "").replace(";", ",").split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        if not entry.startswith("[") and entry.count(":") == 1:
            entry = entry.split(":", 1)[0]
        entry = entry.strip("[]").lstrip(".")
        if entry and (name == entry or name.endswith("." + entry)):
            return True
    return False


def is_forbidden_target(host: str) -> bool:
    """A destination the proxy refuses: loopback, link-local or unspecified.

    The proxy acts with the operator's proxy account, so it never becomes a way
    to reach this machine's own listeners or the link-local metadata range.
    """
    name = host.strip("[]").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(name)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_link_local or addr.is_unspecified


def split_authority(authority: str, default_port: int) -> tuple[str, int]:
    """``host:port`` (or ``[v6]:port``) to its parts; raises ``ValueError`` when malformed."""
    parts = urllib.parse.urlsplit("//" + authority)
    if not parts.hostname:
        raise ValueError(f"no host in {authority!r}")
    port = parts.port or default_port
    return parts.hostname, port
