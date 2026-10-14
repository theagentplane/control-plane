"""Governance tables: configuration TokenOps pulls, and the shared ledger it writes.

Configuration (segments, budgets, policy instances) is what an agent running TokenOps fetches
with ``GET /v1/governance/{agent}``. The ledger (spend and in-flight counts) is what agents share
across processes. A run is a Chronicle trace, so everything per run is keyed by ``trace_id``;
there is no separate run table. A halt is not a table: it is an attribute of the trace
(``traces.attributes``).
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
)

from control_plane.db.metadata_types import metadata

segments = Table(
    "segments",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("id", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("dimension", String(16), nullable=False),
    Column("tag_key", String(128)),
    Column("match_value", String(255)),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_segments"),
)

budgets = Table(
    "budgets",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("id", String(64), nullable=False),
    Column("limit_micros", BigInteger),
    Column("dimension", String(16), nullable=False),
    Column("tag_key", String(128)),
    Column("period", String(32), nullable=False, server_default="lifetime"),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_budgets"),
)

policy_instances = Table(
    "policy_instances",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("id", String(64), nullable=False),
    Column("template", String(64), nullable=False),
    Column("params", JSON, nullable=False),
    Column("agent", String(255)),
    Column("budget_id", String(64)),
    Column("segment_id", String(64)),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("data_scope", String(16), nullable=False, server_default="local"),
    PrimaryKeyConstraint("tenant_id", "id", name="pk_policy_instances"),
)

ledger_spent = Table(
    "ledger_spent",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("budget_id", String(64), nullable=False),
    Column("segment_key", String(255), nullable=False),
    Column("period", String(32), nullable=False),
    Column("spent_micros", BigInteger, nullable=False, server_default="0"),
    PrimaryKeyConstraint("tenant_id", "budget_id", "segment_key", "period", name="pk_ledger_spent"),
)

ledger_inflight = Table(
    "ledger_inflight",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("segment_key", String(255), nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("tenant_id", "segment_key", name="pk_ledger_inflight"),
)
