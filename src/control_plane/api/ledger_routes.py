"""Ledger routes: the shared state TokenOps agents read and write.

- ``POST /v1/ledger/precheck``: the one read a pre-call pass needs.
- ``POST /v1/ledger/events:batch``: one write per crossing, applied in order in one transaction.
  Events are not deduplicated.
- Halts and the per-trace view are keyed by ``trace_id``: a run is a Chronicle trace.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.api.dependencies_utils import get_state, require_scopes
from control_plane.api.state_types import AppState
from control_plane.api.wire_types import EventsBody, HaltBody, PrecheckBody
from control_plane.auth import Principal

router = APIRouter(tags=["ledger"])


@router.post("/v1/ledger/precheck")
def precheck(
    body: PrecheckBody,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Halt, spend, in-flight and window for one trace in a single read."""
    return state.repos.ledger.precheck(
        principal.tenant_id,
        body.trace_id,
        segment_keys=body.segment_keys,
        budgets=body.budgets,
        want=body.want,
    )


@router.post("/v1/ledger/events:batch", status_code=201)
def events_batch(
    request: Request,
    body: EventsBody,
    principal: Principal = Depends(require_scopes("ingest")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Apply ledger events (``spent_add``, ``admit``, ``complete``, ``step``, ``halt_*``)."""
    if int(request.headers.get("content-length") or 0) > state.settings.max_body_bytes:
        raise HTTPException(status_code=413, detail="payload too large")
    if len(body.events) > state.settings.max_batch:
        raise HTTPException(status_code=400, detail=f"batch max {state.settings.max_batch}")
    try:
        return state.repos.ledger.apply_events(principal.tenant_id, body.events)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/v1/ledger/traces")
def ledger_traces(
    halted_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Recent traces with their ledger facts: the TokenOps run list.

    A run is a trace, so this is the trace list joined with halt, spend and step count.
    ``halted_only`` lists the halted traces.
    """
    tenant = principal.tenant_id
    if halted_only:
        ids = state.repos.ledger.halted_trace_ids(tenant, limit)
        found = [t for t in (state.repos.traces.get(tenant, i) for i in ids) if t is not None]
    else:
        found = state.repos.traces.search(tenant, limit=limit)
    ledger = state.repos.ledger.trace_summaries(tenant, [t["trace_id"] for t in found])
    return {"traces": [{**t, **ledger[t["trace_id"]]} for t in found]}


@router.get("/v1/ledger/traces/{trace_id}")
def ledger_trace(
    trace_id: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """One trace's halt, state and per-policy statistics."""
    tenant = principal.tenant_id
    halted, reason = state.repos.ledger.halt_status(tenant, trace_id)
    return {
        "trace_id": trace_id,
        "halted": halted,
        "halt_reason": reason,
        "state": state.repos.ledger.state(tenant, trace_id),
        "policy_stats": state.repos.ledger.policy_stats(tenant, trace_id),
    }


@router.get("/v1/ledger/traces/{trace_id}/halt")
def halt_get(
    trace_id: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    halted, reason = state.repos.ledger.halt_status(principal.tenant_id, trace_id)
    return {"halted": halted, "halt_reason": reason}


@router.post("/v1/ledger/traces/{trace_id}/halt")
def halt_set(
    trace_id: str,
    body: HaltBody,
    principal: Principal = Depends(require_scopes("ingest")),
    state: AppState = Depends(get_state),
) -> dict[str, bool]:
    state.repos.ledger.mark_halted(principal.tenant_id, trace_id, body.reason)
    return {"halted": True}


@router.delete("/v1/ledger/traces/{trace_id}/halt")
def halt_clear(
    trace_id: str,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, bool]:
    state.repos.ledger.clear_halt(principal.tenant_id, trace_id)
    return {"halted": False}
