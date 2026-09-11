"""SQLite store — the control-plane persistence backend.

When ``TOKENOPS_CONTROL_PLANE_URL`` is set, agents and UIs use :class:`RemoteStore`
instead of opening this file directly. Only the control-plane server process should
construct :class:`SqliteStore` in that deployment mode.

One ``tokenops.db`` backs registration, governance config, run history, and **ledger
accumulators** (spend, inflight, halt). The key method is
:meth:`SqliteStore.governance_config_for`, which assembles exactly the dict
``control.config.build_governor`` already consumes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Sequence

from control_plane.templates import POLICY_TEMPLATES as _TEMPLATES
from control_plane.models import (
    ApiKeyView,
    BudgetSpec,
    PolicyInstance,
    RunAlreadyRegisteredError,
    RunNotRegisteredError,
    RunRecord,
    RunRegistration,
    Segment,
    parse_governance_mode,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, dimension TEXT NOT NULL,
  tag_key TEXT, match_value TEXT
);
CREATE TABLE IF NOT EXISTS budgets (
  id TEXT PRIMARY KEY, limit_micros INTEGER, dimension TEXT NOT NULL,
  tag_key TEXT, period TEXT NOT NULL DEFAULT 'lifetime'
);
CREATE TABLE IF NOT EXISTS policy_instances (
  id TEXT PRIMARY KEY, template TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}',
  agent TEXT, budget_id TEXT, segment_id TEXT, enabled INTEGER NOT NULL DEFAULT 1,
  data_scope TEXT NOT NULL DEFAULT 'local'
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, agent TEXT NOT NULL, status TEXT NOT NULL,
  parent_run TEXT, halt_reason TEXT, detector TEXT,
  cost_micros INTEGER NOT NULL DEFAULT 0, steps INTEGER NOT NULL DEFAULT 0,
  started_at REAL NOT NULL DEFAULT 0, ended_at REAL,
  task TEXT, dims TEXT NOT NULL DEFAULT '{}',
  governance_events TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS run_registrations (
  run_id TEXT PRIMARY KEY,
  intent TEXT NOT NULL DEFAULT '',
  user_dims TEXT NOT NULL DEFAULT '{}',
  mode TEXT NOT NULL DEFAULT 'enforce',
  registered_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  scopes TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'ui',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_spent (
  budget_id TEXT NOT NULL,
  segment_key TEXT NOT NULL,
  period TEXT NOT NULL DEFAULT 'lifetime',
  spent_micros INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (budget_id, segment_key, period)
);
CREATE TABLE IF NOT EXISTS ledger_inflight (
  segment_key TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger_halt (
  run_id TEXT PRIMARY KEY,
  halted INTEGER NOT NULL DEFAULT 0,
  halt_reason TEXT
);
CREATE TABLE IF NOT EXISTS ledger_events (
  tenant_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  applied_at REAL NOT NULL,
  PRIMARY KEY (tenant_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS run_state (
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  step_count INTEGER NOT NULL DEFAULT 0,
  window_json TEXT NOT NULL DEFAULT '[]',
  velocity_micros_per_step REAL NOT NULL DEFAULT 0,
  last_ts REAL,
  PRIMARY KEY (tenant_id, run_id)
);
"""

#: Bound on the per-run BoundaryStep ring kept in ``run_state.window_json``.
RUN_STATE_WINDOW = 64

#: Current schema version, tracked in ``PRAGMA user_version``.
#:   0 → pre-versioning (0.1.x). Legacy ad-hoc ALTERs run, then bumped to current.
#:   2 → 0.2.0: additive only. ``_SCHEMA`` (CREATE IF NOT EXISTS) covers the new
#:       ``ledger_events`` / ``run_state`` tables on any existing DB. No destructive
#:       change — ``run_registrations`` and the ``run-records`` write path stay
#:       (deprecated). The 0.3.0 fold (drop ``run_registrations``, etc.) will be v3.
SCHEMA_VERSION = 2


