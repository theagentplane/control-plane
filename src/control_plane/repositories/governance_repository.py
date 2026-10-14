"""Governance configuration: segments, budgets and policy instances.

This is the configuration TokenOps pulls. ``config_for`` assembles exactly the dict
``build_governor`` consumes for one agent. Every row is tenant-scoped.
"""

from typing import Any

from sqlalchemy import Engine, and_, delete, select

from control_plane.db.tables import budgets, policy_instances, segments
from control_plane.models import POLICY_TEMPLATES, BudgetSpec, PolicyInstance, Segment
from control_plane.repositories.upsert_utils import upsert


class GovernanceRepository:
    """CRUD for the governance configuration, and the per-agent config assembly."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- segments ----------------------------------------------------------------------

    def upsert_segment(self, tenant_id: str, seg: Segment) -> Segment:
        with self._engine.begin() as conn:
            upsert(
                conn,
                segments,
                {"tenant_id": tenant_id, "id": seg.id},
                {
                    "name": seg.name,
                    "dimension": seg.dimension,
                    "tag_key": seg.tag_key,
                    "match_value": seg.match_value,
                },
            )
        return seg

    def get_segment(self, tenant_id: str, sid: str) -> Segment | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(segments).where(
                    and_(segments.c.tenant_id == tenant_id, segments.c.id == sid)
                )
            ).first()
        return _segment(row) if row else None

    def list_segments(self, tenant_id: str) -> list[Segment]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(segments).where(segments.c.tenant_id == tenant_id).order_by(segments.c.name)
            ).all()
        return [_segment(r) for r in rows]

    def delete_segment(self, tenant_id: str, sid: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                delete(segments).where(
                    and_(segments.c.tenant_id == tenant_id, segments.c.id == sid)
                )
            )

    # ---- budgets -----------------------------------------------------------------------

    def upsert_budget(self, tenant_id: str, spec: BudgetSpec) -> BudgetSpec:
        with self._engine.begin() as conn:
            upsert(
                conn,
                budgets,
                {"tenant_id": tenant_id, "id": spec.id},
                {
                    "limit_micros": spec.limit_micros,
                    "dimension": spec.dimension,
                    "tag_key": spec.tag_key,
                    "period": spec.period,
                },
            )
        return spec

    def get_budget(self, tenant_id: str, bid: str) -> BudgetSpec | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(budgets).where(and_(budgets.c.tenant_id == tenant_id, budgets.c.id == bid))
            ).first()
        return _budget(row) if row else None

    def list_budgets(self, tenant_id: str) -> list[BudgetSpec]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(budgets).where(budgets.c.tenant_id == tenant_id).order_by(budgets.c.id)
            ).all()
        return [_budget(r) for r in rows]

    def delete_budget(self, tenant_id: str, bid: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                delete(budgets).where(and_(budgets.c.tenant_id == tenant_id, budgets.c.id == bid))
            )

    # ---- policy instances --------------------------------------------------------------

    def upsert_policy(self, tenant_id: str, pi: PolicyInstance) -> PolicyInstance:
        if pi.template not in POLICY_TEMPLATES:  # fail closed, like build_governor
            raise ValueError(
                f"unknown policy template {pi.template!r}; known: {sorted(POLICY_TEMPLATES)}"
            )
        with self._engine.begin() as conn:
            upsert(
                conn,
                policy_instances,
                {"tenant_id": tenant_id, "id": pi.id},
                {
                    "template": pi.template,
                    "params": pi.params,
                    "agent": pi.agent,
                    "budget_id": pi.budget_id,
                    "segment_id": pi.segment_id,
                    "enabled": pi.enabled,
                    "data_scope": pi.data_scope or "local",
                },
            )
        return pi

    def get_policy(self, tenant_id: str, pid: str) -> PolicyInstance | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(policy_instances).where(
                    and_(policy_instances.c.tenant_id == tenant_id, policy_instances.c.id == pid)
                )
            ).first()
        return _policy(row) if row else None

    def list_policies(self, tenant_id: str) -> list[PolicyInstance]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(policy_instances)
                .where(policy_instances.c.tenant_id == tenant_id)
                .order_by(policy_instances.c.template, policy_instances.c.id)
            ).all()
        return [_policy(r) for r in rows]

    def delete_policy(self, tenant_id: str, pid: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                delete(policy_instances).where(
                    and_(policy_instances.c.tenant_id == tenant_id, policy_instances.c.id == pid)
                )
            )

    def clear(self, tenant_id: str) -> None:
        """Delete this tenant's segments, budgets and policy instances (the ledger is kept)."""
        with self._engine.begin() as conn:
            for table in (policy_instances, budgets, segments):
                conn.execute(delete(table).where(table.c.tenant_id == tenant_id))

    # ---- the pull ----------------------------------------------------------------------

    def config_for(self, tenant_id: str, agent: str) -> dict[str, Any]:
        """The exact dict ``build_governor`` consumes for ``agent``.

        Every enabled policy instance scoped to this agent (or to all agents), the budgets they
        reference, and an attached segment resolved into ``dimension`` and ``tag_key``. One
        instance per template (last wins), matching the governor's name-routed registration.
        """
        instances = [
            p
            for p in self.list_policies(tenant_id)
            if p.enabled and (p.agent is None or p.agent == agent)
        ]
        budget_ids = sorted({p.budget_id for p in instances if p.budget_id})
        found = [self.get_budget(tenant_id, bid) for bid in budget_ids]
        budget_dicts = [_budget_dict(b) for b in found if b is not None]
        policies: dict[str, dict[str, Any]] = {}
        for pi in instances:
            params = dict(pi.params)
            if pi.budget_id:
                params["budget"] = pi.budget_id
            if pi.segment_id:
                seg = self.get_segment(tenant_id, pi.segment_id)
                if seg is not None:
                    params.setdefault("dimension", seg.dimension)
                    if seg.tag_key:
                        params.setdefault("tag_key", seg.tag_key)
            params.setdefault("data_scope", pi.data_scope or "local")
            policies[pi.template] = params
        return {"governance": {"budgets": budget_dicts, "policies": policies}}


def _segment(row: Any) -> Segment:
    return Segment(
        id=row.id,
        name=row.name,
        dimension=row.dimension,
        tag_key=row.tag_key,
        match_value=row.match_value,
    )


def _budget(row: Any) -> BudgetSpec:
    return BudgetSpec(
        id=row.id,
        limit_micros=row.limit_micros,
        dimension=row.dimension,
        tag_key=row.tag_key,
        period=row.period,
    )


def _budget_dict(b: BudgetSpec) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": b.id,
        "limit_micros": b.limit_micros,
        "dimension": b.dimension,
        "period": b.period,
    }
    if b.tag_key:
        out["tag_key"] = b.tag_key
    return out


def _policy(row: Any) -> PolicyInstance:
    return PolicyInstance(
        id=row.id,
        template=row.template,
        params=dict(row.params or {}),
        agent=row.agent,
        budget_id=row.budget_id,
        segment_id=row.segment_id,
        enabled=bool(row.enabled),
        data_scope=row.data_scope or "local",
    )
