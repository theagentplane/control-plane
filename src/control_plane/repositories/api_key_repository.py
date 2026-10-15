"""API keys: stored hashed, the secret is returned once at creation."""

import secrets
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, delete, func, select

from control_plane.auth import ApiKeyView, hash_token
from control_plane.db.engine_utils import write_transaction
from control_plane.db.tables import api_keys
from control_plane.time_utils import utc_now


class ApiKeyRepository:
    """Creates, lists, looks up and deletes API keys."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(
        self,
        *,
        name: str,
        tenant_id: str,
        scopes: Sequence[str],
        secret: str | None = None,
        source: str = "ui",
    ) -> ApiKeyView:
        token = secret or secrets.token_urlsafe(24)
        key_id = f"key_{uuid.uuid4().hex[:8]}"
        created = utc_now()
        with write_transaction(self._engine) as conn:
            conn.execute(
                api_keys.insert().values(
                    id=key_id,
                    name=name,
                    key_hash=hash_token(token),
                    key_prefix=token[:8],
                    tenant_id=tenant_id,
                    scopes="+".join(scopes),
                    source=source,
                    created_at=created,
                )
            )
        return ApiKeyView(
            id=key_id,
            name=name,
            key_prefix=token[:8],
            tenant_id=tenant_id,
            scopes=list(scopes),
            source=source,
            created_at=created,
            secret=token,
        )

    def list_keys(self) -> list[ApiKeyView]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(api_keys).order_by(api_keys.c.created_at.desc())).all()
        return [_view(r) for r in rows]

    def count(self) -> int:
        with self._engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(api_keys)).scalar_one())

    def lookup(self, token: str) -> ApiKeyView | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(api_keys).where(api_keys.c.key_hash == hash_token(token))
            ).first()
        return _view(row) if row else None

    def delete(self, key_id: str) -> None:
        with write_transaction(self._engine) as conn:
            conn.execute(delete(api_keys).where(api_keys.c.id == key_id))


def _view(row: Any) -> ApiKeyView:
    return ApiKeyView(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        tenant_id=row.tenant_id,
        scopes=str(row.scopes).split("+"),
        source=row.source,
        created_at=row.created_at,
    )
