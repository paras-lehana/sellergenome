"""V5.7: the VANI buyer-requirement agent — built-in policy (owner's prompt, closings verbatim), Talk as Buyer, the separate
Sarvam Voice Agent (prompt shape per Sarvam's playbook, tools, context) and its routes. Offline: no Sarvam, no network."""

import asyncio
import json
import re

import pytest

from backend.genome import buyer as bd
from backend.genome import sarvam_buyer as sb
from backend.genome.settings import GenomeSettings
from backend.genome.store import GenomeStore


class Replies:
    """A stand-in for the Sarvam client: returns the queued JSON turns in order."""

    def __init__(self, *turns):
        self.turns = list(turns)

    async def chat_json(self, system, user, **kw):
        if system.startswith("Rewrite this reply"):  # the guard's one rewrite (language / two options)
            return {"data": {"reply": user.replace("Theek hai. ", "")}, "ms": 1.0}
        assert "VANI" in system and "Never use the word \"quantity\"" in system
        return {"data": self.turns.pop(0), "ms": 5.0}


@pytest.fixture()
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    settings = GenomeSettings()
    return settings, GenomeStore(tmp_path / "v5.sqlite3")


def _talk(settings, store, *turns, language="hi-IN"):
    d = bd.BuyerDesk(settings, store, Replies(*turns))
    tile = {"call": {"id": "b1", "product": "Industrial Air Compressor", "missed_seller": "Delhi Pneumatics", "language": language,
                     "flow": "new_requirement"}, "turns": [{"who": "bot", "text": "seeded line"}]}
    return d, d.start(tile, operator=False, policy="compact", talk=True)


def test_talk_as_buyer_starts_from_scratch_with_vani_opening(desk):
    _, call = _talk(*desk)
    assert call["policy"] == "requirement" and call["talk"] and [t["who"] for t in call["transcript"]] == ["bot"]
    first = call["transcript"][0]["text"]
    assert "VANI" in first and "Delhi Pneumatics" in first and "Industrial Air Compressor" in first and "seeded" not in first
    assert call["limits"] == {"product": 4, "quantity": 3, "specification": 2} and call["spec_names"] == ["Power", "Type"]


def test_requirement_flow_confirms_collects_and_closes_with_the_owners_line(desk):
    d, call = _talk(*desk,
                    {"reply": "Ji bilkul, aapki requirement note kar raha hoon. Kitne piece chahiye?", "product_confirmed": True,
                     "asked_field": "quantity", "language": "hi-IN"},
                    {"reply": "Theek hai. Power kaun sa, 5 HP ya 10 HP, ya kuch aur?", "captured": {"quantity": "50", "quantity_unit": "Piece"},
                     "asked_field": "specification", "asked_spec": "Power"},
                    {"reply": "x", "captured": {"specs": {"Power": "10 HP", "Type": "Screw"}}, "close": "requirement_noted"})
    out = asyncio.run(d.turn(call, "haan ji compressor chahiye"))
    assert call["product_confirmed"] and call["disposition"] == "BL Approved" and call["outcome"] == "BL Approved"
    asyncio.run(d.turn(call, "pachaas"))
    assert call["lead"]["quantity"] == "50" and call["attempts"]["spec:Power"] == 1
    out = asyncio.run(d.turn(call, "10 HP, screw wala"))
    last = call["transcript"][-1]["text"]
    assert last == bd.CLOSES["requirement_noted"][2]                                # the owner's line, verbatim
    assert call["status"] == "ended" and call["disposition"] == call["outcome"] == "BL Enriched"
    assert {t["tool"] for t in call["tools"]} == {"post_lead", "whatsapp"}
    assert all(t["result"].get("posted_to_production") is False for t in call["tools"] if t["tool"] == "post_lead")
    assert out["call"]["lead"]["specs"] == {"Power": "10 HP", "Type": "Screw"}


def test_ask_limits_language_switch_and_product_change_are_visible(desk):
    d, call = _talk(*desk,
                    {"reply": "How many pieces?", "language": "en-IN", "product_confirmed": True, "asked_field": "quantity"},
                    {"reply": "Is it a solar water heater?", "captured": {"product": "Solar Water Heater"}, "intents": ["PRODUCT_CHANGE"],
                     "product_confirmed": True, "tool": "product_context", "asked_field": "none"})
    asyncio.run(d.turn(call, "yes, but speak English please"))
    assert call["language"] == "en-IN" and call["adaptations"][-1]["kind"] == "language"
    asyncio.run(d.turn(call, "actually I need a solar water heater"))
    assert call["lead"]["product"] == "Solar Water Heater" and call["spec_names"] == ["Capacity", "Type"]
    assert call["adaptations"][-1]["kind"] == "product" and any(t["tool"] == "product_context" for t in call["tools"])
    call["attempts"]["quantity"] = 3
    guard = d._apply_requirement(call, {"reply": "How many?", "asked_field": "quantity"})
    assert "quantity" in call["closed"] and call["adaptations"][-1]["kind"] == "limit"
    assert guard["reply"].startswith("No problem, you can discuss that directly with the seller.") and "Capacity" in guard["reply"]


