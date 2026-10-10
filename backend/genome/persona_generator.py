"""Persona generator (5.5): the owner's v0.3 prompt, fed with the APIs as the Consolidated API Guide describes them.

Flow for one seller: ``build_input`` reduces every API response to the keys the prompt reads (scrubbed of contact
details, amounts, ids, buyer names, ticket content) and adds numbered ``records`` (dated events, r1…) and ``facts``
(counted values, f1…); ``generate`` asks Sarvam (through llm.lehana.in) for the persona JSON; ``evaluate`` checks the
result against the prompt's own rules and the guide's "never mention" list, and one repair round runs when a critical
rule is broken. ``to_brief`` / ``to_persona`` feed the PM brief and the live bot.

Prompt: config/genome/prompts/persona_generator_v0.3.md (the SellerGenome changes are listed at its top).
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.genome import contract, parsers
from backend.genome.parsers import IST, persona_safe, present, scrub

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "config" / "genome" / "prompts" / "persona_generator_v0.3.md"
SCHEMA_VERSION = "0.3"
HOT_LEAD_TYPES = {"SCHD": "Schedule a demo hotlead", "PIM": "Payment Interested (activity)", "PAM": "Payment Attempted (activity)",
                  "OLP": "Online Payment (activity)", "OLPR": "Online Payment Renewal (activity)", "PUA": "Premium User Activity",
                  "UA": "User Activity", "TF": "Toll-Free (related to supplier calls)", "UATF": "Unassigned Toll-Free"}
GUARDRAILS = ["Persona changes delivery only. Facts, pricing and compliance statements never change.", "Never claim to be a human.",
              "Never state anything beyond what the record shows."]
GROUPS = {"Relationship", "Platform use", "Issues", "Reachability", "Data gaps"}
RECORD_TYPES = {"Executive call", "Meeting", "Call note", "Ticket", "Hot lead", "System notice", "Bot call"}
TRIGGERS = ["open_request", "follow_up_due", "hot_lead", "unresolved_last_interaction"]
SPREAD = {"pace": 25, "mix": 25, "formality": 15, "warmth": 15}  # dial bounds: ±spread, +15 when the position is a guess (G)
STATE_LANGUAGE = {"tamil nadu": "Tamil", "kerala": "Malayalam", "karnataka": "Kannada", "andhra pradesh": "Telugu", "telangana": "Telugu",
                  "maharashtra": "Marathi", "gujarat": "Gujarati", "west bengal": "Bengali", "punjab": "Punjabi", "odisha": "Odia"}


SYSTEM_LIMIT = 16000  # llm.lehana.in refuses longer system prompts (400 "System prompt exceeds 16000 chars")


def load_prompt() -> str:
    """The v0.3 prompt file, or its Admin → Prompts edit."""
    from backend.genome import prompts
    return prompts.text("persona.generator") or PROMPT_FILE.read_text()


def prompt_parts() -> tuple[str, str]:
    """(system prompt, input guide). The human change list (HTML comment) is not sent, and the "## Input" section goes at
    the top of the user message so the system prompt stays under the gateway's limit with nothing dropped."""
    text = re.sub(r"<!--.*?-->\s*", "", load_prompt(), flags=re.DOTALL)
    start, end = text.find("## Input"), text.find("## Output")
    if start == -1 or end == -1 or end < start:
        return text, ""
    return (text[:start] + text[end:]).strip(), text[start:end].strip()


# ------------------------------------------------------------------------------------------------- input

def _ok(results: dict, api: str) -> Any:
    r = results.get(api) or {}
    return r.get("response") if r.get("status") == "ok" else None


