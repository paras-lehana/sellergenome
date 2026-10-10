"""V5 Command Center: contract, mocks, registry policy, signals, switches, buyer guards, meetings, API access."""

import asyncio
import json
import time

import pytest

from backend.genome import aggregator, contract, floor, meetings, mock_upstream, roster, signals
from backend.genome.buyer import BuyerDesk, render
from backend.genome.conversation import ConversationEngine
from backend.genome.registry import ApiExecutor, ApiRegistry, host_allowed
from backend.genome.settings import GenomeSettings, SettingsConflict
from backend.genome.store import GenomeStore


class NoElastic:
    enabled = False
    status = "not_configured"

    def alias(self, kind):
        return f"sellergenome-v5-{kind}"

    async def cache_get(self, key):
        return None, {}, 0.0

    def queue(self, *a, **k):
        pass


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    settings = GenomeSettings()
    store = GenomeStore(tmp_path / "v5.sqlite3")
    registry = ApiRegistry(settings, tmp_path / "config")
    executor = ApiExecutor(settings, registry, NoElastic(), store, approved=lambda g: g == "12345")
    monkeypatch.setattr(settings, "values", {**settings.values, "runs": {**settings.values["runs"], "mock_latency_ms": [0, 1]}})
    return settings, store, registry, executor


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# --- roster and mocks ---------------------------------------------------------------------------

def test_heroes_and_generated_sellers_are_deterministic():
    assert len(roster.heroes()) == 14
    a, b = roster.profile("gen-555123"), roster.profile("gen-555123")
    assert a == b and a["synthetic"] and a["glid"] == "555123"
    assert roster.profile("syn-91000101")["company_name"] == "Qureshi Wood Art"
    with pytest.raises(ValueError):
        roster.glid_of("syn-abc")
    assert roster.is_public("syn-1") and roster.is_public("gen-1") and not roster.is_public("real-1")


def test_mocks_follow_production_shapes():
    p = roster.profile("syn-91000101")
    detail = mock_upstream.company_detail(p)
    assert detail["DATA"]["COMPANYDETAIL"]["DIR_SEARCH_COMPANY"] == "Qureshi Wood Art"
    assert detail["DATA"]["PRDSERV"][0]["ISQ"]
    ins = mock_upstream.insights(p)["Data"]["Insights"]
    assert [i["category"] for i in ins["insights"]] == contract.INSIGHT_CATEGORIES
    assert ins["doubts"] and "seller_summary" in ins
    assert "X" in detail["DATA"]["COMPANYDETAIL"]["MOBILE"]  # never a dialable synthetic number


# --- contract -----------------------------------------------------------------------------------

def _results(p, **extra):
    base = {
        "company.detail": {"status": "ok", "origin": "mock", "response": mock_upstream.company_detail(p), "retrieved_at": "t"},
        "context.global": {"status": "ok", "origin": "mock", "response": mock_upstream.global_context(p), "retrieved_at": "t"},
        "context.insights": {"status": "ok", "origin": "mock", "response": mock_upstream.insights(p), "retrieved_at": "t"},
    }
    base.update(extra)
    return base


def test_contract_has_29_fields_with_origins_and_classification():
    p = roster.profile("syn-91000101")
    out = contract.assemble(p, _results(p), lead_id="run-1")
    assert len(out["fields"]) == 29
    assert out["fields"]["company_name"]["origin"] == "mock"
    assert out["fields"]["company_name"]["pointer"] == "/DATA/COMPANYDETAIL/DIR_SEARCH_COMPANY"
    assert out["fields"]["lead_id"]["origin"] == "default"
    assert out["fields"]["custtypeweight"]["value"] == 1199 and out["classification"] == "free"
    assert out["fields"]["personalise"]["value"] == "true"
    assert len(out["insights"]) == 7 and out["doubts"]


