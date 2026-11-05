"""Built-in ACP server backed by Sao Khuê / OpenAI-compatible Chat Completions API.

Runs as a subprocess over stdio JSON-RPC 2.0 (``python -m kiro_crew.acp.saokhue_server``)
so Kiro Crew can connect directly to Sao Khuê Gateway (or any OpenAI-compatible API)
on any platform, including Windows without external CLI binaries or OS sandboxes.

Every tool execution (built-in workspace tools and MCP tools alike) emits an ACP
``tool_call`` notification with first-party ``_meta.kiro`` identity and awaits a
``session/request_permission`` decision from ``AcpClient`` before running, keeping
Kiro Crew's PreToolUse governance, deny rules, sensitive-path checks, SEL audit
logging, and Dashboard approval cards in circuit.
"""

from __future__ import annotations

import os
import sys

# When invoked directly as ``python .../kiro_crew/acp/saokhue_server.py``, Python puts
# ``.../kiro_crew/acp`` on ``sys.path[0]``, where sibling ``types.py`` would shadow
# stdlib ``types``. Replace that directory entry with the package root (``src/``) before
# importing any stdlib module that imports ``types``.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _SCRIPT_DIR]
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

import argparse
import asyncio
import base64
import contextlib
import getpass
import hashlib
import http.server
import json
import logging
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import aiohttp  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - stdlib urllib fallback used when aiohttp is absent
    aiohttp = None  # type: ignore[assignment]

from kiro_crew.config.paths import config_dir, kiro_agents_dir, kiro_sessions_dir
from kiro_crew.remote_session import workspace_tools

# ACP JSON-RPC method and update constants (defined inline so running this module
# as a standalone script never imports ``kiro_crew.acp.__init__``).
METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SESSION_LOAD = "session/load"
METHOD_SESSION_RESUME = "session/resume"
METHOD_SET_MODEL = "session/set_model"
METHOD_SET_MODE = "session/set_mode"
METHOD_SET_CONFIG_OPTION = "session/set_config_option"
METHOD_PROMPT = "session/prompt"
METHOD_CANCEL = "session/cancel"
METHOD_REQUEST_PERMISSION = "session/request_permission"
METHOD_SESSION_UPDATE = "session/update"
METHOD_METADATA = "_kiro.dev/metadata"
METHOD_SESSION_TERMINATE = "_kiro.dev/session/terminate"
METHOD_SESSION_CLOSE = "session/close"
METHOD_AGENT_SWITCHED = "_kiro.dev/agent/switched"
METHOD_MCP_SERVER_INITIALIZED = "_kiro.dev/mcp/server_initialized"
METHOD_MCP_SERVER_INIT_FAILURE = "_kiro.dev/mcp/server_init_failure"
MODEL_CONFIG_ID = "model"
OPTION_ALLOW_ONCE = "allow_once"
OPTION_ALLOW_ALWAYS = "allow_always"
STOP_REASON_CANCELLED = "cancelled"
STOP_REASON_END_TURN = "end_turn"
UPDATE_AGENT_MESSAGE_CHUNK = "agent_message_chunk"
UPDATE_AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
UPDATE_TOOL_CALL = "tool_call"
UPDATE_TOOL_CALL_UPDATE = "tool_call_update"
UPDATE_USAGE = "usage_update"

logger = logging.getLogger("kiro_crew.acp.saokhue_server")

PROTOCOL_VERSION = "2025-08-22"
SERVER_NAME = "saokhue-cli"
SERVER_VERSION = "1.0.0"

DEFAULT_SAOKHUE_BASE_URL = "https://portal.aws.rpa.vpbank.dev/api/ai-llm"
DEFAULT_OIDC_ISSUER = "https://portal.aws.rpa.vpbank.dev/api"
DEFAULT_OIDC_AUTHORIZE_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/authorize"
DEFAULT_OIDC_DEVICE_AUTH_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/device/auth"
DEFAULT_OIDC_TOKEN_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/token"
DEFAULT_OIDC_USERINFO_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/me"
DEFAULT_OIDC_REVOKE_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/revoke"
DEFAULT_OIDC_END_SESSION_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/end-session"
DEFAULT_OIDC_REGISTER_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/register"
DEFAULT_OIDC_CLIENT_ID = "app:test-crew"
DEFAULT_OIDC_SCOPE = "openid offline_access profile email api"
DEFAULT_OIDC_RESOURCE = "https://portal.aws.rpa.vpbank.dev/api/"
DEFAULT_FALLBACK_MODEL = "saokhue-default"
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
MAX_AGENT_TURNS = 40
MCP_CONNECT_TIMEOUT_SECS = 15.0
MCP_CALL_TIMEOUT_SECS = 120.0

#: Built-in tool definitions exposed to the OpenAI-compatible Chat Completions endpoint.
BUILTIN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fs_read",
            "description": "Read the contents of a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative to workspace or absolute).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-based starting line number (optional).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (optional).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_write",
            "description": "Write or overwrite a file with the given text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to create or overwrite.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string occurrence in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Exact existing text to replace.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (defaults to workspace root).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern such as '**/*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search from (optional).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regular expression pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path to search in (optional).",
                    },
                    "include": {
                        "type": "string",
                        "description": "Filename glob filter such as '*.py' (optional).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command line to run.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the command (optional).",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (optional, default 120).",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

BUILTIN_TOOL_KINDS: dict[str, str] = {
    "fs_read": "read",
    "read_file": "read",
    "list_directory": "read",
    "glob": "search",
    "grep": "search",
    "fs_write": "edit",
    "write_file": "edit",
    "edit_file": "edit",
    "execute_bash": "execute",
    "run_command": "execute",
}


def _is_ssl_cert_error(exc: BaseException) -> bool:
    """Return True if *exc* or its cause is a TLS certificate verification failure."""
    cur: BaseException | None = exc
    visited: set[int] = set()
    while cur is not None and id(cur) not in visited:
        visited.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        msg = str(cur).lower()
        if (
            "certificate_verify_failed" in msg
            or "unable to get local issuer certificate" in msg
            or "unable to get issuer certificate" in msg
            or "self-signed certificate" in msg
            or "self signed certificate" in msg
        ):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


_SYSTEM_CA_BUNDLE_CACHE: str | None = None
_SYSTEM_SSL_CONTEXT_CACHE: ssl.SSLContext | None = None

_CA_BUNDLE_ENV_VARS: tuple[str, ...] = (
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "AWS_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def ensure_system_ca_bundle(environ: MutableMapping[str, str] | None = None) -> str | None:
    """Export OS/Windows Global CA certificates (`ROOT` + `CA` + certifi) to a PEM bundle and set CA env vars.

    Allows Python (`urllib`, `aiohttp`, `requests`), Node.js (`kiro-cli` KAS engine via
    ``NODE_EXTRA_CA_CERTS``), OpenSSL (`kiro-cli` via ``SSL_CERT_FILE``), and AWS SDK
    (``AWS_CA_BUNDLE``) to automatically trust corporate/global Root and Intermediate CAs
    installed in the machine's certificate store.
    """
    global _SYSTEM_CA_BUNDLE_CACHE
    target_env = os.environ if environ is None else environ

    if _SYSTEM_CA_BUNDLE_CACHE and os.path.isfile(_SYSTEM_CA_BUNDLE_CACHE):
        for var in _CA_BUNDLE_ENV_VARS:
            if not target_env.get(var):
                target_env[var] = _SYSTEM_CA_BUNDLE_CACHE
        return _SYSTEM_CA_BUNDLE_CACHE

    pem_blocks: list[str] = []
    seen_blocks: set[str] = set()

    def _add_pem_text(text: str) -> None:
        marker_begin = "-----BEGIN CERTIFICATE-----"
        marker_end = "-----END CERTIFICATE-----"
        pos = 0
        while True:
            start = text.find(marker_begin, pos)
            if start == -1:
                break
            end = text.find(marker_end, start)
            if end == -1:
                break
            block = text[start : end + len(marker_end)].strip()
            if block and block not in seen_blocks:
                seen_blocks.add(block)
                pem_blocks.append(block)
            pos = end + len(marker_end)

    # 1. Existing CA bundle from certifi or OpenSSL default verify paths
    with contextlib.suppress(Exception):
        import certifi  # type: ignore[import-not-found]

        cafile = certifi.where()
        if cafile and os.path.isfile(cafile):
            _add_pem_text(Path(cafile).read_text(encoding="utf-8", errors="ignore"))

    with contextlib.suppress(Exception):
        default_paths = ssl.get_default_verify_paths()
        for candidate in (default_paths.cafile, os.environ.get("SSL_CERT_FILE")):
            if candidate and os.path.isfile(candidate):
                _add_pem_text(Path(candidate).read_text(encoding="utf-8", errors="ignore"))

    # 2. Windows Certificate Store (ROOT and Intermediate CA stores)
    if hasattr(ssl, "enum_certificates"):
        for store_name in ("ROOT", "CA"):
            with contextlib.suppress(Exception):
                for cert_bytes, encoding_type, trust in ssl.enum_certificates(store_name):
                    if encoding_type != "x509_asn" or trust is False:
                        continue
                    with contextlib.suppress(Exception):
                        pem_str = ssl.DER_cert_to_PEM_cert(cert_bytes)
                        _add_pem_text(pem_str)

    # 3. Standard POSIX system CA bundles
    for posix_ca in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
    ):
        if os.path.isfile(posix_ca):
            with contextlib.suppress(Exception):
                _add_pem_text(Path(posix_ca).read_text(encoding="utf-8", errors="ignore"))
            break

    if not pem_blocks:
        return None

    try:
        bundle_dir = saokhue_cli_home()
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / "system-ca-bundle.pem"
        combined = "\n".join(pem_blocks) + "\n"
        existing = ""
        if bundle_path.is_file():
            with contextlib.suppress(OSError):
                existing = bundle_path.read_text(encoding="utf-8", errors="ignore")
        if existing != combined:
            tmp_bundle = bundle_path.with_suffix(".pem.tmp")
            tmp_bundle.write_text(combined, encoding="utf-8")
            tmp_bundle.replace(bundle_path)
        _SYSTEM_CA_BUNDLE_CACHE = str(bundle_path)
        for var in _CA_BUNDLE_ENV_VARS:
            if not target_env.get(var):
                target_env[var] = _SYSTEM_CA_BUNDLE_CACHE
        return _SYSTEM_CA_BUNDLE_CACHE
    except Exception:
        logger.debug("Could not write system CA bundle file", exc_info=True)
        return None


def _build_system_ssl_context() -> ssl.SSLContext:
    """Create an ``ssl.SSLContext`` trusted with the OS/Windows Global CA store and certifi."""
    global _SYSTEM_SSL_CONTEXT_CACHE
    if _SYSTEM_SSL_CONTEXT_CACHE is not None:
        return _SYSTEM_SSL_CONTEXT_CACHE

    ctx = ssl.create_default_context()
    with contextlib.suppress(Exception):
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
    bundle_path = ensure_system_ca_bundle()
    if bundle_path and os.path.isfile(bundle_path):
        with contextlib.suppress(Exception):
            ctx.load_verify_locations(cafile=bundle_path)
    _SYSTEM_SSL_CONTEXT_CACHE = ctx
    return ctx


def _urlopen_resilient(
    req: urllib.request.Request | str, timeout: float = 15.0
) -> Any:
    """Open *req* via ``urllib.request.urlopen`` using the machine's Global CA store, falling back to unverified SSL if needed."""
    if os.environ.get("SAOKHUE_SSL_VERIFY", "").strip().lower() in ("0", "false", "no"):
        return urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()
        )
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_build_system_ssl_context())
    except Exception as exc:
        if _is_ssl_cert_error(exc):
            logger.debug("Retrying %s with unverified SSL context after %s", req, exc)
            return urllib.request.urlopen(
                req, timeout=timeout, context=ssl._create_unverified_context()
            )
        raise


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
        if req and req in real_models:
            return req
        if req:
            req_lower = req.lower()
            for m in real_models:
                m_lower = m.lower()
                if (
                    m_lower == req_lower
                    or m_lower.endswith(f"/{req_lower}")
                    or req_lower.endswith(f"/{m_lower}")
                ):
                    return m
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