def _d(value: Any) -> str | None:
    """Any of the date formats the APIs use → YYYY-MM-DD (or None)."""
    text = str(present(value) or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text[:19] if "T" in text or ":" in text else text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def build_input(seller: dict, results: dict, today: date | None = None, lead_extra: dict | None = None) -> tuple[dict, dict]:
    """``seller_data`` for the prompt and ``refs`` for the checks (raw names that must never appear, ids, numbers)."""
    today = today or datetime.now(IST).date()
    refs: dict[str, Any] = {"forbidden": set(), "transcripts": [], "competitors": set(), "state": seller.get("state") or ""}
    data: dict[str, Any] = {}
    glid = str(seller.get("glid"))

    # lead (hot-lead row): contact, company, city, products, data_type, language, last call / meeting.
    lead = {"glid": int(glid) if glid.isdigit() else glid, "contact_name": (seller.get("contact_name") or "").strip() or None,
            "company_name": seller.get("company_name"), "city": seller.get("city") or None,
            # A private seller's profile is a placeholder: its products would be invented, so they come only from live data below.
            "products": ([p.get("name") for p in seller.get("products") or [] if p.get("name")][:6] or None) if seller.get("synthetic", True) else None}
    lead.update({k: v for k, v in (lead_extra or {}).items() if present(v) is not None})
    if str(lead.get("company_name") or "").startswith("Private seller"):
        lead["company_name"] = None  # identity pending: our placeholder is not a company name (the model once used it)
    ds_row = dataset_row(results)
    if ds_row:
        lead["city"] = lead.get("city") or present(ds_row.get("seller_city"))
        lead["state"] = present(ds_row.get("seller_state"))
        lead["categories"] = [c for c in (present(ds_row.get(f"top_category_{i}")) for i in (1, 2, 3)) if c] or None
        refs["state"] = refs["state"] or lead["state"] or ""
        if truthy(ds_row.get("do_not_call_requested")):
            lead["do_not_call"] = True
    if lead.get("last_call_summary"):
        lead["last_call_summary"] = persona_safe(lead["last_call_summary"]) or None
    data["lead"] = {k: v for k, v in lead.items() if present(v) is not None}

    # global_context (documented keys only) — from generateContextUID, or getContext when that holds the segments (5.10.1).
    gc_api = contract.global_context_source(results)
    gc_raw = _ok(results, gc_api)
    gc = _persona_safe_context(parsers.global_context(gc_raw)) if gc_raw else {}
    if gc:
        gc["source_api"] = gc_api
        raw = gc_raw.get("data") or {}
        refs["competitors"] |= {str(c.get("company") or c.get("company_name") or "").strip() for c in
                                (raw.get("activitydetails") or {}).get("top3mcat_competitors") or [] if isinstance(c, dict)} - {""}
        connect = []
        for r in gc.get("connects") or []:
            row = {"call_answered_date": r["at"], "call_answered_summary": r.get("summary"), "call_sts_update": r.get("note"),
                   "meet_sucessful_date": r.get("meet_date"), "meet_sts_update": r.get("meeting"), "video_meet_sts_update": r.get("video_note"),
                   "video_verified_date": r.get("video_date"), "answered": r.get("answered"),
                   "seller_words_script": r.get("seller_script")}
            connect.append({k: v for k, v in row.items() if v not in (None, "", {})})
        for w in ("connect_7D", "connect_30D"):
            for r in ((raw.get("connectdetails") or {}).get(w) or []):
                if isinstance(r, dict) and r.get("call_answered_transcript"):
                    refs["transcripts"].append(scrub(r["call_answered_transcript"]))
        act = gc.get("activity") or {}
        data["global_context"] = {k: v for k, v in {
            "kycdetails": {k: v for k, v in (gc.get("kyc") or {}).items() if v not in (None, [], "")},
            "connectdetails": {"rows": connect, "last_call_summary": gc.get("last_call_summary")},
            "ticketsdetails": [{"ticket_type": t["type"], "ticket_issue_date": _d(t["date"]), "in_progress": t["in_progress"]} for t in gc.get("tickets") or []],
            "activitydetails": {k: v for k, v in {**{k: act.get(k) for k in ("enq_last_30_days", "enq_last_90_days", "pns_received_30_days", "pns_received_90_days")},
                                                   **act.get("windows", {}), "top3mcat_data": act.get("top3mcat") or None,
                                                   "location_preference": act.get("location_preference") or None}.items() if v not in (None, {}, [])},
            "BLdetails": gc.get("buy_leads"),
        }.items() if v}

    # sts_history: dated, human vs system, ticket content withheld, ops/risk notices dropped, repeats merged.
    sts_raw = _ok(results, "sts.history")
    entries = parsers.sts_entries(sts_raw) if sts_raw is not None else []
    refs["forbidden"] |= {e["by"] for e in entries if e.get("by") and not e.get("automated")}
    sts = parsers.sts_for_persona(entries)
    if sts:
        data["sts_history"] = sts

    # pns_details: seller-side buyer calls, no names/numbers/recordings/prices.
    pns_raw = _ok(results, "pns.insights")
    calls = parsers.pns_calls(pns_raw, glid) if pns_raw is not None else []
    for row in (pns_raw.get("data") if isinstance(pns_raw, dict) else None) or []:
        name = ((row.get("extracted_data") or {}).get("buyer_details") or {}).get("name") if isinstance(row, dict) else None
        if name:
            refs["forbidden"].add(str(name).strip())
    mine = [c for c in calls if c.get("role") == "seller"]
    if mine:
        data["pns_details"] = [{"call_timestamp": c.get("at"), "buyer_intent": {"intent_level": c.get("intent")}, "call_type": {"type": c.get("buyer_type")},
                                "lead_tag": {"deal_readiness": c.get("readiness")}, "call_outcome": {"category": c.get("outcome")},
                                "primary_language": c.get("language"), "all_languages": c.get("languages"), "call_purpose": c.get("purpose"),
                                "callback": c.get("callback"), "products": c.get("products")[:3] if c.get("products") else None} for c in mine[:15]]

    # enquiries: date, title, specs, verification — First Copy skipped, buyer identity never sent.
    enq_raw = _ok(results, "enquiry.list")
    enqs = parsers.enquiries(enq_raw) if enq_raw is not None else []
    refs["forbidden"] |= {str(e.get("name") or "").strip() for e in enqs if e.get("name")}
    kept = [e for e in enqs if not e.get("first_copy")]
    if kept:
        data["enquiries"] = [{"postedDate": e.get("at"), "enquiryTitle": e.get("product"), "enquiryISQ": _spec_names(e.get("specs") or []),
                              "verification_checks_passed": e.get("checks_passed"), "quantity_given": bool(e.get("quantity")),
                              "foreign_buyer": e.get("foreign") or None} for e in kept[:10]]

    # company_attributes: storefront facts (ratings split, prices, about-us, addresses never sent).
    comp = _ok(results, "company.detail")
    if isinstance(comp, dict):
        d = comp.get("DATA") or {}
        cd = d.get("COMPANYDETAIL") or {}
        hdr = d.get("HEADER") or {}
        url = comp.get("URL_DETAIL") or {}
        factsheet = [{"title": f.get("TITLE") or f.get("title"), "value": f.get("VALUE") or f.get("value")} for f in d.get("FACTSHEET") or [] if isinstance(f, dict)]
        factsheet = [f for f in factsheet if f["title"] and not re.search(r"gst no|gstin|pan|cin|iec|bank|phone|mobile|email|address", str(f["title"]), re.I)]
        data["company_attributes"] = {k: v for k, v in {
            "BIZ": present(cd.get("BIZ")), "LEGALSTATUS": present(cd.get("LEGALSTATUS")), "NUMBEROFEMP_VAL": present(cd.get("NUMBEROFEMP_VAL")),
            "YOE": present(cd.get("YOE")), "city": present(cd.get("DIR_SEARCH_CITY")), "state": present(cd.get("DIR_SEARCH_STATE")),
            "PRD_COUNT": d.get("PRD_COUNT"), "PRDNAV": [{"CAT_NAME": c.get("CAT_NAME"), "CAT_PRD_COUNT": c.get("CAT_PRD_COUNT")}
                                                         for c in d.get("PRDNAV") or [] if isinstance(c, dict)][:8] or None,
            "FACTSHEET": factsheet[:8] or None, "DOWNLOAD_BROCHURE": bool(present(hdr.get("DOWNLOAD_BROCHURE"))),
            "HOMECORPORATEVIDEO": bool(present(d.get("HOMECORPORATEVIDEO"))), "COMP_TAGLINE": present(url.get("COMP_TAGLINE")),
        }.items() if v is not None}
        refs["state"] = refs["state"] or present(cd.get("DIR_SEARCH_STATE")) or ""
        # Identity and products straight from the company record (live for a private seller) — never from a placeholder.
        names = [present(x.get("ITEM_NAME")) for x in d.get("PRDSERV") or [] if isinstance(x, dict)]
        names = [n for n in names if n][:6]
        if names:
            data["lead"]["products"] = names
        for key, field in (("company_name", "DIR_SEARCH_COMPANY"), ("contact_name", "DIR_SEARCH_CONTACTPERSON"), ("city", "DIR_SEARCH_CITY")):
            if not data["lead"].get(key) and present(cd.get(field)):
                data["lead"][key] = str(cd.get(field)).strip()

    # Real history from the supplied dataset (Apr–Sep 2026 snapshot) and Redash hot-lead dispositions (live).
    if ds_row:
        data["supplied_dataset"] = {k: v for k, v in {
            "as_of": "IndiaMART dataset supplied for the hackathon, Apr–Sep 2026 (history, not today)",
            "business_type": present(ds_row.get("business_type")), "nature_of_business": present(ds_row.get("nature_of_business")),
            "nature_of_business_secondary": present(ds_row.get("nature_of_business_secondary")),
            "gst_registration_year": present(ds_row.get("gst_registration_year")), "annual_turnover": present(ds_row.get("annual_turnover")),
            "categories": data["lead"].get("categories"), "parent_category": present(ds_row.get("top_parent_category")),
            "category_group": present(ds_row.get("top_category_group")), "num_categories": number(ds_row.get("num_categories")),
            "past_objections": present(ds_row.get("past_objections")), "past_questions_asked": present(ds_row.get("past_questions_asked")),
            "past_dispositions": present(ds_row.get("past_dispositions_detailed")),
        }.items() if v is not None}
    history = bot_call_history(results)
    if history:
        data["bot_call_history"] = history
    lang = language_hint(data, gc, calls, refs["state"])
    if lang and "language" not in data["lead"]:
        data["lead"]["language_evidence"] = lang
    refs["language"] = (data["lead"].get("language") or (lang or {}).get("value") or "")
    records, facts = _records(data, gc, today), _facts(gc, enqs, calls, data.get("company_attributes"), today, ds_row)
    data["records"], data["facts"] = records, facts
    refs["record_ids"] = {r["id"] for r in records}
    refs["fact_ids"] = {f["id"] for f in facts}
    # A buyer or staff name that is also part of the seller's own identity ("Spice" in "Menon Spice Traders") is not a leak.
    ld = data["lead"]
    own = " ".join(str(x) for x in (ld.get("company_name"), ld.get("contact_name"), ld.get("city"), *(ld.get("products") or []), *(ld.get("categories") or []))).lower()
    refs["forbidden"] = {x for x in refs["forbidden"] if len(x) >= 4 and x.lower() not in own}
    return data, refs


def _persona_safe_context(gc: dict) -> dict:
    """Call notes, summaries and meeting notes with refund / legal / fraud / verification sentences and amounts removed."""
    for r in gc.get("connects") or []:
        for k in ("summary", "note", "video_note"):
            if r.get(k):
                r[k] = persona_safe(r[k]) or None
        if isinstance(r.get("meeting"), dict):
            r["meeting"] = {k: v for k, v in ((k, persona_safe(v)) for k, v in r["meeting"].items()) if v}
    if gc.get("last_call_summary"):
        gc["last_call_summary"] = persona_safe(gc["last_call_summary"]) or None
    kyc = gc.get("kyc") or {}
    if kyc.get("last_call_details"):
        kyc["last_call_details"] = [{**c, "comment": persona_safe(c.get("comment"))} for c in kyc["last_call_details"] if persona_safe(c.get("comment"))]
    return gc


def language_hint(data: dict, gc: dict, calls: list[dict], state: str) -> dict | None:
    """The guide's order: the seller's own words in transcripts and PNS languages; otherwise the state's language (G)."""
    langs = parsers.pns_stats(calls)["languages"] if calls else {}
    scripts = [r.get("seller_script") for r in gc.get("connects") or [] if r.get("seller_script")]
    if langs:
        top = next(iter(langs))
        return {"value": top, "basis": f"buyer-call languages {langs}", "confidence": "M"}
    if scripts:
        latin = sum(s["latin"] for s in scripts) / len(scripts)
        return {"value": "Hindi" if latin < 0.85 else "English", "basis": f"seller's own words, {round(latin * 100)}% Latin letters", "confidence": "M",
                "english_share_of_letters": round(latin, 2)}
    if state:
        return {"value": STATE_LANGUAGE.get(state.strip().lower(), "Hindi"), "basis": f"state {state} (no transcript or buyer-call language)", "confidence": "G"}
    return None


def _spec_names(specs: list[str]) -> list[str]:
    """Specs only for the category (guide): spec names, quantity kept; order values, amounts and the buyer's own text dropped."""
    out = []
    for s in specs:
        name, _, value = s.partition(":")
        name, value = name.strip(), value.strip()
        if re.search(r"order value|price|budget|amount|buyer filled|details|message|requirement details", name, re.I):
            continue
        out.append(f"{name}: {value}" if name.lower() in ("quantity", "quantity unit") else name)
    return out[:6]


def _records(data: dict, gc: dict, today: date) -> list[dict]:
    """Dated events newest first with ids r1… — the model cites these; chips open their source."""
    out: list[dict] = []
    gsrc = gc.get("source_api", "context.global")
    lead = data.get("lead") or {}
    if lead.get("data_type"):
        out.append({"date": _d(lead.get("trigger_date")) or today.isoformat(), "type": "Hot lead", "outcome": lead["data_type"],
                    "text": HOT_LEAD_TYPES.get(str(lead["data_type"]).upper(), f"Type {lead['data_type']} (not in the known list)"), "source": "lead"})
    for r in gc.get("connects") or []:
        if r["kind"] == "meeting":
            m = r.get("meeting") or {}
            text = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in m.items())[:280] or "Meeting recorded"
            out.append({"date": _d(r.get("meet_date") or r.get("at")), "type": "Meeting", "outcome": "Meeting done", "text": text, "source": gsrc})
        elif r["kind"] == "video_meeting":
            out.append({"date": _d(r.get("video_date") or r.get("at")), "type": "Meeting", "outcome": "Video meeting done",
                        "text": (r.get("video_note") or "Video meeting recorded")[:280], "source": gsrc})
        else:
            answered = r.get("answered")
            out.append({"date": _d(r.get("at")), "type": "Executive call",
                        "outcome": "Answered" if answered else "Not answered" if answered is False else "Note only",
                        "text": (r.get("summary") or r.get("note") or "No summary or note")[:280], "source": gsrc})
    for c in (gc.get("kyc") or {}).get("last_call_details") or []:
        out.append({"date": _d(c.get("at")), "type": "Call note", "outcome": "Executive comment", "text": (c.get("comment") or "")[:280], "source": gsrc})
    for t in gc.get("tickets") or []:
        out.append({"date": _d(t.get("date")), "type": "Ticket", "outcome": "In progress" if t["in_progress"] else "Raised",
                    "text": f"{t.get('type') or 'Ticket'} ticket", "source": gsrc})
    for e in data.get("sts_history") or []:
        if e.get("kind") == "ticket":
            out.append({"date": e["date"], "type": "Ticket", "outcome": "Updated", "text": f"{e.get('ticket_type')} ticket", "source": "sts.history"})
        else:
            rng = f" ({e['repeated']}× since {e['from_date']})" if e.get("repeated") else ""
            kind = e.get("kind") or "note"
            if e.get("source") == "system":
                rtype, outcome = "System notice", kind
            elif kind == "meeting":  # "Sales Appointment - <date> | … | Warm | Met With …" written by the executive
                rtype, outcome = "Meeting", "Meeting update"
            elif kind == "call" and "answered" in e:
                rtype, outcome = "Executive call", "Answered" if e["answered"] else "Not answered"
            elif kind == "follow_up":
                rtype, outcome = "Call note", "Follow-up set"
            else:
                rtype, outcome = "Call note", kind
            out.append({"date": e["date"], "type": rtype, "outcome": outcome, "text": f"{e.get('text', '')}{rng}"[:300], "source": "sts.history"})
    bot_texts = {c["summary"][:80] for c in data.get("bot_call_history") or []}
    if lead.get("last_call_summary") and persona_safe(lead["last_call_summary"])[:80] not in bot_texts:  # else it is a bot call below
        out.append({"date": _d(lead.get("last_call_date")) or None, "type": "Call note", "outcome": "Last call summary",
                    "text": persona_safe(lead["last_call_summary"])[:280], "source": "lead"})
    for c in data.get("bot_call_history") or []:
        out.append({"date": c["date"], "type": "Bot call", "outcome": c["outcome"], "text": c["summary"], "source": c["source"]})
    if lead.get("last_meeting_date") and not any(r["type"] == "Meeting" for r in out):
        out.append({"date": _d(lead["last_meeting_date"]), "type": "Meeting", "outcome": "Meeting date on the lead", "text": "Last meeting date", "source": "lead"})
    # Merge exact duplicates (an undated summary that repeats a dated note is the same event); newest first; undated last.
    seen, merged = set(), []
    dated = {r["text"][:80] for r in out if r["date"]}
    for r in out:
        key = (r["date"], r["type"], r["text"][:80])
        if key not in seen and (r["date"] or r["text"][:80] not in dated):
            seen.add(key)
            merged.append(r)
    merged.sort(key=lambda r: r["date"] or "", reverse=True)
    for i, r in enumerate(merged, start=1):
        r["id"] = f"r{i}"
    return merged


