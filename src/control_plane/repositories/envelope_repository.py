"""Envelopes (Chronicle design sections 2.4 and 5).

An envelope is **one record, mutable until closed**: an open write (``emit_open``) is replaced by
its close write, and a write for an envelope that is already closed is a dedupe, never a reopen.
The key is ``(tenant, trace_id, envelope_id)``; envelope ids are unique within a trace.
A batch is written in one transaction, so it is all or nothing.
"""

from typing import Any

from agentplane_primitives.enums import State
from agentplane_primitives.models import Envelope
from sqlalchemy import Engine, and_, select

from control_plane.db.tables import envelopes
from control_plane.repositories.trace_repository import ensure_trace, widen_trace
from control_plane.repositories.upsert_utils import upsert

SEQUENCE_KEY = "chronicle.sequence"


class EnvelopeRepository:
    """Writes and reads envelopes."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, tenant_id: str, batch: list[Envelope]) -> dict[str, Any]:
        """Store ``batch``. Returns ``accepted``, ``deduped`` and the envelope ids."""
        accepted = deduped = 0
        with self._engine.begin() as conn:
            for envelope in batch:
                key = {
                    "tenant_id": tenant_id,
                    "trace_id": envelope.trace_id,
                    "envelope_id": envelope.envelope_id,
                }
                existing = conn.execute(
                    select(envelopes.c.state).where(
                        and_(*[envelopes.c[k] == v for k, v in key.items()])
                    )
                ).first()
                if existing is not None and existing.state == State.CLOSED.value:
                    deduped += 1
                    continue
                status = envelope.envelope_status
                sequence = envelope.metadata.get(SEQUENCE_KEY)
                ensure_trace(conn, tenant_id, envelope.trace_id, status.started_at)
                upsert(
                    conn,
                    envelopes,
                    key,
                    {
                        "span_id": envelope.span_id,
                        "parent_envelope_id": envelope.parent_envelope_id,
                        "name": envelope.name,
                        "kind": envelope.kind.value,
                        "state": status.state.value,
                        "status": status.status.value if status.status else None,
                        "sequence": sequence
                        if isinstance(sequence, int) and not isinstance(sequence, bool)
                        else None,
                        "started_at": status.started_at,
                        "ended_at": status.ended_at,
                        "schema_version": envelope.schema_version,
                        "body": envelope.model_dump(mode="json", exclude_none=True),
                    },
                )
                widen_trace(conn, tenant_id, envelope.trace_id, status.started_at, status.ended_at)
                accepted += 1
        return {
            "accepted": accepted,
            "deduped": deduped,
            "envelope_ids": [e.envelope_id for e in batch],
        }

    def list_span(self, tenant_id: str, trace_id: str, span_id: str) -> list[dict[str, Any]]:
        """The envelopes of one span of one trace, in call order (for replay)."""
        stmt = (
            select(envelopes.c.body)
            .where(
                and_(
                    envelopes.c.tenant_id == tenant_id,
                    envelopes.c.trace_id == trace_id,
                    envelopes.c.span_id == span_id,
                )
            )
            .order_by(envelopes.c.sequence.is_(None), envelopes.c.sequence, envelopes.c.started_at)
        )
        with self._engine.connect() as conn:
            return [row.body for row in conn.execute(stmt)]

    def list_trace(self, tenant_id: str, trace_id: str) -> list[dict[str, Any]]:
        """Every envelope of a trace in start order (the waterfall)."""
        stmt = (
            select(envelopes.c.body)
            .where(and_(envelopes.c.tenant_id == tenant_id, envelopes.c.trace_id == trace_id))
            .order_by(envelopes.c.started_at, envelopes.c.sequence.is_(None), envelopes.c.sequence)
        )
        with self._engine.connect() as conn:
            return [row.body for row in conn.execute(stmt)]