def test_unconfirmed_product_after_four_asks_closes_with_no_requirement(desk):
    d, call = _talk(*desk)
    call["attempts"]["product"] = 4
    guard = d._apply_requirement(call, {"reply": "Kya aapko compressor chahiye?", "asked_field": "product"})
    assert guard["reply"] == bd.CLOSES["no_requirement"][2] and call["outcome"] == "No Product Requirement" and call["status"] == "ended"


def test_sarvam_prompt_follows_the_playbook():
    heads = re.findall(r"^## (.+)$", sb.PROMPT, flags=re.M)
    assert heads == ["Persona", "Environment & Situation", "Objective", "Speaking style rules", "Facts", "Conversation guidelines", "Guardrails"]
    assert "###" not in sb.PROMPT and "—" not in sb.PROMPT and "--" not in sb.PROMPT and '"' not in sb.PROMPT
    assert not re.search(r"\b(I|I'll|we|We|you|You|your)\b", sb.PROMPT)                 # third person, no address of the caller
    for name in ("requested_another_product_tool", "end_interaction"):
        assert f"call tool:{name}" in sb.PROMPT
    assert not re.search(r"tool:\w+[.,;:]", sb.PROMPT)                                  # a space before punctuation (live build refused it)
    for var in re.findall(r"\{\{(\w+)\}\}", sb.PROMPT + sb.INTRO):
        assert var in sb.INPUTS                                                          # only values that arrive with the call
    assert not any(f"@{k}" in sb.PROMPT or f"@ {k}" in sb.PROMPT for k in sb.OUTPUTS)   # outcomes are after-call
    assert len(sb.INTRO.split()) < 25 and len(sb.PROMPT) < 16000
    assert set(sb.OUTPUTS["call_outcome"][1]) >= {"BL Enriched", "No Product Requirement", "Particular Seller", "Selling Related"}


