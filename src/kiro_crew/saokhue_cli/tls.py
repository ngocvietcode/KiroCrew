"""Certificate trust for ``saokhue-cli``: the system CA bundle and verified requests.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import contextlib
import logging
import os
import ssl
import urllib.parse
import urllib.request
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from kiro_crew.saokhue_cli.base import saokhue_cli_home

logger = logging.getLogger("kiro_crew.acp.saokhue_server")


def _is_ssl_cert_error(exc: BaseException) -> bool:
    """Return True if *exc* or its cause is a TLS certificate verification failure."""
    cur: BaseException | None = exc
    visited: set[int] = set()
    while cur is not None and id(cur) not in visited:
        visited.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        msg = str(cur).lower()
        if (
            "certificate_verify_failed" in msg
            or "unable to get local issuer certificate" in msg
            or "unable to get issuer certificate" in msg
            or "self-signed certificate" in msg
            or "self signed certificate" in msg
        ):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


_SYSTEM_CA_BUNDLE_CACHE: str | None = None
_SYSTEM_SSL_CONTEXT_CACHE: ssl.SSLContext | None = None

_CA_BUNDLE_ENV_VARS: tuple[str, ...] = (
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
    "AWS_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def ensure_system_ca_bundle(environ: MutableMapping[str, str] | None = None) -> str | None:
    """Export OS/Windows Global CA certificates (`ROOT` + `CA` + certifi) to a PEM bundle and set CA env vars.

    Allows Python (`urllib`, `aiohttp`, `requests`), Node.js (`kiro-cli` KAS engine via
    ``NODE_EXTRA_CA_CERTS``), OpenSSL (`kiro-cli` via ``SSL_CERT_FILE``), and AWS SDK
    (``AWS_CA_BUNDLE``) to automatically trust corporate/global Root and Intermediate CAs
    installed in the machine's certificate store.
    """
    global _SYSTEM_CA_BUNDLE_CACHE
    target_env = os.environ if environ is None else environ

    if _SYSTEM_CA_BUNDLE_CACHE and os.path.isfile(_SYSTEM_CA_BUNDLE_CACHE):
        for var in _CA_BUNDLE_ENV_VARS:
            if not target_env.get(var):
                target_env[var] = _SYSTEM_CA_BUNDLE_CACHE
        return _SYSTEM_CA_BUNDLE_CACHE

    pem_blocks: list[str] = []
    seen_blocks: set[str] = set()

    def _add_pem_text(text: str) -> None:
        marker_begin = "-----BEGIN CERTIFICATE-----"
        marker_end = "-----END CERTIFICATE-----"
        pos = 0
        while True:
            start = text.find(marker_begin, pos)
            if start == -1:
                break
            end = text.find(marker_end, start)
            if end == -1:
                break
            block = text[start : end + len(marker_end)].strip()
            if block and block not in seen_blocks:
                seen_blocks.add(block)
                pem_blocks.append(block)
            pos = end + len(marker_end)

    # 1. Existing CA bundle from certifi or OpenSSL default verify paths
    with contextlib.suppress(Exception):
        import certifi  # type: ignore[import-not-found]

        cafile = certifi.where()
        if cafile and os.path.isfile(cafile):
            _add_pem_text(Path(cafile).read_text(encoding="utf-8", errors="ignore"))

    with contextlib.suppress(Exception):
        default_paths = ssl.get_default_verify_paths()
        for candidate in (default_paths.cafile, os.environ.get("SSL_CERT_FILE")):
            if candidate and os.path.isfile(candidate):
                _add_pem_text(Path(candidate).read_text(encoding="utf-8", errors="ignore"))

    # 2. Windows Certificate Store (ROOT and Intermediate CA stores)
    if hasattr(ssl, "enum_certificates"):
        for store_name in ("ROOT", "CA"):
            with contextlib.suppress(Exception):
                for cert_bytes, encoding_type, trust in ssl.enum_certificates(store_name):
                    if encoding_type != "x509_asn" or trust is False:
                        continue
                    with contextlib.suppress(Exception):
                        pem_str = ssl.DER_cert_to_PEM_cert(cert_bytes)
                        _add_pem_text(pem_str)

    # 3. Standard POSIX system CA bundles
    for posix_ca in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
    ):
        if os.path.isfile(posix_ca):
            with contextlib.suppress(Exception):
                _add_pem_text(Path(posix_ca).read_text(encoding="utf-8", errors="ignore"))
            break

    if not pem_blocks:
        return None

    try:
        bundle_dir = saokhue_cli_home()
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / "system-ca-bundle.pem"
        combined = "\n".join(pem_blocks) + "\n"
        existing = ""
        if bundle_path.is_file():
            with contextlib.suppress(OSError):
                existing = bundle_path.read_text(encoding="utf-8", errors="ignore")
        if existing != combined:
            tmp_bundle = bundle_path.with_suffix(".pem.tmp")
            tmp_bundle.write_text(combined, encoding="utf-8")
            tmp_bundle.replace(bundle_path)
        _SYSTEM_CA_BUNDLE_CACHE = str(bundle_path)
        for var in _CA_BUNDLE_ENV_VARS:
            if not target_env.get(var):
                target_env[var] = _SYSTEM_CA_BUNDLE_CACHE
        return _SYSTEM_CA_BUNDLE_CACHE
    except Exception:
        logger.debug("Could not write system CA bundle file", exc_info=True)
        return None


def _build_system_ssl_context() -> ssl.SSLContext:
    """Create an ``ssl.SSLContext`` trusted with the OS/Windows Global CA store and certifi."""
    global _SYSTEM_SSL_CONTEXT_CACHE
    if _SYSTEM_SSL_CONTEXT_CACHE is not None:
        return _SYSTEM_SSL_CONTEXT_CACHE

    ctx = ssl.create_default_context()
    with contextlib.suppress(Exception):
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
    bundle_path = ensure_system_ca_bundle()
    if bundle_path and os.path.isfile(bundle_path):
        with contextlib.suppress(Exception):
            ctx.load_verify_locations(cafile=bundle_path)
    _SYSTEM_SSL_CONTEXT_CACHE = ctx
    return ctx


def _tls_verification_disabled() -> bool:
    """Whether the operator turned certificate checks off with ``SAOKHUE_SSL_VERIFY=0``."""
    return os.environ.get("SAOKHUE_SSL_VERIFY", "").strip().lower() in ("0", "false", "no")


def _aiohttp_ssl() -> Any:
    """The ``ssl=`` value of an aiohttp request: the system CA context, or ``False`` when
    the operator disabled verification."""
    return False if _tls_verification_disabled() else _build_system_ssl_context()


def _warn_untrusted_certificate(exc: BaseException, url: str) -> None:
    """Name the fix when *exc* is a certificate failure. The request is never retried
    unverified: it carries the sign-in token and the conversation."""
    if _is_ssl_cert_error(exc):
        host = urllib.parse.urlsplit(url).hostname or url
        logger.warning(
            "Sao Khuê: the certificate of %s is not trusted, so the request was not sent. "
            "Install the issuing CA in the system certificate store.",
            host,
        )


def _urlopen_resilient(req: urllib.request.Request | str, timeout: float = 15.0) -> Any:
    """Open *req* via ``urllib.request.urlopen``, verified against the system CA store.

    A certificate failure propagates. Only ``SAOKHUE_SSL_VERIFY=0`` turns the check off.
    """
    if _tls_verification_disabled():
        return urllib.request.urlopen(
            req, timeout=timeout, context=ssl._create_unverified_context()
        )
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_build_system_ssl_context())
    except Exception as exc:
        _warn_untrusted_certificate(exc, req if isinstance(req, str) else req.full_url)
        raise
