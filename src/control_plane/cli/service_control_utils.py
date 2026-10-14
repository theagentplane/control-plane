"""start / stop / status for a background ``control-plane serve`` process.

Singleton per state directory: one JSON state file tracks the one instance this CLI manages
(``control-plane start`` twice is a no-op, not two servers). Two OS rough edges are called out
rather than papered over:

* **PIDs get reused.** An "is it running" check must confirm the process is actually ours, not
  just that some process holds that PID.
* **Windows has no SIGTERM.** ``stop()`` calls ``psutil.Process.terminate()``, which is a clean
  signal on POSIX (uvicorn's own graceful shutdown) but a hard stop on Windows.

``CONTROL_PLANE_STATE_DIR`` overrides the state directory (tests use it to avoid touching the
real per-user state and to isolate concurrent runs).
"""

import contextlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil
from platformdirs import user_state_dir

APP_NAME = "agentplane-control-plane"

START_TIMEOUT_S = 10.0
"""How long ``start()`` waits for ``/health`` before declaring failure."""
STOP_TIMEOUT_S = 5.0
"""How long ``stop()`` waits for a graceful exit before escalating to kill."""


class ServiceError(RuntimeError):
    """``start()`` or ``stop()`` failed in a way the caller should see (not just print)."""


@dataclass
class ServiceState:
    """What the state file records about the managed instance."""

    pid: int
    host: str
    port: int
    database_url: str | None
    log_file: str
    started_at: float

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _state_dir() -> Path:
    override = os.environ.get("CONTROL_PLANE_STATE_DIR")
    directory = Path(override) if override else Path(user_state_dir(APP_NAME))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _state_file() -> Path:
    return _state_dir() / "control-plane.json"


def _log_file() -> Path:
    return _state_dir() / "control-plane.log"


def _read_state() -> ServiceState | None:
    path = _state_file()
    if not path.exists():
        return None
    try:
        return ServiceState(**json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _write_state(state: ServiceState) -> None:
    _state_file().write_text(json.dumps(asdict(state)))


def _clear_state() -> None:
    _state_file().unlink(missing_ok=True)


def _is_ours(pid: int) -> bool:
    """A PID match alone is not enough (PIDs are reused): confirm it is ``control_plane serve``."""
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return "control_plane" in cmdline and "serve" in cmdline


def _running_state() -> ServiceState | None:
    """The tracked state iff its PID is alive and genuinely ours; otherwise clears a stale file."""
    state = _read_state()
    if state is None:
        return None
    if psutil.pid_exists(state.pid) and _is_ours(state.pid):
        return state
    _clear_state()
    return None


def _wait_healthy(base_url: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _log_tail(log_path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(log_path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


def start(
    *, host: str = "127.0.0.1", port: int = 8800, database_url: str | None = None
) -> ServiceState:
    """Start ``control-plane serve`` detached from this console.

    Idempotent: if a managed instance is already running it is returned unchanged (not
    restarted, even if the arguments differ; stop it first for different settings).
    """
    existing = _running_state()
    if existing is not None:
        print(f"already running on {existing.base_url} (pid {existing.pid})")
        return existing

    log_path = _log_file()
    cmd = [sys.executable, "-m", "control_plane", "serve", "--host", host, "--port", str(port)]
    if database_url:
        cmd += ["--database-url", database_url]

    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True  # setsid: survives the parent shell closing

    with open(log_path, "ab") as log_handle:
        proc = subprocess.Popen(cmd, stdout=log_handle, stderr=log_handle, **popen_kwargs)

    state = ServiceState(
        pid=proc.pid,
        host=host,
        port=port,
        database_url=database_url,
        log_file=str(log_path),
        started_at=time.time(),
    )
    if not _wait_healthy(state.base_url, timeout=START_TIMEOUT_S):
        if psutil.pid_exists(proc.pid):  # alive but broken (for example a bad URL): reap it
            with contextlib.suppress(psutil.NoSuchProcess):
                psutil.Process(proc.pid).kill()
        raise ServiceError(
            f"control-plane did not become healthy within {START_TIMEOUT_S:.0f}s; "
            f"log tail ({log_path}):\n{_log_tail(log_path)}"
        )
    _write_state(state)
    print(f"started on {state.base_url} (pid {state.pid}); log: {log_path}")
    return state


def stop(*, timeout: float = STOP_TIMEOUT_S) -> bool:
    """Stop the managed instance. Returns False (not an error) if nothing was running."""
    state = _running_state()
    if state is None:
        print("not running")
        return False
    try:
        proc = psutil.Process(state.pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        pass
    _clear_state()
    print(f"stopped (pid {state.pid})")
    return True


def status() -> dict[str, Any]:
    """Report on the managed instance. Never raises for "not running"."""
    state = _running_state()
    if state is None:
        print("not running")
        return {"running": False}
    healthy = False
    version = None
    try:
        response = httpx.get(f"{state.base_url}/health", timeout=1.0)
        healthy = response.status_code == 200
        if healthy:
            version = response.json().get("version")
    except httpx.HTTPError:
        pass
    label = "running" if healthy else "running (not responding on /health)"
    print(
        f"{label} - pid {state.pid}, {state.base_url}" + (f", version {version}" if version else "")
    )
    return {
        "running": True,
        "healthy": healthy,
        "pid": state.pid,
        "host": state.host,
        "port": state.port,
        "database_url": state.database_url,
        "version": version,
        "log_file": state.log_file,
        "started_at": state.started_at,
    }