def test_local_dataset_wins_over_mock_and_blank_is_distinct():
    p = roster.profile("syn-91000101")
    dataset = {"status": "ok", "origin": "local_db", "retrieved_at": "t",
               "response": {"row": {"seller_city": "Pali", "seller_state": "", "business_type": "Trader"}}}
    out = contract.assemble(p, _results(p, **{"dataset.seller": dataset}), lead_id="r")
    assert out["fields"]["city"]["value"] == "Pali" and out["fields"]["city"]["origin"] == "local_db"
    # Dataset state is blank → the next source (mock company detail) answers.
    assert out["fields"]["state"]["origin"] == "mock"
    assert contract.classify(1000) == "unknown" and contract.classify(None) == "unknown" and contract.classify(999) == "paid"


def test_evidence_items_never_include_contact_pii():
    p = roster.profile("syn-91000101")
    items = contract.evidence_items(contract.assemble(p, _results(p), lead_id="r"))
    text = json.dumps(items)
    assert "Imran Qureshi" not in text and "9XXXXX" not in text
    assert any(i["id"].startswith("i:") for i in items) and any(i["id"].startswith("d:") for i in items)


# --- registry and executor policy ---------------------------------------------------------------

def test_host_allow_list_blocks_unknown_hosts():
    assert host_allowed("http://company.imutils.com/x")
    assert host_allowed("https://redash.intermesh.net/api")
    assert not host_allowed("http://169.254.169.254/latest/meta-data")
    assert not host_allowed("http://evil.example.com/")
    assert not host_allowed("file:///etc/passwd")


def test_registry_edits_are_validated_and_revisioned(env):
    settings, store, registry, _ = env
    with pytest.raises(ValueError):
        registry.update("company.detail", {"url": "http://evil.example.com/"}, registry.revision)
    with pytest.raises(ValueError):
        registry.update("company.detail", {"mode": "sometimes"}, registry.revision)
    updated = registry.update("company.detail", {"timeout_seconds": 5, "mode": "off"}, registry.revision)
    assert updated["timeout_seconds"] == 5 and updated["mode"] == "off" and "mode" in updated["overridden"]
    with pytest.raises(ValueError):
        registry.update("company.detail", {"mode": "mock"}, 0)  # stale revision
    with pytest.raises(ValueError):
        registry.update("redash.hotlead", {"url": "https://redash.intermesh.net/other"}, registry.revision)  # fixed by policy


def test_executor_policy_for_synthetic_private_and_off(env):
    settings, store, registry, executor = env
    hero = roster.profile("syn-91000101")
    r = run(executor.call("company.alias", hero))
    assert r["origin"] == "mock" and r["status"] == "ok" and "[server-held]" in r["request"]["url"]
    assert run(executor.call("dataset.seller", hero))["status"] == "skipped"
    registry.update("context.global", {"mode": "live"}, registry.revision)
    synthetic_live = run(executor.call("context.global", hero))
    assert synthetic_live["origin"] == "mock" and "never sent upstream" in synthetic_live["note"]
    private = roster.profile("real-777", {"city": "Pune"})
    assert run(executor.call("context.global", private, operator=False))["status"] == "skipped"  # needs PM
    assert run(executor.call("context.global", private, operator=True))["status"] == "skipped"   # not approved
    approved = roster.profile("real-12345")
    registry.update("context.insights", {"mode": "live"}, registry.revision)
    missing = run(executor.call("context.insights", approved, operator=True))
    assert missing["status"] == "error" and missing["error"] == "not_configured"  # never silently mocked
    registry.update("company.alias", {"mode": "off"}, registry.revision)
    assert run(executor.call("company.alias", hero))["status"] == "skipped"
    assert store.hits(limit=50)  # every hit is logged


# --- signals and switches -----------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("jaldi boliye, time nahi hai", "rush"),
    ("samajh nahi aaya, phir se boliye", "confusion"),
    ("roz call aate hain, bakwas hai", "frustration"),
    ("achha laga, aur batao kitne leads", "interest"),
    ("please don't call me again", "stop"),
    ("are you a robot?", "bot_question"),
])
def test_signal_lexicon(text, expected):
    assert expected in [d["signal"] for d in signals.detect(text, "hi-IN")]


