"""Auth types: the caller's identity and how an API key is shown back."""

from dataclasses import dataclass
from datetime import datetime

SCOPES: tuple[str, ...] = ("ingest", "read", "admin")


@dataclass(frozen=True)
class Principal:
    """Who is calling: a tenant and the scopes the key grants."""

    api_key: str
    tenant_id: str
    scopes: frozenset[str]
    name: str = "dev"


@dataclass
class ApiKeyView:
    """An API key as listed. ``secret`` is set only at creation (or for env keys)."""

    id: str
    name: str
    key_prefix: str
    tenant_id: str
    scopes: list[str]
    source: str
    created_at: datetime | None
    secret: str | None = None
