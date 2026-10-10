"""V5.8: the standalone persona page (PM) — Open persona runs, the page route, the identity it shows, and no invented
profile for a real GLID in search (owner: 23456789 showed up as a generated "Khan Traders")."""

import pytest

from backend.genome import brief


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    with TestClient(main.app) as c:
        yield c, main


def test_a_real_glid_never_gets_an_invented_profile_in_search(client, monkeypatch):
    c, main = client
    g = main.app.state.genome
    monkeypatch.setattr(g, "_approved", lambda glid: glid == "123456789")
    item = c.get("/api/v5/search?q=123456789").json()["items"][0]
    assert item["kind"] == "locked" and item["seller_id"] is None and "invented" in item["note"]
    assert "Traders" not in str(item) and item["company_name"] == "Real IndiaMART seller"
    other = c.get("/api/v5/search?q=87654321").json()["items"][0]
    assert other["kind"] == "generated"                                              # unknown numbers still make a demo seller


def test_display_seller_uses_the_identity_the_run_confirmed():
    run = {"seller": {"seller_id": "real-1", "glid": "1", "company_name": "Private seller · GLID 1", "contact_name": "", "synthetic": False},
           "contract": {"fields": {"company_name": {"value": "Test Lamps", "availability": "present", "origin": "live_api"},
                                   "contact_name": {"value": "Test Person", "availability": "present", "origin": "live_api"},
                                   "city": {"value": "Pune", "availability": "missing"}}}}
    s = brief.display_seller(run)
    assert s["company_name"] == "Test Lamps" and s["contact_name"] == "Test Person" and s["identity"] == "confirmed" and s.get("city") in (None, "")
    synthetic = {"seller": {"company_name": "Qureshi Wood Art", "synthetic": True}, "contract": {"fields": {}}}
    assert brief.display_seller(synthetic)["company_name"] == "Qureshi Wood Art"


def test_open_persona_runs_and_the_page_route(client):
    c, _ = client
    run = c.post("/api/v5/runs", json={"seller_id": "syn-91000105", "origin": "persona"}).json()
    assert run["run_id"].startswith("run-")
    assert c.get(f"/persona/{run['run_id']}").status_code == 200
    assert c.post("/api/v5/runs", json={"seller_id": "syn-91000105", "origin": "nowhere"}).status_code == 422


def test_bulk_key_save_needs_the_operator(client):
    c, _ = client
    assert c.post("/api/v5/admin/secrets/bulk", json={"value": "x", "names": ["IM_EMPLOYEE_AK"]}).status_code == 401
    assert c.post("/api/v5/admin/secrets/bulk", json={"value": "", "names": []}).status_code in (401, 422)