def test_language_switch_explicit_negated_and_hinglish():
    first = signals.detect("Hindi mein boliye please", "en-IN")[0]
    assert first["signal"] == "language_switch" and first["explicit"] and first["target_language"] == "hi-IN"
    assert not [d for d in signals.detect("Hindi nahi, English chalega", "en-IN") if d.get("target_language") == "hi-IN" and d["explicit"]]
    assert not [d for d in signals.detect("kitne leads milenge? meeting rakh do kal", "hi-IN") if d["signal"] == "language_switch"]
    assert signals.script_language("నాకు తెలుగులో చెప్పండి దయచేసి") == "te-IN"


def _call(settings, store):
    engine = ConversationEngine(settings, store, sarvam=None)
    run_doc = {"id": "run-x", "seller_id": "syn-91000103", "glid": "91000103", "persona": {"factors": {"pace": {"value": 1.0}, "language": {"value": "hi-IN"}}}}
    return engine, engine.start(run_doc, [], operator=False)


def test_switch_cooldown_is_per_factor_and_explicit_bypasses(env):
    settings, store, _, _ = env
    engine, call = _call(settings, store)
    first = engine._switch(call, signals.detect("jaldi boliye", "hi-IN"), 1)
    assert first[0]["applied"] and call["delivery"]["pace"] > 1.0
    engine._update_levels(call, [{"signal": "confusion", "confidence": 0.75}])
    held = engine._switch(call, [{"signal": "confusion", "confidence": 0.75, "method": "text_lexicon"}], 2)
    assert held[0]["held_by_cooldown"] and not held[0]["applied"]  # pace was just changed
    lang = engine._switch(call, signals.detect("English mein baat karo", "hi-IN"), 2)
    assert lang[0]["applied"] and call["delivery"]["language"] == "en-IN"
    call["levels"]["confusion"] = 1.3  # confusion seen again → strong enough to override cooldown
    again = engine._switch(call, [{"signal": "confusion", "confidence": 0.75, "method": "text_lexicon"}], 3)
    assert again[0]["applied"] and again[0]["override"]


# --- buyer --------------------------------------------------------------------------------------

def test_render_handles_conditions_and_placeholders():
    tpl = "Hi ﻿ bot\\_name ﻿.{% if ast\\_seller\\_pns \\!= \"\" %} SELLER{% else %} LEAD{% endif %}{%if buyer\\_name==\"\"%} ASKNAME{%endif%} quantity stays"
    assert render(tpl, {"bot_name": "Payal", "ast_seller_pns": "", "buyer_name": ""}) == "Hi Payal. LEAD ASKNAME quantity stays"
    assert "SELLER" in render(tpl, {"bot_name": "P", "ast_seller_pns": "080", "buyer_name": "R"})


def test_buyer_guard_enforces_limits_preconditions_and_consent(env):
    settings, store, _, _ = env
    desk = BuyerDesk(settings, store, sarvam=None)
    call = desk.start({"call": {"flow": "new_requirement", "language": "hi-IN"}, "turns": [{"who": "bot", "text": "Namaste"}]}, False)
    call["attempts"]["quantity"] = 3
    call["transcript"].append({"who": "buyer", "text": "pata nahi"})
    out = desk._apply(call, {"reply": "Kitne chahiye?", "asked_field": "quantity", "tool": "seller_by_city"}, {"units": [], "specs": {}})
    assert any("quantity" in n for n in out["notes"]) and "quantity" in call["closed"]
    assert out["tool"] is None  # city/state unknown
    call["lead"].update(city="Pune", state="Maharashtra")
    call["available_seller"] = {"name": "Pune Pump House", "city": "Pune"}
    call["transcript"] += [{"who": "bot", "text": "Kya main connect kar doon?"}, {"who": "buyer", "text": "haan kar do"}]
    out = desk._apply(call, {"reply": "ok"}, {"units": [], "specs": {}})
    assert call["consent"] and out["tool"] == "assisted_buy"


