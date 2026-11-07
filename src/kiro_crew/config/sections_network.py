"""The ``network`` section: the corporate proxy the gateway and every agent child go through.

An owner of the :mod:`kiro_crew.config.sections` facade; it never imports the facade.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kiro_crew.config.fields import _meta
from kiro_crew.config.sections_defaults import SectionReader

logger = logging.getLogger("kiro_crew.config.loader")

#: The only accepted spelling of ``proxy_password``: a vault reference.
SECRET_REF_PREFIX = "secret://"
#: Values of ``proxy_auth``; anything else reads as ``auto``.
PROXY_AUTH_MODES = ("auto", "basic", "windows")


@dataclass
class NetworkConfig:
    """Outbound network settings.

    With ``proxy_enabled`` on, the gateway starts a local proxy on 127.0.0.1 and
    points itself and every child it spawns at it; that proxy adds the upstream
    credentials, so the password never enters an agent's environment.
    """

    proxy_enabled: bool = field(
        default=False,
        metadata=_meta(
            "Authenticating Proxy",
            "Route the gateway and every agent, MCP server and shell it starts "
            "through a local proxy on 127.0.0.1 that signs in to your company "
            "proxy. Children get only a loopback address and a one-time token; "
            "the proxy password stays in that one process. Takes effect after a "
            "gateway restart.",
            restart=True,
        ),
    )
    proxy_url: str = field(
        default="",
        metadata=_meta(
            "Proxy URL",
            "The company proxy, as http://host:port. Empty = the HTTPS_PROXY or "
            "HTTP_PROXY the gateway was started with.",
            restart=True,
        ),
    )
    proxy_username: str = field(
        default="",
        metadata=_meta(
            "Proxy Username",
            "Account name for Basic sign-in to the company proxy. Empty = no "
            "credentials, unless the proxy URL from the environment carries them.",
            restart=True,
        ),
    )
    proxy_password: str = field(
        default="",
        metadata=_meta(
            "Proxy Password",
            "A vault reference, secret://NAME, to the proxy password stored under "
            "Settings > Secrets. A plain password is refused and ignored.",
            restart=True,
            sensitive=True,
        ),
    )
    proxy_auth: str = field(
        default="auto",
        metadata=_meta(
            "Proxy Sign-in",
            "How to sign in to the company proxy: 'windows' uses the Windows account "
            "you are logged in with (NTLM or Kerberos, no password stored), 'basic' "
            "uses Proxy Username and Proxy Password, 'auto' uses Basic when a user "
            "name is set and the Windows account otherwise.",
            restart=True,
            enum=list(PROXY_AUTH_MODES),
        ),
    )
    proxy_no_proxy: str = field(
        default="",
        metadata=_meta(
            "Bypass Proxy For",
            "Comma-separated hosts or domains reached directly, without the "
            "company proxy (e.g. .vpbank.com.vn,.vpbank.dev). Added to the "
            "NO_PROXY the gateway was started with; localhost is always direct.",
            restart=True,
        ),
    )


def _is_secret_ref(text: str) -> bool:
    name = text[len(SECRET_REF_PREFIX) :] if text.startswith(SECRET_REF_PREFIX) else ""
    return bool(name) and name == name.strip()


def proxy_password_is_plaintext(value: object) -> bool:
    """Whether a stored ``network.proxy_password`` holds something other than a reference.

    The write floor (``loader_document._refuse_unpublishable``) refuses such a
    value, so a password typed into ``kirocrew config set`` never lands on disk.
    """
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and not _is_secret_ref(text)


def _password_ref(value: object) -> str:
    """*value* when it is a ``secret://NAME`` reference, else ``""`` with a warning."""
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_secret_ref(text):
        return text
    logger.warning(
        "Config: network.proxy_password must be a secret://NAME vault reference; "
        "the stored value is ignored. Store the password under Settings > Secrets."
    )
    return ""


def _auth_mode(value: object) -> str:
    text = str(value or "auto").strip().lower()
    if text in PROXY_AUTH_MODES:
        return text
    logger.warning(
        "Config: network.proxy_auth %r is not one of %s; using auto.", value, PROXY_AUTH_MODES
    )
    return "auto"


def build_network_config(data: Mapping[str, Any]) -> NetworkConfig:
    """The ``network`` section from its raw dict, defaults for what it omits."""
    section = SectionReader(NetworkConfig, data)
    return NetworkConfig(
        proxy_enabled=bool(section.get("proxy_enabled")),
        proxy_url=str(section.get("proxy_url") or "").strip(),
        proxy_username=str(section.get("proxy_username") or "").strip(),
        proxy_password=_password_ref(section.get("proxy_password")),
        proxy_auth=_auth_mode(section.get("proxy_auth")),
        proxy_no_proxy=str(section.get("proxy_no_proxy") or "").strip(),
    )
