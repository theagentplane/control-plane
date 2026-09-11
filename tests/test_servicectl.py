"""Integration tests for `control-plane start|stop|status`.

These spawn a real, detached `python -m control_plane.cli serve` subprocess and talk
to it over real HTTP — the one place this repo actually exercises the OS-specific
process-lifecycle code (Windows DETACHED_PROCESS vs POSIX start_new_session,
terminate()/kill() escalation, /health polling). Slower than a unit test; that's the
point.

Every test gets its own CONTROL_PLANE_STATE_DIR (env, monkeypatched) and its own free
port, so tests never collide with each other or with a real user's running instance.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

import httpx
import psutil
import pytest

from control_plane import servicectl


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def service(tmp_path, monkeypatch):
    """Isolated state dir; guarantees cleanup even if a test fails mid-assertion."""
    monkeypatch.setenv("CONTROL_PLANE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CONTROL_PLANE_API_KEYS", "")  # anonymous/local, no auth friction
    yield {"db": str(tmp_path / "cp.db"), "port": _free_port()}
    try:
        servicectl.stop()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# status / stop with nothing running                                          #
# --------------------------------------------------------------------------- #


def test_status_when_never_started(service):
    info = servicectl.status()
    assert info == {"running": False}


def test_stop_when_never_started_is_a_noop(service):
    assert servicectl.stop() is False


def test_status_and_stop_after_stale_state_file(service, tmp_path):
    """A state file pointing at a PID that no longer exists must not be trusted."""
    state_file = tmp_path / "state" / "control-plane.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    dead_pid = _unused_pid()
    state_file.write_text(
        json.dumps(
            {
                "pid": dead_pid,
                "host": "127.0.0.1",
                "port": service["port"],
                "db": service["db"],
                "log_file": str(tmp_path / "x.log"),
                "started_at": time.time(),
            }
        )
    )
    assert servicectl.status() == {"running": False}
    assert not state_file.exists()  # cleaned up as stale


def _unused_pid() -> int:
    """A PID guaranteed not to be a live control-plane process right now."""
    candidate = 999_999
    while psutil.pid_exists(candidate):
        candidate -= 1
    return candidate


# --------------------------------------------------------------------------- #
# the real lifecycle: start -> status -> stop -> status                       #
# --------------------------------------------------------------------------- #


def test_full_lifecycle(service):
    state = servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    assert state.port == service["port"]
    assert psutil.pid_exists(state.pid)

    # the actual API is reachable over real HTTP, not just "a process exists"
    r = httpx.get(f"{state.base_url}/health", timeout=2.0)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    info = servicectl.status()
    assert info["running"] is True
    assert info["healthy"] is True
    assert info["pid"] == state.pid
    assert info["version"] == r.json()["version"]

    assert servicectl.stop() is True
    # the process is actually gone, not just untracked
    deadline = time.monotonic() + 5
    while psutil.pid_exists(state.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(state.pid)

    assert servicectl.status() == {"running": False}


def test_start_is_idempotent(service):
    """A second start() while one is already running must not spawn a new process —
    it should short-circuit and return the same pid untouched."""
    first = servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    second = servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    assert second.pid == first.pid
    assert second.started_at == first.started_at  # same ServiceState, not a fresh one


def test_stop_is_idempotent(service):
    servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    assert servicectl.stop() is True
    assert servicectl.stop() is False  # already stopped -> no-op, not an error


def test_served_endpoints_reachable_through_real_lifecycle(service):
    """Once started, the plane answers the actual TokenOps-facing API — not just
    /health. One assertion per route family is enough here; the route behaviour
    itself is covered by the app-level tests."""
    state = servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    base = state.base_url

    reg = httpx.post(f"{base}/v1/runs", json={"intent": "smoke", "user_dims": {}}, timeout=2.0)
    assert reg.status_code == 201
    run_id = reg.json()["run_id"]
    assert reg.json()["registered_at"] > 0

    resolved = httpx.get(f"{base}/v1/runs/{run_id}/registration", timeout=2.0)
    assert resolved.status_code == 200 and resolved.json()["intent"] == "smoke"

    gov = httpx.get(f"{base}/v1/governance/anyagent", timeout=2.0)
    assert gov.status_code == 200

    batch = httpx.post(
        f"{base}/v1/ledger/events:batch",
        json={
            "events": [
                {
                    "kind": "spent_add",
                    "idempotency_key": "smoke-1",
                    "run_id": run_id,
                    "delta_micros": 1000,
                    "targets": [
                        {
                            "budget_id": "__run_total__",
                            "segment_key": f"run:{run_id}",
                            "period": "lifetime",
                        }
                    ],
                }
            ]
        },
        timeout=2.0,
    )
    assert batch.status_code == 201 and batch.json()["accepted"] == 1

    pre = httpx.post(
        f"{base}/v1/ledger/precheck",
        json={
            "run_id": run_id,
            "want": ["spent", "halt"],
            "budgets": [
                {"budget_id": "__run_total__", "segment_key": f"run:{run_id}", "period": "lifetime"}
            ],
        },
        timeout=2.0,
    )
    assert pre.status_code == 200
    assert pre.json()["spent"][f"__run_total__|run:{run_id}|lifetime"] == 1000

    halt = httpx.get(f"{base}/v1/ledger/runs/{run_id}/halt", timeout=2.0)
    assert halt.status_code == 200 and halt.json()["halted"] is False


def test_log_file_captures_server_output(service):
    state = servicectl.start(host="127.0.0.1", port=service["port"], db=service["db"])
    log_path = Path(state.log_file)
    assert log_path.exists()
    # uvicorn logs its startup banner; give the flush a moment
    deadline = time.monotonic() + 3
    content = ""
    while time.monotonic() < deadline:
        content = log_path.read_text(errors="replace")
        if content:
            break
        time.sleep(0.1)
    assert content  # something was written, not silently discarded
