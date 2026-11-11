"""Authenticated dashboard handlers for Sao Khuê CLI (saokhue-cli) interactive login."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
import urllib.parse
import urllib.request
from typing import Any

from aiohttp import web

from kiro_crew import model_registry
from kiro_crew.acp.saokhue_server import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_REVOKE_URL,
    DEFAULT_SAOKHUE_BASE_URL,
    _probe_user_and_models_sync,
    _urlopen_resilient,
    clear_saokhue_user_auth,
    load_saokhue_user_auth,
    login_with_browser_sso,
    login_with_password,
    login_with_token,
    poll_device_code_once,
    resolve_saokhue_settings,
    saokhue_auth_path,
    save_saokhue_user_auth,
    start_device_code_flow,
)
from kiro_crew.agent_sdk.backends import ACP_BACKEND_SAOKHUE, model_registry_namespace
from kiro_crew.dashboard.handlers.models_cold_probe import MODELS_CHANGED_EVENT
from kiro_crew.dashboard.handlers.source_providers import is_owner_dashboard_request
from kiro_crew.sel import sel

logger = logging.getLogger(__name__)

_PENDING_DEVICE_TTL_SECS = 900.0


def _caller(request: web.Request) -> str:
    user = request.get("user", "")
    return str(user) if user else "dashboard-user"


async def _read_json(request: web.Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def _audit(request: web.Request, operation: str, outcome: str, error: str = "") -> None:
    """SEL-audit a login mutation; a broken audit sink must not break login itself."""

    def _log() -> None:
        sel().log_api_access(
            caller=_caller(request),
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=request.path,
            error=error,
        )

    try:
        await asyncio.to_thread(_log)
    except Exception:
        logger.debug("Could not audit Sao Khuê CLI login operation %s", operation, exc_info=True)


async def _require_owner(request: web.Request, operation: str) -> web.Response | None:
    """Refuse a non-owner caller for a credential mutation."""
    if is_owner_dashboard_request(request):
        return None
    await _audit(request, operation, "denied", error="non-owner caller")
    return web.json_response(
        {
            "error": "Only the dashboard owner can manage Sao Khuê CLI sign-in.",
            "code": "owner_only",
        },
        status=403,
    )


def _build_status_sync(*, probe: bool = False) -> dict[str, Any]:
    """Read current Sao Khuê CLI auth status (without exposing raw tokens)."""
    user_auth = load_saokhue_user_auth()
    base_url, access_token, default_model, _ = resolve_saokhue_settings()
    auth_path = str(saokhue_auth_path())

    models: list[str] = [
        str(m).strip() for m in (user_auth.get("models") or []) if isinstance(m, str) and m.strip()
    ]
    user_id = str(user_auth.get("user_id") or "").strip()
    email = str(user_auth.get("email") or "").strip()

    if probe and access_token:
        with contextlib.suppress(Exception):
            probed_models, probed_uid, probed_email = _probe_user_and_models_sync(
                base_url,
                access_token,
                timeout_secs=8.0,
                userinfo_url=str(user_auth.get("userinfo_url") or ""),
            )
            if probed_models:
                models = probed_models
            if probed_uid and not user_id:
                user_id = probed_uid
            if probed_email and not email:
                email = probed_email
            if user_auth and (probed_models or probed_uid or probed_email):
                updated = dict(user_auth)
                if probed_models:
                    updated["models"] = probed_models
                if user_id:
                    updated["user_id"] = user_id
                if email:
                    updated["email"] = email
                save_saokhue_user_auth(updated)

    expires_at_raw = user_auth.get("expires_at")
    expires_at: int | None = None
    expired = False
    if expires_at_raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            expires_at = int(float(expires_at_raw))
            expired = time.time() >= float(expires_at)

    authenticated = bool(access_token) and not (expired and not user_auth.get("refresh_token"))
    auth_method = str(user_auth.get("auth_method") or ("env" if authenticated else "")).strip()

    return {
        "authenticated": authenticated,
        "user_id": user_id or ("user" if authenticated else ""),
        "email": email,
        "base_url": base_url or DEFAULT_SAOKHUE_BASE_URL,
        "auth_method": auth_method,
        "client_id": str(user_auth.get("client_id") or DEFAULT_OIDC_CLIENT_ID).strip(),
        "default_model": default_model,
        "models": models,
        "expires_at": expires_at,
        "expired": expired,
        "has_refresh_token": bool(user_auth.get("refresh_token")),
        "auth_path": auth_path,
    }


async def _status(request: web.Request, *, probe: bool = False) -> dict[str, Any]:
    """The sign-in status, with its model list handed to the chat model picker.

    The picker lists what a Sao Khuê session advertised, and no session starts
    before the first chat, so without this a sign-in filled this card's model
    list while the picker kept showing Auto alone (or what a session that ran
    signed out had advertised) until a new chat started. The list is the same
    ``/v1/models`` answer a session reads, for the same account.
    """
    payload = await asyncio.to_thread(_build_status_sync, probe=probe)
    if payload.get("authenticated"):
        await _publish_picker_models(request, payload.get("models") or [])
    return payload


async def _publish_picker_models(request: web.Request, models: list[str]) -> None:
    """Record *models* as the Sao Khuê picker list and tell dashboards to re-read it."""
    ids = [m for m in models if m not in ("auto", DEFAULT_FALLBACK_MODEL)]
    namespace = model_registry_namespace(ACP_BACKEND_SAOKHUE)
    if not ids or not model_registry.refresh_advertised_models(namespace, ids):
        return
    await asyncio.to_thread(model_registry.persist_advertised_models)
    state = request.app.get("state")
    if state is not None:
        state.broadcast_ws(MODELS_CHANGED_EVENT, {})


def _pending_device_store(request: web.Request) -> dict[str, dict[str, Any]]:
    store = request.app.get("saokhue_device_logins")
    if not isinstance(store, dict):
        store = {}
        request.app["saokhue_device_logins"] = store
    now = time.monotonic()
    expired_keys = [
        k
        for k, v in store.items()
        if not isinstance(v, dict)
        or (now - float(v.get("created_at") or 0.0)) > _PENDING_DEVICE_TTL_SECS
    ]
    for k in expired_keys:
        store.pop(k, None)
    return store


async def api_saokhue_login_status(request: web.Request) -> web.Response:
    """GET /api/saokhue-login — current Sao Khuê CLI authentication status."""
    probe = request.query.get("probe", "").strip().lower() in ("1", "true", "yes")
    try:
        payload = await _status(request, probe=probe)
        return web.json_response(payload)
    except Exception as exc:
        logger.warning("Failed to read Sao Khuê CLI login status", exc_info=True)
        return web.json_response(
            {
                "error": f"Could not read Sao Khuê CLI sign-in status: {exc}",
                "code": "status_failed",
            },
            status=500,
        )


async def api_saokhue_login_sso(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/sso — complete Browser OAuth2 (PKCE) sign-in."""
    denied = await _require_owner(request, "saokhue_login_sso")
    if denied is not None:
        return denied

    body = await _read_json(request) or {}
    base_url = str(body.get("base_url") or "").strip()
    sso_url = str(body.get("sso_url") or "").strip()
    token_url = str(body.get("token_url") or "").strip()
    userinfo_url = str(body.get("userinfo_url") or "").strip()
    client_id = str(body.get("client_id") or "").strip()
    scope = str(body.get("scope") or "").strip()
    resource = str(body.get("resource") or "").strip()
    default_model = str(body.get("default_model") or "").strip()
    open_browser = bool(body.get("open_browser", True))

    try:
        await asyncio.to_thread(
            login_with_browser_sso,
            base_url,
            sso_url=sso_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            client_id=client_id,
            scope=scope,
            resource=resource,
            default_model=default_model,
            open_browser=open_browser,
            timeout_secs=180.0,
        )
    except TimeoutError as exc:
        await _audit(request, "saokhue_login_sso", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "sso_timeout"},
            status=504,
        )
    except Exception as exc:
        await _audit(request, "saokhue_login_sso", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "sso_failed"},
            status=502,
        )

    await _audit(request, "saokhue_login_sso", "success")
    status_payload = await _status(request)
    return web.json_response({"ok": True, **status_payload})


