"""Tests for the built-in Sao Khuê ACP server (saokhue-cli) and harness registration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from kiro_crew.acp import saokhue_server
from kiro_crew.agent_sdk import backend_install, host_auth, native_allow_stores
from kiro_crew.agent_sdk.backends import (
    ACP_BACKEND_ROUTING,
    ACP_BACKEND_SAOKHUE,
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


def test_saokhue_backend_vocabulary_and_declarations() -> None:
    from kiro_crew import security

    assert ACP_BACKEND_SAOKHUE == "saokhue"
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_KNOWN
    assert ACP_BACKEND_SAOKHUE in BASELINE_SELECTABLE_BACKENDS
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_INDEPENDENT_SETUP
    assert ACP_BACKEND_SAOKHUE not in ACP_BACKENDS_ACP_RUNTIME
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_ACP_CLIENT_SPAWNABLE
    assert POLICY_ID_BY_BACKEND[ACP_BACKEND_SAOKHUE] == "saokhue"
    assert ACP_BACKEND_ROUTING[ACP_BACKEND_SAOKHUE] is Routing.AGENT_SPEC
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_SPEC_FILE_GRANTS
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_SPEC_SERVERS_OFF_WIRE
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_HONOR_ZERO_TOOL_BAN
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_MODEL_VIA_CONFIG_OPTION
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_ADVERTISED_MODEL_SELECTION
    assert ACP_BACKEND_SAOKHUE in ACP_BACKENDS_REPORT_USAGE
    assert model_registry_namespace(ACP_BACKEND_SAOKHUE) == "saokhue"

    assert host_auth.missing_declarations() == ()
    decl = host_auth.declaration_for(ACP_BACKEND_SAOKHUE)
    assert decl.backend == ACP_BACKEND_SAOKHUE
    assert decl.entitlement_source == host_auth.ENTITLEMENT_OWN_CREDENTIAL_FILE
    assert decl.credential_leaves == (".saokhue-cli/auth.json",)
    assert decl.home_override_env_vars == ("SAOKHUE_CLI_HOME",)
    assert decl.override_relative_leaves == ("auth.json",)
    assert ".saokhue-cli/auth.json" in security.sensitive_home_dirs()
    assert "saokhue-cli login" in decl.sign_in_remedy
    assert "saokhue-cli login" in decl.signed_out_message
    assert "--" not in decl.sign_in_remedy
    assert "`" not in decl.sign_in_remedy
    assert host_auth.reports_signed_out(
        ACP_BACKEND_SAOKHUE,
        "Not signed in to Sao Khuê (HTTP 401). Run `saokhue-cli login` in your terminal.",
    )

    assert ACP_BACKEND_SAOKHUE in native_allow_stores.NATIVE_ALLOW_STORES_NONE
    state = backend_install.probe_backend(ACP_BACKEND_SAOKHUE)
    assert state.installed == backend_install.INSTALLED
    assert state.policy_id == "saokhue"


def test_resolve_saokhue_settings_normalizes_url_and_env(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "saokhue_cli")
    base_url, api_key, default_model, timeout = saokhue_server.resolve_saokhue_settings(
        {
            "SAOKHUE_CLI_HOME": cli_home,
            "SAOKHUE_BASE_URL": "http://127.0.0.1:4000/v1/",
            "SAOKHUE_API_KEY": "sk-saokhue-test",
            "SAOKHUE_MODEL": "my-proxy-model",
            "SAOKHUE_TIMEOUT": "90",
        }
    )
    assert base_url == "http://127.0.0.1:4000"
    assert api_key == "sk-saokhue-test"
    assert default_model == "my-proxy-model"
    assert timeout == 90.0


def test_saokhue_cli_per_user_login_password_token_and_logout(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "user_saokhue_home")
    old_urlopen = saokhue_server.urllib.request.urlopen
    old_cli_home = saokhue_server.os.environ.get("SAOKHUE_CLI_HOME")
    saokhue_server.os.environ["SAOKHUE_CLI_HOME"] = cli_home

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

    def _fake_urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> _FakeResp:
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

    saokhue_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
    try:
        rc = saokhue_server.main(
            [
                "login",
                "--url",
                "http://saokhue.company.local:4000",
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

        # Verify resolve_saokhue_settings reads the logged-in user's token
        url, tok, model, _ = saokhue_server.resolve_saokhue_settings({"SAOKHUE_CLI_HOME": cli_home})
        assert url == "http://saokhue.company.local:4000"
        assert tok == "jwt-user-viet-123"
        assert model == "company-claude-sonnet"

        # Status and models commands succeed
        assert saokhue_server.main(["status"]) == 0
        assert saokhue_server.main(["models"]) == 0

        # Logout removes auth.json
        assert saokhue_server.main(["logout"]) == 0
        assert not auth_file.exists()
        assert saokhue_server.main(["status"]) == 1
    finally:
        saokhue_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
        if old_cli_home is None:
            saokhue_server.os.environ.pop("SAOKHUE_CLI_HOME", None)
        else:
            saokhue_server.os.environ["SAOKHUE_CLI_HOME"] = old_cli_home


def test_saokhue_cli_vpbank_oidc_device_flow_and_userinfo(tmp_path: Path) -> None:
    cli_home = str(tmp_path / "vpbank_oidc_home")
    old_urlopen = saokhue_server.urllib.request.urlopen
    old_sleep = saokhue_server.time.sleep
    old_cli_home = saokhue_server.os.environ.get("SAOKHUE_CLI_HOME")
    saokhue_server.os.environ["SAOKHUE_CLI_HOME"] = cli_home
    saokhue_server.time.sleep = lambda _s: None  # type: ignore[assignment]

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

    def _fake_urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> _FakeResp:
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

    saokhue_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]
    try:
        rc = saokhue_server.main(["login", "--use-device-flow", "--no-browser"])
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

        assert saokhue_server.main(["logout"]) == 0
        assert not auth_file.exists()
    finally:
        saokhue_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
        saokhue_server.time.sleep = old_sleep  # type: ignore[assignment]
        if old_cli_home is None:
            saokhue_server.os.environ.pop("SAOKHUE_CLI_HOME", None)
        else:
            saokhue_server.os.environ["SAOKHUE_CLI_HOME"] = old_cli_home


def test_saokhue_acp_server_session_and_tool_permission_roundtrip(tmp_path: Path) -> None:
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

        def _fake_urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> _FakeHttpResponse:
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
                                                    "arguments": json.dumps({"path": "hello.txt"}),
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

        old_url = saokhue_server.os.environ.get("SAOKHUE_BASE_URL")
        old_key = saokhue_server.os.environ.get("SAOKHUE_API_KEY")
        old_home = saokhue_server.os.environ.get("KIROCREW_HOME")
        old_aiohttp = saokhue_server.aiohttp
        old_urlopen = saokhue_server.urllib.request.urlopen

        saokhue_server.os.environ["SAOKHUE_BASE_URL"] = "http://saokhue.local:4000"
        saokhue_server.os.environ["SAOKHUE_API_KEY"] = "sk-test-123"
        saokhue_server.os.environ["KIROCREW_HOME"] = str(tmp_path / "crew_home")
        saokhue_server.aiohttp = None
        saokhue_server.urllib.request.urlopen = _fake_urlopen  # type: ignore[assignment]

        sent_frames: list[dict[str, Any]] = []
        server = saokhue_server.SaokhueAcpServer(agent_name="test_isolated_agent")

        async def _fake_send(payload: dict[str, Any]) -> None:
            sent_frames.append(payload)
            if payload.get("method") == saokhue_server.METHOD_REQUEST_PERMISSION:
                req_id = payload["id"]
                await server.handle_incoming_line(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "outcome": {
                                    "outcome": "selected",
                                    "optionId": saokhue_server.OPTION_ALLOW_ONCE,
                                }
                            },
                        }
                    )
                )

        server.send_message = _fake_send  # type: ignore[method-assign]

        try:
            init_res = await server.handle_initialize({})
            assert init_res["protocolVersion"] == saokhue_server.PROTOCOL_VERSION

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
            assert prompt_res["stopReason"] == saokhue_server.STOP_REASON_END_TURN
            assert prompt_res["usage"]["inputTokens"] == 37
            assert prompt_res["usage"]["outputTokens"] == 18

            updates = [
                f["params"]["update"]
                for f in sent_frames
                if f.get("method") == saokhue_server.METHOD_SESSION_UPDATE
            ]
            update_kinds = [u["sessionUpdate"] for u in updates]
            assert saokhue_server.UPDATE_AGENT_THOUGHT_CHUNK in update_kinds
            assert saokhue_server.UPDATE_TOOL_CALL in update_kinds
            assert saokhue_server.UPDATE_TOOL_CALL_UPDATE in update_kinds
            assert saokhue_server.UPDATE_AGENT_MESSAGE_CHUNK in update_kinds

            text_chunks = [
                u["content"]["text"]
                for u in updates
                if u["sessionUpdate"] == saokhue_server.UPDATE_AGENT_MESSAGE_CHUNK
            ]
            assert "".join(text_chunks) == "File content is: Hello from workspace!"

            # Verify execute_bash built-in tool resolves cwd (str from Workspace.resolve) and runs
            session_obj = server._sessions[sid]
            bash_out, bash_err = await server._execute_builtin_tool(
                session_obj,
                "execute_bash",
                {"command": "echo test"},
            )
            assert not bash_err
            assert "exit_code=0" in bash_out
            assert "test" in bash_out

            # Verify execute_bash rejects a file path passed as cwd
            not_dir_out, not_dir_err = await server._execute_builtin_tool(
                session_obj,
                "execute_bash",
                {"command": "echo test", "cwd": "hello.txt"},
            )
            assert not_dir_err
            assert "Not a directory: hello.txt" in not_dir_out
        finally:
            saokhue_server.aiohttp = old_aiohttp
            saokhue_server.urllib.request.urlopen = old_urlopen  # type: ignore[assignment]
            if old_url is None:
                saokhue_server.os.environ.pop("SAOKHUE_BASE_URL", None)
            else:
                saokhue_server.os.environ["SAOKHUE_BASE_URL"] = old_url
            if old_key is None:
                saokhue_server.os.environ.pop("SAOKHUE_API_KEY", None)
            else:
                saokhue_server.os.environ["SAOKHUE_API_KEY"] = old_key
            if old_home is None:
                saokhue_server.os.environ.pop("KIROCREW_HOME", None)
            else:
                saokhue_server.os.environ["KIROCREW_HOME"] = old_home

    asyncio.run(_run())


def test_saokhue_dashboard_login_handlers(tmp_path: Path, monkeypatch: Any) -> None:
    from kiro_crew.acp import saokhue_server as pkg_saokhue_server
    from kiro_crew.dashboard.handlers import saokhue_login

    cli_home = str(tmp_path / "dashboard_saokhue_home")
    monkeypatch.setenv("SAOKHUE_CLI_HOME", cli_home)
    monkeypatch.setattr(saokhue_login, "is_owner_dashboard_request", lambda _req: True)

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

    def _fake_urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> _FakeResp:
        url = req.full_url
        if url.endswith("/v1/models"):
            return _FakeResp(200, {"data": [{"id": "sk-model-1"}, {"id": "sk-model-2"}]})
        if url.endswith("/user/info"):
            return _FakeResp(200, {"user_id": "vietbn", "email": "vietbn@vpbank.com.vn"})
        if url.endswith("/api/idpOAuth/device/auth"):
            return _FakeResp(
                200,
                {
                    "device_code": "dev-123",
                    "user_code": "ABCD-9999",
                    "verification_uri": "https://portal.aws.rpa.vpbank.dev/device",
                    "verification_uri_complete": "https://portal.aws.rpa.vpbank.dev/device?user_code=ABCD-9999",
                    "interval": 2,
                    "expires_in": 300,
                },
            )
        if url.endswith("/api/idpOAuth/token"):
            return _FakeResp(
                200,
                {
                    "access_token": "dev-jwt-token",
                    "refresh_token": "dev-refresh-token",
                    "expires_in": 3600,
                    "user_id": "vietbn",
                    "email": "vietbn@vpbank.com.vn",
                },
            )
        if url.endswith("/api/idpOAuth/revoke"):
            return _FakeResp(200, {"ok": True})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(pkg_saokhue_server.urllib.request, "urlopen", _fake_urlopen)

    class _FakeReq:
        def __init__(self, app: dict[str, Any], body: dict[str, Any] | None = None) -> None:
            self.app = app
            self.path = "/api/saokhue-login"
            self.query: dict[str, str] = {}
            self._body = body or {}

        def get(self, key: str, default: Any = None) -> Any:
            return default

        async def json(self) -> dict[str, Any]:
            return self._body

    async def _run_dashboard() -> None:
        app_state: dict[str, Any] = {}

        # 1. Initial status is unauthenticated
        r1 = await saokhue_login.api_saokhue_login_status(_FakeReq(app_state))  # type: ignore[arg-type]
        d1 = json.loads(r1.body.decode("utf-8"))
        assert d1["authenticated"] is False

        # 2. Token login works without CMD
        r2 = await saokhue_login.api_saokhue_login_token(
            _FakeReq(
                app_state,
                {
                    "base_url": "https://portal.aws.rpa.vpbank.dev/api/ai-llm",
                    "token": "sk-personal-token-99",
                    "user_id": "vietbn",
                },
            )  # type: ignore[arg-type]
        )
        d2 = json.loads(r2.body.decode("utf-8"))
        assert d2["ok"] is True
        assert d2["authenticated"] is True
        assert d2["user_id"] == "vietbn"
        assert d2["models"] == ["sk-model-1", "sk-model-2"]
        assert "access_token" not in d2

        # 3. Model update works
        r3 = await saokhue_login.api_saokhue_login_model(
            _FakeReq(app_state, {"default_model": "sk-model-2"})  # type: ignore[arg-type]
        )
        d3 = json.loads(r3.body.decode("utf-8"))
        assert d3["default_model"] == "sk-model-2"

        # 4. Device code start + poll works without CMD
        r4 = await saokhue_login.api_saokhue_login_device_start(
            _FakeReq(app_state, {"base_url": "https://portal.aws.rpa.vpbank.dev/api/ai-llm"})  # type: ignore[arg-type]
        )
        d4 = json.loads(r4.body.decode("utf-8"))
        assert d4["ok"] is True
        assert d4["user_code"] == "ABCD-9999"
        login_id = d4["login_id"]

        r5 = await saokhue_login.api_saokhue_login_device_poll(
            _FakeReq(app_state, {"login_id": login_id})  # type: ignore[arg-type]
        )
        d5 = json.loads(r5.body.decode("utf-8"))
        assert d5["ok"] is True
        assert d5["status"] == "authorized"
        assert d5["authenticated"] is True
        assert d5["auth_method"] == "device_code"

        # 5. Logout clears credentials
        r6 = await saokhue_login.api_saokhue_login_logout(_FakeReq(app_state))  # type: ignore[arg-type]
        d6 = json.loads(r6.body.decode("utf-8"))
        assert d6["ok"] is True
        assert d6["authenticated"] is False
        assert not (Path(cli_home) / "auth.json").exists()

    asyncio.run(_run_dashboard())


def _raise_untrusted(*_args: Any, **_kwargs: Any) -> Any:
    import ssl

    raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")


def test_urlopen_never_retries_a_certificate_failure_unverified(monkeypatch: Any) -> None:
    trusted = object()
    contexts: list[Any] = []

    def _urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> Any:
        contexts.append(context)
        return _raise_untrusted()

    monkeypatch.delenv("SAOKHUE_SSL_VERIFY", raising=False)
    monkeypatch.setattr(saokhue_server, "_build_system_ssl_context", lambda: trusted)
    monkeypatch.setattr(saokhue_server.urllib.request, "urlopen", _urlopen)

    try:
        saokhue_server._urlopen_resilient("https://portal.example.test/v1/models")
    except Exception as exc:
        assert saokhue_server._is_ssl_cert_error(exc)
    else:
        raise AssertionError("an untrusted certificate must fail the request")
    assert contexts == [trusted]


def test_urlopen_skips_verification_only_when_the_operator_turns_it_off(monkeypatch: Any) -> None:
    import ssl

    contexts: list[Any] = []

    def _urlopen(req: Any, timeout: float = 0.0, context: Any = None) -> str:
        contexts.append(context)
        return "opened"

    monkeypatch.setenv("SAOKHUE_SSL_VERIFY", "0")
    monkeypatch.setattr(saokhue_server.urllib.request, "urlopen", _urlopen)

    assert saokhue_server._urlopen_resilient("https://portal.example.test/") == "opened"
    assert contexts[0].verify_mode == ssl.CERT_NONE


class _RecordingAiohttp:
    """Stands in for the ``aiohttp`` module: records each request's keyword arguments."""

    def __init__(self, respond: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._respond = respond

    def ClientTimeout(self, **_kwargs: Any) -> None:  # noqa: N802 - aiohttp's name
        return None

    def ClientSession(self, **_kwargs: Any) -> "_RecordingAiohttp":  # noqa: N802
        return self

    async def __aenter__(self) -> "_RecordingAiohttp":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def _request(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        return self._respond()

    get = post = _request


def test_model_listing_goes_through_the_proxy_with_certificate_checks(monkeypatch: Any) -> None:
    trusted = object()
    fake = _RecordingAiohttp(_raise_untrusted)
    monkeypatch.delenv("SAOKHUE_SSL_VERIFY", raising=False)
    monkeypatch.setattr(saokhue_server, "aiohttp", fake)
    monkeypatch.setattr(saokhue_server, "_build_system_ssl_context", lambda: trusted)
    monkeypatch.setattr(saokhue_server, "resolve_proxy", lambda url: "http://proxy.test:8080")
    monkeypatch.setattr(saokhue_server, "load_saokhue_user_auth", lambda **_kwargs: {})

    models = asyncio.run(
        saokhue_server.SaokhueAcpServer._fetch_saokhue_models(
            None, "https://portal.example.test", "token"
        )
    )

    assert models == []
    assert [(c["ssl"], c["proxy"]) for c in fake.calls] == [(trusted, "http://proxy.test:8080")]


def test_a_patch_through_the_facade_reaches_the_owner_that_reads_the_name(
    monkeypatch: Any,
) -> None:
    from kiro_crew.saokhue_cli import auth, tls, turns

    fake = object()
    originals = (tls._build_system_ssl_context, auth._urlopen_resilient, turns.resolve_proxy)
    monkeypatch.setattr(saokhue_server, "_build_system_ssl_context", fake)
    monkeypatch.setattr(saokhue_server, "_urlopen_resilient", fake)
    monkeypatch.setattr(saokhue_server, "resolve_proxy", fake)

    assert tls._build_system_ssl_context is fake
    assert auth._urlopen_resilient is fake and tls._urlopen_resilient is fake
    assert turns.resolve_proxy is fake and saokhue_server.resolve_proxy is fake

    monkeypatch.undo()
    assert (
        tls._build_system_ssl_context,
        auth._urlopen_resilient,
        turns.resolve_proxy,
    ) == originals


def test_the_server_script_starts_without_the_acp_client_and_runtime() -> None:
    """Why the owners live in ``kiro_crew.saokhue_cli``: a script launch stays light."""
    import subprocess
    import sys

    from kiro_crew.acp.harness.saokhue import SAOKHUE_SERVER_SCRIPT
    from kiro_crew.subprocess_utf8 import UTF8_TEXT

    probe = (
        "import runpy, sys\n"
        "sys.argv = [sys.argv[1], '--help']\n"
        "try:\n"
        "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('LOADED', sorted(m for m in sys.modules if m.startswith('kiro_crew.acp')))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe, SAOKHUE_SERVER_SCRIPT],
        capture_output=True,
        timeout=60,
        **UTF8_TEXT,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.rstrip().endswith("LOADED []"), done.stdout[-300:]


def _bare_session(tmp_path: Path, **fields: Any) -> Any:
    from kiro_crew.saokhue_cli.turns import SaokhueSession

    values: dict[str, Any] = {
        "session_id": "sid-1",
        "cwd": str(tmp_path),
        "agent_name": "kirocrew",
        "system_prompt": "sys",
        "model": "model-a",
        "available_models": ["model-a", "vendor/model-b"],
        "models_listed": True,
    }
    values.update(fields)
    return SaokhueSession(**values)


def _server_capturing_frames(tmp_path: Path, monkeypatch: Any) -> tuple[Any, list[dict[str, Any]]]:
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "crew_home"))
    monkeypatch.setattr(saokhue_server, "_persist_preferred_model", lambda *_a, **_k: None)
    server = saokhue_server.SaokhueAcpServer()
    frames: list[dict[str, Any]] = []

    async def _capture(payload: dict[str, Any]) -> None:
        frames.append(payload)

    server.send_message = _capture  # type: ignore[method-assign]
    return server, frames


def test_a_stop_during_a_shell_tool_kills_the_command_it_started(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import sys

    from kiro_crew import platform_compat

    reaped: list[Any] = []
    real_reap = platform_compat.kill_and_reap

    async def _recording_reap(proc: Any, **kwargs: Any) -> None:
        reaped.append(proc)
        await real_reap(proc, **kwargs)

    monkeypatch.setattr(platform_compat, "kill_and_reap", _recording_reap)
    real_spawn = asyncio.create_subprocess_shell

    async def _run() -> None:
        spawned: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        async def _spawn(*args: Any, **kwargs: Any) -> Any:
            proc = await real_spawn(*args, **kwargs)
            spawned.set_result(proc)
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_shell", _spawn)
        server = saokhue_server.SaokhueAcpServer()
        session = _bare_session(tmp_path)
        command = f'"{sys.executable}" -c "import time; time.sleep(60)"'
        task = asyncio.ensure_future(
            server._execute_builtin_tool(session, "execute_bash", {"command": command})
        )
        proc = await asyncio.wait_for(spawned, timeout=30)
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=30)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("the cancelled tool call returned instead of re-raising")
        assert reaped == [proc]
        assert proc.returncode is not None

    asyncio.run(_run())


def test_unanswered_tool_calls_are_closed_after_the_results_that_arrived() -> None:
    from kiro_crew.saokhue_cli.turns import CANCELLED_TOOL_RESULT, close_unanswered_tool_calls

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "done"},
        {"role": "user", "content": "next"},
    ]

    assert close_unanswered_tool_calls(messages) is True
    assert [(m["role"], m.get("tool_call_id")) for m in messages] == [
        ("user", None),
        ("assistant", None),
        ("tool", "c1"),
        ("tool", "c2"),
        ("user", None),
    ]
    assert messages[3]["content"] == CANCELLED_TOOL_RESULT
    assert close_unanswered_tool_calls(messages) is False


