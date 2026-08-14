"""Bearer API-key auth with tenant + scopes.

Caller: every route declares required scopes. Keys come from env
``CONTROL_PLANE_API_KEYS`` and/or hashed rows in SQLite. Empty both → local
anonymous (dev only).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from control_plane.models import ApiKeyView
from control_plane.settings import Settings, load_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    api_key: str
    tenant_id: str
    scopes: frozenset[str]
    name: str = "dev"


def parse_api_keys(raw: str) -> dict[str, Principal]:
    """Parse ``key:tenant:scope+scope,...`` into a key→Principal map (env secrets)."""
    out: dict[str, Principal] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            raise ValueError(f"bad CONTROL_PLANE_API_KEYS entry: {part!r}")
        key, tenant, scopes = bits
        out[key] = Principal(
            api_key=key,
            tenant_id=tenant,
            scopes=frozenset(scopes.split("+")),
            name=key,
        )
    return out


def env_key_views(raw: str) -> list[ApiKeyView]:
    views: list[ApiKeyView] = []
    for key, principal in parse_api_keys(raw).items():
        views.append(
            ApiKeyView(
                id=f"env:{key}",
                name=principal.name,
                key_prefix=key[:8],
                tenant_id=principal.tenant_id,
                scopes=sorted(principal.scopes),
                source="env",
                created_at=0,
                secret=key,
            )
        )
    return views


def require_scopes(*needed: str):
    async def _dep(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        settings: Settings = Depends(load_settings),
    ) -> Principal:
        env_table: dict[str, Principal] = getattr(request.app.state, "api_keys", None) or {}
        store = getattr(request.app.state, "store", None)
        has_db_keys = bool(store and store.list_api_keys())
        if not env_table and not has_db_keys and not settings.api_keys:
            return Principal(
                api_key="dev",
                tenant_id="local",
                scopes=frozenset({"ingest", "read", "admin"}),
                name="dev",
            )
        if creds is None or creds.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = creds.credentials
        principal = env_table.get(token)
        if principal is None and store is not None:
            row = store.lookup_api_key(token)
            if row is not None:
                principal = Principal(
                    api_key=token,
                    tenant_id=row.tenant_id,
                    scopes=frozenset(row.scopes),
                    name=row.name,
                )
        if principal is None:
            raise HTTPException(status_code=401, detail="invalid api key")
        if not set(needed) <= principal.scopes:
            raise HTTPException(status_code=403, detail=f"requires scopes: {', '.join(needed)}")
        return principal

    return _dep
