"""Built-in LiteLLM ACP server, launched one process per session by ``AcpClient``.

Runs Kiro Crew's own Python ACP server (:mod:`kiro_crew.acp.litellm_server`) over stdio
with no external CLI binary required. Pre-spawn governance checks mirror the first-party
``--agent`` spawn path so fork governance, shared-template ceilings, and managed-agent
shadowing are enforced before the subprocess starts.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from kiro_crew import agent as agent_mod
from kiro_crew.acp import managed_agent_shadow as managed_shadow_mod
from kiro_crew.acp.child_env_defaults import apply_child_env_defaults
from kiro_crew.acp.harness.base import ProcessAdapter, SpawnContext, SpawnPlan
from kiro_crew.acp.transport_errors import AcpError
from kiro_crew.agent import ForkGovernanceUnresolved
from kiro_crew.agent_sdk.backends import ACP_BACKEND_LITELLM

logger = logging.getLogger("kiro_crew.acp.client")

__all__ = ["LITELLM_SERVER_MODULE", "LITELLM_SERVER_SCRIPT", "LitellmLaunch"]

LITELLM_SERVER_MODULE = "kiro_crew.acp.litellm_server"
LITELLM_SERVER_SCRIPT = str(Path(__file__).resolve().parents[1] / "litellm_server.py")

_LITELLM_ENV_KEYS: tuple[str, ...] = (
    "LITELLM_CLI_HOME",
    "LITELLM_BASE_URL",
    "LITELLM_PROXY_URL",
    "LITELLM_ACCESS_TOKEN",
    "LITELLM_API_KEY",
    "LITELLM_MODEL",
    "LITELLM_TIMEOUT",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
)


def _read_dotenv_litellm_vars(dotenv_path: Path) -> dict[str, str]:
    """Read LiteLLM-related variables from *dotenv_path* without mutating os.environ."""
    found: dict[str, str] = {}
    try:
        if not dotenv_path.is_file():
            return found
        text = dotenv_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, val = line.partition("=")
        key = key.strip()
        if key not in _LITELLM_ENV_KEYS:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if val:
            found[key] = val
    return found


def inject_litellm_env(env: dict[str, str]) -> None:
    """Populate *env* with LiteLLM settings from ``<config_dir>/.env`` when unset."""
    from kiro_crew.config.paths import config_dir

    dotenv_vars = _read_dotenv_litellm_vars(config_dir() / ".env")
    for key, val in dotenv_vars.items():
        if not env.get(key):
            env[key] = val


class LitellmLaunch(ProcessAdapter):
    """Built-in LiteLLM ACP server launched via ``sys.executable -m kiro_crew.acp.litellm_server``."""

    backend = ACP_BACKEND_LITELLM

    async def resolve_spawn(self, ctx: SpawnContext) -> SpawnPlan:
        """Build the ``python -m kiro_crew.acp.litellm_server`` argv after agent gates."""
        if ctx.agent:
            try:
                await asyncio.to_thread(agent_mod.ensure_agent_materialized, ctx.agent)
            except Exception:
                logger.warning("pre-spawn agent materialization failed", exc_info=True)

            try:
                await asyncio.to_thread(
                    agent_mod.require_fork_governance, ctx.agent, ctx.work_dir
                )
            except ForkGovernanceUnresolved as exc:
                raise AcpError(str(exc)) from exc

            try:
                await asyncio.to_thread(
                    agent_mod.require_shared_template_within_ceiling,
                    ctx.agent,
                    ACP_BACKEND_LITELLM,
                )
            except agent_mod.SharedTemplateOverCeiling as exc:
                raise AcpError(str(exc)) from exc

            shadowed = await asyncio.to_thread(
                managed_shadow_mod.managed_agent_shadow_refusal, ctx.agent, ctx.work_dir
            )
            if shadowed:
                raise AcpError(shadowed)

        argv = [sys.executable, LITELLM_SERVER_SCRIPT, "acp"]
        if ctx.agent:
            argv += ["--agent", ctx.agent]
        if ctx.model and ctx.model != "auto":
            argv += ["--model", ctx.model]
        return SpawnPlan(
            argv=argv,
            spawn_label="litellm-cli acp",
            stderr_label="litellm-cli",
        )

    def apply_spawn_env(self, env: dict[str, str], *, spawned_binary: str | None = None) -> None:
        """Forward LiteLLM configuration from ``~/.kiro/crew/.env`` and child env defaults."""
        inject_litellm_env(env)
        apply_child_env_defaults(env)

    def record_session_projection(
        self, handle: object, custom_agents: object, active_agent: str
    ) -> None:
        """Stamp ``active_agent`` and ``confirmed_projection_template`` on *handle*."""
        setattr(handle, "active_agent", active_agent)
        setattr(handle, "confirmed_projection_template", active_agent)