def test_the_next_turn_after_a_stop_mid_tool_sends_a_complete_transcript(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server, _frames = _server_capturing_frames(tmp_path, monkeypatch)
    session = _bare_session(
        tmp_path,
        messages=[
            {"role": "user", "content": "run it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        ],
    )
    server._sessions[session.session_id] = session
    sent: list[list[dict[str, Any]]] = []

    async def _turn(_session: Any, messages: list[dict[str, Any]], _tools: Any) -> Any:
        sent.append(messages)
        return "ok", [], 1, 1, 0, 0, 0.0

    monkeypatch.setattr(server, "_stream_saokhue_turn", _turn)

    result = asyncio.run(server.handle_prompt({"sessionId": session.session_id, "prompt": "next"}))

    assert result["stopReason"] == saokhue_server.STOP_REASON_END_TURN
    assert [m["role"] for m in sent[0]] == ["system", "user", "assistant", "tool", "user"]
    assert sent[0][3]["tool_call_id"] == "call_1"


def _http_error(status: int, body: str) -> Exception:
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "http://saokhue.test/v1/chat/completions", status, "err", {}, io.BytesIO(body.encode())
    )


def _stream_with(tmp_path: Path, monkeypatch: Any, answer: Any) -> tuple[Any, list[str], Any]:
    from kiro_crew.saokhue_cli import turns

    tried: list[str] = []
    sse = [b'data: {"choices": [{"delta": {"content": "hi"}}]}\n', b"data: [DONE]\n"]

    class _Done:
        status = 200
        headers = {"Content-Type": "text/event-stream"}

        def __iter__(self) -> Any:
            return iter(sse)

        def __enter__(self) -> "_Done":
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

    def _urlopen(req: Any, timeout: float = 0.0) -> Any:
        model = json.loads(req.data)["model"]
        tried.append(model)
        error = answer(model)
        if error is not None:
            raise error
        return _Done()

    monkeypatch.setattr(turns, "aiohttp", None)
    monkeypatch.setattr(turns, "_urlopen_resilient", _urlopen)
    monkeypatch.setattr(
        turns, "resolve_saokhue_settings", lambda: ("http://saokhue.test", "k", "", 30.0)
    )
    monkeypatch.setattr(turns, "load_saokhue_user_auth", lambda **_k: {})
    monkeypatch.setattr(turns, "_persist_preferred_model", lambda *_a, **_k: None)
    server, _frames = _server_capturing_frames(tmp_path, monkeypatch)
    session = _bare_session(tmp_path)
    run = turns._stream_saokhue_turn(server, session, [{"role": "user", "content": "x"}], [])
    return run, tried, session


def test_an_error_that_is_not_about_the_model_keeps_the_session_on_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew.saokhue_cli.turns import SaokhueHttpError

    run, tried, session = _stream_with(
        tmp_path,
        monkeypatch,
        lambda _model: _http_error(400, '{"error": "tool_calls must be followed by tool"}'),
    )

    try:
        asyncio.run(run)
    except SaokhueHttpError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("a refused transcript was reported as a reply")
    assert tried == ["model-a"]
    assert session.model == "model-a"


def test_a_model_the_endpoint_cannot_serve_falls_back_to_the_next_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    run, tried, session = _stream_with(
        tmp_path,
        monkeypatch,
        lambda model: (
            _http_error(404, '{"error": {"code": "model_not_found"}}')
            if model == "model-a"
            else None
        ),
    )

    reply = asyncio.run(run)

    assert reply[0] == "hi"
    assert tried == ["model-a", "vendor/model-b"]
    assert session.model == "vendor/model-b"


def test_a_pick_the_endpoint_does_not_list_is_refused_as_a_config_value(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew.acp.client_model_selection import _is_config_value_rejection
    from kiro_crew.acp.transport_errors import AcpError

    server, frames = _server_capturing_frames(tmp_path, monkeypatch)
    session = _bare_session(tmp_path)
    server._sessions[session.session_id] = session

    def _pick(value: str) -> dict[str, Any]:
        asyncio.run(
            server._dispatch_request(
                len(frames) + 1,
                saokhue_server.METHOD_SET_CONFIG_OPTION,
                {"sessionId": session.session_id, "configId": "model", "value": value},
            )
        )
        return frames[-1]

    refused = _pick("not-served")
    assert refused["error"]["code"] == -32602
    assert session.model == "model-a"
    rejection = AcpError(refused["error"]["message"], code=refused["error"]["code"])
    assert _is_config_value_rejection(rejection, "model", ACP_BACKEND_SAOKHUE)

    accepted = _pick("MODEL-B")
    assert accepted["result"]["configOptions"][0]["currentValue"] == "vendor/model-b"
    assert session.model == "vendor/model-b"


def test_a_pick_is_taken_as_given_when_the_endpoint_listed_no_models(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server, _frames = _server_capturing_frames(tmp_path, monkeypatch)
    session = _bare_session(tmp_path, available_models=["model-a"], models_listed=False)
    server._sessions[session.session_id] = session

    asyncio.run(
        server.handle_set_config_option(
            {"sessionId": session.session_id, "configId": "model", "value": "private-model"}
        )
    )

    assert session.model == "private-model"


def test_a_session_the_endpoint_listed_nothing_for_advertises_no_placeholder(
    tmp_path: Path, monkeypatch: Any
) -> None:
    server, _frames = _server_capturing_frames(tmp_path, monkeypatch)
    monkeypatch.delenv("SAOKHUE_MODEL", raising=False)

    async def _nothing_listed(*_args: Any) -> list[str]:
        return []

    monkeypatch.setattr(server, "_fetch_saokhue_models", _nothing_listed)
    monkeypatch.setattr(server, "_load_agent_spec", lambda *_a: {})

    payload = asyncio.run(server._create_or_load_session({"cwd": str(tmp_path)}))

    assert payload["models"]["availableModels"] == []
    assert payload["configOptions"][0]["options"] == []
    assert server._sessions[payload["sessionId"]].models_listed is False


class _RecordingState:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    def broadcast_ws(self, event: str, payload: dict[str, Any]) -> None:
        self.frames.append((event, payload))


class _FakeRequest:
    def __init__(self, state: _RecordingState) -> None:
        self.app = {"state": state}


def _login_status(monkeypatch: Any, payload: dict[str, Any]) -> tuple[_RecordingState, Any]:
    from kiro_crew import model_registry
    from kiro_crew.dashboard.handlers import saokhue_login

    monkeypatch.setattr(model_registry, "_ADVERTISED_MODELS", {})
    monkeypatch.setattr(model_registry, "_ADVERTISED_META", {})
    monkeypatch.setattr(model_registry, "persist_advertised_models", lambda: None)
    monkeypatch.setattr(saokhue_login, "_build_status_sync", lambda **_k: dict(payload))
    state = _RecordingState()
    asyncio.run(saokhue_login._status(_FakeRequest(state)))  # type: ignore[arg-type]
    return state, model_registry


def test_signing_in_hands_the_account_models_to_the_chat_picker(monkeypatch: Any) -> None:
    from kiro_crew.dashboard.handlers.models_cold_probe import MODELS_CHANGED_EVENT

    state, registry = _login_status(
        monkeypatch,
        {"authenticated": True, "models": ["model-a", "saokhue-default", "vendor/model-b"]},
    )

    assert registry.advertised_models("saokhue") == ["model-a", "vendor/model-b"]
    assert state.frames == [(MODELS_CHANGED_EVENT, {})]


def test_a_signed_out_status_leaves_the_picker_list_alone(monkeypatch: Any) -> None:
    state, registry = _login_status(monkeypatch, {"authenticated": False, "models": ["model-a"]})

    assert registry.advertised_models("saokhue") == []
    assert state.frames == []
