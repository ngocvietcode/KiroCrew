"""The gateway names the machine's CA bundle for its children (``kiro_crew.ca_bundle_env``)."""

from __future__ import annotations

from typing import Any

import pytest

from kiro_crew import ca_bundle_env
from kiro_crew.acp import saokhue_server

BUNDLE = "/data/system-ca-bundle.pem"


@pytest.fixture
def exported(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Stand in for the certificate export; records the mapping each call was given."""
    calls: list[Any] = []

    def _export(environ: Any = None) -> str:
        calls.append(environ)
        return BUNDLE

    monkeypatch.setattr(saokhue_server, "ensure_system_ca_bundle", _export)
    return calls


def test_windows_children_get_every_ca_variable(exported: list[Any]) -> None:
    env: dict[str, str] = {}

    assert ca_bundle_env.export_for_children(env, windows=True) == BUNDLE

    assert env == {
        "NODE_EXTRA_CA_CERTS": BUNDLE,
        "SSL_CERT_FILE": BUNDLE,
        "REQUESTS_CA_BUNDLE": BUNDLE,
        "AWS_CA_BUNDLE": BUNDLE,
    }


def test_other_platforms_get_only_the_additive_variable(exported: list[Any]) -> None:
    env: dict[str, str] = {}

    ca_bundle_env.export_for_children(env, windows=False)

    assert env == {"NODE_EXTRA_CA_CERTS": BUNDLE}


def test_a_value_already_set_is_kept(exported: list[Any]) -> None:
    env = {"NODE_EXTRA_CA_CERTS": "/operator/extra.pem", "SSL_CERT_FILE": "/operator/ca.pem"}

    ca_bundle_env.export_for_children(env, windows=True)

    assert env["NODE_EXTRA_CA_CERTS"] == "/operator/extra.pem"
    assert env["SSL_CERT_FILE"] == "/operator/ca.pem"
    assert env["REQUESTS_CA_BUNDLE"] == BUNDLE
    assert env["AWS_CA_BUNDLE"] == BUNDLE


def test_the_export_itself_assigns_nothing(exported: list[Any]) -> None:
    env: dict[str, str] = {}

    ca_bundle_env.export_for_children(env, windows=False)

    assert exported == [{}]
    assert exported[0] is not env


@pytest.mark.parametrize("outcome", [None, "", RuntimeError("store unreadable")])
def test_no_bundle_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch, outcome: Any
) -> None:
    def _export(environ: Any = None) -> Any:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(saokhue_server, "ensure_system_ca_bundle", _export)
    env = {"PATH": "/bin"}

    assert ca_bundle_env.export_for_children(env, windows=True) is None
    assert env == {"PATH": "/bin"}
