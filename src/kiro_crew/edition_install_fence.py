"""The LTS build's install tree is on the agent's file-tool write gate.

RFC ``rfc-crew-lts-edition`` (Design section 4, tamper resistance): the install
directory is write-protected from the agent. The OS sandbox already seals the packaged
install tree read-only for every sandboxed child (``sandbox_tiers.
_resolved_install_root_targets``). This adds the same tree to
``security.paths.sensitive_write_path_refusal``, so a file-edit tool call the gateway
judges is refused too, including one served by an MCP server that runs outside the
sandbox. Reads stay allowed.

LTS builds, and every packaged build of the Sao Khuê fork
(:func:`kiro_crew.saokhue_permission_surface.fences_install_tree`), whose permission
surface is that installed code. Not governable: no config, environment or policy value
turns it off. An editable or from-source checkout has no packaged install tree, so
nothing is added there in either edition.
"""

from __future__ import annotations

import os

from kiro_crew.edition import is_lts


def install_root_write(path_str: str, base_dir: str | None = None) -> bool:
    """Whether *path_str* lies in this build's fenced install tree (:func:`_fenced`).

    Every candidate spelling of the path (resolved, lexical, raw) is compared, so a
    symlink or ``..`` into the tree is caught. A resolver stall raises
    ``PathResolutionStalled``, which the write gate turns into a refusal.
    """
    if not path_str or not _fenced():
        return False
    # Call-time imports: both modules import the security package at load.
    from kiro_crew.sandbox_tiers import _resolved_install_root_targets
    from kiro_crew.security.paths_targets import _candidate_forms

    roots = [r.casefold().rstrip("/\\") for r in _resolved_install_root_targets() if r]
    if not roots:
        return False
    for cand in _candidate_forms(path_str, base_dir):
        cand_cf = cand.casefold()
        for root in roots:
            if cand_cf == root or cand_cf.startswith(root + os.sep):
                return True
    return False


def _fenced() -> bool:
    """Whether this build fences its install tree: LTS, or the Sao Khuê fork."""
    if is_lts():
        return True
    from kiro_crew.saokhue_permission_surface import fences_install_tree

    return fences_install_tree()
