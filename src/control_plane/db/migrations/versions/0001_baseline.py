"""Baseline: the Chronicle, governance and API key tables.

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="ui", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_table(
        "budgets",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("limit_micros", sa.BigInteger(), nullable=True),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("tag_key", sa.String(length=128), nullable=True),
        sa.Column("period", sa.String(length=32), server_default="lifetime", nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_budgets"),
    )
    op.create_table(
        "envelopes",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("envelope_id", sa.String(length=16), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_envelope_id", sa.String(length=16), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", "envelope_id", name="pk_envelopes"),
    )
    with op.batch_alter_table("envelopes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_envelopes_span", ["tenant_id", "trace_id", "span_id", "sequence"], unique=False
        )

    op.create_table(
        "ledger_inflight",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("segment_key", sa.String(length=255), nullable=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "segment_key", name="pk_ledger_inflight"),
    )
    op.create_table(
        "ledger_spent",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("budget_id", sa.String(length=64), nullable=False),
        sa.Column("segment_key", sa.String(length=255), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("spent_micros", sa.BigInteger(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "budget_id", "segment_key", "period", name="pk_ledger_spent"
        ),
    )
    op.create_table(
        "policy_instances",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("agent", sa.String(length=255), nullable=True),
        sa.Column("budget_id", sa.String(length=64), nullable=True),
        sa.Column("segment_id", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("data_scope", sa.String(length=16), server_default="local", nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_policy_instances"),
    )
    op.create_table(
        "segments",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("tag_key", sa.String(length=128), nullable=True),
        sa.Column("match_value", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_segments"),
    )
    op.create_table(
        "spans",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_envelope_id", sa.String(length=16), nullable=True),
        sa.Column("service", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", "span_id", name="pk_spans"),
    )
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.create_index(
            "ix_spans_parent", ["tenant_id", "trace_id", "parent_envelope_id"], unique=False
        )

    op.create_table(
        "trace_labels",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", "key", name="pk_trace_labels"),
    )
    with op.batch_alter_table("trace_labels", schema=None) as batch_op:
        batch_op.create_index("ix_trace_labels_lookup", ["tenant_id", "key", "value"], unique=False)

    op.create_table(
        "trace_policy_stats",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("policy", sa.String(length=64), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", "policy", name="pk_trace_policy_stats"),
    )
    op.create_table(
        "trace_state",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("step_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("window", sa.JSON(), nullable=False),
        sa.Column("velocity_micros_per_step", sa.Float(), server_default="0", nullable=False),
        sa.Column("last_ts", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", name="pk_trace_state"),
    )
    op.create_table(
        "traces",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("attributes", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "trace_id", name="pk_traces"),
    )
    with op.batch_alter_table("traces", schema=None) as batch_op:
        batch_op.create_index("ix_traces_tenant_started", ["tenant_id", "started_at"], unique=False)


def downgrade() -> None:

    with op.batch_alter_table("traces", schema=None) as batch_op:
        batch_op.drop_index("ix_traces_tenant_started")

    op.drop_table("traces")
    op.drop_table("trace_state")
    op.drop_table("trace_policy_stats")
    with op.batch_alter_table("trace_labels", schema=None) as batch_op:
        batch_op.drop_index("ix_trace_labels_lookup")

    op.drop_table("trace_labels")
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.drop_index("ix_spans_parent")

    op.drop_table("spans")
    op.drop_table("segments")
    op.drop_table("policy_instances")
    op.drop_table("ledger_spent")
    op.drop_table("ledger_inflight")
    with op.batch_alter_table("envelopes", schema=None) as batch_op:
        batch_op.drop_index("ix_envelopes_span")

    op.drop_table("envelopes")
    op.drop_table("budgets")
    op.drop_table("api_keys")
