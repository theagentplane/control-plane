from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.envelope_store import EnvelopeStore
from control_plane.settings import Settings
from control_plane.store import SqliteStore
import tempfile
from pathlib import Path


def test_health():
    with tempfile.TemporaryDirectory() as td:
        db = str(Path(td) / "t.db")
        app = create_app(
            store=SqliteStore(db, auto_seed=False),
            envelopes=EnvelopeStore(db),
            settings=Settings(db_path=db),
        )
        c = TestClient(app)
        assert c.get("/health").json()["status"] == "ok"
