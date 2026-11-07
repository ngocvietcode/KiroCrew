"""The gateway side of the local proxy: upstream choice, env export, and the real process."""

from __future__ import annotations

import asyncio
import base64
import logging
from types import SimpleNamespace

import pytest

from kiro_crew.auth_proxy import supervisor as sup
from kiro_crew.auth_proxy.supervisor import (
    ORIGIN_ENV,
    Launch,
    LaunchRefused,
    ProxySupervisor,
    export_env,
    loopback_url,
    plan_launch,
    start_for_gateway,
)
from kiro_crew.config.sections_network import NetworkConfig

_WAIT = 20.0


def _net(**kw) -> NetworkConfig:
    return NetworkConfig(proxy_enabled=True, **kw)


def _no_secrets(name: str) -> str | None:
    return None


def test_the_configured_url_wins_over_the_environment():
    launch = plan_launch(
        _net(proxy_url="http://proxy.corp.test:8080"),
        {"HTTPS_PROXY": "http://other.corp.test:3128"},
        _no_secrets,
    )
    assert launch.upstream == "http://proxy.corp.test:8080"
    assert launch.upstream_from_env is False
    assert launch.username == "" and launch.password == ""


def test_the_environment_proxy_is_the_fallback_upstream():
    launch = plan_launch(_net(), {"HTTP_PROXY": "proxynorth.corp.test:8080"}, _no_secrets)
    assert launch.upstream == "http://proxynorth.corp.test:8080"
    assert launch.upstream_from_env is True


def test_a_respawned_gateway_recovers_the_upstream_behind_its_own_loopback_url():
    env = {
        "HTTPS_PROXY": loopback_url("old-token", 50123),
        ORIGIN_ENV: "http://proxy.corp.test:8080",
    }
    assert plan_launch(_net(), env, _no_secrets).upstream == "http://proxy.corp.test:8080"


def test_the_password_comes_from_the_vault_by_the_reference_name():
    asked: list[str] = []

    def read(name: str) -> str | None:
        asked.append(name)
        return "p@ss"

    launch = plan_launch(
        _net(
            proxy_url="proxy.corp.test:8080",
            proxy_username="alice",
            proxy_password="secret://CORP_PROXY",
        ),
        {},
        read,
    )
    assert asked == ["CORP_PROXY"]
    assert (launch.username, launch.password) == ("alice", "p@ss")
    assert "p@ss" not in repr(launch) and launch.token not in repr(launch)


def test_a_missing_vault_secret_refuses_the_launch():
    with pytest.raises(LaunchRefused, match="the vault holds no secret"):
        plan_launch(
            _net(
                proxy_url="proxy.corp.test:8080",
                proxy_username="alice",
                proxy_password="secret://GONE",
            ),
            {},
            _no_secrets,
        )


def test_credentials_in_the_environment_url_are_used_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="kiro_crew.auth_proxy"):
        launch = plan_launch(
            _net(), {"HTTPS_PROXY": "http://bob:s3cret@proxy.corp.test:8080"}, _no_secrets
        )
    assert (launch.username, launch.password) == ("bob", "s3cret")
    assert launch.upstream == "http://proxy.corp.test:8080"
    assert "carries a password" in caplog.text and "s3cret" not in caplog.text


@pytest.mark.parametrize(
    "network, env, reason",
    [
        (_net(), {}, "no company proxy is set"),
        (_net(proxy_url="https://proxy.corp.test:8443"), {}, "not supported"),
        (_net(proxy_url="http://127.0.0.1:3128"), {}, "loopback address"),
        (_net(), {"HTTPS_PROXY": loopback_url("t", 5)}, "no company proxy is set"),
    ],
)
def test_unusable_settings_refuse_the_launch(network, env, reason):
    with pytest.raises(LaunchRefused, match=reason):
        plan_launch(network, env, _no_secrets)


def test_no_proxy_merges_the_environment_and_the_setting_and_keeps_loopback():
    launch = plan_launch(
        _net(proxy_url="proxy.corp.test:8080", proxy_no_proxy=".vpbank.dev"),
        {"NO_PROXY": ".corp.test"},
        _no_secrets,
    )
    assert launch.no_proxy == ".corp.test,.vpbank.dev,localhost,127.0.0.1,::1"


def test_export_points_every_proxy_variable_at_the_loopback_url():
    launch = Launch(
        upstream="http://proxy.corp.test:8080", username="alice", no_proxy="localhost", token="tok"
    )
    env: dict[str, str] = {"NO_PROXY": "old"}
    export_env(env, launch, 4242)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[key] == "http://crew:tok@127.0.0.1:4242"
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost"
    assert env[ORIGIN_ENV] == "http://proxy.corp.test:8080"


def test_start_for_gateway_does_nothing_when_off():
    env = {"HTTPS_PROXY": "http://proxy.corp.test:8080"}
    assert start_for_gateway(SimpleNamespace(network=NetworkConfig()), env) is False
    assert env == {"HTTPS_PROXY": "http://proxy.corp.test:8080"}


