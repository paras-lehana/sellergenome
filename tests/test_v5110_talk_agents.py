"""5.11.0 — every workspace agent gets the seller's context under its own variable names; one agent per call."""
from backend.genome import sarvam_voice as sv
from backend.genome.settings import GenomeSettings

RUN = {"seller_id": "real-67890123", "glid": "67890123", "status": "completed",
       "seller": {"glid": "67890123", "company_name": "Private seller · GLID 67890123", "city": "Pune", "trigger": "Payment Interested"},
       "contract": {"fields": {"company_name": {"value": "Test Traders", "availability": "present"},
                               "contact_name": {"value": "Asha Rao", "availability": "present"},
                               "state": {"value": "Maharashtra", "availability": "present"}}},
       "persona": {"archetype": "rushed", "factors": {"language": {"value": "hi-IN"}}, "generated": {"persona": {
           "persona": {"summary": "Busy trader, replies in short bursts.", "title": "Brief, direct, Hindi",
                       "dials": {"pace": {"value": "brisk"}, "warmth": {"value": "warm"}}},
           "basics": {"name": "Asha Rao", "company": "Test Traders, Pune", "rows": [{"k": "Customer type", "v": "Free"}], "products": ["Steel Pipes"]},
           "engagement_with_indiamart": {"insights": [{"group": "Relationship", "text": "Meeting held on 8 Oct 2026."}]},
           "call_plan": {"trigger": {"detail": "Follow-up due 13 Oct 2026."}, "points_to_cover": [{"text": "Payment status"}]},
           "record": [{"id": "r1", "date": "2026-10-08", "type": "Meeting", "outcome": {"label": "Meeting update"}, "text": "Met the partner."}]}}}}


def test_manjula_style_variables_carry_the_real_seller_context():
    v = sv.run_context(RUN)
    assert v["company_name"] == "Test Traders" and v["seller_name"] == "Asha" and v["seller_state"] == "Maharashtra"
    assert "Meeting held on 8 Oct 2026" in v["seller_context"] and "Follow-up due 13 Oct 2026" in v["seller_context"]
    assert "Met the partner" in v["seller_context"] and v["persona_summary"].startswith("Busy trader")
    assert v["preferred_language"] == "Hindi" and v["caller_mood"] == "in a hurry" and "brisk" in v["tone_directive"]
    assert all(isinstance(x, str) for x in v.values())


def test_each_agent_gets_only_the_variables_it_declares():
    svc = sv.SarvamVoice(GenomeSettings(), None)
    ours = svc.start_overrides("real-67890123", {}, RUN)["agent_variables"]
    assert set(ours) <= set(sv.INPUTS) and "seller_context" not in ours          # our Payal declares INPUTS only
    manjula = ["seller_id", "seller_name", "company_name", "seller_city", "seller_context", "persona_summary", "tone_directive",
               "preferred_language", "caller_mood", "session_id"]
    got = svc.start_overrides("real-67890123", {}, RUN, keys=manjula)["agent_variables"]
    assert set(got) <= set(manjula) and got["seller_context"] and got["company_name"] == "Test Traders"
    syn = svc.start_overrides("syn-91000101", {}, None, keys=manjula)["agent_variables"]
    assert syn["seller_context"] and syn["preferred_language"]                     # synthetic sellers too
