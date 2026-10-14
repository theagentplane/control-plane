"""API key parsing and hashing.

Keys come from ``CONTROL_PLANE_API_KEYS`` (``key:tenant:scope+scope,...``) and from hashed rows
in the database. With neither, the service is open: anonymous, tenant ``local``, all scopes.
That is for a single trusted local host only.
"""

import hashlib

from control_plane.auth.auth_types import SCOPES, ApiKeyView, Principal

ANONYMOUS = Principal(api_key="dev", tenant_id="local", scopes=frozenset(SCOPES), name="dev")


def hash_token(token: str) -> str:
    """The stored form of an API key."""
    return hashlib.sha256(token.encode()).hexdigest()


def parse_api_keys(raw: str) -> dict[str, Principal]:
    """Parse ``key:tenant:scope+scope,...`` into a key to principal map."""
    table: dict[str, Principal] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 3:
            raise ValueError(f"bad CONTROL_PLANE_API_KEYS entry: {part!r}")
        key, tenant, scopes = bits
        unknown = set(scopes.split("+")) - set(SCOPES)
        if unknown:
            raise ValueError(f"unknown scope(s) {sorted(unknown)} in {part!r}")
        table[key] = Principal(
            api_key=key, tenant_id=tenant, scopes=frozenset(scopes.split("+")), name=key
        )
    return table


def env_key_views(raw: str) -> list[ApiKeyView]:
    """The environment keys as listed in the admin view (the host already holds the secret)."""
    return [
        ApiKeyView(
            id=f"env:{key}",
            name=principal.name,
            key_prefix=key[:8],
            tenant_id=principal.tenant_id,
            scopes=sorted(principal.scopes),
            source="env",
            created_at=None,
            secret=key,
        )
        for key, principal in parse_api_keys(raw).items()
    ]
