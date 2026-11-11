"""Sign-in for ``saokhue-cli``: stored credentials, OIDC endpoints, and each login flow.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from kiro_crew.config.paths import config_dir
from kiro_crew.saokhue_cli.base import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_OIDC_AUTHORIZE_URL,
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_DEVICE_AUTH_URL,
    DEFAULT_OIDC_RESOURCE,
    DEFAULT_OIDC_REVOKE_URL,
    DEFAULT_OIDC_SCOPE,
    DEFAULT_OIDC_TOKEN_URL,
    DEFAULT_OIDC_USERINFO_URL,
    DEFAULT_SAOKHUE_BASE_URL,
    saokhue_auth_path,
)
from kiro_crew.saokhue_cli.tls import _urlopen_resilient

logger = logging.getLogger("kiro_crew.acp.saokhue_server")


_NON_CHAT_MODEL_SUBSTRINGS: tuple[str, ...] = (
    "embed",
    "rerank",
    "whisper",
    "tts",
    "moderation",
    "bge-",
    "dall-e",
)


def _is_likely_chat_model(model_id: str) -> bool:
    """Return True if *model_id* does not look like an embedding/audio/rerank model."""
    lower = (model_id or "").lower()
    return not any(tok in lower for tok in _NON_CHAT_MODEL_SUBSTRINGS)


def _match_listed_model(requested: str, available_models: list[str]) -> str:
    """The listed model *requested* names, or ``""`` when none does.

    A name matches exactly, in any case, or by its last ``/`` segment on either
    side (``claude-x`` names ``bedrock/claude-x``). ``auto`` and the fallback
    placeholder are never a listed model.
    """
    req = (requested or "").strip()
    if not req:
        return ""
    real_models = [
        m
        for m in available_models
        if isinstance(m, str) and m.strip() and m.strip() not in ("auto", DEFAULT_FALLBACK_MODEL)
    ]
    if req in real_models:
        return req
    req_lower = req.lower()
    for m in real_models:
        m_lower = m.lower()
        if (
            m_lower == req_lower
            or m_lower.endswith(f"/{req_lower}")
            or req_lower.endswith(f"/{m_lower}")
        ):
            return m
    return ""


def _select_valid_saokhue_model(
    requested: str, available_models: list[str], env_model: str = ""
) -> str:
    """Resolve *requested* against *available_models* so unserved Kiro model IDs or 'auto' fall back to a real Sao Khuê model."""
    req = (requested or "").strip()
    if req in ("auto", DEFAULT_FALLBACK_MODEL):
        req = ""
    real_models = [
        m
        for m in available_models
        if isinstance(m, str) and m.strip() and m.strip() not in ("auto", DEFAULT_FALLBACK_MODEL)
    ]
    if real_models:
        matched = _match_listed_model(req, real_models)
        if matched:
            return matched
        env_clean = (env_model or "").strip()
        if env_clean and env_clean in real_models:
            return env_clean
        chat_models = [m for m in real_models if _is_likely_chat_model(m)]
        return chat_models[0] if chat_models else real_models[0]
    if req:
        return req
    if env_model and env_model.strip() not in ("", "auto", DEFAULT_FALLBACK_MODEL):
        return env_model.strip()
    return DEFAULT_FALLBACK_MODEL


def _persist_preferred_model(model_id: str, environ: dict[str, str] | None = None) -> None:
    """Save *model_id* as ``default_model`` in ``~/.saokhue-cli/auth.json`` so Mates and future sessions inherit it."""
    clean = (model_id or "").strip()
    if not clean or clean in ("auto", DEFAULT_FALLBACK_MODEL):
        return
    try:
        auth = load_saokhue_user_auth(environ, auto_refresh=False)
        if not auth:
            return
        if auth.get("default_model") != clean:
            auth["default_model"] = clean
            save_saokhue_user_auth(auth, environ)
    except Exception:
        logger.debug("Could not persist preferred model %s to auth.json", clean, exc_info=True)


def save_saokhue_user_auth(
    auth_data: dict[str, Any], environ: dict[str, str] | None = None
) -> Path:
    """Persist per-user login state to ``~/.saokhue-cli/auth.json`` with 0600 permissions."""
    path = saokhue_auth_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    payload = dict(auth_data)
    payload["updated_at"] = int(time.time())
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp_path, 0o600)
    tmp_path.replace(path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return path


def clear_saokhue_user_auth(environ: dict[str, str] | None = None) -> bool:
    """Delete ``~/.saokhue-cli/auth.json``. Return True if a credential file was removed."""
    path = saokhue_auth_path(environ)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        logger.debug("Could not remove %s", path, exc_info=True)
    return False


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode unverified JWT payload claims (sub, preferred_username, email, exp) if *token* is a JWT."""
    parts = (token or "").strip().split(".")
    if len(parts) != 3:
        return {}
    try:
        payload_b64 = parts[1]
        padding = "=" * ((4 - len(payload_b64) % 4) % 4)
        raw = base64.urlsafe_b64decode((payload_b64 + padding).encode("ascii"))
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def resolve_oidc_endpoints(
    base_url: str = "",
    *,
    authorize_url: str = "",
    device_auth_url: str = "",
    token_url: str = "",
    userinfo_url: str = "",
    revoke_url: str = "",
    client_id: str = "",
    scope: str = "",
    resource: str = "",
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve OIDC / OAuth2 endpoints and client_id (defaults to VPBank IdP ``app:test-crew``)."""
    env = os.environ if environ is None else environ
    norm_base = _normalize_base_url(base_url or DEFAULT_SAOKHUE_BASE_URL)
    parsed = urllib.parse.urlparse(norm_base)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""

    if origin and "vpbank.dev" not in origin and norm_base != DEFAULT_SAOKHUE_BASE_URL:
        default_auth = f"{origin}/api/idpOAuth/authorize"
        default_device = f"{origin}/api/idpOAuth/device/auth"
        default_token = f"{origin}/api/idpOAuth/token"
        default_userinfo = f"{origin}/api/idpOAuth/me"
        default_revoke = f"{origin}/api/idpOAuth/revoke"
        default_resource = f"{origin}/api/"
    else:
        default_auth = DEFAULT_OIDC_AUTHORIZE_URL
        default_device = DEFAULT_OIDC_DEVICE_AUTH_URL
        default_token = DEFAULT_OIDC_TOKEN_URL
        default_userinfo = DEFAULT_OIDC_USERINFO_URL
        default_revoke = DEFAULT_OIDC_REVOKE_URL
        default_resource = DEFAULT_OIDC_RESOURCE

    return {
        "authorize_url": (
            authorize_url or env.get("SAOKHUE_OIDC_AUTHORIZE_URL") or default_auth
        ).strip(),
        "device_auth_url": (
            device_auth_url or env.get("SAOKHUE_OIDC_DEVICE_AUTH_URL") or default_device
        ).strip(),
        "token_url": (token_url or env.get("SAOKHUE_OIDC_TOKEN_URL") or default_token).strip(),
        "userinfo_url": (
            userinfo_url or env.get("SAOKHUE_OIDC_USERINFO_URL") or default_userinfo
        ).strip(),
        "revoke_url": (revoke_url or env.get("SAOKHUE_OIDC_REVOKE_URL") or default_revoke).strip(),
        "client_id": (client_id or env.get("SAOKHUE_CLIENT_ID") or DEFAULT_OIDC_CLIENT_ID).strip(),
        "scope": (scope or env.get("SAOKHUE_OIDC_SCOPE") or DEFAULT_OIDC_SCOPE).strip(),
        "resource": (resource or env.get("SAOKHUE_OIDC_RESOURCE") or default_resource).strip(),
    }


def refresh_saokhue_user_token(
    auth_data: dict[str, Any], environ: dict[str, str] | None = None
) -> dict[str, Any]:
    """Refresh ``access_token`` using ``refresh_token`` when ``token_url`` is configured."""
    refresh_tok = str(auth_data.get("refresh_token") or "").strip()
    base_url = str(auth_data.get("base_url") or DEFAULT_SAOKHUE_BASE_URL).rstrip("/")
    token_url = str(auth_data.get("token_url") or DEFAULT_OIDC_TOKEN_URL).strip()
    if not refresh_tok or not token_url:
        return auth_data

    form_data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
    }
    client_id = str(auth_data.get("client_id") or DEFAULT_OIDC_CLIENT_ID).strip()
    if client_id:
        form_data["client_id"] = client_id
    resource = str(auth_data.get("resource") or DEFAULT_OIDC_RESOURCE).strip()
    if resource:
        form_data["resource"] = resource

    body = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        token_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen_resilient(req, timeout=15.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            res = json.loads(raw) if raw.strip() else {}
        if isinstance(res, dict):
            new_token = str(
                res.get("access_token") or res.get("token") or res.get("id_token") or ""
            ).strip()
            if new_token:
                updated = dict(auth_data)
                updated["access_token"] = new_token
                if res.get("refresh_token"):
                    updated["refresh_token"] = str(res["refresh_token"]).strip()
                if res.get("id_token"):
                    updated["id_token"] = str(res["id_token"]).strip()
                if res.get("expires_in"):
                    updated["expires_at"] = int(time.time()) + int(res["expires_in"])
                save_saokhue_user_auth(updated, environ)
                return updated
    except Exception:
        logger.debug("Token refresh against %s failed", token_url, exc_info=True)
    return auth_data


def load_saokhue_user_auth(
    environ: dict[str, str] | None = None, *, auto_refresh: bool = True
) -> dict[str, Any]:
    """Load per-user login state from ``~/.saokhue-cli/auth.json``, refreshing if near expiry."""
    path = saokhue_auth_path(environ)
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
    except Exception:
        logger.debug("Could not read %s", path, exc_info=True)
        return {}

    if auto_refresh and data.get("refresh_token") and data.get("expires_at"):
        try:
            expires_at = float(data["expires_at"])
            if time.time() >= (expires_at - 60.0):
                data = refresh_saokhue_user_token(data, environ)
        except (TypeError, ValueError):
            pass
    return data


def _load_dotenv_into_dict() -> dict[str, str]:
    """Read key-value pairs from ``~/.kiro/crew/.env`` if present."""
    out: dict[str, str] = {}
    try:
        env_path = config_dir() / ".env"
        if not env_path.is_file():
            return out
        for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            k, _, v = line.partition("=")
            key = k.strip()
            val = v.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key:
                out[key] = val
    except Exception:
        logger.debug("Failed to read .env for saokhue config", exc_info=True)
    return out


def _normalize_base_url(raw_url: str) -> str:
    base_url = (raw_url or DEFAULT_SAOKHUE_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
    return base_url or DEFAULT_SAOKHUE_BASE_URL


def resolve_saokhue_settings(environ: dict[str, str] | None = None) -> tuple[str, str, str, float]:
    """Return ``(base_url, access_token, default_model, timeout_secs)`` from user login or env."""
    env = dict(_load_dotenv_into_dict())
    env.update(os.environ if environ is None else environ)
    user_auth = load_saokhue_user_auth(env)

    raw_url = (
        env.get("SAOKHUE_BASE_URL")
        or env.get("SAOKHUE_PROXY_URL")
        or str(user_auth.get("base_url") or "")
        or env.get("OPENAI_BASE_URL")
        or env.get("OPENAI_API_BASE")
        or DEFAULT_SAOKHUE_BASE_URL
    ).strip()
    base_url = _normalize_base_url(raw_url)

    access_token = (
        str(user_auth.get("access_token") or user_auth.get("token") or "").strip()
        or (env.get("SAOKHUE_ACCESS_TOKEN") or "").strip()
        or (env.get("SAOKHUE_API_KEY") or "").strip()
        or (env.get("OPENAI_API_KEY") or "").strip()
    )

    default_model = (env.get("SAOKHUE_MODEL") or "").strip() or str(
        user_auth.get("default_model") or ""
    ).strip()
    try:
        timeout_secs = float(env.get("SAOKHUE_TIMEOUT") or user_auth.get("timeout") or 300.0)
    except (TypeError, ValueError):
        timeout_secs = 300.0
    return base_url, access_token, default_model, max(10.0, timeout_secs)


def _extract_token_from_response(
    data: Any, headers: Any = None
) -> tuple[str, str, int | None, str, str]:
    """Extract ``(access_token, refresh_token, expires_at, user_id, email)`` from login response."""
    access_token = ""
    refresh_token = ""
    expires_at: int | None = None
    user_id = ""
    email = ""

    if isinstance(data, dict):
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        for src in (data, nested):
            if not access_token:
                for k in ("access_token", "token", "key", "api_key", "jwt"):
                    val = src.get(k)
                    if isinstance(val, str) and val.strip():
                        access_token = val.strip()
                        break
            if not refresh_token and isinstance(src.get("refresh_token"), str):
                refresh_token = src["refresh_token"].strip()
            if expires_at is None:
                if src.get("expires_at"):
                    with contextlib.suppress(TypeError, ValueError):
                        expires_at = int(float(src["expires_at"]))
                elif src.get("expires_in"):
                    with contextlib.suppress(TypeError, ValueError):
                        expires_at = int(time.time()) + int(float(src["expires_in"]))
            if not user_id:
                for uk in ("preferred_username", "user_id", "username", "name", "sub", "user"):
                    uval = src.get(uk)
                    if isinstance(uval, str) and uval.strip():
                        user_id = uval.strip()
                        break
            if not email and isinstance(src.get("email"), str):
                email = src["email"].strip()

        for jwt_candidate in (
            str(data.get("id_token") or ""),
            access_token,
        ):
            claims = _decode_jwt_claims(jwt_candidate)
            if claims:
                if not user_id:
                    for uk in ("preferred_username", "username", "name", "email", "sub"):
                        cval = claims.get(uk)
                        if isinstance(cval, str) and cval.strip():
                            user_id = cval.strip()
                            break
                if not email and isinstance(claims.get("email"), str):
                    email = claims["email"].strip()
                if expires_at is None and claims.get("exp"):
                    with contextlib.suppress(TypeError, ValueError):
                        expires_at = int(float(claims["exp"]))

    if not access_token and headers is not None:
        set_cookies: list[str] = []
        if hasattr(headers, "get_all"):
            set_cookies = list(headers.get_all("Set-Cookie") or ())
        elif isinstance(headers, dict):
            sc = headers.get("Set-Cookie") or headers.get("set-cookie")
            if isinstance(sc, str):
                set_cookies = [sc]
        for cookie_hdr in set_cookies:
            for part in cookie_hdr.split(";"):
                k, _, v = part.strip().partition("=")
                if k.strip().lower() in ("token", "access_token", "saokhue_token") and v.strip():
                    access_token = v.strip().strip('"')
                    break

    return access_token, refresh_token, expires_at, user_id, email


def _probe_oidc_userinfo_sync(
    access_token: str,
    userinfo_url: str = "",
    timeout_secs: float = 8.0,
) -> tuple[str, str]:
    """Fetch ``(user_id, email)`` from OIDC ``userinfo_endpoint`` (e.g. ``/api/idpOAuth/me``)."""
    target = (userinfo_url or DEFAULT_OIDC_USERINFO_URL).strip()
    if not access_token or not target:
        return "", ""
    req = urllib.request.Request(
        target,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="GET",
    )
    try:
        with _urlopen_resilient(req, timeout=timeout_secs) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
        if isinstance(data, dict):
            uid = ""
            for k in ("preferred_username", "username", "user_id", "name", "email", "sub"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    uid = v.strip()
                    break
            mail = str(data.get("email") or data.get("user_email") or "").strip()
            return uid, mail
    except Exception:
        logger.debug("OIDC userinfo probe against %s failed", target, exc_info=True)
    return "", ""


def _query_models_endpoint_sync(base_url: str, token: str, timeout_secs: float = 10.0) -> list[str]:
    """Query ``{base_url}/v1/models`` with *token*, raising a detailed RuntimeError on HTTP error."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url}/v1/models", headers=headers, method="GET")
    try:
        with _urlopen_resilient(req, timeout=timeout_secs) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
            items = data.get("data") if isinstance(data, dict) else None
            if isinstance(items, list):
                return [
                    str(item["id"]).strip()
                    for item in items
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ]
            return []
    except urllib.error.HTTPError as exc:
        err_body = ""
        with contextlib.suppress(Exception):
            err_body = exc.read().decode("utf-8", errors="replace").strip()
        www_auth = ""
        with contextlib.suppress(Exception):
            www_auth = str(exc.headers.get("WWW-Authenticate") or "").strip()
        detail_parts = [f"HTTP Error {exc.code}: {exc.reason}"]
        if www_auth:
            detail_parts.append(f"WWW-Authenticate: {www_auth}")
        if err_body:
            detail_parts.append(f"Body: {err_body[:300]}")
        raise urllib.error.HTTPError(
            exc.url,
            exc.code,
            " | ".join(detail_parts),
            exc.hdrs,
            exc.fp,
        ) from exc


