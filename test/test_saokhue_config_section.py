"""The ``saokhue`` config section and how it reaches the sign-in code."""

from __future__ import annotations

import logging

from kiro_crew.config.sections import SaokhueConfig, build_saokhue_config
from kiro_crew.saokhue_backend import export_saokhue_settings
from kiro_crew.saokhue_cli.auth import resolve_oidc_endpoints


class _Cfg:
    def __init__(self, **values: str) -> None:
        self.saokhue = SaokhueConfig(**values)


def test_an_omitted_section_means_the_built_in_defaults() -> None:
    assert build_saokhue_config({}) == SaokhueConfig(base_url="", client_id="")


def test_values_are_trimmed() -> None:
    section = build_saokhue_config(
        {"base_url": " https://portal.example.test/api/ai-llm ", "client_id": " app:prod "}
    )
    assert section.base_url == "https://portal.example.test/api/ai-llm"
    assert section.client_id == "app:prod"


def test_a_base_url_that_is_not_an_http_address_is_ignored_with_a_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="kiro_crew.config.loader"):
        section = build_saokhue_config({"base_url": "portal.example.test/api"})
    assert section.base_url == ""
    assert "saokhue.base_url" in caplog.text


def test_export_sets_what_the_environment_does_not_have() -> None:
    env: dict[str, str] = {}
    cfg = _Cfg(base_url="https://portal.example.test/api/ai-llm", client_id="app:prod")
    assert export_saokhue_settings(cfg, env) == ["SAOKHUE_BASE_URL", "SAOKHUE_CLIENT_ID"]
    assert env == {
        "SAOKHUE_BASE_URL": "https://portal.example.test/api/ai-llm",
        "SAOKHUE_CLIENT_ID": "app:prod",
    }


def test_an_environment_variable_wins_over_config_json() -> None:
    env = {"SAOKHUE_CLIENT_ID": "app:from-env"}
    assert export_saokhue_settings(_Cfg(client_id="app:from-config"), env) == []
    assert env == {"SAOKHUE_CLIENT_ID": "app:from-env"}


def test_empty_values_export_nothing() -> None:
    env: dict[str, str] = {}
    assert export_saokhue_settings(_Cfg(), env) == []
    assert env == {}


def test_the_exported_client_id_is_the_one_sign_in_presents() -> None:
    env: dict[str, str] = {}
    export_saokhue_settings(_Cfg(client_id="app:prod"), env)
    assert resolve_oidc_endpoints(environ=env)["client_id"] == "app:prod"
    assert resolve_oidc_endpoints(environ={})["client_id"] == "app:test-crew"


def test_first_run_config_names_the_saokhue_defaults(tmp_path, monkeypatch):
    import json

    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.saokhue_backend import declare_saokhue_harness, seed_saokhue_defaults

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    cfg = KiroCrewConfig()
    seed_saokhue_defaults(cfg)
    cfg.save()
    assert declare_saokhue_harness(path) is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["agent"]["acp_backend"] == "saokhue"
    assert stored["saokhue"] == {
        "base_url": "https://portal.aws.rpa.vpbank.dev/api/ai-llm",
        "client_id": "app:test-crew",
    }
    # A config that already holds a choice is never rewritten.
    assert declare_saokhue_harness(path) is False
