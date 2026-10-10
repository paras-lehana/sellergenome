"""Normalise the real IndiaMART responses (seen through the gateway on 2026-10-09) into small records.

The persona and the evidence list read these records, never the raw payloads. Each parser accepts
the real shape and the production-shaped mock (they are the same), tolerates missing keys, and
removes contact details from free text: emails, phone numbers and URLs never reach the LLM.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

IST = timezone(timedelta(hours=5, minutes=30))
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
URL = re.compile(r"https?://\S+")
# Free-text names of IndiaMART staff (e.g. "change their Relationship Manager Kishor K Jagtap").
STAFF = re.compile(r"((?:Relationship Manager|RM|Executive|Manager)\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]*\.?){0,3})")
# Financial identifiers that appear in STS ticket text (already partly masked upstream) never reach the LLM.
FINANCIAL = re.compile(r"(Account(?:\s+(?:Number|No\.?))?\s*[:=]\s*)\S+|\b[A-Z]{4}0[A-Z0-9]{6}\b|\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
# A person's name right after a greeting ("Hi Alok,", "Dear Mr Sharma") — buyers' and sellers' names stay out.
GREETING = re.compile(r"\b((?:Hi|Hello|Hey|Dear|Namaste|Namaskar)\s+(?:Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+|Sir\s+)?)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")


def scrub(text: Any) -> str:
    """Remove emails, phone numbers, URLs and staff names from free text."""
    out = html.unescape(str(text or ""))
    out = EMAIL.sub("[email]", out)
    out = URL.sub("[link]", out)
    out = PHONE.sub("[phone]", out)
    out = FINANCIAL.sub(lambda m: (m.group(1) + "[account]") if m.group(1) else "[id]", out)
    out = STAFF.sub(lambda m: m.group(1) + "[name]", out)
    out = GREETING.sub(lambda m: m.group(1) + "[name]", out)
    return re.sub(r"\s+", " ", out).strip()


def _date(text: str, formats: tuple[str, ...]) -> datetime | None:
    text = (text or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def within_days(at: datetime | None, days: int, now: datetime | None = None) -> bool:
    return bool(at) and (now or datetime.now(IST)) - at <= timedelta(days=days)


# ---- STS history -----------------------------------------------------------------------------------

STS_ENTRY = re.compile(
    r"(?P<at>\d{1,2} [A-Za-z]{3} \d{4} \d{1,2}:\d{2}(?::\d{2})? [AP]M),\s*Updated by\s*(?P<by>.*?)(?:&nbsp;|\s)+through\s*(?P<via>[^<]*)",
    re.IGNORECASE)


def sts_entries(payload: Any) -> list[dict]:
    """STS ``newhistory``: ``[{"data": ["<url-encoded HTML>"], "status": "200"}]`` → dated entries, newest first."""
    blobs = []
    for item in payload if isinstance(payload, list) else [payload]:
        if isinstance(item, dict):
            blobs += [unquote(x) for x in item.get("data") or [] if isinstance(x, str)]
    entries = []
    for blob in blobs:
        starts = list(STS_ENTRY.finditer(blob))
        for i, m in enumerate(starts):
            chunk = blob[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(blob)]
            comments = re.findall(r"Comments:\s*(.*?)(?=<br|$)", chunk, flags=re.IGNORECASE | re.DOTALL)
            text = comments[-1] if comments else re.sub(r"<[^>]+>", " ", chunk)
            text = re.split(r"Comments:\s*", text, flags=re.IGNORECASE)[-1]  # nested ticket boilerplate
            at = _date(m.group("at").title(), ("%d %b %Y %I:%M:%S %p", "%d %b %Y %I:%M %p"))
            by = scrub(m.group("by"))
            via = scrub(m.group("via"))
            entries.append({"at": at.isoformat() if at else m.group("at"), "by": by, "via": via,
                            "text": scrub(re.sub(r"<[^>]+>", " ", text))[:400], "kind": sts_kind(text), "automated": automated(by, via)})
    return entries


# Automated vs human STS updates (API guide). Calibrated on the real log (2026-10-10): channels such as "Online Payment",
# "WA_9696", "SP_UPDATE_STS_HIST" and "... PENNY DROP CONSUMER" are system notices; "WebERP" is the desk tool people use,
# unless the updater is a process ("Weberp Auto Process").
PROCESS = re.compile(r"\b(?:auto\w*\s+process|auto\s*process|process|system|cron|scheduler|batch|job|consumer|bot)\b", re.IGNORECASE)
# 5.9.8 (owner, GLID 12345678 "not seeing any call history"): executives also update through NSDMERP and log
# click-to-call through MERPC2C; only "WebERP" was known, so their meeting, calls and follow-up read as system notices.
# Every ERP desk tool is a person's channel unless the updater is a process.
HUMAN_CHANNELS = ("erp",)


def automated(by: str, via: str = "") -> bool:
    by, channel = (by or "").strip(), (via or "").strip().lower()
    if not by or PROCESS.search(by) or PROCESS.search(channel):  # "MERP MARKETPLACE PENNY DROP CONSUMER" is a process
        return True
    return not any(h in channel for h in HUMAN_CHANNELS)


TICKET = re.compile(r"(?:Updated\s+|Added\s+|Created\s+)?(?P<type>[A-Za-z/ &-]{3,60}?)\s+Ticket\s*\(\d+\)", re.IGNORECASE)
EMP_ID = re.compile(r"\b((?:with|by|emp(?:loyee)?(?:\s*id)?|executive|agent)\s*[:#-]?\s*)\d{5,7}\b", re.IGNORECASE)


AMOUNT = re.compile(r"(?:\bof\s+)?(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d+)?(?:\s*/-)?", re.IGNORECASE)
# Operations, verification, risk and legal notices never reach a persona (API guide: never mention).
OPS = re.compile(r"penny\s*drop|verification failed|could not verify|fraud|risk flag|refund|legal|chargeback|blacklist|suspend|"
                 r"buyer[- ]seller (?:conflict|dispute)", re.IGNORECASE)
PAYMENT_ITEM = re.compile(r"for\s+(?P<item>.+?)\s+(?:of\s+)?(?:\[amount\]|from\b)", re.IGNORECASE)


def persona_safe(text: Any) -> str:
    """Free text bound for a persona prompt: scrubbed, amounts masked, and every sentence about refunds, legal matters,
    fraud or failed verification dropped (API guide: never mention)."""
    out = AMOUNT.sub("[amount]", scrub(text))
    return " ".join(s for s in re.split(r"(?<=[.;!?])\s+", out) if s and not OPS.search(s)).strip()


def sts_for_persona(entries: list[dict]) -> list[dict]:
    """What the persona may read from STS: dated entries, human vs system, ticket entries reduced to their type (never
    their content), amounts and employee ids removed, operations/risk notices dropped, automated payment attempts and
    repeated notices merged into one entry with a date range."""
    out: list[dict] = []
    payments: dict | None = None
    staff = [e.get("by") or "" for e in entries if not PROCESS.search(e.get("by") or "")]
    for e in sts_merge(entries):
        raw = e.get("text") or ""
        if OPS.search(raw):
            continue
        text = staff_safe(AMOUNT.sub("[amount]", EMP_ID.sub(lambda m: m.group(1) + "[id]", raw)), staff)
        date = str(e.get("at") or "")[:10]
        if e.get("kind") == "payment" and e.get("automated"):
            item_name = (PAYMENT_ITEM.search(text) or {}) and PAYMENT_ITEM.search(text).group("item")
            if payments is None:
                payments = {"date": date, "source": "system", "kind": "payment", "items": [], "repeated": 0, "from_date": date}
                out.append(payments)
            payments["repeated"] += e.get("count", 1)
            payments["from_date"] = str(e.get("first_at") or date)[:10]
            item_name = re.sub(r"\s*-\s*\d+$", "", item_name) if item_name else item_name  # "Single India BuyLead - 399" (a price tier)
            if item_name and item_name not in payments["items"]:
                payments["items"].append(item_name)
            continue
        ticket = TICKET.search(text)
        item = {"date": date, "source": "system" if e.get("automated") else "person", "kind": e.get("kind")}
        if ticket or e.get("kind") in ("ticket", "complaint"):
            item.update(kind="ticket", ticket_type=(ticket.group("type").strip() if ticket else "Support request"))
        else:
            item["text"] = text[:300]
            status, secs = CALL_STATUS.search(text), CALL_SECONDS.search(text)
            if item["kind"] == "call" and status:  # click-to-call log of an executive's call
                answered = re.sub(r"\s+", "", status.group("status").lower()) == "answered"
                item.update(answered=answered, text=f"Outgoing call by an IndiaMART executive, {'answered' if answered else 'not answered'}"
                            + (f", {secs.group('s')} seconds" if secs and answered else ""))
        if e.get("count", 1) > 1:
            item.update(repeated=e["count"], from_date=str(e.get("first_at") or "")[:10])
        out.append(item)
    if payments:
        payments["text"] = f"Online payment attempted {payments['repeated']} time(s) for: {', '.join(payments['items'][:6]) or 'packages'}"
        del payments["items"]
    return out


def sts_merge(entries: list[dict]) -> list[dict]:
    """Repeated automated notices become one entry with a date range; human entries stay as they are. Newest first."""
    out: list[dict] = []
    groups: dict[str, dict] = {}
    for e in entries:
        key = re.sub(r"\d+", "#", (e.get("text") or "").lower())[:120]
        if not e.get("automated"):  # people's notes merge only when identical apart from numbers on the same day
            key = f"{key}|{str(e.get('at') or '')[:10]}|person"
        g = groups.get(key)
        if g:
            g["count"] += 1
            g["first_at"] = e["at"]
            continue
        g = {**e, "count": 1, "first_at": e["at"], "last_at": e["at"]}
        groups[key] = g
        out.append(g)
    return out


STS_KINDS = (  # first match wins: a follow-up note that mentions "payment follow up" is a follow-up, not a payment
    (r"complain", "complaint"), (r"\bticket\b", "ticket"), (r"sales appointment|appointment|meeting", "meeting"),
    (r"\btbro\b|follow[\s-]?up|call ?back", "follow_up"), (r"called through|call status|\bcall", "call"),
    (r"payment", "payment"), (r"whatsapp", "whatsapp"), (r"renew", "renewal"))


def sts_kind(text: str) -> str:
    low = (text or "").lower()
    for pattern, kind in STS_KINDS:
        if re.search(pattern, low):
            return kind
    return "note"


# "Alok Kumar Singh - 112259": two to four capitalised words, then a 5–7 digit id (never "Ticket(10781494)").
STAFF_ID = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\s*[-(]\s*\d{5,7}(?!\d)\)?")
SENT_TO = re.compile(r"\b((?:sent|assigned|transferred|mail)\s+to\s*:?\s*-?\s*)[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}", re.IGNORECASE)
MASKED_PHONE = re.compile(r"\b\d{2,4}\*{3,}\d{2,4}\b")


def staff_safe(text: str, names: list[str] | tuple[str, ...] = ()) -> str:
    """No IndiaMART staff names or employee ids, and no (masked) phone numbers, in persona input."""
    text = STAFF_ID.sub("[executive]", text)
    clean = {re.sub(r"\s*\(\d+\)\s*$", "", n or "").strip() for n in names}
    for name in sorted((n for n in clean if len(n) > 3), key=len, reverse=True):
        text = re.sub(re.escape(name), "[executive]", text, flags=re.IGNORECASE)
    text = SENT_TO.sub(lambda m: m.group(1) + "[executive]", text)
    return MASKED_PHONE.sub("[phone]", text)


CALL_STATUS = re.compile(r"call\s*status\s*:\s*(?P<status>not\s*answered|answered|busy|no\s*answer|failed)", re.IGNORECASE)
CALL_SECONDS = re.compile(r"call\s*duration\s*:\s*(?P<s>\d+)\s*sec", re.IGNORECASE)


# ---- PNS -------------------------------------------------------------------------------------------

def pns_calls(payload: Any, glid: str) -> list[dict]:
    """PNS call insights. Rows where this GLID is the BUYER describe their buying, not their selling."""
    rows = payload.get("data") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    out = []
    for i, call in enumerate(rows or []):
        if not isinstance(call, dict):
            continue
        ex = call.get("extracted_data") or {}
        md = ex.get("metadata") or {}
        intent = md.get("buyer_intent") or {}
        products = [p.get("product_name") for p in ex.get("products") or [] if isinstance(p, dict) and p.get("product_name")]
        qty = next(((p.get("quantity_required") or {}) for p in ex.get("products") or [] if isinstance(p, dict) and p.get("quantity_required")), {})
        as_seller = str(call.get("SELLER_GLID", "")) == str(glid)
        out.append({
            "index": i, "at": call.get("call_timestamp"), "role": "seller" if as_seller else "buyer",
            "counterpart_glid": str(call.get("BUYER_GLID") if as_seller else call.get("SELLER_GLID") or ""),
            "product": call.get("mcat_name") or (products[0] if products else None), "products": products,
            "quantity": f"{qty.get('value')} {qty.get('unit') or ''}".strip() if qty.get("value") is not None else None,
            "intent": intent.get("intent_level"), "narrative": scrub(intent.get("narrative"))[:300],
            "readiness": (ex.get("lead_tag") or {}).get("deal_readiness"),
            "blockers": [scrub(b.get("category") or b.get("detail") if isinstance(b, dict) else b)
                         for b in (ex.get("lead_tag") or {}).get("deal_blockers") or []][:4],
            "outcome": (md.get("call_outcome") or {}).get("category"),
            "outcome_note": scrub((md.get("call_outcome") or {}).get("conclusion_notes"))[:240],
            "language": md.get("primary_language"), "languages": [str(x) for x in md.get("all_languages") or []][:4],
            "purpose": md.get("call_purpose"), "buyer_type": (md.get("call_type") or {}).get("type"),
            "callback": bool((ex.get("next_steps") or {}).get("callback")),
            "seller_next": [scrub(x) for x in (ex.get("next_steps") or {}).get("seller_next_steps") or []][:3],
            "buyer_queries": [scrub(q.get("query")) for q in ((md.get("additional_details") or {}).get("buyer_queries") or []) if isinstance(q, dict)][:4],
            "day": str(call.get("call_timestamp") or "")[:10] or None,
        })
    return out


def pns_stats(calls: list[dict]) -> dict:
    """Patterns over the seller-side calls (API guide: records are AI-extracted and can disagree — trust patterns, count
    distinct buyers and days, a callback is a promise not a kept one, never judge the seller's responsiveness)."""
    mine = [c for c in calls if c.get("role") == "seller"]
    langs: dict[str, int] = {}
    for c in mine:
        for lang in ([c.get("language")] if c.get("language") else []) + [x for x in c.get("languages") or [] if x != c.get("language")]:
            langs[lang] = langs.get(lang, 0) + 1
    return {"calls": len(mine), "distinct_buyers": len({c["counterpart_glid"] for c in mine if c.get("counterpart_glid")}),
            "distinct_days": len({c["day"] for c in mine if c.get("day")}),
            "high_intent": sum(1 for c in mine if str(c.get("intent") or "").lower() == "high"),
            "warm": sum(1 for c in mine if str(c.get("readiness") or "").lower() == "warm"),
            "callbacks_promised": sum(1 for c in mine if c.get("callback")),
            "b2b": sum(1 for c in mine if str(c.get("buyer_type") or "").upper() == "B2B"),
            "purposes": {p: sum(1 for c in mine if c.get("purpose") == p) for p in {c.get("purpose") for c in mine if c.get("purpose")}},
            "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1]))}


# ---- LMS -------------------------------------------------------------------------------------------

def _response(payload: Any) -> dict:
    """``payload["response"]`` when it is an object; LMS answers ``"no data found"`` (a string) with code 204."""
    inner = payload.get("response") if isinstance(payload, dict) else None
    return inner if isinstance(inner, dict) else {}


def lms_messages(payload: Any, glid: str, contact: str = "") -> list[dict]:
    rows = _response(payload).get("result") if isinstance(payload, dict) else payload
    out = []
    for i, m in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(m, dict):
            continue
        try:
            text = (json.loads(m.get("msg_text_json") or "{}") or {}).get("message_text") or ""
        except (ValueError, AttributeError):
            text = ""
        at = _date((m.get("msg_date") or "").strip(), ("%d-%m-%Y %H:%M:%S",))
        out.append({"index": i, "at": at.isoformat() if at else (m.get("msg_date") or "").strip(),
                    "from": "seller" if str(m.get("msg_sender_id")) == str(glid) else "contact",
                    "type": m.get("msg_ref_type") or m.get("msg_query_type"), "contact": str(contact), "text": scrub(text)[:300]})
    return out


def lms_total(payload: Any) -> int | None:
    try:
        return int(_response(payload).get("total_count"))
    except (AttributeError, TypeError, ValueError):
        return None


# ---- Enquiries and buy leads ----------------------------------------------------------------------

FIRST_COPY = re.compile(r"first\s*copy|1st\s*copy|replica|master\s*copy|clone", re.IGNORECASE)


def enquiries(payload: Any) -> list[dict]:
    """Enquiry API ``{status, code, error, response: {enquiries: [...]}}`` (API guide, 2026-10-09).

    ``enquiryISQ`` items are ``{DESC, RESPONSE}`` (older mocks used name/value). Buyer names are kept only as a hashed
    key to count distinct buyers (trimmed name + member-since) — never shown. Enquiries asking for "First Copy" (or other
    counterfeit grades) are flagged so every consumer can skip them.
    """
    import hashlib
    rows = _response(payload).get("enquiries")
    out = []
    for i, e in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(e, dict):
            continue
        at = _date((e.get("postedDate") or "")[:19], ("%Y-%m-%dT%H:%M:%S",))
        specs, quantity = [], None
        for q in e.get("enquiryISQ") or []:
            if not isinstance(q, dict):
                continue
            name = q.get("DESC") or q.get("name") or q.get("ISQ_NAME") or q.get("label")
            value = q.get("RESPONSE") or q.get("value") or q.get("ISQ_VALUE") or ""
            if name and str(name).strip().lower() in ("quantity", "quantity unit"):
                quantity = f"{quantity or ''} {value}".strip()
            if name or value:
                specs.append(f"{name}: {value}".strip(": "))
        flag = str(e.get("countryFlag") or "")
        text = " ".join([str(e.get("enquiryTitle") or "")] + specs)
        name = str(e.get("buyerName") or "").strip()
        checks = {k: bool(int(e.get(f"{k}Verified") or 0)) for k in ("mobile", "email", "address", "gst")}
        out.append({"index": i, "at": at.isoformat() if at else e.get("postedDate"), "product": e.get("enquiryTitle"),
                    "specs": [scrub(x) for x in specs][:6], "city": e.get("buyerCity"), "state": e.get("buyerState"),
                    "verified": checks, "checks_passed": sum(checks.values()), "quantity": quantity,
                    "foreign": bool(flag) and "in_flag" not in flag, "first_copy": bool(FIRST_COPY.search(text)),
                    "buyer_key": hashlib.sha256(f"{name.lower()}|{e.get('buyerMemberSince') or ''}".encode()).hexdigest()[:12] if name else None,
                    "buyer_since": (e.get("buyerMemberSince") or "")[:10] or None,
                    "query_id": e.get("queryId"), "name": e.get("buyerName")})
    return out


def enquiry_stats(rows: list[dict], now: datetime | None = None) -> dict:
    """Counts the guide asks for: 7- and 30-day windows, distinct buyers, verification, quantity given, foreign.
    First-Copy enquiries are excluded and only counted as skipped."""
    now = now or datetime.now(IST)
    kept = [r for r in rows if not r.get("first_copy")]
    dated = [(r, datetime.fromisoformat(r["at"])) for r in kept if isinstance(r.get("at"), str) and r["at"][:4].isdigit()]
    return {"listed": len(kept), "skipped_first_copy": len(rows) - len(kept),
            "last_7_days": sum(1 for _, at in dated if now - at <= timedelta(days=7)),
            "last_30_days": sum(1 for _, at in dated if now - at <= timedelta(days=30)),
            "distinct_buyers": len({r["buyer_key"] for r in kept if r.get("buyer_key")}),
            "with_quantity": sum(1 for r in kept if r.get("quantity")),
            "fully_verified": sum(1 for r in kept if r.get("checks_passed") == 4),
            "foreign": sum(1 for r in kept if r.get("foreign")),
            "categories": sorted({str(r.get("product")) for r in kept if r.get("product")})[:6]}


def lead_ids(payload: Any) -> list[str]:
    data = ((payload or {}).get("RESPONSE") or {}).get("DATA") if isinstance(payload, dict) else None
    ids = (data or {}).get("offerids") if isinstance(data, dict) else None
    return [str(x) for x in ids or [] if x]


def lead(payload: Any) -> dict | None:
    data = ((payload or {}).get("RESPONSE") or {}).get("DATA") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    qty = " ".join(str(x) for x in (data.get("ETO_OFR_QTY"), data.get("ETO_OFR_QTY_UNIT")) if x)
    at = _date(str(data.get("ETO_OFR_DATE") or "").title(), ("%d-%b-%y",))
    return {"title": data.get("ETO_OFR_TITLE"), "product": data.get("PRIME_MCAT_NAME") or data.get("ETO_OFR_GLCAT_MCAT_NAME"),
            "quantity": qty or None, "order_value": data.get("ORDER_VAL"), "at": at.date().isoformat() if at else data.get("ETO_OFR_DATE"),
            "buyer_glid": str(data.get("FK_GLUSR_USR_ID") or ""), "buyer_city": data.get("GLUSR_CITY"), "buyer_state": data.get("GLUSR_STATE"),
            "buyer_requirements": data.get("ETO_OFR_BUYER_TOT_REQUIREMENT"), "buyer_interests": data.get("ETO_OFR_BUYER_PRIME_MCATS"),
            "mobile_verified": str(data.get("ETO_OFR_BUYER_IS_MOB_VERF")) == "1", "status": data.get("PURCHASE_STATUS"),
            "name": data.get("GLUSR_NAME")}


# ---- Global Context (API guide 2026-10-09: data.{kycdetails, connectdetails, ticketsdetails, activitydetails, BLdetails}) ----

MISSING = {"", "na", "n/a", "none", "null", "-", "nil"}
MEETING_TEMPLATE = re.compile(r"(?:^|\s)(0?[1-7])[.)\-:\s]+")
MEETING_PARTS = {"1": "agenda", "2": "decision_maker", "3": "products", "4": "summary", "5": "roi", "6": "work_to_do", "7": "pending"}


def present(value: Any) -> Any:
    """Treat "", null, "None", "NA" and "-" as missing (guide + prompt rule 7)."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in MISSING:
        return None
    return value


def meeting_note(text: str) -> dict:
    """``meet_sts_update`` follows a numbered template (01 Agenda … 07 Pending) — split it on the numbers.
    The decision-maker part (names and numbers) is dropped."""
    text = scrub(text)
    parts: dict[str, str] = {}
    marks = list(MEETING_TEMPLATE.finditer(text))
    for i, m in enumerate(marks):
        key = MEETING_PARTS.get(m.group(1).lstrip("0"))
        chunk = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        chunk = re.sub(r"^(Meeting Agenda|Decision Maker Name\s*&\s*No|Major Products/Categories|Meeting Summary\s*&\s*Work Done|"
                       r"ROI/Business Generated in last 6 Months|Work to be Done Post Meeting|Comments/Pending work)\s*[:\-]?\s*", "", chunk.strip(),
                       flags=re.IGNORECASE)
        if key and key != "decision_maker" and present(chunk):
            parts[key] = chunk[:240]
    return parts or ({"summary": text[:240]} if present(text) else {})


def _customer_lines(transcript: str) -> list[str]:
    """The seller's own words in a transcript ("Customer:" lines) — used only to judge language, never quoted."""
    return [m.strip() for m in re.findall(r"Customer\s*:\s*(.+?)(?=(?:Executive|Customer)\s*:|$)", transcript or "", flags=re.IGNORECASE | re.DOTALL)
            if m.strip()]


def script_mix(lines: list[str]) -> dict:
    """Share of Latin vs Devanagari vs other Indic script letters in the seller's own words."""
    counts = {"latin": 0, "devanagari": 0, "other_indic": 0}
    for ch in " ".join(lines):
        o = ord(ch)
        if ch.isascii() and ch.isalpha():
            counts["latin"] += 1
        elif 0x0900 <= o <= 0x097F:
            counts["devanagari"] += 1
        elif 0x0980 <= o <= 0x0DFF:
            counts["other_indic"] += 1
    total = sum(counts.values())
    return {"letters": total, **{k: round(v / total, 2) if total else 0 for k, v in counts.items()}}


def global_context(payload: Any) -> dict:
    """Normalise the Global Context bundle into the blocks the persona needs (key meanings from the API guide).

    Rules from the guide applied here: a call row's timestamp is not proof it was answered; meetings are rows with
    ``meet_sucessful_date``; tickets give only type, date and whether in progress; competitor rows are counted, never
    named; transcripts are reduced to the seller's own lines for language only.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {}
    kyc = data.get("kycdetails") or {}
    conn = data.get("connectdetails") or {}
    tix = data.get("ticketsdetails") or {}
    act = data.get("activitydetails") or {}
    bl = data.get("BLdetails") or {}
    out: dict[str, Any] = {}
    if kyc:
        def num(v):
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return None
        out["kyc"] = {k: present(kyc.get(k)) for k in ("company_name", "customer_name", "first_name", "city", "gst_turnover", "gst_nature_of_business",
                                                         "company_type", "registered_since_with_indiamart", "customer_type", "payment_invoice_date",
                                                         "payment_receipt_date")}
        out["kyc"]["approved_product_count"] = num(kyc.get("approved_product_count"))
        out["kyc"]["top_city_rest_of_india"] = [c.strip() for c in str(kyc.get("top_city_rest_of_india") or "").split(",") if present(c.strip())][:8]
        out["kyc"]["last_call_details"] = [{"at": present(r.get("ENTERED_ON")), "comment": scrub(r.get("COMMENTS"))[:300]}
                                           for r in kyc.get("last_call_details") or [] if isinstance(r, dict) and present(r.get("COMMENTS"))][:5]
    rows, seen = [], set()
    for window in ("connect_7D", "connect_30D"):
        for r in conn.get(window) or []:
            if not isinstance(r, dict):
                continue
            key = (r.get("call_answered_date"), r.get("meet_sucessful_date"), r.get("video_verified_date"))
            if key in seen:
                continue
            seen.add(key)
            summary, note = present(scrub(r.get("call_answered_summary"))), present(scrub(r.get("call_sts_update")))
            transcript = r.get("call_answered_transcript") or ""
            meet, video = present(r.get("meet_sucessful_date")), present(r.get("video_verified_date"))
            customer = _customer_lines(transcript)
            rows.append({"at": present(r.get("call_answered_date")) or meet or video, "window": window,
                         "kind": "meeting" if meet else "video_meeting" if video else "call",
                         # A timestamp alone is not proof the call was answered: look for a summary, the seller's words or a note.
                         "answered": True if (summary or customer) else (None if note else False),
                         "summary": (summary or "")[:300] or None, "note": (note or "")[:300] or None,
                         "meeting": meeting_note(r.get("meet_sts_update")) if present(r.get("meet_sts_update")) else None,
                         "meet_date": meet, "video_note": present(scrub(r.get("video_meet_sts_update"))), "video_date": video,
                         "seller_script": script_mix(customer) if customer else None})
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    out["connects"] = rows
    out["last_call_summary"] = present(scrub(conn.get("last_call_summary")))
    tickets = []
    for window, path in (("in progress", ("wip_tickets",)), ("current", ("current", "tickets")), ("last 7 days", ("last_7_days", "tickets")),
                         ("last 8 to 90 days", ("last_8_to_90_days", "tickets"))):
        node: Any = tix
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        for t in node or [] if isinstance(node, list) else []:
            if isinstance(t, dict):
                key = (present(t.get("ticket_type")), present(t.get("ticket_issue_date")))
                known = next((x for x in tickets if (x["type"], x["date"]) == key), None)
                if known:  # the same ticket listed in two windows (e.g. wip_tickets and current)
                    known["in_progress"] = known["in_progress"] or window in ("in progress", "current")
                    continue
                tickets.append({"window": window, "type": key[0], "date": key[1], "in_progress": window in ("in progress", "current")})
    out["tickets"] = tickets
    windows = {}
    for w in ("last_week", "last_4_week", "last_month", "last_3_month"):
        node = act.get(w) if isinstance(act.get(w), dict) else {}
        windows[w] = {k: node.get(k) for k in ("bl_active_days", "bl_cons", "pns_call_picked", "pns_call_received", "prd_without_photo",
                                                "prd_without_price", "prd_without_ctg_mapping") if node.get(k) is not None}
    top = (act.get("top3mcat_data") or [{}])[0] if isinstance(act.get("top3mcat_data"), list) else (act.get("top3mcat_data") or {})
    mcats = top.get("mcat_list") if isinstance(top, dict) else None
    if isinstance(mcats, str):
        try:
            mcats = json.loads(mcats)
        except ValueError:
            mcats = None
    pref = act.get("location_preference") or (act.get("last_4_week") or {}).get("location_preference") or (act.get("last_month") or {}).get("location_preference")
    out["activity"] = {"enq_last_30_days": act.get("enq_last_30_days"), "enq_last_90_days": act.get("enq_last_90_days"),
                       "pns_received_30_days": act.get("pns_received_30_days"), "pns_received_90_days": act.get("pns_received_90_days"),
                       "windows": {k: v for k, v in windows.items() if v},
                       "top3mcat": {k: top.get(k) for k in ("local_bl", "total_bl", "local_paid_sellers", "total_paid_sellers") if isinstance(top, dict)}
                       | ({"categories": [m.get("mcat_name") or m.get("name") for m in mcats if isinstance(m, dict)][:3]} if isinstance(mcats, list) else {}),
                       "competitors_listed": len(act.get("top3mcat_competitors") or []),  # names are never used
                       "location_preference": pref if isinstance(pref, list) else [x.strip() for x in str(pref or "").split(",") if x.strip()]}
    if bl:
        out["buy_leads"] = {k: v for k, v in bl.items() if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit())}
        if isinstance(bl.get("active_bl_details"), list):
            out["buy_leads"]["active_listed"] = len(bl["active_bl_details"])
    return out


def get_context_segments(payload: Any) -> dict:
    """getContext ``data`` (shape unconfirmed). Returns {} for 'Mapping Not Found'."""
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


# ---- competitor listing (5.12.1) -----------------------------------------------------------------

def main_mcat(company_detail: Any) -> str | None:
    """The seller's main category id from the company API: DATA.BREADCRUMB.MCAT_ID, else the commonest product category."""
    data = company_detail.get("DATA") if isinstance(company_detail, dict) else None
    if not isinstance(data, dict):
        return None
    crumb = data.get("BREADCRUMB") if isinstance(data.get("BREADCRUMB"), dict) else {}
    if str(crumb.get("MCAT_ID") or "").strip().isdigit():
        return str(crumb["MCAT_ID"]).strip()
    counts: dict[str, int] = {}
    for item in data.get("PRDSERV") or []:
        for m in re.findall(r"\d+", str((item or {}).get("ITEM_GLMCAT_ID_LIST") or "")):
            counts[m] = counts.get(m, 0) + 1
    return max(counts, key=counts.get) if counts else None


def city_slug(city: Any) -> str:
    """The listing API's city parameter: lower case, spaces as hyphens ("Navi Mumbai" → "navi-mumbai")."""
    return re.sub(r"\s+", "-", str(city or "").strip().lower())


def _years(text: Any) -> str:
    m = re.match(r"\s*(\d+)\s*(?:yrs?|years?)", str(text or ""), re.I)
    return f"{m.group(1)} years on IndiaMART" if m else ""


def listing_sellers(payload: Any, own_glid: str = "") -> list[dict]:
    """Sellers in a products-listing answer, in rank order, without the seller's own rows.

    The confirmed shape (5.13.5) is ``data[]`` with COMPANY, gl_id, city, member_since ("14 yrs"), wt_avg,
    total_ratings_cnt, Trustseal_url, glusr_paid_url and prd_price. Only these public listing facts are kept: never
    phones, GST, addresses or emails. Other shapes fall back to any list of objects with a company name."""
    found: list[dict] = []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "COMPANY" in rows[0]:
        for r in rows:
            name, glid = str(r.get("COMPANY") or "").strip(), str(r.get("gl_id") or "").strip()
            if not name or (own_glid and glid == str(own_glid)):
                continue
            rating, count = str(r.get("wt_avg") or "").strip(), str(r.get("total_ratings_cnt") or "").strip()
            found.append({"company": name, "city": str(r.get("city") or "").strip(), "glid": glid,
                          "plan": "paid" if r.get("glusr_paid_url") else "",
                          "trustseal": bool(r.get("Trustseal_url")),
                          "rating": f"{rating} stars from {count} ratings" if rating and count and count != "0" else "",
                          "years": _years(r.get("member_since")),
                          "price_shown": bool(str(r.get("prd_price") or "").strip())})
    else:
        def pick(row: dict, *words: str) -> str:
            for k, v in row.items():
                if any(w in k.lower() for w in words) and isinstance(v, (str, int)) and str(v).strip():
                    return str(v).strip()
            return ""

        def walk(x: Any) -> None:
            if isinstance(x, list):
                for r in x:
                    if isinstance(r, dict):
                        name = pick(r, "companyname", "company_name", "compname", "company")
                        glid = pick(r, "glusr_id", "glusrid", "glid", "gl_id", "usr_id")
                        if name and glid != str(own_glid):
                            found.append({"company": name, "city": pick(r, "city"), "plan": pick(r, "custtype", "badge"), "glid": glid})
                        else:
                            walk(list(r.values()))
                    else:
                        walk(r)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)

        walk(payload)
    seen, out = set(), []
    for f in found:
        if f["company"].lower() not in seen:
            seen.add(f["company"].lower())
            out.append(f)
    return out


def listing_market(payload: Any, own_glid: str = "") -> dict:
    """What the listing says about the market: the category, whether the city filter applied, how many sellers list it
    across India, and where the seller stands in the first results (rank, or not shown). {} when the shape is unknown."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return {}
    rows = [r for r in payload["data"] if isinstance(r, dict)]
    own = next((i + 1 for i, r in enumerate(rows) if own_glid and str(r.get("gl_id") or "") == str(own_glid)), None)
    city = next((str(r.get("city")) for r in rows if r.get("city")), "") if str(payload.get("cityid") or "0") not in ("0", "") else ""
    num = lambda v: int(v) if str(v or "").isdigit() else None  # noqa: E731
    return {"category": str(payload.get("mcatname") or payload.get("mcat_display_name") or "").strip(),
            "city": city, "shown": len(rows), "own_rank": own,
            "paid": sum(1 for r in rows if r.get("glusr_paid_url")), "trustseal": sum(1 for r in rows if r.get("Trustseal_url")),
            "with_price": sum(1 for r in rows if str(r.get("prd_price") or "").strip()),
            "sellers_india": num(payload.get("out_total_unq_count")), "products_india": num(payload.get("out_total_count"))}   # city-scoped when city is set
