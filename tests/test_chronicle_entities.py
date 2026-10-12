"""Chronicle 1.0 entities: Trace, Span and Envelope from ``agentplane-primitives``.

Covers the four things Chronicle needs: register a trace, upsert a span, append envelopes and read
one span's envelopes (for replay). Payloads are built with the primitives models, exactly as
Chronicle builds them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agentplane_primitives.enums import Kind, State, Status
from agentplane_primitives.models import (
    Envelope,
    EnvelopeStatus,
    LlmInput,
    LlmOutput,
    Message,
    Span,
    ToolInput,
    ToolOutput,
    Trace,
)
from agentplane_primitives.utils import new_envelope_id, new_span_id, new_trace_id

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)


def _span(trace_id: str, span_id: str, *, closed: bool = False, parent: str | None = None) -> Span:
    if closed:
        return Span(
            span_id=span_id,
            trace_id=trace_id,
            parent_envelope_id=parent,
            service="agent",
            state=State.CLOSED,
            status=Status.SUCCESS,
            started_at=NOW,
            ended_at=NOW + timedelta(seconds=2),
        )
    return Span(
        span_id=span_id,
        trace_id=trace_id,
        parent_envelope_id=parent,
        service="agent",
        state=State.OPEN,
        started_at=NOW,
    )


def _tool_envelope(
    trace_id: str,
    span_id: str,
    name: str = "search",
    *,
    sequence: int = 1,
    closed: bool = True,
    envelope_id: str | None = None,
    result: Any = "ok",
) -> Envelope:
    status = (
        EnvelopeStatus(
            state=State.CLOSED,
            status=Status.SUCCESS,
            started_at=NOW,
            ended_at=NOW + timedelta(seconds=1),
        )
        if closed
        else EnvelopeStatus(state=State.OPEN, started_at=NOW)
    )
    return Envelope(
        envelope_id=envelope_id or new_envelope_id(),
        span_id=span_id,
        trace_id=trace_id,
        name=name,
        kind=Kind.TOOL,
        envelope_status=status,
        input=ToolInput(raw={"q": "cats"}),
        output=ToolOutput(raw=result) if closed else None,
        metadata={"chronicle.sequence": sequence, "chronicle.invocation_index": sequence},
    )


def test_trace_registration_is_idempotent_and_labels_are_written_once(make_client):
    client = make_client()
    trace_id = new_trace_id()
    first = Trace(trace_id=trace_id, name="checkout", state=State.OPEN, metadata={"session_id": "s1"})
    r = client.post("/v1/traces", json=_dump(first))
    assert r.status_code == 201
    assert r.json()["metadata"] == {"session_id": "s1"}

    again = Trace(trace_id=trace_id, state=State.OPEN, metadata={"session_id": "CHANGED"})
    r2 = client.post("/v1/traces", json=_dump(again))
    assert r2.status_code == 200  # a repeat returns the existing trace
    assert r2.json()["metadata"] == {"session_id": "s1"}  # labels never change

    found = client.get("/v1/traces", params={"session_id": "s1"}).json()["traces"]
    assert [t["trace_id"] for t in found] == [trace_id]


def test_invalid_trace_is_rejected_with_400(make_client):
    client = make_client()
    r = client.post("/v1/traces", json={"trace_id": "not-hex", "state": "open"})
    assert r.status_code == 400
    assert "invalid trace" in r.json()["detail"]


def test_span_opens_then_closes_and_a_closed_span_is_immutable(make_client):
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    path = f"/v1/traces/{trace_id}/spans/{span_id}"

    assert client.put(path, json=_dump(_span(trace_id, span_id))).json()["status"] == "stored"
    assert client.get(f"/v1/traces/{trace_id}").json()["state"] == "open"

    assert client.put(path, json=_dump(_span(trace_id, span_id, closed=True))).status_code == 200
    detail = client.get(f"/v1/traces/{trace_id}").json()
    assert detail["state"] == "closed"  # derived: no span is open
    assert detail["spans"][0]["status"] == "success"

    reopened = client.put(path, json=_dump(_span(trace_id, span_id)))
    assert reopened.json()["status"] == "deduped"
    assert client.get(f"/v1/traces/{trace_id}").json()["spans"][0]["state"] == "closed"


def test_span_path_must_match_the_body(make_client):
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    r = client.put(
        f"/v1/traces/{trace_id}/spans/{new_span_id()}", json=_dump(_span(trace_id, span_id))
    )
    assert r.status_code == 400


def test_a_span_creates_its_trace_implicitly(make_client):
    """Lazy registration: no POST /v1/traces was made."""
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    client.put(f"/v1/traces/{trace_id}/spans/{span_id}", json=_dump(_span(trace_id, span_id)))
    assert client.get(f"/v1/traces/{trace_id}").status_code == 200
    # a later explicit registration may still name it
    client.post("/v1/traces", json=_dump(Trace(trace_id=trace_id, name="late", state=State.OPEN)))
    assert client.get(f"/v1/traces/{trace_id}").json()["name"] == "late"


def test_envelope_open_then_close_is_one_record(make_client):
    client = make_client()
    trace_id, span_id, eid = new_trace_id(), new_span_id(), new_envelope_id()
    opened = _tool_envelope(trace_id, span_id, closed=False, envelope_id=eid)
    closed = _tool_envelope(trace_id, span_id, closed=True, envelope_id=eid, result="done")

    r = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(opened)]})
    assert (r.status_code, r.json()["accepted"]) == (201, 1)
    r = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(closed)]})
    assert (r.json()["accepted"], r.json()["deduped"]) == (1, 0)

    (stored,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert stored["envelope_status"]["state"] == "closed"
    assert stored["output"]["raw"] == "done"

    # closed is immutable: a late open write cannot reopen it
    r = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(opened)]})
    assert (r.json()["accepted"], r.json()["deduped"]) == (0, 1)
    (stored,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert stored["envelope_status"]["state"] == "closed"


def test_span_read_returns_only_that_span_in_call_order(make_client):
    client = make_client()
    trace_id, mine, other = new_trace_id(), new_span_id(), new_span_id()
    batch = [
        _tool_envelope(trace_id, mine, "b", sequence=2),
        _tool_envelope(trace_id, mine, "a", sequence=1),
        _tool_envelope(trace_id, other, "elsewhere", sequence=1),
    ]
    client.post("/v1/envelopes:batch", json={"envelopes": [_dump(e) for e in batch]})

    read = client.get(f"/v1/traces/{trace_id}/spans/{mine}/envelopes").json()["envelopes"]
    assert [e["name"] for e in read] == ["a", "b"]
    # every payload is the primitives shape again: Chronicle validates it with the same models
    assert [Envelope.model_validate(e).name for e in read] == ["a", "b"]


def test_llm_envelope_round_trips_losslessly(make_client):
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    envelope = Envelope(
        envelope_id=new_envelope_id(),
        span_id=span_id,
        trace_id=trace_id,
        name="llm.chat",
        kind=Kind.LLM,
        envelope_status=EnvelopeStatus(
            state=State.CLOSED, status=Status.SUCCESS, started_at=NOW, ended_at=NOW
        ),
        input=LlmInput(
            model="gpt-4o",
            messages=[Message(role="user", content="hi")],
            raw={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        ),
        output=LlmOutput(raw={"id": "chatcmpl-1", "choices": []}, provider="openai"),
    )
    client.post("/v1/envelopes:batch", json={"envelopes": [_dump(envelope)]})
    (read,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert Envelope.model_validate(read) == envelope


def test_a_malformed_primitives_envelope_is_rejected_whole_batch(make_client):
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    good = _dump(_tool_envelope(trace_id, span_id))
    bad = _dump(_tool_envelope(trace_id, span_id))
    bad["kind"] = "router"  # not a member of the closed Kind enum

    r = client.post("/v1/envelopes:batch", json={"envelopes": [good, bad]})
    assert r.status_code == 400
    assert "invalid envelope" in r.json()["detail"]
    # all-or-nothing: the good one was not stored either
    assert client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"] == []


def test_tenants_are_isolated_and_scopes_enforced(make_client):
    client = make_client(api_keys="a:t1:ingest+read,b:t2:ingest+read,ro:t1:read")
    trace_id, span_id = new_trace_id(), new_span_id()
    path = f"/v1/traces/{trace_id}/spans/{span_id}"
    head_a = {"Authorization": "Bearer a"}

    assert client.put(path, json=_dump(_span(trace_id, span_id))).status_code == 401
    assert client.put(path, headers={"Authorization": "Bearer ro"},
                      json=_dump(_span(trace_id, span_id))).status_code == 403
    assert client.put(path, headers=head_a, json=_dump(_span(trace_id, span_id))).status_code == 200
    client.post("/v1/envelopes:batch", headers=head_a,
                json={"envelopes": [_dump(_tool_envelope(trace_id, span_id))]})

    mine = client.get(f"{path}/envelopes", headers=head_a).json()["envelopes"]
    theirs = client.get(f"{path}/envelopes", headers={"Authorization": "Bearer b"})
    assert len(mine) == 1
    assert theirs.json()["envelopes"] == []


def test_legacy_envelopes_still_ingest_next_to_primitives_ones(make_client):
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    legacy = {"envelope_id": "old1", "trace_id": "legacy-trace", "name": "llm"}
    modern = _dump(_tool_envelope(trace_id, span_id))
    r = client.post("/v1/envelopes:batch", json={"envelopes": [legacy, modern]})
    assert (r.status_code, r.json()["accepted"]) == (201, 2)
    assert client.get("/v1/traces/legacy-trace/envelopes").json()["envelopes"][0]["name"] == "llm"
