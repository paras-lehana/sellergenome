"""5.11.3: Basic details from the Global Context answer, PIM as the default hot-lead type, and no empty fields.

Synthetic fixtures only (shaped like the real getContext answer seen on 2026-10-10); no network.
"""

from backend.genome import brief, contract, roster


def _results(kyc: dict, activity: dict | None = None) -> dict:
    return {"context.global": {"status": "ok", "origin": "live_api", "response": {"code": 200, "message": "Success", "data": {"mapping_id": "m-1"}}},
            "context.getcontext": {"status": "ok", "origin": "live_api", "response": {"code": 200, "message": "Success",
                                                                                      "data": {"kycdetails": kyc, "activitydetails": activity or {}}}}}


def _run(results: dict) -> dict:
    seller = roster.profile("real-12345")
    return {"contract": contract.assemble(seller, results, "run-x"), "seller": roster.summary(seller), "persona": {}, "results": results}


def _rows(run: dict) -> dict:
    return {r["label"]: r for r in brief.basics(run)["rows"]}


def test_turnover_tenure_and_business_type_come_from_global_context():
    rows = _rows(_run(_results({"gst_turnover": "40 L - 1.5 Cr", "registered_since_with_indiamart": "1 Y 4 M", "company_type": "Proprietor"})))
    assert rows["Turnover"]["value"] == "40 L - 1.5 Cr"
    assert rows["Turnover"]["api"] == "context.getcontext" and rows["Turnover"]["pointer"] == "/data/kycdetails/gst_turnover"
    assert rows["On IndiaMART"]["value"] == "1 year 4 months" and rows["On IndiaMART"]["api"] == "context.getcontext"
    assert rows["Business type"]["value"] == "Proprietor"


def test_na_turnover_counts_as_empty_and_the_row_is_left_out():
    rows = _rows(_run(_results({"gst_turnover": "NA", "registered_since_with_indiamart": "0 Y 7 M"})))
    assert "Turnover" not in rows
    assert rows["On IndiaMART"]["value"] == "7 months"


def test_hot_lead_type_defaults_to_pim_and_a_real_type_wins():
    run = _run(_results({"company_type": "Proprietor"}))
    field = run["contract"]["fields"]["data_type"]
    assert field["value"] == "PIM" and field["origin"] == "default" and field["availability"] == "present"
    assert _rows(run)["Hot lead type"]["value"] == "PIM" and _rows(run)["Hot lead type"]["note"] == "default"
    typed = _rows(_run(_results({}, {"data_type": "UA"})))["Hot lead type"]
    assert typed["value"] == "UA" and typed["note"] == ""
    blank = _run(_results({}, {"data_type": ""}))["contract"]["fields"]["data_type"]
    assert blank["value"] == "PIM", "a blank answer gets the default too"


def test_fields_without_data_are_left_out_and_no_data_gaps_group():
    run = _run(_results({"gst_turnover": "NA"}))
    rows = brief.basics(run)["rows"]
    assert rows and all(not r["missing"] for r in rows)
    labels = {r["label"] for r in rows}
    assert not {"Seller", "Company", "Turnover"} & labels  # a private seller with no company record: nothing to show
    assert all(g["key"] != "data_gaps" for g in brief.engagement(run)["groups"])


def test_tenure_words():
    assert brief._tenure_words("1 Y 4 M") == "1 year 4 months"
    assert brief._tenure_words("2 Y 0 M") == "2 years"
    assert brief._tenure_words("0 Y 1 M") == "1 month"
    assert brief._tenure_words("since 2019") == "since 2019"