def _since_year(tenure: Any, today: date) -> int | None:
    """'4 Y 5 M' on 2026-10-10 → 2022, so the model never has to work a year out (and cite a number not in the input)."""
    m = re.search(r"(\d+)\s*Y(?:\D+(\d+)\s*M)?", str(tenure or ""), re.I)
    if not m:
        return None
    months = int(m.group(1)) * 12 + int(m.group(2) or 0)
    return (today.year * 12 + today.month - 1 - months) // 12


def dataset_row(results: dict) -> dict | None:
    """The supplied-dataset seller row (registry `dataset.seller`), or None."""
    ds = _ok(results, "dataset.seller")
    row = ds.get("row") if isinstance(ds, dict) and ds.get("found") else None
    return row if isinstance(row, dict) else None


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "1.0", "true", "yes")


def number(value: Any) -> float | int | None:
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(v) if v.is_integer() else round(v, 2)


def bot_call_history(results: dict, limit: int = 10) -> list[dict]:
    """Past VANI calls to this seller: supplied dataset (Apr–Sep 2026) and Redash hot-lead dispositions (live), merged by
    start time, newest first; summaries scrubbed. A 'Meeting Fixed' disposition is the bot's claim, not a held meeting."""
    rows: dict[str, dict] = {}
    ds = _ok(results, "dataset.calls")
    for c in (ds.get("calls") if isinstance(ds, dict) else None) or []:
        at = str(c.get("call_start_time") or "")[:19]
        if at:
            rows[at[:16]] = {"date": _d(at), "at": at, "outcome": present(c.get("disposition_label")) or "Answered",
                             "meeting_claimed": truthy(c.get("meeting_fixed")), "seconds": number(c.get("lead_call_duration")),
                             "summary": persona_safe(c.get("lead_call_summary"))[:280], "source": "dataset.calls"}
    rh = _ok(results, "redash.hotlead")
    for c in (rh.get("rows") if isinstance(rh, dict) else rh if isinstance(rh, list) else None) or []:
        at = str(c.get("call_start_time") or "")[:19]
        if at and at[:16] not in rows:
            rows[at[:16]] = {"date": _d(at), "at": at, "outcome": present(c.get("disposition_label")) or "Answered",
                             "meeting_claimed": truthy(c.get("lead_shl_trigger")), "seconds": number(c.get("lead_call_duration")),
                             "summary": persona_safe(c.get("lead_call_summary"))[:280], "source": "redash.hotlead"}
    out = sorted(rows.values(), key=lambda r: r["at"], reverse=True)[:limit]
    for r in out:
        if r["meeting_claimed"]:
            r["outcome"] = f"{r['outcome']} (meeting claimed by the bot)"
        r["summary"] = r["summary"] or "No summary"
    return out


# Supplied-dataset engagement facts: plain labels with window; replies/answer rates are left out on purpose (the guide
# forbids judging how responsive the seller is). All windows end at the dataset snapshot (Apr–Sep 2026), not today.
DATASET_FACTS = [
    ("eng_enq_received_90d", "buyer enquiries received, 90 days to the dataset snapshot"),
    ("eng_enq_received_1yr", "buyer enquiries received, 1 year to the dataset snapshot"),
    ("eng_pns_received_90d", "buyer phone calls received (PNS), 90 days to the dataset snapshot"),
    ("eng_pns_received_1yr", "buyer phone calls received (PNS), 1 year to the dataset snapshot"),
    ("eng_call_attempts_90d", "IndiaMART calls attempted to the seller, 90 days to the dataset snapshot"),
    ("eng_answered_calls_90d", "IndiaMART calls answered by the seller, 90 days to the dataset snapshot"),
    ("eng_callback_1yr", "callbacks requested by the seller, 1 year to the dataset snapshot"),
    ("eng_meetings_1yr", "meetings with IndiaMART, 1 year to the dataset snapshot"),
    ("eng_hot_meetings_1yr", "hot-lead meetings, 1 year to the dataset snapshot"),
    ("eng_product_count", "products listed (dataset snapshot)"),
    ("eng_products_modified_90d", "products modified, 90 days to the dataset snapshot"),
    ("eng_products_modified_1yr", "products modified, 1 year to the dataset snapshot"),
    ("eng_cqs", "catalogue quality score 0–100 (dataset snapshot)"),
    ("bot_attempts", "VANI bot call attempts, Apr–Sep 2026"),
    ("bot_answered", "VANI bot calls answered, Apr–Sep 2026"),
    ("calls_meeting_fixed", "answered bot calls ending 'Meeting Fixed' (the bot's claim), Apr–Sep 2026"),
    ("calls_not_interested", "answered bot calls ending 'Not Interested', Apr–Sep 2026"),
    ("calls_call_later", "answered bot calls ending 'Call Later', Apr–Sep 2026"),
    ("calls_general", "answered bot calls ending 'General', Apr–Sep 2026"),
    ("avg_answered_call_sec", "average answered bot call length in seconds, Apr–Sep 2026"),
]
DATASET_FLAGS = [("do_not_call_requested", "seller asked not to be called (dataset)"),
                 ("already_in_touch_with_executive", "seller said they are already in touch with an IndiaMART executive (dataset)"),
                 ("showed_frustration", "seller showed frustration on a past bot call (dataset)"),
                 ("asked_if_talking_to_bot", "seller asked whether they were talking to a bot (dataset)")]


