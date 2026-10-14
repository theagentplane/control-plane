"""Integration tests for ``control-plane start | stop | status``.

These spawn a real, detached ``python -m control_plane serve`` subprocess and talk to it over
real HTTP: the one place the repository exercises the OS-specific process-lifecycle code
(Windows DETACHED_PROCESS versus POSIX start_new_session, terminate and kill escalation,
``/health`` polling). Slower than a unit test; that is the point.

Every test gets its own ``CONTROL_PLANE_STATE_DIR`` and its own free port, so tests never collide
with each other or with a real user's running instance.
"""

import contextlib
import json
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
from agentplane_primitives.enums import State
from agentplane_primitives.models import Trace
from agentplane_primitives.utils import new_trace_id

from control_plane.cli import service_control_utils as servicectl


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Isolated state dir; guarantees cleanup even if a test fails mid-assertion."""
    monkeypatch.setenv("CONTROL_PLANE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CONTROL_PLANE_API_KEYS", "")  # open local mode, no auth friction
    yield {"db": f"sqlite:///{(tmp_path / 'cp.db').as_posix()}", "port": _free_port()}
    with contextlib.suppress(Exception):
        servicectl.stop()


def _start(service: dict[str, Any]) -> servicectl.ServiceState:
    return servicectl.start(host="127.0.0.1", port=service["port"], database_url=service["db"])


def test_status_when_never_started(service: dict[str, Any]) -> None:
    assert servicectl.status() == {"running": False}


def test_stop_when_never_started_is_a_noop(service: dict[str, Any]) -> None:
    assert servicectl.stop() is False


def _unused_pid() -> int:
    candidate = 4_000_000
    while psutil.pid_exists(candidate):
        candidate -= 1
    return candidate


def test_a_stale_state_file_is_not_trusted(service: dict[str, Any], tmp_path: Path) -> None:
    """A state file pointing at a PID that no longer exists must be cleaned up."""
    state_file = tmp_path / "state" / "control-plane.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "pid": _unused_pid(),
                "host": "127.0.0.1",
                "port": service["port"],
                "database_url": service["db"],
                "log_file": str(tmp_path / "x.log"),
                "started_at": time.time(),
            }
        )
    )
    assert servicectl.status() == {"running": False}
    assert not state_file.exists()


def test_full_lifecycle(service: dict[str, Any]) -> None:
    state = _start(service)
    assert state.port == service["port"]
    assert psutil.pid_exists(state.pid)

    health = httpx.get(f"{state.base_url}/health", timeout=2.0)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    info = servicectl.status()
    assert info["running"] is True
    assert info["healthy"] is True
    assert info["pid"] == state.pid
    assert info["version"] == health.json()["version"]

    assert servicectl.stop() is True
    deadline = time.monotonic() + 5
    while psutil.pid_exists(state.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(state.pid)  # actually gone, not just untracked
    assert servicectl.status() == {"running": False}


def test_start_is_idempotent(service: dict[str, Any]) -> None:
    first = _start(service)
    second = _start(service)
    assert second.pid == first.pid
    assert second.started_at == first.started_at


def test_stop_is_idempotent(service: dict[str, Any]) -> None:
    _start(service)
    assert servicectl.stop() is True
    assert servicectl.stop() is False


def test_the_started_service_migrated_its_database_and_serves_the_api(
    service: dict[str, Any],
) -> None:
    """Started with only a database URL, the service creates its own schema on first start."""
    base = _start(service).base_url

    assert httpx.get(f"{base}/ready", timeout=2.0).json()["status"] == "ready"

    trace = Trace(trace_id=new_trace_id(), name="smoke", state=State.OPEN)
    created = httpx.post(
        f"{base}/v1/traces", json=trace.model_dump(mode="json", exclude_none=True), timeout=2.0
    )
    assert created.status_code == 201

    config = httpx.get(f"{base}/v1/governance/any-agent", timeout=2.0)
    assert config.status_code == 200
    assert "policies" in config.json()["governance"]  # the default configuration was seeded


def test_a_bad_database_url_fails_to_start_with_the_log_tail(service: dict[str, Any]) -> None:
    with pytest.raises(servicectl.ServiceError, match="did not become healthy"):
        servicectl.start(host="127.0.0.1", port=service["port"], database_url="nosuchdialect://x/y")
    assert servicectl.status() == {"running": False}


def test_log_file_captures_server_output(service: dict[str, Any]) -> None:
    state = _start(service)
    log_path = Path(state.log_file)
    assert log_path.exists()
    deadline = time.monotonic() + 3
    content = ""
    while time.monotonic() < deadline:
        content = log_path.read_text(errors="replace")
        if content:
            break
        time.sleep(0.1)
    assert content