def _probe_user_and_models_sync(
    base_url: str,
    access_token: str,
    timeout_secs: float = 10.0,
    *,
    userinfo_url: str = "",
) -> tuple[list[str], str, str]:
    """Verify *access_token* against ``/v1/models`` (``Authorization: Bearer <access_token>``) and ``/me``."""
    headers: dict[str, str] = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    models = _query_models_endpoint_sync(base_url, access_token, timeout_secs=timeout_secs)
    user_id = ""
    email = ""

    if userinfo_url:
        user_id, email = _probe_oidc_userinfo_sync(
            access_token, userinfo_url=userinfo_url, timeout_secs=5.0
        )

    if not user_id:
        with contextlib.suppress(Exception):
            ureq = urllib.request.Request(f"{base_url}/user/info", headers=headers, method="GET")
            with _urlopen_resilient(ureq, timeout=5.0) as uresp:
                uraw = uresp.read().decode("utf-8", errors="replace")
                udata = json.loads(uraw) if uraw.strip() else {}
                if isinstance(udata, dict):
                    info = (
                        udata.get("user_info")
                        if isinstance(udata.get("user_info"), dict)
                        else udata
                    )
                    user_id = str(
                        info.get("preferred_username")
                        or info.get("user_id")
                        or info.get("username")
                        or ""
                    ).strip()
                    email = str(info.get("user_email") or info.get("email") or "").strip()

    return models, user_id, email