def test_greeting_is_not_transfer_consent(env):
    settings, store, _, _ = env
    desk = BuyerDesk(settings, store, sarvam=None)
    call = desk.start({"call": {"flow": "new_requirement"}, "turns": []}, False)
    call["available_seller"] = {"name": "X", "city": "Y"}
    call["transcript"] += [{"who": "bot", "text": "Aap kis shehar se hain?"}, {"who": "buyer", "text": "haan ji namaste"}]
    desk._apply(call, {"reply": "ok"}, {"units": [], "specs": {}})
    assert not call["consent"]  # "haan" answered a different question


# --- meetings, aggregator -------------------------------------------------------------------------

def test_meeting_links_ics_and_validation(env):
    settings = env[0]
    run_doc = {"id": "run-m", "seller_id": "syn-1", "seller": {"company_name": "A", "city": "B", "glid": "1"},
               "contract": {"addresses": [{"kind": "gst", "label": "GST address", "text": "12 Road", "origin": "mock"}]},
               "persona": {"summary": "s", "factors": {}}, "teleprompter": {}}
    online = meetings.plan(settings, run_doc, None, "online", None, None, "2099-01-02T10:00")
    assert online["meeting_link"].startswith("https://meet.jit.si/") and "calendar.google.com" in online["google_calendar_link"]
    assert "BEGIN:VEVENT" in meetings.ics(online)
    visit = meetings.plan(settings, run_doc, None, "in_person", "gst", None, "2099-01-02T10:00")
    assert visit["location"] == "12 Road" and visit["address_origin"] == "mock"
    with pytest.raises(ValueError):
        meetings.plan(settings, run_doc, None, "in_person", None, "", "2099-01-02T10:00")
    with pytest.raises(ValueError):
        meetings.plan(settings, run_doc, None, "online", None, None, "2001-01-02T10:00")


def test_aggregator_validation_keeps_only_grounded_content(env):
    settings = env[0]
    evidence = [{"id": "i:objection_or_blocker", "text": "cash", "api": "context.insights", "pointer": "/x", "origin": "mock"}]
    raw = {"summary": "s", "archetype": "price_sensitive", "tags": [{"tag": "Cash Flow!", "score": 90, "evidence": ["i:objection_or_blocker"]}],
           "factors": {"language": {"value": "hi-IN", "score": 80, "strength": "strong", "evidence": []},
                       "pace": {"value": 3, "score": 60, "evidence": ["i:objection_or_blocker"]},
                       "formality": {"value": "formal"}, "warmth": "high", "empathy": {"value": "medium"},
                       "voice": {"value": "not-a-voice"}, "hinglish_mix": {"value": "high"}, "sentence_length": {"value": "short"},
                       "primary_objection": {"value": "price", "evidence": ["i:objection_or_blocker"]}},
           "novel_factors": [{"name": "Funding Constraint", "value": "after 10th", "evidence": ["i:objection_or_blocker"]},
                             {"name": "made_up", "value": "x", "evidence": ["nope"]}]}
    out = aggregator.validate(raw, evidence, settings, {"archetype": "price_sensitive"})
    assert out["factors"]["pace"]["value"] == 1.25  # clamped
    assert out["factors"]["language"]["strength"] == "guess"  # no citation → guess
    assert out["factors"]["warmth"]["strength"] == "guess"  # bare value accepted as a guess
    assert "voice" not in out["factors"]  # unknown speaker rejected
    assert [n["name"] for n in out["novel_factors"]] == ["funding_constraint"]  # uncited novel claim dropped
    assert out["tags"][0]["tag"] == "cash-flow"


