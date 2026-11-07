"""Gateway side of the local proxy: decide the upstream, launch it, export it, keep it up.

:func:`start_for_gateway` runs once at gateway start, before any service opens a
connection. When ``network.proxy_enabled`` is on it starts the proxy process and
rewrites this process's ``HTTP(S)_PROXY`` / ``NO_PROXY``, so the gateway's own
clients and every child it spawns afterwards inherit the loopback address. The
proxy password is read from the vault here and handed to the proxy on its stdin;
it never enters ``os.environ``.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import IO, Any

from kiro_crew import platform_compat
from kiro_crew.auth_proxy import sspi
from kiro_crew.auth_proxy.auth import resolve_auth_mode
from kiro_crew.auth_proxy.upstream import (
    LOOPBACK_NO_PROXY,
    UpstreamError,
    merge_no_proxy,
    parse_upstream,
)

logger = logging.getLogger("kiro_crew.auth_proxy")

#: How long a launched proxy may take to report the port it listens on.
READY_TIMEOUT_SECS = 15.0
#: Waits between restarts of a proxy that keeps exiting; the last one repeats.
RESTART_BACKOFF_SECS = (1.0, 2.0, 5.0, 10.0, 30.0)
#: A run at least this long resets the backoff.
_STABLE_RUN_SECS = 60.0
#: How long a stop waits for the proxy to exit after its stdin closes.
_STOP_GRACE_SECS = 3.0
#: The user name in the loopback URL; the token is the password.
CLIENT_USER = "crew"
#: Set on export to the upstream (without credentials), so a gateway that a running
#: gateway respawns -- and that inherits the loopback address -- finds the upstream.
ORIGIN_ENV = "KIROCREW_AUTH_PROXY_ORIGIN"
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
NO_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Reads one vault secret by name; ``None`` when the vault holds none.
SecretReader = Callable[[str], "str | None"]


class LaunchRefused(Exception):
    """The proxy is not started; the message says why, and names no secret."""


@dataclass(frozen=True)
class Launch:
    """What one proxy process is started with."""

    upstream: str
    username: str
    no_proxy: str
    #: Resolved sign-in: ``basic``, ``windows`` or ``none``.
    auth: str = "basic"
    password: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    upstream_from_env: bool = False

    def line(self, port: int) -> bytes:
        payload = {
            "token": self.token,
            "upstream": self.upstream,
            "username": self.username,
            "password": self.password,
            "no_proxy": self.no_proxy,
            "auth": self.auth,
            "port": port,
        }
        return (json.dumps(payload) + "\n").encode("utf-8")


def _env_get(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return ""


def _is_local_proxy(raw: str) -> bool:
    try:
        upstream = parse_upstream(raw)
    except UpstreamError:
        return False
    return upstream.host in LOOPBACK_NO_PROXY or upstream.username == CLIENT_USER


def plan_launch(network: Any, env: Mapping[str, str], read_secret: SecretReader) -> Launch:
    """Decide the upstream and credentials from ``network`` settings and the boot env.

    Pure apart from *read_secret*, so every rule is tested without a process.
    """
    raw = network.proxy_url
    from_env = False
    if not raw:
        raw = _env_get(env, "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
        from_env = bool(raw)
        if raw and _is_local_proxy(raw):
            raw = _env_get(env, ORIGIN_ENV)
    if not raw:
        raise LaunchRefused(
            "no company proxy is set: fill network.proxy_url or start with HTTPS_PROXY"
        )
    try:
        upstream = parse_upstream(raw)
    except UpstreamError as exc:
        raise LaunchRefused(str(exc)) from exc
    if upstream.host in LOOPBACK_NO_PROXY:
        raise LaunchRefused(f"the company proxy {upstream.authority} is a loopback address")

    mode = resolve_auth_mode(
        getattr(network, "proxy_auth", "auto"),
        network.proxy_username or upstream.username,
        windows_available=sspi.available(),
    )
    if mode == "windows":
        if not sspi.available():
            raise LaunchRefused("Windows sign-in (network.proxy_auth = windows) needs Windows")
        return Launch(
            upstream=f"http://{upstream.authority}",
            username="",
            no_proxy=merge_no_proxy(_env_get(env, "NO_PROXY", "no_proxy"), network.proxy_no_proxy),
            token=secrets.token_urlsafe(32),
            upstream_from_env=from_env,
            auth="windows",
        )
    if network.proxy_username:
        username, password = network.proxy_username, ""
        ref = network.proxy_password
        if ref:
            value = read_secret(ref.split("://", 1)[1])
            if value is None:
                raise LaunchRefused(
                    "the vault holds no secret under the name network.proxy_password gives"
                )
            password = value
    else:
        username, password = upstream.username, upstream.password
        if username:
            logger.warning(
                "auth-proxy: the proxy URL in the environment carries a password; "
                "store it under Settings > Secrets and set network.proxy_username instead"
            )
    return Launch(
        upstream=f"http://{upstream.authority}",
        username=username,
        password=password,
        no_proxy=merge_no_proxy(_env_get(env, "NO_PROXY", "no_proxy"), network.proxy_no_proxy),
        token=secrets.token_urlsafe(32),
        upstream_from_env=from_env,
        auth="basic" if username else "none",
    )


def loopback_url(token: str, port: int) -> str:
    return f"http://{CLIENT_USER}:{token}@127.0.0.1:{port}"


def export_env(env: MutableMapping[str, str], launch: Launch, port: int) -> None:
    """Point *env* at the local proxy; children started afterwards inherit it."""
    url = loopback_url(launch.token, port)
    for key in PROXY_ENV_KEYS:
        env[key] = url
    for key in NO_PROXY_ENV_KEYS:
        env[key] = launch.no_proxy
    env[ORIGIN_ENV] = launch.upstream


def proxy_argv() -> list[str]:
    """The proxy process command: this interpreter, user site and cwd off ``sys.path``."""
    return [sys.executable, "-s", "-P", "-m", "kiro_crew.auth_proxy"]


def _relay_stderr(stream: IO[bytes]) -> None:
    """Copy the proxy's log lines into the gateway log at their own level."""
    levels = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING}
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        level_name, _, message = line.partition(" ")
        level = levels.get(level_name)
        if level is None:
            level, message = (
                logging.WARNING if level_name in ("ERROR", "CRITICAL") else logging.INFO
            ), line
        logger.log(level, "%s", message)


