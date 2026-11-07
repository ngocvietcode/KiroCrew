"""Upstream parsing, bypass rules and refused destinations of the local proxy."""

from __future__ import annotations

import pytest

from kiro_crew.auth_proxy.auth import BasicAuth, NoAuth
from kiro_crew.auth_proxy.upstream import (
    Upstream,
    UpstreamError,
    bypasses_upstream,
    is_forbidden_target,
    merge_no_proxy,
    parse_upstream,
    split_authority,
)
from kiro_crew.config.sections_network import NetworkConfig, build_network_config


@pytest.mark.parametrize(
    "value, expected",
    [
        ("http://proxynorth.corp.test:8080", Upstream("proxynorth.corp.test", 8080)),
        ("proxynorth.corp.test:3128", Upstream("proxynorth.corp.test", 3128)),
        ("proxynorth.corp.test", Upstream("proxynorth.corp.test", 8080)),
        (
            "http://alice:p%40ss@proxy.corp.test:8080",
            Upstream("proxy.corp.test", 8080, "alice", "p@ss"),
        ),
        ("http://[2001:db8::1]:8080", Upstream("2001:db8::1", 8080)),
    ],
)
def test_parse_upstream(value, expected):
    assert parse_upstream(value) == expected


@pytest.mark.parametrize(
    "value", ["", "   ", "https://proxy.corp.test:8443", "socks5://proxy:1080", "http://:8080"]
)
def test_parse_upstream_refuses_what_it_cannot_speak(value):
    with pytest.raises(UpstreamError):
        parse_upstream(value)


def test_upstream_repr_hides_the_password():
    upstream = parse_upstream("http://alice:hunter2@proxy.corp.test:8080")
    assert "hunter2" not in repr(upstream)
    assert upstream.authority == "proxy.corp.test:8080"
    assert parse_upstream("http://[2001:db8::1]:8080").authority == "[2001:db8::1]:8080"


def test_merge_no_proxy_keeps_order_drops_duplicates_and_adds_loopback():
    merged = merge_no_proxy(".corp.test, Intranet", "intranet;.vpbank.dev", "")
    assert merged == ".corp.test,Intranet,.vpbank.dev,localhost,127.0.0.1,::1"


@pytest.mark.parametrize(
    "host, no_proxy, expected",
    [
        ("api.corp.test", ".corp.test", True),
        ("corp.test", ".corp.test", True),
        ("api.corp.test", "corp.test", True),
        ("evilcorp.test", "corp.test", False),
        ("oidc.us-east-1.amazonaws.com", ".corp.test,localhost", False),
        ("anything.example", "*", True),
        ("anything.example", "", False),
    ],
)
def test_bypass_rules_match_the_standard_library(host, no_proxy, expected):
    assert bypasses_upstream(host, no_proxy) is expected


@pytest.mark.parametrize(
    "host, refused",
    [
        ("127.0.0.1", True),
        ("127.5.6.7", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("app.localhost", True),
        ("169.254.169.254", True),
        ("0.0.0.0", True),
        ("10.0.0.5", False),
        ("oidc.us-east-1.amazonaws.com", False),
    ],
)
def test_forbidden_targets(host, refused):
    assert is_forbidden_target(host) is refused


def test_split_authority():
    assert split_authority("example.test:443", 443) == ("example.test", 443)
    assert split_authority("example.test", 443) == ("example.test", 443)
    assert split_authority("[2001:db8::1]:8443", 443) == ("2001:db8::1", 8443)
    with pytest.raises(ValueError):
        split_authority(":443", 443)


def test_basic_auth_header_and_repr():
    auth = BasicAuth("alice", "p@ss:word")
    assert auth.header() == "Basic YWxpY2U6cEBzczp3b3Jk"
    assert "p@ss" not in repr(auth) and "p@ss" not in str(auth)
    assert NoAuth().begin() is None and NoAuth().begin_from(["Basic"]) is None


def test_network_section_defaults_off():
    assert build_network_config({}) == NetworkConfig()


def test_network_section_refuses_a_plain_password(caplog):
    with caplog.at_level("WARNING", logger="kiro_crew.config.loader"):
        built = build_network_config({"proxy_enabled": True, "proxy_password": "hunter2"})
    assert built.proxy_password == ""
    assert "hunter2" not in caplog.text
    assert "network.proxy_password must be a secret://NAME" in caplog.text


def test_network_section_keeps_a_vault_reference_and_trims_text():
    built = build_network_config(
        {
            "proxy_enabled": True,
            "proxy_url": " http://proxy.corp.test:8080 ",
            "proxy_username": " alice ",
            "proxy_password": "secret://CORP_PROXY",
            "proxy_no_proxy": " .corp.test ",
        }
    )
    assert built == NetworkConfig(
        proxy_enabled=True,
        proxy_url="http://proxy.corp.test:8080",
        proxy_username="alice",
        proxy_password="secret://CORP_PROXY",
        proxy_no_proxy=".corp.test",
    )


def test_no_log_call_in_the_proxy_touches_a_secret():
    """No ``logger.*`` argument names a password, a token or a header value."""
    import ast
    from pathlib import Path

    import kiro_crew.auth_proxy as pkg

    secret_names = {"password", "token", "_value", "credentials", "auth_header"}
    offenders: list[str] = []
    root = Path(pkg.__file__).parent
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner = node.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "logger"):
                continue
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                for sub in ast.walk(arg):
                    name = sub.id if isinstance(sub, ast.Name) else getattr(sub, "attr", None)
                    if name in secret_names or (
                        isinstance(sub, ast.Attribute) and sub.attr == "header"
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, offenders
