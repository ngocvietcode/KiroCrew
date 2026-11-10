"""The wire constants, defaults, tool schemas and per-user home of ``saokhue-cli``.

An owner of :mod:`kiro_crew.acp.saokhue_server`; it never imports that facade.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import aiohttp  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - stdlib urllib fallback used when aiohttp is absent
    aiohttp = None  # type: ignore[assignment]


# ACP JSON-RPC method and update constants (defined inline so running this module
# as a standalone script never imports ``kiro_crew.acp.__init__``).
METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SESSION_LOAD = "session/load"
METHOD_SESSION_RESUME = "session/resume"
METHOD_SET_MODEL = "session/set_model"
METHOD_SET_MODE = "session/set_mode"
METHOD_SET_CONFIG_OPTION = "session/set_config_option"
METHOD_PROMPT = "session/prompt"
METHOD_CANCEL = "session/cancel"
METHOD_REQUEST_PERMISSION = "session/request_permission"
METHOD_SESSION_UPDATE = "session/update"
METHOD_METADATA = "_kiro.dev/metadata"
METHOD_SESSION_TERMINATE = "_kiro.dev/session/terminate"
METHOD_SESSION_CLOSE = "session/close"
METHOD_AGENT_SWITCHED = "_kiro.dev/agent/switched"
METHOD_MCP_SERVER_INITIALIZED = "_kiro.dev/mcp/server_initialized"
METHOD_MCP_SERVER_INIT_FAILURE = "_kiro.dev/mcp/server_init_failure"
MODEL_CONFIG_ID = "model"
OPTION_ALLOW_ONCE = "allow_once"
OPTION_ALLOW_ALWAYS = "allow_always"
STOP_REASON_CANCELLED = "cancelled"
STOP_REASON_END_TURN = "end_turn"
UPDATE_AGENT_MESSAGE_CHUNK = "agent_message_chunk"
UPDATE_AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
UPDATE_TOOL_CALL = "tool_call"
UPDATE_TOOL_CALL_UPDATE = "tool_call_update"
UPDATE_USAGE = "usage_update"

PROTOCOL_VERSION = "2025-08-22"
SERVER_NAME = "saokhue-cli"
SERVER_VERSION = "1.0.0"

DEFAULT_SAOKHUE_BASE_URL = "https://portal.aws.rpa.vpbank.dev/api/ai-llm"
DEFAULT_OIDC_ISSUER = "https://portal.aws.rpa.vpbank.dev/api"
DEFAULT_OIDC_AUTHORIZE_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/authorize"
DEFAULT_OIDC_DEVICE_AUTH_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/device/auth"
DEFAULT_OIDC_TOKEN_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/token"
DEFAULT_OIDC_USERINFO_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/me"
DEFAULT_OIDC_REVOKE_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/revoke"
DEFAULT_OIDC_END_SESSION_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/end-session"
DEFAULT_OIDC_REGISTER_URL = "https://portal.aws.rpa.vpbank.dev/api/idpOAuth/register"
DEFAULT_OIDC_CLIENT_ID = "app:test-crew"
DEFAULT_OIDC_SCOPE = "openid offline_access profile email api"
DEFAULT_OIDC_RESOURCE = "https://portal.aws.rpa.vpbank.dev/api/"
DEFAULT_FALLBACK_MODEL = "saokhue-default"
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
MAX_AGENT_TURNS = 40
MCP_CONNECT_TIMEOUT_SECS = 15.0
MCP_CALL_TIMEOUT_SECS = 120.0

#: Built-in tool definitions exposed to the OpenAI-compatible Chat Completions endpoint.
BUILTIN_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fs_read",
            "description": "Read the contents of a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative to workspace or absolute).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "1-based starting line number (optional).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (optional).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_write",
            "description": "Write or overwrite a file with the given text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to create or overwrite.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string occurrence in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Exact existing text to replace.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List entries in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (defaults to workspace root).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern such as '**/*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search from (optional).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regular expression pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path to search in (optional).",
                    },
                    "include": {
                        "type": "string",
                        "description": "Filename glob filter such as '*.py' (optional).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command line to run.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory for the command (optional).",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (optional, default 120).",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

BUILTIN_TOOL_KINDS: dict[str, str] = {
    "fs_read": "read",
    "read_file": "read",
    "list_directory": "read",
    "glob": "search",
    "grep": "search",
    "fs_write": "edit",
    "write_file": "edit",
    "edit_file": "edit",
    "execute_bash": "execute",
    "run_command": "execute",
}


def saokhue_cli_home(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user ``saokhue-cli`` home directory (``~/.saokhue-cli`` or ``$SAOKHUE_CLI_HOME``)."""
    env = os.environ if environ is None else environ
    override = (env.get("SAOKHUE_CLI_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".saokhue-cli"


def saokhue_auth_path(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user credential file ``~/.saokhue-cli/auth.json``."""
    return saokhue_cli_home(environ) / "auth.json"
