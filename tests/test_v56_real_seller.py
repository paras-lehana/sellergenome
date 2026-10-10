"""V5.6: real-seller data — Redash float ids, supplied-dataset history and past bot calls fed to the persona generator,
products and identity only from the input, do-not-call, and the free-seller company record (offline, synthetic values)."""

import copy
from datetime import date

import pytest

from backend.genome import floor, persona_eval as pe, persona_generator as pg, teleprompter
from backend.genome.conversation import ConversationEngine
from backend.genome.settings import GenomeSettings
from backend.genome.store import GenomeStore
from backend.source_normalization import identity_matches

TODAY = date(2026, 10, 10)


def test_redash_float_ids_match_their_own_seller_only():
    assert identity_matches("redash_hotlead", {"fk_glusr_usr_id": 91000105.0}, "91000105")
    assert identity_matches("redash_hotlead", {"fk_glusr_usr_id": "91000105.0"}, "91000105")
    assert not identity_matches("redash_hotlead", {"fk_glusr_usr_id": 91000106.0}, "91000105")
    assert not identity_matches("redash_hotlead", {"fk_glusr_usr_id": 91000105.5}, "91000105")
    assert not identity_matches("redash_hotlead", {}, "91000105")       # Redash rows must carry the id


def _real_case():
    """A hero seller turned into a 'private' one with dataset and Redash rows (all values made up)."""
    case = copy.deepcopy(next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"]))
    seller = {**case["seller"], "synthetic": False, "kind": "private", "company_name": "Private seller · GLID 91000105",
              "products": [{"name": "Invented (Premium)"}]}
    results = case["results"]
    results["dataset.seller"] = {"status": "ok", "response": {"found": True, "row": {
        "seller_city": "Pune", "seller_state": "Maharashtra", "top_category_1": "Paracetamol Tablets", "top_category_2": "Cough Syrup",
        "top_category_3": "", "nature_of_business": "Retail Business", "business_type": "Proprietorship", "gst_registration_year": 2019.0,
        "eng_enq_received_1yr": 4.0, "bot_attempts": 6.0, "bot_answered": 3.0, "calls_meeting_fixed": 1.0, "do_not_call_requested": 0.0,
        "showed_frustration": 0.0}}}
    results["dataset.calls"] = {"status": "ok", "response": {"calls": [
        {"call_start_time": "2026-08-02 11:00:00", "disposition_label": "Meeting Fixed", "meeting_fixed": 1, "lead_call_duration": 40.0,
         "lead_call_summary": "Seller agreed to a meeting about buy leads."},
        {"call_start_time": "2026-07-01 10:00:00", "disposition_label": "Not Interested", "meeting_fixed": 0, "lead_call_duration": 12.0,
         "lead_call_summary": "Seller said busy."}]}}
    results["redash.hotlead"] = {"status": "ok", "response": {"rows": [
        {"call_start_time": "2026-08-02 11:00:30", "lead_shl_trigger": 1, "lead_call_duration": 40.0, "lead_call_summary": "Seller agreed to a meeting about buy leads."},
        {"call_start_time": "2026-09-20 16:00:00", "lead_shl_trigger": 0, "lead_call_duration": 8.0, "lead_call_summary": "Call dropped."}]}}
    return seller, results, case["lead"]


def test_private_seller_input_uses_dataset_and_bot_history_never_the_placeholder():
    seller, results, lead = _real_case()
    data, refs = pg.build_input(seller, results, TODAY, lead)
    blob = str(data)
    assert "Private seller" not in blob and "Invented (Premium)" not in blob
    assert data["lead"]["categories"] == ["Paracetamol Tablets", "Cough Syrup"] and data["lead"]["state"] == "Maharashtra"
    ds = data["supplied_dataset"]
    assert ds["nature_of_business"] == "Retail Business" and ds["gst_registration_year"] == 2019 and "Apr–Sep 2026" in ds["as_of"]
    hist = data["bot_call_history"]
    assert [h["date"] for h in hist] == ["2026-09-20", "2026-08-02", "2026-07-01"]            # merged by minute, newest first
    assert "meeting claimed by the bot" in hist[1]["outcome"] and hist[1]["source"] == "dataset.calls"
    assert sum(r["type"] == "Bot call" for r in data["records"]) == 3
    labels = {f["label"]: f["value"] for f in data["facts"]}
    assert labels["VANI bot call attempts, Apr–Sep 2026 (bot_attempts)"] == 6
    assert labels["seller showed frustration on a past bot call (dataset) (showed_frustration)"] == "no"
    assert {r["id"] for r in data["records"]} == refs["record_ids"]


def test_company_record_supplies_identity_and_products():
    seller, results, lead = _real_case()
    results["company.detail"] = {"status": "ok", "response": {"DATA": {"COMPANYDETAIL": {
        "DIR_SEARCH_COMPANY": "Test Pharma House", "DIR_SEARCH_CONTACTPERSON": "Test Person", "DIR_SEARCH_CITY": "Pune"},
        "PRDSERV": [{"ITEM_NAME": "Paracetamol 500"}, {"ITEM_NAME": "Cough Syrup 100 ml"}], "PRDNAV": [{"CAT_NAME": "Tablets", "CAT_PRD_COUNT": 3}]}}}
    data, _ = pg.build_input(seller, results, TODAY, lead)
    assert data["lead"]["company_name"] == "Test Pharma House" and data["lead"]["contact_name"] in ("Test Person", seller["contact_name"])
    assert data["lead"]["products"] == ["Paracetamol 500", "Cough Syrup 100 ml"]
    out = {"basics": {"name": "Private seller · GLID 91000105", "company": "x", "products": ["Paracetamol 500", "Tablet", "Cough Syrup (Premium)"]}}
    assert pg.invented_products(out, data) == ["Cough Syrup (Premium)"]
    fixed = pg.polish(out, data, {}, TODAY)
    assert out["basics"]["products"] == ["Paracetamol 500", "Tablet"] and not pg.PLACEHOLDER_ID.search(out["basics"]["name"])
    assert any("removed (not in the input)" in f for f in fixed)


def test_insights_resting_on_dataset_facts_are_marked_as_history():
    seller, results, lead = _real_case()
    data, refs = pg.build_input(seller, results, TODAY, lead)
    ds = next(f["id"] for f in data["facts"] if f["source"] == "dataset.seller")
    out = {"basics": {"products": []}, "engagement_with_indiamart": {"insights": [
        {"id": "i1", "text": "6 bot calls in the last 90 days.", "evidence": [{"label": "x", "record_id": ds}]},
        {"id": "i2", "text": "6 bot calls in the supplied data.", "evidence": [{"label": "x", "record_id": ds}]}]}}
    pg.polish(out, data, refs, TODAY)
    notes = [i.get("note") for i in out["engagement_with_indiamart"]["insights"]]
    assert notes == [pg.DATASET_NOTE, None]


def test_do_not_call_means_no_plan():
    seller, results, lead = _real_case()
    results["dataset.seller"]["response"]["row"]["do_not_call_requested"] = 1.0
    data, refs = pg.build_input(seller, results, TODAY, lead)
    assert data["lead"]["do_not_call"] is True and pg.expected_triggers(data, TODAY) == []
    out = {"basics": {"products": []}, "call_plan": {"trigger": {"type": "follow_up_due"}, "opening": {"line": "Namaste?"}}}
    pg.polish(out, data, refs, TODAY)
    assert out["call_plan"] is None and "not to be called" in out["call_plan_note"]


def test_synthetic_sellers_keep_their_own_products():
    case = next(c for c in pe.scenarios(TODAY) if "Shaikh Pharma" in c["name"])
    data, _ = pg.build_input(case["seller"], case["results"], TODAY, case["lead"])
    assert data["lead"]["products"] and "supplied_dataset" not in data


# --- floor, Talk as Seller and the opening -------------------------------------------------------


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    settings = GenomeSettings()
    monkeypatch.setattr(settings, "values", {**settings.values, "floor": {**settings.values["floor"], "pinned_real_sellers": ["12345"]}})
    return settings, GenomeStore(tmp_path / "v5.sqlite3")


def test_real_seller_tile_is_never_simulated_and_pinned_only_for_operators(env):
    settings, store = env
    public = floor.seller_floor(settings, store)
    assert not any(t.get("real_seller") for t in public["tiles"])               # anonymous: no real seller on the floor
    op = floor.seller_floor(settings, store, operator=True, hints_for=lambda g: {"city": "Pune"})
    tile = op["tiles"][0]
    assert tile["real_seller"] and tile["id"] == "pinned-real-12345" and tile["turns"] == [] and tile["genome"] == []
    assert tile["seller"]["identity"] == "pending" and tile["seller"]["company_name"].startswith("Private seller")
    store.put("runs", "run-1", {"id": "run-1", "seller_id": "real-12345", "finished_at": "2026-10-10T09:00:00Z", "contract": {"fields": {
        "company_name": {"value": "Test Lamps", "availability": "present", "origin": "live_api"},
        "contact_name": {"value": "Test Person", "availability": "present", "origin": "live_api"}}}})
    tile = floor.tile_for("real-12345", 0.0, 4.5, found=True, store=store)
    assert tile["seller"]["company_name"] == "Test Lamps" and tile["seller"]["identity"] == "confirmed" and tile["turns"] == []


def test_talk_as_seller_starts_with_the_bot_opening(env):
    settings, store = env
    engine = ConversationEngine(settings, store, sarvam=None)
    run_doc = {"id": "run-x", "seller_id": "syn-91000103", "glid": "91000103", "teleprompter": {"opening": "Namaste ji, main Payal. Meeting rakh lein?"},
               "persona": {"factors": {"pace": {"value": 1.0}, "language": {"value": "hi-IN"}}}}
    call = engine.start(run_doc, [], operator=True, opening=True)
    assert [t["who"] for t in call["transcript"]] == ["bot"] and call["transcript"][0]["source"] == "opening"
    assert engine.start(run_doc, [], operator=True)["transcript"] == []                 # other entries keep their behaviour


def _fields(**kw):
    return {"fields": {k: {"value": v, "availability": "present", "origin": "live_api"} for k, v in kw.items()}, "history": []}


def test_opening_names_products_and_one_real_reason_first():
    S = type("S", (), {"get": lambda self, k, d=None: d})()
    persona = {"factors": {"language": {"value": "hi-IN"}}}
    real = {"contact_name": "Test Person", "synthetic": False, "products": [{"name": "Invented (Premium)"}]}
    line = teleprompter.opening(real, persona, S, _fields(products="Brass Lamp, Clay Diya", enquiries_received_30d=2))
    assert line.startswith("Namaste Test ji, main Payal, IndiaMART ki virtual assistant.") and "Brass Lamp ka kaam" in line
    assert "2 buyer enquiries" in line and line.endswith("madad kar sakta hai?") and "Invented" not in line
    assert len(" ".join(line.split()[:20]).split("Brass Lamp")) == 2                # the product is inside the first 20 words
    asm = _fields(products="Brass Lamp")
    asm["history"] = [{"kind": "Bot call (Redash)", "at": "2026-09-20 16:00:00", "summary": "x", "origin": "live_api"}]
    assert "20 September ko IndiaMART se aapki baat hui thi" in teleprompter.opening({"contact_name": "", "synthetic": False}, persona, S, asm)
    assert "  " not in teleprompter.opening({"contact_name": "", "synthetic": False}, persona, S, {})       # no double space without a name
    one = teleprompter.opening(real, persona, S, {**_fields(enquiries_received_30d=1), "categories": ["Brass Lamp", "Clay Diya", "Other Products"]})
    assert "Aap Brass Lamp aur Clay Diya ka kaam karte hain — pichhle 30 din mein aapko 1 buyer enquiry aayi hai." in one
    mock = {"fields": {"enquiries_received_30d": {"value": 9, "availability": "present", "origin": "mock"}}, "history": []}
    assert "9" not in teleprompter.value_line({"synthetic": False}, persona, mock, hindi=True)           # a mock never speaks for a real seller


def test_categories_come_from_the_storefront_then_the_dataset_never_a_mock():
    from backend.genome import contract
    seller = {"glid": "12345", "synthetic": False, "kind": "private"}
    nav = {"DATA": {"PRDNAV": [{"CAT_NAME": "Clay Diya", "CAT_PRD_COUNT": "4"}, {"CAT_NAME": "Other Products", "CAT_PRD_COUNT": 9},
                               {"CAT_NAME": "Brass Lamp", "CAT_PRD_COUNT": 9}]}}
    live = {"company.detail": {"status": "ok", "origin": "live_api", "response": nav},
            "dataset.seller": {"status": "ok", "origin": "local_db", "response": {"found": True, "row": {"top_category_1": "Lamps"}}}}
    assert contract.assemble(seller, live, "lead-1")["categories"] == ["Brass Lamp", "Clay Diya"]
    mocked = {**live, "company.detail": {**live["company.detail"], "origin": "mock"}}
    assert contract.assemble(seller, mocked, "lead-1")["categories"] == ["Lamps"]


def test_a_plan_reason_still_starts_with_what_the_seller_deals_in():
    S = type("S", (), {"get": lambda self, k, d=None: d})()
    persona = {"factors": {"language": {"value": "hi-IN"}}, "teleprompter": {"opening_hook": "6 October ka ticket abhi khula hai, kya update mila?"}}
    line = teleprompter.opening({"contact_name": "Test Person", "synthetic": False}, persona, S, {"categories": ["Brass Lamp"]})
    assert line == ("Namaste Test ji, main Payal, IndiaMART ki virtual assistant. Aap Brass Lamp ka kaam karte hain. "
                    "6 October ka ticket abhi khula hai, kya update mila?")
    persona["teleprompter"]["opening_hook"] = "Brass lamp ki enquiry aayi hai, kya baat karein?"
    assert "ka kaam karte hain" not in teleprompter.opening({"contact_name": "", "synthetic": False}, persona, S, {"categories": ["Brass Lamp"]})
