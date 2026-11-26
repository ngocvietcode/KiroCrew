"""Fork-only: when Sao Khuê CLI's permission surface counts as Crew-governed.

``saokhue_server`` is Kiro Crew's own code, and it sends ``session/request_permission``
before every tool call it runs, built-in or MCP, with no allow list of its own. So the
one thing that could take the asking away is an edit to that code. This build puts its
packaged install tree on the agent's file-tool write gate
(:func:`kiro_crew.edition_install_fence.install_root_write`), the fence an LTS build
carries, and :func:`permission_surface_fenced` reports whether that fence covers the
server this session would launch. Only then does ``AcpClient._permission_surface_governed``
answer True for a backend in ``ACP_BACKENDS_CREW_OWNED_PERMISSION_SURFACE``, which is
what lets a crewmate's DM session on Sao Khuê mount its session-control and crew-panel
servers, as a Claude session whose settings file Crew owns does.

A from-source checkout has no packaged tree, so nothing is fenced there and the surface
is not governed. On native Windows there is no OS sandbox behind the gate, so a shell
command is not fenced by the OS; the file-tool gate is the same strength Claude's
fenced ``~/.claude/settings.json`` has there.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fences_install_tree() -> bool:
    """Whether this build puts its packaged install tree on the file-tool write gate.

    Always: every packaged build of this fork ships Sao Khuê CLI, whose permission
    surface is the installed code. Read at call time, so a test can turn it off.
    """
    return True


def permission_surface_fenced() -> bool:
    """Whether the Sao Khuê server this gateway launches lies inside the fenced tree.

    False when the fence is off, when there is no packaged tree (a source checkout), and
    when the check cannot complete (a stalled path resolution): a surface that cannot be
    shown to be fenced is not governed.
    """
    # Call-time imports: the fence reads the security package, which imports widely.
    from kiro_crew.acp.harness.saokhue import SAOKHUE_SERVER_SCRIPT
    from kiro_crew.edition_install_fence import install_root_write

    try:
        return install_root_write(SAOKHUE_SERVER_SCRIPT)
    except Exception:
        logger.warning(
            "could not establish that the Sao Khuê server lies in the fenced install "
            "tree; treating its permission surface as not governed",
            exc_info=True,
        )
        return False
