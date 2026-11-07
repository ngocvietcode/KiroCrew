"""Kiro sign-in and token refresh reach the network through the configured proxy.

aiohttp reads no proxy setting on its own (``trust_env`` is off), so every request
in ``kiro_crew.auth`` names its proxy explicitly with ``http_proxy.resolve_proxy``.
Without that, a host that reaches the internet only through a proxy can never sign
in, and every KAS turn fails once the access token needs a refresh.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

import kiro_crew.auth as auth_pkg
from kiro_crew.auth.login import builder_id
from kiro_crew.auth.refresh import ensure_fresh
from kiro_crew.auth.store import KasToken, TokenStore

_PROXY = "http://proxy.corp.test:3128"
_PROXY_VARS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
)
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request"})
#: Long past, so every token below is due for refresh without reading the clock.
_EXPIRED = datetime(2000, 1, 1, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, *, content_type: str | None = "application/json"):
        return self._payload

    async def text(self):
        return str(self._payload)


class _RecordingSession:
    """Answers each post with the next response and records the proxy it was given."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.proxies: list[str | None] = []

    def post(self, url, *, json=None, data=None, headers=None, proxy=None):  # noqa: A002
        self.proxies.append(proxy)
        return self._responses.pop(0)


@pytest.fixture
def proxy_env(monkeypatch):
    """A proxy set in the environment, with nothing bypassed."""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", _PROXY)
    monkeypatch.setenv("HTTP_PROXY", _PROXY)
    return monkeypatch


def _social() -> KasToken:
    return KasToken(
        access_token="old",
        expires_at=_EXPIRED,
        provider="Google",
        identity="social",
        refresh_token="rt",
        profile_arn="arn:x",
    )


def _builder_id() -> KasToken:
    return KasToken(
        access_token="old",
        expires_at=_EXPIRED,
        provider="BuilderId",
        identity="builder_id",
        refresh_token="rt",
        region="us-east-1",
        client_id="cid",
        client_secret="csec",
    )


def _external_idp() -> KasToken:
    return KasToken(
        access_token="old",
        expires_at=_EXPIRED,
        provider="ExternalIdp",
        identity="external_idp",
        refresh_token="rt",
        auth_method="external_idp",
        client_id="cid",
        token_endpoint="https://idp.corp.test/token",
    )


_REFRESH_CASES = [
    pytest.param(
        _social,
        {"accessToken": "new", "refreshToken": "rt2", "expiresIn": 3600, "profileArn": "arn:x"},
        id="social",
    ),
    pytest.param(_builder_id, {"accessToken": "new", "expiresIn": 3600}, id="builder_id"),
    pytest.param(
        _external_idp,
        {"access_token": "new", "refresh_token": "rt2", "expires_in": 1800},
        id="external_idp",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make_token, answer", _REFRESH_CASES)
async def test_refresh_goes_through_the_environment_proxy(
    proxy_env, tmp_path: Path, make_token, answer
):
    store = TokenStore(tmp_path)
    stale = make_token()
    store.save(stale)
    session = _RecordingSession([_Resp(200, answer)])

    out = await ensure_fresh(store, stale, session=session)

    assert out.access_token == "new"
    assert session.proxies == [_PROXY]


@pytest.mark.asyncio
async def test_a_host_in_no_proxy_is_reached_directly(proxy_env, tmp_path: Path):
    proxy_env.setenv("NO_PROXY", ".corp.test")
    store = TokenStore(tmp_path)
    stale = _external_idp()
    store.save(stale)
    session = _RecordingSession([_Resp(200, {"access_token": "new", "expires_in": 1800})])

    await ensure_fresh(store, stale, session=session)

    assert session.proxies == [None]


@pytest.mark.asyncio
async def test_sign_in_goes_through_the_environment_proxy(proxy_env):
    session = _RecordingSession(
        [_Resp(200, {"clientId": "cid", "clientSecret": "csec", "clientSecretExpiresAt": 0})]
    )

    await builder_id.register_client("us-east-1", session=session)

    assert session.proxies == [_PROXY]


def _session_calls(tree: ast.AST):
    """Every ``<something>session.<http method>(...)`` call in *tree*."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _HTTP_METHODS:
            continue
        receiver = node.func.value
        name = receiver.id if isinstance(receiver, ast.Name) else getattr(receiver, "attr", "")
        if name.lstrip("_").endswith("session"):
            yield node


def test_every_auth_request_names_its_proxy():
    root = Path(auth_pkg.__file__).parent
    seen = 0
    missing: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _session_calls(tree):
            seen += 1
            if not any(kw.arg == "proxy" for kw in call.keywords):
                missing.append(f"{path.relative_to(root).as_posix()}:{call.lineno}")
    # The scan must find the requests it pins, or it pins nothing.
    assert seen >= 13, f"found only {seen} auth requests; the scan no longer sees them"
    assert not missing, f"auth requests without proxy=resolve_proxy(url): {missing}"
