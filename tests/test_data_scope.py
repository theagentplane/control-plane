"""policy_instances.data_scope — contract §10."""

from __future__ import annotations

import tempfile
from pathlib import Path

from control_plane.models import PolicyInstance
from control_plane.store import SqliteStore


def _store(td):
    return SqliteStore(str(Path(td) / "t.db"), auto_seed=False)


def test_default_is_local():
    with tempfile.TemporaryDirectory() as td:
        s = _store(td)
        try:
            s.upsert_policy_instance(PolicyInstance(id="p1", template="step_cap", params={"max_steps": 5}))
            assert s.get_policy_instance("p1").data_scope == "local"
        finally:
            s.close()


def test_roundtrips_global():
    with tempfile.TemporaryDirectory() as td:
        s = _store(td)
        try:
            s.upsert_policy_instance(
                PolicyInstance(id="p2", template="cost_budget", budget_id="b", data_scope="global")
            )
            assert s.get_policy_instance("p2").data_scope == "global"
            assert [p.data_scope for p in s.list_policy_instances()] == ["global"]
        finally:
            s.close()


def test_governance_config_for_emits_data_scope():
    with tempfile.TemporaryDirectory() as td:
        s = _store(td)
        try:
            s.upsert_policy_instance(
                PolicyInstance(id="p3", template="cost_budget", budget_id="b", data_scope="global")
            )
            s.upsert_policy_instance(
                PolicyInstance(id="p4", template="output_runaway", params={"repeats": 4})
            )
            cfg = s.governance_config_for("any")["governance"]["policies"]
            assert cfg["cost_budget"]["data_scope"] == "global"
            assert cfg["output_runaway"]["data_scope"] == "local"
        finally:
            s.close()


def test_yaml_seed_reads_data_scope():
    with tempfile.TemporaryDirectory() as td:
        s = _store(td)
        try:
            s.reseed_governance(
                {
                    "budgets": [{"id": "cap", "limit_micros": 1000, "dimension": "run"}],
                    "policies": {
                        "cost_budget": {"budget": "cap", "data_scope": "global"},
                        "step_cap": {"max_steps": 3},
                    },
                }
            )
            got = {p.template: p.data_scope for p in s.list_policy_instances()}
            assert got["cost_budget"] == "global"
            assert got["step_cap"] == "local"
            # data_scope must not leak into params
            assert "data_scope" not in s.get_policy_instance("seed_cost_budget").params
        finally:
            s.close()