async def api_saokhue_login_device_start(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/device/start — start OAuth2 Device Authorization Flow."""
    denied = await _require_owner(request, "saokhue_login_device_start")
    if denied is not None:
        return denied

    body = await _read_json(request) or {}
    base_url = str(body.get("base_url") or "").strip()
    device_auth_url = str(body.get("device_auth_url") or "").strip()
    token_url = str(body.get("token_url") or "").strip()
    userinfo_url = str(body.get("userinfo_url") or "").strip()
    client_id = str(body.get("client_id") or "").strip()
    scope = str(body.get("scope") or "").strip()
    resource = str(body.get("resource") or "").strip()
    default_model = str(body.get("default_model") or "").strip()

    try:
        pending = await asyncio.to_thread(
            start_device_code_flow,
            base_url,
            device_auth_url=device_auth_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            client_id=client_id,
            scope=scope,
            resource=resource,
            default_model=default_model,
        )
    except Exception as exc:
        await _audit(request, "saokhue_login_device_start", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "device_start_failed"},
            status=502,
        )

    login_id = secrets.token_urlsafe(16)
    pending["created_at"] = time.monotonic()
    store = _pending_device_store(request)
    store[login_id] = pending

    await _audit(request, "saokhue_login_device_start", "success")
    return web.json_response(
        {
            "ok": True,
            "login_id": login_id,
            "user_code": pending["user_code"],
            "verification_uri": pending["verification_uri"],
            "verification_uri_complete": pending["verification_uri_complete"],
            "interval": pending["interval"],
            "expires_in": pending["expires_in"],
        }
    )


async def api_saokhue_login_device_poll(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/device/poll — poll pending OAuth2 Device Authorization Flow."""
    denied = await _require_owner(request, "saokhue_login_device_poll")
    if denied is not None:
        return denied

    body = await _read_json(request) or {}
    login_id = str(body.get("login_id") or "").strip()
    store = _pending_device_store(request)

    pending: dict[str, Any] | None = None
    if login_id and login_id in store:
        pending = store[login_id]
    elif not login_id and store:
        login_id, pending = next(reversed(list(store.items())))

    if pending is None:
        return web.json_response(
            {"error": "No active device authorization session found.", "code": "unknown_login"},
            status=404,
        )

    try:
        res = await asyncio.to_thread(poll_device_code_once, pending)
    except Exception as exc:
        store.pop(login_id, None)
        await _audit(request, "saokhue_login_device_poll", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "device_poll_failed"},
            status=502,
        )

    if res.get("status") == "pending":
        return web.json_response(
            {
                "ok": True,
                "status": "pending",
                "slow_down": bool(res.get("slow_down")),
            }
        )

    store.pop(login_id, None)
    await _audit(request, "saokhue_login_device_poll", "success")
    status_payload = await _status(request)
    return web.json_response(
        {
            "ok": True,
            "status": "authorized",
            **status_payload,
        }
    )