def _facts(gc: dict, enqs: list[dict], calls: list[dict], company: dict | None, today: date, ds_row: dict | None = None) -> list[dict]:
    facts: list[dict] = []

    def add(label: str, value: Any, source: str) -> None:
        if value not in (None, "", [], {}):
            facts.append({"id": f"f{len(facts) + 1}", "label": label, "value": value, "source": source})
    gsrc = gc.get("source_api", "context.global")
    act = gc.get("activity") or {}
    names = {"last_week": "last week (7 days)", "last_4_week": "last 4 weeks (28 days)", "last_month": "last month", "last_3_month": "last 3 months"}
    words = {"bl_active_days": "days active on buy leads", "bl_cons": "buy leads consumed", "pns_call_picked": "buyer phone calls picked",
             "pns_call_received": "buyer phone calls received", "prd_without_photo": "products without a photo",
             "prd_without_price": "products without a price", "prd_without_ctg_mapping": "products without a category"}
    for w, values in (act.get("windows") or {}).items():
        for k, v in values.items():
            add(f"{words.get(k, k)} ({k}), {names.get(w, w)}", v, gsrc)
    # Plain labels with the window and the source key: a terse "pns received 90 days" was read as buy leads (live QA).
    for k, label in (("enq_last_30_days", "buyer enquiries received, last 30 days"), ("enq_last_90_days", "buyer enquiries received, last 90 days"),
                     ("pns_received_30_days", "buyer phone calls received, last 30 days"), ("pns_received_90_days", "buyer phone calls received, last 90 days")):
        add(f"{label} ({k})", act.get(k), gsrc)
    top = act.get("top3mcat") or {}
    add("buy leads in top categories, local", top.get("local_bl"), gsrc)
    add("buy leads in top categories, all India", top.get("total_bl"), gsrc)
    for k, v in (gc.get("buy_leads") or {}).items():
        add(f"buy leads available to the seller now ({k})", v, gsrc)
    kyc = gc.get("kyc") or {}
    add("customer type (kycdetails)", kyc.get("customer_type"), gsrc)
    add("time on IndiaMART (registered_since_with_indiamart)", kyc.get("registered_since_with_indiamart"), gsrc)
    add("on IndiaMART since (year worked out from that tenure)", _since_year(kyc.get("registered_since_with_indiamart"), today), gsrc)
    add("approved products", kyc.get("approved_product_count"), gsrc)
    if enqs:
        st = parsers.enquiry_stats(enqs)
        add("enquiries listed (latest, list may be capped)", st["listed"], "enquiry.list")
        add("enquiries, last 7 days (from the list)", st["last_7_days"], "enquiry.list")
        add("enquiries, last 30 days (from the list)", st["last_30_days"], "enquiry.list")
        add("distinct enquiring buyers (listed)", st["distinct_buyers"], "enquiry.list")
        add("listed enquiries with a quantity", st["with_quantity"], "enquiry.list")
        add("listed enquiries from buyers passing all 4 checks", st["fully_verified"], "enquiry.list")
        add("listed enquiries from outside India", st["foreign"] or None, "enquiry.list")
    if calls:
        ps = parsers.pns_stats(calls)
        if ps["calls"]:
            add("buyer calls analysed (PNS)", ps["calls"], "pns.insights")
            add("distinct buyers on those calls", ps["distinct_buyers"], "pns.insights")
            add("distinct days with buyer calls", ps["distinct_days"], "pns.insights")
            add("buyer calls with high intent", ps["high_intent"], "pns.insights")
            add("buyer calls marked warm", ps["warm"], "pns.insights")
            add("callbacks promised to buyers", ps["callbacks_promised"] or None, "pns.insights")
            add("languages on buyer calls", ps["languages"] or None, "pns.insights")
    if company:
        add("products listed on the storefront", company.get("PRD_COUNT"), "company.detail")
        add("brochure on the storefront", "yes" if company.get("DOWNLOAD_BROCHURE") else "no", "company.detail")
        add("company video on the storefront", "yes" if company.get("HOMECORPORATEVIDEO") else "no", "company.detail")
    if ds_row:
        for key, label in DATASET_FACTS:
            add(f"{label} ({key})", number(ds_row.get(key)), "dataset.seller")
        for key, label in DATASET_FLAGS:
            if present(ds_row.get(key)) is not None:
                add(f"{label} ({key})", "yes" if truthy(ds_row.get(key)) else "no", "dataset.seller")
    return facts


def user_message(seller_data: dict, today: date, input_guide: str = "") -> str:
    head = f"{input_guide}\n\n---\n\n" if input_guide else ""
    return f"{head}today: {today.isoformat()}\nseller_data:\n{json.dumps(seller_data, ensure_ascii=False, default=str)}\n\nReturn only the JSON object described under Output."


# ------------------------------------------------------------------------------------------------- expected behaviour

def expected_triggers(seller_data: dict, today: date) -> list[dict]:
    """The call-plan triggers the prompt's rules imply, in the prompt's order (deterministic, for the checks)."""
    found: list[dict] = []
    if (seller_data.get("lead") or {}).get("do_not_call"):
        return found  # the seller asked not to be called — no trigger opens a plan
    gc = seller_data.get("global_context") or {}
    if any(t.get("in_progress") for t in gc.get("ticketsdetails") or []):
        found.append({"type": "open_request", "why": "a ticket in progress"})
    soon = today + timedelta(days=3)
    records = seller_data.get("records") or []
    for r in records:
        text = r.get("text") or ""
        if not re.search(r"tbro|appointment|call ?back|follow[ -]?up|call (?:after|on|again)|call karne|baad (?:mein )?call", text, re.I):
            continue
        due = _dates_in(text, r.get("date"), today)
        # A dated follow-up (TBRO, appointment, promised callback) due today, in the past or within 3 days, with no later contact.
        if any(d <= soon for d in due) and not any(x.get("date") and r.get("date") and x["date"] > r["date"] and x["type"] in ("Executive call", "Meeting")
                                                   for x in records):
            found.append({"type": "follow_up_due", "why": f"{r['type']} {r.get('date')}: follow-up due {min(due).isoformat()}"})
            break
    dt = str((seller_data.get("lead") or {}).get("data_type") or "").upper()
    if dt in HOT_LEAD_TYPES:
        found.append({"type": "hot_lead", "why": f"{dt} — {HOT_LEAD_TYPES[dt]}"})
    meetings = [r for r in records if r["type"] in ("Meeting", "Call note", "Executive call")]
    if meetings:
        last = meetings[0]
        if re.search(r"need to follow up|work to be done|work to do|pending", (last.get("text") or "").lower()) and not re.search(r"pending: na\b", (last.get("text") or "").lower()):
            if not any(r.get("date") and last.get("date") and r["date"] > last["date"] and r["type"] != "System notice" for r in records):
                found.append({"type": "unresolved_last_interaction", "why": f"{last['type']} {last.get('date')}: pending item"})
    return found


def _dates_in(text: str, record_date: str | None, today: date) -> list[date]:
    """Explicit dates in a note ("2026-10-11", "11 Oct", "11/10/2026"); words like "tomorrow" resolve against the record's date."""
    out = []
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        try:
            out.append(date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    # ERP notes write "13-Oct-2026" (TBRO ADDED, Sales Appointment) — 5.9.8, GLID 12345678's due follow-up was missed.
    for m in re.finditer(r"\b(\d{1,2})[-/](Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/](\d{4})\b", text, re.I):
        try:
            out.append(datetime.strptime(f"{m.group(1)} {m.group(2)[:3].title()} {m.group(3)}", "%d %b %Y").date())
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", text, re.I):
        try:
            out.append(datetime.strptime(f"{m.group(1)} {m.group(2)[:3].title()} {today.year}", "%d %b %Y").date())
        except ValueError:
            pass
    # A bare day of the month ("after the 10th", "10 tareekh") is the next such day on or after the record's date.
    base = date.fromisoformat(record_date) if record_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", record_date) else today
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)\b(?!\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))|\b(\d{1,2})\s*(?:tareekh|tarikh|taarikh)\b", text, re.I):
        day = int(m.group(1) or m.group(2))
        try:
            d = base.replace(day=day)
            out.append(d if d >= base else (d.replace(month=d.month + 1) if d.month < 12 else d.replace(year=d.year + 1, month=1)))
        except ValueError:
            pass
    if record_date and re.search(r"\b(tomorrow|kal)\b", text, re.I):
        try:
            out.append(date.fromisoformat(record_date) + timedelta(days=1))
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------------------------------------- checks

PHONE = parsers.PHONE
EMAIL = parsers.EMAIL
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
MONEY = re.compile(r"(?:₹|\bRs\.?|\bINR)\s*\d|\d[\d,]*\s*(?:lakh|crore|cr)\b(?!\s*-)", re.IGNORECASE)
# Turnover bands ("40 L - 1.5 Cr", "₹100 - 500 Cr") are approximate and allowed by the prompt; exact amounts are not.
BAND = re.compile(r"(?:₹\s*)?\d[\d.,]*\s*(?:L|lakh|lac|cr|crore)?\s*(?:-|–|to)\s*(?:₹\s*)?\d[\d.,]*\s*(?:L|lakh|lac|cr|crore)\b", re.IGNORECASE)
# Topics never written (guide). "Competitors pay less" is allowed: only competitor *names* are banned (checked by name).
OPS_TOPIC = re.compile(r"\b(?:refund\w*|legal (?:notice|case|action|matter|issue|ticket)|fraud\w*|chargeback|risk flag)\b", re.IGNORECASE)
WITHHELD = re.compile(r"email|phone|mobile|gst (?:no|number)|gstin|address|pan\b", re.IGNORECASE)
PLACEHOLDER = "[line to be written and checked by a native speaker]"

