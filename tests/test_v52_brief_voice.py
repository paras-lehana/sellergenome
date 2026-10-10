"""V5.2: PM pre-call brief, audible calls (STT fix), Payal on Sarvam Voice Agents (no network in these tests)."""

import asyncio
import json

import httpx
import pytest

from backend.genome import aggregator, brief, contract, mock_upstream, roster
from backend.genome.sarvam import error_detail, stt_mime


def test_stt_mime_strips_codecs_and_falls_back_to_extension():
    assert stt_mime("audio/webm;codecs=opus", "clip.webm") == "audio/webm"   # Chrome's MediaRecorder type (was a 400)
    assert stt_mime("audio/mp4", "clip.mp4") == "audio/mp4"                  # Safari
    assert stt_mime("", "x.ogg") == "audio/ogg" and stt_mime("weird/type", "x.bin") == "application/octet-stream"


def test_sarvam_error_detail_carries_the_reason():
    req = httpx.Request("POST", "https://api.sarvam.ai/speech-to-text")
    resp = httpx.Response(400, json={"error": {"message": "Invalid file type: audio/webm;codecs=opus"}}, request=req)
    err = httpx.HTTPStatusError("bad", request=req, response=resp)
    assert error_detail(err) == "HTTP 400: Invalid file type: audio/webm;codecs=opus"


def test_grouped_insights_are_validated_and_uncited_become_guesses():
    index = {"h:0": {"id": "h:0", "api": "sts.history", "pointer": "/0", "origin": "live_api", "text": "complaint", "label": "STS 7 Oct"}}
    raw = [{"group": "objections", "text": "Complained about irrelevant leads", "confidence": "high", "evidence": ["h:0"]},
           {"group": "intent", "text": "Wants more buyers", "confidence": "high", "evidence": []},
           {"group": "weather", "text": "ignored group", "confidence": "high", "evidence": ["h:0"]}]
    out = aggregator.validate_insights(raw, index)
    assert [(i["group"], i["confidence"]) for i in out] == [("objections", "high"), ("intent", "guess")]
    assert out[0]["sources"][0]["label"] == "STS 7 Oct"


def _run(seller_id="syn-91000101"):
    p = roster.profile(seller_id)
    results = {k: {"status": "ok", "origin": "mock", "retrieved_at": "t", "response": v} for k, v in {
        "company.detail": mock_upstream.company_detail(p), "context.global": mock_upstream.global_context(p),
        "context.insights": mock_upstream.insights(p), "sts.history": mock_upstream.sts_history(p),
        "enquiry.list": mock_upstream.enquiry_list(p), "pns.insights": mock_upstream.pns_insights(p)}.items()}
    c = contract.assemble(p, results, "r")
    persona = aggregator.rules_persona(p, c, contract.evidence_items(c), type("S", (), {"get": lambda self, k, d=None: d})())
    persona["insights"] = [{"group": "intent", "text": "Viewed Star Supplier twice", "confidence": "high",
                            "sources": [{"api": "context.insights", "pointer": "/Data", "origin": "mock", "label": "Insights"}]}]
    return {"seller": roster.summary(p), "contract": c, "persona": persona}


def test_brief_follows_the_pm_structure_with_sources():
    b = brief.build(_run())
    labels = [r["label"] for r in b["basics"]["rows"]]
    assert labels[:5] == ["Seller", "Company", "Nature of business", "City", "State"]
    assert all(r["missing"] or r["origin"] for r in b["basics"]["rows"])        # a value always says where it came from
    assert b["engagement"]["groups"][0]["title"] == "Intent" and b["engagement"]["groups"][0]["items"][0]["confidence"] == "H"
    assert set(b["plan"]["dials"]) == {"voice", "pace", "language", "formality", "tone"}
    assert list(b["plan"]["steps"]) == ["opening", "personalise", "objection", "escalation", "next"]
    assert b["plan"]["settings"] == 10 and isinstance(b["plan"]["low_data"], bool)
    log = b["record"]["log"]
    assert log and log == sorted(log, key=lambda x: str(x["at"] or ""), reverse=True)
    assert {x["disp"] for x in log} <= {"pos", "neu", "neg"}


