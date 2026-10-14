"""API keys: environment keys, stored keys, scopes and the open local mode."""

from collections.abc import Callable

from fastapi.testclient import TestClient

Make = Callable[..., TestClient]


def test_with_no_keys_the_service_is_open_for_the_local_tenant(make_client: Make) -> None:
    client = make_client()
    assert client.get("/v1/budgets").status_code == 200
    assert client.get("/v1/admin/keys").json()["auth_disabled"] is True


def test_an_environment_key_authenticates_and_is_scoped(make_client: Make) -> None:
    client = make_client(api_keys="agent:t1:ingest+read")
    assert client.get("/v1/budgets").status_code == 401
    assert client.get("/v1/budgets", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/budgets", headers={"Authorization": "Bearer agent"}).status_code == 200
    assert (
        client.get("/v1/admin/keys", headers={"Authorization": "Bearer agent"}).status_code == 403
    )


def test_a_stored_key_is_shown_once_and_is_scoped(make_client: Make) -> None:
    client = make_client()
    created = client.post(
        "/v1/admin/keys", json={"name": "ci", "tenant_id": "t9", "scopes": ["read"]}
    ).json()
    secret = created["secret"]
    assert secret
    assert created["key_prefix"] == secret[:8]

    # creating the first key closes the open local mode: the key now has to be presented
    assert client.get("/v1/admin/keys").status_code == 401
    head = {"Authorization": f"Bearer {secret}"}
    assert client.get("/v1/budgets", headers=head).status_code == 200
    assert client.put("/v1/budgets", headers=head, json={"id": "b"}).status_code == 403
    assert client.get("/v1/admin/keys", headers=head).status_code == 403  # read is not admin


def test_listing_never_shows_a_stored_secret(make_client: Make) -> None:
    client = make_client(api_keys="root:local:admin")
    root = {"Authorization": "Bearer root"}
    created = client.post("/v1/admin/keys", headers=root, json={"name": "ci"}).json()
    listed = client.get("/v1/admin/keys", headers=root).json()
    assert listed["auth_disabled"] is False
    stored = next(k for k in listed["keys"] if k["id"] == created["id"])
    assert stored["secret"] is None


def test_deleting_a_key_revokes_it(make_client: Make) -> None:
    client = make_client(api_keys="root:local:admin+read+ingest")
    root = {"Authorization": "Bearer root"}
    created = client.post(
        "/v1/admin/keys", headers=root, json={"name": "tmp", "scopes": ["read"]}
    ).json()
    token = {"Authorization": f"Bearer {created['secret']}"}
    assert client.get("/v1/budgets", headers=token).status_code == 200
    assert client.delete(f"/v1/admin/keys/{created['id']}", headers=root).status_code == 200
    assert client.get("/v1/budgets", headers=token).status_code == 401


def test_environment_keys_cannot_be_deleted_through_the_api(make_client: Make) -> None:
    client = make_client(api_keys="root:local:admin")
    response = client.delete("/v1/admin/keys/env:root", headers={"Authorization": "Bearer root"})
    assert response.status_code == 400


def test_unknown_scopes_are_rejected(make_client: Make) -> None:
    client = make_client()
    response = client.post("/v1/admin/keys", json={"name": "x", "scopes": ["root"]})
    assert response.status_code == 400
