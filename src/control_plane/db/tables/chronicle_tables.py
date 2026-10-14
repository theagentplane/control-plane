"""Chronicle tables: traces, their labels, spans and envelopes (primitives entities).

Every table is tenant-scoped. A trace has no stored state or status of its own: its state is
derived from its spans. Labels live in their own indexed table (a trace's labels are written
once). ``attributes`` is the primitives ``Trace.attributes`` dict: mutable trace-level state
written by the control plane and consumers (the halt flag). Span and envelope rows keep the
columns queries need and the full primitives payload in ``body`` so what is served back is
lossless.
"""

from sqlalchemy import JSON, Column, Index, Integer, PrimaryKeyConstraint, String, Table

from control_plane.db.metadata_types import metadata
from control_plane.db.utc_datetime_types import UtcDateTime

traces = Table(
    "traces",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("name", String(255)),
    Column("attributes", JSON, nullable=False, server_default="{}"),
    Column("started_at", UtcDateTime),
    Column("ended_at", UtcDateTime),
    PrimaryKeyConstraint("tenant_id", "trace_id", name="pk_traces"),
)
Index("ix_traces_tenant_started", traces.c.tenant_id, traces.c.started_at)

trace_labels = Table(
    "trace_labels",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("key", String(128), nullable=False),
    Column("value", String(512), nullable=False),
    PrimaryKeyConstraint("tenant_id", "trace_id", "key", name="pk_trace_labels"),
)
Index("ix_trace_labels_lookup", trace_labels.c.tenant_id, trace_labels.c.key, trace_labels.c.value)

spans = Table(
    "spans",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("span_id", String(16), nullable=False),
    Column("parent_envelope_id", String(16)),
    Column("service", String(255), nullable=False),
    Column("state", String(16), nullable=False),
    Column("status", String(16)),
    Column("started_at", UtcDateTime, nullable=False),
    Column("ended_at", UtcDateTime),
    Column("schema_version", String(16), nullable=False),
    Column("body", JSON, nullable=False),
    PrimaryKeyConstraint("tenant_id", "trace_id", "span_id", name="pk_spans"),
)
Index("ix_spans_parent", spans.c.tenant_id, spans.c.trace_id, spans.c.parent_envelope_id)

envelopes = Table(
    "envelopes",
    metadata,
    Column("tenant_id", String(64), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("envelope_id", String(16), nullable=False),
    Column("span_id", String(16), nullable=False),
    Column("parent_envelope_id", String(16)),
    Column("name", String(255), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("state", String(16), nullable=False),
    Column("status", String(16)),
    Column("sequence", Integer),
    Column("started_at", UtcDateTime, nullable=False),
    Column("ended_at", UtcDateTime),
    Column("schema_version", String(16), nullable=False),
    Column("body", JSON, nullable=False),
    PrimaryKeyConstraint("tenant_id", "trace_id", "envelope_id", name="pk_envelopes"),
)
Index(
    "ix_envelopes_span",
    envelopes.c.tenant_id,
    envelopes.c.trace_id,
    envelopes.c.span_id,
    envelopes.c.sequence,
)
