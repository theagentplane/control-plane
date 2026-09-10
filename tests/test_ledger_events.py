"""POST /v1/ledger/events:batch — contract §5 / §6."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.app import create_app
from control_plane.envelope_store import EnvelopeStore
from control_plane.settings import Settings
from control_plane.store import SqliteStore


def _client(tmp: Path, **settings_kw) -> TestClient:
    db = str(tmp / "t.db")
    app = create_app(
        store=SqliteStore(db, auto_seed=False),
        envelopes=EnvelopeStore(db),
        settings=Settings(db_path=db, **settings_kw),
    )
    return TestClient(app)


def _spent_add(key: str, delta: int, *, run_id="run_1"):
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


def test_spent_add_fans_out_and_returns_totals():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post("/v1/ledger/events:batch", json={"events": [_spent_add("k1", 10_500)]})
        assert r.status_code == 201
        body = r.json()
        assert body["accepted"] == 1 and body["deduped"] == 0
        assert body["totals"] == {
            "__run_total__|run:run_1|lifetime": 10_500,
            "run_llm_cap|run:run_1|lifetime": 10_500,
        }
        assert body["halted"] is False


def test_batch_applies_in_order_and_accumulates():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [_spent_add("a", 10_500), _spent_add("b", 4_800)]},
        )
        assert r.json()["totals"]["run_llm_cap|run:run_1|lifetime"] == 15_300


def test_idempotent_replay_is_deduped_not_reapplied():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        c.post("/v1/ledger/events:batch", json={"events": [_spent_add("dup", 10_500)]})
        r = c.post("/v1/ledger/events:batch", json={"events": [_spent_add("dup", 10_500)]})
        body = r.json()
        assert body["accepted"] == 0 and body["deduped"] == 1
        # still reports the current total, unchanged
        assert body["totals"]["run_llm_cap|run:run_1|lifetime"] == 10_500


def test_admit_complete_counter():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        ev = lambda k, kind: {  # noqa: E731
            "kind": kind, "idempotency_key": k, "run_id": "run_1", "segment_key": "run:run_1",
        }
        c.post("/v1/ledger/events:batch", json={"events": [ev("i1", "admit"), ev("i2", "admit")]})
        assert c.get("/v1/ledger/inflight", params={"segment_key": "run:run_1"}).json()["count"] == 2
        c.post("/v1/ledger/events:batch", json={"events": [ev("i3", "complete")]})
        assert c.get("/v1/ledger/inflight", params={"segment_key": "run:run_1"}).json()["count"] == 1


def test_step_accumulates_run_state():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        step = lambda seq, cum: {  # noqa: E731
            "kind": "step", "idempotency_key": f"run_1:researcher:{seq}:step",
            "run_id": "run_1", "agent": "researcher", "seq": seq, "node_type": "llm",
            "boundary_id": "researcher.chat", "cost_micros": 10_500, "cum_spent_micros": cum,
            "ts": float(seq),
        }
        c.post("/v1/ledger/events:batch", json={"events": [step(1, 10_500), step(2, 21_000)]})
        # get_run_state is store-level; assert via a second batch's echo of totals + a direct read
        from control_plane.store import SqliteStore  # noqa: F401
        # round-trip through the store the app owns
        st = c.app.state.store.get_run_state("local", "run_1")
        assert st["step_count"] == 2
        assert len(st["recent"]) == 2
        assert st["velocity_micros_per_step"] == 10_500.0


def test_halt_mark_shows_in_halt_and_batch_ack():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [{
                "kind": "halt_mark", "idempotency_key": "h1", "run_id": "run_1",
                "reason": "step_cap: 20", "detector": "step_cap",
            }]},
        )
        assert r.json()["halted"] is True
        assert c.get("/v1/ledger/halt/run_1").json()["halted"] is True


def test_unknown_kind_is_400_and_batch_rolls_back():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [_spent_add("ok", 10_500), {"kind": "bogus", "idempotency_key": "x", "run_id": "run_1"}]},
        )
        assert r.status_code == 400
        # first event must NOT have landed (whole batch rolled back)
        assert c.get(
            "/v1/ledger/spent",
            params={"budget_id": "run_llm_cap", "segment_key": "run:run_1", "period": "lifetime"},
        ).json()["spent_micros"] == 0


def test_missing_idempotency_key_is_400():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [{"kind": "spent_add", "run_id": "run_1", "delta_micros": 1, "targets": []}]},
        )
        assert r.status_code == 400


def test_batch_over_max_is_400():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td), max_batch=2)
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [_spent_add(f"k{i}", 1) for i in range(3)]},
        )
        assert r.status_code == 400


def test_bad_durability_header_is_400():
    with tempfile.TemporaryDirectory() as td:
        c = _client(Path(td))
        r = c.post(
            "/v1/ledger/events:batch",
            json={"events": [_spent_add("k", 1)]},
            headers={"Durability": "whenever"},
        )
        assert r.status_code == 400
