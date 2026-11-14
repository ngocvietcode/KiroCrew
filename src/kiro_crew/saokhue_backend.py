"""Fork-only glue for the Sao Khuê CLI backend.

Bots default to a Kiro backend (KAS, or Kiro CLI read as KAS). An install whose
main harness is Sao Khuê CLI has no Kiro sign-in, so its Bots follow the main
harness instead (:func:`follow_saokhue_primary`); an explicit pick of any other
harness is left alone. :func:`export_saokhue_settings` carries the ``saokhue``
section of ``config.json`` (sign-in address and client id) to the gateway and its
children as the environment variables they already read. :func:`seed_saokhue_defaults`
fills the first-run ``config.json`` with the Sao Khuê defaults
(:func:`declare_saokhue_harness` adds the harness choice).
"""

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from kiro_crew.acp_backends import ACP_BACKEND_KAS, ACP_BACKEND_KIRO, ACP_BACKEND_SAOKHUE


def follow_saokhue_primary(primary: object, backend: Any) -> Any:
    """*backend*, or Sao Khuê CLI when *primary* is that and *backend* is a Kiro one or unset."""
    if primary == ACP_BACKEND_SAOKHUE and backend in (None, ACP_BACKEND_KIRO, ACP_BACKEND_KAS):
        return ACP_BACKEND_SAOKHUE
    return backend


def export_saokhue_settings(cfg: Any, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Make ``saokhue.base_url`` and ``saokhue.client_id`` the gateway's sign-in defaults.

    Both become ``SAOKHUE_BASE_URL`` / ``SAOKHUE_CLIENT_ID`` in *environ* (the
    process environment by default), which the sign-in code and every Sao Khuê CLI
    child it spawns already read. A variable the operator already set is left alone,
    so an environment override still wins over ``config.json``. Returns the names it
    set; never raises.
    """
    env = os.environ if environ is None else environ
    section = getattr(cfg, "saokhue", None)
    exported: list[str] = []
    for name, attr in (("SAOKHUE_BASE_URL", "base_url"), ("SAOKHUE_CLIENT_ID", "client_id")):
        value = str(getattr(section, attr, "") or "").strip()
        if value and not (env.get(name) or "").strip():
            env[name] = value
            exported.append(name)
    return exported


def seed_saokhue_defaults(cfg: Any) -> None:
    """Fill a brand-new default config (no ``config.json`` yet) with the Sao Khuê sign-in defaults.

    The ``saokhue`` section carries the built-in address and client id, written out
    so the operator can see and edit them. Only the first-run path calls this; a
    config that already exists is never touched. The harness choice cannot ride on
    ``cfg``: ``save()`` never invents one, so :func:`declare_saokhue_harness` adds it.
    """
    from kiro_crew.saokhue_cli.base import DEFAULT_OIDC_CLIENT_ID, DEFAULT_SAOKHUE_BASE_URL

    cfg.saokhue.base_url = DEFAULT_SAOKHUE_BASE_URL
    cfg.saokhue.client_id = DEFAULT_OIDC_CLIENT_ID


def declare_saokhue_harness(path: Path) -> bool:
    """Write ``agent.acp_backend = "saokhue"`` into the just-created *path* when it names no harness.

    Returns whether it wrote. A file that is unreadable, not a JSON object, or that
    already holds a choice is left exactly as it is.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    agent = document.setdefault("agent", {})
    if not isinstance(agent, dict) or agent.get("acp_backend"):
        return False
    agent["acp_backend"] = ACP_BACKEND_SAOKHUE
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    return True
