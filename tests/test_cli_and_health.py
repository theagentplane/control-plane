"""The command line (database migrations, seeding, retention), health probes and settings."""

import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from agentplane_primitives.enums import State
from agentplane_primitives.models import Trace
from agentplane_primitives.utils import new_trace_id
from fastapi.testclient import TestClient

from control_plane.cli import main
from control_plane.db import create_db_engine, head_revision
from control_plane.repositories import build_repositories
from control_plane.settings import load_settings
from control_plane.time_utils import utc_now

Make = Callable[..., TestClient]


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    monkeypatch.setenv("CONTROL_PLANE_DATABASE_URL", url)
    return url


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["control-plane", *argv])
    try:
        main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_health_and_ready(make_client: Make) -> None:
    client = make_client()
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert (ready.status_code, ready.json()["revision"]) == (200, head_revision())


def test_db_upgrade_current_and_seed(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "db", "upgrade") == 0
    assert _run(monkeypatch, "db", "current") == 0
    assert head_revision() in capsys.readouterr().out

    assert _run(monkeypatch, "db", "seed", "--tenant", "acme") == 0
    engine = create_db_engine(database_url)
    try:
        repos = build_repositories(engine)
        assert repos.governance.list_policies("acme") != []
        assert repos.governance.list_policies("other") == []
        before = len(repos.governance.list_policies("acme"))
        assert _run(monkeypatch, "db", "seed", "--tenant", "acme") == 0  # idempotent
        assert len(repos.governance.list_policies("acme")) == before
    finally:
        engine.dispose()


def test_db_seed_from_a_file(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "gov.yaml"
    source.write_text(
        "governance:\n  budgets:\n    - id: mine\n      limit_micros: 5\n"
        "  policies:\n    step_cap:\n      max_steps: 3\n",
        encoding="utf-8",
    )
    assert _run(monkeypatch, "db", "upgrade") == 0
    assert _run(monkeypatch, "db", "seed", "--file", str(source)) == 0
    engine = create_db_engine(database_url)
    try:
        repos = build_repositories(engine)
        assert [b.id for b in repos.governance.list_budgets("local")] == ["mine"]
    finally:
        engine.dispose()


def test_db_current_reports_an_unmigrated_database(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "db", "current") == 1
    assert "not migrated" in capsys.readouterr().out


def test_prune_removes_old_traces_and_what_hangs_off_them(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(monkeypatch, "db", "upgrade") == 0
    engine = create_db_engine(database_url)
    repos = build_repositories(engine)
    old, fresh = new_trace_id(), new_trace_id()
    repos.traces.register(
        "local",
        Trace(
            trace_id=old,
            state=State.CLOSED,
            started_at=utc_now() - timedelta(days=40),
            ended_at=utc_now() - timedelta(days=40),
            metadata={"session_id": "gone"},
        ),
    )
    repos.traces.register("local", Trace(trace_id=fresh, state=State.OPEN, started_at=utc_now()))
    repos.ledger.mark_halted("local", old, "stale")
    engine.dispose()

    assert _run(monkeypatch, "db", "prune") == 2  # no age given
    assert _run(monkeypatch, "db", "prune", "--older-than-days", "30") == 0

    engine = create_db_engine(database_url)
    try:
        repos = build_repositories(engine)
        assert repos.traces.get("local", old) is None
        assert repos.traces.get("local", fresh) is not None
        assert repos.ledger.halt_status("local", old) == (False, None)
    finally:
        engine.dispose()


def test_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_AUTO_MIGRATE", "false")
    monkeypatch.setenv("CONTROL_PLANE_MAX_BATCH", "7")
    monkeypatch.setenv("CONTROL_PLANE_RETENTION_DAYS", "90")
    settings = load_settings()
    assert (settings.auto_migrate, settings.max_batch, settings.retention_days) == (False, 7, 90)
    assert settings.database_url == "sqlite:///control_plane.db"
    monkeypatch.setenv("CONTROL_PLANE_AUTO_MIGRATE", "maybe")
    with pytest.raises(ValueError, match="boolean"):
        load_settings()


def test_the_web_page_is_served_and_speaks_only_the_current_api(make_client: Make) -> None:
    client = make_client()
    page = client.get("/")
    assert page.status_code == 200
    assert "AgentPlane" in page.text
    script = client.get("/static/plane.js").text
    for removed in ("/v1/run-records", "/v1/runs", "boundary_kind", "node_id", ".dims", "run_id"):
        assert removed not in script, f"plane.js still uses {removed}"
    for used in ("/v1/ledger/traces", "/v1/traces", "envelope_status", "/v1/admin/keys"):
        assert used in script
