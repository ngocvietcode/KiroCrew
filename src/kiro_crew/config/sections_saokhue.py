"""The ``saokhue`` section: where the Sao Khuê CLI backend signs in and what it signs in as.

An owner of the :mod:`kiro_crew.config.sections` facade; it never imports the facade.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kiro_crew.config.fields import _meta
from kiro_crew.config.sections_defaults import SectionReader

logger = logging.getLogger("kiro_crew.config.loader")


@dataclass
class SaokhueConfig:
    """Sign-in settings of the ``saokhue-cli`` backend.

    Empty means the built-in default (the VPBank test portal and its test client).
    An environment variable of the same meaning (``SAOKHUE_BASE_URL``,
    ``SAOKHUE_CLIENT_ID``) still wins over a value saved here.
    """

    base_url: str = field(
        default="",
        metadata=_meta(
            "Sao Khuê Server URL",
            "The AI gateway Sao Khuê CLI signs in to and sends its chats through, as "
            "https://host/api/ai-llm. Empty = the built-in VPBank portal. The sign-in "
            "addresses follow from it. Takes effect after a gateway restart.",
            restart=True,
        ),
    )
    client_id: str = field(
        default="",
        metadata=_meta(
            "Sao Khuê Client ID",
            "The OAuth2 / OIDC client (app id) Sao Khuê CLI presents to the identity "
            "provider, e.g. app:name. Empty = the built-in test client. Takes effect "
            "after a gateway restart.",
            restart=True,
        ),
    )


def _base_url(value: object) -> str:
    """*value* when it is an http(s) address with a host, else ``""`` with a warning."""
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return text
    logger.warning(
        "Config: saokhue.base_url %r is not an http(s) address; the built-in one is used.", text
    )
    return ""


def build_saokhue_config(data: Mapping[str, Any]) -> SaokhueConfig:
    """The ``saokhue`` section from its raw dict, defaults for what it omits."""
    section = SectionReader(SaokhueConfig, data)
    return SaokhueConfig(
        base_url=_base_url(section.get("base_url")),
        client_id=str(section.get("client_id") or "").strip(),
    )
