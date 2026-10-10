"""5.13.0 — call events land on the run (live log + Elasticsearch events index); the brief carries the agents' talking points."""
import time

import pytest


@pytest.fixture()
def op_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main.config, "operator_key", "synthetic-operator-key-for-tests")
    with TestClient(main.app) as c:
        yield c, main, {"x-operator-key": "synthetic-operator-key-for-tests", "Origin": "http://testserver"}


def test_call_events_are_run_events_and_the_brief_has_talking_points(op_client, monkeypatch):
    c, main, h = op_client
    from backend.genome.sarvam import SarvamError

    async def offline(*a, **k):
        raise SarvamError("offline in tests")
    monkeypatch.setattr(main.app.state.genome.sarvam, "chat_json", offline)
    rid = c.post("/api/v5/runs", json={"seller_id": "syn-91000105"}).json()["run_id"]
    for _ in range(80):
        time.sleep(0.1)
        if c.get(f"/api/v5/runs/{rid}").json()["status"] != "running":
            break
    out = c.post(f"/api/v5/runs/{rid}/call-events", json={"kind": "switch", "label": "Persona switch · rush", "detail": "pace 1.0 → 1.12"}).json()
    assert out["seq"] > 0 and out["elastic"]["index"].endswith("-events")
    events = c.get(f"/api/v5/runs/{rid}").json()["events"]
    assert any(e["node"] == "call.switch" and e["label"] == "Persona switch · rush" for e in events)
    assert c.post(f"/api/v5/runs/{rid}/call-events", json={"kind": "hack", "label": "x"}).status_code == 422
    brief = c.get(f"/api/v5/runs/{rid}/brief").json()
    assert set(brief["talking_points"]) >= {"competitors", "payment_status", "buyer_activity", "market_snapshot", "product_overview", "call_reason"}
