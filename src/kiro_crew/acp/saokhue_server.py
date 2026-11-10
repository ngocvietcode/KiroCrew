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
import contextlib
import json
import logging
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from kiro_crew.config.paths import config_dir, kiro_agents_dir
from kiro_crew.http_proxy import resolve_proxy
from kiro_crew.remote_session import workspace_tools
from kiro_crew.saokhue_cli import turns as _sk_turns
from kiro_crew.saokhue_cli.auth import (
    _persist_preferred_model,
    _select_valid_saokhue_model,
    load_saokhue_user_auth,
    resolve_saokhue_settings,
)
from kiro_crew.saokhue_cli.base import (
    BUILTIN_TOOL_KINDS,
    BUILTIN_TOOL_SCHEMAS,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_OIDC_AUTHORIZE_URL,
    DEFAULT_OIDC_CLIENT_ID,
    DEFAULT_OIDC_DEVICE_AUTH_URL,
    DEFAULT_OIDC_RESOURCE,
    DEFAULT_OIDC_SCOPE,
    DEFAULT_OIDC_TOKEN_URL,
    DEFAULT_OIDC_USERINFO_URL,
    DEFAULT_SAOKHUE_BASE_URL,
    MAX_AGENT_TURNS,
    METHOD_AGENT_SWITCHED,
    METHOD_CANCEL,
    METHOD_INITIALIZE,
    METHOD_MCP_SERVER_INIT_FAILURE,
    METHOD_MCP_SERVER_INITIALIZED,
    METHOD_METADATA,
    METHOD_PROMPT,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_CLOSE,
    METHOD_SESSION_LOAD,
    METHOD_SESSION_NEW,
    METHOD_SESSION_RESUME,
    METHOD_SESSION_TERMINATE,
    METHOD_SESSION_UPDATE,
    METHOD_SET_CONFIG_OPTION,
    METHOD_SET_MODE,
    METHOD_SET_MODEL,
    MODEL_CONFIG_ID,
    OPTION_ALLOW_ALWAYS,
    OPTION_ALLOW_ONCE,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    STOP_REASON_CANCELLED,
    STOP_REASON_END_TURN,
    UPDATE_TOOL_CALL,
    UPDATE_TOOL_CALL_UPDATE,
    UPDATE_USAGE,
    aiohttp,
    saokhue_auth_path,
)
from kiro_crew.saokhue_cli.cli import _cli_login, _cli_logout, _cli_models, _cli_status
from kiro_crew.saokhue_cli.mcp_client import StdioMcpClient
from kiro_crew.saokhue_cli.tls import (
    _aiohttp_ssl,
    _urlopen_resilient,
    _warn_untrusted_certificate,
    ensure_system_ca_bundle,
)
from kiro_crew.saokhue_cli.turns import SaokhueSession

