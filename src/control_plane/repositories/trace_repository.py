"""Traces and spans (Chronicle design sections 2.2, 3.3, 10.2).

- A trace is registered idempotently with a client-supplied id; its **labels are written once**.
  A trace may also be created implicitly by the first span or envelope that names it, so
  Chronicle can use lazy registration; the first explicit registration of such a trace may then
  name it and set its labels (a trace that already has labels is never changed).
- A trace has no state or status of its own: it is ``open`` while any span is open.
- A closed span is immutable.
"""

from datetime import datetime
from typing import Any

from agentplane_primitives.enums import State
from agentplane_primitives.models import Span, Trace
from sqlalchemy import Connection, Engine, Select, and_, case, delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from control_plane.db.engine_utils import write_transaction
from control_plane.db.tables import (
    envelopes,
    ledger_spent,
    spans,
    trace_labels,
    traces,
)
from control_plane.repositories.upsert_utils import upsert
from control_plane.time_utils import iso


def ensure_trace(conn: Connection, tenant_id: str, trace_id: str, started: datetime | None) -> None:
    """Create the trace row if it does not exist (implicit creation by a span or envelope)."""
    exists_already = conn.execute(
        select(traces.c.trace_id).where(
            and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id)
        )
    ).first()
    if exists_already is not None:
        return
    try:
        with conn.begin_nested():
            conn.execute(
                traces.insert().values(tenant_id=tenant_id, trace_id=trace_id, started_at=started)
            )
    except IntegrityError:
        pass  # a concurrent writer created it


def read_attributes(conn: Connection, tenant_id: str, trace_id: str) -> dict[str, Any]:
    """The trace's ``attributes`` dict (empty if the trace does not exist)."""
    row = conn.execute(
        select(traces.c.attributes).where(
            and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id)
        )
    ).first()
    return dict(row.attributes) if row and row.attributes else {}


def merge_attributes(
    conn: Connection,
    tenant_id: str,
    trace_id: str,
    set_keys: dict[str, Any],
    remove_keys: tuple[str, ...] = (),
) -> None:
    """Set and remove keys in the trace's ``attributes``, creating the trace row if needed.

    Read-modify-write inside the caller's transaction. Two concurrent writers of the *same* key
    resolve last write wins; writers of different keys on the same trace can overwrite each
    other's change, which is acceptable for the flags kept here.
    """
    ensure_trace(conn, tenant_id, trace_id, None)
    current = read_attributes(conn, tenant_id, trace_id)
    current.update(set_keys)
    for key in remove_keys:
        current.pop(key, None)
    conn.execute(
        traces.update()
        .where(and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id))
        .values(attributes=current)
    )


def widen_trace(
    conn: Connection,
    tenant_id: str,
    trace_id: str,
    started: datetime | None,
    ended: datetime | None,
) -> None:
    """Keep the earliest start and the latest end (spans arrive out of order)."""
    row = conn.execute(
        select(traces.c.started_at, traces.c.ended_at).where(
            and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id)
        )
    ).first()
    if row is None:
        return
    starts = [v for v in (row.started_at, started) if v is not None]
    ends = [v for v in (row.ended_at, ended) if v is not None]
    conn.execute(
        traces.update()
        .where(and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id))
        .values(started_at=min(starts) if starts else None, ended_at=max(ends) if ends else None)
    )


