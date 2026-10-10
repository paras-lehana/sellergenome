"""5.12.0 — every agent knows IndiaMART, the payment-first sales playbook and the seller's live talking points."""
import re

from backend.genome import agent_context as ac, conversation, prompts, sarvam_agents, sarvam_voice

GC = {"data": {"activitydetails": {
    "top3mcat_competitors": [{"competitor_company": "Alpha Textiles", "competitor_city": "Mumbai", "custtype_name": "LEADER", "competitor_membersince": "Nov 2010"},
                             {"competitor_company": "Beta Fabrics", "competitor_city": "Mumbai", "custtype_name": "STAR"},
                             {"competitor_company": "Gamma Mills", "competitor_city": "Mumbai", "custtype_name": "CATALOG"},
                             {"competitor_company": "Delta Co", "competitor_city": "Mumbai", "custtype_name": "CATALOG"}],
    "top3mcat_data": [{"local_paid_sellers": 412, "hyperlocal_paid_sellers": 120, "local_bl": 350, "hyperlocal_bl": 128,
                       "mcat_list": '[{"mcat_name": "Cotton Fabric"}, {"mcat_name": "Denim Fabric"}]'}],
    "LAST_LEAD": {"TITLE": "Linen Fabrics", "DATE_R": "2026-09-07 15:15:01"},
    "LAST_ENQUIRY": {"PRODUCT_NAME": "Enquiry for Round Tin Box from Some Buyer via IndiaMART"}},
    "kycdetails": {"customer_type": "Free", "payment_invoice_date": "14-Mar-2019", "gst_nature_of_business": "Trader - Wholesaler/Distributor",
                   "nach_status": [{"S_NAME": "MDC.", "DIS_TEXT": "Stopped - OVP Rejection"}]}}}
RUN = {"seller": {"city": "Mumbai", "synthetic": False}, "contract": {"fields": {}},
       "results": {"context.getcontext": {"status": "ok", "response": GC}},
       "persona": {"generated": {"persona": {"engagement_with_buyers": {"stats": [{"n": 0, "label": "enquiries received, last 30 days"}]},
                                             "call_plan": {"trigger": {"detail": "Payment follow-up due 13 Oct 2026."}, "purpose": "Check the payment."}}}}}


def test_call_facts_carry_competitors_market_payment_and_buyers():
    f = ac.call_facts(RUN)
    assert f["competitors"].startswith("Alpha Textiles (Mumbai, Leading Supplier plan") and "and 1 more" in f["competitors"]
    assert "412 paid sellers in Mumbai" in f["market_snapshot"] and "Cotton Fabric" in f["market_snapshot"]
    assert "auto-debit (NACH) mandate for MDC: Stopped - OVP Rejection" in f["payment_status"] and "14-Mar-2019" in f["payment_status"]
    assert "Linen Fabrics" in f["buyer_activity"] and "Round Tin Box" in f["buyer_activity"] and "Some Buyer" not in f["buyer_activity"]
    assert "Payment follow-up due 13 Oct 2026" in f["call_reason"]
    assert all(isinstance(v, str) for v in f.values())
    assert ac.call_facts({"results": {}, "persona": {}, "seller": {}}) == {k: "" for k in f}     # nothing invented


def test_every_seller_agent_prompt_has_the_knowledge_and_the_playbook():
    for text in (sarvam_voice.PROMPT, sarvam_agents.PROMPT, conversation.BOT_TURN_SYSTEM):
        assert "Mini Dynamic Catalog" in text and "Payment first" in text and "Competitors." in text
    for pid in ("sarvam.payal.prompt", "sarvam.manjula_v2.prompt", "sarvam.manjula_v2.intro", "seller.bot_turn"):
        assert prompts.problems(pid, prompts.default(pid)) == [], pid
    for var in re.findall(r"\{\{(\w+)\}\}", sarvam_agents.PROMPT + sarvam_agents.INTRO):
        assert var in sarvam_agents.INPUTS
    assert {"competitors", "payment_status", "buyer_activity", "market_snapshot"} <= set(sarvam_voice.INPUTS)


def test_listing_api_inputs_and_tolerant_parser():
    from backend.genome import parsers
    detail = {"DATA": {"BREADCRUMB": {"MCAT_ID": "8591"}, "PRDSERV": [{"ITEM_GLMCAT_ID_LIST": ",1318, ,149653,"}]}}
    assert parsers.main_mcat(detail) == "8591" and parsers.main_mcat({"DATA": {"PRDSERV": [{"ITEM_GLMCAT_ID_LIST": ",1318,"}, {"ITEM_GLMCAT_ID_LIST": ",1318, ,2,"}]}}) == "1318"
    assert parsers.city_slug("Navi Mumbai") == "navi-mumbai"
    rows = {"RESULTS": [{"COMPANYNAME": "Alpha Textiles", "CITY": "Mumbai", "CUSTTYPE": "Star", "GLUSR_ID": "1"},
                        {"COMPANYNAME": "Own Co", "GLUSR_ID": "99"}, {"COMPANYNAME": "alpha textiles", "GLUSR_ID": "3"}]}
    assert parsers.listing_sellers(rows, "99") == [{"company": "Alpha Textiles", "city": "Mumbai", "plan": "Star", "glid": "1"}]
    run = {**RUN, "results": {**RUN["results"], "market.listing": {"status": "ok", "response": rows}}}
    assert ac.call_facts(run)["competitors"].startswith("Alpha Textiles (Mumbai; Star)")         # the listing wins over Global Context


def test_synthetic_payment_bullets_read_as_one_clause_list():
    """5.13.4: the hero's purchase-intent insight holds "- " bullet lines; the agent heard "August.; - On 12 August"."""
    p = {"insights": {"purchase_intent": ["Viewed the Star package in August.", "- On 12 August a note.\n- Opened the page on 7 October."]}}
    pay = ac.profile_facts(p)["payment_status"]
    assert pay == "Viewed the Star package in August; On 12 August a note; Opened the page on 7 October"
