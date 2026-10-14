"""FastAPI dependencies: the app state and bearer authentication with scopes.

With no environment keys and no stored keys the service is open (anonymous, tenant ``local``,
all scopes): that is for a single trusted local host. Handlers are plain ``def`` so FastAPI runs
them in its thread pool, which is what the synchronous database layer needs.
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from control_plane.api.state_types import AppState
from control_plane.auth import ANONYMOUS, Principal

_bearer = HTTPBearer(auto_error=False)


def get_state(request: Request) -> AppState:
    """The ``AppState`` of the running app."""
    state: AppState = request.app.state.cp
    return state


def require_scopes(*needed: str) -> Callable[..., Principal]:
    """A dependency that authenticates the caller and checks it holds every scope in ``needed``."""

    def dependency(
        state: AppState = Depends(get_state),
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal:
        if not state.env_keys and state.repos.api_keys.count() == 0:
            return ANONYMOUS
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing bearer token")
        principal = state.env_keys.get(creds.credentials)
        if principal is None:
            stored = state.repos.api_keys.lookup(creds.credentials)
            if stored is not None:
                principal = Principal(
                    api_key=creds.credentials,
                    tenant_id=stored.tenant_id,
                    scopes=frozenset(stored.scopes),
                    name=stored.name,
                )
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid api key")
        if not set(needed) <= principal.scopes:
            raise HTTPException(status_code=403, detail=f"requires scopes: {', '.join(needed)}")
        return principal

    return dependency
