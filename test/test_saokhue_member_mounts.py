"""Sao Khuê receives a crewmate's MCP servers the way every member backend does.

``saokhue_server`` reads the agent spec's own servers itself, so Crew's array for it
carries only what a spec cannot: a member DM session's session-control and crew-panel
servers, composed by ``AcpClient._mirrorless_member_mounts`` under the same switch-offs
as on the other member backends, and handed over by ``SaokhueLaunch``. A crewmate's
worker session runs on the worker spec the other backends' projections read.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from acp_session_mcp_helpers import _installed_claude_adapter_version  # noqa: F401
from acp_session_mcp_helpers import _pinned_kiro_cli_version  # noqa: F401
from acp_session_mcp_helpers import agents_dir_fixture  # noqa: F401
from acp_session_mcp_helpers import isolated_seed_provenance  # noqa: F401
from acp_session_mcp_helpers import _by_name, _write_spec

from kiro_crew.acp import saokhue_server
from kiro_crew.acp.client import AcpClient
from kiro_crew.acp.harness.base import SpawnContext
from kiro_crew.acp.harness.saokhue import SaokhueLaunch
from kiro_crew.agent_sdk.backends import ACP_BACKEND_SAOKHUE

_MEMBER_KEY = "dashboard:member-kirocrew"


def _member_client(tmp_path: Path) -> AcpClient:
    client = AcpClient(
        work_dir=tmp_path,
        agent="kirocrew",
        acp_backend=ACP_BACKEND_SAOKHUE,
        session_key=_MEMBER_KEY,
    )
    client.member_context = True
    return client


def _member_array(tmp_path: Path, agents_dir: Path, monkeypatch: Any) -> dict[str, Any]:
    from kiro_crew import members

    _write_spec(agents_dir, servers={}, tools=[])
    entry = {"name": members.MEMBER_DISPATCH_SERVER, "command": "/opt/kc", "args": [], "env": []}
    monkeypatch.setattr(members, "member_dispatch_session_server", lambda *_a, **_k: dict(entry))
    monkeypatch.setattr(members, "crew_panel_enabled", lambda: False)
    client = _member_client(tmp_path)
    asyncio.run(client._prepare_session_mcp())
    return _by_name(SaokhueLaunch().session_mcp_servers(client))


def test_a_member_session_hands_saokhue_its_session_control_server_once_fenced(
    tmp_path: Path, agents_dir: Path, monkeypatch: Any
) -> None:
    from kiro_crew import members, saokhue_permission_surface

    monkeypatch.setattr(saokhue_permission_surface, "permission_surface_fenced", lambda: True)

    assert members.MEMBER_DISPATCH_SERVER in _member_array(tmp_path, agents_dir, monkeypatch)


def test_session_control_is_withheld_while_saokhue_code_is_not_fenced(
    tmp_path: Path, agents_dir: Path, monkeypatch: Any, caplog: Any
) -> None:
    from kiro_crew import members

    monkeypatch.setattr(
        "kiro_crew.saokhue_permission_surface.permission_surface_fenced", lambda: False
    )
    with caplog.at_level("WARNING"):
        names = _member_array(tmp_path, agents_dir, monkeypatch)

    assert members.MEMBER_DISPATCH_SERVER not in names
    assert "permission surface not Crew-owned" in caplog.text


def test_an_ordinary_session_hands_saokhue_no_server(tmp_path: Path, agents_dir: Path) -> None:
    _write_spec(agents_dir, servers={"foo": {"command": "/bin/foo"}}, tools=["@foo"])
    client = AcpClient(work_dir=tmp_path, agent="kirocrew", acp_backend=ACP_BACKEND_SAOKHUE)

    asyncio.run(client._prepare_session_mcp())

    # The spec's own servers are read by saokhue_server itself, not sent twice.
    assert SaokhueLaunch().session_mcp_servers(client) == []


def test_the_launch_warms_the_array_before_the_spawn(tmp_path: Path) -> None:
    warmed: list[bool] = []

    class _Session:
        async def _prepare_session_mcp(self) -> None:
            warmed.append(True)

    ctx = SpawnContext(
        agent="",
        work_dir=tmp_path,
        model=None,
        environ={},
        home=tmp_path,
        session=_Session(),  # type: ignore[arg-type]
    )
    plan = asyncio.run(SaokhueLaunch().resolve_spawn(ctx))

    assert warmed == [True]
    assert plan.argv[-1] == "acp"


def test_the_agent_spec_is_read_through_the_session_projection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew.acp import session_mcp

    def _snapshot(agent: str | None, *, work_dir: Any = None) -> dict[str, Any]:
        return {"name": agent, "work_dir": str(work_dir)}

    monkeypatch.setattr(session_mcp, "agent_spec_snapshot", _snapshot)
    monkeypatch.delenv("KIROCREW_SESSION_KEY", raising=False)
    server = saokhue_server.SaokhueAcpServer(agent_name="probe")

    assert server._load_agent_spec("probe", str(tmp_path)) == {
        "name": "probe",
        "work_dir": str(tmp_path),
    }


def test_a_crewmate_worker_runs_on_its_worker_spec(tmp_path: Path, monkeypatch: Any) -> None:
    from kiro_crew import mate_worker
    from kiro_crew.acp import session_mcp

    monkeypatch.setattr(
        session_mcp, "agent_spec_snapshot", lambda agent, *, work_dir=None: {"base": agent}
    )
    monkeypatch.setattr(
        mate_worker, "worker_session_spec", lambda spec, key, **_k: {**spec, "worker_of": key}
    )
    monkeypatch.setenv("KIROCREW_SESSION_KEY", "worker:mate-1")
    server = saokhue_server.SaokhueAcpServer(agent_name=mate_worker.MATE_WORKER_AGENT)

    spec = server._load_agent_spec(mate_worker.MATE_WORKER_AGENT, str(tmp_path))

    assert spec == {"base": mate_worker.MATE_WORKER_AGENT, "worker_of": "worker:mate-1"}


def test_a_worker_without_its_kit_refuses_rather_than_runs_without_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew import mate_worker
    from kiro_crew.acp import session_mcp

    monkeypatch.setattr(session_mcp, "agent_spec_snapshot", lambda agent, *, work_dir=None: None)
    monkeypatch.setattr(saokhue_server.SaokhueAcpServer, "_read_agent_spec", lambda *_a: {})
    monkeypatch.setenv("KIROCREW_SESSION_KEY", "worker:mate-1")
    server = saokhue_server.SaokhueAcpServer(agent_name=mate_worker.MATE_WORKER_AGENT)

    with pytest.raises(mate_worker.WorkerKitUnreadable):
        server._load_agent_spec(mate_worker.MATE_WORKER_AGENT, str(tmp_path))


def test_the_surface_is_governed_only_for_a_crew_owned_harness_on_the_fence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew import saokhue_permission_surface

    monkeypatch.setattr(saokhue_permission_surface, "permission_surface_fenced", lambda: True)
    saokhue = AcpClient(work_dir=tmp_path, agent="kirocrew", acp_backend=ACP_BACKEND_SAOKHUE)
    goose = AcpClient(work_dir=tmp_path, agent="kirocrew", acp_backend="goose")
    assert saokhue._permission_surface_governed is True
    assert goose._permission_surface_governed is False

    monkeypatch.setattr(saokhue_permission_surface, "permission_surface_fenced", lambda: False)
    assert saokhue._permission_surface_governed is False


def test_the_server_is_fenced_only_inside_a_packaged_install_tree(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from kiro_crew import sandbox, saokhue_permission_surface
    from kiro_crew.acp.harness import saokhue as harness_saokhue

    root = tmp_path / "site-packages" / "kiro_crew"
    script = root / "acp" / "saokhue_server.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(harness_saokhue, "SAOKHUE_SERVER_SCRIPT", str(script))
    monkeypatch.setattr(sandbox, "_resolved_install_root_targets", lambda: [str(root)])
    assert saokhue_permission_surface.permission_surface_fenced() is True

    monkeypatch.setattr(sandbox, "_resolved_install_root_targets", lambda: [])
    assert saokhue_permission_surface.permission_surface_fenced() is False

    monkeypatch.setattr(sandbox, "_resolved_install_root_targets", lambda: [str(root)])
    monkeypatch.setattr(saokhue_permission_surface, "fences_install_tree", lambda: False)
    assert saokhue_permission_surface.permission_surface_fenced() is False


def test_a_fence_check_that_cannot_complete_is_not_governed(monkeypatch: Any) -> None:
    from kiro_crew import edition_install_fence, saokhue_permission_surface

    def _stall(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("resolver stalled")

    monkeypatch.setattr(edition_install_fence, "install_root_write", _stall)
    assert saokhue_permission_surface.permission_surface_fenced() is False
