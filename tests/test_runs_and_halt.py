"""0.2.0 additive changes to run identity, run-record PATCH, and the run-scoped
halt route — contract §7 / §8 / §11."""

from __future__ import annotations


def test_register_returns_registered_at(make_client):
    c = make_client()
    r = c.post("/v1/runs", json={"run_id": "run_1", "intent": "demo", "user_dims": {"user_id": "u"}})
    assert r.status_code == 201
    body = r.json()
    assert body["run_id"] == "run_1"
    assert body["status"] == "registered"
    assert isinstance(body["registered_at"], (int, float)) and body["registered_at"] > 0
    # and it round-trips through GET .../registration
    reg = c.get("/v1/runs/run_1/registration").json()
    assert reg["registered_at"] == body["registered_at"]


def test_patch_run_record_ignores_derived_fields(make_client):
    c = make_client()
    c.post("/v1/runs", json={"run_id": "run_1", "intent": "demo"})
    r = c.patch(
        "/v1/run-records/run_1",
        json={"status": "completed", "steps": 999, "cost_micros": 12345},
    )
    assert r.status_code == 200
    rec = c.get("/v1/run-records/run_1").json()
    assert rec["status"] == "completed"
    assert rec["steps"] == 0          # client value dropped
    assert rec["cost_micros"] == 0    # client value dropped


def test_run_scoped_halt_get_post_delete(make_client):
    c = make_client()
    c.post("/v1/runs", json={"run_id": "run_1", "intent": "demo"})

    assert c.get("/v1/ledger/runs/run_1/halt").json() == {"halted": False, "halt_reason": None}

    assert c.post("/v1/ledger/runs/run_1/halt", json={"reason": "step_cap: 20"}).json() == {"halted": True}
    got = c.get("/v1/ledger/runs/run_1/halt").json()
    assert got["halted"] is True and got["halt_reason"] == "step_cap: 20"
    # run row status flipped too
    assert c.get("/v1/run-records/run_1").json()["status"] == "halted"

    assert c.delete("/v1/ledger/runs/run_1/halt").json() == {"halted": False}
    assert c.get("/v1/ledger/runs/run_1/halt").json()["halted"] is False


def test_legacy_halt_alias_still_works(make_client):
    c = make_client()
    c.post("/v1/ledger/halt/mark", json={"run_id": "run_1", "reason": "x"})
    assert c.get("/v1/ledger/halt/run_1").json()["halted"] is True
    assert c.get("/v1/ledger/runs/run_1/halt").json()["halted"] is True


def test_health_advertises_limits(make_client):
    c = make_client(max_batch=50)
    body = c.get("/health").json()
    assert body["max_batch"] == 50
    assert body["max_body_bytes"] > 0
    assert body["version"] == "0.2.0"
