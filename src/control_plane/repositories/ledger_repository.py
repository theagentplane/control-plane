"""The shared ledger: spend, in-flight counts, halts, and in-flight counts.

Agents in different processes share this state. Everything per run is keyed by ``trace_id``;
a run *is* a Chronicle trace, so there is no separate run table. A halt is not ledger state: it
is stored in the trace's ``attributes`` (primitives ``Trace.attributes``) under the
``tokenops.halted`` and ``tokenops.halt_reason`` keys.

- ``apply_events`` applies a batch **in array order, in one transaction**. Events are not
  deduplicated: a batch the client retries after a lost response is applied again.
- Counters are single atomic ``UPDATE ... SET n = n + delta`` statements, so concurrent writers
  on any database cannot lose an increment.
- ``precheck`` is the one consolidated read a pre-call pass needs.
"""

from typing import Any

from sqlalchemy import Connection, Engine, and_, case, func, select

from control_plane.db.engine_utils import write_transaction
from control_plane.db.tables import (
    ledger_inflight,
    ledger_spent,
    traces,
)
from control_plane.repositories.trace_repository import merge_attributes, read_attributes
from control_plane.repositories.upsert_utils import increment
from control_plane.time_utils import utc_now

#: Keys in ``Trace.attributes`` that carry the halt (namespaced for TokenOps).
HALTED_KEY = "tokenops.halted"
HALT_REASON_KEY = "tokenops.halt_reason"