logger = logging.getLogger("kiro_crew.acp.saokhue_server")


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
        """Query ``GET {base_url}/v1/models`` for available model IDs, with cached auth fallback."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{base_url}/v1/models"
        try:
            if aiohttp is not None:
                timeout = aiohttp.ClientTimeout(total=8.0)
                data = None
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as http:
                        async with http.get(
                            url, headers=headers, ssl=_aiohttp_ssl(), proxy=resolve_proxy(url)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                except Exception as exc:
                    _warn_untrusted_certificate(exc, url)
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
        wire_servers = (
            params.get("mcpServers") if isinstance(params.get("mcpServers"), list) else []
        )
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
            resolved_model = _select_valid_saokhue_model(value, session.available_models, env_model)
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
                        "description": str(
                            schema_obj.get("description") or f"{srv_name}.{orig_name}"
                        ),
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
                return opt in (
                    OPTION_ALLOW_ONCE,
                    OPTION_ALLOW_ALWAYS,
                    "allow_once",
                    "allow_always",
                    "allow",
                )
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

    # Owner: kiro_crew.saokhue_cli.turns
    _stream_saokhue_turn = _sk_turns._stream_saokhue_turn

    _stream_saokhue_turn_urllib = _sk_turns._stream_saokhue_turn_urllib

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

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_text or "",
                }
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


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(Exception):
        ensure_system_ca_bundle()
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Support `saokhue-cli auth login` / `saokhue-cli auth logout` / `saokhue-cli auth status`
    if (
        len(raw_argv) >= 2
        and raw_argv[0] == "auth"
        and raw_argv[1] in ("login", "logout", "status", "whoami")
    ):
        raw_argv = raw_argv[1:]

    if raw_argv and raw_argv[0] in ("login", "logout", "status", "whoami", "models"):
        cmd = raw_argv[0]
        sub_argv = raw_argv[1:]
        if cmd == "login":
            p = argparse.ArgumentParser(
                prog="saokhue-cli login", description="Sign in to Sao Khuê per user"
            )
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
            p.add_argument(
                "--callback-port", type=int, default=0, help="Local callback port for SSO"
            )
            p.add_argument(
                "--no-browser",
                action="store_true",
                help="Do not auto-open browser for SSO/Device flow",
            )
            p.add_argument("--token", "-t", default="", help="Personal access token or virtual key")
            p.add_argument(
                "--model", "-m", default="", help="Default model ID to save for this user"
            )
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


import importlib as _importlib
from typing import TYPE_CHECKING

from kiro_crew import _facade_mirror

#: Owner modules, lowest layer first: the first holder of a name answers its reads.
_OWNERS = (
    "kiro_crew.saokhue_cli.base",
    "kiro_crew.saokhue_cli.tls",
    "kiro_crew.saokhue_cli.auth",
    "kiro_crew.saokhue_cli.mcp_client",
    "kiro_crew.saokhue_cli.turns",
    "kiro_crew.saokhue_cli.cli",
)
for _owner in _OWNERS:
    _importlib.import_module(_owner)
#: Name the facade serves but does not bind -> the owner that answers a read of it.
_EXPORTS: dict[str, str] = {}
_EXPORTS.update(_facade_mirror.install(sys.modules[__name__], _OWNERS, excluded=()).served)
sys.modules[__name__].__class__ = _facade_mirror.MirroredModule

if TYPE_CHECKING:  # every owner-defined name in _EXPORTS, for type checkers and IDEs
    from kiro_crew.saokhue_cli.auth import (  # noqa: F401
        _NON_CHAT_MODEL_SUBSTRINGS,
        _decode_jwt_claims,
        _extract_token_from_response,
        _is_likely_chat_model,
        _load_dotenv_into_dict,
        _normalize_base_url,
        _probe_oidc_userinfo_sync,
        _probe_user_and_models_sync,
        _query_models_endpoint_sync,
        clear_saokhue_user_auth,
        login_with_browser_sso,
        login_with_device_code,
        login_with_password,
        login_with_token,
        poll_device_code_once,
        refresh_saokhue_user_token,
        resolve_oidc_endpoints,
        save_saokhue_user_auth,
        start_device_code_flow,
    )
    from kiro_crew.saokhue_cli.base import (  # noqa: F401
        DEFAULT_OIDC_END_SESSION_URL,
        DEFAULT_OIDC_ISSUER,
        DEFAULT_OIDC_REGISTER_URL,
        DEFAULT_OIDC_REVOKE_URL,
        MCP_CALL_TIMEOUT_SECS,
        MCP_CONNECT_TIMEOUT_SECS,
        UPDATE_AGENT_MESSAGE_CHUNK,
        UPDATE_AGENT_THOUGHT_CHUNK,
        saokhue_cli_home,
    )
    from kiro_crew.saokhue_cli.tls import (  # noqa: F401
        _CA_BUNDLE_ENV_VARS,
        _SYSTEM_CA_BUNDLE_CACHE,
        _SYSTEM_SSL_CONTEXT_CACHE,
        _build_system_ssl_context,
        _is_ssl_cert_error,
        _tls_verification_disabled,
    )
    from kiro_crew.saokhue_cli.turns import (  # noqa: F401
        _estimate_messages_tokens,
        _extract_usage_fields,
        _stream_saokhue_turn,
        _stream_saokhue_turn_urllib,
    )


if __name__ == "__main__":
    raise SystemExit(main())
