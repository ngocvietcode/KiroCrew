"""``agent.openai_compatible``: an OpenAI-compatible endpoint, seeded into OpenCode.

OpenCode reaches any server that speaks the OpenAI chat-completions API through a
CUSTOM PROVIDER in its own configuration: an ``@ai-sdk/openai-compatible`` provider
with a ``baseURL`` and a list of models. Crew already hands this harness its routing
seed as inline config on ``OPENCODE_CONFIG_CONTENT``, so the endpoint rides the SAME
seed -- one provider under :data:`OPENAI_COMPAT_PROVIDER_ID`, plus the top-level
``model`` that makes it the session's default -- and the routing read-back judges the
merged document, so the endpoint can never displace the permission posture.

The key never enters that document. A config provider names the environment
variables its key may come from (``env``), and the harness reads the first one set
straight out of its own process environment (opencode 1.19's ``provider.ts``: the
``env`` list feeds ``provider.key``, which becomes ``options.apiKey``). So the seed
carries only the NAME :data:`OPENAI_COMPAT_KEY_ENV`, and the value -- resolved from
Crew's vault -- is placed on the child's environment at spawn
(:func:`openai_compat_key_env`). That keeps the key out of ``config.json``, out of
the routing read-back's output (the read-back child never receives it), and out of
any ``{env:...}`` text substitution, which splices a value into JSON unescaped.

What the key is exposed to, stated rather than implied: the harness's own process
tree, which includes the shell its bash tool runs. That is the same reach the
harness's own sign-in already has under Crew -- ``host_auth`` spares
``.local/share/opencode/auth.json`` from the credential mask for exactly this
process -- and every tool call that shell makes still asks Crew's gate first,
because the routing this module merges into is verified before the first prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kiro_crew.config.paths import config_dir
from kiro_crew.config.sections_agent_env import openai_compatible_issues
from kiro_crew.mcp_gateway.secret_uri import SECRET_URI_PREFIX, resolve_secret_uris

__all__ = [
    "OPENAI_COMPAT_KEY_ENV",
    "OPENAI_COMPAT_NPM",
    "OPENAI_COMPAT_PROVIDER_ID",
    "load_openai_compatible",
    "merge_openai_compat_seed",
    "openai_compat_key_env",
    "openai_compat_seed",
]

#: The provider id the endpoint is registered under in the harness's config, and so
#: the prefix of its model id (``kirocrew-openai/<model>``). Crew's own, so an
#: operator provider of any other id is never overwritten.
OPENAI_COMPAT_PROVIDER_ID = "kirocrew-openai"

#: The harness's package for a ``/v1/chat/completions`` endpoint.
OPENAI_COMPAT_NPM = "@ai-sdk/openai-compatible"

#: The environment variable the provider reads its key from. In Crew's namespace,
#: because Crew writes it; the session-identity pass sets named variables only and
#: the agent environment scrub strips none with this prefix, so it reaches the child.
OPENAI_COMPAT_KEY_ENV = "KIROCREW_OPENAI_COMPAT_API_KEY"

#: The display name when the operator gives none.
_DEFAULT_PROVIDER_NAME = "OpenAI-compatible (Kiro Crew)"


def load_openai_compatible() -> dict[str, str]:
    """The coerced ``agent.openai_compatible`` mapping, ``{}`` when it is off.

    Blocking (reads ``config.json``); callers run it off the loop. Config is imported
    lazily for the reason every harness module gives: ``config.loader`` reaches this
    package through ``acp.session_handle``.
    """
    from kiro_crew.config.loader import KiroCrewConfig

    return dict(KiroCrewConfig.load().agent.openai_compatible)


def openai_compat_seed(mapping: dict[str, str]) -> dict[str, Any]:
    """The config keys that register *mapping*'s endpoint, or ``{}`` when it is off.

    Raises :exc:`ValueError` naming every field that keeps the entry from being
    usable (``sections.openai_compatible_issues``); the message names keys only and
    never echoes a value, so it is safe on a log line and in a chat error card. A
    refusal rather than a skip: an operator who configured an endpoint and got a
    session on some other model would have no error telling them why.
    """
    if not mapping:
        return {}
    issues = openai_compatible_issues(mapping)
    if issues:
        raise ValueError("agent.openai_compatible cannot be used: " + "; ".join(issues) + ".")
    model_id = mapping["model"]
    model: dict[str, Any] = {"name": model_id}
    limit: dict[str, int] = {}
    if "context_tokens" in mapping:
        limit["context"] = int(mapping["context_tokens"])
    if "output_tokens" in mapping:
        limit["output"] = int(mapping["output_tokens"])
    if limit:
        model["limit"] = limit
    provider: dict[str, Any] = {
        "npm": OPENAI_COMPAT_NPM,
        "name": mapping.get("name") or _DEFAULT_PROVIDER_NAME,
        "options": {"baseURL": mapping["base_url"]},
        "models": {model_id: model},
    }
    if mapping.get("api_key"):
        provider["env"] = [OPENAI_COMPAT_KEY_ENV]
    qualified = f"{OPENAI_COMPAT_PROVIDER_ID}/{model_id}"
    return {"provider": {OPENAI_COMPAT_PROVIDER_ID: provider}, "model": qualified}


def merge_openai_compat_seed(config: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    """*config* with *seed* merged in, in place, and returned.

    The provider lands beside every provider the operator's ambient config already
    declares, replacing only Crew's own id. ``model`` is Crew's: the operator asked
    for this endpoint by configuring it. ``small_model`` -- what the harness uses for
    titles and summaries -- follows it unless the operator chose one, because its own
    default names a hosted provider that an install pointed at a private endpoint
    usually cannot reach.
    """
    if not seed:
        return config
    providers = config.get("provider")
    merged_providers = dict(providers) if isinstance(providers, dict) else {}
    merged_providers.update(seed["provider"])
    config["provider"] = merged_providers
    config["model"] = seed["model"]
    if not config.get("small_model"):
        config["small_model"] = seed["model"]
    return config


def openai_compat_key_env(mapping: dict[str, str]) -> dict[str, str]:
    """The child-environment entry carrying the endpoint's key, resolved from the vault.

    ``{}`` when the endpoint takes no key. Raises :exc:`ValueError` for a reference
    that is malformed or names no stored secret (the resolver's own fail-closed
    refusals, which name the variable and never the vault name or value). The caller
    copies the entry onto the child's environment and clears this dict, the
    resolver's contract for the plaintext it returns. Blocking (reads the vault).
    """
    reference = mapping.get("api_key", "")
    if not reference:
        return {}
    if not reference.startswith(SECRET_URI_PREFIX):
        # The resolver passes a non-reference through verbatim, so this is the floor
        # that keeps a hand-edited plaintext from reaching the child as if it were
        # vault-backed.
        raise ValueError(
            "agent.openai_compatible 'api_key' holds a literal value; it takes a "
            f"'{SECRET_URI_PREFIX}<vault name>' reference only. Save the key under "
            "Settings > Secrets, then set it as 'secret://<vault name>'."
        )
    resolved, _ = resolve_secret_uris(
        {OPENAI_COMPAT_KEY_ENV: reference},
        Path(config_dir()),
        subject="agent.openai_compatible",
    )
    return resolved