def test_sarvam_context_product_and_outcome_use_synthetic_calls_only(desk):
    settings, store = desk
    from backend.genome.sarvam_voice import SarvamVoice
    agent = sb.BuyerVoiceAgent(settings, store, SarvamVoice(settings, store))
    ctx = agent.context("buyer-b1")
    assert ctx["buyer_call_id"] == "buyer-b1" and ctx["product_name"] == "Industrial Air Compressor"
    assert json.loads(ctx["specification_options"]) == {"Power": ["5 HP", "10 HP", "15 HP"], "Type": ["Screw", "Reciprocating"]}
    assert agent.context("real-23456789")["buyer_call_id"] == "buyer-b1"             # unknown ids fall back to a demo call
    _, call = _talk(settings, store)
    store.put("buyer_calls", call["id"], call)
    assert agent.context(call["id"])["product_name"] == "Industrial Air Compressor"
    p = agent.product(call["id"], "solar water heater")
    assert "Capacity" in p["specification_options"] and p["confirmation"].startswith("Noted")
    agent.outcome({"buyer_call_id": call["id"], "quantity": "20", "call_outcome": "BL Enriched", "phone": "9876543210"})
    req = store.get("buyer_calls", call["id"])["sarvam"]["requirement"]
    assert req["quantity"] == "20" and req["posted_to_production"] is False and "phone" not in req
    with pytest.raises(sb.VoiceAgentError):
        agent.live_start("buyer-b1", "visitor")                                          # not built in a fresh config
    status = agent.public_status()
    assert "build_log" not in status and "links" not in status and status["changes"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SARVAM_AGENT_TOOL_KEY", "tool-key-test")
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    with TestClient(main.app) as c:
        yield c


def test_buyer_agent_routes(client):
    assert client.get("/api/v5/sarvam-buyer-agent/context?call_id=buyer-b1").status_code == 401
    ok = client.get("/api/v5/sarvam-buyer-agent/context?call_id=buyer-b1", headers={"X-SellerGenome-Key": "tool-key-test"})
    assert ok.status_code == 200 and ok.json()["seller_name"]
    tile = client.get("/api/v5/floor?flow=buyer").json()["tiles"][0]
    call = client.post("/api/v5/buyer/calls", json={"tile_id": tile["id"], "talk": True, "policy": "requirement"}).json()
    assert call["policy"] == "requirement" and call["transcript"][0]["source"] == "opening"
    assert client.post("/api/v5/sarvam-buyer/chat", json={"buyer_call_id": call["id"], "text": "haan"}).status_code == 409  # not built
    assert client.post("/api/v5/sarvam-live/sessions", json={"agent": "buyer", "buyer_call_id": call["id"]}).status_code == 409
    assert client.post("/api/v5/admin/sarvam-buyer/build").status_code == 401
    status = client.get("/api/v5/sarvam-buyer").json()
    assert status["prompt"].startswith("## Persona") and "links" not in status


def test_english_reply_with_hindi_words_is_rewritten_once(desk):
    settings, store = desk
    d = bd.BuyerDesk(settings, store, None)

    class Two:
        calls = 0

        async def chat_json(self, system, user, **kw):
            Two.calls += 1
            if Two.calls == 1:
                return {"data": {"reply": "Sure. Power kya lena hai, 5 HP ya 10 HP?", "language": "en-IN", "product_confirmed": True}, "ms": 5}
            assert "plain, polite Indian English" in system and "Power kya" in user
            return {"data": {"reply": "Sure. Which power do you need, 5 HP or 10 HP?"}, "ms": 4}
    d.sarvam = Two()
    _, call = _talk(settings, store)
    asyncio.run(d.turn(call, "Please speak English"))
    assert call["transcript"][-1]["text"] == "Sure. Which power do you need, 5 HP or 10 HP?" and Two.calls == 2


def test_three_options_are_cut_to_two_and_buyer_tiles_have_a_clock(desk):
    settings, store = desk
    from backend.genome import floor
    d = bd.BuyerDesk(settings, store, None)
    _, call = _talk(settings, store)
    ctx = bd.product_context("Industrial Air Compressor")
    fixes = d._reply_problems(call, {"asked_spec": "Power"}, "Power kaun sa, 5 HP, 10 HP ya 15 HP?", ctx)
    assert fixes == ["name only two options, 5 HP ya 10 HP, then invite any other answer"]
    assert d._reply_problems(call, {"asked_spec": "Power"}, "Power kaun sa, 5 HP ya 10 HP, ya kuch aur?", ctx) == []
    call["language"] = "en-IN"
    assert d._reply_problems(call, {}, "Sure. Part kavala, belt leda bearing?", ctx)[0].startswith("write it only in plain")
    assert all(isinstance(t.get("on_air_s"), float) for t in floor.buyer_floor(settings)["tiles"])   # was "ON AIR NaN:NaN"


def test_an_explicit_english_request_switches_in_code(desk):
    d, call = _talk(*desk, {"reply": "Theek hai. How many pieces do you need?", "product_confirmed": True, "asked_field": "quantity"})
    asyncio.run(d.turn(call, "Haan, please speak English"))
    assert call["language"] == "en-IN" and call["adaptations"][-1]["kind"] == "language"
    assert call["transcript"][-1]["text"] == "How many pieces do you need?"                   # Hindi word rewritten away


def test_payal_gets_real_reference_lines_and_names_only_known_variables():
    from backend.genome import roster, sarvam_voice as sv
    lines = sv.reference_lines(roster.profile("syn-91000105"))
    assert set(lines) == {"demand_line", "last_contact"} and (lines["demand_line"] or lines["last_contact"])
    assert sv.reference_lines({"counts": {}, "products": []}) == {"demand_line": "", "last_contact": ""}   # nothing invented
    for var in re.findall(r"\{\{(\w+)\}\}", sv.PROMPT + sv.INTRO):
        assert var in sv.INPUTS
    assert "Adapting to the seller" in sv.PROMPT and '"' not in sv.PROMPT.split("## Guardrails")[0]


def test_devanagari_loanwords_from_sarvam_stt_are_signals():
    from backend.genome import signals
    assert "rush" in [d["signal"] for d in signals.detect("टाइम नहीं है मेरे पास।", "hi-IN")]
    assert "interest" in [d["signal"] for d in signals.detect("ठीक है, कल मीटिंग रख लीजिए", "hi-IN")]
    assert "confusion" in [d["signal"] for d in signals.detect("क्लियर नहीं हुआ", "hi-IN")]
