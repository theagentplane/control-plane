from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.envelope_store import EnvelopeStore
from control_plane.settings import Settings
from control_plane.store import SqliteStore


def _client(tmp: Path, api_keys: str = "") -> TestClient:
    db = str(tmp / "t.db")
    settings = Settings(db_path=db, api_keys=api_keys)
    app = create_app(
        store=SqliteStore(db, auto_seed=False),
        envelopes=EnvelopeStore(db),
        settings=settings,
    )
    return TestClient(app)


def test_ingest_and_query_by_dims():
    with tempfile.TemporaryDirectory() as td:
        client = _client(Path(td))
        body = {
            "envelope_id": "e1",
            "trace_id": "tr1",
            "name": "llm",
            "dims": {"session_id": "s1", "message_id": "m1"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        r = client.post("/v1/envelopes", json=body)
        assert r.status_code == 201
        assert r.json()["deduped"] is False

        r2 = client.post("/v1/envelopes", json=body)
        assert r2.status_code == 200
        assert r2.json()["deduped"] is True

        found = client.get("/v1/traces", params={"session_id": "s1", "message_id": "m1"})
        assert found.status_code == 200
        assert found.json()["traces"][0]["trace_id"] == "tr1"

        envs = client.get("/v1/traces/tr1/envelopes")
        assert len(envs.json()["envelopes"]) == 1


def test_batch_and_auth():
    keys = "agent:t1:ingest,reader:t1:read"
    with tempfile.TemporaryDirectory() as td:
        client = _client(Path(td), api_keys=keys)
        assert client.post("/v1/envelopes", json={"envelope_id": "e", "trace_id": "t"}).status_code == 401

        r = client.post(
            "/v1/envelopes:batch",
            headers={"Authorization": "Bearer agent"},
            json={
                "envelopes": [
                    {"envelope_id": "a", "trace_id": "tr", "dims": {"message_id": "m"}},
                    {"envelope_id": "b", "trace_id": "tr", "dims": {"message_id": "m"}},
                ]
            },
        )
        assert r.status_code == 201
        assert r.json()["accepted"] == 2

        # ingest key cannot read
        assert client.get("/v1/traces/tr", headers={"Authorization": "Bearer agent"}).status_code == 403
        ok = client.get("/v1/traces/tr", headers={"Authorization": "Bearer reader"})
        assert ok.status_code == 200
        assert ok.json()["span_count"] == 2


def test_governance_segments_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        client = _client(Path(td))
        payload = {
            "id": "seg1",
            "name": "default",
            "dimension": "run",
            "tag_key": None,
            "match_value": None,
        }
        r = client.put("/v1/segments", json=payload)
        assert r.status_code == 200
        listed = client.get("/v1/segments")
        assert any(s["id"] == "seg1" for s in listed.json())
