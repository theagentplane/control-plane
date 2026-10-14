"""API key table. Keys are stored hashed; the secret is shown once, at creation."""

from sqlalchemy import Column, PrimaryKeyConstraint, String, Table, UniqueConstraint

from control_plane.db.metadata_types import metadata
from control_plane.db.utc_datetime_types import UtcDateTime

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("key_hash", String(64), nullable=False),
    Column("key_prefix", String(16), nullable=False),
    Column("tenant_id", String(64), nullable=False),
    Column("scopes", String(255), nullable=False),
    Column("source", String(16), nullable=False, server_default="ui"),
    Column("created_at", UtcDateTime, nullable=False),
    PrimaryKeyConstraint("id", name="pk_api_keys"),
    UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
)
