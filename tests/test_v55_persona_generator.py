"""V5.5: the owner's Persona Generator prompt v0.3 fed per the Consolidated API Guide (offline — no LLM, no network)."""

import copy
import json
from datetime import date

import pytest

from backend.genome import brief, contract, mock_upstream, parsers, persona_eval as pe, persona_generator as pg, roster

TODAY = date(2026, 10, 10)


def test_enquiry_parser_reads_the_guide_shape_and_skips_counterfeit():
    payload = {"status": "success", "code": 200, "error": "", "response": {"enquiries": [
        {"enquiryTitle": "Rolex First Copy", "enquiryISQ": [{"DESC": "Quality", "RESPONSE": "First Copy"}], "postedDate": "2026-10-08T10:00:00.123456",
         "buyerName": "Ravi ", "buyerMemberSince": "2024-01-01T00:00:00", "mobileVerified": 1, "emailVerified": 0, "addressVerified": 0, "gstVerified": 0,
         "countryFlag": "in_flag_s.png", "queryId": 1},
        {"enquiryTitle": "Steel pipes", "enquiryISQ": [{"DESC": "Quantity", "RESPONSE": "50"}, {"DESC": "Quantity Unit", "RESPONSE": "Piece"},
                                                       {"DESC": "Probable Order Value", "RESPONSE": "Rs. 5000"}],
         "postedDate": "2026-10-07T09:00:00.000001", "buyerName": "Ravi", "buyerMemberSince": "2024-01-01T00:00:00", "mobileVerified": 1, "emailVerified": 1,
         "addressVerified": 1, "gstVerified": 1, "countryFlag": "de_flag_s.png", "queryId": 2}]}}
    rows = parsers.enquiries(payload)
    assert rows[0]["first_copy"] and not rows[1]["first_copy"]
    assert rows[1]["quantity"] == "50 Piece" and rows[1]["checks_passed"] == 4 and rows[1]["foreign"]
    assert rows[0]["buyer_key"] == rows[1]["buyer_key"]                       # trimmed name + member-since = one buyer
    st = parsers.enquiry_stats(rows, now=parsers.datetime(2026, 10, 10, tzinfo=parsers.IST))
    assert st["listed"] == 1 and st["skipped_first_copy"] == 1 and st["with_quantity"] == 1 and st["fully_verified"] == 1


def test_sts_is_persona_safe():
    entries = [
        {"at": "2026-10-09T10:00:00", "by": "WEBPAY", "via": "Online Payment", "text": "Online Payment Attempted by - X GLID : 1 for Gold Package of Rs 9440.00 from APP", "kind": "payment"},
        {"at": "2026-10-08T10:00:00", "by": "WEBPAY", "via": "Online Payment", "text": "Online Payment Attempted by - X GLID : 1 for Single India BuyLead - 399 of Rs 470.82 from APP", "kind": "payment"},
        {"at": "2026-10-07T11:00:00", "by": "Ramesh Kumar", "via": "WEBERP", "text": "Updated TAN Verification/Change Request Ticket(10781494) Ticket Status: Closed. Comments: details", "kind": "ticket"},
        {"at": "2026-10-07T11:30:00", "by": "Ramesh Kumar", "via": "WEBERP", "text": "App sharing session concluded with 113749. Start time 11:30", "kind": "note"},
        {"at": "2026-10-07T12:00:00", "by": "IN Penny Drop", "via": "MERP MARKETPLACE PENNY DROP CONSUMER", "text": "Could not verify details. Reason:Penny Drop Failed.", "kind": "note"},
        {"at": "2026-10-05T12:00:00", "by": "Seller Help", "via": "WA_9696", "text": "Service Deactivation. Seller wants to deactivate their services.", "kind": "note"},
    ]
    for e in entries:
        e["automated"] = parsers.automated(e["by"], e["via"])
    assert [e["automated"] for e in entries] == [True, True, False, False, True, True]
    assert not parsers.automated("Kapil Sharma", "WebERP") and parsers.automated("Weberp Auto Process", "WebERP")
    out = parsers.sts_for_persona(entries)
    text = json.dumps(out)
    assert "Rs" not in text and "9440" not in text and "113749" not in text and "Penny" not in text and "Ticket Status" not in text
    payment = next(x for x in out if x["kind"] == "payment")
    assert payment["repeated"] == 2 and "Gold Package" in payment["text"] and "Single India BuyLead" in payment["text"] and "399" not in payment["text"]
    assert any(x.get("ticket_type") == "TAN Verification/Change Request" for x in out)
    assert any("deactivate" in (x.get("text") or "") for x in out)            # the seller's own request stays


