"""Dashboard API for the company proxy: status, save, test.

``conftest`` pins the data home per test, so ``config.json`` and the vault written
here are the test's own.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from kiro_crew.auth_proxy import sspi
from kiro_crew.auth_proxy import supervisor as proxy_supervisor
from kiro_crew.auth_proxy.auth import BasicAuth, NoAuth
from kiro_crew.auth_proxy.probe import probe_upstream
from kiro_crew.auth_proxy.upstream import Upstream
from kiro_crew.config.loader_locations import config_path
from kiro_crew.config.paths import config_dir
from kiro_crew.dashboard.handlers import network_proxy as api
from kiro_crew.secrets import SecretVault

pytestmark = pytest.mark.asyncio

_WAIT = 10.0


class _State:
    owner_id = ""


class _Request:
    def __init__(self, body=None, *, owner: bool = True, app: dict | None = None):
        self.app = app if app is not None else {}
        self.app.setdefault("state", _State())
        self._body = body
        self.path = "/api/network/proxy"
        self._items = {"app": "", "user": "local-app" if owner else "someone-else"}

    def __getitem__(self, key):
        return self._items[key]

    def __contains__(self, key):
        return key in self._items

    def get(self, key, default=""):
        return self._items.get(key, default)

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _body(resp) -> dict:
    return json.loads(resp.text)


def _saved() -> dict:
    return json.loads(config_path().read_text(encoding="utf-8")).get("network", {})


class _CompanyProxy:
    """Answers CONNECT: 200 for the expected Basic, else 407 offering *offers*."""

    def __init__(self, expected: str | None, offers: tuple[str, ...] = ('Basic realm="corp"',)):
        self.expected = expected
        self.offers = offers
        self.seen: list[str | None] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
            auth = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in head
                    if line.lower().startswith("proxy-authorization:")
                ),
                None,
            )
            self.seen.append(auth)
            if auth == self.expected:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                challenge = "".join(f"Proxy-Authenticate: {o}\r\n" for o in self.offers)
                writer.write(
                    f"HTTP/1.1 407 Proxy Authentication Required\r\n{challenge}\r\n".encode()
                )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.fixture(autouse=True)
def _no_running_proxy(monkeypatch):
    monkeypatch.setattr(proxy_supervisor, "_SUPERVISOR", None)
    monkeypatch.setattr(proxy_supervisor, "_RUNNING", None)
    monkeypatch.setattr(proxy_supervisor, "_LAST_REFUSAL", "")


@pytest.mark.parametrize(
    "handler, body",
    [
        (api.api_network_proxy_status, None),
        (api.api_network_proxy_save, {"enabled": True}),
        (api.api_network_proxy_test, {"url": "proxy.corp.test:8080"}),
    ],
)
async def test_every_endpoint_is_owner_only(handler, body):
    resp = await handler(_Request(body, owner=False))
    assert resp.status == 403
    assert _body(resp)["code"] == "owner_only"


async def test_status_of_a_fresh_install():
    resp = await api.api_network_proxy_status(_Request())
    assert resp.status == 200
    assert _body(resp) == {
        "enabled": False,
        "url": "",
        "username": "",
        "no_proxy": "",
        "has_password": False,
        "restart_required": False,
        "running": None,
        "not_started": "",
        "auth": "auto",
        "windows_available": sspi.available(),
    }


async def test_save_puts_the_password_in_the_vault_and_only_a_reference_in_config():
    app: dict = {}
    resp = await api.api_network_proxy_save(
        _Request(
            {
                "enabled": True,
                "url": " http://proxy.corp.test:8080 ",
                "username": "alice",
                "password": "p@ss w0rd",
                "no_proxy": ".corp.test",
            },
            app=app,
        )
    )
    assert resp.status == 200
    status = _body(resp)
    assert status["has_password"] is True and status["restart_required"] is True
    assert "p@ss w0rd" not in resp.text
    saved = _saved()
    assert saved == {
        "proxy_enabled": True,
        "proxy_url": "http://proxy.corp.test:8080",
        "proxy_username": "alice",
        "proxy_password": "secret://NETWORK_PROXY_PASSWORD",
        "proxy_no_proxy": ".corp.test",
        "proxy_auth": "auto",
    }
    assert "p@ss w0rd" not in config_path().read_text(encoding="utf-8")
    secret = SecretVault(config_dir()).get(api.VAULT_SECRET_NAME)
    assert secret is not None and secret.reveal() == "p@ss w0rd"


async def test_saving_without_a_password_keeps_the_stored_one_and_clear_removes_it():
    await api.api_network_proxy_save(
        _Request(
            {"enabled": True, "url": "proxy.corp.test:8080", "username": "alice", "password": "one"}
        )
    )
    resp = await api.api_network_proxy_save(
        _Request({"enabled": True, "url": "proxy.corp.test:8080", "username": "bob"})
    )
    assert _body(resp)["has_password"] is True
    assert _saved()["proxy_password"] == "secret://NETWORK_PROXY_PASSWORD"

    resp = await api.api_network_proxy_save(
        _Request({"enabled": False, "url": "", "username": "", "clear_password": True})
    )
    assert _body(resp)["has_password"] is False
    assert _saved()["proxy_password"] == ""
    assert SecretVault(config_dir()).get(api.VAULT_SECRET_NAME) is None


@pytest.mark.parametrize(
    "body, code",
    [
        ("not an object", "invalid_body"),
        ({"url": "https://proxy.corp.test:8443"}, "invalid_proxy_url"),
        ({"url": "proxy.corp.test:8080", "password": 123}, "invalid_password"),
        ({"url": "x" * 3000}, "field_too_long"),
    ],
)
async def test_save_refuses_bad_input_and_writes_nothing(body, code):
    resp = await api.api_network_proxy_save(_Request(body))
    assert resp.status == 400
    assert _body(resp)["code"] == code
    assert not config_path().exists() or "network" not in json.loads(
        config_path().read_text(encoding="utf-8")
    )


async def test_status_reports_the_running_proxy_or_why_it_did_not_start(monkeypatch):
    monkeypatch.setattr(proxy_supervisor, "_LAST_REFUSAL", "no company proxy is set")
    status = _body(await api.api_network_proxy_status(_Request()))
    assert status["running"] is None and status["not_started"] == "no company proxy is set"

    class _Alive:
        alive = True

    monkeypatch.setattr(proxy_supervisor, "_SUPERVISOR", _Alive())
    monkeypatch.setattr(
        proxy_supervisor,
        "_RUNNING",
        {
            "port": 4242,
            "upstream": "http://proxy.corp.test:8080",
            "username": "alice",
            "no_proxy": "localhost",
        },
    )
    monkeypatch.setattr(proxy_supervisor, "_LAST_REFUSAL", "")
    status = _body(await api.api_network_proxy_status(_Request()))
    assert status["running"]["port"] == 4242 and status["not_started"] == ""


async def test_test_connection_uses_the_typed_password():
    company = _CompanyProxy(_basic("alice", "typed"))
    port = await company.start()
    try:
        resp = await api.api_network_proxy_test(
            _Request({"url": f"http://127.0.0.1:{port}", "username": "alice", "password": "typed"})
        )
    finally:
        await company.close()
    assert _body(resp) == {
        "ok": True,
        "outcome": "ok",
        "status": 200,
        "schemes": [],
        "scheme": "Basic",
    }


async def test_test_connection_falls_back_to_the_stored_password():
    await api.api_network_proxy_save(
        _Request(
            {
                "enabled": True,
                "url": "proxy.corp.test:8080",
                "username": "alice",
                "password": "stored",
            }
        )
    )
    company = _CompanyProxy(_basic("alice", "stored"))
    port = await company.start()
    try:
        resp = await api.api_network_proxy_test(
            _Request({"url": f"127.0.0.1:{port}", "username": "alice"})
        )
    finally:
        await company.close()
    assert _body(resp)["ok"] is True


async def test_test_connection_reports_what_the_proxy_offers_when_it_rejects():
    company = _CompanyProxy("never", offers=("NTLM", "Negotiate"))
    port = await company.start()
    try:
        resp = await api.api_network_proxy_test(
            _Request({"url": f"127.0.0.1:{port}", "username": "alice", "password": "wrong"})
        )
    finally:
        await company.close()
    assert _body(resp) == {
        "ok": False,
        "outcome": "rejected",
        "status": 407,
        "schemes": ["NTLM", "Negotiate"],
        "scheme": "Basic",
    }


async def test_test_connection_needs_a_url():
    resp = await api.api_network_proxy_test(_Request({"url": ""}))
    assert resp.status == 400 and _body(resp)["code"] == "missing_proxy_url"


async def test_probe_reports_an_unreachable_proxy():
    company = _CompanyProxy(None)
    port = await company.start()
    await company.close()
    result = await probe_upstream(Upstream("127.0.0.1", port), NoAuth())
    assert result.outcome == "unreachable" and not result.ok


async def test_probe_sends_basic_and_nothing_without_credentials():
    company = _CompanyProxy(None)
    port = await company.start()
    try:
        await probe_upstream(Upstream("127.0.0.1", port), NoAuth())
        await probe_upstream(Upstream("127.0.0.1", port), BasicAuth("alice", "x"))
    finally:
        await company.close()
    assert company.seen == [None, _basic("alice", "x")]


async def test_save_stores_the_sign_in_mode_and_refuses_an_unknown_one():
    resp = await api.api_network_proxy_save(
        _Request({"enabled": True, "url": "proxy.corp.test:8080", "auth": "windows"})
    )
    assert resp.status == 200 and _body(resp)["auth"] == "windows"
    assert _saved()["proxy_auth"] == "windows"
    resp = await api.api_network_proxy_save(
        _Request({"url": "proxy.corp.test:8080", "auth": "kerberos"})
    )
    assert resp.status == 400 and _body(resp)["code"] == "invalid_auth"


async def test_test_connection_refuses_windows_sign_in_off_windows(monkeypatch):
    monkeypatch.setattr(api.sspi, "available", lambda: False)
    resp = await api.api_network_proxy_test(
        _Request({"url": "proxy.corp.test:8080", "auth": "windows"})
    )
    assert resp.status == 400 and _body(resp)["code"] == "windows_unavailable"
