"""``agent.openai_compatible``: an OpenAI-compatible endpoint seeded into OpenCode.

The config field and its write floor, the provider block the seed registers, its
merge with the routing seed the read-back verifies, and the launch that refuses a
broken entry before any harness child starts and feeds the key from Crew's vault.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kiro_crew.acp import launch as launch_mod
from kiro_crew.acp.client import AcpClient
from kiro_crew.acp.harness import opencode as opencode_mod
from kiro_crew.acp.harness.base import SpawnContext
from kiro_crew.acp.harness.opencode_openai_compat import (
    OPENAI_COMPAT_KEY_ENV,
    OPENAI_COMPAT_NPM,
    OPENAI_COMPAT_PROVIDER_ID,
    merge_openai_compat_seed,
    openai_compat_key_env,
    openai_compat_seed,
)
from kiro_crew.acp.transport_errors import AcpError
from kiro_crew.acp_backends import ACP_BACKEND_OPENCODE
from kiro_crew.config import sections

_ENV_CONFIG = "OPENCODE_CONFIG_CONTENT"
_USABLE = {"base_url": "https://llm.example.test/v1", "model": "qwen3-coder"}
# A canary, never a real credential: a test that finds it in an error message has
# found a message that echoes values.
_CANARY_KEY = "canary-not-a-real-key-7f3a"


# ── The config field ──────────────────────────────────────────────────────────


class TestCoercion:
    def test_known_keys_are_kept_and_stripped(self):
        raw = {"base_url": " https://x.test/v1 ", "model": "m", "name": " Local "}
        assert sections.coerce_openai_compatible(raw) == {
            "base_url": "https://x.test/v1",
            "model": "m",
            "name": "Local",
        }

    def test_an_integer_budget_is_stored_as_its_decimal_string(self):
        coerced = sections.coerce_openai_compatible({"context_tokens": 32768})
        assert coerced == {"context_tokens": "32768"}

    def test_an_unknown_key_and_a_non_string_value_are_dropped(self):
        raw = {"bogus": "x", "model": 3, "base_url": True, "name": ""}
        assert sections.coerce_openai_compatible(raw) == {}

    @pytest.mark.parametrize("raw", [None, "https://x.test", ["a"], 7])
    def test_a_non_mapping_is_off(self, raw):
        assert sections.coerce_openai_compatible(raw) == {}


class TestIssues:
    def test_an_empty_mapping_is_the_off_state_not_an_error(self):
        assert sections.openai_compatible_issues({}) == ()

    def test_a_usable_entry_has_no_issue(self):
        mapping = {**_USABLE, "api_key": "secret://llm", "context_tokens": "32768"}
        assert sections.openai_compatible_issues(mapping) == ()

    @pytest.mark.parametrize(
        "mapping, fragment",
        [
            pytest.param({"model": "m"}, "'base_url' is missing", id="no-url"),
            pytest.param({"base_url": "https://x.test/v1"}, "'model' is missing", id="no-model"),
            pytest.param(
                {"base_url": "ftp://x.test", "model": "m"}, "http:// or https://", id="scheme"
            ),
            pytest.param(
                {"base_url": "https://x.test/a b", "model": "m"}, "whitespace", id="space"
            ),
            pytest.param({**_USABLE, "context_tokens": "512"}, "'context_tokens'", id="small"),
            pytest.param({**_USABLE, "output_tokens": "lots"}, "'output_tokens'", id="nan"),
        ],
    )
    def test_each_unusable_field_is_named(self, mapping, fragment):
        assert any(fragment in issue for issue in sections.openai_compatible_issues(mapping))

    def test_a_plaintext_key_is_an_issue_that_never_echoes_the_value(self):
        issues = sections.openai_compatible_issues({**_USABLE, "api_key": _CANARY_KEY})
        assert any("'api_key' holds a literal value" in issue for issue in issues)
        assert _CANARY_KEY not in " ".join(issues)

    @pytest.mark.parametrize(
        "raw, plaintext",
        [
            ({"api_key": _CANARY_KEY}, True),
            ({"api_key": "secret://llm"}, False),
            ({"api_key": ""}, False),
            ({}, False),
            (None, False),
            ({"api_key": 12345}, True),
        ],
    )
    def test_plaintext_detection(self, raw, plaintext):
        assert sections.openai_compatible_plaintext_key(raw) is plaintext


def _write_config(monkeypatch, tmp_path, agent: dict) -> None:
    from kiro_crew.config.paths import config_dir

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "config.json").write_text(json.dumps({"agent": agent}), encoding="utf-8")


class TestConfigDocument:
    def test_a_plaintext_key_is_refused_at_write_and_nothing_lands(self, monkeypatch, tmp_path):
        from kiro_crew.config.loader import (
            ConfigWriteRefused,
            config_path,
            write_config_atomically,
        )

        monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {"agent": {"openai_compatible": {**_USABLE, "api_key": _CANARY_KEY}}}
        with pytest.raises(ConfigWriteRefused) as refused:
            write_config_atomically(path, document)
        assert _CANARY_KEY not in str(refused.value)
        assert not path.exists()

    def test_a_reference_round_trips_through_load(self, monkeypatch, tmp_path):
        from kiro_crew.config.loader import KiroCrewConfig

        entry = {**_USABLE, "api_key": "secret://llm", "context_tokens": 65536}
        _write_config(monkeypatch, tmp_path, {"openai_compatible": entry})
        assert KiroCrewConfig.load().agent.openai_compatible == {
            **_USABLE,
            "api_key": "secret://llm",
            "context_tokens": "65536",
        }

    def test_the_default_is_off(self, monkeypatch, tmp_path):
        from kiro_crew.config.loader import KiroCrewConfig

        _write_config(monkeypatch, tmp_path, {})
        assert KiroCrewConfig.load().agent.openai_compatible == {}


# ── The provider seed ─────────────────────────────────────────────────────────


class TestSeed:
    def test_off_is_no_seed(self):
        assert openai_compat_seed({}) == {}

    def test_the_provider_block_registers_the_endpoint_and_its_model(self):
        seed = openai_compat_seed({**_USABLE, "context_tokens": "32768", "output_tokens": "8192"})
        provider = seed["provider"][OPENAI_COMPAT_PROVIDER_ID]
        assert provider["npm"] == OPENAI_COMPAT_NPM
        assert provider["options"] == {"baseURL": _USABLE["base_url"]}
        assert provider["models"] == {
            "qwen3-coder": {"name": "qwen3-coder", "limit": {"context": 32768, "output": 8192}}
        }
        assert seed["model"] == f"{OPENAI_COMPAT_PROVIDER_ID}/qwen3-coder"

    def test_a_keyless_endpoint_names_no_key_variable(self):
        provider = openai_compat_seed(_USABLE)["provider"][OPENAI_COMPAT_PROVIDER_ID]
        assert "env" not in provider
        assert "apiKey" not in provider["options"]

    def test_a_keyed_endpoint_names_the_variable_never_the_value(self):
        seed = openai_compat_seed({**_USABLE, "api_key": "secret://llm"})
        provider = seed["provider"][OPENAI_COMPAT_PROVIDER_ID]
        assert provider["env"] == [OPENAI_COMPAT_KEY_ENV]
        # Neither the reference nor a substitution template reaches the document.
        assert "secret://" not in json.dumps(seed)
        assert "{env:" not in json.dumps(seed)

    def test_an_unusable_entry_raises_naming_keys_only(self):
        with pytest.raises(ValueError) as raised:
            openai_compat_seed({"base_url": "ftp://x.test", "api_key": _CANARY_KEY})
        message = str(raised.value)
        assert "'base_url'" in message and "'model'" in message and "'api_key'" in message
        assert _CANARY_KEY not in message


class TestMerge:
    def test_crews_provider_lands_beside_the_operators(self):
        config = {"provider": {"ollama": {"npm": "x"}}, "small_model": "ollama/tiny"}
        merge_openai_compat_seed(config, openai_compat_seed(_USABLE))
        assert set(config["provider"]) == {"ollama", OPENAI_COMPAT_PROVIDER_ID}
        assert config["model"] == f"{OPENAI_COMPAT_PROVIDER_ID}/qwen3-coder"
        assert config["small_model"] == "ollama/tiny", "an operator's small_model is kept"

    def test_small_model_follows_the_endpoint_when_unset(self):
        config: dict = {}
        merge_openai_compat_seed(config, openai_compat_seed(_USABLE))
        assert config["small_model"] == config["model"]

    def test_the_routing_seed_still_asks_after_the_merge(self, monkeypatch):
        """The endpoint rides the routing seed and must never displace the posture."""
        monkeypatch.setenv(_ENV_CONFIG, json.dumps({"permission": "allow", "model": "a/b"}))
        merged = json.loads(
            opencode_mod._opencode_routing_config(
                ACP_BACKEND_OPENCODE, (), openai_compat_seed(_USABLE)
            )
        )
        assert merged["permission"] == "ask"
        assert merged["model"] == f"{OPENAI_COMPAT_PROVIDER_ID}/qwen3-coder"
        assert OPENAI_COMPAT_PROVIDER_ID in merged["provider"]

    def test_without_an_endpoint_the_seed_is_unchanged(self, monkeypatch):
        monkeypatch.delenv(_ENV_CONFIG, raising=False)
        assert json.loads(opencode_mod._opencode_routing_config(ACP_BACKEND_OPENCODE)) == {
            "permission": "ask"
        }


# ── The key ───────────────────────────────────────────────────────────────────


class TestKeyEnv:
    def test_no_key_is_no_variable(self):
        assert openai_compat_key_env(_USABLE) == {}

    def test_a_literal_is_refused_even_past_the_write_floor(self):
        """A hand edit can land a plaintext the write floor never saw; the resolver
        would pass it through verbatim, so the spawn refuses it itself."""
        with pytest.raises(ValueError) as raised:
            openai_compat_key_env({**_USABLE, "api_key": _CANARY_KEY})
        assert _CANARY_KEY not in str(raised.value)

    def test_a_reference_resolves_from_the_vault(self, monkeypatch, tmp_path):
        from kiro_crew.config.paths import config_dir
        from kiro_crew.secrets import SecretVault

        monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
        config_dir().mkdir(parents=True, exist_ok=True)
        SecretVault(config_dir()).set_sync("llm", _CANARY_KEY)
        assert openai_compat_key_env({**_USABLE, "api_key": "secret://llm"}) == {
            OPENAI_COMPAT_KEY_ENV: _CANARY_KEY
        }

    def test_a_missing_secret_fails_closed(self, monkeypatch, tmp_path):
        from kiro_crew.config.paths import config_dir

        monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
        config_dir().mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError):
            openai_compat_key_env({**_USABLE, "api_key": "secret://absent"})


# ── The launch ────────────────────────────────────────────────────────────────


class _Rig:
    """An ``OpencodeLaunch`` with every seam that would start a real child replaced.

    Records which seams ran and the config content the read-back was handed; the
    launch logic itself -- the endpoint load, the seed, the merge -- is real.
    """

    def __init__(self, monkeypatch, tmp_path):
        self.events: list[str] = []
        self.verified_config: str | None = None
        self.client = AcpClient(work_dir=tmp_path, acp_backend=ACP_BACKEND_OPENCODE)
        self.adapter = opencode_mod.OpencodeLaunch()
        self._tmp_path = tmp_path

        async def _launch(backend):
            self.events.append("resolve")
            return "/opt/bin/opencode", ["/opt/bin/opencode", "acp"], "opencode acp", "opencode"

        async def _prepare_session_mcp():
            self.events.append("session_mcp")

        async def _preflight(preflight, backend, mode):
            self.events.append("preflight")
            return ()

        async def _wrap(argv, **kwargs):
            self.events.append("wrap")
            return list(argv), None

        def _verify(session, backend, argv, config_content):
            self.events.append("verify")
            self.verified_config = config_content
            return "", ""

        class _Completed:
            returncode = 0
            stdout = "1.19.0"
            stderr = ""

        def _run(argv, **kwargs):
            self.events.append("version_probe")
            return _Completed()

        monkeypatch.setattr(launch_mod, "resolve_self_served_launch", _launch)
        monkeypatch.setattr(self.client, "_prepare_session_mcp", _prepare_session_mcp)
        monkeypatch.setattr(launch_mod, "_run_preflight_bounded", _preflight)
        monkeypatch.setattr(opencode_mod, "wrap_argv_async", _wrap)
        monkeypatch.setattr(opencode_mod, "_verify_opencode_routing", _verify)
        monkeypatch.setattr(launch_mod, "_unlink_readback_launcher", lambda _p: None)
        monkeypatch.setattr(opencode_mod.subprocess_mod, "run", _run)

    def resolve(self):
        return asyncio.run(
            self.adapter.resolve_spawn(
                SpawnContext(
                    agent="kirocrew",
                    work_dir=self._tmp_path,
                    model=None,
                    environ={},
                    home=self._tmp_path,
                    sandbox_mode="standard",
                    session=self.client,
                )
            )
        )

    def spawn_env(self) -> dict[str, str]:
        env: dict[str, str] = {}

        async def _to_thread(fn, *args):
            return await asyncio.to_thread(fn, *args)

        asyncio.run(self.adapter.prepare_spawn_env(env, to_thread=_to_thread))
        return env


def test_an_unusable_entry_refuses_before_any_harness_child(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {"openai_compatible": {"model": "m"}})
    rig = _Rig(monkeypatch, tmp_path)
    with pytest.raises(AcpError) as refused:
        rig.resolve()
    assert "'base_url' is missing" in str(refused.value)
    assert "version_probe" not in rig.events and "verify" not in rig.events


def test_the_read_back_judges_the_merged_document(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, {"openai_compatible": _USABLE})
    rig = _Rig(monkeypatch, tmp_path)
    rig.resolve()
    assert rig.verified_config is not None
    verified = json.loads(rig.verified_config)
    assert verified["permission"] == "ask"
    assert verified["model"] == f"{OPENAI_COMPAT_PROVIDER_ID}/qwen3-coder"
    env = rig.spawn_env()
    assert json.loads(env[_ENV_CONFIG]) == verified, "the child gets the document verified"
    assert OPENAI_COMPAT_KEY_ENV not in env, "a keyless endpoint puts no key variable"


def test_the_key_reaches_the_child_from_the_vault(monkeypatch, tmp_path):
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets import SecretVault

    _write_config(
        monkeypatch, tmp_path, {"openai_compatible": {**_USABLE, "api_key": "secret://llm"}}
    )
    SecretVault(config_dir()).set_sync("llm", _CANARY_KEY)
    rig = _Rig(monkeypatch, tmp_path)
    rig.resolve()
    assert _CANARY_KEY not in (rig.verified_config or ""), "the read-back never sees the key"
    env = rig.spawn_env()
    assert env[OPENAI_COMPAT_KEY_ENV] == _CANARY_KEY
    assert _CANARY_KEY not in env[_ENV_CONFIG]


def test_a_missing_secret_refuses_the_spawn(monkeypatch, tmp_path):
    _write_config(
        monkeypatch, tmp_path, {"openai_compatible": {**_USABLE, "api_key": "secret://absent"}}
    )
    rig = _Rig(monkeypatch, tmp_path)
    rig.resolve()
    with pytest.raises(AcpError) as refused:
        rig.spawn_env()
    assert "agent.openai_compatible" in str(refused.value)


def test_the_key_variable_survives_the_agent_environment_scrub():
    """Injected before the scrub, so a name the scrub strips would arrive empty."""
    from kiro_crew.sandbox import agent_env_scrub_prefixes

    assert not any(OPENAI_COMPAT_KEY_ENV.startswith(p) for p in agent_env_scrub_prefixes())
