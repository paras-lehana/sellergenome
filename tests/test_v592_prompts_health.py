"""5.9.2 — every prompt in Admin (visible, where it is used, editable) and a one-click health test of every API after a key change."""
import pathlib
import re

import pytest

from backend.genome import prompts


@pytest.fixture()
def bound(tmp_path):
    from backend.genome.store import GenomeStore
    store = GenomeStore(tmp_path / "v5.sqlite3")
    prompts.bind(store)
    yield store
    prompts.bind(None)


def test_every_prompt_is_catalogued_with_where_it_is_used_and_passes_its_own_checks():
    ids = [e["id"] for e in prompts.CATALOG]
    assert len(ids) == len(set(ids)) and len(ids) >= 25
    for e in prompts.CATALOG:
        assert e["used"] and e["where"] and e["applies"] and e["label"] and e["group"]
        text = prompts.default(e["id"])
        internal = e["where"].startswith("docs/") and not pathlib.Path(e["where"]).exists()   # the public copy has no docs/
        if not e.get("allow_empty") and not internal:
            assert text, e["id"]
            assert prompts.problems(e["id"], text) == [], e["id"]
    for needed in ("seller.bot_turn", "seller.simulated", "persona.generator", "persona.aggregator", "persona.locality", "buyer.requirement",
                   "buyer.compact", "buyer.rewrite", "sarvam.payal.prompt", "sarvam.payal.intro", "sarvam.vani.prompt", "sarvam.vani.intro"):
        assert needed in ids


def test_no_prompt_literal_left_outside_the_catalogue():
    """Every chat_json system prompt comes from prompts.text (a new inline prompt would be invisible in Admin)."""
    import pathlib
    for path in pathlib.Path("backend/genome").glob("*.py"):
        for m in re.finditer(r"chat_json\(\s*([^,\n]+)", path.read_text()):
            arg = m.group(1).strip()
            assert not arg.startswith(('"', "f\"", "'")), f"{path.name}: inline prompt {arg[:60]}"


def test_edit_is_used_by_the_call_site_then_reset(bound):
    from backend.genome import buyer
    original = prompts.text("buyer.rewrite")
    assert original == buyer.REWRITE_PROMPT
    item = prompts.save("buyer.rewrite", "Fix this reply: {fixes}. JSON {{\"reply\": str}}.")
    assert item["edited"] and prompts.text("buyer.rewrite").startswith("Fix this reply")
    assert prompts.text("buyer.rewrite").format(fixes="x") == 'Fix this reply: x. JSON {"reply": str}.'
    item = prompts.reset("buyer.rewrite")
    assert not item["edited"] and prompts.text("buyer.rewrite") == original and item["history"][0]["chars"] > 0
    # saving the default text is the same as a reset
    assert not prompts.save("buyer.rewrite", original)["edited"]


def test_edits_that_would_break_the_call_are_refused(bound):
    with pytest.raises(ValueError, match="Unknown placeholder"):
        prompts.save("seller.bot_turn", "You are {bot_name}. Return JSON {\"reply\": str}.")   # unescaped JSON braces
    with pytest.raises(ValueError, match="FACTORS_HERE"):
        prompts.save("persona.aggregator", "Build the persona.")
    with pytest.raises(ValueError, match="empty"):
        prompts.save("persona.locality", "   ")
    bad = prompts.default("sarvam.payal.prompt").replace("## Guardrails", "## Rules")
    with pytest.raises(ValueError, match="sections"):
        prompts.save("sarvam.payal.prompt", bad)
    with pytest.raises(ValueError, match="Unknown \\{\\{variables\\}\\}"):
        prompts.save("sarvam.vani.intro", "Namaste {{buyer_phone}}")
    with pytest.raises(ValueError, match="space"):
        prompts.save("sarvam.vani.prompt", prompts.default("sarvam.vani.prompt").replace("call tool:end_interaction", "call tool:end_interaction.", 1))


def test_sarvam_agents_send_the_edited_prompt(bound):
    from backend.genome import sarvam_voice as sv
    intro = "Namaste {{seller_name}} ji, Payal here from IndiaMART. You sell {{products}}, right?"
    prompts.save("sarvam.payal.intro", intro)
    assert prompts.text("sarvam.payal.intro") == intro and sv.INTRO != intro


@pytest.fixture()
def op_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPERATOR_API_KEY", "synthetic-operator-key-for-tests")
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main.config, "operator_key", "synthetic-operator-key-for-tests")   # read once at first import
    with TestClient(main.app) as c:
        yield c, {"x-operator-key": "synthetic-operator-key-for-tests", "Origin": "http://testserver"}
    prompts.bind(None)


def test_admin_prompt_routes(op_client):
    c, h = op_client
    assert c.get("/api/v5/admin/prompts").status_code == 401
    items = c.get("/api/v5/admin/prompts", headers=h).json()["items"]
    one = next(i for i in items if i["id"] == "persona.locality")
    assert one["used"] and one["text"] == one["default"] and not one["edited"]
    r = c.put("/api/v5/admin/prompts/persona.locality", json={"text": "Return JSON {locality, city}."}, headers=h)
    assert r.status_code == 200 and r.json()["edited"]
    assert c.put("/api/v5/admin/prompts/persona.aggregator", json={"text": "no token"}, headers=h).status_code == 422
    assert c.put("/api/v5/admin/prompts/nope", json={"text": "x"}, headers=h).status_code == 404
    assert not c.delete("/api/v5/admin/prompts/persona.locality", headers=h).json()["edited"]


def test_api_health_runs_every_live_api_once(op_client):
    c, h = op_client
    assert c.post("/api/v5/admin/apis/test-all", json={"seller_id": "syn-91000101"}).status_code == 401
    r = c.post("/api/v5/admin/apis/test-all", json={"seller_id": "syn-91000101"}, headers=h).json()
    assert r["total"] >= 8 and len(r["items"]) == r["total"] and r["ok"] >= 1
    for item in r["items"]:
        assert {"api", "label", "status", "ms", "key", "key_expired", "note"} <= set(item)
    by = {i["api"]: i for i in r["items"]}
    assert by["buyer.caller"]["status"] == "skipped" and "No live endpoint" in by["buyer.caller"]["note"]   # not a false error
    assert "buyer-call tool" not in by["packages.catalog"]["note"] and r["ok"] + r["errors"] + r["skipped"] <= r["total"]
    assert c.post("/api/v5/admin/apis/test-all", json={"seller_id": "bad id"}, headers=h).status_code in (404, 422)
