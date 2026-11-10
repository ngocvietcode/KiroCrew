"""The ``saokhue-cli`` terminal commands: login, logout, status and models.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import sys
import time
import urllib.parse
import urllib.request

from kiro_crew.saokhue_cli.auth import (
    _decode_jwt_claims,
    _normalize_base_url,
    _probe_user_and_models_sync,
    clear_saokhue_user_auth,
    load_saokhue_user_auth,
    login_with_browser_sso,
    login_with_device_code,
    login_with_password,
    login_with_token,
    resolve_saokhue_settings,
)
from kiro_crew.saokhue_cli.base import (
    DEFAULT_OIDC_AUTHORIZE_URL,
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_DEVICE_AUTH_URL,
    DEFAULT_OIDC_REVOKE_URL,
    DEFAULT_SAOKHUE_BASE_URL,
    saokhue_auth_path,
)
from kiro_crew.saokhue_cli.tls import _urlopen_resilient


def _cli_login(args: argparse.Namespace) -> int:
    existing = load_saokhue_user_auth(auto_refresh=False)
    default_url = (
        args.url
        or os.environ.get("SAOKHUE_BASE_URL")
        or str(existing.get("base_url") or "")
        or f"{DEFAULT_SAOKHUE_BASE_URL}/v1"
    )
    use_device_flow = bool(getattr(args, "use_device_flow", False))
    device_auth_url = str(getattr(args, "device_auth_url", "") or "")
    userinfo_url = str(getattr(args, "userinfo_url", "") or "")
    scope = str(getattr(args, "scope", "") or "")
    resource = str(getattr(args, "resource", "") or "")

    base_url = args.url
    if not base_url:
        if use_device_flow or args.method or args.username or args.password or args.token:
            base_url = default_url
        else:
            try:
                entered = input(f"Sao Khuê Server URL [{default_url}]: ").strip()
            except EOFError:
                entered = ""
            base_url = entered or default_url
    base_url = _normalize_base_url(base_url)

    method = (args.method or "").strip().lower()
    if not method:
        if use_device_flow or device_auth_url:
            method = "device"
        elif args.username or args.password:
            method = "password"
        elif args.sso_url or args.client_id:
            method = "sso"
        elif args.token:
            method = "token"
        else:
            print("\nSelect Sao Khuê sign-in method:", flush=True)
            print(
                f"  [1] Browser OAuth2 / SSO (PKCE -> {DEFAULT_OIDC_AUTHORIZE_URL}, client_id={DEFAULT_OIDC_CLIENT_ID})",
                flush=True,
            )
            print(
                f"  [2] Device Code Flow ({DEFAULT_OIDC_DEVICE_AUTH_URL}, client_id={DEFAULT_OIDC_CLIENT_ID})",
                flush=True,
            )
            print("  [3] Username & Password (POST /login on Sao Khuê server)", flush=True)
            print("  [4] Personal Access Token / Virtual Key (paste user token)", flush=True)
            try:
                choice = input("Choice [1]: ").strip() or "1"
            except EOFError:
                choice = "1"
            method = {
                "1": "sso",
                "2": "device",
                "3": "password",
                "4": "token",
            }.get(choice, choice)

    try:
        if method in ("1", "sso", "oauth", "browser", "pkce"):
            record = login_with_browser_sso(
                base_url,
                sso_url=args.sso_url or "",
                token_url=args.token_url or "",
                userinfo_url=userinfo_url,
                client_id=args.client_id or "",
                scope=scope,
                resource=resource,
                callback_port=int(args.callback_port or 0),
                default_model=args.model or "",
                open_browser=not bool(args.no_browser),
            )
        elif method in ("2", "device", "device_code", "device-code"):
            record = login_with_device_code(
                base_url,
                device_auth_url=device_auth_url,
                token_url=args.token_url or "",
                userinfo_url=userinfo_url,
                client_id=args.client_id or "",
                scope=scope,
                resource=resource,
                default_model=args.model or "",
                open_browser=not bool(args.no_browser),
            )
        elif method in ("3", "password", "user", "credentials"):
            username = args.username or ""
            if not username:
                username = input("Username / Email: ").strip()
            password = args.password or ""
            if not password:
                password = getpass.getpass("Password: ")
            record = login_with_password(
                base_url,
                username,
                password,
                login_endpoint=args.login_endpoint or "",
                default_model=args.model or "",
            )
        elif method in ("4", "token", "key", "jwt"):
            user_id = args.username or ""
            if not user_id and sys.stdin.isatty():
                user_id = input("User ID / Email (optional): ").strip()
            tok = args.token or ""
            if not tok:
                tok = getpass.getpass("Personal Access Token / Virtual Key: ").strip()
            record = login_with_token(
                base_url,
                tok,
                user_id=user_id,
                default_model=args.model or "",
            )
        else:
            print(
                f"Unknown login method: {method!r} (choose sso, device, password, or token)",
                file=sys.stderr,
            )
            return 2
    except Exception as exc:
        print(f"Sign-in failed: {exc}", file=sys.stderr)
        return 1

    auth_file = saokhue_auth_path()
    models = record.get("models") or []
    print(
        f"\nSigned in to Sao Khuê ({record.get('base_url')}/v1) as {record.get('user_id') or 'user'}!"
    )
    print(f"  Method:      {record.get('auth_method')}")
    if record.get("client_id"):
        print(f"  Client ID:   {record.get('client_id')}")
    print(f"  Credentials: {auth_file}")
    if models:
        preview = ", ".join(models[:8])
        more = f" (+{len(models) - 8} more)" if len(models) > 8 else ""
        print(f"  Models ({len(models)}):  {preview}{more}")
    if record.get("default_model"):
        print(f"  Default:     {record.get('default_model')}")
    return 0


def _cli_logout() -> int:
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
    path = saokhue_auth_path()
    if clear_saokhue_user_auth():
        print(f"Signed out of Sao Khuê (removed {path}).")
    else:
        print(f"Already signed out (no credential file at {path}).")
    return 0


def _cli_status() -> int:
    auth = load_saokhue_user_auth()
    path = saokhue_auth_path()
    if not auth or not auth.get("access_token"):
        print("Status: NOT SIGNED IN")
        print(f"Run `saokhue-cli login` to sign in (credentials stored at {path}).")
        return 1

    base_url = str(auth.get("base_url") or DEFAULT_SAOKHUE_BASE_URL)
    user_id = str(auth.get("user_id") or "user")
    email = str(auth.get("email") or "")
    method = str(auth.get("auth_method") or "unknown")
    client_id = str(auth.get("client_id") or "")
    expires_at = auth.get("expires_at")
    access_tok = str(auth.get("access_token") or "")
    id_tok = str(auth.get("id_token") or "")
    granted_scope = str(auth.get("granted_scope") or "")

    access_claims = _decode_jwt_claims(access_tok)
    id_claims = _decode_jwt_claims(id_tok) if id_tok else {}
    tok_kind = "JWT" if access_claims else f"Opaque ({len(access_tok)} chars)"

    print("Status: SIGNED IN")
    print(f"  User:        {user_id}" + (f" <{email}>" if email else ""))
    print(f"  Server URL:  {base_url}/v1")
    print(f"  Auth Method: {method}" + (f" (client_id={client_id})" if client_id else ""))
    print(f"  Token Type:  {tok_kind}" + (" + id_token (JWT)" if id_claims else ""))
    scope_val = str(
        access_claims.get("scope") or id_claims.get("scope") or granted_scope or ""
    ).strip()
    if scope_val:
        print(f"  Scope:       {scope_val}")
    resource_val = str(auth.get("resource") or "").strip()
    if resource_val:
        print(f"  Resource:    {resource_val}")
    aud_val = access_claims.get("aud") or id_claims.get("aud")
    if aud_val:
        print(f"  Audience:    {aud_val}")
    print(f"  Credentials: {path}")
    if expires_at:
        rem = int(float(expires_at) - time.time())
        print(f"  Token TTL:   {rem}s remaining" if rem > 0 else "  Token TTL:   expired")
    try:
        models, _, _ = _probe_user_and_models_sync(
            base_url,
            access_tok,
            timeout_secs=8.0,
            userinfo_url=str(auth.get("userinfo_url") or ""),
        )
        print(f"  Models:      {len(models)} available ({', '.join(models[:6])})")
    except Exception as exc:
        print(f"  Live check:  could not query /v1/models ({exc})")
    return 0


def _cli_models() -> int:
    base_url, token, default_model, _ = resolve_saokhue_settings()
    if not token:
        print("Not signed in. Run `saokhue-cli login` first.", file=sys.stderr)
        return 1
    try:
        models, _, _ = _probe_user_and_models_sync(base_url, token, timeout_secs=10.0)
    except Exception as exc:
        print(f"Failed to list models from {base_url}: {exc}", file=sys.stderr)
        return 1
    if not models:
        print(f"No models returned by {base_url}/v1/models.")
        return 0
    for m in models:
        marker = " * (default)" if m == default_model else ""
        print(f"{m}{marker}")
    return 0