GENDERED = re.compile(r"\b(?:karunga|karungi|karegi|karega|sakti hoon|sakta hoon|rahi hoon|raha hoon|bol rahi|bol raha|aaungi|aaunga|"
                      r"chahti hoon|chahta hoon|gayi|gaya hoon)\b", re.IGNORECASE)
PRONOUN = re.compile(r"\b(?:he|she|his|her|him|hers)\b", re.IGNORECASE)
JUDGEMENT = re.compile(r"\b(?:ignor\w*|unresponsive|careless|lazy|difficult|price[- ]sensitive|never (?:replies|picks|answers))\b", re.IGNORECASE)


def _words(text: Any) -> int:
    return len(str(text or "").split())


def _texts(out: dict) -> list[tuple[str, str]]:
    """Every free-text string in the output with its path (for the never-mention scans)."""
    found: list[tuple[str, str]] = []

    def walk(x: Any, path: str) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str) and path not in (".generated_at",) and not path.startswith(".guardrails"):
            found.append((path, x))
    walk(out, "")
    return found


def invented_numbers(out: Any, seller_data: dict) -> list[tuple[str, str]]:
    """Numbers in the output that are not in the input (a possible invented figure). "3,900" is one number; a percentage
    worked out from two given counts ("picked 51 of 60" → 85%) is the input restated, not a new figure."""
    if not isinstance(out, dict):
        return []
    blob = json.dumps(seller_data, ensure_ascii=False, default=str)
    numbers_in = set(re.findall(r"\d+(?:\.\d+)?", blob))
    numbers_in |= {str(int(n)) for n in numbers_in if n.isdigit()}  # "2026-10-08" in the input → "8 Oct" in the output
    counts = sorted({float(f["value"]) for f in seller_data.get("facts") or [] if isinstance(f.get("value"), (int, float)) and not isinstance(f.get("value"), bool)})
    ratios = {round(100 * a / b) for a in counts for b in counts if b > 0 and a <= b}
    found = []
    for path, t in _texts(out):
        if path.startswith((".record", ".generated_at", ".persona.dials", ".schema_version")) or "bounds" in path:
            continue
        for m in re.finditer(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)(?![\w.,])(\s*%)?", t):
            num = m.group(1).replace(",", "")
            if m.group(2) and num.isdigit() and int(num) in ratios:
                continue
            if num not in numbers_in and len(num) <= 7 and num not in ("0", "1", "2", "3", "4", "5", "100"):
                found.append((path, m.group(0).strip()))
    return found