def login_with_password(
    base_url: str,
    username: str,
    password: str,
    *,
    login_endpoint: str = "",
    default_model: str = "",
    timeout_secs: float = 30.0,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Authenticate a user with ``username`` and ``password`` against Sao Khuê and save ``auth.json``."""
    norm_url = _normalize_base_url(base_url)
    endpoints = (
        [login_endpoint]
        if login_endpoint
        else [
            f"{norm_url}/login",
            f"{norm_url}/v2/login",
            f"{norm_url}/auth/login",
            f"{norm_url}/token",
            f"{norm_url}/user/login",
        ]
    )

    last_err: Exception | None = None
    access_token = ""
    refresh_token = ""
    expires_at: int | None = None
    resolved_user = username.strip()
    resolved_email = ""

    payload_variants = [
        (
            "application/json",
            json.dumps({"username": username, "password": password, "user_id": username}).encode(
                "utf-8"
            ),
        ),
        (
            "application/x-www-form-urlencoded",
            urllib.parse.urlencode(
                {"username": username, "password": password, "grant_type": "password"}
            ).encode("utf-8"),
        ),
    ]

    for ep in endpoints:
        url = ep if ep.startswith(("http://", "https://")) else f"{norm_url}/{ep.lstrip('/')}"
        for ctype, body_bytes in payload_variants:
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={"Content-Type": ctype, "Accept": "application/json"},
                method="POST",
            )
            try:
                with _urlopen_resilient(req, timeout=timeout_secs) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw) if raw.strip() else {}
                    except ValueError:
                        parsed = {}
                    tok, rtok, exp, uid, mail = _extract_token_from_response(
                        parsed, getattr(resp, "headers", None)
                    )
                    if tok:
                        access_token = tok
                        refresh_token = rtok
                        expires_at = exp
                        if uid:
                            resolved_user = uid
                        if mail:
                            resolved_email = mail
                        break
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (401, 403):
                    err_txt = ""
                    with contextlib.suppress(Exception):
                        err_txt = exc.read().decode("utf-8", errors="replace")[:300]
                    raise RuntimeError(
                        f"Authentication rejected by {url} (HTTP {exc.code}): {err_txt or exc.reason}"
                    ) from exc
            except Exception as exc:
                last_err = exc
        if access_token:
            break

    if not access_token:
        raise RuntimeError(
            f"Could not obtain an access token from {norm_url} "
            f"(last error: {last_err or 'no token in response'})."
        )

    models: list[str] = []
    with contextlib.suppress(Exception):
        models, probed_uid, probed_email = _probe_user_and_models_sync(
            norm_url, access_token, timeout_secs=10.0
        )
        if probed_uid and not resolved_user:
            resolved_user = probed_uid
        if probed_email and not resolved_email:
            resolved_email = probed_email

    chosen_model = default_model.strip() or (
        _select_valid_saokhue_model("", models) if models else ""
    )
    auth_record: dict[str, Any] = {
        "base_url": norm_url,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "user_id": resolved_user,
        "email": resolved_email,
        "auth_method": "password",
        "default_model": chosen_model,
        "models": models,
    }
    save_saokhue_user_auth(auth_record, environ)
    return auth_record


def login_with_token(
    base_url: str,
    token: str,
    *,
    user_id: str = "",
    default_model: str = "",
    verify: bool = True,
    timeout_secs: float = 15.0,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Save a per-user personal access token / JWT / virtual key after optional verification."""
    norm_url = _normalize_base_url(base_url)
    clean_tok = token.strip()
    if clean_tok.lower().startswith("bearer "):
        clean_tok = clean_tok[7:].strip()
    if not clean_tok:
        raise ValueError("Access token must not be empty.")

    models: list[str] = []
    resolved_user = user_id.strip()
    resolved_email = ""
    if verify:
        try:
            models, probed_uid, probed_email = _probe_user_and_models_sync(
                norm_url, clean_tok, timeout_secs=timeout_secs
            )
            if probed_uid and not resolved_user:
                resolved_user = probed_uid
            if probed_email:
                resolved_email = probed_email
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"Token rejected by {norm_url}/v1/models (HTTP {exc.code})."
                ) from exc

    chosen_model = default_model.strip() or (
        _select_valid_saokhue_model("", models) if models else ""
    )
    auth_record: dict[str, Any] = {
        "base_url": norm_url,
        "access_token": clean_tok,
        "refresh_token": "",
        "expires_at": None,
        "user_id": resolved_user or "user",
        "email": resolved_email,
        "auth_method": "token",
        "default_model": chosen_model,
        "models": models,
    }
    save_saokhue_user_auth(auth_record, environ)
    return auth_record


