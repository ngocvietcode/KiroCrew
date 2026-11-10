"""Hand the machine's CA certificates to the processes the gateway starts.

A child that does not read the operating system's certificate store (a Node MCP
server, the KAS engine, kiro-cli, an AWS SDK tool) rejects a host whose
certificate a company CA issued, typically behind a proxy that inspects TLS.
:func:`export_for_children` writes the system store to one PEM file and names it
in the variables those children read. It runs at every gateway start, whether or
not ``network.proxy_enabled`` is on: an internal host reached directly needs the
company CA just as much.

A variable that already holds a value is never changed: it is the operator's, or
the trust :mod:`kiro_crew._ssl_compat` derived for this install.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping

logger = logging.getLogger(__name__)

#: Read by Node in ADDITION to its built-in CAs, so naming a bundle there can only
#: widen what a child trusts. Set on every platform.
ADDITIVE_ENV = ("NODE_EXTRA_CA_CERTS",)
#: Read INSTEAD of a client's default CAs (OpenSSL, requests, the AWS SDKs). Set
#: only where the exported bundle holds everything the system store does: on
#: Windows. Elsewhere the bundle lacks the macOS Keychain's certificates, and
#: ``_ssl_compat`` already chooses these for the install.
REPLACING_ENV = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE")


def export_for_children(
    env: MutableMapping[str, str] | None = None, *, windows: bool | None = None
) -> str | None:
    """Write the CA bundle and name it in each unset CA variable of *env*.

    Returns the bundle's path, or ``None`` when none could be written. Never
    raises: a bundle that cannot be exported leaves *env* as it was.
    """
    target = os.environ if env is None else env
    is_windows = sys.platform == "win32" if windows is None else windows
    try:
        from kiro_crew.acp.saokhue_server import ensure_system_ca_bundle

        # A scratch mapping: which variables are set is decided here, not there.
        bundle = ensure_system_ca_bundle({})
    except Exception:
        logger.warning("CA bundle: could not export the system certificates", exc_info=True)
        return None
    if not bundle:
        logger.warning("CA bundle: no system certificate was found to export")
        return None
    names = ADDITIVE_ENV + (REPLACING_ENV if is_windows else ())
    assigned = [name for name in names if not target.get(name)]
    for name in assigned:
        target[name] = bundle
    if assigned:
        logger.info("CA bundle: children read %s through %s", bundle, ", ".join(assigned))
    return bundle
