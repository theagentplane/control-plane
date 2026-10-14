"""Governance: the configuration an agent running TokenOps pulls, and its CRUD."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

Make = Callable[..., TestClient]


def _seed_basic(client: TestClient) -> None:
    client.put("/v1/budgets", json={"id": "cap", "limit_micros": 2_000_000, "dimension": "trace"})
    client.put(
        "/v1/policies",
        json={"id": "p1", "template": "cost_budget", "budget_id": "cap", "data_scope": "global"},
    )
    client.put(
        "/v1/policies", json={"id": "p2", "template": "step_cap", "params": {"max_steps": 20}}
    )
    client.put(
        "/v1/policies",
        json={"id": "p3", "template": "tool_fix", "agent": "writer", "params": {"k": 3}},
    )


def test_an_agent_pulls_its_configuration(make_client: Make) -> None:
    client = make_client()
    _seed_basic(client)

    config = client.get("/v1/governance/researcher").json()["governance"]
    assert config["budgets"] == [
        {"id": "cap", "limit_micros": 2_000_000, "dimension": "trace", "period": "lifetime"}
    ]
    assert set(config["policies"]) == {"cost_budget", "step_cap"}  # tool_fix is for `writer`
    assert config["policies"]["cost_budget"] == {"budget": "cap", "data_scope": "global"}
    assert config["policies"]["step_cap"] == {"max_steps": 20, "data_scope": "local"}

    writer = client.get("/v1/governance/writer").json()["governance"]
    assert "tool_fix" in writer["policies"]


def test_the_pull_supports_conditional_requests(make_client: Make) -> None:
    client = make_client()
    _seed_basic(client)
    first = client.get("/v1/governance/researcher")
    etag = first.headers["etag"]

    assert (
        client.get("/v1/governance/researcher", headers={"If-None-Match": etag}).status_code == 304
    )

    client.put(
        "/v1/policies", json={"id": "p2", "template": "step_cap", "params": {"max_steps": 5}}
    )
    changed = client.get("/v1/governance/researcher", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag


def test_a_disabled_policy_is_not_pulled(make_client: Make) -> None:
    client = make_client()
    _seed_basic(client)
    client.put(
        "/v1/policies",
        json={"id": "p2", "template": "step_cap", "params": {}, "enabled": False},
    )
    config = client.get("/v1/governance/researcher").json()["governance"]
    assert "step_cap" not in config["policies"]


def test_a_segment_is_resolved_into_the_policy(make_client: Make) -> None:
    client = make_client()
    client.put(
        "/v1/segments",
        json={
            "id": "gold",
            "name": "Gold",
            "dimension": "tag",
            "tag_key": "tier",
            "match_value": "gold",
        },
    )
    client.put(
        "/v1/policies",
        json={"id": "p", "template": "step_cap", "segment_id": "gold", "params": {"max_steps": 3}},
    )
    params = client.get("/v1/governance/x").json()["governance"]["policies"]["step_cap"]
    assert (params["dimension"], params["tag_key"]) == ("tag", "tier")


def test_crud_round_trips_and_deletes(make_client: Make) -> None:
    client = make_client()
    _seed_basic(client)
    assert [b["id"] for b in client.get("/v1/budgets").json()] == ["cap"]
    assert client.get("/v1/budgets/cap").json()["limit_micros"] == 2_000_000
    assert client.get("/v1/budgets/nope").status_code == 404
    assert len(client.get("/v1/policies").json()) == 3
    assert client.get("/v1/policies/p1").json()["data_scope"] == "global"
    assert client.delete("/v1/policies/p1").json() == {"status": "deleted"}
    assert client.get("/v1/policies/p1").status_code == 404
    assert client.delete("/v1/budgets/cap").json() == {"status": "deleted"}
    assert client.get("/v1/segments").json() == []


def test_unknown_policy_template_fails_closed(make_client: Make) -> None:
    client = make_client()
    response = client.put("/v1/policies", json={"id": "p", "template": "no_such_policy"})
    assert response.status_code == 400
    assert "unknown policy template" in response.json()["detail"]


def test_bad_enum_values_are_rejected(make_client: Make) -> None:
    client = make_client()
    assert client.put("/v1/budgets", json={"id": "b", "dimension": "run"}).status_code == 422
    assert (
        client.put(
            "/v1/policies", json={"id": "p", "template": "step_cap", "data_scope": "everywhere"}
        ).status_code
        == 422
    )


def test_configuration_is_per_tenant(make_client: Make) -> None:
    client = make_client(api_keys="a:t1:read+admin,b:t2:read+admin")
    head_a, head_b = {"Authorization": "Bearer a"}, {"Authorization": "Bearer b"}
    client.put("/v1/budgets", headers=head_a, json={"id": "cap", "limit_micros": 1})
    assert [b["id"] for b in client.get("/v1/budgets", headers=head_a).json()] == ["cap"]
    assert client.get("/v1/budgets", headers=head_b).json() == []
    assert client.get("/v1/governance/x", headers=head_b).json()["governance"]["budgets"] == []


def test_writes_need_the_admin_scope(make_client: Make) -> None:
    client = make_client(api_keys="agent:t1:ingest+read")
    head = {"Authorization": "Bearer agent"}
    assert client.get("/v1/budgets", headers=head).status_code == 200
    assert client.put("/v1/budgets", headers=head, json={"id": "b"}).status_code == 403


def test_the_default_configuration_is_seeded_for_the_local_tenant(make_client: Make) -> None:
    client = make_client(seed=True)
    config: dict[str, Any] = client.get("/v1/governance/any-agent").json()["governance"]
    assert {b["id"] for b in config["budgets"]} == {"run_llm_cap"}
    assert {"cost_budget", "step_cap", "concurrency_cap"} <= set(config["policies"])
    assert config["budgets"][0]["dimension"] == "trace"  # a run is a trace


def test_reseed_and_clear(make_client: Make) -> None:
    client = make_client()
    _seed_basic(client)
    assert client.post("/v1/admin/governance/clear").json() == {"status": "cleared"}
    assert client.get("/v1/policies").json() == []
    assert client.post("/v1/admin/governance/reseed").json()["written"] > 0
    assert client.get("/v1/policies").json() != []
