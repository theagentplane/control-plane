"""Chronicle routes: traces, spans and envelopes on the primitives schema.

Bodies are the primitives entities, validated with the same models Chronicle builds them with.
Writes are acknowledged after the transaction commits (there is no queued acknowledgement), and
a batch is all or nothing.
"""

from typing import Any

from agentplane_primitives.models import Envelope, Span, Trace
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from control_plane.api.dependencies_utils import get_state, require_scopes
from control_plane.api.schema_version_utils import ensure_supported_schema
from control_plane.api.state_types import AppState
from control_plane.auth import Principal

router = APIRouter(tags=["chronicle"])


def _validated(model: Any, payload: Any, what: str) -> Any:
    try:
        ensure_supported_schema(payload)
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise HTTPException(
            status_code=400, detail=f"invalid {what}: {first['msg']} at {list(first['loc'])}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {what}: {exc}") from exc


@router.post("/v1/traces")
def register_trace(
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(require_scopes("ingest")),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Register a trace: idempotent, client-supplied id, labels written once."""
    trace: Trace = _validated(Trace, payload, "trace")
    created, stored = state.repos.traces.register(principal.tenant_id, trace)
    return JSONResponse(stored, status_code=201 if created else 200)


@router.put("/v1/traces/{trace_id}/spans/{span_id}")
def put_span(
    trace_id: str,
    span_id: str,
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(require_scopes("ingest")),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    """Open a span, and close it later. A closed span is immutable."""
    span: Span = _validated(Span, payload, "span")
    if (span.trace_id, span.span_id) != (trace_id, span_id):
        raise HTTPException(status_code=400, detail="path ids do not match the body")
    stored = state.repos.traces.upsert_span(principal.tenant_id, span)
    return {"span_id": span_id, "status": "stored" if stored else "deduped"}


@router.post("/v1/envelopes:batch")
def append_envelopes(
    request: Request,
    payload: dict[str, Any] = Body(...),
    principal: Principal = Depends(require_scopes("ingest")),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    """Append envelopes. An open record is replaced by its close write; closed is immutable."""
    settings = state.settings
    if int(request.headers.get("content-length") or 0) > settings.max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")
    items = payload.get("envelopes")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="expected {envelopes: [...]}")
    if len(items) > settings.max_batch:
        raise HTTPException(status_code=400, detail=f"batch max {settings.max_batch}")
    batch: list[Envelope] = [_validated(Envelope, item, "envelope") for item in items]
    result = state.repos.envelopes.append(principal.tenant_id, batch)
    return JSONResponse({"status": "stored", **result}, status_code=201)


@router.get("/v1/traces/{trace_id}/spans/{span_id}/envelopes")
def span_envelopes(
    trace_id: str,
    span_id: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """The envelopes of one span of one trace, in call order (replay reads this)."""
    return {"envelopes": state.repos.envelopes.list_span(principal.tenant_id, trace_id, span_id)}


@router.get("/v1/traces/{trace_id}/envelopes")
def trace_envelopes(
    trace_id: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Every envelope of a trace in start order (the waterfall)."""
    if state.repos.traces.get(principal.tenant_id, trace_id) is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return {"envelopes": state.repos.envelopes.list_trace(principal.tenant_id, trace_id)}


@router.get("/v1/traces/{trace_id}")
def get_trace(
    trace_id: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """One trace: labels, derived state and its spans."""
    found = state.repos.traces.get(principal.tenant_id, trace_id)
    if found is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return found


@router.get("/v1/traces")
def search_traces(
    session_id: str | None = None,
    message_id: str | None = None,
    user_id: str | None = None,
    label: list[str] = Query(default_factory=list, description="key:value, repeatable"),
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Recent traces first, filtered by labels and free text."""
    labels: dict[str, str] = {}
    for key, value in (
        ("session_id", session_id),
        ("message_id", message_id),
        ("user_id", user_id),
    ):
        if value:
            labels[key] = value
    for item in label:
        key, sep, value = item.partition(":")
        if not sep or not key:
            raise HTTPException(status_code=400, detail=f"label must be key:value, got {item!r}")
        labels[key] = value
    return {
        "traces": state.repos.traces.search(
            principal.tenant_id, labels=labels, q=q, limit=limit, offset=offset
        )
    }
