"""A local proxy that signs in to the company proxy for the gateway and its children.

Children see only ``http://crew:<token>@127.0.0.1:<port>``; the proxy process
adds the upstream credentials. :func:`start_for_gateway` is the only entry point
the gateway calls; the process itself is ``python -m kiro_crew.auth_proxy``.
"""

from __future__ import annotations

from kiro_crew.auth_proxy.supervisor import start_for_gateway

__all__ = ["start_for_gateway"]