def evaluate(out: Any, seller_data: dict, refs: dict, today: date) -> dict:
    """Check one generated persona against the prompt's rules and the guide. Critical failures trigger a repair."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "", critical: bool = False) -> None:
        checks.append({"check": name, "ok": bool(ok), "critical": critical, "detail": detail[:300]})

    if not isinstance(out, dict):
        check("JSON object", False, "The reply was not a JSON object", critical=True)
        return _score(checks)
    required = ["schema_version", "glid", "segment", "basics", "persona", "engagement_with_indiamart", "engagement_with_buyers",
                "call_plan", "record", "counts", "gaps", "guardrails"]
    missing = [k for k in required if k not in out]
    check("Every top-level section present", not missing, f"missing: {', '.join(missing)}", critical=bool(missing))
    kyc = (seller_data.get("global_context") or {}).get("kycdetails") or {}
    want_segment = {"paid": "Paid", "free": "Free"}.get(str(kyc.get("customer_type") or "").lower(), "Unknown")
    check("Segment follows customer_type", out.get("segment") == want_segment, f"{out.get('segment')} vs {want_segment}", critical=True)
    check("Guardrails exactly as given", out.get("guardrails") == GUARDRAILS, "guardrails changed")

    # Record ids and evidence.
    record = out.get("record") if isinstance(out.get("record"), list) else []
    given = refs.get("record_ids", set())
    invented = [r.get("id") for r in record if isinstance(r, dict) and r.get("id") not in given]
    check("Record only lists given records", not invented, f"unknown ids: {invented[:5]}", critical=bool(invented))
    bad_types = [r.get("type") for r in record if isinstance(r, dict) and r.get("type") not in RECORD_TYPES]
    check("Record types from the list", not bad_types, f"{bad_types[:3]}")
    eng = out.get("engagement_with_indiamart") or {}
    insights = eng.get("insights") if isinstance(eng.get("insights"), list) else []
    valid_ids = given | refs.get("fact_ids", set())
    unsupported, broken = [], []
    for ins in insights:
        if not isinstance(ins, dict):
            continue
        ev = [e.get("record_id") for e in ins.get("evidence") or [] if isinstance(e, dict)]
        if not ev and not ins.get("gap") and ins.get("confidence") == "H":
            unsupported.append(ins.get("id"))
        broken += [x for x in ev if x not in valid_ids]
    check("Every H insight has evidence", not unsupported, f"no evidence: {unsupported[:5]}", critical=bool(unsupported))
    check("Evidence points to given records or facts", not broken, f"unknown: {broken[:5]}", critical=bool(broken))
    n = len([i for i in insights if isinstance(i, dict) and not i.get("gap")])
    rich = len(seller_data.get("records") or []) >= 3
    # Owner, 2026-10-10: more insights — 7 to 12 when the record is rich (one point each), fewer with little data.
    want = min(7, max(3, len(seller_data.get("records") or []) + len(seller_data.get("facts") or []) // 3))
    check("7 to 12 IndiaMART insights (fewer allowed with little data)", (want <= n + 1 <= 13) if rich else n >= 1, f"{n} insights (want ≥{want})")
    groups = {i.get("group") for i in insights if isinstance(i, dict)}
    check("Insight groups from the list", groups <= GROUPS, f"{groups - GROUPS}")
    long_ins = [i.get("id") for i in insights if isinstance(i, dict) and _words(i.get("text")) > 18]
    check("Insights at most 18 words", not long_ins, f"{long_ins[:5]}")
    conf = [i.get("confidence") for i in insights if isinstance(i, dict)] + [i.get("confidence") for i in (out.get("engagement_with_buyers") or {}).get("insights") or [] if isinstance(i, dict)]
    check("Confidence is H, M or G", all(c in ("H", "M", "G") for c in conf), f"{[c for c in conf if c not in ('H', 'M', 'G')][:3]}")

    # Persona block.
    persona = out.get("persona") or {}
    check("Summary at most 30 words", _words(persona.get("summary")) <= 30, f"{_words(persona.get('summary'))} words")
    tags = (out.get("basics") or {}).get("persona_tags") or []
    check("2 to 5 persona tags", 2 <= len(tags) <= 5, f"{len(tags)} tags")
    voice = persona.get("voice") or {}
    check("Voice gender stays neutral", str(voice.get("gender", "")).lower().startswith("neutral"), str(voice.get("gender")))
    dials = persona.get("dials") or {}
    for name in SPREAD:
        dial = dials.get(name) or {}
        pos = dial.get("pos")
        ok = isinstance(pos, (int, float)) and 0 <= pos <= 100
        check(f"Dial {name} position 0–100", ok, str(pos), critical=not ok)
        # `bounds` is arithmetic on `pos` (±spread, +15 when G): enforce_bounds sets it, so it is not scored here.

    # Call plan vs the triggers the rules imply.
    expected = expected_triggers(seller_data, today)
    plan = out.get("call_plan")
    if not expected:
        check("No call plan without a trigger", plan is None, f"plan given with trigger {((plan or {}).get('trigger') or {}).get('type')}", critical=plan is not None)
        check("Reason given when there is no plan", plan is not None or bool(out.get("call_plan_note")), "call_plan_note empty")
    else:
        check("Call plan present when a trigger exists", isinstance(plan, dict), f"expected {expected[0]['type']} ({expected[0]['why']})", critical=not isinstance(plan, dict))
        if isinstance(plan, dict):
            got = (plan.get("trigger") or {}).get("type")
            check("Main trigger follows the rule order", got == expected[0]["type"], f"{got} vs {expected[0]['type']}")
            opening = str((plan.get("opening") or {}).get("line") or "")
            if opening.strip() == PLACEHOLDER:
                opening = "?"  # left for a native speaker (prompt rule): nothing to count
            sentences = [x for x in re.split(r"(?<=[.?!])\s+", opening.strip()) if x]
            check("Opening: at most two sentences, ends with one question", len(sentences) <= 2 and opening.strip().endswith("?") and opening.count("?") == 1, opening[:120])
            long_s = [x for x in sentences if _words(x) > 14]  # the seller has little time (owner, 2026-10-10): 12 words, +2 slack
            check("Opening sentences short (14 words or fewer)", not long_s, f"{[_words(x) for x in long_s]} words")
            check("Opening has no gendered verb forms", not GENDERED.search(opening), (GENDERED.search(opening) or [""])[0])
            own = str((seller_data.get("lead") or {}).get("company_name") or "").strip()
            check("Opening does not speak for the seller's company", not (own and re.search(rf"{re.escape(own)}\s+se\b", opening, re.I)), opening[:80])
            lang = str((seller_data.get("lead") or {}).get("language") or "Hindi").lower()
            if "hi" in lang:
                check("Hindi opening in Roman letters", not re.search(r"[ऀ-ॿ]", opening), opening[:80])
            contact = str((seller_data.get("lead") or {}).get("contact_name") or "")
            company = str((seller_data.get("lead") or {}).get("company_name") or "")
            if company and contact.strip().lower() == company.strip().lower():
                check("Company name not addressed with 'ji'", not re.search(r"\b\w+\s+ji\b", opening, re.I) or "aap" in opening.lower(), opening[:80])
            pts = plan.get("points_to_cover") or []
            check("2 to 4 points to cover", 2 <= len(pts) <= 4, f"{len(pts)}")
            check("1 to 3 questions to ask", 1 <= len(plan.get("questions_to_ask") or []) <= 3, f"{len(plan.get('questions_to_ask') or [])}")
            if (seller_data.get("lead") or {}).get("data_type") and str(seller_data["lead"]["data_type"]).upper() not in HOT_LEAD_TYPES:
                check("Unknown hot-lead type is not a trigger", got != "hot_lead", f"{seller_data['lead']['data_type']}", critical=True)
            in_progress = any(t.get("in_progress") for t in ((seller_data.get("global_context") or {}).get("ticketsdetails") or []))
            # open_request is pure data (a ticket in progress); a question asked on a call is not one (live run 5, Joshi).
            check("An open request needs a ticket in progress", got != "open_request" or in_progress, "no ticket is in progress", critical=got == "open_request" and not in_progress)

    # Never mention (guide + prompt).
    texts = _texts(out)
    hits = []
    for path, t in texts:
        for label, pattern in (("phone", PHONE), ("email", EMAIL), ("GST number", GSTIN), ("link", parsers.URL), ("exact amount", MONEY)):
            if pattern.search(BAND.sub(" ", t) if label == "exact amount" else t):
                hits.append(f"{label} at {path}")
        if re.search(r"first\s*copy", t, re.I):
            hits.append(f"counterfeit enquiry at {path}")
        if OPS_TOPIC.search(t) and not path.startswith(".call_plan.avoid"):
            hits.append(f"refund/legal/fraud topic at {path}")
        for name in refs.get("forbidden", set()) | refs.get("competitors", set()):
            if name and re.search(rf"\b{re.escape(name)}\b", t, re.I):
                hits.append(f"a name that must not appear at {path}")
        if re.search(r"\b\d{5,7}\b", t) and re.search(r"emp|executive|staff|agent", t, re.I):
            hits.append(f"possible employee id at {path}")
    check("Never-mention list respected", not hits, "; ".join(hits[:4]), critical=bool(hits))
    quotes = []
    for path, t in texts:
        words = t.lower().split()
        for tr in refs.get("transcripts", []):
            tw = tr.lower()
            if any(" ".join(words[i:i + 6]) in tw for i in range(max(0, len(words) - 5))):
                quotes.append(path)
                break
    check("No transcript quotes", not quotes, f"{quotes[:3]}", critical=bool(quotes))
    judged = [p for p, t in texts if JUDGEMENT.search(t) and not p.startswith(".call_plan.avoid")]
    check("No judgement of the seller's responsiveness", not judged, f"{judged[:3]}")
    # Record text restates the given notes (written by executives, often with he/she); the model's own text must not.
    pron = [p for p, t in texts if PRONOUN.search(t) and not p.startswith((".persona.voice", ".record"))]
    check("No he/she — 'the seller'", not pron, f"{pron[:3]}")

    invented = invented_numbers(out, seller_data)
    invented_numbers_list = [f"{num} at {path}" for path, num in invented]
    # 5.9.8 (GLID 12345678): "Active on 28 of the last 28 days" with no such field in the input — the model copied the
    # prompt's style example. An invented figure is critical: it triggers the repair round, and polish drops what is left.
    check("Every number comes from the input", not invented, "; ".join(invented_numbers_list[:4]), critical=bool(invented))
    stats = (out.get("engagement_with_buyers") or {}).get("stats") or []
    no_window = [s.get("label") for s in stats if isinstance(s, dict) and not re.search(r"day|week|month|list|latest|analysed|calls|available|current", str(s.get("label", "")), re.I)]
    check("Buyer stats name their window", not no_window, f"{no_window[:3]}")
    withheld = [g for g in out.get("gaps") or [] if WITHHELD.search(str(g))]
    check("Gaps do not list withheld contact/GST fields", not withheld, f"{withheld[:3]}")
    want_lang = str(refs.get("language") or "").split()[0].lower() if refs.get("language") else ""
    got_lang = str(voice.get("language") or "").lower()
    if want_lang:
        check("Voice language follows the guide's order", want_lang in got_lang or got_lang.startswith(want_lang[:4]), f"{voice.get('language')} vs {refs.get('language')}")
    # A real seller's products and identity are never made up (owner, 2026-10-10: "dont make things anywhere").
    made_up = invented_products(out, seller_data)
    check("Products only from the input", not made_up, f"not in the input: {made_up[:3]}", critical=bool(made_up))
    basics = out.get("basics") if isinstance(out.get("basics"), dict) else {}
    placeholder = [k for k in ("name", "company") if PLACEHOLDER_ID.search(str(basics.get(k) or ""))]
    check("No placeholder identity", not placeholder, f"basics.{placeholder[:1]}", critical=bool(placeholder))
    return _score(checks)


DATASET_NOTE = "Supplied dataset, Apr–Sep 2026 — history, not today"
PLACEHOLDER_ID = re.compile(r"private seller|glid\s*\d", re.I)


def _norm_name(text: Any) -> str:
    """'Brass Lamps (Premium)' → 'brass lamp premium' (case, punctuation and a plural s ignored)."""
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()))


def allowed_products(seller_data: dict) -> list[str]:
    """Every product or category name the input gives: lead products/categories, storefront categories, dataset
    categories and enquiry titles."""
    lead = seller_data.get("lead") or {}
    ca = seller_data.get("company_attributes") or {}
    ds = seller_data.get("supplied_dataset") or {}
    names = [*(lead.get("products") or []), *(lead.get("categories") or []), *(ds.get("categories") or []),
             *[c.get("CAT_NAME") for c in ca.get("PRDNAV") or [] if isinstance(c, dict)], ds.get("parent_category"), ds.get("category_group"),
             *[e.get("enquiryTitle") for e in seller_data.get("enquiries") or []]]
    return [str(n).strip() for n in names if n and str(n).strip()]


def invented_products(out: Any, seller_data: dict) -> list[str]:
    """`basics.products` items that are neither an input name nor a shortening of one."""
    allowed = {_norm_name(n) for n in allowed_products(seller_data)}
    items = ((out.get("basics") or {}).get("products") if isinstance(out, dict) and isinstance(out.get("basics"), dict) else None) or []
    bad = []
    for item in items if isinstance(items, list) else []:
        n = _norm_name(item)
        if n and not any(n == a or f" {n} " in f" {a} " for a in allowed):
            bad.append(str(item))
    return bad


def _score(checks: list[dict]) -> dict:
    critical = [c for c in checks if not c["ok"] and c["critical"]]
    warnings = [c for c in checks if not c["ok"] and not c["critical"]]
    return {"score": max(0, 100 - 25 * len(critical) - 4 * len(warnings)), "passed": sum(c["ok"] for c in checks), "total": len(checks),
            "critical": len(critical), "warnings": len(warnings), "checks": checks}


# ------------------------------------------------------------------------------------------------- generate

async def generate(sarvam, seller_data: dict, refs: dict, today: date, *, scope: str, run_id: str | None = None, max_tokens: int = 7000) -> dict:
    """Generate, check, repair once when a critical rule is broken, then apply the fixes that are pure rules (``polish``).

    ``evaluation`` scores what is delivered; ``evaluation.raw_score`` is the model's own reply before any code fix, so the
    two can be compared run to run.
    """
    system, input_guide = prompt_parts()
    if len(system) > SYSTEM_LIMIT:
        raise ValueError(f"System prompt is {len(system)} chars; the gateway accepts {SYSTEM_LIMIT}.")
    user = user_message(seller_data, today, input_guide)
    attempts = []
    started = time.monotonic()
    from backend.genome.sarvam import SarvamError  # local: sarvam imports httpx, kept out of the module's import time
    try:
        reply = await sarvam.chat_json(system, user, scope=scope, max_tokens=max_tokens, temperature=0.2, run_id=run_id, label="persona-generator")
    except SarvamError as error:
        # 5.10.5 (live, GLID 23456789 with 13 records and 62 facts): a long persona came back cut off and the seller fell
        # back to the rules persona with no insights. One retry with more room before giving up.
        if "JSONDecodeError" not in str(error):
            raise
        reply = await sarvam.chat_json(system, user, scope=scope, max_tokens=max_tokens, temperature=0.1, run_id=run_id,
                                       label="persona-generator-retry")  # the gateway refuses max_tokens above ~8k
    out = reply["data"]
    ev = evaluate(out, seller_data, refs, today)
    attempts.append({"ms": reply["ms"], "score": ev["score"], "critical": ev["critical"], "usage": reply.get("usage")})
    if ev["critical"]:
        problems = "\n".join(f"- {c['check']}: {c['detail']}" for c in ev["checks"] if not c["ok"] and c["critical"])
        fix = (f"{user}\n\nYour previous JSON broke these rules:\n{problems}\n\nPrevious JSON:\n{json.dumps(out, ensure_ascii=False)[:12000]}\n\n"
               "Return the corrected JSON only, changing only what the rules require.")
        reply2 = await sarvam.chat_json(system, fix, scope=scope, max_tokens=max_tokens, temperature=0.1, run_id=run_id, label="persona-generator-repair")
        ev2 = evaluate(reply2["data"], seller_data, refs, today)
        attempts.append({"ms": reply2["ms"], "score": ev2["score"], "critical": ev2["critical"], "usage": reply2.get("usage")})
        if ev2["score"] >= ev["score"]:
            out, ev, reply = reply2["data"], ev2, reply2
    raw = ev
    if isinstance(out, dict):
        fixed = polish(out, seller_data, refs, today)
        ev = evaluate(out, seller_data, refs, today) if fixed else raw
        ev["corrected"] = fixed
    ev["raw_score"], ev["raw_critical"], ev["raw_warnings"] = raw["score"], raw["critical"], raw["warnings"]
    ev["raw_failed"] = [c["check"] for c in raw["checks"] if not c["ok"]]
    return {"persona": out, "evaluation": ev, "attempts": attempts, "ms": round((time.monotonic() - started) * 1000),
            "model": reply.get("model"), "expected_triggers": expected_triggers(seller_data, today),
            "request": {"system_prompt": f"{PROMPT_FILE.name} ({len(system)} chars)", "user": user[:6000], "model": reply.get("model")}}


NATIVE_OK = ("hindi", "english", "hinglish")


NEG_TONE = re.compile(r"not answered|no answer|declin|refus|not interested|in progress|open|busy|unreachable", re.IGNORECASE)
POS_TONE = re.compile(r"answered|meeting|interest|resolved|follow-up set|paid|payment done|positive", re.IGNORECASE)


def record_tone(r: dict) -> str:
    """The prompt's tone rule in code: neg for no answer, a decline or an open issue; pos for interest or resolution."""
    text = f"{r.get('outcome') or ''} {r.get('type') or ''}"
    return "neg" if NEG_TONE.search(text) else "pos" if POS_TONE.search(text) else "neu"


