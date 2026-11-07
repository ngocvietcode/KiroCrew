"""Entry point of the local proxy process: ``python -m kiro_crew.auth_proxy``.

The gateway writes one JSON line on stdin -- the client token, the upstream, its
credentials -- so none of them appears in this process's argv or environment.
The process answers one JSON line on stdout, ``{"port": N}`` once it listens or
``{"error": "..."}`` when it cannot, then serves until stdin closes: the gateway
holds the pipe open for as long as it runs, so its exit ends this process too.
Diagnostics go to stderr, which the gateway copies into its own log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import threading
from typing import Any

from kiro_crew.auth_proxy.auth import build_auth
from kiro_crew.auth_proxy.handshake import UpstreamAuth
from kiro_crew.auth_proxy.server import ProxyServer, ProxySettings
from kiro_crew.auth_proxy.upstream import Upstream, UpstreamError, parse_upstream


def settings_from(launch: dict[str, Any]) -> ProxySettings:
    """The server settings a launch line describes; ``UpstreamError`` when unusable."""
    token = str(launch.get("token") or "")
    if not token:
        raise UpstreamError("no client token was given")
    upstream: Upstream | None = None
    if launch.get("upstream"):
        upstream = parse_upstream(str(launch["upstream"]))
    username = str(launch.get("username") or "")
    mode = str(launch.get("auth") or ("basic" if username else "none"))
    auth: UpstreamAuth = build_auth(
        mode,
        proxy_host=upstream.host if upstream else "",
        username=username,
        password=str(launch.get("password") or ""),
    )
    return ProxySettings(
        token=token,
        upstream=upstream,
        auth=auth,
        no_proxy=str(launch.get("no_proxy") or ""),
    )


def _answer(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


async def _serve(settings: ProxySettings, port: int) -> int:
    server = ProxyServer(settings)
    try:
        bound = await server.start("127.0.0.1", port)
    except OSError as exc:
        _answer({"error": f"cannot listen on 127.0.0.1:{port}: {exc.strerror or exc}"})
        return 2
    _answer({"port": bound})
    logging.getLogger("kiro_crew.auth_proxy").info(
        "auth-proxy: listening on 127.0.0.1:%d, upstream %s, auth %s",
        bound,
        settings.upstream.authority if settings.upstream else "none (direct)",
        settings.auth.scheme,
    )
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _wait_for_parent() -> None:
        # Blocks until the gateway closes its end of the pipe (or exits).
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read()
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=_wait_for_parent, name="auth-proxy-parent", daemon=True).start()
    await stop.wait()
    await server.close()
    return 0


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        launch = json.loads(sys.stdin.buffer.readline().decode("utf-8") or "{}")
        settings = settings_from(launch)
        port = int(launch.get("port") or 0)
    except (ValueError, UpstreamError) as exc:
        _answer({"error": str(exc)})
        return 2
    return asyncio.run(_serve(settings, port))


if __name__ == "__main__":
    sys.exit(main())