class ProxySupervisor:
    """Owns one proxy process: starts it, restarts it on the same port, stops it."""

    def __init__(
        self,
        launch: Launch,
        *,
        argv: list[str] | None = None,
        ready_timeout: float = READY_TIMEOUT_SECS,
    ) -> None:
        self._launch = launch
        self._argv = argv or proxy_argv()
        self._ready_timeout = ready_timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._port = 0
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._spawned = threading.Condition()
        self._generation = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def alive(self) -> bool:
        with self._lock:
            proc = self._proc
        return proc is not None and proc.poll() is None

    def wait_for_generation(self, generation: int, timeout: float) -> bool:
        """Block until the *generation*-th process is listening; ``False`` on timeout.

        The first launch is generation 1 and each restart adds one.
        """
        with self._spawned:
            return self._spawned.wait_for(lambda: self._generation >= generation, timeout)

    def start(self) -> int:
        """Launch the proxy and return its port; ``LaunchRefused`` when it does not come up."""
        self._port = self._spawn(0)
        threading.Thread(target=self._watch, name="auth-proxy-watch", daemon=True).start()
        return self._port

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=_STOP_GRACE_SECS)
        except (OSError, subprocess.TimeoutExpired):
            platform_compat.terminate_and_reap_sync(proc, grace=_STOP_GRACE_SECS)

    def _spawn(self, port: int) -> int:
        proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=platform_compat.IS_POSIX,
            creationflags=platform_compat.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        threading.Thread(
            target=_relay_stderr, args=(proc.stderr,), name="auth-proxy-log", daemon=True
        ).start()
        try:
            proc.stdin.write(self._launch.line(port))
            proc.stdin.flush()
            answer = self._read_ready(proc.stdout)
        except (OSError, LaunchRefused) as exc:
            platform_compat.terminate_and_reap_sync(proc, grace=_STOP_GRACE_SECS)
            raise LaunchRefused(f"the proxy process did not start ({exc})") from exc
        if "port" not in answer:
            platform_compat.terminate_and_reap_sync(proc, grace=_STOP_GRACE_SECS)
            raise LaunchRefused(str(answer.get("error") or "the proxy process gave no port"))
        with self._lock:
            self._proc = proc
        with self._spawned:
            self._generation += 1
            self._spawned.notify_all()
        return int(answer["port"])

    def _read_ready(self, stdout: IO[bytes]) -> dict[str, Any]:
        answers: queue.Queue[bytes] = queue.Queue(maxsize=1)
        threading.Thread(
            target=lambda: answers.put(stdout.readline()), name="auth-proxy-ready", daemon=True
        ).start()
        try:
            line = answers.get(timeout=self._ready_timeout)
        except queue.Empty as exc:
            raise LaunchRefused(f"no answer within {self._ready_timeout:.0f}s") from exc
        if not line:
            raise LaunchRefused("it exited before listening")
        try:
            answer = json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise LaunchRefused("it answered something other than JSON") from exc
        return answer if isinstance(answer, dict) else {}

    def _watch(self) -> None:
        """Restart an exited proxy on the same port and token, with growing waits."""
        attempt = 0
        while not self._stopping.is_set():
            with self._lock:
                proc = self._proc
            started = time.monotonic()
            code = proc.wait() if proc is not None else None
            if self._stopping.is_set():
                return
            if proc is not None and time.monotonic() - started >= _STABLE_RUN_SECS:
                attempt = 0
            delay = RESTART_BACKOFF_SECS[min(attempt, len(RESTART_BACKOFF_SECS) - 1)]
            attempt += 1
            if proc is not None:
                logger.warning("auth-proxy: exited with code %s; restarting in %.0fs", code, delay)
            if self._stopping.wait(delay):
                return
            try:
                self._spawn(self._port)
            except LaunchRefused as exc:
                logger.warning("auth-proxy: restart failed: %s", exc)
                with self._lock:
                    self._proc = None