def login_with_browser_sso(
    base_url: str,
    *,
    sso_url: str = "",
    token_url: str = "",
    userinfo_url: str = "",
    client_id: str = "",
    scope: str = "",
    resource: str = "",
    callback_port: int = 0,
    default_model: str = "",
    open_browser: bool = True,
    timeout_secs: float = 180.0,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Authenticate a user via Browser OAuth2 / OIDC Authorization Code + PKCE redirect to localhost."""
    norm_url = _normalize_base_url(base_url)
    oidc = resolve_oidc_endpoints(
        norm_url,
        authorize_url=sso_url,
        token_url=token_url,
        userinfo_url=userinfo_url,
        client_id=client_id,
        scope=scope,
        resource=resource,
        environ=environ,
    )
    resolved_client_id = oidc["client_id"]
    resolved_authorize_url = oidc["authorize_url"]
    resolved_token_url = oidc["token_url"]
    resolved_userinfo_url = oidc["userinfo_url"]
    resolved_scope = oidc["scope"]
    resolved_resource = oidc["resource"]

    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    result_box: dict[str, Any] = {}
    done_event = threading.Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _handle_params(self, qs: dict[str, list[str]]) -> bool:
            tok = (
                (
                    qs.get("access_token")
                    or qs.get("token")
                    or qs.get("key")
                    or qs.get("api_key")
                    or [""]
                )[0]
            ).strip()
            code = (qs.get("code") or [""])[0].strip()
            err = (qs.get("error_description") or qs.get("error") or [""])[0].strip()

            if err:
                result_box["error"] = err
                self._send_html(
                    400,
                    f"<h3>Sao Khuê CLI Sign-In Failed</h3><p>{err}</p>",
                )
                done_event.set()
                return True

            if tok or code:
                req_state = (qs.get("state") or [""])[0].strip()
                if req_state and req_state != state:
                    result_box["error"] = "Invalid OAuth state parameter"
                    self._send_html(400, "<h3>Invalid OAuth state parameter</h3>")
                    done_event.set()
                    return True
                result_box["token"] = tok
                result_box["code"] = code
                result_box["refresh_token"] = (qs.get("refresh_token") or [""])[0].strip()
                result_box["user_id"] = (
                    (
                        qs.get("preferred_username")
                        or qs.get("user_id")
                        or qs.get("username")
                        or qs.get("email")
                        or [""]
                    )[0]
                ).strip()
                self._send_html(
                    200,
                    "<h2>Sao Khuê CLI Sign-In Successful</h2>"
                    "<p>You are now signed in. You may close this browser tab and return to Sao Khuê.</p>",
                )
                done_event.set()
                return True
            return False

        def do_GET(self) -> None:  # noqa: N802
            parsed_url = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed_url.query)
            if self._handle_params(qs):
                return

            # If the SSO provider returned the token in the URL hash fragment (#access_token=...),
            # serve a small bridge script that redirects the fragment query to /callback.
            bridge_html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Sao Khuê CLI Sign-In</title></head>
<body style="font-family: sans-serif; padding: 2rem;">
<h3>Completing Sao Khuê CLI sign-in...</h3>
<script>
(function() {
  var hash = window.location.hash ? window.location.hash.substring(1) : "";
  if (hash) {
    window.location.replace("/callback?" + hash);
  } else {
    document.body.innerHTML = "<h3>Waiting for authentication token...</h3>";
  }
})();
</script>
</body></html>"""
            self._send_html(200, bridge_html)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""
            qs = urllib.parse.parse_qs(body)
            if not self._handle_params(qs):
                self._send_html(400, "<h3>Invalid OAuth callback POST payload</h3>")
                done_event.set()

        def _send_html(self, status: int, body: str) -> None:
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    httpd = http.server.HTTPServer(("127.0.0.1", callback_port), _CallbackHandler)
    actual_port = httpd.server_port
    callback_uri = f"http://127.0.0.1:{actual_port}/callback"

    sep = "&" if "?" in resolved_authorize_url else "?"
    if resolved_client_id:
        auth_query: dict[str, str] = {
            "response_type": "code",
            "client_id": resolved_client_id,
            "redirect_uri": callback_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": resolved_scope,
            "prompt": "consent",
        }
        if resolved_resource:
            auth_query["resource"] = resolved_resource
        params = urllib.parse.urlencode(auth_query)
    else:
        params = urllib.parse.urlencode(
            {
                "redirect_uri": callback_uri,
                "callback_url": callback_uri,
                "state": state,
            }
        )
    full_auth_url = f"{resolved_authorize_url}{sep}{params}"

    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()
    try:
        print(f"\nOpening browser for Sao Khuê OAuth2 sign-in:\n  {full_auth_url}\n", flush=True)
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(full_auth_url)
        print(f"Waiting for authentication callback on {callback_uri} ...", flush=True)
        if not done_event.wait(timeout=timeout_secs):
            raise TimeoutError(
                f"Timed out after {int(timeout_secs)}s waiting for browser SSO callback."
            )
    finally:
        httpd.shutdown()
        httpd.server_close()

    if result_box.get("error"):
        raise RuntimeError(f"Browser SSO sign-in failed: {result_box['error']}")

    access_token = str(result_box.get("token") or "").strip()
    id_token = ""
    granted_scope = ""
    refresh_token = str(result_box.get("refresh_token") or "").strip()
    expires_at: int | None = None
    user_id = str(result_box.get("user_id") or "").strip()
    email = ""

    if not access_token and result_box.get("code"):
        code = str(result_box["code"])
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_uri,
            "code_verifier": code_verifier,
        }
        if resolved_client_id:
            form["client_id"] = resolved_client_id
        if resolved_resource:
            form["resource"] = resolved_resource
        req = urllib.request.Request(
            resolved_token_url,
            data=urllib.parse.urlencode(form).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        with _urlopen_resilient(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.strip() else {}
            if isinstance(parsed, dict):
                id_token = str(parsed.get("id_token") or "").strip()
                granted_scope = str(parsed.get("scope") or "").strip()
            access_token, rtok, expires_at, uid, mail = _extract_token_from_response(
                parsed, getattr(resp, "headers", None)
            )
            if rtok:
                refresh_token = rtok
            if uid and not user_id:
                user_id = uid
            if mail:
                email = mail

    if not access_token:
        raise RuntimeError("Browser SSO callback completed without returning an access token.")

    oauth_access_token = access_token
    if not user_id or not email:
        uid_info, mail_info = _probe_oidc_userinfo_sync(
            oauth_access_token, userinfo_url=resolved_userinfo_url, timeout_secs=8.0
        )
        if uid_info and not user_id:
            user_id = uid_info
        if mail_info and not email:
            email = mail_info

    models: list[str] = []
    with contextlib.suppress(Exception):
        models = _query_models_endpoint_sync(norm_url, access_token, timeout_secs=10.0)

    chosen_model = default_model.strip() or (
        _select_valid_saokhue_model("", models) if models else ""
    )
    auth_record: dict[str, Any] = {
        "base_url": norm_url,
        "access_token": access_token,
        "oauth_access_token": oauth_access_token,
        "id_token": id_token,
        "granted_scope": granted_scope or resolved_scope,
        "resource": resolved_resource,
        "refresh_token": refresh_token,
        "token_url": resolved_token_url,
        "userinfo_url": resolved_userinfo_url,
        "revoke_url": oidc["revoke_url"],
        "client_id": resolved_client_id,
        "expires_at": expires_at,
        "user_id": user_id or "sso-user",
        "email": email,
        "auth_method": "sso",
        "default_model": chosen_model,
        "models": models,
    }
    save_saokhue_user_auth(auth_record, environ)
    return auth_record


def start_device_code_flow(
    base_url: str = "",
    *,
    device_auth_url: str = "",
    token_url: str = "",
    userinfo_url: str = "",
    client_id: str = "",
    scope: str = "",
    resource: str = "",
    default_model: str = "",
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Start an OAuth 2.0 Device Authorization Grant (RFC 8628) without blocking.

    Returns a dictionary containing ``device_code``, ``user_code``,
    ``verification_uri``, ``verification_uri_complete``, ``interval``,
    ``expires_in``, and the resolved OIDC endpoints needed by
    :func:`poll_device_code_once`.
    """
    norm_url = _normalize_base_url(base_url)
    oidc = resolve_oidc_endpoints(
        norm_url,
        device_auth_url=device_auth_url,
        token_url=token_url,
        userinfo_url=userinfo_url,
        client_id=client_id,
        scope=scope,
        resource=resource,
        environ=environ,
    )
    resolved_client_id = oidc["client_id"]
    resolved_device_url = oidc["device_auth_url"]
    resolved_token_url = oidc["token_url"]
    resolved_userinfo_url = oidc["userinfo_url"]
    resolved_scope = oidc["scope"]
    resolved_resource = oidc["resource"]

    req_form: dict[str, str] = {"client_id": resolved_client_id}
    if resolved_scope:
        req_form["scope"] = resolved_scope
    if resolved_resource:
        req_form["resource"] = resolved_resource

    req = urllib.request.Request(
        resolved_device_url,
        data=urllib.parse.urlencode(req_form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen_resilient(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            dev_data = json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        err_body = ""
        with contextlib.suppress(Exception):
            err_body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Device authorization request to {resolved_device_url} failed (HTTP {exc.code}): "
            f"{err_body or exc.reason}"
        ) from exc

    if not isinstance(dev_data, dict) or not dev_data.get("device_code"):
        raise RuntimeError(
            f"Invalid device authorization response from {resolved_device_url}: {dev_data!r}"
        )

    device_code = str(dev_data["device_code"]).strip()
    user_code = str(dev_data.get("user_code") or "").strip()
    verification_uri = str(
        dev_data.get("verification_uri") or dev_data.get("verification_url") or ""
    ).strip()
    verification_uri_complete = str(dev_data.get("verification_uri_complete") or "").strip()
    interval = max(1.0, float(dev_data.get("interval") or 5.0))
    expires_in = float(dev_data.get("expires_in") or 300.0)

    return {
        "base_url": norm_url,
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": verification_uri_complete,
        "interval": interval,
        "expires_in": expires_in,
        "token_url": resolved_token_url,
        "userinfo_url": resolved_userinfo_url,
        "revoke_url": oidc["revoke_url"],
        "client_id": resolved_client_id,
        "scope": resolved_scope,
        "resource": resolved_resource,
        "default_model": default_model,
    }


def poll_device_code_once(
    pending: dict[str, Any], environ: dict[str, str] | None = None
) -> dict[str, Any]:
    """Perform one non-blocking poll of a pending Device Code session.

    Returns ``{"status": "pending", "slow_down": bool}`` while awaiting user
    approval, or ``{"status": "authorized", "auth": auth_record}`` once the token
    is issued and saved to ``~/.saokhue-cli/auth.json``.
    """
    norm_url = _normalize_base_url(str(pending.get("base_url") or ""))
    device_code = str(pending.get("device_code") or "").strip()
    resolved_client_id = str(pending.get("client_id") or DEFAULT_OIDC_CLIENT_ID).strip()
    resolved_token_url = str(pending.get("token_url") or DEFAULT_OIDC_TOKEN_URL).strip()
    resolved_userinfo_url = str(pending.get("userinfo_url") or DEFAULT_OIDC_USERINFO_URL).strip()
    resolved_scope = str(pending.get("scope") or DEFAULT_OIDC_SCOPE).strip()
    resolved_resource = str(pending.get("resource") or DEFAULT_OIDC_RESOURCE).strip()
    revoke_url = str(pending.get("revoke_url") or DEFAULT_OIDC_REVOKE_URL).strip()
    default_model = str(pending.get("default_model") or "").strip()

    poll_form: dict[str, str] = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": resolved_client_id,
    }
    if resolved_resource:
        poll_form["resource"] = resolved_resource
    poll_req = urllib.request.Request(
        resolved_token_url,
        data=urllib.parse.urlencode(poll_form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    token_payload: dict[str, Any] = {}
    try:
        with _urlopen_resilient(poll_req, timeout=20.0) as poll_resp:
            raw_poll = poll_resp.read().decode("utf-8", errors="replace")
            parsed_poll = json.loads(raw_poll) if raw_poll.strip() else {}
            if isinstance(parsed_poll, dict) and (
                parsed_poll.get("access_token")
                or parsed_poll.get("token")
                or parsed_poll.get("id_token")
            ):
                token_payload = parsed_poll
    except urllib.error.HTTPError as exc:
        err_raw = ""
        with contextlib.suppress(Exception):
            err_raw = exc.read().decode("utf-8", errors="replace")
        err_code = ""
        err_desc = ""
        with contextlib.suppress(Exception):
            err_json = json.loads(err_raw) if err_raw.strip() else {}
            if isinstance(err_json, dict):
                err_code = str(err_json.get("error") or "").strip()
                err_desc = str(err_json.get("error_description") or "").strip()
        if err_code in ("authorization_pending", "slow_down"):
            return {"status": "pending", "slow_down": err_code == "slow_down"}
        if err_code in ("expired_token", "access_denied"):
            raise RuntimeError(
                f"Device authorization {err_code}: {err_desc or 'request was denied or expired.'}"
            ) from exc
        raise RuntimeError(
            f"Device token exchange failed (HTTP {exc.code}): {err_desc or err_code or err_raw[:200]}"
        ) from exc

    if not token_payload:
        return {"status": "pending", "slow_down": False}

    id_token = str(token_payload.get("id_token") or "").strip()
    granted_scope = str(token_payload.get("scope") or "").strip()
    access_token, refresh_token, expires_at, user_id, email = _extract_token_from_response(
        token_payload
    )
    if not access_token:
        raise RuntimeError("Device token response did not include an access_token.")

    oauth_access_token = access_token
    if not user_id or not email:
        uid_info, mail_info = _probe_oidc_userinfo_sync(
            oauth_access_token, userinfo_url=resolved_userinfo_url, timeout_secs=8.0
        )
        if uid_info and not user_id:
            user_id = uid_info
        if mail_info and not email:
            email = mail_info

    models: list[str] = []
    with contextlib.suppress(Exception):
        models = _query_models_endpoint_sync(norm_url, access_token, timeout_secs=10.0)

    chosen_model = default_model or (_select_valid_saokhue_model("", models) if models else "")
    auth_record: dict[str, Any] = {
        "base_url": norm_url,
        "access_token": access_token,
        "oauth_access_token": oauth_access_token,
        "id_token": id_token,
        "granted_scope": granted_scope or resolved_scope,
        "resource": resolved_resource,
        "refresh_token": refresh_token,
        "token_url": resolved_token_url,
        "userinfo_url": resolved_userinfo_url,
        "revoke_url": revoke_url,
        "client_id": resolved_client_id,
        "expires_at": expires_at,
        "user_id": user_id or "device-user",
        "email": email,
        "auth_method": "device_code",
        "default_model": chosen_model,
        "models": models,
    }
    save_saokhue_user_auth(auth_record, environ)
    return {"status": "authorized", "auth": auth_record}


def login_with_device_code(
    base_url: str,
    *,
    device_auth_url: str = "",
    token_url: str = "",
    userinfo_url: str = "",
    client_id: str = "",
    scope: str = "",
    resource: str = "",
    default_model: str = "",
    open_browser: bool = True,
    timeout_secs: float = 300.0,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Authenticate a user via OAuth 2.0 Device Authorization Grant (RFC 8628)."""
    norm_url = _normalize_base_url(base_url)
    oidc = resolve_oidc_endpoints(
        norm_url,
        device_auth_url=device_auth_url,
        token_url=token_url,
        userinfo_url=userinfo_url,
        client_id=client_id,
        scope=scope,
        resource=resource,
        environ=environ,
    )
    resolved_client_id = oidc["client_id"]
    resolved_device_url = oidc["device_auth_url"]
    resolved_token_url = oidc["token_url"]
    resolved_userinfo_url = oidc["userinfo_url"]
    resolved_scope = oidc["scope"]
    resolved_resource = oidc["resource"]

    req_form: dict[str, str] = {"client_id": resolved_client_id}
    if resolved_scope:
        req_form["scope"] = resolved_scope
    if resolved_resource:
        req_form["resource"] = resolved_resource

    req = urllib.request.Request(
        resolved_device_url,
        data=urllib.parse.urlencode(req_form).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _urlopen_resilient(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            dev_data = json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        err_body = ""
        with contextlib.suppress(Exception):
            err_body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Device authorization request to {resolved_device_url} failed (HTTP {exc.code}): "
            f"{err_body or exc.reason}"
        ) from exc

    if not isinstance(dev_data, dict) or not dev_data.get("device_code"):
        raise RuntimeError(
            f"Invalid device authorization response from {resolved_device_url}: {dev_data!r}"
        )

    device_code = str(dev_data["device_code"]).strip()
    user_code = str(dev_data.get("user_code") or "").strip()
    verification_uri = str(
        dev_data.get("verification_uri") or dev_data.get("verification_url") or ""
    ).strip()
    verification_uri_complete = str(dev_data.get("verification_uri_complete") or "").strip()
    interval = max(1.0, float(dev_data.get("interval") or 5.0))
    expires_in = float(dev_data.get("expires_in") or timeout_secs)
    deadline = time.monotonic() + min(timeout_secs, expires_in)

    target_browser_url = verification_uri_complete or verification_uri
    print("\nTo sign in to Sao Khuê, complete device verification in your browser:", flush=True)
    if verification_uri:
        print(f"  Verification URL: {verification_uri}", flush=True)
    if user_code:
        print(f"  User Code:        {user_code}", flush=True)
    if verification_uri_complete and verification_uri_complete != verification_uri:
        print(f"  Direct Link:      {verification_uri_complete}", flush=True)
    print("", flush=True)

    if open_browser and target_browser_url:
        with contextlib.suppress(Exception):
            webbrowser.open(target_browser_url)

    print("Waiting for device authorization approval...", flush=True)
    token_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        time.sleep(interval)
        poll_form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": resolved_client_id,
        }
        if resolved_resource:
            poll_form["resource"] = resolved_resource
        poll_req = urllib.request.Request(
            resolved_token_url,
            data=urllib.parse.urlencode(poll_form).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with _urlopen_resilient(poll_req, timeout=20.0) as poll_resp:
                raw_poll = poll_resp.read().decode("utf-8", errors="replace")
                parsed_poll = json.loads(raw_poll) if raw_poll.strip() else {}
                if isinstance(parsed_poll, dict) and (
                    parsed_poll.get("access_token")
                    or parsed_poll.get("token")
                    or parsed_poll.get("id_token")
                ):
                    token_payload = parsed_poll
                    break
        except urllib.error.HTTPError as exc:
            err_raw = ""
            with contextlib.suppress(Exception):
                err_raw = exc.read().decode("utf-8", errors="replace")
            err_code = ""
            err_desc = ""
            with contextlib.suppress(Exception):
                err_json = json.loads(err_raw) if err_raw.strip() else {}
                if isinstance(err_json, dict):
                    err_code = str(err_json.get("error") or "").strip()
                    err_desc = str(err_json.get("error_description") or "").strip()
            if err_code == "authorization_pending":
                continue
            if err_code == "slow_down":
                interval += 5.0
                continue
            if err_code in ("expired_token", "access_denied"):
                raise RuntimeError(
                    f"Device authorization {err_code}: {err_desc or 'request was denied or expired.'}"
                ) from exc
            raise RuntimeError(
                f"Device token exchange failed (HTTP {exc.code}): {err_desc or err_code or err_raw[:200]}"
            ) from exc

    if not token_payload:
        raise TimeoutError("Timed out waiting for device code authorization approval.")

    id_token = str(token_payload.get("id_token") or "").strip()
    granted_scope = str(token_payload.get("scope") or "").strip()
    access_token, refresh_token, expires_at, user_id, email = _extract_token_from_response(
        token_payload
    )
    if not access_token:
        raise RuntimeError("Device token response did not include an access_token.")

    oauth_access_token = access_token
    if not user_id or not email:
        uid_info, mail_info = _probe_oidc_userinfo_sync(
            oauth_access_token, userinfo_url=resolved_userinfo_url, timeout_secs=8.0
        )
        if uid_info and not user_id:
            user_id = uid_info
        if mail_info and not email:
            email = mail_info

    models: list[str] = []
    with contextlib.suppress(Exception):
        models = _query_models_endpoint_sync(norm_url, access_token, timeout_secs=10.0)

    chosen_model = default_model.strip() or (
        _select_valid_saokhue_model("", models) if models else ""
    )
    auth_record: dict[str, Any] = {
        "base_url": norm_url,
        "access_token": access_token,
        "oauth_access_token": oauth_access_token,
        "id_token": id_token,
        "granted_scope": granted_scope or resolved_scope,
        "resource": resolved_resource,
        "refresh_token": refresh_token,
        "token_url": resolved_token_url,
        "userinfo_url": resolved_userinfo_url,
        "revoke_url": oidc["revoke_url"],
        "client_id": resolved_client_id,
        "expires_at": expires_at,
        "user_id": user_id or "device-user",
        "email": email,
        "auth_method": "device_code",
        "default_model": chosen_model,
        "models": models,
    }
    save_saokhue_user_auth(auth_record, environ)
    return auth_record