def test_global_context_parser_follows_the_guide():
    p = roster.profile("syn-91000105")  # frustrated: an open ticket listed in wip and current
    gc = parsers.global_context(mock_upstream.global_context(p))
    assert len(gc["tickets"]) == 1 and gc["tickets"][0]["in_progress"]
    assert all(r["kind"] in ("call", "meeting", "video_meeting") for r in gc["connects"])
    assert gc["activity"]["competitors_listed"] >= 1 and "competitors" not in json.dumps(gc["activity"]).lower().replace("competitors_listed", "")
    assert gc["kyc"]["customer_type"] in ("Paid", "Free") and gc["activity"]["windows"]["last_4_week"]["bl_active_days"] >= 0
    unanswered = parsers.global_context({"data": {"connectdetails": {"connect_30D": [{"call_answered_date": "2026-10-01 10:00:00", "call_sts_update": ""}]}}})
    assert unanswered["connects"][0]["answered"] is False                       # a timestamp alone is not an answered call
    note = parsers.meeting_note("01 Meeting Agenda: renewal 02 Decision Maker Name & No: Mr X 9876543210 06 Work to be Done Post Meeting: send photos")
    assert note == {"agenda": "renewal", "work_to_do": "send photos"}            # decision maker dropped


def test_customer_type_decides_paid_or_free():
    assert contract.classify_customer("Free", 500) == "free" and contract.classify_customer("", 500) == "paid"


