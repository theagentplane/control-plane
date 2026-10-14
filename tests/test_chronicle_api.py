"""Chronicle 1.0 on the primitives schema: traces, spans and envelopes over HTTP.

Payloads are built with the primitives models, exactly as Chronicle builds them.
"""

from collections.abc import Callable
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
from fastapi.testclient import TestClient

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
Make = Callable[..., TestClient]


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump(mode="json", exclude_none=True)
    return dumped


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


def _tool(
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
            started_at=NOW + timedelta(seconds=sequence),
            ended_at=NOW + timedelta(seconds=sequence + 1),
        )
        if closed
        else EnvelopeStatus(state=State.OPEN, started_at=NOW + timedelta(seconds=sequence))
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


def test_trace_registration_is_idempotent_and_labels_are_written_once(make_client: Make) -> None:
    client = make_client()
    trace_id = new_trace_id()
    first = Trace(
        trace_id=trace_id, name="checkout", state=State.OPEN, metadata={"session_id": "s1"}
    )
    created = client.post("/v1/traces", json=_dump(first))
    assert created.status_code == 201
    assert created.json()["metadata"] == {"session_id": "s1"}

    again = Trace(trace_id=trace_id, state=State.OPEN, metadata={"session_id": "CHANGED"})
    repeat = client.post("/v1/traces", json=_dump(again))
    assert repeat.status_code == 200  # a repeat returns the existing trace
    assert repeat.json()["metadata"] == {"session_id": "s1"}  # labels never change

    found = client.get("/v1/traces", params={"session_id": "s1"}).json()["traces"]
    assert [t["trace_id"] for t in found] == [trace_id]
    assert found[0]["metadata"] == {"session_id": "s1"}
    assert "dims" not in found[0]  # the pre-1.0 name is gone


def test_labels_filter_generically_and_free_text_search(make_client: Make) -> None:
    client = make_client()
    a, b = new_trace_id(), new_trace_id()
    client.post(
        "/v1/traces",
        json=_dump(
            Trace(trace_id=a, name="refund flow", state=State.OPEN, metadata={"tier": "gold"})
        ),
    )
    client.post(
        "/v1/traces",
        json=_dump(Trace(trace_id=b, name="other", state=State.OPEN, metadata={"tier": "free"})),
    )
    by_label = client.get("/v1/traces", params=[("label", "tier:gold")]).json()["traces"]
    assert [t["trace_id"] for t in by_label] == [a]
    by_text = client.get("/v1/traces", params={"q": "refund"}).json()["traces"]
    assert [t["trace_id"] for t in by_text] == [a]
    assert client.get("/v1/traces", params=[("label", "nocolon")]).status_code == 400


def test_invalid_trace_is_rejected_with_400(make_client: Make) -> None:
    client = make_client()
    response = client.post("/v1/traces", json={"trace_id": "not-hex", "state": "open"})
    assert response.status_code == 400
    assert "invalid trace" in response.json()["detail"]


def test_span_opens_then_closes_and_a_closed_span_is_immutable(make_client: Make) -> None:
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    path = f"/v1/traces/{trace_id}/spans/{span_id}"

    assert client.put(path, json=_dump(_span(trace_id, span_id))).json()["status"] == "stored"
    assert client.get(f"/v1/traces/{trace_id}").json()["state"] == "open"

    assert client.put(path, json=_dump(_span(trace_id, span_id, closed=True))).status_code == 200
    detail = client.get(f"/v1/traces/{trace_id}").json()
    assert detail["state"] == "closed"  # derived: no span is open
    assert detail["spans"][0]["status"] == "success"
    assert detail["ended_at"] is not None

    reopened = client.put(path, json=_dump(_span(trace_id, span_id)))
    assert reopened.json()["status"] == "deduped"
    assert client.get(f"/v1/traces/{trace_id}").json()["spans"][0]["state"] == "closed"


def test_span_path_must_match_the_body(make_client: Make) -> None:
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    response = client.put(
        f"/v1/traces/{trace_id}/spans/{new_span_id()}", json=_dump(_span(trace_id, span_id))
    )
    assert response.status_code == 400


def test_a_span_creates_its_trace_implicitly_and_a_later_registration_may_name_it(
    make_client: Make,
) -> None:
    """Lazy registration: no POST /v1/traces was made first."""
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    client.put(f"/v1/traces/{trace_id}/spans/{span_id}", json=_dump(_span(trace_id, span_id)))
    assert client.get(f"/v1/traces/{trace_id}").status_code == 200
    client.post(
        "/v1/traces",
        json=_dump(
            Trace(trace_id=trace_id, name="late", state=State.OPEN, metadata={"user_id": "u9"})
        ),
    )
    detail = client.get(f"/v1/traces/{trace_id}").json()
    assert (detail["name"], detail["metadata"]) == ("late", {"user_id": "u9"})


