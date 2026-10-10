"""The production persona contract (owner point 1).

``assemble(results)`` turns API results into the 28 vendor fields + outer ``custtypeweight`` that
IndiaMART's hot-lead pipeline sends today, plus the extras the persona needs. Every field is an
envelope saying **where the value came from** (origin), which API and which JSON pointer, and
whether it is present, blank or missing. See docs/architecture/production-persona-contract.md.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from backend.genome import parsers

VENDOR_FIELDS = [
    "is_dev", "lead_id", "glid", "phone_number", "contact_name", "business_nature", "company_name", "city", "state",
    "bot_flag_id", "last_call_summary", "pns_received_30d", "pns_picked_30d", "local_buylead_count",
    "national_buylead_count", "enquiries_received_30d", "products", "competitor", "data_type", "custtype",
    "retry_flag", "sub_data_type", "last_call_start_time", "last_meeting_date", "lead_locality_1",
    "lead_locality_2", "personalise", "insights",
]
OUTER_FIELDS = ["custtypeweight"]
ALL_FIELDS = VENDOR_FIELDS + OUTER_FIELDS

FIELD_GROUPS = {
    "Identity & contact": ["is_dev", "lead_id", "glid", "phone_number", "contact_name"],
    "Company": ["business_nature", "company_name", "city", "state", "products", "competitor"],
    "Engagement (30 days)": ["pns_received_30d", "pns_picked_30d", "local_buylead_count", "national_buylead_count", "enquiries_received_30d"],
    "History": ["bot_flag_id", "last_call_summary", "last_call_start_time", "last_meeting_date"],
    "Classification": ["data_type", "sub_data_type", "custtype", "custtypeweight", "retry_flag"],
    "Location": ["lead_locality_1", "lead_locality_2"],
    "Personalisation": ["personalise", "insights"],
}

ORIGINS = {
    "live_api": "Live API call in this run",
    "elastic_cache": "Earlier API response read back from Elasticsearch",
    "local_db": "Supplied dataset (private, real)",
    "supplied_export": "Owner-supplied production export",
    "mock": "Production-shaped mock (real API unreachable or not supplied)",
    "llm": "Derived by Sarvam from other fields",
    "default": "Documented default — nothing available",
    "blank": "Source answered with an empty value",
}

INSIGHT_CATEGORIES = [
    "purchase_intent", "objection_or_blocker", "last_interaction", "market_opportunity",
    "competitor_landscape", "engagement_history", "catalog_gaps",
]


def pointer_get(document: Any, pointer: str) -> Any:
    """RFC 6901 JSON pointer lookup; returns None when the path does not exist."""
    node = document
    for raw in [p for p in pointer.split("/") if p != ""]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _availability(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, str) and value.strip() == "":
        return "blank"
    if isinstance(value, (list, dict)) and not value:
        return "blank"
    return "present"


def envelope(name: str, value: Any, origin: str, api: str | None, pointer: str | None, result: dict | None = None,
             note: str = "") -> dict:
    availability = _availability(value)
    if availability == "blank" and origin not in ("default",):
        shown_origin = "blank"
    else:
        shown_origin = origin
    return {
        "field": name, "value": value, "availability": availability, "origin": shown_origin,
        "source_origin": origin, "api": api, "pointer": pointer,
        "retrieved_at": (result or {}).get("retrieved_at"),
        "cached_origin": (result or {}).get("cached_origin"), "cached_at": (result or {}).get("cached_at"),
        "note": note,
    }


# Each field: ordered candidates (api id, JSON pointer or extractor). First present value wins.
Extractor = Callable[[Any], tuple[Any, str]]


def _products(resp: Any) -> tuple[Any, str]:
    items = pointer_get(resp, "/DATA/PRDSERV") or []
    return (", ".join(p.get("ITEM_NAME", "") for p in items) if items else None), "/DATA/PRDSERV/*/ITEM_NAME"


def _first_call(field: str) -> Extractor:
    def get(resp: Any) -> tuple[Any, str]:
        calls = (resp or {}).get("calls") or []
        return (calls[0].get(field) if calls else None), f"/calls/0/{field}"
    return get


def _redash_first(field: str) -> Extractor:
    def get(resp: Any) -> tuple[Any, str]:
        rows = (resp or {}).get("rows") or []
        return (rows[0].get(field) if rows else None), f"/rows/0/{field}"
    return get


def _enquiries_30d(resp: Any) -> tuple[Any, str]:
    rows = parsers.enquiries(resp)
    if not rows:
        return None, "/response/enquiries"
    recent = sum(1 for e in rows if parsers.within_days(_parse_iso(e["at"]), 30))
    return recent, "/response/enquiries/*/postedDate (count ≤ 30 days; the API returns the latest 10)"


def _pns_30d(resp: Any) -> tuple[Any, str]:
    calls = [c for c in parsers.pns_calls(resp, _GLID.get("glid", "")) if c["role"] == "seller"]
    if resp is None:
        return None, "/data"
    return sum(1 for c in calls if parsers.within_days(_parse_ts(c["at"]), 30)), "/data/*/call_timestamp (SELLER_GLID = this seller, page 1)"


def _last_meeting(resp: Any) -> tuple[Any, str]:
    meetings = [e for e in parsers.sts_entries(resp) if e["kind"] == "meeting"]
    return (meetings[0]["at"][:10] if meetings else None), "/0/data/0 (STS entry mentioning a meeting)"


def _parse_iso(text: Any):
    try:
        return datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _parse_ts(text: Any):
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M:%S").replace(tzinfo=parsers.IST)
    except (TypeError, ValueError):
        return None


_GLID: dict[str, str] = {}  # set per assemble() call — PNS rows are mixed-party and need the seller's GLID


def _insights_json(resp: Any) -> tuple[Any, str]:
    doc = pointer_get(resp, "/Data/Insights")
    return (json.dumps(doc, ensure_ascii=False) if doc else None), "/Data/Insights"


def _gc_last_meeting(resp: Any) -> tuple[Any, str]:
    """Latest ``meet_sucessful_date`` in the Global Context connect rows (the guide: count meetings only from it)."""
    conn = pointer_get(resp, "/data/connectdetails") or {}
    dates = sorted((r.get("meet_sucessful_date") for w in ("connect_7D", "connect_30D") for r in (conn.get(w) or [])
                    if isinstance(r, dict) and str(r.get("meet_sucessful_date") or "").strip() not in ("", "NA", "None")), reverse=True)
    return (dates[0], "/data/connectdetails/connect_30D/*/meet_sucessful_date") if dates else (None, "")


CANDIDATES: dict[str, list[tuple[str, Any]]] = {
    "contact_name": [("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_CONTACTPERSON"),
                     ("context.insights", "/Data/Insights/seller_summary/contact_name")],
    "company_name": [("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_COMPANY")],
    "phone_number": [("company.detail", "/DATA/COMPANYDETAIL/MOBILE")],
    # A live company answer is fresher than the supplied export; a mock never outranks the real dataset.
    "business_nature": [("company.detail", "/DATA/COMPANYDETAIL/BIZ", "live"), ("dataset.seller", "/row/nature_of_business"), ("dataset.seller", "/row/business_type"),
                        ("company.detail", "/DATA/COMPANYDETAIL/BIZ")],
    "city": [("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_CITY", "live"), ("dataset.seller", "/row/seller_city"),
             ("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_CITY")],
    "state": [("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_STATE", "live"), ("dataset.seller", "/row/seller_state"),
              ("company.detail", "/DATA/COMPANYDETAIL/DIR_SEARCH_STATE")],
    "products": [("company.detail", _products)],
    "competitor": [("context.global", "/data/activitydetails/competitor")],
    # Documented Global Context keys first (Consolidated API Guide, 2026-10-09), then the earlier production names.
    "pns_received_30d": [("context.global", "/data/activitydetails/pns_received_30_days"), ("context.global", "/data/activitydetails/pns_received_30d"),
                         ("pns.insights", _pns_30d)],
    "pns_picked_30d": [("context.global", "/data/activitydetails/last_4_week/pns_call_picked"), ("context.global", "/data/activitydetails/pns_picked_30d")],
    "local_buylead_count": [("context.global", "/data/activitydetails/top3mcat_data/0/local_bl"), ("context.global", "/data/activitydetails/local_buylead_count")],
    "national_buylead_count": [("context.global", "/data/activitydetails/top3mcat_data/0/total_bl"),
                               ("context.global", "/data/activitydetails/national_buylead_count")],
    "enquiries_received_30d": [("context.global", "/data/activitydetails/enq_last_30_days"), ("context.global", "/data/activitydetails/enquiries_received_30d"),
                               ("enquiry.list", _enquiries_30d)],
    "bot_flag_id": [("context.global", "/data/activitydetails/bot_flag_id")],
    "last_call_summary": [("redash.hotlead", _redash_first("lead_call_summary")), ("dataset.calls", _first_call("lead_call_summary")),
                          ("context.global", "/data/connectdetails/last_call_summary"), ("context.global", "/data/activitydetails/last_call_summary")],
    "last_call_start_time": [("redash.hotlead", _redash_first("call_start_time")), ("dataset.calls", _first_call("call_start_time")),
                             ("context.global", "/data/activitydetails/last_call_start_time")],
    "last_meeting_date": [("context.global", _gc_last_meeting), ("context.global", "/data/activitydetails/last_meeting_date"), ("sts.history", _last_meeting)],
    "data_type": [("context.global", "/data/activitydetails/data_type")],
    "sub_data_type": [("context.global", "/data/activitydetails/sub_data_type")],
    "retry_flag": [("context.global", "/data/activitydetails/retry_flag")],
    "custtype": [("context.global", "/data/kycdetails/customer_type"), ("context.global", "/data/kycdetails/custtype")],
    "custtypeweight": [("company.detail", "/DATA/COMPANYDETAIL/CUSTWT"), ("company.alias", "/URL_DETAIL/CUSTWT"),
                       ("context.global", "/data/kycdetails/custtype_weight")],
    "lead_locality_1": [("company.detail", "/DATA/COMPANYDETAIL/GLUSR_USR_LOCALITY")],
    "insights": [("context.insights", _insights_json)],
}
# 5.10.1: generateContextUID (context.global) may answer with the context UID alone, or KYC only; getContext
# (data_segments=all) answers in the same data.{…} shape — so every Global Context source is also tried there, right after.
CANDIDATES = {name: [c for src in sources for c in ([src, ("context.getcontext", *src[1:])] if src[0] == "context.global" else [src])]
              for name, sources in CANDIDATES.items()}
GC_SEGMENTS = ("kycdetails", "connectdetails", "ticketsdetails", "activitydetails", "BLdetails")


def global_context_source(results: dict) -> str:
    """The API whose answer holds the Global Context segments: getContext when it carries more of them than
    generateContextUID (``context.global``) does, else context.global."""
    def count(api_id: str) -> int:
        r = results.get(api_id) or {}
        data = (r.get("response") or {}).get("data") if r.get("status") == "ok" and isinstance(r.get("response"), dict) else None
        return sum(1 for k in GC_SEGMENTS if isinstance(data, dict) and data.get(k))
    return "context.getcontext" if count("context.getcontext") > count("context.global") else "context.global"


DEFAULTS = {"is_dev": False, "lead_locality_2": "", "personalise": "false",
            # Owner, 2026-10-10: a hot lead with no type is a PIM (Payment Interested) lead. Real sellers' Global Context
            # carries no data_type at all, so without this the field was always empty.
            "data_type": "PIM"}
DEFAULT_NOTES = {"data_type": "Default hot-lead type when the record gives none"}


def classify(weight: Any) -> str:
    """custtypeweight: < 1000 paid, > 1000 free, exactly 1000 or missing → unknown."""
    try:
        w = int(weight)
    except (TypeError, ValueError):
        return "unknown"
    return "paid" if w < 1000 else "free" if w > 1000 else "unknown"


def classify_customer(customer_type: Any, weight: Any) -> str:
    text = str(customer_type or "").strip().lower()
    if text in ("paid", "free"):
        return text
    return classify(weight)


def assemble(seller: dict, results: dict[str, dict], lead_id: str) -> dict:
    """Build the field table and extras from a run's API results (keyed by api id)."""
    def ok(api_id: str) -> dict | None:
        r = results.get(api_id)
        return r if r and r.get("status") == "ok" and r.get("response") is not None else None

    _GLID["glid"] = str(seller["glid"])
    fields: dict[str, dict] = {}
    for name in ALL_FIELDS:
        chosen = None
        for api_id, getter, *only in CANDIDATES.get(name, []):
            r = ok(api_id)
            if not r:
                continue
            if only and only[0] == "live" and _effective_origin(r) != "live_api":
                continue
            if callable(getter):
                value, pointer = getter(r["response"])
            else:
                value, pointer = pointer_get(r["response"], getter), getter
            if value is None:
                continue
            chosen = envelope(name, value, r["origin"], api_id, pointer, r)
            if chosen["availability"] == "present":
                break  # a blank from one source lets the next source try
        if name == "glid":
            chosen = envelope(name, seller["glid"], "supplied_export" if seller.get("kind") == "export" else "default",
                              None, None, note="Requested identifier")
            chosen["origin"] = "request"
        elif name == "lead_id":
            chosen = envelope(name, lead_id, "default", None, None, note="Generated for this preparation run")
        if name in DEFAULTS and (chosen is None or chosen["availability"] != "present"):  # a blank answer gets the default too
            chosen = envelope(name, DEFAULTS[name], "default", None, None, note=DEFAULT_NOTES.get(name, "Documented default"))
        if chosen is None:
            chosen = envelope(name, None, "default", None, None, note="No source returned this field")
        fields[name] = chosen

    # personalise = true only when there is something real to personalise with.
    insights_doc = pointer_get(ok("context.insights")["response"], "/Data/Insights") if ok("context.insights") else None
    fields["personalise"] = envelope("personalise", "true" if insights_doc else "false",
                                     "llm" if insights_doc else "default", "context.insights" if insights_doc else None,
                                     None, note="Derived: insights available" if insights_doc else "No insights")

    extras = _extras(seller, results, ok, insights_doc)
    present = sum(1 for f in fields.values() if f["availability"] == "present")
    by_origin: dict[str, int] = {}
    for f in fields.values():
        by_origin[f["origin"]] = by_origin.get(f["origin"], 0) + 1
    return {
        "fields": fields,
        "groups": FIELD_GROUPS,
        # Paid/Free straight from Global Context's customer_type when present (guide); else the custtypeweight rule.
        "classification": classify_customer(fields["custtype"]["value"], fields["custtypeweight"]["value"]),
        "completeness": {"present": present, "total": len(fields), "by_origin": by_origin},
        **extras,
    }