class LedgerRepository:
    """Reads and writes the shared ledger."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- batched writes ----------------------------------------------------------------

    def apply_events(self, tenant_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply ``events`` atomically. Returns ``accepted``, ``totals`` and ``halted``.

        ``totals`` is the post-commit spend for every accumulator a ``spent_add`` in this batch
        names. A malformed event raises ``ValueError`` and nothing in the batch is applied.
        """
        accepted = 0
        touched: set[tuple[str, str, str]] = set()
        traces_seen: set[str] = set()
        with write_transaction(self._engine) as conn:
            for index, event in enumerate(events):
                kind = event.get("kind")
                if not isinstance(kind, str):
                    raise ValueError(f"event {index} missing kind")
                if kind == "spent_add":
                    for target in event.get("targets") or []:
                        touched.add(_target(target))
                if event.get("trace_id"):
                    traces_seen.add(str(event["trace_id"]))
                self._apply_one(conn, tenant_id, kind, event)
                accepted += 1
            totals = {
                f"{b}|{s}|{p}": self._spent(conn, tenant_id, b, s, p) for b, s, p in sorted(touched)
            }
            halted = any(
                read_attributes(conn, tenant_id, t).get(HALTED_KEY) for t in sorted(traces_seen)
            )
        return {
            "accepted": accepted,
            "atomic": True,
            "totals": totals,
            "halted": halted,
        }

    def _apply_one(
        self, conn: Connection, tenant_id: str, kind: Any, event: dict[str, Any]
    ) -> None:
        trace_id = str(event.get("trace_id") or "")
        if kind == "spent_add":
            delta = int(event.get("delta_micros", 0))
            for target in event.get("targets") or []:
                budget_id, segment_key, period = _target(target)
                increment(
                    conn,
                    ledger_spent,
                    {
                        "tenant_id": tenant_id,
                        "budget_id": budget_id,
                        "segment_key": segment_key,
                        "period": period,
                    },
                    "spent_micros",
                    delta,
                )
        elif kind == "admit":
            increment(
                conn,
                ledger_inflight,
                {"tenant_id": tenant_id, "segment_key": str(event["segment_key"])},
                "count",
                1,
            )
        elif kind == "complete":
            conn.execute(
                ledger_inflight.update()
                .where(
                    and_(
                        ledger_inflight.c.tenant_id == tenant_id,
                        ledger_inflight.c.segment_key == str(event["segment_key"]),
                    )
                )
                .values(
                    count=case((ledger_inflight.c.count > 0, ledger_inflight.c.count - 1), else_=0)
                )
            )
        elif kind == "halt_mark":
            _set_halt(conn, tenant_id, trace_id, str(event.get("reason") or ""))
        elif kind == "halt_clear":
            _clear_halt(conn, tenant_id, trace_id)
        else:
            raise ValueError(f"unknown event kind {kind!r}")

    # ---- halts -------------------------------------------------------------------------

    def mark_halted(self, tenant_id: str, trace_id: str, reason: str = "") -> None:
        with write_transaction(self._engine) as conn:
            _set_halt(conn, tenant_id, trace_id, reason)

    def clear_halt(self, tenant_id: str, trace_id: str) -> None:
        with write_transaction(self._engine) as conn:
            _clear_halt(conn, tenant_id, trace_id)

    def halt_status(self, tenant_id: str, trace_id: str) -> tuple[bool, str | None]:
        with self._engine.connect() as conn:
            attributes = read_attributes(conn, tenant_id, trace_id)
        return bool(attributes.get(HALTED_KEY)), attributes.get(HALT_REASON_KEY)

    # ---- reads -------------------------------------------------------------------------

    def precheck(
        self,
        tenant_id: str,
        trace_id: str,
        *,
        segment_keys: list[str] | None = None,
        budgets: list[dict[str, Any]] | None = None,
        want: list[str] | None = None,
    ) -> dict[str, Any]:
        """The consolidated pre-call read: halt, spend and in-flight in one call."""
        want = want or ["spent", "inflight", "halt"]
        out: dict[str, Any] = {"server_ts": utc_now().timestamp()}
        with self._engine.connect() as conn:
            if "halt" in want:
                attributes = read_attributes(conn, tenant_id, trace_id)
                out["halted"] = bool(attributes.get(HALTED_KEY))
                out["halt_reason"] = attributes.get(HALT_REASON_KEY)
            if "spent" in want:
                spent: dict[str, int] = {}
                for b in budgets or []:
                    budget_id = str(b["budget_id"])
                    segment = str(b["segment_key"])
                    period = str(b.get("period", "lifetime"))
                    spent[f"{budget_id}|{segment}|{period}"] = self._spent(
                        conn, tenant_id, budget_id, segment, period
                    )
                out["spent"] = spent
            if "inflight" in want:
                out["inflight"] = {
                    seg: self._inflight(conn, tenant_id, seg) for seg in segment_keys or []
                }
        return out

    def spent(self, tenant_id: str, budget_id: str, segment_key: str, period: str) -> int:
        with self._engine.connect() as conn:
            return self._spent(conn, tenant_id, budget_id, segment_key, period)

    def inflight(self, tenant_id: str, segment_key: str) -> int:
        with self._engine.connect() as conn:
            return self._inflight(conn, tenant_id, segment_key)

    def trace_summaries(self, tenant_id: str, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Ledger facts for the TokenOps run list: halt and spend per budget."""
        if not trace_ids:
            return {}
        keys = {f"trace:{t}": t for t in trace_ids}
        out: dict[str, dict[str, Any]] = {
            t: {"halted": False, "halt_reason": None, "spent": {}, "spent_micros": 0}
            for t in trace_ids
        }
        with self._engine.connect() as conn:
            for r in conn.execute(
                select(traces.c.trace_id, traces.c.attributes).where(
                    and_(traces.c.tenant_id == tenant_id, traces.c.trace_id.in_(trace_ids))
                )
            ):
                attributes = r.attributes or {}
                out[r.trace_id].update(
                    halted=bool(attributes.get(HALTED_KEY)),
                    halt_reason=attributes.get(HALT_REASON_KEY),
                )
            for r in conn.execute(
                select(ledger_spent).where(
                    and_(
                        ledger_spent.c.tenant_id == tenant_id,
                        ledger_spent.c.segment_key.in_(list(keys)),
                    )
                )
            ):
                entry = out[keys[r.segment_key]]
                entry["spent"][r.budget_id] = int(r.spent_micros)
                entry["spent_micros"] = max(entry["spent_micros"], int(r.spent_micros))
        return out

    def halted_trace_ids(self, tenant_id: str, limit: int = 100) -> list[str]:
        """Halted traces, most recently active first.

        The flag is matched with SQLAlchemy's portable JSON index expression, so no dialect SQL
        is involved; on a large table an index on the flag is a later optimisation.
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(traces.c.trace_id)
                .where(
                    and_(
                        traces.c.tenant_id == tenant_id,
                        traces.c.attributes[HALTED_KEY].as_boolean().is_(True),
                    )
                )
                .order_by(
                    func.coalesce(traces.c.ended_at, traces.c.started_at).desc(),
                    traces.c.trace_id,
                )
                .limit(limit)
            ).all()
        return [r.trace_id for r in rows]

    # ---- helpers -----------------------------------------------------------------------

    @staticmethod
    def _spent(
        conn: Connection, tenant_id: str, budget_id: str, segment_key: str, period: str
    ) -> int:
        row = conn.execute(
            select(func.coalesce(ledger_spent.c.spent_micros, 0)).where(
                and_(
                    ledger_spent.c.tenant_id == tenant_id,
                    ledger_spent.c.budget_id == budget_id,
                    ledger_spent.c.segment_key == segment_key,
                    ledger_spent.c.period == period,
                )
            )
        ).first()
        return int(row[0]) if row else 0

    @staticmethod
    def _inflight(conn: Connection, tenant_id: str, segment_key: str) -> int:
        row = conn.execute(
            select(ledger_inflight.c.count).where(
                and_(
                    ledger_inflight.c.tenant_id == tenant_id,
                    ledger_inflight.c.segment_key == segment_key,
                )
            )
        ).first()
        return int(row[0]) if row else 0


def _set_halt(conn: Connection, tenant_id: str, trace_id: str, reason: str) -> None:
    merge_attributes(conn, tenant_id, trace_id, {HALTED_KEY: True, HALT_REASON_KEY: reason or None})


def _clear_halt(conn: Connection, tenant_id: str, trace_id: str) -> None:
    merge_attributes(conn, tenant_id, trace_id, {}, (HALTED_KEY, HALT_REASON_KEY))


def _target(target: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(target["budget_id"]),
        str(target["segment_key"]),
        str(target.get("period", "lifetime")),
    )