_log = logging.getLogger("control_plane.store")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _velocity_from_window(window: list[dict]) -> float:
    """micros/step over the ring: (newest cum_spent - oldest cum_spent) / span."""
    if len(window) < 2:
        return 0.0
    newest = window[-1].get("cum_spent_micros") or 0
    oldest = window[0].get("cum_spent_micros") or 0
    return (newest - oldest) / (len(window) - 1)


class SqliteStore:
    def __init__(self, path: str = "control_plane.db", *, auto_seed: bool = True) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)  # CREATE IF NOT EXISTS — fresh DB + new tables
        self._apply_migrations()
        self._db.commit()
        if auto_seed:
            self.seed_default_governance_if_empty()

    def _apply_migrations(self) -> None:
        """Forward-only schema migrations, tracked in ``PRAGMA user_version``.

        ``_SCHEMA`` already ran, so every table exists. This only adds columns / does
        destructive folds that ``CREATE IF NOT EXISTS`` cannot express.
        """
        version = self._db.execute("PRAGMA user_version").fetchone()[0]

        # Always run (all guarded / idempotent): covers a fresh DB (no-op — _SCHEMA
        # already made the columns), a pre-versioning 0.1 DB, and any intermediate.
        self._ensure_columns()

        # v2 (0.2.0) is otherwise purely additive — ledger_events / run_state come
        # from _SCHEMA.

        # if version < 3:  # 0.3.0 — fold run_registrations into runs, drop it, etc.
        #     self._migrate_v3_fold_registrations()

        if version != SCHEMA_VERSION:
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _ensure_columns(self) -> None:
        def _cols(table: str) -> set[str]:
            return {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}

        runs = _cols("runs")
        if "dims" not in runs:
            self._db.execute("ALTER TABLE runs ADD COLUMN dims TEXT NOT NULL DEFAULT '{}'")
        if "parent_span" not in runs:
            self._db.execute("ALTER TABLE runs ADD COLUMN parent_span TEXT")
        if "governance_events" not in runs:
            self._db.execute(
                "ALTER TABLE runs ADD COLUMN governance_events TEXT NOT NULL DEFAULT '[]'"
            )
        reg = _cols("run_registrations")
        if reg and "mode" not in reg:
            self._db.execute(
                "ALTER TABLE run_registrations ADD COLUMN mode TEXT NOT NULL DEFAULT 'enforce'"
            )
        if "data_scope" not in _cols("policy_instances"):
            self._db.execute(
                "ALTER TABLE policy_instances ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'local'"
            )

    def close(self) -> None:
        self._db.close()

    # ---- segments --------------------------------------------------------- #

    def upsert_segment(self, seg: Segment) -> Segment:
        self._db.execute(
            "REPLACE INTO segments(id, name, dimension, tag_key, match_value) VALUES (?,?,?,?,?)",
            (seg.id, seg.name, seg.dimension, seg.tag_key, seg.match_value),
        )
        self._db.commit()
        return seg

    def get_segment(self, sid: str) -> Segment | None:
        row = self._db.execute("SELECT * FROM segments WHERE id=?", (sid,)).fetchone()
        return _segment(row) if row else None

    def list_segments(self) -> list[Segment]:
        return [_segment(r) for r in self._db.execute("SELECT * FROM segments ORDER BY name")]

    def delete_segment(self, sid: str) -> None:
        self._db.execute("DELETE FROM segments WHERE id=?", (sid,))
        self._db.commit()

    # ---- budgets ---------------------------------------------------------- #

    def upsert_budget(self, b: BudgetSpec) -> BudgetSpec:
        self._db.execute(
            "REPLACE INTO budgets(id, limit_micros, dimension, tag_key, period) VALUES (?,?,?,?,?)",
            (b.id, b.limit_micros, b.dimension, b.tag_key, b.period),
        )
        self._db.commit()
        return b

    def get_budget(self, bid: str) -> BudgetSpec | None:
        row = self._db.execute("SELECT * FROM budgets WHERE id=?", (bid,)).fetchone()
        return _budget(row) if row else None

    def list_budgets(self) -> list[BudgetSpec]:
        return [_budget(r) for r in self._db.execute("SELECT * FROM budgets ORDER BY id")]

    def delete_budget(self, bid: str) -> None:
        self._db.execute("DELETE FROM budgets WHERE id=?", (bid,))
        self._db.commit()

    # ---- policy instances ------------------------------------------------- #

    def upsert_policy_instance(self, pi: PolicyInstance) -> PolicyInstance:
        if pi.template not in _TEMPLATES:  # fail closed — same rule as build_governor
            raise ValueError(
                f"unknown policy template {pi.template!r}; known: {sorted(_TEMPLATES)}"
            )
        self._db.execute(
            "REPLACE INTO policy_instances"
            "(id, template, params, agent, budget_id, segment_id, enabled, data_scope) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                pi.id,
                pi.template,
                json.dumps(pi.params),
                pi.agent,
                pi.budget_id,
                pi.segment_id,
                1 if pi.enabled else 0,
                pi.data_scope or "local",
            ),
        )
        self._db.commit()
        return pi

    def get_policy_instance(self, pid: str) -> PolicyInstance | None:
        row = self._db.execute("SELECT * FROM policy_instances WHERE id=?", (pid,)).fetchone()
        return _policy(row) if row else None

    def list_policy_instances(self) -> list[PolicyInstance]:
        return [
            _policy(r) for r in self._db.execute("SELECT * FROM policy_instances ORDER BY template")
        ]

    def delete_policy_instance(self, pid: str) -> None:
        self._db.execute("DELETE FROM policy_instances WHERE id=?", (pid,))
        self._db.commit()

    def seed_default_governance_if_empty(self, governance: dict | None = None) -> bool:
        """Load budgets + policies from config YAML when the store has none yet.

        Skipped when ``TOKENOPS_SKIP_GOVERNANCE_SEED=1`` or policy instances already
        exist (Admin edits are never overwritten).
        """
        if os.environ.get("TOKENOPS_SKIP_GOVERNANCE_SEED"):
            return False
        if self.list_policy_instances():
            return False
        return self._apply_governance_yaml(governance)

    def clear_all(self) -> None:
        """Delete every row (runs, registrations, governance, ledger). Schema is preserved."""
        for table in (
            "runs",
            "run_registrations",
            "policy_instances",
            "budgets",
            "segments",
            "ledger_spent",
            "ledger_inflight",
            "ledger_halt",
        ):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()

    def clear_governance(self) -> None:
        """Delete segments, budgets, and policy instances only."""
        for table in ("policy_instances", "budgets", "segments"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()

    def reseed_governance(self, governance: dict | None = None) -> bool:
        """Replace governance config from YAML (discards Admin edits)."""
        self.clear_governance()
        return self._apply_governance_yaml(governance)

    def _apply_governance_yaml(self, governance: dict | None = None) -> bool:
        if governance is None:
            from control_plane.governance_loader import load_governance_yaml

            governance = load_governance_yaml()
        if not governance:
            return False

        for spec in governance.get("budgets") or []:
            self.upsert_budget(
                BudgetSpec(
                    id=spec["id"],
                    limit_micros=spec.get("limit_micros"),
                    dimension=spec.get("dimension", "run"),
                    tag_key=spec.get("tag_key"),
                    period=spec.get("period", "lifetime"),
                )
            )

        for template, raw_params in (governance.get("policies") or {}).items():
            params = dict(raw_params or {})
            budget_id = params.pop("budget", None)
            data_scope = str(params.pop("data_scope", "local") or "local")
            self.upsert_policy_instance(
                PolicyInstance(
                    id=f"seed_{template}",
                    template=template,
                    params=params,
                    budget_id=budget_id,
                    data_scope=data_scope,
                )
            )
        return True

    # ---- run registration (attribution) ----------------------------------- #

    def register_run(self, reg: RunRegistration) -> RunRegistration:
        if self.get_run_registration(reg.run_id) is not None:
            raise RunAlreadyRegisteredError(f"run {reg.run_id!r} is already registered")
        self._db.execute(
            "INSERT INTO run_registrations(run_id, intent, user_dims, mode, registered_at) "
            "VALUES (?,?,?,?,?)",
            (reg.run_id, reg.intent, json.dumps(reg.user_dims), reg.mode.value, time.time()),
        )
        agent = reg.intent or "agent"
        self.create_run(
            RunRecord(
                run_id=reg.run_id,
                agent=agent,
                status="running",
                dims=dict(reg.user_dims),
                task=reg.intent or None,
            )
        )
        self._db.commit()
        # Return the persisted row so callers get registered_at (contract §8 —
        # the SDK binds this directly, no follow-up GET .../registration).
        return self.get_run_registration(reg.run_id) or reg

    def resolve_run(self, run_id: str) -> RunRegistration:
        reg = self.get_run_registration(run_id)
        if reg is None:
            raise RunNotRegisteredError(f"run {run_id!r} is not registered")
        return reg

    def get_run_registration(self, run_id: str) -> RunRegistration | None:
        row = self._db.execute(
            "SELECT * FROM run_registrations WHERE run_id=?", (run_id,)
        ).fetchone()
        return _registration(row) if row else None

    # ---- the bridge to build_governor ------------------------------------- #

    def governance_config_for(self, agent: str) -> dict:
        """Assemble the exact dict ``build_governor`` consumes for one agent.

        Includes every enabled policy instance scoped to this agent (or to all agents),
        the budgets they reference, and resolves an attached segment into dimension/tag_key
        for segment-scoped templates. One instance per template (last wins) — matches the
        Governor's name-routed registration.
        """
        instances = [
            pi
            for pi in self.list_policy_instances()
            if pi.enabled and (pi.agent is None or pi.agent == agent)
        ]
        budget_ids = {pi.budget_id for pi in instances if pi.budget_id}
        budgets = [_budget_dict(self.get_budget(bid)) for bid in budget_ids if self.get_budget(bid)]

        policies: dict[str, dict] = {}
        for pi in instances:
            params = dict(pi.params)
            if pi.budget_id:
                params["budget"] = pi.budget_id
            if pi.segment_id:
                seg = self.get_segment(pi.segment_id)
                if seg:
                    params.setdefault("dimension", seg.dimension)
                    if seg.tag_key:
                        params.setdefault("tag_key", seg.tag_key)
            params.setdefault("data_scope", pi.data_scope or "local")
            policies[pi.template] = params
        return {"governance": {"budgets": budgets, "policies": policies}}

    # ---- runs (dashboard) ------------------------------------------------- #

    def create_run(self, rec: RunRecord) -> RunRecord:
        if not rec.started_at:
            rec.started_at = time.time()
        self._db.execute(
            "REPLACE INTO runs(run_id, agent, status, parent_run, parent_span, halt_reason, detector, "
            "cost_micros, steps, started_at, ended_at, task, dims, governance_events) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.run_id,
                rec.agent,
                rec.status,
                rec.parent_run,
                rec.parent_span,
                rec.halt_reason,
                rec.detector,
                rec.cost_micros,
                rec.steps,
                rec.started_at,
                rec.ended_at,
                rec.task,
                json.dumps(rec.dims),
                json.dumps(rec.governance_events),
            ),
        )
        self._db.commit()
        return rec

    def update_run(self, run_id: str, **fields) -> None:
        # steps / cost_micros are derived (run_state / ledger_spent). A client-sent
        # value is dropped in 0.2.x (logged) and rejected in 0.3.0. Contract §8.
        for derived in ("steps", "cost_micros"):
            if fields.pop(derived, None) is not None:
                _log.warning(
                    "update_run(%s): ignoring client-sent %r — derived server-side "
                    "(deprecated in 0.2.x)",
                    run_id,
                    derived,
                )
        if not fields:
            return
        if "governance_events" in fields and not isinstance(fields["governance_events"], str):
            fields["governance_events"] = json.dumps(fields["governance_events"])
        if "dims" in fields and not isinstance(fields["dims"], str):
            fields["dims"] = json.dumps(fields["dims"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._db.execute(f"UPDATE runs SET {cols} WHERE run_id=?", (*fields.values(), run_id))
        self._db.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return _run(row) if row else None

    def list_runs(self, *, problematic_only: bool = False, limit: int = 200) -> list[RunRecord]:
        sql = "SELECT * FROM runs"
        if problematic_only:
            sql += " WHERE status IN ('halted','throttled','error')"
        sql += " ORDER BY started_at DESC LIMIT ?"
        return [_run(r) for r in self._db.execute(sql, (limit,))]

    def run_tag_keys(self, *, limit: int = 500) -> list[str]:
        """Distinct segment-tag keys seen across recent runs — the choices a dashboard can
        group runs by (in addition to ``agent``)."""
        keys: set[str] = set()
        for r in self.list_runs(limit=limit):
            keys.update(r.dims.keys())
        return sorted(keys)

    # ---- shared ledger (cross-process spend / inflight / halt) ------------ #

    def ledger_add_spent(
        self,
        budget_id: str,
        segment_key: str,
        period: str,
        delta: int,
    ) -> int:
        """Atomically increment a budget accumulator; return the new total."""
        self._db.execute(
            "INSERT INTO ledger_spent(budget_id, segment_key, period, spent_micros) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(budget_id, segment_key, period) "
            "DO UPDATE SET spent_micros = spent_micros + excluded.spent_micros",
            (budget_id, segment_key, period, delta),
        )
        row = self._db.execute(
            "SELECT spent_micros FROM ledger_spent "
            "WHERE budget_id=? AND segment_key=? AND period=?",
            (budget_id, segment_key, period),
        ).fetchone()
        self._db.commit()
        return int(row[0]) if row else 0

    def ledger_get_spent(self, budget_id: str, segment_key: str, period: str) -> int:
        row = self._db.execute(
            "SELECT spent_micros FROM ledger_spent "
            "WHERE budget_id=? AND segment_key=? AND period=?",
            (budget_id, segment_key, period),
        ).fetchone()
        return int(row[0]) if row else 0

    def ledger_admit(self, segment_key: str) -> int:
        self._db.execute(
            "INSERT INTO ledger_inflight(segment_key, count) VALUES (?, 1) "
            "ON CONFLICT(segment_key) DO UPDATE SET count = count + 1",
            (segment_key,),
        )
        row = self._db.execute(
            "SELECT count FROM ledger_inflight WHERE segment_key=?",
            (segment_key,),
        ).fetchone()
        self._db.commit()
        return int(row[0]) if row else 0

    def ledger_complete(self, segment_key: str) -> int:
        self._db.execute(
            "UPDATE ledger_inflight SET count = MAX(0, count - 1) WHERE segment_key=?",
            (segment_key,),
        )
        row = self._db.execute(
            "SELECT count FROM ledger_inflight WHERE segment_key=?",
            (segment_key,),
        ).fetchone()
        self._db.commit()
        return int(row[0]) if row else 0

    def ledger_inflight(self, segment_key: str) -> int:
        row = self._db.execute(
            "SELECT count FROM ledger_inflight WHERE segment_key=?",
            (segment_key,),
        ).fetchone()
        return int(row[0]) if row else 0

    def ledger_mark_halted(self, run_id: str, reason: str = "") -> None:
        self._db.execute(
            "INSERT INTO ledger_halt(run_id, halted, halt_reason) VALUES (?, 1, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET halted=1, "
            "halt_reason=COALESCE(excluded.halt_reason, ledger_halt.halt_reason)",
            (run_id, reason or None),
        )
        self._db.execute(
            "UPDATE runs SET status='halted', halt_reason=? WHERE run_id=?",
            (reason or None, run_id),
        )
        self._db.commit()

    def ledger_is_halted(self, run_id: str) -> bool:
        row = self._db.execute(
            "SELECT halted FROM ledger_halt WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return bool(row and row[0])

    def ledger_halt_reason(self, run_id: str) -> str | None:
        row = self._db.execute(
            "SELECT halt_reason FROM ledger_halt WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return row[0] if row else None

    def ledger_clear_halt(self, run_id: str) -> None:
        self._db.execute(
            "INSERT INTO ledger_halt(run_id, halted, halt_reason) VALUES (?, 0, NULL) "
            "ON CONFLICT(run_id) DO UPDATE SET halted=0, halt_reason=NULL",
            (run_id,),
        )
        self._db.commit()

    # ---- batched ledger events (contract §5) ----------------------------- #

    def apply_events(self, tenant_id: str, events: list[dict]) -> dict:
        """Apply a batch of LedgerEvents in array order, in one transaction.

        Returns ``{accepted, deduped, atomic, totals, halted}``. ``totals`` is the
        post-commit ``spent_micros`` for every ``(budget_id, segment_key, period)``
        touched by a ``spent_add`` in this batch. Idempotency: a replayed
        ``idempotency_key`` (per tenant) is counted in ``deduped`` and not re-applied.
        """
        accepted = 0
        deduped = 0
        touched: set[tuple[str, str, str]] = set()
        run_ids: set[str] = set()
        now = time.time()

        with self._lock:
            # sqlite3 (isolation_level="") auto-opens a transaction on the first DML;
            # commit()/rollback() below bound it. No explicit BEGIN (would nest).
            try:
                for i, ev in enumerate(events):
                    kind = ev.get("kind")
                    key = str(ev.get("idempotency_key") or "").strip()
                    if not key:
                        raise ValueError(f"event {i} missing idempotency_key")
                    # Record which accumulators this batch references *before* the dedup
                    # check, so a fully-deduped batch still gets current totals in the ack.
                    if kind == "spent_add":
                        for t in ev.get("targets") or []:
                            touched.add(
                                (
                                    str(t["budget_id"]),
                                    str(t["segment_key"]),
                                    str(t.get("period", "lifetime")),
                                )
                            )
                    if ev.get("run_id"):
                        run_ids.add(str(ev["run_id"]))
                    seen = self._db.execute(
                        "SELECT 1 FROM ledger_events WHERE tenant_id=? AND idempotency_key=?",
                        (tenant_id, key),
                    ).fetchone()
                    if seen:
                        deduped += 1
                        continue
                    self._apply_one(tenant_id, kind, ev, touched, run_ids)
                    self._db.execute(
                        "INSERT INTO ledger_events(tenant_id, idempotency_key, applied_at) "
                        "VALUES (?,?,?)",
                        (tenant_id, key, now),
                    )
                    accepted += 1
                self._db.commit()

                totals = {
                    f"{b}|{s}|{p}": self.ledger_get_spent(b, s, p) for (b, s, p) in sorted(touched)
                }
                halted = any(self.ledger_is_halted(r) for r in run_ids)
            except Exception:
                self._db.rollback()
                raise

        return {
            "accepted": accepted,
            "deduped": deduped,
            "atomic": True,
            "totals": totals,
            "halted": halted,
        }

    def _apply_one(
        self,
        tenant_id: str,
        kind: str | None,
        ev: dict,
        touched: set[tuple[str, str, str]],
        run_ids: set[str],
    ) -> None:
        """Apply a single event inside the open transaction. Raw SQL only — the public
        ``ledger_*`` helpers commit, which we must not do mid-batch."""
        run_id = str(ev.get("run_id") or "")
        if run_id:
            run_ids.add(run_id)

        if kind == "spent_add":
            delta = int(ev.get("delta_micros", 0))
            for t in ev.get("targets") or []:
                b = str(t["budget_id"])
                s = str(t["segment_key"])
                p = str(t.get("period", "lifetime"))
                self._db.execute(
                    "INSERT INTO ledger_spent(budget_id, segment_key, period, spent_micros) "
                    "VALUES (?,?,?,?) ON CONFLICT(budget_id, segment_key, period) "
                    "DO UPDATE SET spent_micros = spent_micros + excluded.spent_micros",
                    (b, s, p, delta),
                )
                touched.add((b, s, p))

        elif kind == "admit":
            self._db.execute(
                "INSERT INTO ledger_inflight(segment_key, count) VALUES (?, 1) "
                "ON CONFLICT(segment_key) DO UPDATE SET count = count + 1",
                (str(ev["segment_key"]),),
            )

        elif kind == "complete":
            self._db.execute(
                "UPDATE ledger_inflight SET count = MAX(0, count - 1) WHERE segment_key=?",
                (str(ev["segment_key"]),),
            )

        elif kind == "step":
            self._apply_step(tenant_id, run_id, ev)

        elif kind == "halt_mark":
            reason = ev.get("reason") or None
            self._db.execute(
                "INSERT INTO ledger_halt(run_id, halted, halt_reason) VALUES (?, 1, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET halted=1, "
                "halt_reason=COALESCE(excluded.halt_reason, ledger_halt.halt_reason)",
                (run_id, reason),
            )
            self._db.execute(
                "UPDATE runs SET status='halted', halt_reason=? WHERE run_id=?",
                (reason, run_id),
            )

        elif kind == "halt_clear":
            self._db.execute(
                "INSERT INTO ledger_halt(run_id, halted, halt_reason) VALUES (?, 0, NULL) "
                "ON CONFLICT(run_id) DO UPDATE SET halted=0, halt_reason=NULL",
                (run_id,),
            )

        else:
            raise ValueError(f"unknown event kind {kind!r}")

    def _apply_step(self, tenant_id: str, run_id: str, ev: dict) -> None:
        row = self._db.execute(
            "SELECT step_count, window_json FROM run_state WHERE tenant_id=? AND run_id=?",
            (tenant_id, run_id),
        ).fetchone()
        window = json.loads(row["window_json"]) if row else []
        step_entry = {
            k: ev.get(k)
            for k in (
                "agent",
                "seq",
                "node_type",
                "boundary_id",
                "cost_micros",
                "cum_spent_micros",
                "usage",
                "tags",
                "tool_signature",
                "result_hash",
                "ts",
            )
            if ev.get(k) is not None
        }
        window.append(step_entry)
        window = window[-RUN_STATE_WINDOW:]
        step_count = (row["step_count"] if row else 0) + 1
        velocity = _velocity_from_window(window)
        self._db.execute(
            "INSERT INTO run_state(tenant_id, run_id, step_count, window_json, "
            "velocity_micros_per_step, last_ts) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, run_id) DO UPDATE SET "
            "step_count=excluded.step_count, window_json=excluded.window_json, "
            "velocity_micros_per_step=excluded.velocity_micros_per_step, last_ts=excluded.last_ts",
            (tenant_id, run_id, step_count, json.dumps(window), velocity, ev.get("ts")),
        )

    def precheck(
        self,
        tenant_id: str,
        run_id: str,
        *,
        segment_keys: list[str] | None = None,
        budgets: list[dict] | None = None,
        want: list[str] | None = None,
    ) -> dict:
        """Consolidated pre_call read — contract §4. Returns halt + the requested
        spend / inflight / window slices in one shot."""
        want = want or ["spent", "inflight", "halt"]
        out: dict = {"server_ts": time.time()}

        if "halt" in want:
            out["halted"] = self.ledger_is_halted(run_id)
            out["halt_reason"] = self.ledger_halt_reason(run_id)

        if "spent" in want:
            spent: dict[str, int] = {}
            for b in budgets or []:
                bid = str(b["budget_id"])
                seg = str(b["segment_key"])
                per = str(b.get("period", "lifetime"))
                spent[f"{bid}|{seg}|{per}"] = self.ledger_get_spent(bid, seg, per)
            out["spent"] = spent

        if "inflight" in want:
            out["inflight"] = {seg: self.ledger_inflight(seg) for seg in (segment_keys or [])}

        if "window" in want:
            st = self.get_run_state(tenant_id, run_id)
            out["window"] = (
                {
                    "step_count": st["step_count"],
                    "recent": st["recent"],
                    "velocity_micros_per_step": st["velocity_micros_per_step"],
                }
                if st
                else {"step_count": 0, "recent": [], "velocity_micros_per_step": 0.0}
            )

        return out

    def get_run_state(self, tenant_id: str, run_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT step_count, window_json, velocity_micros_per_step, last_ts "
            "FROM run_state WHERE tenant_id=? AND run_id=?",
            (tenant_id, run_id),
        ).fetchone()
        if row is None:
            return None
        return {
            "step_count": row["step_count"],
            "recent": json.loads(row["window_json"]),
            "velocity_micros_per_step": row["velocity_micros_per_step"],
            "last_ts": row["last_ts"],
        }

    def create_api_key(
        self,
        *,
        name: str,
        tenant_id: str,
        scopes: Sequence[str],
        secret: str | None = None,
        source: str = "ui",
    ) -> ApiKeyView:
        token = secret or secrets.token_urlsafe(24)
        kid = new_id("key")
        prefix = token[:8]
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        self._db.execute(
            "INSERT INTO api_keys(id, name, key_hash, key_prefix, tenant_id, scopes, source, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (kid, name, digest, prefix, tenant_id, "+".join(scopes), source, now),
        )
        self._db.commit()
        return ApiKeyView(
            id=kid,
            name=name,
            key_prefix=prefix,
            tenant_id=tenant_id,
            scopes=list(scopes),
            source=source,
            created_at=now,
            secret=token,
        )

    def list_api_keys(self) -> list[ApiKeyView]:
        rows = self._db.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        return [
            ApiKeyView(
                id=r["id"],
                name=r["name"],
                key_prefix=r["key_prefix"],
                tenant_id=r["tenant_id"],
                scopes=str(r["scopes"]).split("+"),
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def lookup_api_key(self, token: str) -> ApiKeyView | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self._db.execute("SELECT * FROM api_keys WHERE key_hash=?", (digest,)).fetchone()
        if row is None:
            return None
        return ApiKeyView(
            id=row["id"],
            name=row["name"],
            key_prefix=row["key_prefix"],
            tenant_id=row["tenant_id"],
            scopes=str(row["scopes"]).split("+"),
            source=row["source"],
            created_at=row["created_at"],
        )

    def delete_api_key(self, kid: str) -> None:
        self._db.execute("DELETE FROM api_keys WHERE id=?", (kid,))
        self._db.commit()


# ---- row -> model ---------------------------------------------------------- #


def _registration(r: sqlite3.Row) -> RunRegistration:
    keys = r.keys()
    return RunRegistration(
        run_id=r["run_id"],
        intent=r["intent"] or "",
        user_dims=json.loads(r["user_dims"] or "{}"),
        mode=parse_governance_mode(r["mode"] if "mode" in keys else None),
        registered_at=float(r["registered_at"] or 0.0) if "registered_at" in keys else 0.0,
    )


def _segment(r: sqlite3.Row) -> Segment:
    return Segment(
        id=r["id"],
        name=r["name"],
        dimension=r["dimension"],
        tag_key=r["tag_key"],
        match_value=r["match_value"],
    )


def _budget(r: sqlite3.Row) -> BudgetSpec:
    return BudgetSpec(
        id=r["id"],
        limit_micros=r["limit_micros"],
        dimension=r["dimension"],
        tag_key=r["tag_key"],
        period=r["period"],
    )


def _budget_dict(b: BudgetSpec) -> dict:
    d = {"id": b.id, "limit_micros": b.limit_micros, "dimension": b.dimension, "period": b.period}
    if b.tag_key:
        d["tag_key"] = b.tag_key
    return d


def _policy(r: sqlite3.Row) -> PolicyInstance:
    keys = r.keys()
    return PolicyInstance(
        id=r["id"],
        template=r["template"],
        params=json.loads(r["params"]),
        agent=r["agent"],
        budget_id=r["budget_id"],
        segment_id=r["segment_id"],
        enabled=bool(r["enabled"]),
        data_scope=(r["data_scope"] if "data_scope" in keys else "local") or "local",
    )


def _run(r: sqlite3.Row) -> RunRecord:
    dims = json.loads((r["dims"] if "dims" in r.keys() else None) or "{}")
    keys = r.keys()
    gov_raw = r["governance_events"] if "governance_events" in keys else "[]"
    try:
        governance_events = json.loads(gov_raw or "[]")
    except json.JSONDecodeError:
        governance_events = []
    return RunRecord(
        run_id=r["run_id"],
        agent=r["agent"],
        status=r["status"],
        parent_run=r["parent_run"],
        parent_span=r["parent_span"] if "parent_span" in keys else None,
        halt_reason=r["halt_reason"],
        detector=r["detector"],
        cost_micros=r["cost_micros"],
        steps=r["steps"],
        started_at=r["started_at"],
        ended_at=r["ended_at"],
        task=r["task"],
        dims=dims,
        governance_events=governance_events,
    )


# Backward-compatible alias — tests and scripts may still construct Store(...) directly.
Store = SqliteStore
