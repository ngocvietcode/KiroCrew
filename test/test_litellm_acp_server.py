"""Tests for the built-in LiteLLM ACP server and harness registration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

from kiro_crew.agent_sdk import backend_install, host_auth, native_allow_stores
from kiro_crew.agent_sdk.backends import (
    ACP_BACKEND_LITELLM,
    ACP_BACKEND_ROUTING,
    ACP_BACKENDS_ACP_CLIENT_SPAWNABLE,
    ACP_BACKENDS_ACP_RUNTIME,
    ACP_BACKENDS_ADVERTISED_MODEL_SELECTION,
    ACP_BACKENDS_HONOR_ZERO_TOOL_BAN,
    ACP_BACKENDS_INDEPENDENT_SETUP,
    ACP_BACKENDS_KNOWN,
    ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION,
    ACP_BACKENDS_REPORT_USAGE,
    ACP_BACKENDS_SPEC_FILE_GRANTS,
    ACP_BACKENDS_SPEC_SERVERS_OFF_WIRE,
    BASELINE_SELECTABLE_BACKENDS,
    POLICY_ID_BY_BACKEND,
    Routing,
    model_registry_namespace,
)


def _load_litellm_server_module() -> Any:
    import sys

    script_path = (
        Path(__file__).resolve().parents[1] / "src" / "kiro_crew" / "acp" / "litellm_server.py"
    )
    spec = importlib.util.spec_from_file_location("kiro_crew_litellm_server", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


litellm_server = _load_litellm_server_module()


def test_litellm_backend_vocabulary_and_declarations() -> None:
    from kiro_crew import security

    assert ACP_BACKEND_LITELLM == "litellm"
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_KNOWN
    assert ACP_BACKEND_LITELLM in BASELINE_SELECTABLE_BACKENDS
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_INDEPENDENT_SETUP
    assert ACP_BACKEND_LITELLM not in ACP_BACKENDS_ACP_RUNTIME
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_ACP_CLIENT_SPAWNABLE
    assert POLICY_ID_BY_BACKEND[ACP_BACKEND_LITELLM] == "litellm"
    assert ACP_BACKEND_ROUTING[ACP_BACKEND_LITELLM] is Routing.AGENT_SPEC
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_SPEC_FILE_GRANTS
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_SPEC_SERVERS_OFF_WIRE
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_HONOR_ZERO_TOOL_BAN
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_ADVERTISED_MODEL_SELECTION
    assert ACP_BACKEND_LITELLM in ACP_BACKENDS_REPORT_USAGE
    assert model_registry_namespace(ACP_BACKEND_LITELLM) == "litellm"

    assert host_auth.missing_declarations() == ()
    decl = host_auth.declaration_for(ACP_BACKEND_LITELLM)
    assert decl.backend == ACP_BACKEND_LITELLM
    assert decl.entitlement_source == host_auth.ENTITLEMENT_OWN_CREDENTIAL_FILE
    assert decl.credential_leaves == (".litellm-cli/auth.json",)
    assert decl.home_override_env_vars == ("LITELLM_CLI_HOME",)
    assert decl.override_relative_leaves == ("auth.json",)
    assert ".litellm-cli/auth.json" in security.sensitive_home_dirs()
    assert "litellm-cli login" in decl.sign_in_remedy
    assert "litellm-cli login" in decl.signed_out_message
    assert "--" not in decl.sign_in_remedy
    assert "`" not in decl.sign_in_remedy
    assert host_auth.reports_signed_out(
        ACP_BACKEND_LITELLM,
        "Not signed in to LiteLLM (HTTP 401). Run `litellm-cli login` in your terminal.",
    )

    assert ACP_BACKEND_LITELLM in native_allow_stores.NATIVE_ALLOW_STORES_NONE
    state = backend_install.probe_backend(ACP_BACKEND_LITELLM)
    assert state.installed == backend_install.INSTALLED
    assert state.policy_id == "litellm"


def test_resolve_litellm_settings_normalizes_url_and_env(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "litellm_cli")
    base_url, api_key, default_model, timeout = litellm_server.resolve_litellm_settings(
        {
            "LITELLM_CLI_HOME": cli_home,
            "LITELLM_BASE_URL": "http://127.0.0.1:4000/v1/",
            "LITELLM_API_KEY": "sk-litellm-test",
            "LITELLM_MODEL": "my-proxy-model",
            "LITELLM_TIMEOUT": "90",
        }
    )
    assert base_url == "http://127.0.0.1:4000"
    assert api_key == "sk-litellm-test"
    assert default_model == "my-proxy-model"
    assert timeout == 90.0


def test_litellm_cli_per_user_login_password_token_and_logout(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "user_litellm_home")
    old_urlopen = litellm_server.urllib.request.urlopen
    old_cli_home = litellm_server.os.environ.get("LITELLM_CLI_HOME")
    litellm_server.os.environ["LITELLM_CLI_HOME"] = cli_home

    class _FakeResp:
        def __init__(self, status: int, body: dict[str, Any]) -> None:
            self.status = status
            self.headers = {"Content-Type": "application/json"}
            self._raw = json.dumps(body).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        url = req.full_url
        if url.endswith("/login"):
            body = json.loads(req.data.decode("utf-8"))
            assert body["username"] == "viet.bui"
            assert body["password"] == "secret-pass"
            return _FakeResp(
                200,
                {
                    "access_token": "jwt-user-viet-123",
                    "refresh_token": "refresh-xyz",
                    "expires_in": 3600,
                    "user_id": "viet.bui",
                    "email": "viet.bui@company.local",
                },
            )
        if url.endswith("/v1/models"):
            assert req.get_header("Authorization") == "Bearer jwt-user-viet-123"
            return _FakeResp(
                200,
                {
                    "data": [
                        {"id": "company-claude-sonnet"},
                        {"id": "company-gpt-4o"},
                    ]
                },
            )
        if url.endswith("/user/info"):
            return _FakeResp(
                200,
                {"user_id": "viet.bui", "user_email": "viet.bui@company.local"},
            )
        if url.endswith("/api/idpOAuth/revoke"):
            return _FakeResp(200, {"revoked": True})
        raise AssertionError(f"Unexpected URL: {url}")

    litellm_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
    try:
        rc = litellm_server.main(
            [
                "login",
                "--url",
                "http://litellm.company.local:4000",
                "--method",
                "password",
                "--username",
                "viet.bui",
                "--password",
                "secret-pass",
            ]
        )
        assert rc == 0

        auth_file = Path(cli_home) / "auth.json"
        assert auth_file.is_file()
        saved = json.loads(auth_file.read_text(encoding="utf-8"))
        assert saved["user_id"] == "viet.bui"
        assert saved["email"] == "viet.bui@company.local"
        assert saved["access_token"] == "jwt-user-viet-123"
        assert saved["auth_method"] == "password"
        assert saved["default_model"] == "company-claude-sonnet"

        # Verify resolve_litellm_settings reads the logged-in user's token
        url, tok, model, _ = litellm_server.resolve_litellm_settings({"LITELLM_CLI_HOME": cli_home})
        assert url == "http://litellm.company.local:4000"
        assert tok == "jwt-user-viet-123"
        assert model == "company-claude-sonnet"

        # Status and models commands succeed
        assert litellm_server.main(["status"]) == 0
        assert litellm_server.main(["models"]) == 0

        # Logout removes auth.json
        assert litellm_server.main(["logout"]) == 0
        assert not auth_file.exists()
        assert litellm_server.main(["status"]) == 1
    finally:
        litellm_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
        if old_cli_home is None:
            litellm_server.os.environ.pop("LITELLM_CLI_HOME", None)
        else:
            litellm_server.os.environ["LITELLM_CLI_HOME"] = old_cli_home


def test_litellm_cli_vpbank_oidc_device_flow_and_userinfo(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "vpbank_oidc_home")
    old_urlopen = litellm_server.urllib.request.urlopen
    old_sleep = litellm_server.time.sleep
    old_cli_home = litellm_server.os.environ.get("LITELLM_CLI_HOME")
    litellm_server.os.environ["LITELLM_CLI_HOME"] = cli_home
    litellm_server.time.sleep = lambda _s: None  # type: ignore[assignment]

    class _FakeResp:
        def __init__(self, status: int, body: dict[str, Any]) -> None:
            self.status = status
            self.headers = {"Content-Type": "application/json"}
            self._raw = json.dumps(body).encode("utf-8")

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        url = req.full_url
        if url == "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/device/auth":
            form = req.data.decode("utf-8")
            assert "client_id=app%3Atest-crew" in form
            assert "resource=https%3A%2F%2Fportal.aws.rpa.vpbank.dev%2Fapi%2F" in form
            return _FakeResp(
                200,
                {
                    "device_code": "dev-code-999",
                    "user_code": "VPBK-1234",
                    "verification_uri": "https://portal.aws.rpa.vpbank.dev/device",
                    "verification_uri_complete": "https://portal.aws.rpa.vpbank.dev/device?user_code=VPBK-1234",
                    "expires_in": 300,
                    "interval": 1,
                },
            )
        if url == "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/token":
            form = req.data.decode("utf-8")
            assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Adevice_code" in form
            assert "device_code=dev-code-999" in form
            assert "client_id=app%3Atest-crew" in form
            assert "resource=https%3A%2F%2Fportal.aws.rpa.vpbank.dev%2Fapi%2F" in form
            return _FakeResp(
                200,
                {
                    "access_token": "vpbank-oauth-access-token",
                    "refresh_token": "vpbank-oauth-refresh-token",
                    "expires_in": 3600,
                },
            )
        if url == "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/me":
            assert req.get_header("Authorization") == "Bearer vpbank-oauth-access-token"
            return _FakeResp(
                200,
                {
                    "sub": "u-12345",
                    "preferred_username": "vietbn",
                    "name": "Bui Ngoc Viet",
                    "email": "vietbn@vpbank.com.vn",
                },
            )
        if url == "https://portal.aws.rpa.vpbank.dev/api/ai-llm/v1/models":
            assert req.get_header("Authorization") == "Bearer vpbank-oauth-access-token"
            return _FakeResp(
                200,
                {
                    "data": [
                        {"id": "claude-sonnet-4"},
                        {"id": "gpt-4.1"},
                    ]
                },
            )
        if url == "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/revoke":
            return _FakeResp(200, {"status": "ok"})
        raise AssertionError(f"Unexpected URL: {url}")

    litellm_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
    try:
        rc = litellm_server.main(["login", "--use-device-flow", "--no-browser"])
        assert rc == 0

        auth_file = Path(cli_home) / "auth.json"
        saved = json.loads(auth_file.read_text(encoding="utf-8"))
        assert saved["base_url"] == "https://portal.aws.rpa.vpbank.dev/api/ai-llm"
        assert saved["client_id"] == "app:test-crew"
        assert saved["user_id"] == "vietbn"
        assert saved["email"] == "vietbn@vpbank.com.vn"
        assert saved["access_token"] == "vpbank-oauth-access-token"
        assert saved["refresh_token"] == "vpbank-oauth-refresh-token"
        assert saved["auth_method"] == "device_code"
        assert saved["models"] == ["claude-sonnet-4", "gpt-4.1"]

        assert litellm_server.main(["logout"]) == 0
        assert not auth_file.exists()
    finally:
        litellm_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
        litellm_server.time.sleep = old_sleep  # type: ignore[assignment]
        if old_cli_home is None:
            litellm_server.os.environ.pop("LITELLM_CLI_HOME", None)
        else:
            litellm_server.os.environ["LITELLM_CLI_HOME"] = old_cli_home


def test_litellm_acp_server_session_and_tool_permission_roundtrip(tmp_path: Path) -> None:
    sample_file = tmp_path / "hello.txt"
    sample_file.write_text("Hello from workspace!", encoding="utf-8")

    class _FakeHttpResponse:
        def __init__(self, status: int, content_type: str, lines: list[bytes]) -> None:
            self.status = status
            self.headers = {"Content-Type": content_type}
            self._lines = lines

        def read(self) -> bytes:
            return b"".join(self._lines)

        def __iter__(self):
            return iter(self._lines)

        def __enter__(self) -> "_FakeHttpResponse":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

    async def _run() -> None:
        call_count = 0

        def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeHttpResponse:
            nonlocal call_count
            url = req.full_url
            if url.endswith("/v1/models"):
                payload = json.dumps(
                    {
                        "object": "list",
                        "data": [
                            {"id": "proxy-alpha", "object": "model"},
                            {"id": "proxy-beta", "object": "model"},
                        ],
                    }
                ).encode("utf-8")
                return _FakeHttpResponse(200, "application/json", [payload])

            if url.endswith("/v1/chat/completions"):
                call_count += 1
                if call_count == 1:
                    chunks = [
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "reasoning_content": "Reading hello.txt first.",
                                    }
                                }
                            ]
                        },
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "call_read_1",
                                                "type": "function",
                                                "function": {
                                                    "name": "fs_read",
                                                    "arguments": json.dumps(
                                                        {"path": "hello.txt"}
                                                    ),
                                                },
                                            }
                                        ]
                                    }
                                }
                            ],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                        },
                    ]
                else:
                    chunks = [
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "content": "File content is: Hello from workspace!",
                                    }
                                }
                            ],
                            "usage": {"prompt_tokens": 25, "completion_tokens": 10},
                        }
                    ]
                sse_lines = [f"data: {json.dumps(ch)}\n".encode("utf-8") for ch in chunks]
                sse_lines.append(b"data: [DONE]\n")
                return _FakeHttpResponse(200, "text/event-stream", sse_lines)

            raise AssertionError(f"Unexpected URL: {url}")

        old_url = litellm_server.os.environ.get("LITELLM_BASE_URL")
        old_key = litellm_server.os.environ.get("LITELLM_API_KEY")
        old_home = litellm_server.os.environ.get("KIROCREW_HOME")
        old_aiohttp = litellm_server.aiohttp
        old_urlopen = litellm_server.urllib.request.urlopen

        litellm_server.os.environ["LITELLM_BASE_URL"] = "http://litellm.local:4000"
        litellm_server.os.environ["LITELLM_API_KEY"] = "sk-test-123"
        litellm_server.os.environ["KIROCREW_HOME"] = str(tmp_path / "crew_home")
        litellm_server.aiohttp = None
        litellm_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]

        sent_frames: list[dict[str, Any]] = []
        server = litellm_server.LitellmAcpServer(agent_name="test_isolated_agent")

        async def _fake_send(payload: dict[str, Any]) -> None:
            sent_frames.append(payload)
            if payload.get("method") == litellm_server.METHOD_REQUEST_PERMISSION:
                req_id = payload["id"]
                await server.handle_incoming_line(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "outcome": {
                                    "outcome": "selected",
                                    "optionId": litellm_server.OPTION_ALLOW_ONCE,
                                }
                            },
                        }
                    )
                )

        server.send_message = _fake_send  # type: ignore[method-assign]

        try:
            init_res = await server.handle_initialize({})
            assert init_res["protocolVersion"] == litellm_server.PROTOCOL_VERSION

            new_res = await server._create_or_load_session({"cwd": str(tmp_path)})
            sid = new_res["sessionId"]
            avail_ids = [m["modelId"] for m in new_res["models"]["availableModels"]]
            assert "proxy-alpha" in avail_ids
            assert "proxy-beta" in avail_ids

            prompt_res = await server.handle_prompt(
                {
                    "sessionId": sid,
                    "prompt": [{"type": "text", "text": "Read hello.txt please"}],
                }
            )
            assert prompt_res["stopReason"] == litellm_server.STOP_REASON_END_TURN
            assert prompt_res["usage"]["inputTokens"] == 37
            assert prompt_res["usage"]["outputTokens"] == 18

            updates = [
                f["params"]["update"]
                for f in sent_frames
                if f.get("method") == litellm_server.METHOD_SESSION_UPDATE
            ]
            update_kinds = [u["sessionUpdate"] for u in updates]
            assert litellm_server.UPDATE_AGENT_THOUGHT_CHUNK in update_kinds
            assert litellm_server.UPDATE_TOOL_CALL in update_kinds
            assert litellm_server.UPDATE_TOOL_CALL_UPDATE in update_kinds
            assert litellm_server.UPDATE_AGENT_MESSAGE_CHUNK in update_kinds

            text_chunks = [
                u["content"]["text"]
                for u in updates
                if u["sessionUpdate"] == litellm_server.UPDATE_AGENT_MESSAGE_CHUNK
            ]
            assert "".join(text_chunks) == "File content is: Hello from workspace!"
        finally:
            litellm_server.aiohttp = old_aiohttp
            litellm_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
            if old_url is None:
                litellm_server.os.environ.pop("LITELLM_BASE_URL", None)
            else:
                litellm_server.os.environ["LITELLM_BASE_URL"] = old_url
            if old_key is None:
                litellm_server.os.environ.pop("LITELLM_API_KEY", None)
            else:
                litellm_server.os.environ["LITELLM_API_KEY"] = old_key
            if old_home is None:
                litellm_server.os.environ.pop("KIROCREW_HOME", None)
            else:
                litellm_server.os.environ["KIROCREW_HOME"] = old_home

    asyncio.run(_run())
