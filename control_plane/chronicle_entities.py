"""Chronicle entities on the primitives schema: Trace, Span and Envelope persistence.

Chronicle 1.0 writes the shared ``agentplane-primitives`` entities. This module stores them in the
same SQLite file as the legacy envelope table, keeping one row per entity:

* **Trace** (``traces``): registered idempotently with a client-supplied id; labels are written
  **once** (a repeat returns the existing trace and never changes labels). Labels live in the
  existing ``dims`` column so label search (``session_id`` ...) keeps working. A trace has no
  stored state: ``open`` while any of its spans is open, derived on read.
* **Span** (``spans``): upserted at open and at close. A closed span is immutable.
* **Envelope** (``envelopes``, new columns ``span_id`` and ``state``): one record, mutable until
  closed. An open write is updated by its close write; a write for a closed envelope is a dedupe.

A trace is created implicitly by the first span or envelope that names it, so registration may
be lazy (Chronicle ``trace_registration = lazy``).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agentplane_primitives.enums import State
from agentplane_primitives.models import Envelope, Span, Trace

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
  tenant_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  span_id TEXT NOT NULL,
  parent_envelope_id TEXT,
  service TEXT NOT NULL,
  state TEXT NOT NULL,
  status TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  body TEXT NOT NULL,
  PRIMARY KEY (tenant_id, trace_id, span_id)
);

CREATE INDEX IF NOT EXISTS idx_spans_parent
  ON spans(tenant_id, trace_id, parent_envelope_id);
"""

_ENVELOPE_COLUMNS = {"span_id": "TEXT", "state": "TEXT"}
_TRACE_COLUMNS = {"name": "TEXT"}


def migrate(db: sqlite3.Connection) -> None:
    """Create the span table and add the columns the primitives entities need."""
    db.executescript(SCHEMA)
    for table, wanted in (("envelopes", _ENVELOPE_COLUMNS), ("traces", _TRACE_COLUMNS)):
        have = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, kind in wanted.items():
            if column not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_envelopes_span ON envelopes(tenant_id, trace_id, span_id)"
    )
    db.commit()


def is_primitives_envelope(body: Any) -> bool:
    """True for a body in the primitives shape (it carries ``envelope_status``)."""
    return isinstance(body, dict) and "envelope_status" in body


def _dump(model: Trace | Span | Envelope) -> str:
    return model.model_dump_json(exclude_none=True)


def upsert_trace(db: sqlite3.Connection, tenant_id: str, trace: Trace) -> tuple[bool, dict[str, Any]]:
    """Register a trace. Returns ``(created, stored_trace)``; labels are never overwritten."""
    row = db.execute(
        "SELECT * FROM traces WHERE tenant_id=? AND trace_id=?", (tenant_id, trace.trace_id)
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO traces(tenant_id, trace_id, dims, name, started_at, ended_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                tenant_id,
                trace.trace_id,
                json.dumps(trace.metadata),
                trace.name,
                trace.started_at.isoformat() if trace.started_at else None,
                trace.ended_at.isoformat() if trace.ended_at else None,
            ),
        )
        db.commit()
        return True, {"trace_id": trace.trace_id, "name": trace.name, "metadata": trace.metadata}
    # Created implicitly by a span or envelope: the first explicit registration may name it.
    if row["name"] is None and trace.name:
        db.execute(
            "UPDATE traces SET name=? WHERE tenant_id=? AND trace_id=?",
            (trace.name, tenant_id, trace.trace_id),
        )
        db.commit()
    stored = json.loads(row["dims"] or "{}")
    return False, {"trace_id": trace.trace_id, "name": row["name"] or trace.name, "metadata": stored}


def _ensure_trace(db: sqlite3.Connection, tenant_id: str, trace_id: str, started: str | None) -> None:
    db.execute(
        "INSERT OR IGNORE INTO traces(tenant_id, trace_id, dims, started_at) VALUES (?,?,?,?)",
        (tenant_id, trace_id, "{}", started),
    )


def _widen_trace(
    db: sqlite3.Connection, tenant_id: str, trace_id: str, started: str | None, ended: str | None
) -> None:
    """Keep the earliest start and the latest end (spans arrive out of order)."""
    row = db.execute(
        "SELECT started_at, ended_at FROM traces WHERE tenant_id=? AND trace_id=?",
        (tenant_id, trace_id),
    ).fetchone()
    start = min(filter(None, (row["started_at"], started)), default=None)
    end = max(filter(None, (row["ended_at"], ended)), default=None)
    db.execute(
        "UPDATE traces SET started_at=?, ended_at=? WHERE tenant_id=? AND trace_id=?",
        (start, end, tenant_id, trace_id),
    )