def test_a_refused_start_leaves_the_environment_alone(monkeypatch, caplog):
    monkeypatch.setattr(sup, "_SUPERVISOR", None)
    env = {"NO_PROXY": ".corp.test"}
    with caplog.at_level(logging.WARNING, logger="kiro_crew.auth_proxy"):
        started = start_for_gateway(SimpleNamespace(network=_net()), env)
    assert started is False
    assert env == {"NO_PROXY": ".corp.test"}
    assert "auth-proxy: not started: no company proxy is set" in caplog.text


# ── the real proxy process ──


class _CompanyProxy:
    """Accepts one CONNECT carrying the expected Basic, then tunnels to the requested port."""

    def __init__(self, expected: str):
        self.expected = expected
        self.auth_seen: list[str | None] = []

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
            self.auth_seen.append(auth)
            if auth != self.expected:
                writer.write(b"HTTP/1.1 407 Nope\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
            await writer.drain()
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def close(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), _WAIT)


async def _tunnel_echo(port: int, token: str, payload: bytes) -> tuple[str, bytes]:
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), _WAIT)
    auth = base64.b64encode(f"crew:{token}".encode()).decode()
    writer.write(
        f"CONNECT site.example:443 HTTP/1.1\r\nProxy-Authorization: Basic {auth}\r\n\r\n".encode()
    )
    await writer.drain()
    head = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _WAIT)).decode("latin-1")
    body = b""
    if head.startswith("HTTP/1.1 200"):
        writer.write(payload)
        await writer.drain()
        body = await asyncio.wait_for(reader.readexactly(len(payload)), _WAIT)
    writer.close()
    return head, body


@pytest.mark.asyncio
async def test_the_proxy_process_serves_restarts_on_its_port_and_stops(monkeypatch):
    # Only how soon the restart starts; the test waits on wait_for_generation, not on time.
    monkeypatch.setattr(
        sup, "RESTART_BACKOFF_SECS", (0.05,)
    )  # flake-ok: speeds a signalled restart
    company = _CompanyProxy("Basic " + base64.b64encode(b"alice:p@ss").decode())
    company_port = await company.start()
    launch = Launch(
        upstream=f"http://127.0.0.1:{company_port}",
        username="alice",
        password="p@ss",
        no_proxy="localhost",
        token="tok-1",
    )
    supervisor = ProxySupervisor(launch)
    try:
        port = await asyncio.to_thread(supervisor.start)
        head, body = await _tunnel_echo(port, "tok-1", b"through the real process")
        assert head.startswith("HTTP/1.1 200") and body == b"through the real process"

        first = supervisor._proc
        assert first is not None
        first.kill()

        # The watcher brings a new process up on the SAME port and token; generation 2
        # is signalled only once that process reports it is listening.
        assert await asyncio.to_thread(supervisor.wait_for_generation, 2, _WAIT)
        head, body = await _tunnel_echo(port, "tok-1", b"again")
        assert head.startswith("HTTP/1.1 200") and body == b"again"
        assert company.auth_seen and all(a == company.expected for a in company.auth_seen)
    finally:
        await asyncio.to_thread(supervisor.stop)
        await company.close()
    proc = supervisor._proc
    assert proc is not None and proc.poll() is not None


@pytest.mark.asyncio
async def test_a_proxy_that_cannot_listen_refuses_with_its_reason():
    launch = Launch(upstream="http://proxy.corp.test:8080", username="", no_proxy="", token="")
    supervisor = ProxySupervisor(launch)
    with pytest.raises(LaunchRefused, match="no client token was given"):
        await asyncio.to_thread(supervisor.start)


# ── network.proxy_auth ──


def test_auto_without_a_user_signs_in_as_the_windows_account(monkeypatch):
    monkeypatch.setattr(sup.sspi, "available", lambda: True)
    launch = plan_launch(_net(proxy_url="proxy.corp.test:8080"), {}, _no_secrets)
    assert launch.auth == "windows" and launch.username == "" and launch.password == ""
    assert b'"auth": "windows"' in launch.line(0)


def test_auto_with_a_user_keeps_basic(monkeypatch):
    monkeypatch.setattr(sup.sspi, "available", lambda: True)
    launch = plan_launch(
        _net(proxy_url="proxy.corp.test:8080", proxy_username="alice"), {}, _no_secrets
    )
    assert launch.auth == "basic" and launch.username == "alice"


def test_auto_off_windows_without_a_user_sends_no_credentials(monkeypatch):
    monkeypatch.setattr(sup.sspi, "available", lambda: False)
    assert plan_launch(_net(proxy_url="proxy.corp.test:8080"), {}, _no_secrets).auth == "none"


def test_windows_mode_ignores_a_configured_password(monkeypatch):
    monkeypatch.setattr(sup.sspi, "available", lambda: True)
    asked: list[str] = []

    def read(name: str) -> str | None:
        asked.append(name)
        return "unused"

    launch = plan_launch(
        _net(
            proxy_url="proxy.corp.test:8080",
            proxy_username="alice",
            proxy_password="secret://CORP_PROXY",
            proxy_auth="windows",
        ),
        {},
        read,
    )
    assert launch.auth == "windows" and launch.password == "" and asked == []


def test_windows_mode_off_windows_refuses(monkeypatch):
    monkeypatch.setattr(sup.sspi, "available", lambda: False)
    with pytest.raises(LaunchRefused, match="needs Windows"):
        plan_launch(_net(proxy_url="proxy.corp.test:8080", proxy_auth="windows"), {}, _no_secrets)
