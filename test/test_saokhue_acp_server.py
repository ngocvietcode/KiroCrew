"""Tests for the built-in Sao Khuê ACP server (saokhue-cli) and harness registration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

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


def _load_saokhue_server_module() -> Any:
    import sys

    script_path = (
        Path(__file__).resolve().parents[1] / "src" / "kiro_crew" / "acp" / "saokhue_server.py"
    )
    spec = importlib.util.spec_from_file_location("kiro_crew_saokhue_server", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


saokhue_server = _load_saokhue_server_module()


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

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
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
