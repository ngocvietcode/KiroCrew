"""One test connection through the company proxy, for the dashboard's "Test connection".

Opens ``CONNECT <target>`` through the upstream, signing in the way the proxy
will (Basic, or the Windows account through NTLM/Negotiate), and reports what the
proxy answered. Nothing is sent inside the tunnel: a 200 is the proof that the
proxy accepted the sign-in and reached the destination.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from kiro_crew.auth_proxy.handshake import UpstreamAuth, exchange, offered_schemes
from kiro_crew.auth_proxy.server import HEAD_LIMIT
from kiro_crew.auth_proxy.upstream import Upstream

#: The destination probed by default: Kiro's sign-in endpoint, the first host a
#: signed-out gateway needs.
DEFAULT_PROBE_TARGET = "oidc.us-east-1.amazonaws.com:443"
#: Bound on the whole probe: connect, every sign-in leg, and the proxy's answer.
PROBE_TIMEOUT_SECS = 15.0
#: How long closing the probe connection may wait.
_CLOSE_SECS = 2.0


@dataclass(frozen=True)
class ProbeResult:
    """What the proxy answered. ``outcome`` is one of a fixed set the UI maps to text.

    ``ok`` -- 200, sign-in accepted (``scheme`` names how: ``Basic``, ``NTLM``,
    ``Negotiate``, or empty for none); ``rejected`` -- 407 (``schemes`` lists what
    the proxy offers); ``refused`` -- another status; ``unreachable`` -- no TCP
    connection to the proxy; ``timeout`` -- no answer in time; ``bad_response`` --
    not HTTP.
    """

    outcome: str
    status: int = 0
    schemes: list[str] = field(default_factory=list)
    scheme: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "outcome": self.outcome,
            "status": self.status,
            "schemes": self.schemes,
            "scheme": self.scheme,
        }


async def probe_upstream(
    upstream: Upstream,
    auth: UpstreamAuth,
    *,
    target: str = DEFAULT_PROBE_TARGET,
    timeout: float = PROBE_TIMEOUT_SECS,
) -> ProbeResult:
    """``CONNECT target`` through *upstream* with *auth*; never raises for a network fault."""
    try:
        return await asyncio.wait_for(_probe(upstream, auth, target, timeout), timeout)
    except asyncio.TimeoutError:
        return ProbeResult("timeout")


async def _probe(
    upstream: Upstream, auth: UpstreamAuth, target: str, timeout: float
) -> ProbeResult:
    connected = False

    async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        nonlocal connected
        pair = await asyncio.open_connection(upstream.host, upstream.port, limit=HEAD_LIMIT)
        connected = True
        return pair

    def build(header: str | None) -> bytes:
        lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
        if header:
            lines.append(f"Proxy-Authorization: {header}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    try:
        done = await exchange(connect, build, auth, head_timeout=timeout)
    except OSError:
        return ProbeResult("unreachable" if not connected else "bad_response")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        return ProbeResult("bad_response")
    try:
        status = done.status
        if status == 200:
            return ProbeResult("ok", status, scheme=done.scheme)
        if status == 407:
            return ProbeResult("rejected", status, offered_schemes(done.head), scheme=done.scheme)
        if status == 0:
            return ProbeResult("bad_response")
        return ProbeResult("refused", status)
    finally:
        done.writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(done.writer.wait_closed(), _CLOSE_SECS)
