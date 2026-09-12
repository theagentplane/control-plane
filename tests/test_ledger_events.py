"""POST /v1/ledger/events:batch — contract §5 / §6."""

from __future__ import annotations


def _spent_add(key: str, delta: int, *, run_id: str = "run_1") -> dict:
    return {
        "kind": "spent_add",
        "idempotency_key": key,
        "run_id": run_id,
        "delta_micros": delta,
        "targets": [
            {"budget_id": "__run_total__", "segment_key": f"run:{run_id}", "period": "lifetime"},
            {"budget_id": "run_llm_cap", "segment_key": f"run:{run_id}", "period": "lifetime"},
        ],
    }


def _conc(key: str, kind: str, *, run_id: str = "run_1") -> dict:
    return {"kind": kind, "idempotency_key": key, "run_id": run_id, "segment_key": f"run:{run_id}"}


def _step(seq: int, cum: int, *, run_id: str = "run_1") -> dict:
    return {
        "kind": "step",
        "idempotency_key": f"{run_id}:researcher:{seq}:step",
        "run_id": run_id,
        "agent": "researcher",
        "seq": seq,
        "node_type": "llm",
        "boundary_id": "researcher.chat",
        "cost_micros": 10_500,
        "cum_spent_micros": cum,
        "ts": float(seq),
    }


def test_spent_add_fans_out_and_returns_totals(make_client):
    c = make_client()
    r = c.post("/v1/ledger/events:batch", json={"events": [_spent_add("k1", 10_500)]})
    assert r.status_code == 201
    body = r.json()
    assert body["accepted"] == 1 and body["deduped"] == 0
    assert body["totals"] == {
        "__run_total__|run:run_1|lifetime": 10_500,
        "run_llm_cap|run:run_1|lifetime": 10_500,
    }
    assert body["halted"] is False


def test_batch_applies_in_order_and_accumulates(make_client):
    c = make_client()
    r = c.post(
        "/v1/ledger/events:batch",
        json={"events": [_spent_add("a", 10_500), _spent_add("b", 4_800)]},
    )
    assert r.json()["totals"]["run_llm_cap|run:run_1|lifetime"] == 15_300


def test_idempotent_replay_is_deduped_not_reapplied(make_client):
    c = make_client()
    c.post("/v1/ledger/events:batch", json={"events": [_spent_add("dup", 10_500)]})
    r = c.post("/v1/ledger/events:batch", json={"events": [_spent_add("dup", 10_500)]})
    body = r.json()
    assert body["accepted"] == 0 and body["deduped"] == 1
    assert body["totals"]["run_llm_cap|run:run_1|lifetime"] == 10_500


def test_admit_complete_counter(make_client):
    c = make_client()
    c.post(
        "/v1/ledger/events:batch",
        json={"events": [_conc("i1", "admit"), _conc("i2", "admit")]},
    )
    assert c.get("/v1/ledger/inflight", params={"segment_key": "run:run_1"}).json()["count"] == 2
    c.post("/v1/ledger/events:batch", json={"events": [_conc("i3", "complete")]})
    assert c.get("/v1/ledger/inflight", params={"segment_key": "run:run_1"}).json()["count"] == 1


def test_step_accumulates_run_state(make_client):
    c = make_client()
    c.post("/v1/ledger/events:batch", json={"events": [_step(1, 10_500), _step(2, 21_000)]})
    st = c.app.state.store.get_run_state("local", "run_1")
    assert st["step_count"] == 2
    assert len(st["recent"]) == 2
    assert st["velocity_micros_per_step"] == 10_500.0


def test_halt_mark_shows_in_halt_and_batch_ack(make_client):
    c = make_client()
    r = c.post(
        "/v1/ledger/events:batch",
        json={
            "events": [
                {
                    "kind": "halt_mark",
                    "idempotency_key": "h1",
                    "run_id": "run_1",
                    "reason": "step_cap: 20",
                    "detector": "step_cap",
                }
            ]
        },
    )
    assert r.json()["halted"] is True
    assert c.get("/v1/ledger/halt/run_1").json()["halted"] is True


def test_unknown_kind_is_400_and_batch_rolls_back(make_client):
    c = make_client()
    r = c.post(
        "/v1/ledger/events:batch",
        json={
            "events": [
                _spent_add("ok", 10_500),
                {"kind": "bogus", "idempotency_key": "x", "run_id": "run_1"},
            ]
        },
    )
    assert r.status_code == 400
    assert (
        c.get(
            "/v1/ledger/spent",
            params={"budget_id": "run_llm_cap", "segment_key": "run:run_1", "period": "lifetime"},
        ).json()["spent_micros"]
        == 0
    )


def test_missing_idempotency_key_is_400(make_client):
    c = make_client()
    r = c.post(
        "/v1/ledger/events:batch",
        json={
            "events": [{"kind": "spent_add", "run_id": "run_1", "delta_micros": 1, "targets": []}]
        },
    )
    assert r.status_code == 400


def test_batch_over_max_is_400(make_client):
    c = make_client(max_batch=2)
    r = c.post(
        "/v1/ledger/events:batch",
        json={"events": [_spent_add(f"k{i}", 1) for i in range(3)]},
    )
    assert r.status_code == 400


def test_bad_durability_header_is_400(make_client):
    c = make_client()
    r = c.post(
        "/v1/ledger/events:batch",
        json={"events": [_spent_add("k", 1)]},
        headers={"Durability": "whenever"},
    )
    assert r.status_code == 400