def saokhue_cli_home(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user ``saokhue-cli`` home directory (``~/.saokhue-cli`` or ``$SAOKHUE_CLI_HOME``)."""
    env = os.environ if environ is None else environ
    override = (env.get("SAOKHUE_CLI_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".saokhue-cli"


def saokhue_auth_path(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user credential file ``~/.saokhue-cli/auth.json``."""
    return saokhue_cli_home(environ) / "auth.json"


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
        "token_url": (
            token_url or env.get("SAOKHUE_OIDC_TOKEN_URL") or default_token
        ).strip(),
        "userinfo_url": (
            userinfo_url or env.get("SAOKHUE_OIDC_USERINFO_URL") or default_userinfo
        ).strip(),
        "revoke_url": (
            revoke_url or env.get("SAOKHUE_OIDC_REVOKE_URL") or default_revoke
        ).strip(),
        "client_id": (
            client_id or env.get("SAOKHUE_CLIENT_ID") or DEFAULT_OIDC_CLIENT_ID
        ).strip(),
        "scope": (
            scope or env.get("SAOKHUE_OIDC_SCOPE") or DEFAULT_OIDC_SCOPE
        ).strip(),
        "resource": (
            resource or env.get("SAOKHUE_OIDC_RESOURCE") or default_resource
        ).strip(),
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

    default_model = (
        (env.get("SAOKHUE_MODEL") or "").strip()
        or str(user_auth.get("default_model") or "").strip()
    )
    try:
        timeout_secs = float(
            env.get("SAOKHUE_TIMEOUT") or user_auth.get("timeout") or 300.0
        )
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


def _query_models_endpoint_sync(
    base_url: str, token: str, timeout_secs: float = 10.0
) -> list[str]:
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
                    info = udata.get("user_info") if isinstance(udata.get("user_info"), dict) else udata
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
            json.dumps(
                {"username": username, "password": password, "user_id": username}
            ).encode("utf-8"),
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

    chosen_model = default_model or (
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


class StdioMcpClient:
    """Minimal JSON-RPC 2.0 stdio client for a single MCP server subprocess."""

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str], cwd: str):
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.tools: list[dict[str, Any]] = []

    async def start(self) -> None:
        merged_env = dict(os.environ)
        merged_env.update(self.env)
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=merged_env,
            cwd=self.cwd if os.path.isdir(self.cwd) else None,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
            timeout=MCP_CONNECT_TIMEOUT_SECS,
        )
        await self._notify("notifications/initialized", {})
        resp = await self._request("tools/list", {}, timeout=MCP_CONNECT_TIMEOUT_SECS)
        raw_tools = resp.get("tools") if isinstance(resp, dict) else []
        self.tools = [t for t in raw_tools if isinstance(t, dict) and t.get("name")]

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                req_id = msg.get("id")
                if isinstance(req_id, int) and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
                elif req_id is not None and "method" in msg:
                    # Server->client request on MCP stdio: reply with empty result
                    await self._send_raw({"jsonrpc": "2.0", "id": req_id, "result": {}})
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("MCP stdio reader for %s ended", self.name, exc_info=True)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError(f"MCP server {self.name!r} closed pipe"))
            self._pending.clear()

    async def _send_raw(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"MCP server {self.name!r} is not running")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send_raw({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float = MCP_CALL_TIMEOUT_SECS
    ) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut
        await self._send_raw({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)
        if "error" in msg and msg["error"] is not None:
            err = msg["error"]
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"MCP {self.name}.{method} error: {err_msg}")
        res = msg.get("result")
        return res if isinstance(res, dict) else {}

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Invoke ``tools/call`` and return ``(text_output, is_error)``."""
        res = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=MCP_CALL_TIMEOUT_SECS,
        )
        is_error = bool(res.get("isError", False))
        content = res.get("content")
        parts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    else:
                        parts.append(json.dumps(block, ensure_ascii=False))
        elif isinstance(content, str):
            parts.append(content)
        text = "\n".join(parts) if parts else json.dumps(res, ensure_ascii=False)
        return text, is_error

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc is not None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


@dataclass
class SaokhueSession:
    session_id: str
    cwd: str
    agent_name: str
    system_prompt: str
    model: str
    available_models: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    mcp_clients: dict[str, StdioMcpClient] = field(default_factory=dict)
    mcp_tool_map: dict[str, tuple[str, str]] = field(default_factory=dict)
    disabled_tools: set[tuple[str, str]] = field(default_factory=set)
    zero_tools: bool = False
    active_turn_task: asyncio.Task[None] | None = None
    cancelled: bool = False
    cumulative_cost_usd: float = 0.0


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token estimate (~4 chars per token) when the upstream stream omits usage."""
    total_chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total_chars += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total_chars += len(part["text"])
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            total_chars += len(json.dumps(tcs, ensure_ascii=False))
    return max(1, total_chars // 4)


def _extract_usage_fields(
    chunk_or_data: dict[str, Any],
    prev_in: int = 0,
    prev_out: int = 0,
    prev_cache_read: int = 0,
    prev_cache_write: int = 0,
    prev_cost: float = 0.0,
) -> tuple[int, int, int, int, float]:
    """Extract ``(input_tokens, output_tokens, cache_read, cache_write, cost_usd)`` from an OpenAI/Sao Khuê payload."""
    in_tok = prev_in
    out_tok = prev_out
    cache_read = prev_cache_read
    cache_write = prev_cache_write
    cost_usd = prev_cost

    usage = chunk_or_data.get("usage")
    if not isinstance(usage, dict):
        x_meta = chunk_or_data.get("x_saokhue") or chunk_or_data.get("_hidden_params")
        if isinstance(x_meta, dict) and isinstance(x_meta.get("usage"), dict):
            usage = x_meta["usage"]

    if isinstance(usage, dict):
        raw_in = (
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokens")
            or usage.get("inputTokens")
        )
        raw_out = (
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completionTokens")
            or usage.get("outputTokens")
        )
        if isinstance(raw_in, (int, float)) and int(raw_in) > 0:
            in_tok = int(raw_in)
        if isinstance(raw_out, (int, float)) and int(raw_out) > 0:
            out_tok = int(raw_out)
        if in_tok == 0 and out_tok == 0:
            tot = usage.get("total_tokens") or usage.get("totalTokens")
            if isinstance(tot, (int, float)) and int(tot) > 0:
                in_tok = int(tot)

        p_details = usage.get("prompt_tokens_details") or {}
        if isinstance(p_details, dict) and isinstance(p_details.get("cached_tokens"), (int, float)):
            cache_read = int(p_details["cached_tokens"])
        elif isinstance(usage.get("cache_read_input_tokens"), (int, float)):
            cache_read = int(usage["cache_read_input_tokens"])

        if isinstance(usage.get("cache_creation_input_tokens"), (int, float)):
            cache_write = int(usage["cache_creation_input_tokens"])

        for ck in ("cost", "response_cost", "total_cost"):
            cval = usage.get(ck)
            if isinstance(cval, (int, float)) and float(cval) > 0:
                cost_usd = float(cval)
                break

    for top_ck in ("_response_cost", "response_cost"):
        tcval = chunk_or_data.get(top_ck)
        if isinstance(tcval, (int, float)) and float(tcval) > 0:
            cost_usd = float(tcval)
            break
    hidden = chunk_or_data.get("_hidden_params")
    if isinstance(hidden, dict):
        hcval = hidden.get("response_cost")
        if isinstance(hcval, (int, float)) and float(hcval) > 0:
            cost_usd = float(hcval)

    return in_tok, out_tok, cache_read, cache_write, cost_usd


class SaokhueAcpServer:
    """JSON-RPC 2.0 ACP server communicating over stdin/stdout and calling Sao Khuê."""

    def __init__(self, *, agent_name: str = "kirocrew", initial_model: str = "") -> None:
        self._default_agent = agent_name or "kirocrew"
        self._initial_model = initial_model
        self._sessions: dict[str, SaokhueSession] = {}
        self._next_server_req_id = 10_000
        self._pending_client_replies: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()

    async def send_message(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        async with self._write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    async def send_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.send_message({"jsonrpc": "2.0", "method": method, "params": params})

    async def send_server_request(
        self, method: str, params: dict[str, Any], *, timeout: float = 3600.0
    ) -> dict[str, Any]:
        req_id = self._next_server_req_id
        self._next_server_req_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_client_replies[req_id] = fut
        await self.send_message(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_client_replies.pop(req_id, None)
        res = msg.get("result")
        return res if isinstance(res, dict) else {}

    def _load_agent_spec(self, agent_name: str, cwd: str) -> dict[str, Any]:
        """Load ``<config_dir>/agents/<agent_name>.json``, ``~/.kiro/agents/<agent_name>.json``, or member snapshot."""
        with contextlib.suppress(Exception):
            from kiro_crew.acp import session_mcp as _session_mcp

            snap = _session_mcp.agent_spec_snapshot(agent_name, cwd)
            if isinstance(snap, dict) and snap:
                return snap
            worker = _session_mcp._crewmate_worker_spec(agent_name)
            if isinstance(worker, dict) and worker:
                return worker
        candidates = [
            config_dir() / "agents" / f"{agent_name}.json",
            Path(cwd) / ".kiro" / "agents" / f"{agent_name}.json",
            kiro_agents_dir() / f"{agent_name}.json",
        ]
        for path in candidates:
            try:
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
            except Exception:
                logger.debug("Could not read agent spec at %s", path, exc_info=True)
        return {}

    def _resolve_system_prompt(self, spec: dict[str, Any], cwd: str) -> str:
        prompt = spec.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return "You are Kiro Crew, a helpful personal AI assistant and coding agent."
        prompt = prompt.strip()
        if prompt.startswith("file://"):
            rel_or_abs = prompt[len("file://") :]
            p = Path(rel_or_abs)
            if not p.is_absolute():
                p = kiro_agents_dir() / rel_or_abs
            try:
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                logger.debug("Could not resolve file:// prompt %s", p, exc_info=True)
        return prompt

    async def _fetch_saokhue_models(self, base_url: str, api_key: str) -> list[str]:
        """Query ``GET {base_url}/v1/models`` for available model IDs, with SSL fallback and cached auth fallback."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base_url}/v1/models"
        try:
            if aiohttp is not None:
                timeout = aiohttp.ClientTimeout(total=8.0)
                ssl_opt: Any = (
                    False
                    if os.environ.get("SAOKHUE_SSL_VERIFY", "").strip().lower()
                    in ("0", "false", "no")
                    else None
                )
                data = None
                for attempt_ssl in ([ssl_opt] if ssl_opt is False else [None, False]):
                    try:
                        async with aiohttp.ClientSession(timeout=timeout) as http:
                            req_kwargs: dict[str, Any] = {"headers": headers}
                            if attempt_ssl is False:
                                req_kwargs["ssl"] = False
                            async with http.get(url, **req_kwargs) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                else:
                                    data = None
                        break
                    except Exception as exc:
                        if attempt_ssl is None and _is_ssl_cert_error(exc):
                            logger.debug("Retrying GET %s with ssl=False after %s", url, exc)
                            continue
                        raise
            else:
                def _sync_get() -> Any:
                    req = urllib.request.Request(url, headers=headers, method="GET")
                    with _urlopen_resilient(req, timeout=8.0) as resp:
                        if resp.status == 200:
                            return json.loads(resp.read().decode("utf-8", errors="replace"))
                    return None

                data = await asyncio.to_thread(_sync_get)
            items = data.get("data") if isinstance(data, dict) else None
            if isinstance(items, list):
                models = [
                    str(item["id"]).strip()
                    for item in items
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ]
                if models:
                    return models
        except Exception:
            logger.debug("Could not fetch /v1/models from %s", url, exc_info=True)
        user_auth = load_saokhue_user_auth(auto_refresh=False)
        cached = user_auth.get("models")
        if isinstance(cached, list):
            cached_models = [
                str(m).strip()
                for m in cached
                if isinstance(m, str)
                and m.strip()
                and m.strip() not in ("auto", DEFAULT_FALLBACK_MODEL)
            ]
            if cached_models:
                return cached_models
        return []

    def _normalize_mcp_entries(
        self, spec: dict[str, Any], wire_servers: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], set[tuple[str, str]], bool]:
        """Combine spec MCP servers, member session MCP servers, and session/new wire MCP servers."""
        tools_allowlist = spec.get("tools")
        zero_tools = isinstance(tools_allowlist, list) and len(tools_allowlist) == 0
        allowed_servers: set[str] | None = None
        if isinstance(tools_allowlist, list):
            allowed_servers = set()
            for entry in tools_allowlist:
                if isinstance(entry, str) and entry.startswith("@"):
                    srv = entry[1:].split("/", 1)[0].strip()
                    if srv:
                        allowed_servers.add(srv)

        by_name: dict[str, dict[str, Any]] = {}
        disabled: set[tuple[str, str]] = set()
        spec_servers = spec.get("mcpServers")
        if isinstance(spec_servers, dict) and not zero_tools:
            for name, cfg in spec_servers.items():
                if not isinstance(cfg, dict) or cfg.get("disabled") is True:
                    continue
                if allowed_servers is not None and name not in allowed_servers:
                    continue
                for dt in cfg.get("disabledTools") or ():
                    if isinstance(dt, str) and dt:
                        disabled.add((name, dt))
                command = cfg.get("command")
                if isinstance(command, str) and command.strip():
                    raw_env = cfg.get("env")
                    env_map = (
                        {str(k): str(v) for k, v in raw_env.items() if v is not None}
                        if isinstance(raw_env, dict)
                        else {}
                    )
                    by_name[name] = {
                        "name": name,
                        "command": command.strip(),
                        "args": [str(a) for a in (cfg.get("args") or ())],
                        "env": env_map,
                        "from_wire": False,
                    }

        extra_wire = list(wire_servers)
        if not zero_tools:
            with contextlib.suppress(Exception):
                from kiro_crew.acp import session_mcp as _session_mcp

                session_key = (os.environ.get("KIROCREW_SESSION_KEY") or "").strip()
                if session_key:
                    for extra_srv in (
                        _session_mcp.member_dispatch_session_server(session_key),
                        _session_mcp.member_panel_session_server(session_key),
                    ):
                        if isinstance(extra_srv, dict) and extra_srv.get("name"):
                            extra_wire.append(extra_srv)

        for item in extra_wire:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            command = str(item.get("command") or "").strip()
            if not name or not command:
                continue
            env_map: dict[str, str] = {}
            raw_env = item.get("env")
            if isinstance(raw_env, list):
                for pair in raw_env:
                    if isinstance(pair, dict) and pair.get("name"):
                        env_map[str(pair["name"])] = str(pair.get("value") or "")
            elif isinstance(raw_env, dict):
                env_map = {str(k): str(v) for k, v in raw_env.items() if v is not None}
            by_name[name] = {
                "name": name,
                "command": command,
                "args": [str(a) for a in (item.get("args") or ())],
                "env": env_map,
                "from_wire": item in wire_servers,
            }

        return list(by_name.values()), disabled, zero_tools

    async def _mount_mcp_servers(
        self,
        session: SaokhueSession,
        entries: list[dict[str, Any]],
    ) -> None:
        """Start stdio MCP servers concurrently and populate ``session.mcp_clients``."""

        async def _start_one(entry: dict[str, Any]) -> None:
            name = entry["name"]
            client = StdioMcpClient(
                name=name,
                command=entry["command"],
                args=entry["args"],
                env=entry["env"],
                cwd=session.cwd,
            )
            try:
                await client.start()
                session.mcp_clients[name] = client
                for tool in client.tools:
                    t_name = str(tool.get("name") or "").strip()
                    if not t_name or (name, t_name) in session.disabled_tools:
                        continue
                    fn_name = f"mcp__{name}__{t_name}"
                    session.mcp_tool_map[fn_name] = (name, t_name)
                if entry.get("from_wire"):
                    await self.send_notification(
                        METHOD_MCP_SERVER_INITIALIZED,
                        {"sessionId": session.session_id, "serverName": name},
                    )
            except Exception as exc:
                await client.close()
                if entry.get("from_wire"):
                    await self.send_notification(
                        METHOD_MCP_SERVER_INIT_FAILURE,
                        {
                            "sessionId": session.session_id,
                            "serverName": name,
                            "error": str(exc)[:200],
                        },
                    )

        if entries:
            await asyncio.gather(*(_start_one(e) for e in entries), return_exceptions=True)

    def _session_response_payload(self, session: SaokhueSession) -> dict[str, Any]:
        models_list = session.available_models or [session.model]
        if session.model and session.model not in models_list:
            models_list = [session.model, *models_list]
        return {
            "sessionId": session.session_id,
            "modes": {
                "currentModeId": session.agent_name,
                "availableModes": [
                    {
                        "id": session.agent_name,
                        "name": session.agent_name,
                        "description": f"Kiro Crew agent ({session.agent_name})",
                    }
                ],
            },
            "models": {
                "currentModelId": session.model,
                "availableModels": [{"modelId": m, "name": m} for m in models_list],
            },
            "configOptions": [
                {
                    "id": MODEL_CONFIG_ID,
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": session.model,
                    "options": [{"value": m, "name": m} for m in models_list],
                }
            ],
        }

    def _transcript_path(self, session_id: str) -> Path:
        return config_dir() / "saokhue_sessions" / f"{session_id}.json"

    def _save_transcript(self, session: SaokhueSession) -> None:
        try:
            path = self._transcript_path(session.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessionId": session.session_id,
                "agent": session.agent_name,
                "model": session.model,
                "cwd": session.cwd,
                "messages": session.messages,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.debug("Could not persist transcript for %s", session.session_id, exc_info=True)

    def _load_transcript_messages(self, session_id: str) -> list[dict[str, Any]]:
        try:
            path = self._transcript_path(session_id)
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                msgs = data.get("messages") if isinstance(data, dict) else None
                if isinstance(msgs, list):
                    return [m for m in msgs if isinstance(m, dict)]
        except Exception:
            logger.debug("Could not read transcript for %s", session_id, exc_info=True)
        return []

    async def handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        req_proto = params.get("protocolVersion") or PROTOCOL_VERSION
        return {
            "protocolVersion": req_proto,
            "agentInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": True},
                "mcpCapabilities": {"http": True, "sse": True},
            },
        }

    async def _create_or_load_session(
        self, params: dict[str, Any], *, session_id: str | None = None
    ) -> dict[str, Any]:
        cwd = str(params.get("cwd") or os.getcwd())
        sid = session_id or uuid.uuid4().hex
        base_url, api_key, env_model, _ = resolve_saokhue_settings()
        models = await self._fetch_saokhue_models(base_url, api_key)

        chosen_model = _select_valid_saokhue_model(self._initial_model, models, env_model)
        if chosen_model not in models:
            models = [chosen_model, *models]

        spec = self._load_agent_spec(self._default_agent, cwd)
        system_prompt = self._resolve_system_prompt(spec, cwd)
        wire_servers = params.get("mcpServers") if isinstance(params.get("mcpServers"), list) else []
        mcp_entries, disabled_tools, zero_tools = self._normalize_mcp_entries(spec, wire_servers)

        restored_messages = self._load_transcript_messages(sid) if session_id else []
        session = SaokhueSession(
            session_id=sid,
            cwd=cwd,
            agent_name=self._default_agent,
            system_prompt=system_prompt,
            model=chosen_model,
            available_models=models,
            messages=restored_messages,
            disabled_tools=disabled_tools,
            zero_tools=zero_tools,
        )
        self._sessions[sid] = session
        await self._mount_mcp_servers(session, mcp_entries)
        self._save_transcript(session)
        return self._session_response_payload(session)

    async def handle_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("sessionId") or "")
        mode_id = str(params.get("modeId") or self._default_agent)
        session = self._sessions.get(sid)
        if session is not None:
            session.agent_name = mode_id
            spec = self._load_agent_spec(mode_id, session.cwd)
            if spec:
                session.system_prompt = self._resolve_system_prompt(spec, session.cwd)
        await self.send_notification(
            METHOD_AGENT_SWITCHED,
            {"sessionId": sid, "agentName": mode_id, "modeId": mode_id},
        )
        return {}

    async def handle_set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("sessionId") or "")
        model_id = str(params.get("modelId") or "").strip()
        session = self._sessions.get(sid)
        if session is not None and model_id and model_id != "auto":
            _, _, env_model, _ = resolve_saokhue_settings()
            resolved_model = _select_valid_saokhue_model(
                model_id, session.available_models, env_model
            )
            session.model = resolved_model
            if resolved_model not in session.available_models:
                session.available_models.insert(0, resolved_model)
            _persist_preferred_model(resolved_model)
            self._save_transcript(session)
        return {}

    async def handle_set_config_option(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("sessionId") or "")
        config_id = str(params.get("configId") or "")
        value = str(params.get("value") or "").strip()
        session = self._sessions.get(sid)
        if session is not None and config_id == MODEL_CONFIG_ID and value and value != "auto":
            _, _, env_model, _ = resolve_saokhue_settings()
            resolved_model = _select_valid_saokhue_model(
                value, session.available_models, env_model
            )
            session.model = resolved_model
            if resolved_model not in session.available_models:
                session.available_models.insert(0, resolved_model)
            _persist_preferred_model(resolved_model)
            self._save_transcript(session)
        if session is not None:
            return {"configOptions": self._session_response_payload(session)["configOptions"]}
        return {"configOptions": []}

    async def handle_close_session(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("sessionId") or "")
        session = self._sessions.pop(sid, None)
        if session is not None:
            if session.active_turn_task is not None and not session.active_turn_task.done():
                session.cancelled = True
                session.active_turn_task.cancel()
            for client in list(session.mcp_clients.values()):
                await client.close()
            session.mcp_clients.clear()
        return {}

    async def handle_cancel(self, params: dict[str, Any]) -> None:
        sid = str(params.get("sessionId") or "")
        session = self._sessions.get(sid)
        if session is not None:
            session.cancelled = True
            if session.active_turn_task is not None and not session.active_turn_task.done():
                session.active_turn_task.cancel()

    def _build_openai_tools(self, session: SaokhueSession) -> list[dict[str, Any]]:
        if session.zero_tools:
            return []
        tools: list[dict[str, Any]] = list(BUILTIN_TOOL_SCHEMAS)
        for fn_name, (srv_name, orig_name) in session.mcp_tool_map.items():
            client = session.mcp_clients.get(srv_name)
            if client is None:
                continue
            schema_obj = next((t for t in client.tools if t.get("name") == orig_name), None)
            if schema_obj is None:
                continue
            input_schema = schema_obj.get("inputSchema")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "description": str(schema_obj.get("description") or f"{srv_name}.{orig_name}"),
                        "parameters": input_schema,
                    },
                }
            )
        return tools

    def _extract_user_content(self, prompt_param: Any) -> str | list[dict[str, Any]]:
        if isinstance(prompt_param, str):
            return prompt_param
        if not isinstance(prompt_param, list):
            return str(prompt_param or "")
        text_parts: list[str] = []
        multimodal_parts: list[dict[str, Any]] = []
        has_image = False
        for block in prompt_param:
            if isinstance(block, str):
                text_parts.append(block)
                multimodal_parts.append({"type": "text", "text": block})
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
                multimodal_parts.append({"type": "text", "text": block["text"]})
            elif btype == "image" and isinstance(block.get("data"), str):
                has_image = True
                mime = block.get("mimeType") or "image/png"
                data_uri = f"data:{mime};base64,{block['data']}"
                multimodal_parts.append({"type": "image_url", "image_url": {"url": data_uri}})
        if has_image:
            return multimodal_parts
        return "\n".join(text_parts)

    async def _request_tool_permission(
        self,
        session: SaokhueSession,
        *,
        tool_call_id: str,
        title: str,
        kind: str,
        raw_input: dict[str, Any],
        tool_name: str,
        mcp_server_name: str = "",
    ) -> bool:
        """Emit ``tool_call`` update and ask ``session/request_permission``."""
        kiro_meta: dict[str, Any] = {"toolName": tool_name}
        if mcp_server_name:
            kiro_meta["mcpServerName"] = mcp_server_name
        await self.send_notification(
            METHOD_SESSION_UPDATE,
            {
                "sessionId": session.session_id,
                "update": {
                    "sessionUpdate": UPDATE_TOOL_CALL,
                    "toolCallId": tool_call_id,
                    "title": title,
                    "kind": kind,
                    "status": "pending",
                    "rawInput": raw_input,
                    "_meta": {"kiro": kiro_meta},
                },
            },
        )
        perm_meta: dict[str, Any] = {"toolId": tool_name}
        if mcp_server_name:
            perm_meta["mcpTool"] = {
                "identity": {"serverName": mcp_server_name, "toolName": tool_name}
            }
        resp = await self.send_server_request(
            METHOD_REQUEST_PERMISSION,
            {
                "sessionId": session.session_id,
                "toolCall": {
                    "toolCallId": tool_call_id,
                    "title": title,
                    "kind": kind,
                    "status": "pending",
                    "rawInput": raw_input,
                },
                "options": [
                    {"optionId": OPTION_ALLOW_ONCE, "name": "Allow once", "kind": "allow_once"},
                    {
                        "optionId": OPTION_ALLOW_ALWAYS,
                        "name": "Allow always",
                        "kind": "allow_always",
                    },
                    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                ],
                "_meta": {"kiro": perm_meta},
            },
        )
        outcome = resp.get("outcome")
        if isinstance(outcome, dict):
            if outcome.get("outcome") == "selected":
                opt = str(outcome.get("optionId") or "")
                return opt in (OPTION_ALLOW_ONCE, OPTION_ALLOW_ALWAYS, "allow_once", "allow_always", "allow")
        return False

    async def _execute_builtin_tool(
        self, session: SaokhueSession, tool_name: str, args: dict[str, Any]
    ) -> tuple[str, bool]:
        ws = workspace_tools.Workspace(session.cwd)
        try:
            if tool_name in ("fs_read", "read_file"):
                text = ws.read_file(
                    path=str(args.get("path") or ""),
                    offset=int(args.get("offset") or 0),
                    limit=int(args.get("limit") or 0),
                )
                return text, False
            if tool_name in ("fs_write", "write_file"):
                text = ws.write_file(
                    path=str(args.get("path") or ""),
                    content=str(args.get("content") or ""),
                )
                return text, False
            if tool_name == "edit_file":
                text = ws.edit_file(
                    path=str(args.get("path") or ""),
                    old_str=str(args.get("old_str") or ""),
                    new_str=str(args.get("new_str") or ""),
                    replace_all=bool(args.get("replace_all", False)),
                )
                return text, False
            if tool_name == "list_directory":
                text = ws.list_directory(path=str(args.get("path") or "."))
                return text, False
            if tool_name == "glob":
                text = ws.glob(
                    pattern=str(args.get("pattern") or "*"),
                    path=str(args.get("path") or "."),
                )
                return text, False
            if tool_name == "grep":
                text = ws.grep(
                    pattern=str(args.get("pattern") or ""),
                    path=str(args.get("path") or "."),
                    include=str(args.get("include") or ""),
                )
                return text, False
            if tool_name in ("execute_bash", "run_command"):
                command = str(args.get("command") or "").strip()
                if not command:
                    return "command must not be empty", True
                rel_cwd = str(args.get("cwd") or ".")
                workdir = ws.resolve(rel_cwd)
                if not os.path.isdir(workdir):
                    return f"Not a directory: {rel_cwd}", True
                timeout_s = max(1, min(int(args.get("timeout") or 120), 600))
                child_env = {
                    k: v
                    for k, v in os.environ.items()
                    if k not in ("SAOKHUE_API_KEY", "SAOKHUE_ACCESS_TOKEN", "OPENAI_API_KEY")
                }
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(workdir),
                    env=child_env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    await proc.wait()
                    return f"Command timed out after {timeout_s}s", True
                out_str = (stdout_bytes or b"").decode("utf-8", errors="replace")
                rc = proc.returncode or 0
                return f"exit_code={rc}\n{out_str}", rc != 0
            return f"Unknown built-in tool: {tool_name}", True
        except Exception as exc:
            return f"Tool execution error: {exc}", True

    async def _stream_saokhue_turn(
        self,
        session: SaokhueSession,
        openai_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        """Stream one Chat Completions call from Sao Khuê and emit ACP text/thought chunks."""
        base_url, api_key, env_model, timeout_secs = resolve_saokhue_settings()
        user_auth = load_saokhue_user_auth()
        user_id = str(user_auth.get("user_id") or "").strip()
        if not api_key and os.environ.get("SAOKHUE_REQUIRE_LOGIN") == "1":
            raise RuntimeError(
                "Not signed in to Sao Khuê. Sign in from Settings > Agent Harness "
                "or run `saokhue-cli login` in your terminal, then start a new chat."
            )

        real_avail = [
            m
            for m in session.available_models
            if isinstance(m, str) and m.strip() and m.strip() not in ("auto", DEFAULT_FALLBACK_MODEL)
        ]
        if not real_avail:
            fetched = await self._fetch_saokhue_models(base_url, api_key)
            if fetched:
                session.available_models = fetched
        session.model = _select_valid_saokhue_model(
            session.model, session.available_models, env_model
        )

        candidate_models: list[str] = []
        for cand in (
            session.model,
            env_model,
            *[m for m in session.available_models if _is_likely_chat_model(m)],
            *session.available_models,
        ):
            c_clean = (cand or "").strip()
            if (
                c_clean
                and c_clean not in ("auto", DEFAULT_FALLBACK_MODEL)
                and c_clean not in candidate_models
            ):
                candidate_models.append(c_clean)
        if not candidate_models:
            candidate_models = [session.model or DEFAULT_FALLBACK_MODEL]
        candidate_models = candidate_models[:5]

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if user_id:
            headers["X-SaoKhue-User"] = user_id

        payload: dict[str, Any] = {
            "model": candidate_models[0],
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if user_id:
            payload["user"] = user_id
        if tools:
            payload["tools"] = tools

        url = f"{base_url}/v1/chat/completions"
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        turn_cost_usd = 0.0
        message_id = uuid.uuid4().hex

        if aiohttp is None:
            last_urllib_err: Exception | None = None
            for cand_idx, cand_model in enumerate(candidate_models):
                payload["model"] = cand_model
                session.model = cand_model
                for include_stream_opts in (True, False):
                    if include_stream_opts:
                        payload["stream_options"] = {"include_usage": True}
                    else:
                        payload.pop("stream_options", None)
                    try:
                        res = await self._stream_saokhue_turn_urllib(
                            session=session,
                            url=url,
                            headers=headers,
                            payload=payload,
                            timeout_secs=timeout_secs,
                            message_id=message_id,
                            openai_messages=openai_messages,
                        )
                        if cand_idx > 0:
                            _persist_preferred_model(cand_model)
                        return res
                    except Exception as exc:
                        last_urllib_err = exc
                        if "Not signed in to Sao Khuê" in str(exc):
                            raise
                        if include_stream_opts and "stream_options" in str(exc).lower():
                            continue
                        break
                logger.warning(
                    "Sao Khuê model %r failed (%s); trying next candidate if available",
                    cand_model,
                    last_urllib_err,
                )
            if last_urllib_err is not None:
                raise last_urllib_err

        timeout = aiohttp.ClientTimeout(total=timeout_secs)
        ssl_opt: Any = (
            False
            if os.environ.get("SAOKHUE_SSL_VERIFY", "").strip().lower()
            in ("0", "false", "no")
            else _build_system_ssl_context()
        )
        last_model_err: Exception | None = None
        for cand_idx, cand_model in enumerate(candidate_models):
            payload["model"] = cand_model
            session.model = cand_model
            payload["stream_options"] = {"include_usage": True}
            succeeded = False
            for attempt_ssl in ([ssl_opt] if ssl_opt is False else [ssl_opt, False]):
                content_parts.clear()
                tool_calls_by_index.clear()
                input_tokens = 0
                output_tokens = 0
                cache_read_tokens = 0
                cache_write_tokens = 0
                turn_cost_usd = 0.0
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as http:
                        post_kwargs: dict[str, Any] = {"headers": headers, "json": payload}
                        if attempt_ssl is not None:
                            post_kwargs["ssl"] = attempt_ssl
                        async with http.post(url, **post_kwargs) as resp:
                            if resp.status in (401, 403):
                                err_body = await resp.text()
                                raise RuntimeError(
                                    "Not signed in to Sao Khuê (HTTP "
                                    f"{resp.status}). Sign in from Settings > Agent Harness "
                                    f"or run `saokhue-cli login` in your terminal, then start a new chat. Details: {err_body[:300]}"
                                )
                            if resp.status >= 400:
                                err_body = await resp.text()
                                if (
                                    resp.status in (400, 422)
                                    and "stream_options" in payload
                                    and "stream_options" in err_body.lower()
                                ):
                                    payload.pop("stream_options", None)
                                    post_kwargs["json"] = payload
                                    async with http.post(url, **post_kwargs) as resp2:
                                        if resp2.status >= 400:
                                            err_body2 = await resp2.text()
                                            raise RuntimeError(
                                                f"Sao Khuê status={resp2.status} from {url} (model={cand_model!r}): "
                                                f"{err_body2[:500]}"
                                            )
                                        resp_cost_hdr = resp2.headers.get("x-saokhue-response-cost")
                                        if resp_cost_hdr:
                                            with contextlib.suppress(TypeError, ValueError):
                                                turn_cost_usd = max(turn_cost_usd, float(resp_cost_hdr))
                                        ctype = (resp2.headers.get("Content-Type") or "").lower()
                                        if "text/event-stream" not in ctype:
                                            data = await resp2.json()
                                            if isinstance(data, dict):
                                                (
                                                    input_tokens,
                                                    output_tokens,
                                                    cache_read_tokens,
                                                    cache_write_tokens,
                                                    turn_cost_usd,
                                                ) = _extract_usage_fields(
                                                    data,
                                                    input_tokens,
                                                    output_tokens,
                                                    cache_read_tokens,
                                                    cache_write_tokens,
                                                    turn_cost_usd,
                                                )
                                                choices = data.get("choices") or []
                                                if isinstance(choices, list) and choices:
                                                    msg = choices[0].get("message") or {}
                                                    text = msg.get("content")
                                                    if isinstance(text, str) and text:
                                                        content_parts.append(text)
                                                        await self.send_notification(
                                                            METHOD_SESSION_UPDATE,
                                                            {
                                                                "sessionId": session.session_id,
                                                                "update": {
                                                                    "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                                                    "messageId": message_id,
                                                                    "content": {"type": "text", "text": text},
                                                                },
                                                            },
                                                        )
                                        else:
                                            async for raw_line in resp2.content:
                                                if session.cancelled:
                                                    break
                                                line = raw_line.decode("utf-8", errors="replace").strip()
                                                if not line or not line.startswith("data:"):
                                                    continue
                                                data_str = line[len("data:") :].strip()
                                                if data_str == "[DONE]":
                                                    break
                                                try:
                                                    chunk = json.loads(data_str)
                                                except ValueError:
                                                    continue
                                                if not isinstance(chunk, dict):
                                                    continue
                                                (
                                                    input_tokens,
                                                    output_tokens,
                                                    cache_read_tokens,
                                                    cache_write_tokens,
                                                    turn_cost_usd,
                                                ) = _extract_usage_fields(
                                                    chunk,
                                                    input_tokens,
                                                    output_tokens,
                                                    cache_read_tokens,
                                                    cache_write_tokens,
                                                    turn_cost_usd,
                                                )
                                                choices = chunk.get("choices")
                                                if isinstance(choices, list) and choices:
                                                    delta = choices[0].get("delta") or {}
                                                    text_piece = delta.get("content")
                                                    if isinstance(text_piece, str) and text_piece:
                                                        content_parts.append(text_piece)
                                                        await self.send_notification(
                                                            METHOD_SESSION_UPDATE,
                                                            {
                                                                "sessionId": session.session_id,
                                                                "update": {
                                                                    "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                                                    "messageId": message_id,
                                                                    "content": {"type": "text", "text": text_piece},
                                                                },
                                                            },
                                                        )
                                    succeeded = True
                                    break
                                raise RuntimeError(
                                    f"Sao Khuê status={resp.status} from {url} (model={cand_model!r}): "
                                    f"{err_body[:500]}"
                                )
                            resp_cost_hdr = resp.headers.get("x-saokhue-response-cost")
                            if resp_cost_hdr:
                                with contextlib.suppress(TypeError, ValueError):
                                    turn_cost_usd = max(turn_cost_usd, float(resp_cost_hdr))
                            ctype = (resp.headers.get("Content-Type") or "").lower()
                            if "text/event-stream" not in ctype:
                                # Non-streaming JSON response fallback
                                data = await resp.json()
                                choices = data.get("choices") if isinstance(data, dict) else []
                                if isinstance(choices, list) and choices:
                                    msg = choices[0].get("message") or {}
                                    reasoning = msg.get("reasoning_content") or msg.get("thinking")
                                    if isinstance(reasoning, str) and reasoning:
                                        await self.send_notification(
                                            METHOD_SESSION_UPDATE,
                                            {
                                                "sessionId": session.session_id,
                                                "update": {
                                                    "sessionUpdate": UPDATE_AGENT_THOUGHT_CHUNK,
                                                    "messageId": message_id,
                                                    "content": {"type": "thinking", "text": reasoning},
                                                },
                                            },
                                        )
                                    text = msg.get("content")
                                    if isinstance(text, str) and text:
                                        content_parts.append(text)
                                        await self.send_notification(
                                            METHOD_SESSION_UPDATE,
                                            {
                                                "sessionId": session.session_id,
                                                "update": {
                                                    "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                                    "messageId": message_id,
                                                    "content": {"type": "text", "text": text},
                                                },
                                            },
                                        )
                                    raw_tcs = msg.get("tool_calls")
                                    if isinstance(raw_tcs, list):
                                        for idx, tc in enumerate(raw_tcs):
                                            if isinstance(tc, dict):
                                                tool_calls_by_index[idx] = tc
                                if isinstance(data, dict):
                                    (
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                        turn_cost_usd,
                                    ) = _extract_usage_fields(
                                        data,
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                        turn_cost_usd,
                                    )
                            else:
                                async for raw_line in resp.content:
                                    if session.cancelled:
                                        break
                                    line = raw_line.decode("utf-8", errors="replace").strip()
                                    if not line or not line.startswith("data:"):
                                        continue
                                    data_str = line[len("data:") :].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                    except ValueError:
                                        continue
                                    if not isinstance(chunk, dict):
                                        continue
                                    (
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                        turn_cost_usd,
                                    ) = _extract_usage_fields(
                                        chunk,
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                        turn_cost_usd,
                                    )
                                    choices = chunk.get("choices")
                                    if not isinstance(choices, list) or not choices:
                                        continue
                                    delta = choices[0].get("delta")
                                    if not isinstance(delta, dict):
                                        continue
                                    reasoning = delta.get("reasoning_content") or delta.get("thinking")
                                    if isinstance(reasoning, str) and reasoning:
                                        await self.send_notification(
                                            METHOD_SESSION_UPDATE,
                                            {
                                                "sessionId": session.session_id,
                                                "update": {
                                                    "sessionUpdate": UPDATE_AGENT_THOUGHT_CHUNK,
                                                    "messageId": message_id,
                                                    "content": {"type": "thinking", "text": reasoning},
                                                },
                                            },
                                        )
                                    text_piece = delta.get("content")
                                    if isinstance(text_piece, str) and text_piece:
                                        content_parts.append(text_piece)
                                        await self.send_notification(
                                            METHOD_SESSION_UPDATE,
                                            {
                                                "sessionId": session.session_id,
                                                "update": {
                                                    "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                                    "messageId": message_id,
                                                    "content": {"type": "text", "text": text_piece},
                                                },
                                            },
                                        )
                                    tc_deltas = delta.get("tool_calls")
                                    if isinstance(tc_deltas, list):
                                        for tc_delta in tc_deltas:
                                            if not isinstance(tc_delta, dict):
                                                continue
                                            idx = int(tc_delta.get("index") or 0)
                                            entry = tool_calls_by_index.setdefault(
                                                idx,
                                                {
                                                    "id": "",
                                                    "type": "function",
                                                    "function": {"name": "", "arguments": ""},
                                                },
                                            )
                                            if tc_delta.get("id"):
                                                entry["id"] = str(tc_delta["id"])
                                            fn_delta = tc_delta.get("function")
                                            if isinstance(fn_delta, dict):
                                                if fn_delta.get("name"):
                                                    entry["function"]["name"] += str(fn_delta["name"])
                                                if fn_delta.get("arguments"):
                                                    entry["function"]["arguments"] += str(
                                                        fn_delta["arguments"]
                                                    )
                    succeeded = True
                    break
                except Exception as exc:
                    if attempt_ssl is not False and _is_ssl_cert_error(exc):
                        logger.debug("Retrying POST %s with ssl=False after %s", url, exc)
                        continue
                    if "Not signed in to Sao Khuê" in str(exc):
                        raise
                    last_model_err = exc
                    break
            if succeeded:
                if cand_idx > 0:
                    _persist_preferred_model(cand_model)
                break
            logger.warning(
                "Sao Khuê model %r failed (%s); trying next candidate if available",
                cand_model,
                last_model_err,
            )
        else:
            if last_model_err is not None:
                raise last_model_err

        ordered_tcs: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_by_index):
            tc = tool_calls_by_index[idx]
            if not tc.get("id"):
                tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
            ordered_tcs.append(tc)
        full_reply = "".join(content_parts)
        if input_tokens <= 0:
            input_tokens = _estimate_messages_tokens(openai_messages)
        if output_tokens <= 0 and (full_reply or ordered_tcs):
            output_tokens = max(
                1,
                (len(full_reply) + len(json.dumps(ordered_tcs, ensure_ascii=False))) // 4,
            )
        return (
            full_reply,
            ordered_tcs,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            turn_cost_usd,
        )

    async def _stream_saokhue_turn_urllib(
        self,
        *,
        session: SaokhueSession,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_secs: float,
        message_id: str,
        openai_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], int, int, int, int, float]:
        """Stdlib ``urllib.request`` streaming fallback when ``aiohttp`` is not installed."""
        loop = asyncio.get_running_loop()
        line_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def _worker() -> None:
            body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
            try:
                with _urlopen_resilient(req, timeout=timeout_secs) as resp:
                    resp_cost_hdr = resp.headers.get("x-saokhue-response-cost")
                    if resp_cost_hdr:
                        loop.call_soon_threadsafe(line_queue.put_nowait, ("cost_hdr", resp_cost_hdr))
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if "text/event-stream" not in ctype:
                        raw = resp.read().decode("utf-8", errors="replace")
                        loop.call_soon_threadsafe(line_queue.put_nowait, ("json", raw))
                    else:
                        for raw_line in resp:
                            if session.cancelled:
                                break
                            decoded = raw_line.decode("utf-8", errors="replace").strip()
                            loop.call_soon_threadsafe(line_queue.put_nowait, ("line", decoded))
            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    err_body = str(exc)
                if exc.code in (401, 403):
                    err_obj = RuntimeError(
                        "Not signed in to Sao Khuê (HTTP "
                        f"{exc.code}). Sign in from Settings > Agent Harness "
                        f"or run `saokhue-cli login` in your terminal, then start a new chat. Details: {err_body[:300]}"
                    )
                else:
                    err_obj = RuntimeError(
                        f"Sao Khuê status={exc.code} from {url} (model={session.model!r}): "
                        f"{err_body[:500]}"
                    )
                loop.call_soon_threadsafe(
                    line_queue.put_nowait,
                    ("error", err_obj),
                )
                return
            except Exception as exc:
                loop.call_soon_threadsafe(line_queue.put_nowait, ("error", exc))
                return
            loop.call_soon_threadsafe(line_queue.put_nowait, ("eof", None))

        worker_fut = asyncio.to_thread(_worker)
        worker_task = asyncio.create_task(worker_fut)

        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        turn_cost_usd = 0.0

        try:
            while True:
                kind, item = await line_queue.get()
                if kind == "error":
                    raise item
                if kind == "eof":
                    break
                if kind == "cost_hdr":
                    with contextlib.suppress(TypeError, ValueError):
                        turn_cost_usd = max(turn_cost_usd, float(item))
                    continue
                if kind == "json":
                    data = json.loads(item)
                    choices = data.get("choices") if isinstance(data, dict) else []
                    if isinstance(choices, list) and choices:
                        msg = choices[0].get("message") or {}
                        reasoning = msg.get("reasoning_content") or msg.get("thinking")
                        if isinstance(reasoning, str) and reasoning:
                            await self.send_notification(
                                METHOD_SESSION_UPDATE,
                                {
                                    "sessionId": session.session_id,
                                    "update": {
                                        "sessionUpdate": UPDATE_AGENT_THOUGHT_CHUNK,
                                        "messageId": message_id,
                                        "content": {"type": "thinking", "text": reasoning},
                                    },
                                },
                            )
                        text = msg.get("content")
                        if isinstance(text, str) and text:
                            content_parts.append(text)
                            await self.send_notification(
                                METHOD_SESSION_UPDATE,
                                {
                                    "sessionId": session.session_id,
                                    "update": {
                                        "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                        "messageId": message_id,
                                        "content": {"type": "text", "text": text},
                                    },
                                },
                            )
                        raw_tcs = msg.get("tool_calls")
                        if isinstance(raw_tcs, list):
                            for idx, tc in enumerate(raw_tcs):
                                if isinstance(tc, dict):
                                    tool_calls_by_index[idx] = tc
                    if isinstance(data, dict):
                        (
                            input_tokens,
                            output_tokens,
                            cache_read_tokens,
                            cache_write_tokens,
                            turn_cost_usd,
                        ) = _extract_usage_fields(
                            data,
                            input_tokens,
                            output_tokens,
                            cache_read_tokens,
                            cache_write_tokens,
                            turn_cost_usd,
                        )
                    break
                if kind == "line":
                    line = str(item)
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    (
                        input_tokens,
                        output_tokens,
                        cache_read_tokens,
                        cache_write_tokens,
                        turn_cost_usd,
                    ) = _extract_usage_fields(
                        chunk,
                        input_tokens,
                        output_tokens,
                        cache_read_tokens,
                        cache_write_tokens,
                        turn_cost_usd,
                    )
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue
                    reasoning = delta.get("reasoning_content") or delta.get("thinking")
                    if isinstance(reasoning, str) and reasoning:
                        await self.send_notification(
                            METHOD_SESSION_UPDATE,
                            {
                                "sessionId": session.session_id,
                                "update": {
                                    "sessionUpdate": UPDATE_AGENT_THOUGHT_CHUNK,
                                    "messageId": message_id,
                                    "content": {"type": "thinking", "text": reasoning},
                                },
                            },
                        )
                    text_piece = delta.get("content")
                    if isinstance(text_piece, str) and text_piece:
                        content_parts.append(text_piece)
                        await self.send_notification(
                            METHOD_SESSION_UPDATE,
                            {
                                "sessionId": session.session_id,
                                "update": {
                                    "sessionUpdate": UPDATE_AGENT_MESSAGE_CHUNK,
                                    "messageId": message_id,
                                    "content": {"type": "text", "text": text_piece},
                                },
                            },
                        )
                    tc_deltas = delta.get("tool_calls")
                    if isinstance(tc_deltas, list):
                        for tc_delta in tc_deltas:
                            if not isinstance(tc_delta, dict):
                                continue
                            idx = int(tc_delta.get("index") or 0)
                            entry = tool_calls_by_index.setdefault(
                                idx,
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if tc_delta.get("id"):
                                entry["id"] = str(tc_delta["id"])
                            fn_delta = tc_delta.get("function")
                            if isinstance(fn_delta, dict):
                                if fn_delta.get("name"):
                                    entry["function"]["name"] += str(fn_delta["name"])
                                if fn_delta.get("arguments"):
                                    entry["function"]["arguments"] += str(fn_delta["arguments"])
        finally:
            with contextlib.suppress(Exception):
                await worker_task

        ordered_tcs: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_by_index):
            tc = tool_calls_by_index[idx]
            if not tc.get("id"):
                tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
            ordered_tcs.append(tc)
        full_reply = "".join(content_parts)
        if input_tokens <= 0 and openai_messages:
            input_tokens = _estimate_messages_tokens(openai_messages)
        if output_tokens <= 0 and (full_reply or ordered_tcs):
            output_tokens = max(
                1,
                (len(full_reply) + len(json.dumps(ordered_tcs, ensure_ascii=False))) // 4,
            )
        return (
            full_reply,
            ordered_tcs,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            turn_cost_usd,
        )

    async def handle_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("sessionId") or "")
        session = self._sessions.get(sid)
        if session is None:
            raise RuntimeError(f"Unknown sessionId: {sid!r}")

        session.cancelled = False
        user_content = self._extract_user_content(params.get("prompt"))
        session.messages.append({"role": "user", "content": user_content})
        tools = self._build_openai_tools(session)

        total_in = 0
        total_out = 0
        total_cache_read = 0
        total_cache_write = 0
        total_cost_usd = 0.0
        last_call_context = 0
        try:
            for _step in range(MAX_AGENT_TURNS):
                if session.cancelled:
                    return {"stopReason": STOP_REASON_CANCELLED}

                openai_messages: list[dict[str, Any]] = [
                    {"role": "system", "content": session.system_prompt},
                    *session.messages,
                ]
                (
                    assistant_text,
                    tool_calls,
                    in_tok,
                    out_tok,
                    c_read,
                    c_write,
                    step_cost,
                ) = await self._stream_saokhue_turn(session, openai_messages, tools)
                total_in += in_tok
                total_out += out_tok
                total_cache_read += c_read
                total_cache_write += c_write
                total_cost_usd += step_cost
                last_call_context = in_tok + out_tok

                if session.cancelled:
                    return {"stopReason": STOP_REASON_CANCELLED}

                assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_text or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                session.messages.append(assistant_msg)
                self._save_transcript(session)

                if not tool_calls:
                    break

                for tc in tool_calls:
                    if session.cancelled:
                        return {"stopReason": STOP_REASON_CANCELLED}
                    tc_id = str(tc.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                    fn = tc.get("function") or {}
                    fn_name = str(fn.get("name") or "")
                    raw_args_str = str(fn.get("arguments") or "{}")
                    try:
                        parsed_args = json.loads(raw_args_str) if raw_args_str.strip() else {}
                        if not isinstance(parsed_args, dict):
                            parsed_args = {"value": parsed_args}
                    except ValueError:
                        parsed_args = {"_raw": raw_args_str}

                    if fn_name in session.mcp_tool_map:
                        srv_name, orig_tool = session.mcp_tool_map[fn_name]
                        kind = "other"
                        title = f"{srv_name}: {orig_tool}"
                        allowed = await self._request_tool_permission(
                            session,
                            tool_call_id=tc_id,
                            title=title,
                            kind=kind,
                            raw_input=parsed_args,
                            tool_name=orig_tool,
                            mcp_server_name=srv_name,
                        )
                        if not allowed:
                            output_text = f"Tool call {orig_tool!r} was denied by user or policy."
                            status = "refused"
                        else:
                            mcp_client = session.mcp_clients.get(srv_name)
                            if mcp_client is None:
                                output_text = f"MCP server {srv_name!r} is not connected."
                                status = "failed"
                            else:
                                try:
                                    output_text, is_err = await mcp_client.call_tool(
                                        orig_tool, parsed_args
                                    )
                                    status = "failed" if is_err else "completed"
                                except Exception as exc:
                                    output_text = f"MCP tool error: {exc}"
                                    status = "failed"
                    else:
                        kind = BUILTIN_TOOL_KINDS.get(fn_name, "other")
                        if kind == "execute":
                            title = str(parsed_args.get("command") or fn_name)
                        elif "path" in parsed_args:
                            title = f"{fn_name}: {parsed_args['path']}"
                        else:
                            title = fn_name
                        allowed = await self._request_tool_permission(
                            session,
                            tool_call_id=tc_id,
                            title=title,
                            kind=kind,
                            raw_input=parsed_args,
                            tool_name=fn_name,
                        )
                        if not allowed:
                            output_text = f"Tool call {fn_name!r} was denied by user or policy."
                            status = "refused"
                        else:
                            output_text, is_err = await self._execute_builtin_tool(
                                session, fn_name, parsed_args
                            )
                            status = "failed" if is_err else "completed"

                    await self.send_notification(
                        METHOD_SESSION_UPDATE,
                        {
                            "sessionId": session.session_id,
                            "update": {
                                "sessionUpdate": UPDATE_TOOL_CALL_UPDATE,
                                "toolCallId": tc_id,
                                "status": status,
                                "content": [
                                    {
                                        "type": "content",
                                        "content": {"type": "text", "text": output_text},
                                    }
                                ],
                            },
                        },
                    )
                    session.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": output_text,
                        }
                    )
                    self._save_transcript(session)
        except asyncio.CancelledError:
            return {"stopReason": STOP_REASON_CANCELLED}

        used_tokens = last_call_context or (total_in + total_out)
        pct = min(100.0, round((used_tokens / DEFAULT_CONTEXT_WINDOW_TOKENS) * 100.0, 2))
        usage_update_payload: dict[str, Any] = {
            "sessionUpdate": UPDATE_USAGE,
            "used": used_tokens,
            "size": DEFAULT_CONTEXT_WINDOW_TOKENS,
        }
        if total_cost_usd > 0:
            session.cumulative_cost_usd += total_cost_usd
            usage_update_payload["cost"] = {
                "amount": round(session.cumulative_cost_usd, 6),
                "currency": "USD",
            }
        await self.send_notification(
            METHOD_SESSION_UPDATE,
            {
                "sessionId": session.session_id,
                "update": usage_update_payload,
            },
        )
        # Convert token count to Kiro-equivalent credits (1 credit per 1,000 tokens)
        # when the upstream proxy does not report USD cost, so the assistant reply
        # footer (`turn_stats`) displays turn consumption alongside the model name.
        turn_credits = round((total_in + total_out) / 1000.0, 4) if total_cost_usd <= 0 else 0.0
        metadata_payload: dict[str, Any] = {
            "sessionId": session.session_id,
            "contextUsagePercentage": pct,
        }
        if turn_credits > 0:
            metadata_payload["meteringUsage"] = [
                {"unit": "credit", "unitPlural": "credits", "value": turn_credits}
            ]
        await self.send_notification(
            METHOD_METADATA,
            metadata_payload,
        )
        return {
            "stopReason": STOP_REASON_END_TURN,
            "usage": {
                "inputTokens": total_in,
                "outputTokens": total_out,
                "cachedReadTokens": total_cache_read,
                "cachedWriteTokens": total_cache_write,
            },
        }

    async def _dispatch_request(self, req_id: Any, method: str, params: dict[str, Any]) -> None:
        try:
            if method == METHOD_INITIALIZE:
                result = await self.handle_initialize(params)
            elif method == METHOD_SESSION_NEW:
                result = await self._create_or_load_session(params)
            elif method in (METHOD_SESSION_LOAD, METHOD_SESSION_RESUME):
                sid = str(params.get("sessionId") or "")
                result = await self._create_or_load_session(params, session_id=sid or None)
            elif method == METHOD_SET_MODE:
                result = await self.handle_set_mode(params)
            elif method == METHOD_SET_MODEL:
                result = await self.handle_set_model(params)
            elif method == METHOD_SET_CONFIG_OPTION:
                result = await self.handle_set_config_option(params)
            elif method in (METHOD_SESSION_TERMINATE, METHOD_SESSION_CLOSE):
                result = await self.handle_close_session(params)
            elif method == METHOD_PROMPT:
                sid = str(params.get("sessionId") or "")
                session = self._sessions.get(sid)
                task = asyncio.create_task(self.handle_prompt(params))
                if session is not None:
                    session.active_turn_task = task
                result = await task
            elif method == METHOD_CANCEL:
                await self.handle_cancel(params)
                result = {}
            else:
                await self.send_message(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
                return
            await self.send_message({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:
            logger.warning("Request %s failed: %s", method, exc, exc_info=True)
            await self.send_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32603,
                        "message": str(exc),
                        "data": {"details": str(exc)},
                    },
                }
            )

    async def handle_incoming_line(
        self, text: str, active_tasks: set[asyncio.Task[None]] | None = None
    ) -> None:
        """Process one inbound JSON-RPC 2.0 line from the client."""
        text = text.strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return

        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

        if method is None and req_id is not None:
            fut = self._pending_client_replies.pop(req_id, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return

        if method == METHOD_CANCEL and req_id is None:
            await self.handle_cancel(params)
            return

        if isinstance(method, str) and req_id is not None:
            task = asyncio.create_task(self._dispatch_request(req_id, method, params))
            if active_tasks is not None:
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

    async def serve_stdio(self) -> None:
        active_tasks: set[asyncio.Task[None]] = set()
        if sys.platform == "win32":
            while True:
                raw_line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not raw_line:
                    break
                text = raw_line.decode("utf-8", errors="replace")
                await self.handle_incoming_line(text, active_tasks)
            return

        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            await self.handle_incoming_line(text, active_tasks)


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
    print(f"\nSigned in to Sao Khuê ({record.get('base_url')}/v1) as {record.get('user_id') or 'user'}!")
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
                    data=urllib.parse.urlencode(
                        {"token": tok, "client_id": client_id}
                    ).encode("utf-8"),
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
    scope_val = str(access_claims.get("scope") or id_claims.get("scope") or granted_scope or "").strip()
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


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(Exception):
        ensure_system_ca_bundle()
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Support `saokhue-cli auth login` / `saokhue-cli auth logout` / `saokhue-cli auth status`
    if len(raw_argv) >= 2 and raw_argv[0] == "auth" and raw_argv[1] in ("login", "logout", "status", "whoami"):
        raw_argv = raw_argv[1:]

    if raw_argv and raw_argv[0] in ("login", "logout", "status", "whoami", "models"):
        cmd = raw_argv[0]
        sub_argv = raw_argv[1:]
        if cmd == "login":
            p = argparse.ArgumentParser(prog="saokhue-cli login", description="Sign in to Sao Khuê per user")
            p.add_argument(
                "--url",
                "--server",
                dest="url",
                default="",
                help=f"Sao Khuê Server URL (default: {DEFAULT_SAOKHUE_BASE_URL}/v1)",
            )
            p.add_argument(
                "--method",
                choices=["sso", "device", "password", "token"],
                default="",
                help="Authentication method: sso (PKCE), device (Device Code), password, or token",
            )
            p.add_argument(
                "--use-device-flow",
                action="store_true",
                help=f"Use OAuth2 Device Authorization Flow ({DEFAULT_OIDC_DEVICE_AUTH_URL})",
            )
            p.add_argument("--username", "-u", default="", help="Username / User ID / Email")
            p.add_argument("--password", "-p", default="", help="Password (for password method)")
            p.add_argument("--login-endpoint", default="", help="Custom login endpoint path or URL")
            p.add_argument(
                "--sso-url",
                default="",
                help=f"OAuth2 authorize URL (default: {DEFAULT_OIDC_AUTHORIZE_URL})",
            )
            p.add_argument(
                "--device-auth-url",
                default="",
                help=f"OAuth2 device authorization URL (default: {DEFAULT_OIDC_DEVICE_AUTH_URL})",
            )
            p.add_argument(
                "--token-url",
                default="",
                help=f"OAuth2 token URL (default: {DEFAULT_OIDC_TOKEN_URL})",
            )
            p.add_argument(
                "--userinfo-url",
                default="",
                help=f"OIDC userinfo URL (default: {DEFAULT_OIDC_USERINFO_URL})",
            )
            p.add_argument(
                "--client-id",
                default="",
                help=f"OAuth2 / OIDC client_id (default: {DEFAULT_OIDC_CLIENT_ID})",
            )
            p.add_argument(
                "--scope",
                default="",
                help=f"OAuth2 scopes (default: '{DEFAULT_OIDC_SCOPE}')",
            )
            p.add_argument(
                "--resource",
                default="",
                help=f"OAuth2 RFC 8707 resource indicator (default: '{DEFAULT_OIDC_RESOURCE}')",
            )
            p.add_argument("--callback-port", type=int, default=0, help="Local callback port for SSO")
            p.add_argument("--no-browser", action="store_true", help="Do not auto-open browser for SSO/Device flow")
            p.add_argument("--token", "-t", default="", help="Personal access token or virtual key")
            p.add_argument("--model", "-m", default="", help="Default model ID to save for this user")
            return _cli_login(p.parse_args(sub_argv))
        if cmd == "logout":
            return _cli_logout()
        if cmd in ("status", "whoami"):
            return _cli_status()
        if cmd == "models":
            return _cli_models()

    parser = argparse.ArgumentParser(
        prog="saokhue-cli",
        description=(
            "Sao Khuê built-in CLI & ACP server.\n"
            f"Default Server: {DEFAULT_SAOKHUE_BASE_URL}/v1 (IdP client_id: {DEFAULT_OIDC_CLIENT_ID})\n\n"
            "Commands:\n"
            "  saokhue-cli login                    Sign in via Browser OAuth2 (PKCE) or Device Flow\n"
            "  saokhue-cli login --use-device-flow  Sign in via OAuth2 Device Code (/api/idpOAuth/device/auth)\n"
            "  saokhue-cli logout                   Revoke token & remove ~/.saokhue-cli/auth.json\n"
            "  saokhue-cli status                   Show current signed-in user and test connection\n"
            "  saokhue-cli models                   List available models for the signed-in user\n"
            "  saokhue-cli acp                      Run the JSON-RPC 2.0 ACP server over stdio"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--agent", default="kirocrew", help="Agent spec name")
    parser.add_argument("--model", default="", help="Initial model ID")

    if not raw_argv:
        parser.print_help()
        print("", flush=True)
        auth = load_saokhue_user_auth(auto_refresh=False)
        if auth and auth.get("access_token"):
            print(
                f"Current status: SIGNED IN as {auth.get('user_id') or 'user'} "
                f"({auth.get('base_url') or DEFAULT_SAOKHUE_BASE_URL}/v1)",
                flush=True,
            )
            return 0
        print(f"Current status: NOT SIGNED IN ({saokhue_auth_path()})", flush=True)
        if sys.stdin.isatty():
            try:
                ans = input("\nWould you like to sign in now? [Y/n]: ").strip().lower()
            except EOFError:
                ans = "n"
            if ans in ("", "y", "yes"):
                return _cli_login(
                    argparse.Namespace(
                        url="",
                        method="",
                        use_device_flow=False,
                        username="",
                        password="",
                        login_endpoint="",
                        sso_url="",
                        device_auth_url="",
                        token_url="",
                        userinfo_url="",
                        client_id="",
                        scope="",
                        resource="",
                        callback_port=0,
                        no_browser=False,
                        token="",
                        model="",
                    )
                )
        return 0

    if raw_argv[0] == "acp":
        raw_argv = raw_argv[1:]

    args, _ = parser.parse_known_args(raw_argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")

    server = SaokhueAcpServer(agent_name=args.agent, initial_model=args.model)
    try:
        asyncio.run(server.serve_stdio())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
