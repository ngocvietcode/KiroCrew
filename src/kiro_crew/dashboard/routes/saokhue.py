"""Routes the Sao Khuê build adds: the company proxy settings and the Sao Khuê CLI sign-in.

Each handler module (``handlers/network_proxy.py``, ``handlers/saokhue_login.py``)
is imported on the first request, so registration adds no gateway-boot work.
"""

from __future__ import annotations

from aiohttp import web


def _lazy(module_name: str, handler_name: str):
    async def _dispatch(request: web.Request) -> web.StreamResponse:
        import importlib

        module = importlib.import_module(f"kiro_crew.dashboard.handlers.{module_name}")
        return await getattr(module, handler_name)(request)

    return _dispatch


def register(app: web.Application) -> None:
    proxy = "network_proxy"
    app.router.add_get("/api/network/proxy", _lazy(proxy, "api_network_proxy_status"))
    app.router.add_post("/api/network/proxy", _lazy(proxy, "api_network_proxy_save"))
    app.router.add_post("/api/network/proxy/test", _lazy(proxy, "api_network_proxy_test"))

    login = "saokhue_login"
    app.router.add_get("/api/saokhue-login", _lazy(login, "api_saokhue_login_status"))
    app.router.add_post("/api/saokhue-login/sso", _lazy(login, "api_saokhue_login_sso"))
    app.router.add_post(
        "/api/saokhue-login/device/start", _lazy(login, "api_saokhue_login_device_start")
    )
    app.router.add_post(
        "/api/saokhue-login/device/poll", _lazy(login, "api_saokhue_login_device_poll")
    )
    app.router.add_post("/api/saokhue-login/password", _lazy(login, "api_saokhue_login_password"))
    app.router.add_post("/api/saokhue-login/token", _lazy(login, "api_saokhue_login_token"))
    app.router.add_post("/api/saokhue-login/model", _lazy(login, "api_saokhue_login_model"))
    app.router.add_post("/api/saokhue-login/logout", _lazy(login, "api_saokhue_login_logout"))
