"""Production-shaped mock responses for IndiaMART APIs that are unreachable from this server.

Shapes are copied from REAL responses seen through the gateway on 2026-10-09 (approved test GLIDs):
company alias + detail, buy-lead purchase + detail, enquiry list, PNS call insights, LMS conversation
list and STS history (an HTML blob). The Insights API shape comes from the production hot-lead log.
Where no successful response has been seen (generateContextUID, getContext, package catalogue) the shape is our
documented best guess and labelled as such in Admin.

Every function takes a seller profile from ``roster`` and returns the JSON body an upstream would.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

IST = timezone(timedelta(hours=5, minutes=30))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def _masked_phone(glid: str) -> str:
    # Clearly synthetic and non-dialable: the middle digits are masked.
    return f"9XXXXX{glid[-4:]}"


def company_alias(p: dict) -> dict:
    return {
        "URL_DETAIL": {
            "GLUSR_USR_ID": int(p["glid"]),
            "FREESHOWROOM_ALIAS": _slug(p["company_name"]),
            "FREE_SHOWROOM_URL": f"https://www.indiamart.com/{_slug(p['company_name'])}/",
            "GLUSR_USR_CUSTTYPE_NAME": p.get("custtype_name", ""),
            "USER_MEMBER_SINCE": f"{max(1, p.get('tenure_days', 365) // 365)} yrs",
            "MOBILE_VERIFIED": 1, "EMAIL_VERIFIED": 1, "URL_STATUS": "200",
        },
        "DATA": {}, "STANDARD_PRODUCTS": [],
    }


def company_detail(p: dict) -> dict:
    products = p.get("products", [])
    mcat = (p.get("top_mcats") or [{"mcat_name": "General"}])[0]["mcat_name"]
    return {
        "DATA": {
            "COMPANYDETAIL": {
                "GLUSRID": int(p["glid"]),
                "DIR_SEARCH_COMPANY": p["company_name"],
                "DIR_SEARCH_CONTACTPERSON": p["contact_name"],
                "BIZ": p.get("business_nature", ""),
                "LEGALSTATUS": p.get("legal_status", ""),
                "NUMBEROFEMP_VAL": p.get("employees", ""),
                "CUSTWT": p.get("custtypeweight"),
                "CATNAME": mcat,
                "DIR_SEARCH_ADDRESS": p.get("locality", ""),
                "DIR_SEARCH_ADDRESS1": p.get("address_profile", "").split(",")[0],
                "DIR_SEARCH_ADDRESS2": ", ".join(p.get("address_profile", "").split(",")[1:3]).strip(),
                "DIR_SEARCH_CITY": p["city"],
                "DIR_SEARCH_STATE": p.get("state", ""),
                "DIR_SEARCH_PIN": p.get("pincode", ""),
                "DIR_SEARCH_COUNTRY": "India",
                "GLUSR_USR_ADD1": p.get("address_profile", ""),
                "GLUSR_USR_ADD2": "",
                "GLUSR_USR_LOCALITY": p.get("locality", ""),
                "MOBILE": _masked_phone(p["glid"]),
            },
            "BREADCRUMB": {"MCAT_NAME": mcat, "GRP_NAME": mcat},
            "FACTSHEET": [
                {"TITLE": "Basic Information", "DATA": [
                    {"TITLE": "Nature of Business", "DATA": p.get("business_nature", "")},
                    {"TITLE": "Total Number of Employees", "DATA": p.get("employees", "")},
                    {"TITLE": "Year of Establishment", "DATA": p.get("year_established", "")},
                    {"TITLE": "Legal Status of Firm", "DATA": p.get("legal_status", "")},
                    {"TITLE": "Annual Turnover", "DATA": p.get("turnover_band", "")},
                ]},
                {"TITLE": "GST Information", "DATA": [
                    {"TITLE": "GST Registration Address", "DATA": p.get("address_gst", "")},
                ]},
            ],
            "PRDSERV": [
                {
                    "ITEM_ID": int(hashlib.sha256((p["glid"] + x["name"]).encode()).hexdigest()[:8], 16) % 900000000,
                    "ITEM_NAME": x["name"],
                    "PC_ITEM_FOB_PRICE_FORMATTED": x.get("price") or "",
                    "PC_ITEM_MOQ_UNIT_TYPE": x.get("unit", ""),
                    "ITEM_SDESC": "" if not x.get("price") else f"{x['name']} from {p['company_name']}.",
                    "ISQ": [{"FK_IM_SPEC_MASTER_DESC": k, "SUPPLIER_RESPONSE_DETAIL": v} for k, v in (x.get("isq") or {}).items()],
                }
                for x in products
            ],
            "PRD_COUNT": sum(m.get("product_count", 0) for m in p.get("top_mcats", [])) or len(products),
        },
        "URL_DETAIL": company_alias(p)["URL_DETAIL"],
    }


def global_context(p: dict) -> dict:
    """Global Context (``context.global``; generateContextUID since 5.10.1, userCtx before) in the shape documented by
    the Consolidated API Guide (2026-10-09): ``data.{kycdetails, connectdetails, ticketsdetails, activitydetails, BLdetails}``
    plus the mapping_id. No live response has been seen yet, so the values are synthetic; the keys and formats are the guide's.
    The archetype shapes the story: frustrated → an open ticket, already with an executive → a recent meeting with
    pending work, uninterested → unanswered calls, rushed → a promised callback. Legacy production keys stay in
    ``activitydetails`` for the vendor contract."""
    import json as _json
    import random as _random
    c = p.get("counts", {})
    rng = _random.Random(f"gc:{p['glid']}")
    today = datetime.now(IST).replace(microsecond=0)
    day = lambda n: (today - timedelta(days=n))  # noqa: E731
    paid = int(p.get("custtypeweight") or 1500) < 1000
    tenure_days = int(p.get("tenure_days") or 400)
    archetype = p.get("archetype", "curious")
    first = (p.get("contact_name") or "").split(" ")[0]
    lang = p.get("language", "hi-IN")
    hindi_words = {"hi-IN": "haan ji boliye, abhi thoda kaam mein hoon", "ta-IN": "சரி சொல்லுங்க, இப்போ கொஞ்சம் வேலை", "te-IN": "చెప్పండి, ఇప్పుడు కొంచెం పని ఉంది",
                   "en-IN": "yes please tell me, I am a bit busy"}.get(lang, "haan ji boliye")
    connect_7, connect_30, tickets_current, tickets_7, tickets_90, wip = [], [], [], [], [], []
    # One answered call with a summary in the last 7 days for most sellers.
    if archetype != "uninterested":
        connect_7.append({"call_answered_date": day(rng.randint(1, 6)).strftime("%Y-%m-%d %H:%M:%S"),
                          "call_answered_summary": (p.get("last_call") or {}).get("summary", ""),
                          "call_answered_transcript": f"Executive: Namaste {first} ji, IndiaMART se. Customer: {hindi_words}. Executive: ji.",
                          "call_sts_update": "Seller asked to call back in the evening" if archetype == "rushed" else "Discussed buy lead usage",
                          "meet_sts_update": "", "meet_sucessful_date": "", "video_meet_sts_update": "", "video_verified_date": ""})
    else:
        for n in (3, 9, 16):
            connect_30.append({"call_answered_date": day(n).strftime("%Y-%m-%d %H:%M:%S"), "call_answered_summary": "", "call_answered_transcript": "",
                               "call_sts_update": "Not reachable / call not answered", "meet_sts_update": "", "meet_sucessful_date": "",
                               "video_meet_sts_update": "", "video_verified_date": ""})
    if archetype in ("already_with_executive", "interested"):
        meet = day(rng.randint(8, 20))
        connect_30.append({"call_answered_date": meet.strftime("%Y-%m-%d %H:%M:%S"), "call_answered_summary": "", "call_answered_transcript": "",
                           "call_sts_update": "", "meet_sucessful_date": meet.strftime("%Y-%m-%d"),
                           "meet_sts_update": ("01 Meeting Agenda: Buy lead usage review 02 Decision Maker Name & No: [withheld] 03 Major Products/Categories: "
                                               f"{(p.get('products') or [{'name': 'products'}])[0]['name']} 04 Meeting Summary & Work Done: explained BL filters "
                                               "05 ROI/Business Generated in last 6 Months: NA 06 Work to be Done Post Meeting: need to follow up on catalogue photos "
                                               "07 Comments/Pending work: NA"),
                           "video_meet_sts_update": "", "video_verified_date": ""})
    if archetype == "frustrated":
        wip.append({"ticket_type": "Lead Quality", "ticket_issue_date": day(4).strftime("%d-%b-%Y"), "ticket_opening_comments": "[content withheld]"})
        tickets_current.append(wip[0])
    if archetype == "price_sensitive":
        tickets_90.append({"ticket_type": "Payment Related", "ticket_issue_date": day(40).strftime("%d-%b-%Y"), "ticket_opening_comments": "[content withheld]"})
    picked = c.get("pns_picked_30d") or rng.randint(2, 12)
    received = max(picked, c.get("pns_received_30d") or picked + rng.randint(0, 6))
    active = {"rushed": 26, "interested": 22, "curious": 14, "confused": 6, "uninterested": 2, "frustrated": 9,
              "price_sensitive": 12, "already_with_executive": 18, "language_switch": 15}.get(archetype, 12)
    products = p.get("products") or []
    unpriced = sum(1 for x in products if not x.get("price"))
    mcats = [{"mcat_name": m.get("mcat_name"), "mcat_id": 1000 + i} for i, m in enumerate(p.get("top_mcats") or [])]
    reg_y, reg_m = divmod(tenure_days // 30, 12)
    return {
        "status": 200, "glid": int(p["glid"]), "mapping_id": str(uuid5(NAMESPACE_URL, f"sellergenome-mock:{p['glid']}")),
        "data": {
            "glid": int(p["glid"]),
            "kycdetails": {
                "company_name": p.get("company_name"), "customer_name": p.get("contact_name"), "first_name": first, "city": p.get("city"),
                "gst_turnover": {"Upto ₹40 lakh": "0 - 40 L", "₹40 lakh – 1.5 crore": "40 L - 1.5 Cr", "₹1 – 5 crore": "1.5 - 5 Cr"}.get(p.get("turnover_band"), "NA"),
                "gst_nature_of_business": p.get("business_nature"), "company_type": (p.get("legal_status") or "").split(" - ")[-1] or "Proprietor",
                "registered_since_with_indiamart": f"{reg_y} Y {reg_m} M", "customer_type": "Paid" if paid else "Free",
                "top_city_rest_of_india": ", ".join(sorted({b.get("city") for b in p.get("buyers") or [] if b.get("city")})[:3]),
                "approved_product_count": str(len(products)),
                "payment_invoice_date": day(120).strftime("%d-%m-%Y") if paid else "NA", "payment_receipt_date": day(118).strftime("%Y-%m-%d") if paid else "",
                "last_call_details": [{"COMMENTS": (p.get("last_call") or {}).get("summary", ""), "ENTERED_ON": (p.get("last_call") or {}).get("start", "")}]
                if (p.get("last_call") or {}).get("summary") else [],
                # legacy production keys (vendor contract)
                "custtype": p.get("custtype"), "custtype_weight": p.get("custtypeweight"), "custtype_name": p.get("custtype_name"),
                "gst_address": p.get("address_gst", ""),
            },
            "connectdetails": {"connect_7D": connect_7, "connect_30D": connect_30 + connect_7,
                               "last_call_summary": (p.get("last_call") or {}).get("summary", "") if archetype != "uninterested" else ""},
            "ticketsdetails": {"wip_tickets": wip, "current": {"tickets": tickets_current}, "last_7_days": {"tickets": tickets_7},
                               "last_8_to_90_days": {"tickets": tickets_90 or None}},
            "activitydetails": {
                "enq_last_30_days": c.get("enquiries_received_30d") or rng.randint(1, 20), "enq_last_90_days": (c.get("enquiries_received_30d") or 5) * 3,
                "pns_received_30_days": received, "pns_received_90_days": received * 3,
                "last_week": {"bl_active_days": min(7, active // 4), "bl_cons": active // 3},
                "last_4_week": {"bl_active_days": active, "bl_cons": active * 3, "pns_call_picked": picked, "pns_call_received": received,
                                "prd_without_photo": rng.randint(0, max(1, len(products) // 2)), "prd_without_price": unpriced, "prd_without_ctg_mapping": 0,
                                "location_preference": [p.get("state") or "Pan India"]},
                "last_month": {"bl_active_days": active + 1, "bl_cons": active * 3 + 2, "prd_without_photo": 1, "prd_without_price": unpriced,
                               "location_preference": p.get("state") or "Pan India"},
                "last_3_month": {"bl_active_days": min(90, active * 3), "bl_cons": active * 9},
                "top3mcat_data": [{"local_bl": c.get("local_buylead_count") or rng.randint(5, 60), "total_bl": c.get("national_buylead_count") or rng.randint(80, 900),
                                   "local_paid_sellers": rng.randint(5, 40), "total_paid_sellers": rng.randint(100, 900), "mcat_list": _json.dumps(mcats)}],
                "top3mcat_competitors": [{"company": x.split(":")[0], "city": p.get("city"), "tier": "Star", "member_since": x.split(":")[-1].strip()}
                                         for x in p.get("competitors") or []],
                # legacy production keys (vendor contract / NSD bot)
                "data_type": p.get("data_type", "PIM"), "sub_data_type": "", "retry_flag": 0, "bot_flag_id": 0,
                "pns_received_30d": c.get("pns_received_30d"), "pns_picked_30d": c.get("pns_picked_30d"),
                "local_buylead_count": c.get("local_buylead_count"), "national_buylead_count": c.get("national_buylead_count"),
                "enquiries_received_30d": c.get("enquiries_received_30d"), "competitor": p.get("competitors", []),
                "last_meeting_date": p.get("last_meeting_date", ""), "last_call_summary": (p.get("last_call") or {}).get("summary", ""),
                "last_call_start_time": (p.get("last_call") or {}).get("start", ""),
            },
            "BLdetails": {"total_bl_available": c.get("national_buylead_count") or rng.randint(80, 900), "local_bl_available": c.get("local_buylead_count") or 10,
                          "active_bl_details": [{"mcat": m["mcat_name"]} for m in mcats][:3]},
        },
    }


def insights(p: dict) -> dict:
    """Insights API, exactly as printed in the production hot-lead log."""
    items = []
    for i, category in enumerate(["purchase_intent", "objection_or_blocker", "last_interaction", "market_opportunity",
                                  "competitor_landscape", "engagement_history", "catalog_gaps"], start=1):
        headline, detail = (p.get("insights") or {}).get(category, ["", ""])
        items.append({"id": i, "category": category, "headline": headline, "detail": detail})
    return {
        "Status": 200,
        "Data": {
            "OK": True,
            "RequestID": int(p["glid"]) % 1000000,
            "Insights": {
                "glid": int(p["glid"]),
                "generated_at": datetime(2026, 10, 9, 9, 27, tzinfo=timezone.utc).isoformat(),
                "seller_summary": {
                    "contact_name": p["contact_name"].split()[0],
                    "tenure_days": p.get("tenure_days"),
                    "top_mcats": p.get("top_mcats", []),
                },
                "insights": items,
                "doubts": p.get("doubts", []),
            },
        },
    }


def get_context(p: dict) -> dict:
    """Global Context ``getContext`` (data_segments=all). Only the 'Mapping Not Found' answer has been seen — documented guess."""
    c = p.get("counts", {})
    return {"code": 200, "message": "Success", "data": {
        "vintage": {"member_since_days": p.get("tenure_days"), "custtype_name": p.get("custtype_name")},
        "nature_of_business": p.get("business_nature", ""),
        "last_activities": [{"activity": a} for a in [(p.get("trigger") or {}).get("activity", ""), "Replied to an enquiry", "Logged in on app"] if a],
        "buylead": {"local": c.get("local_buylead_count"), "national": c.get("national_buylead_count")},
        "calls": {"received_30d": c.get("pns_received_30d"), "answered_30d": c.get("pns_picked_30d")},
        "summaries": [b["summary"] for b in p.get("bot_calls", [])][:3],
        "tickets": [],
    }}


def _offer_id(p: dict, buyer: dict) -> str:
    return f"1527{int(hashlib.sha256((p['glid'] + buyer['glid']).encode()).hexdigest()[:8], 16) % 100000000:08d}"


def leads_purchased(p: dict) -> dict:
    """Buy-lead purchase — real shape: ``RESPONSE.DATA.offerids`` (strings)."""
    ids = [_offer_id(p, b) for b in p.get("buyers", []) if b["channel"] == "buylead"]
    return {"RESPONSE": {"CODE": "200", "STATUS": "1", "DATA": {"offerids": ids or [None]},
                         "MESSAGE": "purchased Lead id Found." if ids else "No purchased lead found.", "ERRORMESSAGE": "", "OTHER_DATA": []},
            "EXECUTION_TIME": 0.011}


def lead_detail(p: dict, offerid: str) -> dict:
    """Buy-lead details — real field names (ETO_OFR_*, GLUSR_* for the buyer)."""
    buyer = next((b for b in p.get("buyers", []) if b["channel"] == "buylead" and _offer_id(p, b) == str(offerid)), None)
    if not buyer:
        return {"RESPONSE": {"CODE": "204", "DATA": "NULL", "MESSAGE": "No offer found", "STATUS": "0"}}
    qty = re.match(r"(\d+)\s*(.*)", buyer["qty"])
    day = datetime.fromisoformat(buyer["last_at"])
    return {"RESPONSE": {"CODE": "200", "STATUS": "3", "MESSAGE": "THE OFFER HAS ALREADY BEEN PURCHASED", "DATA": {
        "ETO_OFR_TITLE": f"{buyer['product']} requirement", "PRIME_MCAT_NAME": buyer["product"], "ETO_OFR_GLCAT_MCAT_NAME": buyer["product"],
        "ETO_OFR_QTY": qty.group(1) if qty else None, "ETO_OFR_QTY_UNIT": qty.group(2) if qty else None,
        "ORDER_VAL": None, "ETO_OFR_DATE": day.strftime("%d-%b-%y").upper(), "ETO_OFR_POSTDATE_ORIG": day.strftime("%d-%b-%y").upper(),
        "FK_GLUSR_USR_ID": buyer["glid"], "GLUSR_NAME": buyer["name"].split()[0], "GLUSR_CITY": buyer["city"], "GLUSR_STATE": "",
        "ETO_OFR_BUYER_IS_MOB_VERF": "1", "ETO_OFR_BUYER_TOT_REQUIREMENT": "3", "ETO_OFR_BUYER_PRIME_MCATS": buyer["product"],
        "PURCHASE_STATUS": "OPEN", "ETO_OFR_DESC": "", "INDIAMART_MEMBER_SINCE": "01-JAN-24",
    }}}


def enquiry_list(p: dict) -> dict:
    """Enquiry list — real shape (no buyer GLID in the response)."""
    out = []
    for b in p.get("buyers", []):
        if b["channel"] != "enquiry":
            continue
        out.append({"enquiryTitle": b["product"], "enquiryISQ": [{"name": "Quantity", "value": b["qty"]}],
                    "postedDate": f"{b['last_at']}T11:20:00.000000", "buyerName": b["name"].split()[0], "buyerMemberSince": "2024-01-01T10:00:00",
                    "mobileVerified": 1, "emailVerified": 1 if b["intent"] == "High" else 0, "addressVerified": 0, "gstVerified": 0,
                    "buyerCity": b["city"], "buyerState": "", "countryFlag": "", "queryId": int(b["glid"]) * 7 % 1000000000})
    return {"status": "success", "code": 200, "error": "", "response": {"enquiries": out}}


def pns_insights(p: dict) -> dict:
    """PNS call insights — real envelope ``{Code, Status, data: [...], page_index}``."""
    out = []
    for b in p.get("buyers", []):
        if b["channel"] != "pns":
            continue
        qty_value = int(re.match(r"\d+", b["qty"]).group()) if re.match(r"\d+", b["qty"]) else None
        out.append({
            "file_id": int(b["glid"]) % 10000000,
            "SELLER_GLID": int(p["glid"]), "BUYER_GLID": int(b["glid"]),
            "call_timestamp": f"{b['last_at']} 15:45:06", "mcat_id": None, "mcat_name": b["product"], "src_mcat_id": None,
            "call_recording_url": "",
            "extracted_data": {
                "buyer_details": {"buyer_name": b["name"].split()[0]},
                "lead_tag": {"deal_readiness": "Warm" if b["intent"] != "Low" else "Cold", "deal_blockers": [],
                             "deal_readiness_reason": f"Asked for {b['qty']} of {b['product']}."},
                "metadata": {
                    "primary_language": "Hindi", "all_languages": ["Hindi"], "call_purpose": "requirement",
                    "buyer_intent": {"intent_level": b["intent"], "narrative": f"Wants {b['qty']} of {b['product']}."},
                    "call_outcome": {"category": "Follow-up", "conclusion_notes": "Asked seller to share rate."},
                    "call_type": {"type": "B2B"},
                },
                "products": [{"product_name": b["product"], "is_buyer_interested": True,
                              "quantity_required": {"value": qty_value, "unit": b["qty"].split(" ", 1)[-1] if " " in b["qty"] else ""}}],
                "next_steps": {"callback": False, "seller_next_steps": ["Share rate and delivery time"], "buyer_next_steps": []},
            },
        })
    return {"Code": 200, "Status": "Success", "UniqueId": hashlib.sha256(p["glid"].encode()).hexdigest()[:40], "data": out, "page_index": 1}


def lms_messages(p: dict, contact_glid: str = "") -> dict:
    """LMS conversation list — real envelope ``{code, status, response: {result, total_count}}``."""
    buyer = next((b for b in p.get("buyers", []) if b["glid"] == str(contact_glid)), None) or (p.get("buyers") or [{"glid": "0"}])[0]
    out = []
    for i, m in enumerate(sorted(p.get("lms", []), key=lambda x: x["date"], reverse=True)):
        seller_sent = m["from"] == "seller"
        out.append({
            "msg_date": datetime.fromisoformat(m["date"].replace(" ", "T")).strftime(" %d-%m-%Y %H:%M:%S"),
            "message_id": f"R{int(p['glid']) % 100000}{i}", "msg_query_type": "PV", "msg_ref_type": "REPLY",
            "msg_sender_id": p["glid"] if seller_sent else buyer["glid"],
            "msg_receiver_id": buyer["glid"] if seller_sent else p["glid"],
            "message_sender_bs_flag": "S" if seller_sent else "B",
            "msg_text_json": json.dumps({"message_text": m["text"], "isq": None, "enrichment": None, "additional_details": None}),
        })
    return {"code": 200, "status": "success", "response": {"result": out, "total_count": str(len(out)), "is_viewMore": 0}}


def sts_history(p: dict) -> list[dict]:
    """STS history — real shape: one URL-encoded HTML blob of dated entries, newest first."""
    parts = []
    for h in sorted(p.get("sts", []), key=lambda h: h["date"], reverse=True):
        day = datetime.fromisoformat(h["date"]).strftime("%d %b %Y 11:00:00 AM")
        # Executives write through WebERP (real STS logs); anything else reads as an automated channel.
        parts.append(f"{day}, Updated by {h['by']}&nbsp; through {h.get('via') or 'WebERP'}<br/>&nbsp;&nbsp; Comments: {h['note']}<br /><br/><br />")
    return [{"data": [quote("".join(parts))] if parts else [], "enddate": "", "message": "New History", "status": "200"}]


def redash_hotlead(p: dict) -> list[dict]:
    """Rows shaped like ``redash.data_hotlead_disposition_dtl`` for synthetic sellers."""
    rows = []
    for i, call in enumerate(p.get("bot_calls", [])):
        rows.append({
            "data_hotlead_disposition_dtlid": int(p["glid"]) * 10 + i,
            "fk_glusr_usr_id": int(p["glid"]),
            "lead_call_duration": call["duration_s"],
            "call_start_time": f"{call['date']} 12:00:00",
            "last_updated_on": f"{call['date']} 12:05:00",
            "lead_call_summary": call["summary"],
            "lead_shl_trigger": 1 if call["outcome"] == "Meeting Fixed" else 0,
            "disposition_label": call["outcome"],
        })
    return rows


PACKAGES = [
    {"code": "star_supplier", "name": "Star Supplier", "for": "sellers who want more buy leads and higher listing",
     "features": ["Priority listing in category", "Monthly buy-lead quota", "Lead Manager"], "price_verified": False},
    {"code": "trustseal", "name": "TrustSEAL", "for": "sellers who need buyer trust",
     "features": ["Document verification", "Trust badge on catalog"], "price_verified": False},
    {"code": "leader", "name": "Leader", "for": "high-volume sellers",
     "features": ["Top placement", "Larger buy-lead quota", "Dedicated support"], "price_verified": False},
    {"code": "global", "name": "Export (global) package", "for": "exporters",
     "features": ["Visibility to international buyers", "Export enquiries"], "price_verified": False},
    {"code": "buylead_topup", "name": "Buy-lead top-up", "for": "sellers who want to test lead quality",
     "features": ["Pay-per-lead pack", "No long commitment"], "price_verified": False},
]


def package_catalog(p: dict) -> dict:
    """Package catalogue. No verified prices were supplied, so none are returned (never invent prices)."""
    return {"packages": PACKAGES, "prices_available": False}


def now_ist() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")
