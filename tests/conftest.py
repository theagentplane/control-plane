"""Shared fixtures: an application over a fresh temporary database, migrated by Alembic.

``make_client`` builds a ``TestClient`` over a new SQLite file (the database URL is the only
thing that says SQLite) and disposes the engine on teardown: open SQLite handles block deleting
the file on Windows.
"""

import gc
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.settings import Settings


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    created: list[tuple[TestClient, str]] = []

    def _make(*, seed: bool = False, **settings: Any) -> TestClient:
        directory = tempfile.mkdtemp()
        url = f"sqlite:///{(Path(directory) / 'test.db').as_posix()}"
        cfg = Settings(database_url=url, seed_on_start=seed, **settings)
        client = TestClient(create_app(cfg))
        created.append((client, directory))
        return client

    yield _make

    for client, directory in created:
        client.close()
        client.app.state.cp.engine.dispose()  # type: ignore[attr-defined]
        gc.collect()
        shutil.rmtree(directory, ignore_errors=True)
