from __future__ import annotations


def test_batch_ingest_and_query_by_trace(make_client):
    client = make_client()
    body = {
        "envelopes": [
            {
                "envelope_id": "e1",
                "trace_id": "tr1",
                "node_id": "llm",
                "dims": {"session_id": "s1", "message_id": "m1"},
                "timestamp": "2026-01-01T00:00:00Z",
            }
        ]
    }
    r = client.post("/v1/envelopes:batch", json=body)
    assert r.status_code == 201
    assert r.json()["accepted"] == 1

    r2 = client.post("/v1/envelopes:batch", json=body)
    assert r2.status_code == 201
    assert r2.json()["deduped"] == 1

    found = client.get("/v1/traces", params={"session_id": "s1", "message_id": "m1"})
    assert found.status_code == 200
    assert found.json()["traces"][0]["trace_id"] == "tr1"

    envs = client.get("/v1/traces/tr1/envelopes")
    assert len(envs.json()["envelopes"]) == 1

    assert (
        client.post("/v1/envelopes", json={"envelope_id": "x", "trace_id": "y"}).status_code == 404
    )


def test_traces_listed_recent_first(make_client):
    client = make_client()
    older = {
        "envelopes": [
            {
                "envelope_id": "old",
                "trace_id": "trace-old",
                "node_id": "llm",
                "timestamp": "2026-01-01T00:00:00Z",
            }
        ]
    }
    newer = {
        "envelopes": [
            {
                "envelope_id": "new",
                "trace_id": "trace-new",
                "node_id": "llm",
                "timestamp": "2026-08-13T12:00:00Z",
            }
        ]
    }
    assert client.post("/v1/envelopes:batch", json=older).status_code == 201
    assert client.post("/v1/envelopes:batch", json=newer).status_code == 201
    traces = client.get("/v1/traces").json()["traces"]
    assert [t["trace_id"] for t in traces] == ["trace-new", "trace-old"]


def test_batch_and_auth(make_client):
    client = make_client(api_keys="agent:t1:ingest,reader:t1:read")
    assert (
        client.post(
            "/v1/envelopes:batch", json={"envelopes": [{"envelope_id": "e", "trace_id": "t"}]}
        ).status_code
        == 401
    )

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

    assert client.get("/v1/traces/tr", headers={"Authorization": "Bearer agent"}).status_code == 403
    ok = client.get("/v1/traces/tr", headers={"Authorization": "Bearer reader"})
    assert ok.status_code == 200
    assert ok.json()["span_count"] == 2


def test_ui_and_register_run(make_client):
    client = make_client()
    home = client.get("/")
    assert home.status_code == 200
    assert b"AgentPlane" in home.content
    assert b"Chronicle" in home.content
    assert b"TokenOps" in home.content
    r = client.post("/v1/runs", json={"intent": "demo", "user_dims": {"user_id": "u"}})
    assert r.status_code == 201
    assert r.json()["status"] == "registered"
    assert r.json()["mode"] == "enforce"
    listed = client.get("/v1/run-records")
    assert any(x["run_id"] == r.json()["run_id"] for x in listed.json())


def test_governance_segments_roundtrip(make_client):
    client = make_client()
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
