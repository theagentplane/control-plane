"""Admin routes: API keys, and re-seeding or clearing a tenant's governance configuration."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from control_plane.api.dependencies_utils import get_state, require_scopes
from control_plane.api.state_types import AppState
from control_plane.api.wire_types import GovernanceSeedBody, KeyCreateBody
from control_plane.auth import SCOPES, Principal, env_key_views
from control_plane.seed import seed_governance

router = APIRouter(tags=["admin"])


def _view(key: Any) -> dict[str, Any]:
    return {
        "id": key.id,
        "name": key.name,
        "key_prefix": key.key_prefix,
        "tenant_id": key.tenant_id,
        "scopes": key.scopes,
        "source": key.source,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "secret": key.secret,
    }


@router.get("/v1/admin/keys")
def list_keys(
    principal: Principal = Depends(require_scopes("admin")), state: AppState = Depends(get_state)
) -> dict[str, Any]:
    """Environment keys include the secret (the host already has it); stored keys show a prefix."""
    stored = state.repos.api_keys.list_keys()
    env = env_key_views(state.settings.api_keys)
    return {
        "keys": [_view(k) for k in [*env, *stored]],
        "auth_disabled": not state.env_keys and not stored,
    }


@router.post("/v1/admin/keys")
def create_key(
    body: KeyCreateBody,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Create a key. The secret is returned once."""
    unknown = set(body.scopes) - set(SCOPES)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown scope(s): {sorted(unknown)}")
    created = state.repos.api_keys.create(
        name=body.name, tenant_id=body.tenant_id, scopes=body.scopes
    )
    return _view(created)


@router.delete("/v1/admin/keys/{key_id}")
def delete_key(
    key_id: str,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, str]:
    if key_id.startswith("env:"):
        raise HTTPException(
            status_code=400, detail="env keys are removed by unsetting CONTROL_PLANE_API_KEYS"
        )
    state.repos.api_keys.delete(key_id)
    return {"status": "deleted"}


@router.post("/v1/admin/governance/reseed")
def reseed_governance(
    body: GovernanceSeedBody | None = None,
    principal: Principal = Depends(require_scopes("admin")),
    state: AppState = Depends(get_state),
) -> dict[str, int]:
    """Replace this tenant's governance configuration from YAML (discards edits)."""
    written = seed_governance(
        state.repos.governance,
        principal.tenant_id,
        body.governance if body else None,
        reset=True,
    )
    return {"written": written}


@router.post("/v1/admin/governance/clear")
def clear_governance(
    principal: Principal = Depends(require_scopes("admin")), state: AppState = Depends(get_state)
) -> dict[str, str]:
    """Delete this tenant's segments, budgets and policies (the ledger is kept)."""
    state.repos.governance.clear(principal.tenant_id)
    return {"status": "cleared"}
