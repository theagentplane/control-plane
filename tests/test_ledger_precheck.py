"""POST /v1/ledger/precheck — contract §4."""

from __future__ import annotations


def _seed_spend(c, run_id="run_1", delta=10_500):
    c.post(
        "/v1/ledger/events:batch",
        json={"events": [{
            "kind": "spent_add", "idempotency_key": f"{run_id}:seed", "run_id": run_id,
            "delta_micros": delta,
            "targets": [
                {"budget_id": "run_llm_cap", "segment_key": f"run:{run_id}", "period": "lifetime"},
            ],
        }]},
    )


def test_precheck_returns_halt_spent_inflight(make_client):
    c = make_client()
    _seed_spend(c)
    c.post(
        "/v1/ledger/events:batch",
        json={"events": [{
            "kind": "admit", "idempotency_key": "a1", "run_id": "run_1", "segment_key": "run:run_1",
        }]},
    )
    r = c.post(
        "/v1/ledger/precheck",
        json={
            "run_id": "run_1",
            "segment_keys": ["run:run_1"],
            "budgets": [
                {"budget_id": "run_llm_cap", "segment_key": "run:run_1", "period": "lifetime"}
            ],
            "want": ["spent", "inflight", "halt"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["halted"] is False and body["halt_reason"] is None
    assert body["spent"]["run_llm_cap|run:run_1|lifetime"] == 10_500
    assert body["inflight"]["run:run_1"] == 1
    assert "server_ts" in body
    assert "window" not in body


def test_precheck_window_slice(make_client):
    c = make_client()
    c.post(
        "/v1/ledger/events:batch",
        json={"events": [{
            "kind": "step", "idempotency_key": "run_1:a:1:step", "run_id": "run_1",
            "agent": "a", "seq": 1, "node_type": "llm", "boundary_id": "a.chat",
            "cost_micros": 10_500, "cum_spent_micros": 10_500, "ts": 1.0,
        }]},
    )
    r = c.post(
        "/v1/ledger/precheck",
        json={"run_id": "run_1", "want": ["window"]},
    )
    body = r.json()
    assert body["window"]["step_count"] == 1
    assert len(body["window"]["recent"]) == 1
    assert "spent" not in body


def test_precheck_reflects_halt(make_client):
    c = make_client()
    c.post(
        "/v1/ledger/events:batch",
        json={"events": [{
            "kind": "halt_mark", "idempotency_key": "h", "run_id": "run_1",
            "reason": "step_cap", "detector": "step_cap",
        }]},
    )
    body = c.post("/v1/ledger/precheck", json={"run_id": "run_1", "want": ["halt"]}).json()
    assert body["halted"] is True
    assert body["halt_reason"] == "step_cap"


def test_precheck_unknown_run_is_zeros(make_client):
    c = make_client()
    body = c.post(
        "/v1/ledger/precheck",
        json={
            "run_id": "nope",
            "segment_keys": ["run:nope"],
            "budgets": [{"budget_id": "b", "segment_key": "run:nope", "period": "lifetime"}],
            "want": ["spent", "inflight", "halt", "window"],
        },
    ).json()
    assert body["halted"] is False
    assert body["spent"]["b|run:nope|lifetime"] == 0
    assert body["inflight"]["run:nope"] == 0
    assert body["window"] == {"step_count": 0, "recent": [], "velocity_micros_per_step": 0.0}


def test_precheck_missing_run_id_is_400(make_client):
    c = make_client()
    assert c.post("/v1/ledger/precheck", json={"want": ["halt"]}).status_code == 400