def polish(out: dict, seller_data: dict, refs: dict, today: date) -> list[str]:
    """Fixes that are rules, not judgement — applied in code after the model so a broken rule never reaches a caller.
    Returns what was changed (shown under the quality panel as "Corrected by code")."""
    fixed = enforce_bounds(out)
    # 5.10.6: the model returns `record` empty; it is the input's records (ids, dates, types, outcomes), filled here.
    if not out.get("record"):
        out["record"] = [{"id": r.get("id"), "date": r.get("date"), "type": r.get("type"),
                          "outcome": {"label": r.get("outcome"), "tone": record_tone(r)}, "text": r.get("text")}
                         for r in seller_data.get("records") or [] if isinstance(r, dict)]
    # An insight that still carries a figure the input does not have is dropped, never shown ("dont make things anywhere").
    for section in ("engagement_with_indiamart", "engagement_with_buyers"):
        items = (out.get(section) or {}).get("insights")
        if not isinstance(items, list):
            continue
        bad = {int(m.group(1)) for path, _ in invented_numbers(out, seller_data)
               for m in [re.match(rf"\.{section}\.insights\[(\d+)\]", path)] if m}
        if bad:
            out[section]["insights"] = [x for i, x in enumerate(items) if i not in bad]
            fixed.append(f"Dropped {len(bad)} {section.replace('_', ' ')} insight(s) with a number not in the input")
    out["schema_version"] = SCHEMA_VERSION
    out["generated_at"] = datetime.now(IST).isoformat(timespec="seconds")
    if out.get("guardrails") != GUARDRAILS:
        out["guardrails"] = GUARDRAILS
        fixed.append("guardrails restored")
    # Evidence must point at a given record or fact. "No evidence, no insight" (guide): an insight left with none goes,
    # except the data-gaps insight.
    valid = refs.get("record_ids", set()) | refs.get("fact_ids", set())
    eng = out.get("engagement_with_indiamart") if isinstance(out.get("engagement_with_indiamart"), dict) else {}
    kept_insights = []
    for ins in eng.get("insights") or []:
        if not isinstance(ins, dict):
            continue
        evidence = ins.get("evidence") if isinstance(ins.get("evidence"), list) else []
        keep = [e for e in evidence if isinstance(e, dict) and e.get("record_id") in valid]
        if len(keep) != len(evidence):
            fixed.append(f"{ins.get('id') or 'insight'}: evidence {[e.get('record_id') for e in evidence if e not in keep]} removed")
            ins["evidence"] = keep
        if not keep and not ins.get("gap"):
            fixed.append(f"{ins.get('id') or 'insight'} removed: no evidence (\"{str(ins.get('text') or '')[:60]}\")")
            continue
        kept_insights.append(ins)
    if eng.get("insights") is not None:
        eng["insights"] = kept_insights
    # Supplied-dataset values are history (Apr–Sep 2026): an insight resting only on them says so (live run, 2026-10-10:
    # "7 calls … in the last 90 days" read as today's window).
    ds_ids = {f["id"] for f in seller_data.get("facts") or [] if f.get("source") == "dataset.seller"}
    for ins in kept_insights:
        ids = {e.get("record_id") for e in ins.get("evidence") or [] if isinstance(e, dict)}
        if ids and ids <= ds_ids and not re.search(r"dataset|supplied|snapshot|apr|sep", f"{ins.get('text')} {ins.get('note') or ''}", re.I):
            ins["note"] = DATASET_NOTE
            fixed.append(f"{ins.get('id') or 'insight'}: marked as supplied-dataset history")
    # Withheld fields are never gaps.
    gaps = out.get("gaps") if isinstance(out.get("gaps"), list) else []
    kept = [g for g in gaps if not WITHHELD.search(str(g))]
    if len(kept) != len(gaps):
        fixed.append(f"gaps {[g for g in gaps if g not in kept]} removed (withheld on purpose)")
        out["gaps"] = kept
    made_up = invented_products(out, seller_data)
    if made_up:
        out["basics"]["products"] = [p for p in out["basics"]["products"] if str(p) not in made_up]
        fixed.append(f"basics.products {made_up[:4]} removed (not in the input)")
    basics = out.get("basics") if isinstance(out.get("basics"), dict) else {}
    lead = seller_data.get("lead") or {}
    for key, real in (("name", lead.get("contact_name") or lead.get("company_name")), ("company", lead.get("company_name"))):
        if PLACEHOLDER_ID.search(str(basics.get(key) or "")):
            basics[key] = real or None
            fixed.append(f"basics.{key}: placeholder replaced ({'from the company record' if real else 'unknown'})")
    plan = out.get("call_plan") if isinstance(out.get("call_plan"), dict) else None
    expected = expected_triggers(seller_data, today)
    if plan and lead.get("do_not_call"):
        out["call_plan"] = None
        out["call_plan_note"] = "The seller asked not to be called (supplied dataset)."
        fixed.append("call plan removed: the seller asked not to be called")
        plan = None
    if plan:
        trig = (plan.get("trigger") or {}).get("type")
        data_type = str((seller_data.get("lead") or {}).get("data_type") or "").upper()
        in_progress = any(t.get("in_progress") for t in ((seller_data.get("global_context") or {}).get("ticketsdetails") or []))
        # The two triggers that are pure data: a ticket in progress, a hot-lead type from the table.
        invalid = (trig == "open_request" and not in_progress) or (trig == "hot_lead" and data_type not in HOT_LEAD_TYPES)
        if invalid and not expected:
            out["call_plan"] = None
            out["call_plan_note"] = "No ticket in progress, follow-up due or known hot-lead type in the data."
            fixed.append(f"call plan removed: '{trig}' is not present in the data")
            plan = None
    if plan:
        lang = re.split(r"[^a-z]+", str(((out.get("persona") or {}).get("voice") or {}).get("language") or "Hindi").lower().strip())[0]  # "Hindi-English" → hindi
        opening = plan.get("opening") if isinstance(plan.get("opening"), dict) else {}
        line = str(opening.get("line") or "")
        if PLACEHOLDER in line and line != PLACEHOLDER and lang in NATIVE_OK:
            # The placeholder is a whole line for other languages, never a name slot inside a Hindi or English line.
            opening["line"] = re.sub(r"\s*" + re.escape(PLACEHOLDER) + r"[\s.,]*", " ", line).strip()
            fixed.append("opening: native-speaker placeholder removed from a Hindi/English line")
        if lang not in NATIVE_OK and opening.get("line") and opening.get("line") != PLACEHOLDER:
            plan["opening"] = {**opening, "line": PLACEHOLDER, "draft_unchecked": opening.get("line")}
            fixed.append(f"opening left for a native {lang.title()} speaker (prompt rule)")
        lead = seller_data.get("lead") or {}
        line = str((plan.get("opening") or {}).get("line") or "")
        same = str(lead.get("contact_name") or "").strip().lower() == str(lead.get("company_name") or "").strip().lower() != ""
        if same and re.match(r"\s*[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?\s+ji\s*,\s*", line):
            # The contact is the company name: no name, no "ji" (prompt rule) — "Agarwal ji, IndiaMART se VANI." → "IndiaMART se VANI."
            plan["opening"]["line"] = re.sub(r"^\s*[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?\s+ji\s*,\s*", "", line)
            fixed.append("opening: name and 'ji' removed (the contact is the company)")
    return fixed