def test_knowledge_base_holds_only_synthetic_sellers_and_no_prices():
    from backend.genome import sarvam_voice as sv
    docs = sv.kb_documents()
    briefs = [n for n in docs if n.startswith("seller-brief-")]
    assert len(briefs) == len(roster.heroes()) == 14 and {"indiamart-packages.md", "seller-faq.md", "objection-handling.md"} <= set(docs)
    playbook = "\n".join(t for n, t in docs.items() if not n.startswith("seller-brief-"))
    assert "₹" not in playbook and "Rs " not in playbook   # package prices are never in what Payal can say (demand figures in briefs are not prices)
    assert all(n.split("-")[-1].split(".")[0] in roster.heroes() for n in briefs)
    assert all(len(name) <= 32 and name == name.lower() for name in sv.KBS)  # Sarvam KB name rule


def test_agent_context_real_sellers_come_from_their_run():
    """5.10.2 (owner, production): real sellers go to the Sarvam agents — with their prepared run's data, never invented."""
    from backend.genome import sarvam_voice as sv
    from backend.genome.settings import GenomeSettings
    svc = sv.SarvamVoice(GenomeSettings(), None)
    assert svc.context("91000101")["company_name"] == roster.profile("syn-91000101")["company_name"]
    assert svc.context("real-67890123")["seller_id"] == "91000101"           # no prepared run yet: never a made-up profile
    run = {"seller_id": "real-67890123", "glid": "67890123", "status": "completed", "persona": {"generated": {"persona": {}}},
           "seller": {"glid": "67890123", "company_name": "Private seller · GLID 67890123", "city": "Pune"},
           "contract": {"fields": {"company_name": {"value": "Test Traders", "availability": "present"},
                                   "contact_name": {"value": "Asha Rao", "availability": "present"}}}}
    ctx = sv.run_context(run)
    assert ctx["company_name"] == "Test Traders" and ctx["seller_name"] == "Asha" and ctx["seller_id"] == "67890123"
    assert ctx["demand_line"] == "" or isinstance(ctx["demand_line"], str)
    run["seller"]["trigger"] = "Payment Interested"                          # real runs carry the trigger as text (live, 5.10.3)
    assert sv.run_context(run)["trigger_activity"] == "Payment Interested"
    svc.select("real-67890123")                                              # no longer refused


def test_sign_in_refuses_a_callback_from_another_attempt(tmp_path, monkeypatch):
    from backend.genome import sarvam_voice as sv
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path))
    (tmp_path / sv.VoiceAuth.FILE).write_text(json.dumps({"client_id": "c", "pending": {"state": "abc", "verifier": "v"}}))
    with pytest.raises(sv.VoiceAgentError):
        asyncio.run(sv.VoiceAuth().complete("http://127.0.0.1:53682/callback?code=x&state=other"))


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


def test_agent_tool_endpoints_need_the_key_and_public_status_hides_auth(client):
    assert client.get("/api/v5/sarvam-agent/context?seller_id=91000101").status_code == 401
    ok = client.get("/api/v5/sarvam-agent/context?seller_id=91000101", headers={"X-SellerGenome-Key": "tool-key-test"})
    assert ok.status_code == 200 and ok.json()["company_name"]
    m = client.post("/api/v5/sarvam-agent/meetings", json={"seller_id": "91000101", "slot": "tomorrow 11 am", "mode": "online"},
                    headers={"X-API-Key": "tool-key-test"}).json()
    assert m["meeting_link"].startswith("https://meet.jit.si/") and "tomorrow 11 am" in m["confirmation"]
    status = client.get("/api/v5/sarvam-voice").json()
    assert "auth" not in status and "build_log" not in status and status["calls"]
    assert client.post("/api/v5/sarvam-voice/chat", json={"text": "hello"}).status_code == 409   # not built in a fresh config
    assert client.post("/api/v5/admin/sarvam-voice/build").status_code == 401