_SUPERVISOR: ProxySupervisor | None = None
#: Why the proxy did not start at this gateway's boot, for the dashboard; "" otherwise.
_LAST_REFUSAL = ""
#: What the running proxy was started with (no secret), for the dashboard.
_RUNNING: dict[str, Any] | None = None


def running_status() -> dict[str, Any]:
    """The proxy as this gateway started it: running details, or why it is not running.

    ``running`` is ``None`` when no proxy serves (off, or refused at boot); then
    ``not_started`` carries the refusal reason, empty when the proxy is simply off.
    Never carries the password or the client token.
    """
    alive = _SUPERVISOR is not None and _SUPERVISOR.alive
    return {"running": dict(_RUNNING) if alive and _RUNNING else None, "not_started": _LAST_REFUSAL}


def _vault_reader() -> SecretReader:
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets import SecretVault

    vault = SecretVault(config_dir())

    def read(name: str) -> str | None:
        try:
            value = vault.get(name)
        except Exception:
            logger.warning("auth-proxy: the vault could not be read", exc_info=False)
            return None
        return value.reveal() if value is not None else None

    return read


def start_for_gateway(cfg: Any, env: MutableMapping[str, str] | None = None) -> bool:
    """Start the proxy when ``network.proxy_enabled`` is on; ``True`` once it serves.

    Never raises: a proxy that cannot start leaves the environment exactly as it
    was, with one warning naming why.
    """
    global _SUPERVISOR, _LAST_REFUSAL, _RUNNING
    target = os.environ if env is None else env
    network = getattr(cfg, "network", None)
    if network is None or not network.proxy_enabled:
        return False
    if _SUPERVISOR is not None:
        return True
    try:
        launch = plan_launch(network, target, _vault_reader())
        supervisor = ProxySupervisor(launch)
        port = supervisor.start()
    except LaunchRefused as exc:
        _LAST_REFUSAL = str(exc)
        logger.warning(
            "auth-proxy: not started: %s. Outbound requests keep their current proxy settings.", exc
        )
        return False
    except Exception:
        _LAST_REFUSAL = "the proxy process failed to start; see the gateway log"
        logger.exception("auth-proxy: not started")
        return False
    export_env(target, launch, port)
    _SUPERVISOR = supervisor
    _LAST_REFUSAL = ""
    _RUNNING = {
        "port": port,
        "upstream": launch.upstream,
        "username": launch.username,
        "no_proxy": launch.no_proxy,
        "auth": launch.auth,
    }
    atexit.register(supervisor.stop)
    logger.info(
        "auth-proxy: gateway and its children now go through 127.0.0.1:%d to %s (%s)",
        port,
        launch.upstream,
        {"windows": "the Windows account", "basic": f"Basic as {launch.username}"}.get(
            launch.auth, "no credentials"
        ),
    )
    return True
