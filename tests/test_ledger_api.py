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


def _spent_add(delta: int, *, trace: str = TRACE) -> dict[str, Any]:
    return {
        "kind": "spent_add",
        "trace_id": trace,
        "delta_micros": delta,
        "targets": [
            {"budget_id": "__trace_total__", "segment_key": f"trace:{trace}", "period": "lifetime"},
            {"budget_id": "run_llm_cap", "segment_key": f"trace:{trace}", "period": "lifetime"},
        ],
    }


def _conc(kind: str, *, trace: str = TRACE) -> dict[str, Any]:
    return {
        "kind": kind,
        "trace_id": trace,
        "segment_key": f"trace:{trace}",
    }


def _post(client: TestClient, *events: dict[str, Any]) -> Any:
    return client.post("/v1/ledger/events:batch", json={"events": list(events)})


def _precheck(client: TestClient, **body: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"trace_id": TRACE, **body}
    result: dict[str, Any] = client.post("/v1/ledger/precheck", json=payload).json()
    return result


def test_spent_add_fans_out_and_returns_totals(make_client: Make) -> None:
    client = make_client()
    response = _post(client, _spent_add(10_500))
    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] == 1
    assert body["totals"] == {
        f"__trace_total__|trace:{TRACE}|lifetime": 10_500,
        f"run_llm_cap|trace:{TRACE}|lifetime": 10_500,
    }
    assert body["halted"] is False


def test_batch_applies_in_order_and_accumulates(make_client: Make) -> None:
    client = make_client()
    body = _post(client, _spent_add(10_500), _spent_add(4_800)).json()
    assert body["totals"][f"run_llm_cap|trace:{TRACE}|lifetime"] == 15_300


def test_a_replayed_event_is_applied_again(make_client: Make) -> None:
    """Events are not deduplicated (ADR 0007): a retried batch counts twice."""
    client = make_client()
    _post(client, _spent_add(10_500))
    body = _post(client, _spent_add(10_500)).json()
    assert body["accepted"] == 1
    assert "deduped" not in body
    assert body["totals"][f"run_llm_cap|trace:{TRACE}|lifetime"] == 21_000


def test_admit_and_complete_count_inflight(make_client: Make) -> None:
    client = make_client()
    keys = [f"trace:{TRACE}"]
    _post(client, _conc("admit"), _conc("admit"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 2
    _post(client, _conc("complete"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 1
    _post(client, _conc("complete"), _conc("complete"))
    assert _precheck(client, segment_keys=keys, want=["inflight"])["inflight"][keys[0]] == 0


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
        _spent_add(10_500),
        {"kind": "bogus", "idempotency_key": "x", "trace_id": TRACE},
    )
    assert response.status_code == 400
    spent = _precheck(
        client,
        want=["spent"],
        budgets=[{"budget_id": "run_llm_cap", "segment_key": f"trace:{TRACE}"}],
    )["spent"]
    assert spent == {f"run_llm_cap|trace:{TRACE}|lifetime": 0}


def test_an_event_without_a_kind_is_400(make_client: Make) -> None:
    client = make_client()
    assert _post(client, {"trace_id": TRACE, "delta_micros": 1}).status_code == 400


def test_batch_over_the_maximum_is_400(make_client: Make) -> None:
    client = make_client(max_batch=2)
    assert _post(client, *[_spent_add(1) for i in range(3)]).status_code == 400


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
    _post(client, _spent_add(12_000, trace=trace_id))
    client.post(f"/v1/ledger/traces/{trace_id}/halt", json={"reason": "budget"})

    (row,) = client.get("/v1/ledger/traces").json()["traces"]
    assert row["trace_id"] == trace_id
    assert row["metadata"] == {"user_id": "u1"}
    assert (row["halted"], row["halt_reason"]) == (True, "budget")
    assert row["spent"] == {"__trace_total__": 12_000, "run_llm_cap": 12_000}
    assert (row["spent_micros"], row["span_count"]) == (12_000, 1)

    halted = client.get("/v1/ledger/traces", params={"halted_only": True}).json()["traces"]
    assert [t["trace_id"] for t in halted] == [trace_id]

    # The halt is an attribute of the trace itself (ADR 0006), not a separate table.
    stored = client.get(f"/v1/traces/{trace_id}").json()
    assert stored["attributes"] == {"tokenops.halted": True, "tokenops.halt_reason": "budget"}

    client.delete(f"/v1/ledger/traces/{trace_id}/halt")
    assert client.get(f"/v1/traces/{trace_id}").json()["attributes"] == {}
    assert client.get("/v1/ledger/traces", params={"halted_only": True}).json()["traces"] == []