def upsert_span(db: sqlite3.Connection, tenant_id: str, span: Span) -> bool:
    """Create or update a span. Returns False if the write was a no-op on a closed span."""
    started = span.started_at.isoformat()
    ended = span.ended_at.isoformat() if span.ended_at else None
    _ensure_trace(db, tenant_id, span.trace_id, started)
    existing = db.execute(
        "SELECT state FROM spans WHERE tenant_id=? AND trace_id=? AND span_id=?",
        (tenant_id, span.trace_id, span.span_id),
    ).fetchone()
    if existing is not None and existing["state"] == State.CLOSED.value:
        return False  # a closed span is immutable
    db.execute(
        "INSERT OR REPLACE INTO spans(tenant_id, trace_id, span_id, parent_envelope_id, service, "
        "state, status, started_at, ended_at, body) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            tenant_id,
            span.trace_id,
            span.span_id,
            span.parent_envelope_id,
            span.service,
            span.state.value,
            span.status.value if span.status else None,
            started,
            ended,
            _dump(span),
        ),
    )
    _widen_trace(db, tenant_id, span.trace_id, started, ended)
    db.commit()
    return True


def append_envelope(db: sqlite3.Connection, tenant_id: str, envelope: Envelope) -> bool:
    """Write an envelope. Returns True if it was stored or updated, False if deduped.

    A write for an envelope that is already closed is a dedupe (closed is immutable). An open
    record is replaced by its close write.
    """
    started = envelope.envelope_status.started_at.isoformat()
    ended = envelope.envelope_status.ended_at.isoformat() if envelope.envelope_status.ended_at else None
    existing = db.execute(
        "SELECT state FROM envelopes WHERE tenant_id=? AND trace_id=? AND envelope_id=?",
        (tenant_id, envelope.trace_id, envelope.envelope_id),
    ).fetchone()
    if existing is not None and existing["state"] == State.CLOSED.value:
        return False
    sequence = envelope.metadata.get("chronicle.sequence")
    _ensure_trace(db, tenant_id, envelope.trace_id, started)
    values = (
        envelope.span_id,
        envelope.envelope_status.state.value,
        sequence if isinstance(sequence, int) else None,
        envelope.parent_envelope_id,
        json.dumps({}),
        _dump(envelope),
        started,
        ended,
    )
    if existing is None:
        db.execute(
            "INSERT INTO envelopes(tenant_id, trace_id, envelope_id, span_id, state, sequence, "
            "parent_envelope_id, dims, body, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, envelope.trace_id, envelope.envelope_id, *values),
        )
    else:
        db.execute(
            "UPDATE envelopes SET span_id=?, state=?, sequence=?, parent_envelope_id=?, dims=?, "
            "body=?, started_at=?, ended_at=? WHERE tenant_id=? AND trace_id=? AND envelope_id=?",
            (*values, tenant_id, envelope.trace_id, envelope.envelope_id),
        )
    _widen_trace(db, tenant_id, envelope.trace_id, started, ended)
    db.commit()
    return True


def list_span_envelopes(
    db: sqlite3.Connection, tenant_id: str, trace_id: str, span_id: str
) -> list[dict[str, Any]]:
    """The envelopes of span ``span_id`` in trace ``trace_id`` (replay), in start order."""
    rows = db.execute(
        "SELECT body FROM envelopes WHERE tenant_id=? AND trace_id=? AND span_id=? "
        "ORDER BY sequence IS NULL, sequence, started_at",
        (tenant_id, trace_id, span_id),
    ).fetchall()
    return [json.loads(r["body"]) for r in rows]


def list_trace_spans(db: sqlite3.Connection, tenant_id: str, trace_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT body FROM spans WHERE tenant_id=? AND trace_id=? ORDER BY started_at",
        (tenant_id, trace_id),
    ).fetchall()
    return [json.loads(r["body"]) for r in rows]


def trace_state(spans: list[dict[str, Any]]) -> str:
    """A trace is open while any of its spans is open (it has no status of its own)."""
    if any(span.get("state") != State.CLOSED.value for span in spans):
        return State.OPEN.value
    return State.CLOSED.value if spans else State.OPEN.value
