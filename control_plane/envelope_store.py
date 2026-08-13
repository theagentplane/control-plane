"""Chronicle envelope persistence (same SQLite file as governance store)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
  tenant_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  dims TEXT NOT NULL DEFAULT '{}',
  started_at TEXT,
  ended_at TEXT,
  PRIMARY KEY (tenant_id, trace_id)
);

CREATE TABLE IF NOT EXISTS envelopes (
  tenant_id TEXT NOT NULL,
  envelope_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  sequence INTEGER,
  parent_envelope_id TEXT,
  dims TEXT NOT NULL DEFAULT '{}',
  body TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  PRIMARY KEY (tenant_id, envelope_id)
);

CREATE INDEX IF NOT EXISTS idx_envelopes_trace
  ON envelopes(tenant_id, trace_id, sequence);
CREATE INDEX IF NOT EXISTS idx_envelopes_session
  ON envelopes(tenant_id, json_extract(dims, '$.session_id'));
CREATE INDEX IF NOT EXISTS idx_envelopes_message
  ON envelopes(tenant_id, json_extract(dims, '$.message_id'));
"""


def _validate_envelope(body: dict[str, Any]) -> None:
    if not isinstance(body, dict):
        raise ValueError("envelope must be a JSON object")
    if not str(body.get("envelope_id") or "").strip():
        raise ValueError("envelope_id is required")
    if not str(body.get("trace_id") or "").strip():
        raise ValueError("trace_id is required")


class EnvelopeStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def append(self, tenant_id: str, body: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Insert one envelope. Returns (deduped, ack_meta)."""
        _validate_envelope(body)
        eid = str(body["envelope_id"])
        tid = str(body["trace_id"])
        existing = self._db.execute(
            "SELECT 1 FROM envelopes WHERE tenant_id=? AND envelope_id=?",
            (tenant_id, eid),
        ).fetchone()
        if existing:
            return True, {"envelope_id": eid, "trace_id": tid, "deduped": True}

        dims = body.get("dims") or {}
        if not isinstance(dims, dict):
            dims = {}
        dims_json = json.dumps(dims)
        started = body.get("started_at")
        ended = body.get("timestamp") or body.get("ended_at")
        seq = body.get("sequence")
        parent = body.get("parent_envelope_id")

        self._db.execute(
            "INSERT INTO envelopes(tenant_id, envelope_id, trace_id, sequence, "
            "parent_envelope_id, dims, body, started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                tenant_id,
                eid,
                tid,
                seq,
                parent,
                dims_json,
                json.dumps(body),
                started,
                ended,
            ),
        )
        # Upsert trace summary (merge dims)
        row = self._db.execute(
            "SELECT dims, started_at, ended_at FROM traces WHERE tenant_id=? AND trace_id=?",
            (tenant_id, tid),
        ).fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO traces(tenant_id, trace_id, dims, started_at, ended_at) VALUES (?,?,?,?,?)",
                (tenant_id, tid, dims_json, started, ended),
            )
        else:
            merged = json.loads(row["dims"] or "{}")
            merged.update(dims)
            new_start = row["started_at"] or started
            new_end = ended or row["ended_at"]
            self._db.execute(
                "UPDATE traces SET dims=?, started_at=?, ended_at=? WHERE tenant_id=? AND trace_id=?",
                (json.dumps(merged), new_start, new_end, tenant_id, tid),
            )
        self._db.commit()
        return False, {"envelope_id": eid, "trace_id": tid, "deduped": False}

    def append_many(self, tenant_id: str, bodies: list[dict[str, Any]]) -> dict[str, Any]:
        accepted = 0
        deduped = 0
        ids: list[str] = []
        for body in bodies:
            was_dup, meta = self.append(tenant_id, body)
            ids.append(meta["envelope_id"])
            if was_dup:
                deduped += 1
            else:
                accepted += 1
        return {
            "accepted": accepted,
            "deduped": deduped,
            "envelope_ids": ids,
        }

    def get_envelope(self, tenant_id: str, envelope_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM envelopes WHERE tenant_id=? AND envelope_id=?",
            (tenant_id, envelope_id),
        ).fetchone()
        return json.loads(row["body"]) if row else None

    def list_trace_envelopes(self, tenant_id: str, trace_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT body FROM envelopes WHERE tenant_id=? AND trace_id=? "
            "ORDER BY sequence IS NULL, sequence, ended_at",
            (tenant_id, trace_id),
        ).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def get_trace(self, tenant_id: str, trace_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT * FROM traces WHERE tenant_id=? AND trace_id=?",
            (tenant_id, trace_id),
        ).fetchone()
        if row is None:
            return None
        count = self._db.execute(
            "SELECT COUNT(*) AS n FROM envelopes WHERE tenant_id=? AND trace_id=?",
            (tenant_id, trace_id),
        ).fetchone()["n"]
        return {
            "trace_id": row["trace_id"],
            "dims": json.loads(row["dims"] or "{}"),
            "span_count": count,
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
        }

    def find_traces(
        self,
        tenant_id: str,
        *,
        session_id: str | None = None,
        message_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id=?"]
        args: list[Any] = [tenant_id]
        if session_id:
            clauses.append("json_extract(dims, '$.session_id')=?")
            args.append(session_id)
        if message_id:
            clauses.append("json_extract(dims, '$.message_id')=?")
            args.append(message_id)
        if user_id:
            clauses.append("json_extract(dims, '$.user_id')=?")
            args.append(user_id)
        where = " AND ".join(clauses)
        rows = self._db.execute(
            f"SELECT * FROM traces WHERE {where} ORDER BY ended_at DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [
            {
                "trace_id": r["trace_id"],
                "dims": json.loads(r["dims"] or "{}"),
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
            }
            for r in rows
        ]
