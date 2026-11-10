"""The stdio MCP client ``saokhue-cli`` mounts an agent's MCP servers with.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from kiro_crew.saokhue_cli.base import (
    MCP_CALL_TIMEOUT_SECS,
    MCP_CONNECT_TIMEOUT_SECS,
    SERVER_NAME,
    SERVER_VERSION,
)

logger = logging.getLogger("kiro_crew.acp.saokhue_server")


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