def test_settings_update_rollback_and_lock(tmp_path):
    s = GenomeSettings(tmp_path)
    with pytest.raises(ValueError):
        s.update({"llm.organization_id": "x"}, 0)
    with pytest.raises(ValueError):
        s.update({"runs.max_buyers": "five"}, 0)
    s.update({"runs.max_buyers": 3}, 0)
    assert s.get("runs.max_buyers") == 3
    with pytest.raises(SettingsConflict):
        s.update({"runs.max_buyers": 2}, 0)
    s.rollback(0, s.revision)
    assert s.get("runs.max_buyers") == 5
    status = s.set_secret("VOICE_PLUGIN_KEY", "abc123")
    assert status["configured"] and status["fingerprint"] and "abc123" not in json.dumps(status)


def test_floor_is_deterministic_and_labelled(env):
    settings, store, _, _ = env
    a = floor.seller_floor(settings, store)
    assert a["simulation"] and len(a["tiles"]) == settings.get("floor.seller_lanes")
    assert len({t["seller"]["seller_id"] for t in a["tiles"]}) == len(a["tiles"])
    assert all("virtual assistant" in t["turns"][0]["text"] for t in a["tiles"] if t["turns"][0]["who"] == "bot")
    assert floor.buyer_floor(settings)["tiles"]


# --- API access control -------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    with TestClient(main.app) as c:
        yield c, main


def test_api_access_rules(client, monkeypatch):
    c, main = client
    assert c.get("/api/v5/floor").status_code == 200
    assert c.post("/api/v5/runs", json={"seller_id": "real-123"}).status_code == 401
    assert c.get("/api/v5/admin/apis").status_code == 401
    assert c.patch("/api/v5/admin/apis/company.detail", json={"changes": {"mode": "off"}, "expected_revision": 0}).status_code == 401
    assert c.post("/api/v5/persona/syn-91000101/apply", json={"factor": "pace", "value": 1}).status_code == 401
    assert c.post("/api/v5/voice/sessions", json={"glid": "91000101"}).status_code == 401
    item = c.get("/api/v5/search?q=87654321").json()["items"][0]
    assert item["kind"] == "generated" and item["seller_id"] == "gen-87654321"
    for page in ("/", "/buyer", "/genome", "/sarvam", "/admin", "/v4", "/v4/buyer", "/v4/admin", "/operations", "/studio", "/walkthrough", "/analysis"):
        assert c.get(page).status_code == 200, page
    assert c.get("/v4/unknown").status_code == 404


def test_api_run_falls_back_to_labelled_rules_without_sarvam(client, monkeypatch):
    c, main = client
    from backend.genome.sarvam import SarvamError

    async def fail(*a, **k):
        raise SarvamError("offline in tests")
    monkeypatch.setattr(main.app.state.genome.sarvam, "chat_json", fail)
    rid = c.post("/api/v5/runs", json={"seller_id": "syn-91000105"}).json()["run_id"]
    for _ in range(80):
        time.sleep(0.1)
        doc = c.get(f"/api/v5/runs/{rid}").json()
        if doc["status"] != "running":
            break
    assert doc["status"] == "completed" and doc["persona"]["origin"] == "rules"
    assert any(e["node"] == "sarvam.aggregate" and e["status"] == "fallback" for e in doc["events"])
    node = c.get(f"/api/v5/runs/{rid}/nodes/company.detail").json()
    assert node["result"]["response"]["DATA"]["COMPANYDETAIL"] and node["fields_fed"]
    rerun = c.post(f"/api/v5/runs/{rid}/nodes/company.alias/rerun", json={}).json()
    assert rerun["result"]["status"] == "ok"
    fields = c.get(f"/api/v5/runs/{rid}/fields").json()
    assert len(fields["fields"]) == 29


def test_dataset_hints_never_change_a_generated_identity():
    """Regression (found in live QA): the found tile and the call header named the same seller differently."""
    plain = roster.profile("real-67890123")
    hinted = roster.profile("real-67890123", {"city": "Surat", "category": "Cotton Fabric"})
    assert (plain["company_name"], plain["contact_name"]) == (hinted["company_name"], hinted["contact_name"])
    assert hinted["city"] == "Surat" and hinted["top_mcats"][0]["mcat_name"] == "Cotton Fabric"
