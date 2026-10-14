"""Liveness and readiness probes (unauthenticated)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from control_plane import __version__
from control_plane.api.dependencies_utils import get_state
from control_plane.api.state_types import AppState
from control_plane.db.migration_utils import current_revision, head_revision

router = APIRouter(tags=["health"])


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> dict[str, Any]:
    """The process is up. Does not touch the database."""
    return {
        "status": "ok",
        "service": "control-plane",
        "version": __version__,
        "max_batch": state.settings.max_batch,
        "max_body_bytes": state.settings.max_body_bytes,
    }


@router.get("/ready")
def ready(state: AppState = Depends(get_state)) -> dict[str, str]:
    """The database answers and is at the newest migration."""
    try:
        with state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        at = current_revision(state.engine)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"database unavailable: {type(exc).__name__}"
        ) from exc
    head = head_revision()
    if at != head:
        raise HTTPException(
            status_code=503, detail=f"database is at revision {at!r}, expected {head!r}"
        )
    return {"status": "ready", "revision": head}
