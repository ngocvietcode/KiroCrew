"""Dashboard API for the company proxy (``network.*`` settings and the local proxy).

``GET  /api/network/proxy``       the saved settings and what this gateway started with
``POST /api/network/proxy``       save the settings; a password goes to the vault
``POST /api/network/proxy/test``  one test connection through the company proxy

Owner only: the settings name the proxy account and decide where every request of
the gateway goes. The password is never returned: the status says only whether
the vault holds one. Saving takes effect at the next gateway start.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from kiro_crew.auth_proxy import sspi
from kiro_crew.auth_proxy import supervisor as proxy_supervisor
from kiro_crew.auth_proxy.auth import build_auth, resolve_auth_mode
from kiro_crew.auth_proxy.handshake import UpstreamAuth
from kiro_crew.auth_proxy.probe import probe_upstream
from kiro_crew.auth_proxy.upstream import UpstreamError, parse_upstream
from kiro_crew.config.loader_document import update_config_locked
from kiro_crew.config.loader_locations import config_path, read_config_text
from kiro_crew.config.paths import config_dir
from kiro_crew.config.sections_network import (
    PROXY_AUTH_MODES,
    SECRET_REF_PREFIX,
    NetworkConfig,
    build_network_config,
)
from kiro_crew.dashboard.chat_utils_awaits import run_config_write
from kiro_crew.dashboard.handlers.source_providers import is_owner_dashboard_request
from kiro_crew.sel import sel

logger = logging.getLogger(__name__)

#: The vault entry a password saved from the dashboard is stored under.
VAULT_SECRET_NAME = "NETWORK_PROXY_PASSWORD"
#: Set on the app once settings were saved in this gateway process.
_SAVED_KEY = "network_proxy_saved_since_boot"
#: Longest accepted text field, so a pasted blob cannot land in config.json.
_MAX_FIELD_CHARS = 2048


def _vault() -> Any:
    from kiro_crew.secrets import SecretVault

    return SecretVault(config_dir())


def _saved_network() -> NetworkConfig:
    try:
        document = json.loads(read_config_text(config_path()))
    except (OSError, ValueError):
        return NetworkConfig()
    section = document.get("network") if isinstance(document, dict) else None
    return build_network_config(section if isinstance(section, dict) else {})


def _ref_name(ref: str) -> str:
    return ref[len(SECRET_REF_PREFIX) :] if ref.startswith(SECRET_REF_PREFIX) else ""


def _read_password(ref: str) -> str | None:
    name = _ref_name(ref)
    if not name:
        return None
    try:
        value = _vault().get(name)
    except Exception:
        logger.warning("network proxy: the vault could not be read")
        return None
    return value.reveal() if value is not None else None


async def _audit(request: web.Request, operation: str, outcome: str, error: str = "") -> None:
    def _log() -> None:
        user = request.get("user", "")
        sel().log_api_access(
            caller=str(user) if user else "dashboard-user",
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=request.path,
            error=error,
        )

    try:
        await asyncio.to_thread(_log)
    except Exception:
        logger.debug("Could not audit network proxy operation %s", operation, exc_info=True)


async def _require_owner(request: web.Request, operation: str) -> web.Response | None:
    if is_owner_dashboard_request(request):
        return None
    await _audit(request, operation, "denied", error="non-owner caller")
    return web.json_response(
        {"error": "Only the dashboard owner can manage the company proxy.", "code": "owner_only"},
        status=403,
    )


async def _read_body(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _bad(message: str, code: str) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=400)


async def _status_payload(request: web.Request) -> dict[str, Any]:
    network = await asyncio.to_thread(_saved_network)
    has_password = bool(network.proxy_password) and (
        await asyncio.to_thread(_read_password, network.proxy_password) is not None
    )
    return {
        "enabled": network.proxy_enabled,
        "url": network.proxy_url,
        "username": network.proxy_username,
        "no_proxy": network.proxy_no_proxy,
        "auth": network.proxy_auth,
        "windows_available": sspi.available(),
        "has_password": has_password,
        "restart_required": bool(request.app.get(_SAVED_KEY)),
        **proxy_supervisor.running_status(),
    }


async def api_network_proxy_status(request: web.Request) -> web.Response:
    """GET /api/network/proxy."""
    denied = await _require_owner(request, "network_proxy_status")
    if denied is not None:
        return denied
    return web.json_response(await _status_payload(request))


async def api_network_proxy_save(request: web.Request) -> web.Response:
    """POST /api/network/proxy {enabled, url, username, no_proxy, password?, clear_password?}."""
    denied = await _require_owner(request, "network_proxy_save")
    if denied is not None:
        return denied
    body = await _read_body(request)
    if body is None:
        return _bad("Expected a JSON object.", "invalid_body")
    url, username, no_proxy = _text(body, "url"), _text(body, "username"), _text(body, "no_proxy")
    if any(len(v) > _MAX_FIELD_CHARS for v in (url, username, no_proxy)):
        return _bad("A field is too long.", "field_too_long")
    if url:
        try:
            parse_upstream(url)
        except UpstreamError as exc:
            return _bad(str(exc), "invalid_proxy_url")
    password = body.get("password")
    if password is not None and not isinstance(password, str):
        return _bad("password must be text.", "invalid_password")
    clear = body.get("clear_password") is True
    mode = _text(body, "auth").lower() or "auto"
    if mode not in PROXY_AUTH_MODES:
        return _bad(f"auth must be one of {', '.join(PROXY_AUTH_MODES)}.", "invalid_auth")

    vault = _vault()
    current = await asyncio.to_thread(_saved_network)
    ref = current.proxy_password
    try:
        if password:
            await asyncio.to_thread(vault.set_sync, VAULT_SECRET_NAME, password)
            ref = SECRET_REF_PREFIX + VAULT_SECRET_NAME
        elif clear:
            if _ref_name(ref) == VAULT_SECRET_NAME:
                await asyncio.to_thread(vault.delete_sync, VAULT_SECRET_NAME)
            ref = ""
    except Exception as exc:
        await _audit(request, "network_proxy_save", "failed", error=f"vault: {type(exc).__name__}")
        return web.json_response(
            {
                "error": "The password could not be stored in the vault.",
                "code": "vault_write_failed",
            },
            status=500,
        )

    values = {
        "proxy_enabled": body.get("enabled") is True,
        "proxy_url": url,
        "proxy_username": username,
        "proxy_password": ref,
        "proxy_no_proxy": no_proxy,
        "proxy_auth": mode,
    }

    def _mutate(document: dict) -> dict:
        section = document.get("network")
        if not isinstance(section, dict):
            section = {}
        section.update(values)
        document["network"] = section
        return document

    try:
        await run_config_write(update_config_locked, mutate=_mutate)
    except ValueError as exc:
        await _audit(request, "network_proxy_save", "failed", error=type(exc).__name__)
        return _bad(str(exc), "config_write_refused")
    request.app[_SAVED_KEY] = True
    await _audit(request, "network_proxy_save", "success")
    return web.json_response(await _status_payload(request))


async def api_network_proxy_test(request: web.Request) -> web.Response:
    """POST /api/network/proxy/test {url, username, password?}.

    Without a password, the one stored in the vault is used, so a saved setup
    can be retested without typing it again.
    """
    denied = await _require_owner(request, "network_proxy_test")
    if denied is not None:
        return denied
    body = await _read_body(request)
    if body is None:
        return _bad("Expected a JSON object.", "invalid_body")
    url, username = _text(body, "url"), _text(body, "username")
    if not url:
        return _bad("Enter the company proxy URL to test.", "missing_proxy_url")
    try:
        upstream = parse_upstream(url)
    except UpstreamError as exc:
        return _bad(str(exc), "invalid_proxy_url")
    typed = body.get("password")
    password: str = typed if isinstance(typed, str) else ""
    if username and not password:
        saved = await asyncio.to_thread(_saved_network)
        stored = await asyncio.to_thread(_read_password, saved.proxy_password)
        password = stored or ""
    requested = _text(body, "auth").lower() or "auto"
    if requested not in PROXY_AUTH_MODES:
        return _bad(f"auth must be one of {', '.join(PROXY_AUTH_MODES)}.", "invalid_auth")
    mode = resolve_auth_mode(requested, username, windows_available=sspi.available())
    if mode == "windows" and not sspi.available():
        return _bad("Windows sign-in needs Windows.", "windows_unavailable")
    auth: UpstreamAuth = build_auth(
        mode, proxy_host=upstream.host, username=username, password=password
    )
    result = await probe_upstream(upstream, auth)
    await _audit(
        request, "network_proxy_test", "success" if result.ok else "failed", error=result.outcome
    )
    return web.json_response(result.to_json())