def _effective_origin(result: dict) -> str:
    """live_api for a live answer, including one read back from the Elastic cache."""
    return result.get("cached_origin") or result.get("origin") if result.get("origin") == "elastic_cache" else result.get("origin")


def _keyed(results: dict, prefix: str) -> list[tuple[str, dict]]:
    """Results of fan-out nodes such as ``leads.detail:<offer>`` and ``lms.messages:<contact>``."""
    return [(k, r) for k, r in results.items() if k.startswith(prefix + ":") and r.get("status") == "ok" and r.get("response") is not None]


def _extras(seller: dict, results: dict, ok, insights_doc) -> dict:
    company = ok("company.detail")
    detail = pointer_get(company["response"], "/DATA/COMPANYDETAIL") if company else {}
    factsheet = pointer_get(company["response"], "/DATA/FACTSHEET") if company else []
    gst_address = None
    for sheet in factsheet or []:
        for row in sheet.get("DATA", []):
            if "GST" in row.get("TITLE", "") and row.get("DATA"):
                gst_address = row["DATA"]
    ctx_api = global_context_source(results)
    ctx = ok(ctx_api)
    if ctx and not gst_address:
        gst_address = pointer_get(ctx["response"], "/data/kycdetails/gst_address")
    addresses = []
    if detail and detail.get("GLUSR_USR_ADD1"):
        addresses.append({"kind": "profile", "label": "Profile address (Company API)", "text": detail["GLUSR_USR_ADD1"],
                          "origin": company["origin"], "api": "company.detail", "pointer": "/DATA/COMPANYDETAIL/GLUSR_USR_ADD1"})
    if gst_address:
        addresses.append({"kind": "gst", "label": "GST address", "text": gst_address,
                          "origin": (company or ctx)["origin"], "api": "company.detail" if company else ctx_api,
                          "pointer": "/DATA/FACTSHEET/1/DATA/0/DATA"})
    dataset = ok("dataset.seller")
    row = (dataset["response"] or {}).get("row") if dataset else None
    if row and (row.get("seller_locality") or row.get("seller_city")):
        addresses.append({"kind": "dataset", "label": "Dataset locality", "text": ", ".join(
            x for x in [row.get("seller_locality"), row.get("seller_district"), row.get("seller_city"), row.get("seller_state"), row.get("seller_pincode")] if x),
            "origin": "local_db", "api": "dataset.seller", "pointer": "/row/seller_locality"})

    company_facts = {}
    for si, sheet in enumerate(factsheet or []):
        for ri, row in enumerate((sheet or {}).get("DATA") or []):
            title, value = (row or {}).get("TITLE"), (row or {}).get("DATA")
            if title and value not in (None, "") and title not in company_facts:
                company_facts[title] = {"value": value, "origin": company["origin"], "api": "company.detail",
                                        "pointer": f"/DATA/FACTSHEET/{si}/DATA/{ri}/DATA"}
    for key in ("LEGALSTATUS", "NUMBEROFEMP_VAL", "YOE"):
        if detail and detail.get(key) not in (None, "") and key not in company_facts:
            company_facts[key] = {"value": detail[key], "origin": company["origin"], "api": "company.detail", "pointer": f"/DATA/COMPANYDETAIL/{key}"}

    products = []
    for p in (pointer_get(company["response"], "/DATA/PRDSERV") if company else []) or []:
        products.append({"name": p.get("ITEM_NAME"), "price": p.get("PC_ITEM_FOB_PRICE_FORMATTED") or p.get("PC_ITEM_FOB_PRICE") or None,
                         "unit": p.get("PC_ITEM_MOQ_UNIT_TYPE"),
                         "has_image": bool(p.get("ITEM_IMG_SMALL") or p.get("ITEM_SIMG") or p.get("ITEM_BIMG") or p.get("PC_ITEM_IMG_SMALL")),
                         "specs": {i.get("FK_IM_SPEC_MASTER_DESC"): i.get("SUPPLIER_RESPONSE_DETAIL") for i in (p.get("ISQ") or [])
                                   if isinstance(i, dict) and i.get("FK_IM_SPEC_MASTER_DESC")},
                         "origin": company["origin"]})

    insights = []
    doubts = []
    seller_summary = {}
    if insights_doc:
        r = ok("context.insights")
        seller_summary = insights_doc.get("seller_summary", {})
        for i, item in enumerate(insights_doc.get("insights", [])):
            insights.append({**item, "origin": r["origin"], "api": "context.insights", "pointer": f"/Data/Insights/insights/{i}"})
        for i, d in enumerate(insights_doc.get("doubts", [])):
            doubts.append({**d, "origin": r["origin"], "api": "context.insights", "pointer": f"/Data/Insights/doubts/{i}"})

    glid = str(seller["glid"])
    # ---- who the seller dealt with: enquiries, purchased buy leads, PNS calls (seller side only) ----
    enquiries = []
    enq = ok("enquiry.list")
    for e in parsers.enquiries(enq["response"]) if enq else []:
        enquiries.append({**{k: v for k, v in e.items() if k != "name"}, "buyer_name": e.get("name"),
                          "origin": enq["origin"], "api": "enquiry.list", "pointer": f"/response/enquiries/{e['index']}"})
    leads = []
    for key, r in _keyed(results, "leads.detail"):
        item = parsers.lead(r["response"])
        if item and (item.get("title") or item.get("product")):
            leads.append({**{k: v for k, v in item.items() if k != "name"}, "buyer_name": item.get("name"), "offer_id": key.split(":", 1)[1],
                          "origin": r["origin"], "api": key, "pointer": "/RESPONSE/DATA"})
    pr = ok("pns.insights")
    pns_all = parsers.pns_calls(pr["response"], glid) if pr else []
    pns = [{**c, "origin": pr["origin"], "api": "pns.insights", "pointer": f"/data/{c['index']}/extracted_data"} for c in pns_all]
    buyers = []
    for e in enquiries:
        buyers.append({"glid": None, "channel": "enquiry", "product": e["product"], "last_at": e["at"], "name": e.get("buyer_name"),
                       "city": e.get("city"), "quantity": "; ".join(e.get("specs") or []) or None, "intent": None,
                       "origin": e["origin"], "detail_origin": e["origin"], "api": e["api"], "pointer": e["pointer"]})
    for l in leads:
        buyers.append({"glid": l["buyer_glid"], "channel": "buylead", "product": l["title"] or l["product"], "last_at": l["at"],
                       "name": l.get("buyer_name"), "city": l.get("buyer_city"), "quantity": l.get("quantity"), "intent": None,
                       "origin": l["origin"], "detail_origin": l["origin"], "api": l["api"], "pointer": l["pointer"]})
    for c in pns:
        if c["role"] == "seller":
            buyers.append({"glid": c["counterpart_glid"], "channel": "pns", "product": c["product"], "last_at": c["at"], "name": None,
                           "city": None, "quantity": c.get("quantity"), "intent": c.get("intent"), "origin": c["origin"],
                           "detail_origin": c["origin"], "api": c["api"], "pointer": c["pointer"]})
    buyers.sort(key=lambda b: str(b.get("last_at") or ""), reverse=True)

    history = []
    for api_id, label in (("dataset.calls", "Past bot call (dataset)"), ("redash.hotlead", "Bot call (Redash)")):
        r = ok(api_id)
        if not r:
            continue
        rows = r["response"].get("calls") if api_id == "dataset.calls" else r["response"].get("rows")
        for i, row in enumerate(rows or []):
            history.append({"kind": label, "at": row.get("call_start_time"), "outcome": row.get("disposition_label"),
                            "summary": row.get("lead_call_summary"), "duration_s": row.get("lead_call_duration"),
                            "origin": r["origin"], "api": api_id, "pointer": f"/{'calls' if api_id == 'dataset.calls' else 'rows'}/{i}"})
    sts = ok("sts.history")
    for i, h in enumerate(parsers.sts_entries(sts["response"]) if sts else []):
        history.append({"kind": f"STS · {h['kind']}", "at": h["at"], "summary": h["text"], "by": h["by"], "via": h["via"],
                        "origin": sts["origin"], "api": "sts.history", "pointer": f"/0/data/0 (entry {i + 1})"})
    for c in pns:
        if c["role"] == "seller":
            history.append({"kind": "PNS call", "at": c["at"], "outcome": c.get("outcome"), "summary": c.get("narrative") or c.get("outcome_note"),
                            "origin": c["origin"], "api": c["api"], "pointer": c["pointer"]})
    history.sort(key=lambda h: str(h.get("at") or ""), reverse=True)

    messages = []
    for key, r in _keyed(results, "lms.messages") + ([("lms.messages", ok("lms.messages"))] if ok("lms.messages") else []):
        contact = key.split(":", 1)[1] if ":" in key else ""
        for m in parsers.lms_messages(r["response"], glid, contact):
            messages.append({**m, "origin": r["origin"], "api": key, "pointer": f"/response/result/{m['index']}/msg_text_json"})
    messages.sort(key=lambda m: str(m.get("at") or ""), reverse=True)

    context = {}
    gc = ok("context.getcontext")
    if gc:
        context = {"segments": parsers.get_context_segments(gc["response"]), "message": (gc["response"] or {}).get("message"),
                   "origin": gc["origin"], "api": "context.getcontext"}

    signals = {}
    if row:
        for key in ("past_objections", "past_questions_asked", "past_dispositions_detailed", "annual_turnover",
                    "top_category_1", "top_category_2", "top_category_3", "bot_attempts", "bot_answered",
                    "calls_meeting_fixed", "calls_not_interested", "calls_call_later", "calls_general",
                    "avg_answered_call_sec", "eng_pickup_ratio_90d", "eng_enq_received_90d", "eng_enq_replies_90d",
                    "showed_frustration", "asked_if_talking_to_bot", "already_in_touch_with_executive", "do_not_call_requested"):
            if row.get(key) not in (None, ""):
                signals[key] = {"value": row[key], "origin": "local_db", "api": "dataset.seller", "pointer": f"/row/{key}"}

    packages = (ok("packages.catalog")["response"].get("packages", []) if ok("packages.catalog") else [])
    # What the seller deals in, as categories (the opening's "Aap … ka kaam karte hain"): the storefront's product
    # categories by product count, else the supplied dataset's top categories. A mock never speaks for a real seller.
    categories: list[str] = []
    detail = ok("company.detail")
    if detail and (seller.get("synthetic", True) or detail.get("origin") != "mock"):
        nav = [c for c in pointer_get(detail["response"], "/DATA/PRDNAV") or [] if isinstance(c, dict) and str(c.get("CAT_NAME") or "").strip()]
        nav.sort(key=lambda c: -_num(c.get("CAT_PRD_COUNT")))
        categories = [str(c["CAT_NAME"]).strip() for c in nav if not str(c["CAT_NAME"]).strip().lower().startswith("other")]
    if not categories and row:
        categories = [str(row[k]).strip() for k in ("top_category_1", "top_category_2") if str(row.get(k) or "").strip()]
    return {"addresses": addresses, "products": products, "insights": insights, "doubts": doubts,
            "seller_summary": seller_summary, "buyers": buyers, "history": history, "messages": messages,
            "pns_calls": pns, "enquiries": enquiries, "leads": leads, "global_context": context, "company_facts": company_facts,
            "dataset_signals": signals, "packages": packages, "categories": categories[:3]}


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def short_date(value: Any) -> str:
    """'2026-10-07T11:42:35+05:30' / '2026-10-06 10:57:18' / '2026-10-09' → '7 Oct' (empty if unknown)."""
    text = str(value or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not m:
        return ""
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{int(m.group(3))} {months[int(m.group(2)) - 1]}"


CHIP = {"company.detail": "Company", "company.alias": "Company", "context.global": "Global ctx", "context.getcontext": "Global ctx",
        "context.insights": "Insights", "dataset.seller": "Dataset", "dataset.calls": "Bot call", "redash.hotlead": "Bot call",
        "pns.insights": "PNS", "enquiry.list": "Enquiry", "sts.history": "STS", "packages.catalog": "Catalogue"}


def chip_label(item: dict) -> str:
    """Short evidence-chip text in the PM's style: 'STS 7 Oct', 'PNS 6 Oct', 'Enquiry 4 Oct', 'Company'."""
    api = (item.get("api") or "").split(":")[0]
    base = {"leads.detail": "Lead", "lms.messages": "Chat"}.get(api) or CHIP.get(api) or api or "Source"
    when = short_date(item.get("at"))
    return f"{base} {when}".strip()


def evidence_items(assembled: dict) -> list[dict]:
    items = _evidence_items(assembled)
    for item in items:
        item["label"] = chip_label(item)
    return items


def _evidence_items(assembled: dict) -> list[dict]:
    """Compact, PII-free evidence list for the LLM. Each item has an id the model must cite."""
    items = []
    skip = {"phone_number", "contact_name", "lead_id", "is_dev", "insights", "glid"}
    for name, f in assembled["fields"].items():
        if name in skip or f["availability"] != "present":
            continue
        value = f["value"] if not isinstance(f["value"], list) else "; ".join(map(str, f["value"][:6]))
        items.append({"id": f"f:{name}", "text": f"{name} = {str(value)[:240]}", "origin": f["origin"], "api": f["api"], "pointer": f["pointer"]})
    items.append({"id": "f:customer_class", "text": f"customer class from custtypeweight = {assembled['classification']}",
                  "origin": "llm", "api": "company.detail", "pointer": "/DATA/COMPANYDETAIL/CUSTWT"})
    for i, ins in enumerate(assembled["insights"]):
        if ins.get("headline"):
            items.append({"id": f"i:{ins['category']}", "text": f"{ins['category']}: {ins['headline']} {ins.get('detail', '')[:300]}",
                          "origin": ins["origin"], "api": ins["api"], "pointer": ins["pointer"]})
    for i, d in enumerate(assembled["doubts"]):
        items.append({"id": f"d:{i}", "text": f"doubt: {d['doubt'][:300]}", "origin": d["origin"], "api": d["api"], "pointer": d["pointer"]})
    for i, h in enumerate(assembled["history"][:6]):
        if h.get("summary"):
            items.append({"id": f"h:{i}", "text": f"{h['kind']} {h.get('at') or ''}: {h.get('outcome') or ''} — {str(h['summary'])[:220]}",
                          "origin": h["origin"], "api": h["api"], "pointer": h["pointer"], "at": h.get("at")})
    for i, b in enumerate(assembled["buyers"][:5]):
        items.append({"id": f"b:{i}", "text": f"buyer via {b.get('channel')}: {b.get('product')} {b.get('quantity') or ''} from {b.get('city') or '?'}, intent {b.get('intent') or '?'}",
                      "origin": b["origin"], "api": b["api"], "pointer": b["pointer"], "at": b.get("last_at")})
    for i, m in enumerate(assembled["messages"][:4]):
        items.append({"id": f"m:{i}", "text": f"chat {m['from']}: {m['text'][:200]}", "origin": m["origin"], "api": m["api"], "pointer": m["pointer"], "at": m.get("at")})
    for key, value in list((assembled.get("global_context") or {}).get("segments", {}).items())[:8]:
        g = assembled["global_context"]
        items.append({"id": f"g:{key}", "text": f"global context {key} = {parsers.scrub(json.dumps(value, ensure_ascii=False, default=str))[:220]}",
                      "origin": g["origin"], "api": g["api"], "pointer": f"/data/{key}"})
    for key, s in assembled["dataset_signals"].items():
        items.append({"id": f"s:{key}", "text": f"{key} = {s['value']}", "origin": "local_db", "api": "dataset.seller", "pointer": s["pointer"]})
    for i, p in enumerate(assembled["products"][:6]):
        items.append({"id": f"p:{i}", "text": f"product {p['name']} price {'shown' if p['price'] else 'NOT shown'}",
                      "origin": p.get("origin", "mock"), "api": "company.detail", "pointer": f"/DATA/PRDSERV/{i}"})
    return items