def test_generator_input_is_minimal_and_clean():
    case = next(c for c in pe.scenarios(TODAY) if "First Copy" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    blob = json.dumps(data, ensure_ascii=False)
    assert "First Copy" not in blob and "Test Buyer" not in blob and "buyerName" not in blob
    assert not parsers.PHONE.search(blob) and not parsers.EMAIL.search(blob) and "recording" not in blob.lower()
    assert not any(c in blob for c in refs["competitors"])
    assert {r["id"] for r in data["records"]} == refs["record_ids"] and all(f["id"].startswith("f") for f in data["facts"])
    system, guide = pg.prompt_parts()
    assert len(system) <= pg.SYSTEM_LIMIT and guide.startswith("## Input") and "<!--" not in system


@pytest.mark.parametrize("name,want", [("New free seller", []), ("Follow-up due", ["follow_up_due"]), ("Unknown hot-lead type PNSM", []),
                                       ("Meeting with pending work", ["unresolved_last_interaction"]), ("Renewal activity", ["hot_lead"])])
def test_expected_triggers_follow_the_prompt_rules(name, want):
    case = next(c for c in pe.scenarios(TODAY) if name in c["name"])
    data, _ = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    assert [t["type"] for t in pg.expected_triggers(data, TODAY)] == want


def _good_output(data: dict) -> dict:
    rec = data["records"][:2]
    return {"schema_version": "0.3", "glid": data["lead"]["glid"], "segment": "Paid", "basics": {"name": "x", "company": "y", "rows": [{"k": "City", "v": "Pune"}],
            "products": [], "persona_tags": ["Active daily", "Hands-on"], "flags": []},
            "persona": {"title": "Active", "summary": "Active on buy leads.", "voice": {"gender": "Neutral default", "language": "Hindi", "confidence": "G"},
                        "dials": {k: {"pos": v, "confidence": "M", "bounds": [max(0, v - s), min(100, v + s)]} for k, v, s in
                                  (("pace", 60, 25), ("mix", 30, 25), ("formality", 55, 15), ("warmth", 70, 15))}},
            "engagement_with_indiamart": {"insights": [{"id": f"i{i}", "group": "Platform use", "text": "Active on buy leads.", "confidence": "H",
                                                        "evidence": [{"label": "x", "record_id": rec[i % len(rec)]["id"]}]} for i in range(5)], "data_doubts": []},
            "engagement_with_buyers": {"stats": [], "catalogue": [], "insights": []},
            "call_plan": {"trigger": {"type": "open_request", "detail": "A ticket is in progress."}, "purpose": "Ask for its status.",
                          "opening": {"line": "Farhan ji, IndiaMART se VANI. Kya ticket ka update mil gaya?", "why": "ticket"},
                          "points_to_cover": [{"text": "a"}, {"text": "b"}], "questions_to_ask": ["Kya update hai?"], "avoid": ["No new plan"],
                          "next_step": "Note the status.", "other_triggers": ["hot_lead"]},
            "call_plan_note": None, "record": [{"id": r["id"], "date": r["date"], "type": r["type"], "outcome": {"label": "x", "tone": "neu"}, "text": "t"} for r in rec],
            "counts": [], "gaps": ["Turnover"], "guardrails": pg.GUARDRAILS}


def test_checks_pass_a_good_persona_and_catch_planted_violations():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    good = _good_output(data)
    ev = pg.evaluate(good, data, refs, TODAY)
    assert ev["critical"] == 0, [c for c in ev["checks"] if not c["ok"]]
    bad = copy.deepcopy(good)
    bad["record"].append({"id": "r99", "date": "2026-01-01", "type": "Meeting", "outcome": {"label": "x", "tone": "neu"}, "text": "invented"})
    bad["engagement_with_indiamart"]["insights"][0]["text"] = "Call the seller on 9876543210 about the refund."
    bad["call_plan"] = None
    ev = pg.evaluate(bad, data, refs, TODAY)
    failed = {c["check"] for c in ev["checks"] if not c["ok"] and c["critical"]}
    assert {"Record only lists given records", "Never-mention list respected", "Call plan present when a trigger exists"} <= failed
    widened = copy.deepcopy(good)
    widened["persona"]["dials"]["pace"].update(confidence="G", bounds=[0, 0])
    assert pg.enforce_bounds(widened) and widened["persona"]["dials"]["pace"]["bounds"] == [20, 100]


def test_generated_persona_feeds_the_bot_and_the_brief():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    result = {"persona": out, "evaluation": pg.evaluate(out, data, refs, TODAY), "attempts": [{"ms": 1}], "ms": 1, "model": "m", "expected_triggers": []}
    base = {"summary": "s", "tags": [], "factors": {}, "teleprompter": {}}

    class S:
        def get(self, k, d=None):
            return d
    persona = pg.to_persona(result, base, S())
    assert persona["factors"]["opening"]["value"].startswith("Farhan ji") and persona["engine"] == "persona_generator_v0.3"
    assert 0.85 <= persona["factors"]["pace"]["value"] <= 1.25 and persona["factors"]["language"]["value"] == "hi-IN"
    run = {"seller": {"company_name": "y"}, "persona": persona, "contract": {"fields": {}},
           "results": {"sarvam.persona_generator": {"input": data}, "sts.history": {"origin": "mock"}, "context.global": {"origin": "mock"}}}
    b = brief.build(run)
    assert b["engine"] == "generator" and b["plan"]["trigger"]["type"] == "open_request" and b["quality"]["score"] == result["evaluation"]["score"]
    chips = [e for g in b["engagement"]["groups"] for i in g["items"] for e in i["evidence"]]
    assert chips and all(c["api"] in ("context.global", "sts.history", "enquiry.list", "pns.insights", "company.detail") for c in chips)


def test_persona_safe_text_drops_ops_sentences_and_amounts():
    out = parsers.persona_safe("Complained about irrelevant leads; wants refund discussion. Paid Rs 4,130 yesterday. Call on 9876543210.")
    assert "refund" not in out and "4,130" not in out and "9876543210" not in out and out.startswith("Complained")


def test_after_the_10th_is_a_dated_callback():
    case = next(c for c in pe.scenarios(TODAY) if "Qureshi" in c["name"])
    data, _ = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    assert [t["type"] for t in pg.expected_triggers(data, TODAY)][:1] == ["follow_up_due"]   # "asked to call after the 10th", today is the 10th
    assert pg._since_year("4 Y 5 M", TODAY) == 2022 and pg._since_year("0 Y 10 M", TODAY) == 2025 and pg._since_year("", TODAY) is None


def test_own_name_is_not_a_forbidden_name_and_bands_are_not_amounts():
    case = next(c for c in pe.scenarios(TODAY) if "Menon" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    assert not any(x.lower() in "menon spice traders" for x in refs["forbidden"])
    assert not pg.MONEY.search(pg.BAND.sub(" ", "40 L - 1.5 Cr")) and pg.MONEY.search(pg.BAND.sub(" ", "Paid ₹9,440 for the plan"))


def test_polish_applies_the_pure_rules():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    out["engagement_with_indiamart"]["insights"][0]["evidence"] = [{"label": "kyc", "record_id": "kycdetails"}]
    out["gaps"] = ["Turnover", "GST number", "Email"]
    out["persona"]["voice"]["language"] = "Tamil"
    fixed = pg.polish(out, data, refs, TODAY)
    assert len(out["engagement_with_indiamart"]["insights"]) == 4 and out["gaps"] == ["Turnover"]   # no evidence, no insight
    assert out["call_plan"]["opening"]["line"] == pg.PLACEHOLDER and out["call_plan"]["opening"]["draft_unchecked"].startswith("Farhan ji")
    assert len(fixed) >= 3 and pg.evaluate(out, data, refs, TODAY)["critical"] == 0
    persona = pg.to_persona({"persona": out}, {"factors": {"opening": {"value": "rules opening"}}}, type("S", (), {"get": lambda self, k, d=None: d})())
    assert persona["factors"]["opening"]["value"] == "rules opening"                 # the placeholder is never spoken


def test_polish_removes_a_plan_the_data_does_not_support():
    case = next(c for c in pe.scenarios(TODAY) if "PNSM" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)                                                      # plan says open_request; no ticket in progress
    out["segment"] = "Free"
    assert pg.evaluate(out, data, refs, TODAY)["critical"] >= 1
    pg.polish(out, data, refs, TODAY)
    assert out["call_plan"] is None and out["call_plan_note"] and pg.evaluate(out, data, refs, TODAY)["critical"] == 0


def test_numbers_check_reads_thousands_and_derived_percentages():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    picked = next(f["value"] for f in data["facts"] if f["label"].startswith("buyer phone calls picked") and "4 weeks" in f["label"])
    received = next(f["value"] for f in data["facts"] if f["label"].startswith("buyer phone calls received") and "4 weeks" in f["label"])
    out["engagement_with_buyers"]["insights"] = [{"text": f"Picked {round(100 * picked / received)}% of buyer calls in the last 4 weeks.", "confidence": "H"},
                                                 {"text": "Competitors pay less, the seller said.", "confidence": "M"}]
    failed = {c["check"] for c in pg.evaluate(out, data, refs, TODAY)["checks"] if not c["ok"]}
    assert "Every number comes from the input" not in failed and "Never-mention list respected" not in failed
    out["engagement_with_buyers"]["insights"][0]["text"] = "Picked 3,917 buyer calls."
    assert "Every number comes from the input" in {c["check"] for c in pg.evaluate(out, data, refs, TODAY)["checks"] if not c["ok"]}


def test_polish_handles_hindi_english_and_company_as_contact():
    case = next(c for c in pe.scenarios(TODAY) if "Contact name is the company" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    out["persona"]["voice"]["language"] = "Hindi-English"
    out["call_plan"]["opening"]["line"] = "Agarwal ji, IndiaMART se VANI. Kya plan dekha?"
    pg.polish(out, data, refs, TODAY)
    assert out["call_plan"]["opening"]["line"] == "IndiaMART se VANI. Kya plan dekha?"
    system, _ = pg.prompt_parts()
    assert not __import__("re").search(r'(?<!")\b(?:he|his|him)\b(?!")', system.split("## Style example")[0])   # only the quoted rule itself



def test_placeholder_is_never_a_name_slot_in_a_hindi_line():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    out["call_plan"]["opening"]["line"] = f"{pg.PLACEHOLDER}. IndiaMART se VANI. Kya ticket ka update mila?"
    pg.polish(out, data, refs, TODAY)
    assert out["call_plan"]["opening"]["line"] == "IndiaMART se VANI. Kya ticket ka update mila?"


def test_the_spoken_opening_follows_the_generator_plan():
    assert pg.opening_hook("Farhan ji, IndiaMART se VANI. 6 October ka ticket abhi khula hai, kya update mila?") == "6 October ka ticket abhi khula hai, kya update mila?"
    assert pg.opening_hook("Namaskar ji. 3 October ko TrustSEAL mein interest dikhaya tha. List ready hai kya?").startswith("3 October ko")
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    persona = pg.to_persona({"persona": out}, {"factors": {}, "teleprompter": {"opening_hook": "aapne renewal page dekha"}},
                            type("S", (), {"get": lambda self, k, d=None: d})())
    assert persona["teleprompter"]["opening_hook"] == "Kya ticket ka update mil gaya?" and persona["teleprompter"]["first_question"] == "Kya update hai?"
    from backend.genome import teleprompter
    line = teleprompter.opening({"contact_name": "Farhan Shaikh", "language": "hi-IN"}, {**persona, "factors": {"language": {"value": "hi-IN"}}},
                                type("S", (), {"get": lambda self, k, d=None: d})())
    assert "renewal" not in line and line.endswith("Kya ticket ka update mil gaya?")


def test_mock_cache_keys_change_with_the_release_live_keys_do_not():
    from backend.genome import registry
    import hashlib
    live = registry.cache_key("context.global", {"glid": "1"}, 3)
    legacy = hashlib.sha256(json.dumps({"api": "context.global", "values": {"glid": "1"}, "rev": 3}, sort_keys=True).encode()).hexdigest()[:32]
    assert live == legacy                                                        # cached live responses stay valid
    assert registry.cache_key("context.global", {"glid": "1"}, 3, salt="mock 5.5.0") != registry.cache_key("context.global", {"glid": "1"}, 3, salt="mock 5.4.0")



def test_regional_seller_hears_the_plans_reason_in_english():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)
    out["persona"]["voice"]["language"] = "Punjabi"
    pg.polish(out, data, refs, TODAY)
    stub = type("S", (), {"get": lambda self, k, d=None: d})()
    persona = pg.to_persona({"persona": out}, {"factors": {}, "teleprompter": {"opening_hook": "you missed 8 pns calls"}}, stub)
    assert persona["teleprompter"]["opening_hook"] == pg.TRIGGER_HOOK_EN["open_request"]
    from backend.genome import teleprompter
    line = teleprompter.opening({"contact_name": "Gurpreet Kaur", "language": "pa-IN"}, {**persona, "factors": {"language": {"value": "pa-IN"}}}, stub)
    assert line.startswith("Hello Gurpreet") and "sorted out?" in line and "missed" not in line



def test_open_request_without_a_ticket_in_progress_is_critical():
    case = next(c for c in pe.scenarios(TODAY) if "Joshi Millet" in c["name"])          # PIM, no ticket in progress
    data, refs = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    out = _good_output(data)                                                         # plan trigger: open_request
    out["segment"] = {"paid": "Paid", "free": "Free"}.get(str(data["global_context"]["kycdetails"].get("customer_type", "")).lower(), "Unknown")
    failed = {c["check"] for c in pg.evaluate(out, data, refs, TODAY)["checks"] if not c["ok"] and c["critical"]}
    assert failed == {"An open request needs a ticket in progress"}
