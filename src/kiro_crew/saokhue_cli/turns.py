"""One model turn of ``saokhue-cli``: the session state and the Chat Completions stream.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kiro_crew.http_proxy import resolve_proxy
from kiro_crew.saokhue_cli.auth import (
    _is_likely_chat_model,
    _persist_preferred_model,
    _select_valid_saokhue_model,
    load_saokhue_user_auth,
    resolve_saokhue_settings,
)
from kiro_crew.saokhue_cli.base import (
    DEFAULT_FALLBACK_MODEL,
    METHOD_SESSION_UPDATE,
    UPDATE_AGENT_MESSAGE_CHUNK,
    UPDATE_AGENT_THOUGHT_CHUNK,
    aiohttp,
)
from kiro_crew.saokhue_cli.mcp_client import StdioMcpClient
from kiro_crew.saokhue_cli.tls import (
    _aiohttp_ssl,
    _is_ssl_cert_error,
    _urlopen_resilient,
    _warn_untrusted_certificate,
)

if TYPE_CHECKING:
    from kiro_crew.acp.saokhue_server import SaokhueAcpServer

logger = logging.getLogger("kiro_crew.acp.saokhue_server")


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
    #: Whether ``available_models`` came from the endpoint (or the login's cached
    #: list) rather than being only the model the session started on. Only then can
    #: a pick be checked against it.
    models_listed: bool = False


#: The result recorded for a tool call that a Stop ended before it answered.
CANCELLED_TOOL_RESULT = "Tool call was cancelled by the user before it finished."


def close_unanswered_tool_calls(messages: list[dict[str, Any]]) -> bool:
    """Give every assistant ``tool_calls`` entry a ``tool`` result; True if any was added.

    Chat Completions refuses a transcript in which an assistant message's tool
    calls are not each followed by their ``tool`` message. A Stop that lands
    between the assistant message and its results would otherwise leave every
    later turn of the session refused. The missing results are recorded as
    cancelled, after the results that did arrive.
    """
    repaired: list[dict[str, Any]] = []
    changed = False
    i = 0
    while i < len(messages):
        msg = messages[i]
        repaired.append(msg)
        i += 1
        calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
        if not isinstance(calls, list) or not calls:
            continue
        answered: set[Any] = set()
        while i < len(messages) and messages[i].get("role") == "tool":
            answered.add(messages[i].get("tool_call_id"))
            repaired.append(messages[i])
            i += 1
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            if call_id and call_id not in answered:
                repaired.append(
                    {"role": "tool", "tool_call_id": call_id, "content": CANCELLED_TOOL_RESULT}
                )
                changed = True
    if changed:
        messages[:] = repaired
    return changed


class SaokhueHttpError(RuntimeError):
    """An error status the Sao Khuê endpoint answered a chat request with."""

    def __init__(self, message: str, *, status: int, body: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


#: Text an OpenAI-compatible endpoint puts in a 400/422 that refuses the MODEL.
_MODEL_REFUSAL_MARKERS = (
    "model_not_found",
    "does not exist",
    "not found",
    "invalid model",
    "unknown model",
    "no such model",
    "not allowed to access",
    "not available",
)


def _model_unavailable(exc: BaseException) -> bool:
    """Whether *exc* says the requested model cannot be served, so another may be tried.

    Anything else -- a refused transcript, a network failure, a timeout -- would
    fail the same way on every model, and trying the next one would only move the
    session off the model the user chose.
    """
    if not isinstance(exc, SaokhueHttpError):
        return False
    if exc.status == 404:
        return True
    body = exc.body.lower()
    return (
        exc.status in (400, 422)
        and "model" in body
        and any(marker in body for marker in _MODEL_REFUSAL_MARKERS)
    )


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


async def _stream_saokhue_turn(
    self: SaokhueAcpServer,
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
            session.models_listed = True
    session.model = _select_valid_saokhue_model(session.model, session.available_models, env_model)

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
                    session.model = cand_model
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
            if last_urllib_err is not None and not _model_unavailable(last_urllib_err):
                raise last_urllib_err
            logger.warning(
                "Sao Khuê model %r failed (%s); trying next candidate if available",
                cand_model,
                last_urllib_err,
            )
        if last_urllib_err is not None:
            raise last_urllib_err

    timeout = aiohttp.ClientTimeout(total=timeout_secs)
    ssl_opt: Any = _aiohttp_ssl()
    last_model_err: Exception | None = None
    for cand_idx, cand_model in enumerate(candidate_models):
        payload["model"] = cand_model
        payload["stream_options"] = {"include_usage": True}
        succeeded = False
        # One pass: the loop is the scope its ``break`` statements leave.
        for _ in range(1):
            content_parts.clear()
            tool_calls_by_index.clear()
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            cache_write_tokens = 0
            turn_cost_usd = 0.0
            try:
                async with aiohttp.ClientSession(timeout=timeout) as http:
                    post_kwargs: dict[str, Any] = {
                        "headers": headers,
                        "json": payload,
                        "ssl": ssl_opt,
                        "proxy": resolve_proxy(url),
                    }
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
                                        raise SaokhueHttpError(
                                            f"Sao Khuê status={resp2.status} from {url} (model={cand_model!r}): "
                                            f"{err_body2[:500]}",
                                            status=resp2.status,
                                            body=err_body2,
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
                                                                "content": {
                                                                    "type": "text",
                                                                    "text": text,
                                                                },
                                                            },
                                                        },
                                                    )
                                    else:
                                        async for raw_line in resp2.content:
                                            if session.cancelled:
                                                break
                                            line = raw_line.decode(
                                                "utf-8", errors="replace"
                                            ).strip()
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
                                                                "content": {
                                                                    "type": "text",
                                                                    "text": text_piece,
                                                                },
                                                            },
                                                        },
                                                    )
                                succeeded = True
                                break
                            raise SaokhueHttpError(
                                f"Sao Khuê status={resp.status} from {url} (model={cand_model!r}): "
                                f"{err_body[:500]}",
                                status=resp.status,
                                body=err_body,
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
                if _is_ssl_cert_error(exc):
                    # Another model would meet the same certificate.
                    _warn_untrusted_certificate(exc, url)
                    raise
                if "Not signed in to Sao Khuê" in str(exc):
                    raise
                if not _model_unavailable(exc):
                    raise
                last_model_err = exc
                break
        if succeeded:
            session.model = cand_model
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
    self: SaokhueAcpServer,
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
                err_obj = SaokhueHttpError(
                    f"Sao Khuê status={exc.code} from {url} (model={payload.get('model')!r}): "
                    f"{err_body[:500]}",
                    status=exc.code,
                    body=err_body,
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
