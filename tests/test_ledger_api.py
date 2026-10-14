"""The shared ledger: batched events, precheck, halts and the per-trace view.

A run is a Chronicle trace, so events and halts are keyed by ``trace_id``.
"""

from collections.abc import Callable
from typing import Any

from agentplane_primitives.enums import State
from agentplane_primitives.models import Span, Trace
from agentplane_primitives.utils import new_span_id, new_trace_id
from fastapi.testclient import TestClient

from control_plane.time_utils import utc_now

Make = Callable[..., TestClient]
TRACE = "a" * 32


def _spent_add(key: str, delta: int, *, trace: str = TRACE) -> dict[str, Any]:
    return {
        "kind": "spent_add",
        "idempotency_key": key,
        "trace_id": trace,
        "delta_micros": delta,
        "targets": [
            {"budget_id": "__trace_total__", "segment_key": f"trace:{trace}", "period": "lifetime"},
            {"budget_id": "run_llm_cap", "segment_key": f"trace:{trace}", "period": "lifetime"},
        ],
    }


def _conc(key: str, kind: str, *, trace: str = TRACE) -> dict[str, Any]:
    return {
        "kind": kind,
        "idempotency_key": key,
        "trace_id": trace,
        "segment_key": f"trace:{trace}",
    }


def _step(seq: int, cum: int, *, trace: str = TRACE) -> dict[str, Any]:
    return {
        "kind": "step",
        "idempotency_key": f"{trace}:researcher:{seq}:step",
        "trace_id": trace,
        "agent": "researcher",
        "seq": seq,
        "node_type": "llm",
        "boundary_id": "researcher.chat",
        "cost_micros": 10_500,
        "cum_spent_micros": cum,
        "ts": float(seq),
    }


def _post(client: TestClient, *events: dict[str, Any]) -> Any:
    return client.post("/v1/ledger/events:batch", json={"events": list(events)})


def _precheck(client: TestClient, **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"trace_id": TRACE, **body}
    result: dict[str, Any] = client.post("/v1/ledger/precheck", json=payload).json()
    return result


def test_spent_add_fans_out_and_returns_totals(make_client: Make) -> None:
    client = make_client()
    response = _post(client, _spent_add("k1", 10_500))
    assert response.status_code == 201
    body = response.json()
    assert (body["accepted"], body["deduped"]) == (1, 0)
    assert body["totals"] == {
        f"__trace_total__|trace:{TRACE}|lifetime": 10_500,
        f"run_llm_cap|trace:{TRACE}|lifetime": 10_500,
    }
    assert body["halted"] is False


def test_batch_applies_in_order_and_accumulates(make_client: Make) -> None:
    client = make_client()
    body = _post(client, _spent_add("a", 10_500), _spent_add("b", 4_800)).json()
    assert body["totals"][f"run_llm_cap|trace:{TRACE}|lifetime"] == 15_300


def test_a_replayed_event_is_deduped_not_reapplied(make_client: Make) -> None:
    client = make_client()
    _post(client, _spent_add("dup", 10_500))
    body = _post(client, _spent_add("dup", 10_500)).json()
    assert (body["accepted"], body["deduped"]) == (0, 1)
    assert body["totals"][f"run_llm_cap|trace:{TRACE}|lifetime"] == 10_500


def test_idempotency_keys_are_per_tenant(make_client: Make) -> None:
    client = make_client(api_keys="a:t1:ingest+read,b:t2:ingest+read")
    for who in ("a", "b"):
        response = client.post(
            "/v1/ledger/events:batch",
            headers={"Authorization": f"Bearer {who}"},
            json={"events": [_spent_add("same-key", 7)]},
        )
        assert response.json()["accepted"] == 1


