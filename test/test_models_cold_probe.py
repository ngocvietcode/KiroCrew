"""A cold model list opens a throwaway background session and asks the dashboards to read again."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiro_crew.dashboard.handlers import models_cold_probe
from kiro_crew.testing.wait import await_until


def _state(*, backend: str = "codex", runtime: bool = True, fails: bool = False) -> SimpleNamespace:
    handle = MagicMock()
    handle.destroy = AsyncMock()
    sessions = MagicMock()
    sessions._configured_bg_backend.return_value = backend
    sessions._bg_backend_supports_runtime.return_value = runtime
    sessions.get_bg_session = AsyncMock(
        side_effect=RuntimeError("no runtime") if fails else None, return_value=handle
    )
    return SimpleNamespace(sessions=sessions, broadcast_ws=MagicMock(), handle=handle)


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(models_cold_probe, "_now", lambda: now[0])
    return now


async def _run(state: SimpleNamespace, backend: str = "codex") -> bool:
    started = models_cold_probe.start_cold_model_probe(state, backend)
    task = getattr(state, models_cold_probe._TASK_ATTR, None)
    if task is not None:
        await await_until(task, what="the cold model probe")
    return started


@pytest.mark.asyncio
async def test_cold_read_opens_and_destroys_a_throwaway_session_then_announces(clock) -> None:
    state = _state()
    assert await _run(state) is True
    state.sessions.get_bg_session.assert_awaited_once()
    state.handle.destroy.assert_awaited_once()
    state.broadcast_ws.assert_called_once_with(models_cold_probe.MODELS_CHANGED_EVENT, {})
    # Nothing persistent: the provider-backed background entry is never asked for.
    state.sessions._ensure_background.assert_not_called()


@pytest.mark.asyncio
async def test_another_harness_is_not_probed(clock) -> None:
    state = _state(backend="kas")
    assert await _run(state, "codex") is False
    state.sessions.get_bg_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_backend_without_the_shared_runtime_is_not_probed(clock) -> None:
    state = _state(runtime=False)
    assert await _run(state) is False
    state.sessions.get_bg_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_start_announces_nothing(clock) -> None:
    state = _state(fails=True)
    assert await _run(state) is True
    state.broadcast_ws.assert_not_called()


@pytest.mark.asyncio
async def test_one_probe_per_backend_per_cooldown(clock) -> None:
    state = _state()
    assert await _run(state) is True
    clock[0] += models_cold_probe.COLD_PROBE_COOLDOWN_SECS - 1
    assert await _run(state) is False
    clock[0] += 1
    assert await _run(state) is True
    assert state.sessions.get_bg_session.await_count == 2


@pytest.mark.asyncio
async def test_a_running_probe_is_not_started_twice(clock) -> None:
    gate = asyncio.Event()
    state = _state()

    async def slow() -> MagicMock:
        await gate.wait()
        return state.handle

    state.sessions.get_bg_session = AsyncMock(side_effect=slow)
    assert models_cold_probe.start_cold_model_probe(state, "codex") is True
    clock[0] += models_cold_probe.COLD_PROBE_COOLDOWN_SECS * 2
    assert models_cold_probe.start_cold_model_probe(state, "codex") is False
    gate.set()
    await await_until(getattr(state, models_cold_probe._TASK_ATTR), what="the cold model probe")
    state.sessions.get_bg_session.assert_awaited_once()


def test_no_dashboard_state_is_not_an_error() -> None:
    assert models_cold_probe.start_cold_model_probe(None, "codex") is False


def test_cold_advertised_list_starts_the_probe(monkeypatch) -> None:
    from kiro_crew.dashboard.handlers import agents

    calls: list[str] = []
    monkeypatch.setattr(agents, "_advertised_cc_models", lambda _req, _ns: [])
    monkeypatch.setattr(
        models_cold_probe, "start_cold_model_probe", lambda _s, b: calls.append(b) or True
    )
    request = SimpleNamespace(app={"state": object()})
    rows = agents._advertised_backend_models(request, "codex")
    assert [r["model_name"] for r in rows] == ["auto"]
    assert calls == ["codex"]


def test_warm_advertised_list_starts_no_probe(monkeypatch) -> None:
    from kiro_crew.dashboard.handlers import agents

    calls: list[str] = []
    monkeypatch.setattr(
        agents, "_advertised_cc_models", lambda _req, _ns: [{"model_name": "gpt-x"}]
    )
    monkeypatch.setattr(
        models_cold_probe, "start_cold_model_probe", lambda _s, b: calls.append(b) or True
    )
    rows = agents._advertised_backend_models(SimpleNamespace(app={"state": object()}), "codex")
    assert [r["model_name"] for r in rows] == ["auto", "gpt-x"]
    assert calls == []


@pytest.fixture
def _no_catalog_listeners(_floor_monkeypatch):
    from kiro_crew.agent_sdk import kas_model_catalog

    _floor_monkeypatch.setattr(kas_model_catalog, "_listeners", [])
    _floor_monkeypatch.setattr(kas_model_catalog, "_recorded", None)
    _floor_monkeypatch.setattr(kas_model_catalog, "_recorded_identity", "")
    return kas_model_catalog


def _kas_select(*values: str) -> dict:
    options = [{"value": v, "name": v, "description": ""} for v in values]
    return {
        "configOptions": [
            {"id": "model", "type": "select", "currentValue": "auto", "options": options}
        ]
    }


@pytest.mark.asyncio
async def test_a_kas_catalog_recording_announces_after_a_cold_probe_recorded_nothing(
    _no_catalog_listeners,
) -> None:
    # The probe's session answered before the engine had its select, so the frame it
    # sent re-read a cold list. The session that records later must announce again.
    catalog = _no_catalog_listeners
    state = _state(backend="kas")
    models_cold_probe.watch_kas_catalog(state)
    models_cold_probe.watch_kas_catalog(state)  # once per dashboard state
    announced = asyncio.Event()
    state.broadcast_ws.side_effect = lambda *_: announced.set()

    catalog.record_catalog(_kas_select("auto", "m1"), "kas", identity="acct")
    await asyncio.wait_for(announced.wait(), timeout=5)
    state.broadcast_ws.assert_called_once_with(models_cold_probe.MODELS_CHANGED_EVENT, {})

    # The same catalog again is no news: no listener runs, so nothing is scheduled.
    with patch.object(asyncio.get_running_loop(), "call_soon_threadsafe") as scheduled:
        catalog.record_catalog(_kas_select("auto", "m1"), "kas", identity="acct")
    scheduled.assert_not_called()


def test_watching_without_a_state_is_not_an_error(_no_catalog_listeners) -> None:
    models_cold_probe.watch_kas_catalog(None)
    assert _no_catalog_listeners._listeners == []
