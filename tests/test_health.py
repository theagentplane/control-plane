def test_health(make_client):
    c = make_client()
    assert c.get("/health").json()["status"] == "ok"


def test_ready(make_client):
    c = make_client()
    assert c.get("/ready").json()["status"] == "ready"