def test_envelope_open_then_close_is_one_record(make_client: Make) -> None:
    client = make_client()
    trace_id, span_id, eid = new_trace_id(), new_span_id(), new_envelope_id()
    opened = _tool(trace_id, span_id, closed=False, envelope_id=eid)
    closed = _tool(trace_id, span_id, closed=True, envelope_id=eid, result="done")

    first = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(opened)]})
    assert (first.status_code, first.json()["accepted"]) == (201, 1)
    second = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(closed)]})
    assert (second.json()["accepted"], second.json()["deduped"]) == (1, 0)

    (stored,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert stored["envelope_status"]["state"] == "closed"
    assert stored["output"]["raw"] == "done"

    # closed is immutable: a late open write cannot reopen it
    late = client.post("/v1/envelopes:batch", json={"envelopes": [_dump(opened)]})
    assert (late.json()["accepted"], late.json()["deduped"]) == (0, 1)
    (stored,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert stored["envelope_status"]["state"] == "closed"


def test_span_read_returns_only_that_span_in_call_order(make_client: Make) -> None:
    client = make_client()
    trace_id, mine, other = new_trace_id(), new_span_id(), new_span_id()
    batch = [
        _tool(trace_id, mine, "b", sequence=2),
        _tool(trace_id, mine, "a", sequence=1),
        _tool(trace_id, other, "elsewhere", sequence=1),
    ]
    client.post("/v1/envelopes:batch", json={"envelopes": [_dump(e) for e in batch]})

    read = client.get(f"/v1/traces/{trace_id}/spans/{mine}/envelopes").json()["envelopes"]
    assert [e["name"] for e in read] == ["a", "b"]
    assert [Envelope.model_validate(e).name for e in read] == ["a", "b"]  # valid primitives again

    whole = client.get(f"/v1/traces/{trace_id}/envelopes").json()["envelopes"]
    assert len(whole) == 3
    assert client.get(f"/v1/traces/{new_trace_id()}/envelopes").status_code == 404


def test_llm_envelope_round_trips_losslessly(make_client: Make) -> None:
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
            messages=[Message(role="user", content="hi")],  # type: ignore[arg-type]
            raw={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        ),
        output=LlmOutput(raw={"id": "chatcmpl-1", "choices": []}, provider="openai"),
    )
    client.post("/v1/envelopes:batch", json={"envelopes": [_dump(envelope)]})
    (read,) = client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"]
    assert Envelope.model_validate(read) == envelope


def test_a_malformed_envelope_rejects_the_whole_batch(make_client: Make) -> None:
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    good = _dump(_tool(trace_id, span_id))
    bad = _dump(_tool(trace_id, span_id))
    bad["kind"] = "router"  # not a member of the closed Kind enum

    response = client.post("/v1/envelopes:batch", json={"envelopes": [good, bad]})
    assert response.status_code == 400
    assert "invalid envelope" in response.json()["detail"]
    # all or nothing: the good one was not stored either
    assert client.get(f"/v1/traces/{trace_id}/spans/{span_id}/envelopes").json()["envelopes"] == []


def test_the_pre_1_0_envelope_shape_is_no_longer_accepted(make_client: Make) -> None:
    client = make_client()
    legacy = {"envelope_id": "old1", "trace_id": "legacy-trace", "node_id": "llm", "dims": {}}
    response = client.post("/v1/envelopes:batch", json={"envelopes": [legacy]})
    assert response.status_code == 400


def test_an_unsupported_schema_major_is_rejected(make_client: Make) -> None:
    client = make_client()
    trace_id, span_id = new_trace_id(), new_span_id()
    body = _dump(_tool(trace_id, span_id))
    body["schema_version"] = "7.0"
    response = client.post("/v1/envelopes:batch", json={"envelopes": [body]})
    assert response.status_code == 400
    assert "schema_version" in response.json()["detail"]

    ok = _dump(_tool(trace_id, span_id))
    ok["schema_version"] = "0.99"  # a newer minor of the same major is read
    assert client.post("/v1/envelopes:batch", json={"envelopes": [ok]}).status_code == 201


def test_batch_limits(make_client: Make) -> None:
    client = make_client(max_batch=2)
    trace_id, span_id = new_trace_id(), new_span_id()
    three = [_dump(_tool(trace_id, span_id, sequence=i)) for i in range(1, 4)]
    assert client.post("/v1/envelopes:batch", json={"envelopes": three}).status_code == 400
    assert client.post("/v1/envelopes:batch", json={"nope": []}).status_code == 400


def test_tenants_are_isolated_and_scopes_enforced(make_client: Make) -> None:
    client = make_client(api_keys="a:t1:ingest+read,b:t2:ingest+read,ro:t1:read")
    trace_id, span_id = new_trace_id(), new_span_id()
    path = f"/v1/traces/{trace_id}/spans/{span_id}"
    head_a = {"Authorization": "Bearer a"}

    assert client.put(path, json=_dump(_span(trace_id, span_id))).status_code == 401
    read_only = client.put(
        path, headers={"Authorization": "Bearer ro"}, json=_dump(_span(trace_id, span_id))
    )
    assert read_only.status_code == 403
    assert client.put(path, headers=head_a, json=_dump(_span(trace_id, span_id))).status_code == 200
    client.post(
        "/v1/envelopes:batch",
        headers=head_a,
        json={"envelopes": [_dump(_tool(trace_id, span_id))]},
    )

    mine = client.get(f"{path}/envelopes", headers=head_a).json()["envelopes"]
    theirs = client.get(f"{path}/envelopes", headers={"Authorization": "Bearer b"})
    assert len(mine) == 1
    assert theirs.json()["envelopes"] == []
    assert (
        client.get(f"/v1/traces/{trace_id}", headers={"Authorization": "Bearer b"}).status_code
        == 404
    )
