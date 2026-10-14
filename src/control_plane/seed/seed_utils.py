"""Seed the governance configuration from a YAML file.

TokenOps agents pull their configuration from the control plane, so the configuration has to be
in the database first. The packaged ``default.yaml`` is the starting point; ``control-plane db
seed --file my.yaml`` loads your own. Seeding never overwrites a tenant that already has policy
instances unless ``reset`` is asked for.
"""

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from control_plane.models import BudgetSpec, PolicyInstance
from control_plane.repositories.governance_repository import GovernanceRepository


def load_governance_yaml(path: Path | str | None = None) -> dict[str, Any]:
    """The ``governance:`` block of ``path`` (the packaged default when omitted)."""
    if path is None:
        text = resources.files("control_plane.seed").joinpath("default.yaml").read_text("utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    block = data.get("governance")
    return block if isinstance(block, dict) else {}


def apply_governance(repo: GovernanceRepository, tenant_id: str, governance: dict[str, Any]) -> int:
    """Write the budgets and policies of ``governance`` for ``tenant_id``; returns rows written."""
    written = 0
    for spec in governance.get("budgets") or []:
        repo.upsert_budget(
            tenant_id,
            BudgetSpec(
                id=str(spec["id"]),
                limit_micros=spec.get("limit_micros"),
                dimension=spec.get("dimension", "trace"),
                tag_key=spec.get("tag_key"),
                period=spec.get("period", "lifetime"),
            ),
        )
        written += 1
    for template, raw_params in (governance.get("policies") or {}).items():
        params = dict(raw_params or {})
        budget_id = params.pop("budget", None)
        data_scope = str(params.pop("data_scope", "local") or "local")
        repo.upsert_policy(
            tenant_id,
            PolicyInstance(
                id=f"seed_{template}",
                template=str(template),
                params=params,
                budget_id=budget_id,
                data_scope=data_scope,
            ),
        )
        written += 1
    return written


def seed_governance(
    repo: GovernanceRepository,
    tenant_id: str,
    governance: dict[str, Any] | None = None,
    *,
    reset: bool = False,
) -> int:
    """Seed ``tenant_id``; skip (return 0) if it already has policies, unless ``reset``."""
    if reset:
        repo.clear(tenant_id)
    elif repo.list_policies(tenant_id):
        return 0
    return apply_governance(
        repo, tenant_id, governance if governance is not None else load_governance_yaml()
    )
