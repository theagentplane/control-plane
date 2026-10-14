"""The database layer: Alembic owns the schema, and nothing is bound to SQLite."""

import ast
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from control_plane.db import create_db_engine, current_revision, head_revision, upgrade
from control_plane.db import migration_utils as migrations
from control_plane.db.metadata_types import metadata

SRC = Path(__file__).resolve().parents[1] / "src" / "control_plane"
DOMAIN_TABLES = {
    "api_keys",
    "budgets",
    "envelopes",
    "ledger_events",
    "ledger_halt",
    "ledger_inflight",
    "ledger_spent",
    "policy_instances",
    "segments",
    "spans",
    "trace_labels",
    "trace_policy_stats",
    "trace_state",
    "traces",
}


@pytest.fixture
def database_url() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as directory:
        yield f"sqlite:///{(Path(directory) / 'm.db').as_posix()}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    db = create_db_engine(database_url)
    yield db
    db.dispose()


def test_a_fresh_database_is_migrated_to_head(database_url: str, engine: Engine) -> None:
    assert current_revision(engine) is None
    upgrade(database_url)
    assert current_revision(engine) == head_revision()
    assert set(inspect(engine).get_table_names()) >= DOMAIN_TABLES


def test_upgrade_is_idempotent(database_url: str, engine: Engine) -> None:
    upgrade(database_url)
    upgrade(database_url)
    assert current_revision(engine) == head_revision()


def test_the_migrations_and_the_table_definitions_agree(database_url: str, engine: Engine) -> None:
    """Drift check: ``alembic upgrade head`` must produce exactly the declared tables."""
    upgrade(database_url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), metadata)
    assert diff == [], f"the tables and the migrations have drifted: {diff}"


def test_downgrade_to_base_removes_everything(database_url: str, engine: Engine) -> None:
    upgrade(database_url)
    migrations.downgrade(database_url, "base")
    assert DOMAIN_TABLES.isdisjoint(inspect(engine).get_table_names())


def test_there_is_a_single_head() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(migrations.alembic_config("sqlite://"))
    assert len(script.get_heads()) == 1


def test_sqlite_primary_keys_include_the_tenant(database_url: str, engine: Engine) -> None:
    upgrade(database_url)
    inspector = inspect(engine)
    for table in DOMAIN_TABLES - {"api_keys"}:
        pk = inspector.get_pk_constraint(table)["constrained_columns"]
        assert "tenant_id" in pk, f"{table} is not tenant-scoped"


def test_foreign_keys_and_wal_are_switched_on_for_sqlite(database_url: str, engine: Engine) -> None:
    upgrade(database_url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"


def test_nothing_outside_the_engine_factory_imports_sqlite3() -> None:
    """The service must not be bound to one database driver."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "engine_utils.py" or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "sqlite3" or n.startswith("sqlite3.") for n in names):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_no_dialect_specific_sql_in_the_repositories() -> None:
    forbidden = (
        "PRAGMA",
        "json_extract",
        "INSERT OR",
        "ON CONFLICT",
        "REPLACE INTO",
        "AUTOINCREMENT",
    )
    offenders = []
    for path in (SRC / "repositories").rglob("*.py"):
        text_ = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {word}" for word in forbidden if word in text_]
    assert offenders == []


def test_the_database_url_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from control_plane.settings import load_settings

    monkeypatch.setenv("CONTROL_PLANE_DATABASE_URL", "postgresql+psycopg://u:p@db/cp")
    assert load_settings().database_url == "postgresql+psycopg://u:p@db/cp"
    assert "CONTROL_PLANE_DB" not in os.environ