async def api_saokhue_login_password(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/password — sign in with Username & Password."""
    denied = await _require_owner(request, "saokhue_login_password")
    if denied is not None:
        return denied

    body = await _read_json(request)
    if body is None:
        return web.json_response(
            {"error": "Request body must be a JSON object.", "code": "invalid_body"},
            status=400,
        )

    base_url = str(body.get("base_url") or "").strip()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    login_endpoint = str(body.get("login_endpoint") or "").strip()
    default_model = str(body.get("default_model") or "").strip()

    if not username or not password:
        return web.json_response(
            {"error": "Both username and password are required.", "code": "missing_credentials"},
            status=400,
        )

    try:
        await asyncio.to_thread(
            login_with_password,
            base_url,
            username,
            password,
            login_endpoint=login_endpoint,
            default_model=default_model,
        )
    except Exception as exc:
        await _audit(request, "saokhue_login_password", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "password_login_failed"},
            status=401,
        )

    await _audit(request, "saokhue_login_password", "success")
    status_payload = await _status(request)
    return web.json_response({"ok": True, **status_payload})


async def api_saokhue_login_token(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/token — sign in with Personal Access Token / Virtual Key."""
    denied = await _require_owner(request, "saokhue_login_token")
    if denied is not None:
        return denied

    body = await _read_json(request)
    if body is None:
        return web.json_response(
            {"error": "Request body must be a JSON object.", "code": "invalid_body"},
            status=400,
        )

    base_url = str(body.get("base_url") or "").strip()
    token = str(body.get("token") or "").strip()
    user_id = str(body.get("user_id") or "").strip()
    default_model = str(body.get("default_model") or "").strip()
    verify = bool(body.get("verify", True))

    if not token:
        return web.json_response(
            {"error": "Access token / virtual key is required.", "code": "missing_token"},
            status=400,
        )

    try:
        await asyncio.to_thread(
            login_with_token,
            base_url,
            token,
            user_id=user_id,
            default_model=default_model,
            verify=verify,
        )
    except ValueError as exc:
        await _audit(request, "saokhue_login_token", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "invalid_token"},
            status=400,
        )
    except Exception as exc:
        await _audit(request, "saokhue_login_token", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "token_login_failed"},
            status=401,
        )

    await _audit(request, "saokhue_login_token", "success")
    status_payload = await _status(request)
    return web.json_response({"ok": True, **status_payload})