def test_admit_and_complete_count_inflight(make_client: Make) -> None:
    client = make_client()
    keys = [f"trace:{TRACE}"]
    _post(client, _conc("i1", "admit"), _conc("i2", "admit"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 2
    _post(client, _conc("i3", "complete"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 1
    _post(client, _conc("i4", "complete"), _conc("i5", "complete"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 0


def test_steps_build_the_trace_window(make_client: Make) -> None:
    client = make_client()
    _post(client, _step(1, 10_500), _step(2, 21_000))
    window = _precheck(client, want=["window"])["window"]
    assert window["step_count"] == 2
    assert len(window["recent"]) == 2
    assert window["velocity_micros_per_step"] == 10_500.0


def test_the_window_is_bounded(make_client: Make) -> None:
    client = make_client()
    _post(client, *[_step(i, i * 100) for i in range(1, 71)])
    window = _precheck(client, want=["window"])["window"]
    assert window["step_count"] == 70
    assert len(window["recent"]) == 64


def test_halt_mark_shows_in_the_ack_and_the_halt_route(make_client: Make) -> None:
    client = make_client()
    body = _post(
        client,
        {"kind": "halt_mark", "idempotency_key": "h1", "trace_id": TRACE, "reason": "step_cap: 20"},
    ).json()
    assert body["halted"] is True
    assert client.get(f"/v1/ledger/traces/{TRACE}/halt").json() == {
        "halted": True,
        "halt_reason": "step_cap: 20",
    }
    pre = _precheck(client, want=["halt"])
    assert (pre["halted"], pre["halt_reason"]) == (True, "step_cap: 20")


def test_halt_routes_set_and_clear(make_client: Make) -> None:
    client = make_client()
    assert client.post(f"/v1/ledger/traces/{TRACE}/halt", json={"reason": "manual"}).json() == {
        "halted": True
    }
    assert client.delete(f"/v1/ledger/traces/{TRACE}/halt").json() == {"halted": False}
    assert client.get(f"/v1/ledger/traces/{TRACE}/halt").json()["halted"] is False


def test_unknown_kind_is_400_and_the_batch_rolls_back(make_client: Make) -> None:
    client = make_client()
    response = _post(
        client,
        _spent_add("ok", 10_500),
        {"kind": "bogus", "idempotency_key": "x", "trace_id": TRACE},
    )
    assert response.status_code == 400
    spent = _precheck(
        client,
        want=["spent"],
        budgets=[{"budget_id": "run_llm_cap", "segment_key": f"trace:{TRACE}"}],
    )["spent"]
    assert spent == {f"run_llm_cap|trace:{TRACE}|lifetime": 0}


def test_missing_idempotency_key_is_400(make_client: Make) -> None:
    client = make_client()
    event = {"kind": "spent_add", "trace_id": TRACE, "delta_micros": 1, "targets": []}
    assert _post(client, event).status_code == 400


def test_batch_over_the_maximum_is_400(make_client: Make) -> None:
    client = make_client(max_batch=2)
    assert _post(client, *[_spent_add(f"k{i}", 1) for i in range(3)]).status_code == 400


def _compacted(seq: int, saved: int) -> dict[str, Any]:
    event = _step(seq, seq * 10_500)
    event["compaction"] = {
        "tokens_before": saved + 8_000,
        "tokens_after": 8_000,
        "tokens_saved": saved,
    }
    return event


def test_compaction_is_aggregated_per_trace_and_policy(make_client: Make) -> None:
    client = make_client()
    _post(client, _compacted(1, 4_000), _step(2, 21_000), _compacted(3, 1_000))
    stats = client.get(f"/v1/ledger/traces/{TRACE}").json()["policy_stats"]
    assert stats["context_compaction"] == {
        "tokens_before": 12_000 + 9_000,
        "tokens_after": 16_000,
        "tokens_saved": 5_000,
        "calls": 2,
    }


def test_a_replayed_compacted_step_does_not_double_count(make_client: Make) -> None:
    client = make_client()
    event = _compacted(1, 4_000)
    _post(client, event)
    assert _post(client, event).json()["deduped"] == 1
    stats = client.get(f"/v1/ledger/traces/{TRACE}").json()["policy_stats"]
    assert stats["context_compaction"]["tokens_saved"] == 4_000


def test_the_run_list_is_the_trace_list_with_ledger_facts(make_client: Make) -> None:
    client = make_client()
    trace_id = new_trace_id()
    client.post(
        "/v1/traces",
        json=Trace(
            trace_id=trace_id, name="job", state=State.OPEN, metadata={"user_id": "u1"}
        ).model_dump(mode="json", exclude_none=True),
    )
    span = Span(
        span_id=new_span_id(),
        trace_id=trace_id,
        service="agent",
        state=State.OPEN,
        started_at=utc_now(),
    )
    client.put(
        f"/v1/traces/{trace_id}/spans/{span.span_id}",
        json=span.model_dump(mode="json", exclude_none=True),
    )
    _post(client, _spent_add("s1", 12_000, trace=trace_id), _step(1, 12_000, trace=trace_id))
    client.post(f"/v1/ledger/traces/{trace_id}/halt", json={"reason": "budget"})

    (row,) = client.get("/v1/ledger/traces").json()["traces"]
    assert row["trace_id"] == trace_id
    assert row["metadata"] == {"user_id": "u1"}
    assert (row["halted"], row["halt_reason"]) == (True, "budget")
    assert row["spent"] == {"__trace_total__": 12_000, "run_llm_cap": 12_000}
    assert (row["spent_micros"], row["steps"]) == (12_000, 1)

    halted = client.get("/v1/ledger/traces", params={"halted_only": True}).json()["traces"]
    assert [t["trace_id"] for t in halted] == [trace_id]