def enforce_bounds(out: dict) -> list[str]:
    """`bounds` is a fixed rule (±25 pace/mix, ±15 formality/warmth, +15 when G, clipped) — set it in code."""
    fixed = []
    for name, spread in SPREAD.items():
        dial = (((out.get("persona") or {}).get("dials") or {}).get(name)) or {}
        pos = dial.get("pos")
        if not isinstance(pos, (int, float)):
            continue
        width = spread + (15 if dial.get("confidence") == "G" else 0)
        want = [max(0, round(pos - width)), min(100, round(pos + width))]
        if dial.get("bounds") != want:
            fixed.append(f"{name} bounds {dial.get('bounds')} → {want}")
            dial["bounds"] = want
    return fixed


# ------------------------------------------------------------------------------------------------- downstream

DIAL_LANG = {"hindi": "hi-IN", "english": "en-IN", "tamil": "ta-IN", "telugu": "te-IN", "marathi": "mr-IN", "gujarati": "gu-IN", "bengali": "bn-IN",
             "kannada": "kn-IN", "malayalam": "ml-IN", "punjabi": "pa-IN", "odia": "od-IN", "hinglish": "hi-IN"}


def to_delivery(gen: dict) -> dict:
    """Generator dials → the live bot's delivery factors (pace multiplier, Hinglish mix, formality, warmth, language)."""
    dials = ((gen or {}).get("persona") or {}).get("dials") or {}
    voice = ((gen or {}).get("persona") or {}).get("voice") or {}

    def pos(name: str, default: int) -> int:
        try:
            return int((dials.get(name) or {}).get("pos", default))
        except (TypeError, ValueError):
            return default
    pace = round(0.85 + pos("pace", 45) / 100 * 0.35, 2)  # 0 → 0.85×, 100 → 1.20×
    mix = pos("mix", 30)
    lang = DIAL_LANG.get(str(voice.get("language") or "Hindi").split()[0].lower(), "hi-IN")
    return {"pace": pace, "hinglish_mix": "none" if mix < 15 else "low" if mix < 40 else "medium" if mix < 70 else "high",
            "formality": "casual" if pos("formality", 55) < 35 else "formal" if pos("formality", 55) > 70 else "respectful",
            "warmth": "low" if pos("warmth", 70) < 35 else "high" if pos("warmth", 70) > 70 else "medium", "language": lang}


STRENGTH = {"H": "strong", "M": "weak", "G": "guess"}


# Spoken reason for a regional-language seller while the plan's opening waits for a native speaker: English, neutral,
# and only what the prompt's purpose rules allow for that trigger (no facts, no sale assumed).
TRIGGER_HOOK_EN = {
    "open_request": "I'm calling about the request you raised with us — has it been sorted out?",
    "follow_up_due": "You had asked us to call back — how do things stand now?",
    "hot_lead": "I'm calling about your recent activity on IndiaMART — do you have two minutes?",
    "unresolved_last_interaction": "I'm following up on our last conversation — how do things stand now?",
}


def opening_hook(line: str) -> str:
    """The plan's opening without its greeting and self-introduction — the teleprompter adds the bot's own:
    "Farhan ji, IndiaMART se VANI. 6 October ka ticket abhi khula hai, kya update mila?" → "6 October ka ticket … mila?"."""
    parts = [x for x in re.split(r"(?<=[.?!])\s+", str(line or "").strip()) if x]
    parts = [x for x in parts if x.endswith("?") or not re.search(r"\bIndiaMART se\b", x, re.I)]
    hook = re.sub(r"^\s*(?:Namaste|Namaskar|Hello)?\s*,?\s*(?:[A-Z][\w.]*\s+)?ji\s*[,.]\s*", "", " ".join(parts)).strip()
    return hook[:1].upper() + hook[1:]


def to_persona(result: dict, base: dict, settings) -> dict:
    """Overlay a generated persona on the rules persona so every downstream consumer (teleprompter, switch log, live
    bot, persona index) keeps working: summary, title, tags, delivery factors from the dials, opening and plan items."""
    out = result.get("persona") if isinstance(result.get("persona"), dict) else {}
    if not out:
        return base
    p = json.loads(json.dumps(base, default=str))
    persona = out.get("persona") or {}
    dials = persona.get("dials") or {}
    delivery = to_delivery(out)
    p["summary"] = persona.get("summary") or p.get("summary")
    p["headline"] = persona.get("title")
    p["tags"] = [{"tag": str(t)[:60], "score": 70, "reason": "Persona generator", "sources": []} for t in (out.get("basics") or {}).get("persona_tags") or []] or p.get("tags")
    f = p.setdefault("factors", {})

    def put(name: str, value: Any, dial: dict | None, reason: str) -> None:
        if value in (None, "", []):
            return
        conf = (dial or {}).get("confidence", "M")
        f[name] = {"value": value, "score": {"H": 85, "M": 65, "G": 40}.get(conf, 55), "strength": STRENGTH.get(conf, "weak"),
                   "reason": reason[:200], "sources": [], "origin": "llm"}
    voice = persona.get("voice") or {}
    put("language", delivery["language"], {"confidence": voice.get("confidence", "G")}, f"Persona generator: {voice.get('why') or voice.get('language')}")
    put("hinglish_mix", delivery["hinglish_mix"], dials.get("mix"), f"Dial mix {(dials.get('mix') or {}).get('pos')}: {(dials.get('mix') or {}).get('why', '')}")
    put("pace", max(float(settings.get("persona.pace_min", 0.8)), min(float(settings.get("persona.pace_max", 1.25)), delivery["pace"])), dials.get("pace"),
        f"Dial pace {(dials.get('pace') or {}).get('pos')}: {(dials.get('pace') or {}).get('why', '')}")
    put("formality", delivery["formality"], dials.get("formality"), f"Dial formality {(dials.get('formality') or {}).get('pos')}: {(dials.get('formality') or {}).get('why', '')}")
    put("warmth", delivery["warmth"], dials.get("warmth"), f"Dial warmth {(dials.get('warmth') or {}).get('pos')}: {(dials.get('warmth') or {}).get('why', '')}")
    plan = out.get("call_plan") if isinstance(out.get("call_plan"), dict) else None
    if plan:
        trig = plan.get("trigger") or {}
        line = (plan.get("opening") or {}).get("line")
        if line and line != PLACEHOLDER:  # the native-speaker placeholder is never spoken; the rules opening stays
            put("opening", line, {"confidence": "M"}, f"Call plan · {trig.get('type')}: {trig.get('detail', '')}")
            hook = opening_hook(line)
            if hook:  # the teleprompter speaks the bot's own greeting + this reason for the call (teleprompter.opening)
                p.setdefault("teleprompter", {})["opening_hook"] = hook
        elif line == PLACEHOLDER and TRIGGER_HOOK_EN.get(trig.get("type")):
            p.setdefault("teleprompter", {})["opening_hook"] = TRIGGER_HOOK_EN[trig["type"]]
        put("personalization", [x.get("text") for x in plan.get("points_to_cover") or [] if isinstance(x, dict)], {"confidence": "M"}, "Call plan points to cover")
        put("next_action", plan.get("next_step"), {"confidence": "M"}, f"Call plan · purpose: {plan.get('purpose', '')}")
        p.setdefault("teleprompter", {})["avoid"] = list(dict.fromkeys((plan.get("avoid") or []) + ["Quoting any price"]))[:5]
        p["teleprompter"]["questions"] = plan.get("questions_to_ask") or []
        if plan.get("questions_to_ask"):
            p["teleprompter"]["first_question"] = str(plan["questions_to_ask"][0])
    p["engine"] = "persona_generator_v0.3"
    p["origin"] = "llm"
    p["model"] = result.get("model")
    p["generated"] = {"persona": out, "evaluation": result.get("evaluation"), "attempts": result.get("attempts"), "ms": result.get("ms"),
                      "expected_triggers": result.get("expected_triggers")}
    return p
