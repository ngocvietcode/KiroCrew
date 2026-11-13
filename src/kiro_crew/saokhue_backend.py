"""Which backend a Bot runs on when the Settings harness is Sao Khuê CLI.

Bots default to a Kiro backend (KAS, or Kiro CLI read as KAS). An install whose
main harness is Sao Khuê CLI has no Kiro sign-in, so its Bots follow the main
harness instead. An explicit pick of any other harness is left alone.
"""

from __future__ import annotations

from typing import Any

from kiro_crew.acp_backends import ACP_BACKEND_KAS, ACP_BACKEND_KIRO, ACP_BACKEND_SAOKHUE


def follow_saokhue_primary(primary: object, backend: Any) -> Any:
    """*backend*, or Sao Khuê CLI when *primary* is that and *backend* is a Kiro one or unset."""
    if primary == ACP_BACKEND_SAOKHUE and backend in (None, ACP_BACKEND_KIRO, ACP_BACKEND_KAS):
        return ACP_BACKEND_SAOKHUE
    return backend