async def api_saokhue_login_model(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/model — update the user's preferred default model."""
    denied = await _require_owner(request, "saokhue_login_model")
    if denied is not None:
        return denied

    body = await _read_json(request)
    if body is None:
        return web.json_response(
            {"error": "Request body must be a JSON object.", "code": "invalid_body"},
            status=400,
        )

    model = str(body.get("default_model") or body.get("model") or "").strip()
    if not model:
        return web.json_response(
            {"error": "default_model must be a non-empty string.", "code": "missing_model"},
            status=400,
        )

    def _update_model() -> None:
        auth = load_saokhue_user_auth(auto_refresh=False)
        updated = dict(auth) if isinstance(auth, dict) else {}
        updated["default_model"] = model
        save_saokhue_user_auth(updated)

    try:
        await asyncio.to_thread(_update_model)
    except Exception as exc:
        await _audit(request, "saokhue_login_model", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "model_update_failed"},
            status=500,
        )

    await _audit(request, "saokhue_login_model", "success")
    status_payload = await _status(request)
    return web.json_response({"ok": True, **status_payload})


def _logout_sync() -> bool:
    auth = load_saokhue_user_auth(auto_refresh=False)
    if auth:
        revoke_url = str(auth.get("revoke_url") or DEFAULT_OIDC_REVOKE_URL).strip()
        client_id = str(auth.get("client_id") or DEFAULT_OIDC_CLIENT_ID).strip()
        tok = str(auth.get("refresh_token") or auth.get("access_token") or "").strip()
        if revoke_url and tok:
            with contextlib.suppress(Exception):
                req = urllib.request.Request(
                    revoke_url,
                    data=urllib.parse.urlencode({"token": tok, "client_id": client_id}).encode(
                        "utf-8"
                    ),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with _urlopen_resilient(req, timeout=5.0):
                    pass
    return clear_saokhue_user_auth()


async def api_saokhue_login_logout(request: web.Request) -> web.Response:
    """POST /api/saokhue-login/logout — revoke token and remove ~/.saokhue-cli/auth.json."""
    denied = await _require_owner(request, "saokhue_login_logout")
    if denied is not None:
        return denied

    store = request.app.get("saokhue_device_logins")
    if isinstance(store, dict):
        store.clear()

    try:
        removed = await asyncio.to_thread(_logout_sync)
    except Exception as exc:
        await _audit(request, "saokhue_login_logout", "failed", error=str(exc))
        return web.json_response(
            {"error": str(exc), "code": "logout_failed"},
            status=500,
        )

    await _audit(request, "saokhue_login_logout", "success")
    status_payload = await _status(request)
    return web.json_response({"ok": True, "removed": removed, **status_payload})
