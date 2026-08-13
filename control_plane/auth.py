"""Bearer API-key auth with tenant + scopes (RFC control-plane-api)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from control_plane.settings import Settings, load_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    api_key: str
    tenant_id: str
    scopes: frozenset[str]


def parse_api_keys(raw: str) -> dict[str, Principal]:
    """Parse ``key:tenant:scope+scope,...`` into a key→Principal map."""
    out: dict[str, Principal] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            raise ValueError(f"bad CONTROL_PLANE_API_KEYS entry: {part!r}")
        key, tenant, scopes = bits
        out[key] = Principal(api_key=key, tenant_id=tenant, scopes=frozenset(scopes.split("+")))
    return out


def require_scopes(*needed: str):
    """FastAPI dependency factory. When no keys configured, allows anonymous local tenant."""

    async def _dep(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        settings: Settings = Depends(load_settings),
    ) -> Principal:
        table = getattr(request.app.state, "api_keys", None)
        if table is None:
            table = parse_api_keys(settings.api_keys)
            request.app.state.api_keys = table
        if not table:
            return Principal(api_key="dev", tenant_id="local", scopes=frozenset({"ingest", "read", "admin"}))
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing bearer token")
        principal = table.get(creds.credentials)
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid api key")
        if not set(needed) <= principal.scopes:
            raise HTTPException(status_code=403, detail=f"requires scopes: {', '.join(needed)}")
        return principal

    return _dep
