"""Schema versioning / forward migrations (SqliteStore._apply_migrations)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from control_plane.store import SCHEMA_VERSION, SqliteStore


def _tables(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_db_is_at_current_version():
    with tempfile.TemporaryDirectory() as td:
        s = SqliteStore(str(Path(td) / "t.db"), auto_seed=False)
        try:
            assert s._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert {"ledger_events", "run_state"} <= _tables(s._db)
        finally:
            s.close()


def test_reopen_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "t.db")
        SqliteStore(p, auto_seed=False).close()
        s = SqliteStore(p, auto_seed=False)
        try:
            assert s._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        finally:
            s.close()


def test_legacy_0_1_db_upgrades_additively():
    """A pre-versioning DB (user_version 0) missing the new tables gets them and is
    bumped to current — without touching run_registrations / runs data."""
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "legacy.db")
        raw = sqlite3.connect(p)
        raw.executescript(
            """
            CREATE TABLE run_registrations (
              run_id TEXT PRIMARY KEY, intent TEXT, user_dims TEXT, registered_at REAL
            );
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, agent TEXT, status TEXT);
            CREATE TABLE policy_instances (
              id TEXT PRIMARY KEY, template TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
              agent TEXT, budget_id TEXT, segment_id TEXT, enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO run_registrations(run_id, intent, user_dims, registered_at)
              VALUES ('r1', 'demo', '{}', 1.0);
            INSERT INTO runs(run_id, agent, status) VALUES ('r1', 'demo', 'running');
            INSERT INTO policy_instances(id, template) VALUES ('pi1', 'step_cap');
            """
        )
        raw.commit()
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
        raw.close()

        s = SqliteStore(p, auto_seed=False)
        try:
            assert s._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert {"ledger_events", "run_state"} <= _tables(s._db)
            assert "run_registrations" in _tables(s._db)  # NOT dropped in v2
            assert s._db.execute("SELECT COUNT(*) FROM run_registrations").fetchone()[0] == 1
            assert s._db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
            # legacy + v2 column adds applied
            reg_cols = {r[1] for r in s._db.execute("PRAGMA table_info(run_registrations)")}
            assert "mode" in reg_cols
            pol_cols = {r[1] for r in s._db.execute("PRAGMA table_info(policy_instances)")}
            assert "data_scope" in pol_cols
            # pre-existing rows get the default via the ALTER
            assert s.get_policy_instance("pi1").data_scope == "local"
        finally:
            s.close()
