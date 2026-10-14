"""Governance routes: the configuration TokenOps pulls, and its CRUD.

``GET /v1/governance/{agent}`` is how an agent running TokenOps gets its governor
configuration: the exact dict ``build_governor`` consumes, with an ``ETag`` so a client can ask
"has it changed" with ``If-None-Match`` and get a ``304``.
"""

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from control_plane.api.dependencies_utils import get_state, require_scopes
from control_plane.api.state_types import AppState
from control_plane.api.wire_types import BudgetBody, PolicyBody, SegmentBody
from control_plane.auth import Principal

router = APIRouter(tags=["governance"])


def _plain(obj: Any) -> dict[str, Any]:
    return {k: getattr(obj, k) for k in obj.__dataclass_fields__}


@router.get("/v1/governance/{agent}")
def governance_for_agent(
    agent: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> Any:
    """The governance configuration for ``agent``. Supports ``If-None-Match``."""
    config = state.repos.governance.config_for(principal.tenant_id, agent)
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    etag = f'"{digest[:32]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return config


# ---- segments ---------------------------------------------------------------------------


@router.get("/v1/segments")
def list_segments(
    principal: Principal = Depends(require_scopes("read")), state: AppState = Depends(get_state)
) -> list[dict[str, Any]]:
    return [_plain(s) for s in state.repos.governance.list_segments(principal.tenant_id)]


@router.put("/v1/segments")
def upsert_segment(
    body: SegmentBody,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    return _plain(state.repos.governance.upsert_segment(principal.tenant_id, body.to_model()))


@router.get("/v1/segments/{sid}")
def get_segment(
    sid: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    found = state.repos.governance.get_segment(principal.tenant_id, sid)
    if found is None:
        raise HTTPException(status_code=404, detail="segment not found")
    return _plain(found)


@router.delete("/v1/segments/{sid}")
def delete_segment(
    sid: str,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    state.repos.governance.delete_segment(principal.tenant_id, sid)
    return {"status": "deleted"}


# ---- budgets ----------------------------------------------------------------------------


@router.get("/v1/budgets")
def list_budgets(
    principal: Principal = Depends(require_scopes("read")), state: AppState = Depends(get_state)
) -> list[dict[str, Any]]:
    return [_plain(b) for b in state.repos.governance.list_budgets(principal.tenant_id)]


@router.put("/v1/budgets")
def upsert_budget(
    body: BudgetBody,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    return _plain(state.repos.governance.upsert_budget(principal.tenant_id, body.to_model()))


@router.get("/v1/budgets/{bid}")
def get_budget(
    bid: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    found = state.repos.governance.get_budget(principal.tenant_id, bid)
    if found is None:
        raise HTTPException(status_code=404, detail="budget not found")
    return _plain(found)


@router.delete("/v1/budgets/{bid}")
def delete_budget(
    bid: str,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    state.repos.governance.delete_budget(principal.tenant_id, bid)
    return {"status": "deleted"}


# ---- policies ---------------------------------------------------------------------------


@router.get("/v1/policies")
def list_policies(
    principal: Principal = Depends(require_scopes("read")), state: AppState = Depends(get_state)
) -> list[dict[str, Any]]:
    return [_plain(p) for p in state.repos.governance.list_policies(principal.tenant_id)]


@router.put("/v1/policies")
def upsert_policy(
    body: PolicyBody,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    try:
        saved = state.repos.governance.upsert_policy(principal.tenant_id, body.to_model())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _plain(saved)


@router.get("/v1/policies/{pid}")
def get_policy(
    pid: str,
    principal: Principal = Depends(require_scopes("read")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    found = state.repos.governance.get_policy(principal.tenant_id, pid)
    if found is None:
        raise HTTPException(status_code=404, detail="policy not found")
    return _plain(found)


@router.delete("/v1/policies/{pid}")
def delete_policy(
    pid: str,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    state.repos.governance.delete_policy(principal.tenant_id, pid)
    return {"status": "deleted"}