class TraceRepository:
    """Reads and writes traces, labels and spans."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- registration ------------------------------------------------------------------

    def register(self, tenant_id: str, trace: Trace) -> tuple[bool, dict[str, Any]]:
        """Register ``trace``. Returns ``(created, stored)``; stored labels never change."""
        try:
            return self._register(tenant_id, trace)
        except IntegrityError:  # two registrations raced: the second is a repeat
            return self._register(tenant_id, trace)

    def _register(self, tenant_id: str, trace: Trace) -> tuple[bool, dict[str, Any]]:
        with write_transaction(self._engine) as conn:
            row = conn.execute(
                select(traces).where(
                    and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace.trace_id)
                )
            ).first()
            created = row is None
            if row is None:
                conn.execute(
                    traces.insert().values(
                        tenant_id=tenant_id,
                        trace_id=trace.trace_id,
                        name=trace.name,
                        started_at=trace.started_at,
                        ended_at=trace.ended_at,
                    )
                )
            elif row.name is None and trace.name:
                conn.execute(
                    traces.update()
                    .where(
                        and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace.trace_id)
                    )
                    .values(name=trace.name)
                )
            labels = self._labels(conn, tenant_id, [trace.trace_id]).get(trace.trace_id, {})
            if not labels and trace.metadata:  # labels are written once, by the first writer
                conn.execute(
                    trace_labels.insert(),
                    [
                        {
                            "tenant_id": tenant_id,
                            "trace_id": trace.trace_id,
                            "key": key,
                            "value": value,
                        }
                        for key, value in trace.metadata.items()
                    ],
                )
                labels = dict(trace.metadata)
            name = row.name if row is not None and row.name else trace.name
        return created, {"trace_id": trace.trace_id, "name": name, "metadata": labels}

    # ---- spans -------------------------------------------------------------------------

    def upsert_span(self, tenant_id: str, span: Span) -> bool:
        """Create or update a span. Returns False if it was already closed (a no-op)."""
        with write_transaction(self._engine) as conn:
            existing = conn.execute(
                select(spans.c.state).where(
                    and_(
                        spans.c.tenant_id == tenant_id,
                        spans.c.trace_id == span.trace_id,
                        spans.c.span_id == span.span_id,
                    )
                )
            ).first()
            if existing is not None and existing.state == State.CLOSED.value:
                return False
            ensure_trace(conn, tenant_id, span.trace_id, span.started_at)
            upsert(
                conn,
                spans,
                {"tenant_id": tenant_id, "trace_id": span.trace_id, "span_id": span.span_id},
                {
                    "parent_envelope_id": span.parent_envelope_id,
                    "service": span.service,
                    "state": span.state.value,
                    "status": span.status.value if span.status else None,
                    "started_at": span.started_at,
                    "ended_at": span.ended_at,
                    "schema_version": span.schema_version,
                    "body": span.model_dump(mode="json", exclude_none=True),
                },
            )
            widen_trace(conn, tenant_id, span.trace_id, span.started_at, span.ended_at)
        return True

    # ---- reads -------------------------------------------------------------------------

    def get(self, tenant_id: str, trace_id: str) -> dict[str, Any] | None:
        """One trace with its labels, derived state and spans, or ``None``."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(traces).where(
                    and_(traces.c.tenant_id == tenant_id, traces.c.trace_id == trace_id)
                )
            ).first()
            if row is None:
                return None
            span_rows = conn.execute(
                select(spans.c.body)
                .where(and_(spans.c.tenant_id == tenant_id, spans.c.trace_id == trace_id))
                .order_by(spans.c.started_at, spans.c.span_id)
            ).all()
            envelope_count = conn.execute(
                select(func.count())
                .select_from(envelopes)
                .where(and_(envelopes.c.tenant_id == tenant_id, envelopes.c.trace_id == trace_id))
            ).scalar_one()
            labels = self._labels(conn, tenant_id, [trace_id]).get(trace_id, {})
        bodies = [r.body for r in span_rows]
        return {
            "trace_id": trace_id,
            "name": row.name,
            "metadata": labels,
            "attributes": dict(row.attributes or {}),
            "state": _derived_state([b.get("state") for b in bodies]),
            "started_at": iso(row.started_at),
            "ended_at": iso(row.ended_at),
            "span_count": len(bodies),
            "envelope_count": envelope_count,
            "spans": bodies,
        }

    def search(
        self,
        tenant_id: str,
        *,
        labels: dict[str, str] | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Recent traces first. ``labels`` must all match; ``q`` matches id, name or a label."""
        stmt: Select[Any] = select(traces).where(traces.c.tenant_id == tenant_id)
        for key, value in (labels or {}).items():
            stmt = stmt.where(
                exists().where(
                    and_(
                        trace_labels.c.tenant_id == traces.c.tenant_id,
                        trace_labels.c.trace_id == traces.c.trace_id,
                        trace_labels.c.key == key,
                        trace_labels.c.value == value,
                    )
                )
            )
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    traces.c.trace_id.like(like),
                    traces.c.name.like(like),
                    exists().where(
                        and_(
                            trace_labels.c.tenant_id == traces.c.tenant_id,
                            trace_labels.c.trace_id == traces.c.trace_id,
                            trace_labels.c.value.like(like),
                        )
                    ),
                )
            )
        stmt = (
            stmt.order_by(
                func.coalesce(traces.c.ended_at, traces.c.started_at).desc(),
                traces.c.trace_id,
            )
            .limit(limit)
            .offset(offset)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
            ids = [r.trace_id for r in rows]
            label_map = self._labels(conn, tenant_id, ids)
            counts = self._span_counts(conn, tenant_id, ids)
        out: list[dict[str, Any]] = []
        for r in rows:
            total, open_spans = counts.get(r.trace_id, (0, 0))
            out.append(
                {
                    "trace_id": r.trace_id,
                    "name": r.name,
                    "metadata": label_map.get(r.trace_id, {}),
                    "attributes": dict(r.attributes or {}),
                    "state": State.OPEN.value if open_spans or not total else State.CLOSED.value,
                    "started_at": iso(r.started_at),
                    "ended_at": iso(r.ended_at),
                    "span_count": total,
                }
            )
        return out

    # ---- retention ---------------------------------------------------------------------

    def prune_before(self, cutoff: datetime, tenant_id: str | None = None) -> int:
        """Delete traces last touched before ``cutoff`` and everything keyed by them.

        Returns the number of traces removed. Nothing calls this in the background; it is
        run by ``control-plane db prune``.
        """
        activity = func.coalesce(traces.c.ended_at, traces.c.started_at)
        pick = select(traces.c.tenant_id, traces.c.trace_id).where(activity < cutoff)
        if tenant_id is not None:
            pick = pick.where(traces.c.tenant_id == tenant_id)
        removed = 0
        with write_transaction(self._engine) as conn:
            for row in conn.execute(pick).all():
                key = {"tenant_id": row.tenant_id, "trace_id": row.trace_id}
                for table in (
                    envelopes,
                    spans,
                    trace_labels,
                    traces,
                ):
                    conn.execute(
                        delete(table).where(and_(*[table.c[k] == v for k, v in key.items()]))
                    )
                conn.execute(
                    delete(ledger_spent).where(
                        and_(
                            ledger_spent.c.tenant_id == row.tenant_id,
                            ledger_spent.c.segment_key == f"trace:{row.trace_id}",
                        )
                    )
                )
                removed += 1
        return removed

    # ---- helpers -----------------------------------------------------------------------

    @staticmethod
    def _labels(
        conn: Connection, tenant_id: str, trace_ids: list[str]
    ) -> dict[str, dict[str, str]]:
        if not trace_ids:
            return {}
        rows = conn.execute(
            select(trace_labels.c.trace_id, trace_labels.c.key, trace_labels.c.value).where(
                and_(trace_labels.c.tenant_id == tenant_id, trace_labels.c.trace_id.in_(trace_ids))
            )
        ).all()
        result: dict[str, dict[str, str]] = {}
        for r in rows:
            result.setdefault(r.trace_id, {})[r.key] = r.value
        return result

    @staticmethod
    def _span_counts(
        conn: Connection, tenant_id: str, trace_ids: list[str]
    ) -> dict[str, tuple[int, int]]:
        if not trace_ids:
            return {}
        rows = conn.execute(
            select(
                spans.c.trace_id,
                func.count(),
                func.sum(case((spans.c.state != State.CLOSED.value, 1), else_=0)),
            )
            .where(and_(spans.c.tenant_id == tenant_id, spans.c.trace_id.in_(trace_ids)))
            .group_by(spans.c.trace_id)
        ).all()
        return {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows}


def _derived_state(states: list[Any]) -> str:
    if not states or any(s != State.CLOSED.value for s in states):
        return State.OPEN.value
    return State.CLOSED.value
