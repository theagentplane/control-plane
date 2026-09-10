"""Shared test fixtures.

``make_client`` builds a ``TestClient`` over a fresh SQLite file and — crucially on
Windows — closes the store connections on teardown before the temp dir is removed
(open SQLite handles block ``unlink`` on Windows; POSIX does not care).
"""

from __future__ import annotations

import gc
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.envelope_store import EnvelopeStore
from control_plane.settings import Settings
from control_plane.store import SqliteStore


@pytest.fixture
def make_client():
    created: list[tuple] = []

    def _make(*, auto_seed: bool = False, **settings_kw) -> TestClient:
        td = tempfile.mkdtemp()
        db = str(Path(td) / "t.db")
        store = SqliteStore(db, auto_seed=auto_seed)
        env = EnvelopeStore(db)
        app = create_app(
            store=store, envelopes=env, settings=Settings(db_path=db, **settings_kw)
        )
        client = TestClient(app)
        created.append((client, store, env, td))
        return client

    yield _make

    for client, store, env, td in created:
        try:
            client.close()
        except Exception:
            pass
        store.close()
        env.close()
        gc.collect()
        shutil.rmtree(td, ignore_errors=True)
