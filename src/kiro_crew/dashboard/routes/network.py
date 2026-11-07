"""Routes for the company proxy settings (``handlers/network_proxy.py``).

The handler module is imported on the first request, so registration adds no
gateway-boot work.
"""

from __future__ import annotations

from aiohttp import web


def _lazy(handler_name: str):
    async def _dispatch(request: web.Request) -> web.StreamResponse:
        from kiro_crew.dashboard.handlers import network_proxy

        return await getattr(network_proxy, handler_name)(request)

    return _dispatch


def register(app: web.Application) -> None:
    app.router.add_get("/api/network/proxy", _lazy("api_network_proxy_status"))
    app.router.add_post("/api/network/proxy", _lazy("api_network_proxy_save"))
    app.router.add_post("/api/network/proxy/test", _lazy("api_network_proxy_test"))
