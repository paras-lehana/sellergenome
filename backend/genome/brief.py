"""The pre-call brief in the PM's "VANI Persona Studio" structure (docs/inputs/pm-design/README.md).

One function, ``build(run)``, turns a finished preparation run (production contract + Sarvam persona +
evidence) into the five blocks the PM designed:

1. Basic details — each value with where it came from, or "Not in file"
2. Engagement with IndiaMART — insights grouped (Intent, Decision, Objections, Reachability, Profile,
   Data gaps) with confidence H/M/G and dated evidence chips that open the exact source
3. Engagement with buyers — reply/pick-up ratios, catalogue facts, buyer-side insights
4. Conversation plan — headline, five delivery dials (voice, pace, language, formality, tone) and five
   steps (opening, personalise, objection, escalation, next step), each with confidence and "why"
5. Full record — counts, a dated timeline with outcomes, evidence rules and what is not in file

Nothing here invents a fact: every value comes from the contract or the persona, which already carry
origin and source pointers. Confidence: strong → H, weak → M, guess/default → G.
"""

from __future__ import annotations

import re
from typing import Any

from backend.genome.contract import chip_label, global_context_source, short_date
from backend.genome.parsers import present as _present

CONF = {"strong": "H", "weak": "M", "guess": "G", "default": "G", "high": "H", "medium": "M", "operator": "H"}
GROUP_TITLE = {"intent": "Intent", "decision": "Decision", "objections": "Objections", "reachability": "Reachability",
               "buyers": "Buyers", "profile": "Profile", "data_gaps": "Data gaps"}
LANG_NAME = {"hi-IN": "Hindi", "en-IN": "English", "te-IN": "Telugu", "ta-IN": "Tamil", "mr-IN": "Marathi", "gu-IN": "Gujarati",
             "bn-IN": "Bengali", "kn-IN": "Kannada", "ml-IN": "Malayalam", "pa-IN": "Punjabi", "od-IN": "Odia"}
MIX_POS = {"none": 6, "low": 28, "medium": 55, "high": 80}
FORMALITY = {"casual": (20, "Casual, friendly"), "respectful": (55, "Respectful, friendly"), "formal": (85, "Formal, precise")}
WARMTH = {"low": (20, "Reserved, to the point"), "medium": (55, "Warm, steady"), "high": (85, "Warm, patient, no pressure")}
RULE_TEXT = {
    "time": "Patient tone, one small step, for sellers who say they need time",
    "price": "No price on the call; offer a comparison meeting, for price-sensitive sellers",
    "lead_quality": "Acknowledge the lead-quality complaint before any pitch",
    "trust": "Lead with proof (verified buyers, TrustSEAL) for sellers who doubt the platform",
    "confusion": "Slow down, one idea per line, for sellers who get confused",
    "existing_executive": "Hand off to the existing executive instead of pitching",
    "not_interested": "Respect a firm no: one useful fact, then a callback offer",
    "none": "Move to the next step when there is no objection on record",
}


def _src(item: dict) -> dict:
    return {"api": item.get("api"), "pointer": item.get("pointer"), "origin": item.get("origin"),
            "label": item.get("label") or chip_label(item), "text": (item.get("text") or "")[:160]}


def _field(c: dict, name: str) -> dict | None:
    f = (c.get("fields") or {}).get(name)
    return f if f and f.get("availability") == "present" else None


def _row(label: str, value: Any, origin: str | None = None, api: str | None = None, pointer: str | None = None, note: str = "") -> dict:
    present = value not in (None, "", [], {})
    return {"label": label, "value": value if present else None, "origin": origin if present else None, "api": api if present else None,
            "pointer": pointer if present else None, "note": note if present else "", "missing": not present}


def _from_field(label: str, f: dict | None, note: str = "") -> dict:
    if not f:
        return _row(label, None)
    return _row(label, f["value"], f.get("origin"), f.get("api"), f.get("pointer"), note)


def _gc_kyc(run: dict) -> tuple[dict, dict | None]:
    """Global Context kycdetails and the answer that held it (generateContextUID or getContext — whichever has it)."""
    results = run.get("results") or {}
    api = global_context_source(results)
    r = results.get(api) or {}
    resp = r.get("response") if r.get("status") == "ok" else None
    kyc = (resp.get("data") or {}).get("kycdetails") if isinstance(resp, dict) and isinstance(resp.get("data"), dict) else None
    return (kyc, {**r, "api": api}) if isinstance(kyc, dict) and kyc else ({}, None)


def _gc_row(label: str, kyc: dict, gc: dict | None, key: str, value=None, note: str = "") -> dict | None:
    """A Basic details row straight from Global Context's kycdetails; None when the key is empty ("NA", "", null)."""
    raw = _present(kyc.get(key))
    if raw is None or not gc:
        return None
    return _row(label, value if value is not None else raw, gc.get("origin"), gc["api"], f"/data/kycdetails/{key}", note)


def _tenure_words(text: str) -> str:
    """Global Context's "1 Y 4 M" → "1 year 4 months"; anything else stays as given."""
    m = re.fullmatch(r"\s*(\d+)\s*Y\s*(\d+)\s*M\s*", str(text), re.I)
    if not m:
        return str(text)
    y, mo = int(m.group(1)), int(m.group(2))
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{mo} month{'s' if mo != 1 else ''}"] if mo or not y else [])
    return " ".join(parts)


SOURCE_NOTE = {"company.detail": "company page", "company.alias": "company page", "dataset.seller": "supplied file",
               "context.global": "global context", "context.getcontext": "global context", "context.insights": "insights API", "sarvam.locality": "Sarvam from addresses"}


def basics(run: dict) -> dict:
    c, seller, persona = run.get("contract") or {}, run.get("seller") or {}, run.get("persona") or {}
    facts = c.get("company_facts") or {}
    signals = c.get("dataset_signals") or {}

    def fact(*titles: str) -> dict | None:
        for t in titles:
            if t in facts:
                return facts[t]
        return None

    nature = _field(c, "business_nature")
    rows = [
        _from_field("Seller", _field(c, "contact_name")),
        _from_field("Company", _field(c, "company_name")),
        _from_field("Nature of business", nature, f"from {SOURCE_NOTE.get((nature or {}).get('api'), 'record')}" if nature else ""),
        _from_field("City", _field(c, "city")),
        _from_field("State", _field(c, "state")),
    ]
    # Owner, 2026-10-10: turnover comes from the Global Context answer itself (kycdetails.gst_turnover); the company
    # page and the supplied file only fill in when Global Context has none.
    kyc, gc = _gc_kyc(run)
    turnover_row = _gc_row("Turnover", kyc, gc, "gst_turnover", note="GST turnover")
    if not turnover_row:
        turnover = fact("Annual Turnover")
        if not turnover and signals.get("annual_turnover"):
            turnover = {**signals["annual_turnover"], "origin": "local_db"}
        turnover_row = _row("Turnover", (turnover or {}).get("value"), (turnover or {}).get("origin"), (turnover or {}).get("api"), (turnover or {}).get("pointer"))
    rows.append(turnover_row)
    legal = fact("Legal Status of Firm", "LEGALSTATUS")
    rows.append(_row("Business type", (legal or {}).get("value"), (legal or {}).get("origin"), (legal or {}).get("api"), (legal or {}).get("pointer"))
                if legal else _gc_row("Business type", kyc, gc, "company_type") or _row("Business type", None))
    year = fact("Year of Establishment", "YOE")
    if year:
        rows.append(_row("Established", year["value"], year["origin"], year["api"], year["pointer"]))
    lead_type = _field(c, "data_type")
    rows.append(_from_field("Hot lead type", lead_type, "default" if (lead_type or {}).get("origin") == "default" else ""))
    lang = (persona.get("factors") or {}).get("language") or {}
    rows.append(_row("Language", LANG_NAME.get(lang.get("value"), lang.get("value") or LANG_NAME.get(seller.get("language"))),
                     "llm" if lang.get("sources") else "default", "sarvam.aggregate", "/factors/language",
                     "confirmed in evidence" if lang.get("strength") == "strong" else "a guess — confirm on the call"))
    since = _present(kyc.get("registered_since_with_indiamart"))
    tenure = (c.get("seller_summary") or {}).get("tenure_days")
    rows.append(_gc_row("On IndiaMART", kyc, gc, "registered_since_with_indiamart", _tenure_words(since)) if since else
                _row("On IndiaMART", f"{int(tenure):,} days" if isinstance(tenure, (int, float)) else None, "mock" if tenure else None,
                     "context.insights", "/Data/Insights/seller_summary/tenure_days"))
    gst = fact("GST No.", "GST")
    if gst:
        rows.append(_row("GST", gst["value"], gst["origin"], gst["api"], gst["pointer"]))
    products = [{"name": p["name"], "origin": p.get("origin"), "api": "company.detail", "pointer": f"/DATA/PRDSERV/{i}/ITEM_NAME"}
                for i, p in enumerate(c.get("products") or []) if p.get("name")][:8]
    flags = []
    contact, company = _field(c, "contact_name"), _field(c, "company_name")
    if contact and company and str(contact["value"]).strip().lower() == str(company["value"]).strip().lower():
        flags.append("Contact name is the company name")
    # Owner, 2026-10-10: a field with no data is left out rather than shown as "Not in file".
    return {"rows": [r for r in rows if not r["missing"]], "products": products, "flags": flags}


def _rule_insights(c: dict) -> list[dict]:
    """When Sarvam gave no grouped insights (rules fallback or an older run), derive them from the record."""
    out = []
    by_cat = {"purchase_intent": "intent", "objection_or_blocker": "objections", "last_interaction": "reachability",
              "engagement_history": "reachability", "market_opportunity": "buyers", "catalog_gaps": "profile",
              "competitor_landscape": "profile"}
    for ins in c.get("insights") or []:
        if ins.get("headline") and ins.get("category") in by_cat:
            out.append({"group": by_cat[ins["category"]], "text": ins["headline"][:140], "confidence": "medium",
                        "sources": [_src({**ins, "label": "Insights"})]})
    complaints = [h for h in c.get("history") or [] if h.get("api") == "sts.history" and "complaint" in str(h.get("kind"))]
    if complaints:
        out.append({"group": "objections", "text": f"{len(complaints)} complaint(s) on record — latest: {complaints[0]['summary'][:80]}",
                    "confidence": "high", "sources": [_src({**h, "label": chip_label({**h})}) for h in complaints[:3]]})
    payments = [h for h in c.get("history") or [] if "payment" in str(h.get("kind"))]
    if payments:
        out.append({"group": "intent", "text": f"{len(payments)} payment attempt(s) recently", "confidence": "high",
                    "sources": [_src({**h, "label": chip_label(h)}) for h in payments[:3]]})
    return out[:10]


def engagement(run: dict) -> dict:
    c, persona = run.get("contract") or {}, run.get("persona") or {}
    insights = persona.get("insights") or _rule_insights(c)
    groups: dict[str, list] = {}
    for ins in insights:
        groups.setdefault(ins["group"], []).append({
            "text": ins["text"], "confidence": CONF.get(ins.get("confidence"), "M"),
            "evidence": [{**s, "label": s.get("label") or chip_label(s)} for s in ins.get("sources") or []][:4]})
    dates = sorted(d for d in (_iso(h.get("at")) for h in c.get("history") or []) if d)
    span = f"{_month(dates[0])} to {_month(dates[-1])}" if dates else "dates not in file"
    order = ["intent", "decision", "objections", "reachability", "buyers", "profile", "data_gaps"]
    return {"groups": [{"key": k, "title": GROUP_TITLE[k], "items": groups[k]} for k in order if k in groups],
            "count": sum(len(v) for k, v in groups.items() if k != "data_gaps"), "span": span,
            "origin": "llm" if persona.get("insights") else "rules"}


def _iso(value: Any) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else None


def _month(day: str) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(day[5:7]) - 1]} {day[:4]}"


def buyers(run: dict) -> dict:
    c = run.get("contract") or {}
    fields, signals = c.get("fields") or {}, c.get("dataset_signals") or {}
    stats, notes = [], []

    def num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rec, rep = num((signals.get("eng_enq_received_90d") or {}).get("value")), num((signals.get("eng_enq_replies_90d") or {}).get("value"))
    if rec:
        stats.append({"n": int(rep or 0), "d": int(rec), "label": "enquiries replied · 90 days", "f": (rep or 0) / rec, "origin": "local_db",
                      "api": "dataset.seller", "pointer": "/row/eng_enq_replies_90d"})
        if not rep:
            notes.append({"text": "No enquiry replies recorded in 90 days", "confidence": "H", "note": f"0 of {int(rec)} (supplied dataset)"})
    pickup = num((signals.get("eng_pickup_ratio_90d") or {}).get("value"))
    if pickup is not None:
        stats.append({"n": f"{round(pickup * 100) if pickup <= 1 else round(pickup)}%", "d": None, "label": "buyer calls picked up · 90 days",
                      "f": pickup if pickup <= 1 else pickup / 100, "origin": "local_db", "api": "dataset.seller", "pointer": "/row/eng_pickup_ratio_90d"})
    got, picked = fields.get("pns_received_30d") or {}, fields.get("pns_picked_30d") or {}
    if got.get("availability") == "present" and num(got.get("value")):
        n, d = int(num(picked.get("value")) or 0), int(num(got["value"]))
        stats.append({"n": n, "d": d, "label": "phone leads answered · 30 days", "f": n / d if d else 0, "origin": picked.get("origin") or got.get("origin"),
                      "api": got.get("api"), "pointer": got.get("pointer")})
        notes.append({"text": f"Answered {n} of {d} buyer calls in 30 days", "confidence": "H", "note": "phone leads (PNS)"})
    enq = fields.get("enquiries_received_30d") or {}
    if enq.get("availability") == "present":
        stats.append({"n": enq["value"], "d": None, "label": "enquiries · last 30 days", "origin": enq.get("origin"), "api": enq.get("api"), "pointer": enq.get("pointer")})
    msgs = c.get("messages") or []
    if msgs:
        mine = sum(1 for m in msgs if m["from"] == "seller")
        stats.append({"n": mine, "d": len(msgs), "label": "chat messages sent by the seller", "f": mine / len(msgs), "origin": msgs[0]["origin"],
                      "api": msgs[0]["api"], "pointer": "/response/result"})
    if c.get("leads"):
        stats.append({"n": len(c["leads"]), "d": None, "label": "recent buy leads purchased (expanded)", "origin": c["leads"][0]["origin"],
                      "api": c["leads"][0]["api"], "pointer": "/RESPONSE/DATA"})
    seller_calls = [p for p in c.get("pns_calls") or [] if p.get("role") == "seller"]
    high = [p for p in seller_calls if str(p.get("intent")).lower() == "high"]
    if high:
        notes.append({"text": "Buyers call with high intent", "confidence": "H", "note": f"{len(high)} of {len(seller_calls)} PNS calls"})
    products = c.get("products") or []
    catalogue = []
    if products:
        priced = sum(1 for p in products if p.get("price"))
        photos = sum(1 for p in products if p.get("has_image"))
        catalogue = [f"{len(products)} products", f"{priced} with price", f"{photos} with photos"]
        if priced < len(products):
            notes.append({"text": "Catalogue is missing prices", "confidence": "H", "note": f"{len(products) - priced} of {len(products)} products"})
    return {"stats": stats[:5], "catalogue": catalogue, "insights": notes[:4]}


def _f(persona: dict, name: str) -> dict:
    return (persona.get("factors") or {}).get(name) or {}


def _text(value: Any) -> str:
    if isinstance(value, list):
        first = value[0] if value else ""
        if isinstance(first, dict):
            return " — ".join(str(v) for v in first.values() if v)[:160]
        return str(first)[:160]
    return str(value or "")[:160]


def plan(run: dict) -> dict:
    persona = run.get("persona") or {}
    lang = _f(persona, "language")
    lang_name = LANG_NAME.get(lang.get("value"), lang.get("value") or "Hindi")
    pace = _f(persona, "pace")
    pv = float(pace.get("value") or 1.0)
    mix = _f(persona, "hinglish_mix")
    form = _f(persona, "formality")
    warm = _f(persona, "warmth")
    voice = _f(persona, "voice")

    def dial(f: dict, pos: int, value: str, left: str, right: str) -> dict:
        return {"pos": max(2, min(98, pos)), "value": value, "c": CONF.get(f.get("strength"), "G"), "why": (f.get("reason") or "No evidence")[:110],
                "left": left, "right": right, "sources": f.get("sources") or []}

    pace_text = "Slow, with pauses" if pv < 0.9 else "Measured" if pv < 1.0 else "Normal" if pv <= 1.08 else "Brisk, short lines"
    mix_v = str(mix.get("value") or "medium")
    mix_text = lang_name if lang.get("value") != "hi-IN" else {"none": "Pure Hindi", "low": "Hindi, light Hinglish", "medium": "Hinglish",
                                                                 "high": "Hinglish, English-heavy"}.get(mix_v, "Hinglish")
    fv = FORMALITY.get(str(form.get("value")), (55, str(form.get("value") or "Respectful")))
    wv = WARMTH.get(str(warm.get("value")), (55, str(warm.get("value") or "Warm")))
    dials = {
        "voice": {"value": f"{str(voice.get('value') or 'ritu').title()}, {lang_name}", "c": CONF.get(voice.get("strength"), "G"),
                  "why": (voice.get("reason") or "No gender or voice preference in data")[:110], "sources": voice.get("sources") or []},
        "pace": dial(pace, round((pv - 0.8) / 0.45 * 100), pace_text, "Slow", "Brisk"),
        "language": dial(lang if lang.get("strength") == "strong" else mix, MIX_POS.get(mix_v, 55) if lang.get("value") == "hi-IN" else 8,
                         mix_text, lang_name, "English"),
        "formality": dial(form, fv[0], fv[1], "Casual", "Formal"),
        "tone": dial(warm, wv[0], wv[1], "Reserved", "Warm"),
    }
    steps = {}
    for key, name, label in (("opening", "opening", "Opening"), ("personalise", "personalization", "Personalise"),
                             ("objection", "objection_playbook", "Objection"), ("escalation", "escalation", "Escalation"),
                             ("next", "next_action", "Next step")):
        f = _f(persona, name)
        steps[key] = {"label": label, "text": _text(f.get("value")) or "—", "c": CONF.get(f.get("strength"), "G"),
                      "why": (f.get("reason") or "")[:110], "sources": f.get("sources") or []}
    guesses = sum(1 for d in dials.values() if d["c"] == "G") + sum(1 for s in steps.values() if s["c"] == "G")
    return {"headline": persona.get("headline") or f"{pace_text.split(',')[0]}, {wv[1].split(',')[0].lower()}, {lang_name}",
            "dials": dials, "steps": steps, "guesses": guesses, "settings": len(dials) + len(steps),
            "low_data": guesses >= 5, "origin": persona.get("origin")}


DISPO = [(r"meeting fixed|interested|payment|paid|renew", "pos"), (r"not interested|complain|ticket|unanswered|missed|deactivat|angry|refund", "neg")]


def _dispo(text: str) -> str:
    low = (text or "").lower()
    for pattern, kind in DISPO:
        if re.search(pattern, low):
            return kind
    return "neu"


def record(run: dict) -> dict:
    c = run.get("contract") or {}
    log = []
    for h in c.get("history") or []:
        kind = str(h.get("kind") or "")
        label = h.get("outcome") or kind.replace("STS · ", "").title()
        log.append({"at": h.get("at"), "date": short_date(h.get("at")), "type": kind.replace("STS · ", "STS ").replace("Past bot call (dataset)", "Bot call"),
                    "outcome": label, "disp": _dispo(f"{label} {h.get('summary')}"), "text": (h.get("summary") or "")[:220],
                    "origin": h.get("origin"), "api": h.get("api"), "pointer": h.get("pointer")})
    for e in c.get("enquiries") or []:
        log.append({"at": e.get("at"), "date": short_date(e.get("at")), "type": "Buyer enquiry", "outcome": "Enquiry received", "disp": "pos",
                    "text": f"{e.get('product') or ''}{' · ' + e['city'] if e.get('city') else ''}", "origin": e.get("origin"), "api": e.get("api"),
                    "pointer": e.get("pointer")})
    for l in c.get("leads") or []:
        log.append({"at": l.get("at"), "date": short_date(l.get("at")), "type": "Buy lead purchased", "outcome": "Lead bought", "disp": "pos",
                    "text": f"{l.get('title') or l.get('product') or ''}{' · ' + l['quantity'] if l.get('quantity') else ''}", "origin": l.get("origin"),
                    "api": l.get("api"), "pointer": l.get("pointer")})
    log.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    counts: dict[str, int] = {}
    for item in log:
        key = item["type"].split(" ·")[0]
        counts[key] = counts.get(key, 0) + 1
    count_rows = [[str(n), k.lower()] for k, n in sorted(counts.items(), key=lambda kv: -kv[1])][:6]
    persona = run.get("persona") or {}
    objection = _f(persona, "primary_objection")
    rules = []
    if objection.get("value"):
        rules.append({"rule": RULE_TEXT.get(str(objection["value"]), f"Approach for objection: {objection['value']}"),
                      "basis": (objection.get("reason") or "")[:160], "strength": objection.get("strength", "guess"),
                      "sources": objection.get("sources") or [], "similar": None})
    gaps = [r["label"] for r in basics(run)["rows"] if r["missing"]]
    return {"log": log[:40], "counts": count_rows, "last": log[0]["date"] if log else None, "rules": rules, "gaps": gaps, "total": len(log)}


GEN_GROUPS = {"Relationship": "relationship", "Platform use": "platform_use", "Issues": "issues", "Reachability": "reachability", "Data gaps": "data_gaps"}
GROUP_TITLE.update({"relationship": "Relationship", "platform_use": "Platform use", "issues": "Issues"})


def _gen_sources(run: dict) -> dict:
    """record/fact id → {api, origin, label} so every chip of a generated persona opens the API it came from."""
    res = (run.get("results") or {}).get("sarvam.persona_generator") or {}
    data = res.get("input") or {}
    origins = {k: v.get("origin") for k, v in (run.get("results") or {}).items() if isinstance(v, dict)}
    out = {}
    for r in data.get("records") or []:
        api = r.get("source") if r.get("source") != "lead" else "context.global"
        out[r["id"]] = {"api": api, "pointer": f"/records/{r['id']}", "origin": origins.get(api) or "mock", "text": (r.get("text") or "")[:160],
                        "label": f"{r.get('type')} {short_date(r.get('date')) if r.get('date') else ''}".strip()}
    for f in data.get("facts") or []:
        out[f["id"]] = {"api": f.get("source"), "pointer": f"/facts/{f['id']}", "origin": origins.get(f.get("source")) or "mock",
                        "text": f"{f.get('label')}: {f.get('value')}", "label": str(f.get("label"))[:40]}
    return out


def _customer_type(run: dict) -> dict:
    """Paid / Free from Global Context's customer_type, else the company record's custtypeweight (D39: below 1000 is paid)."""
    for api in ("context.global", "context.getcontext"):  # 5.10.1: generateContextUID first, then getContext (same shape)
        gc = ((run.get("results") or {}).get(api) or {})
        kyc = (((gc.get("response") or {}).get("data") or {}).get("kycdetails") or {}) if gc.get("status") == "ok" else {}
        if str(kyc.get("customer_type") or "").strip():
            return _row("Customer type", str(kyc["customer_type"]).title(), gc.get("origin"), api, "/data/kycdetails/customer_type")
    w = _field(run.get("contract") or {}, "custtypeweight")
    try:
        weight = float((w or {}).get("value"))
    except (TypeError, ValueError):
        return _row("Customer type", None)
    return _row("Customer type", "Paid" if weight < 1000 else "Free", w.get("origin"), w.get("api"), w.get("pointer"), f"custtypeweight {int(weight)}")


def from_generated(run: dict, gen: dict, meta: dict) -> dict:
    """The PM layout from a Persona Generator v0.3 output (backend/genome/persona_generator.py)."""
    src = _gen_sources(run)
    b = gen.get("basics") or {}
    # Owner, 2026-10-10: facts come from the APIs, not from the model. Basic details, products and buyer counts are read
    # straight from the API fields (each chip names its API and origin); the generator adds only what needs judgement:
    # the language it inferred, flags, insights, dials and the plan.
    api_basics = basics(run)
    # "On IndiaMART" stays when Global Context gave it (a real tenure); the insights API's figure is a mock.
    rows = [r for r in api_basics["rows"] if r["label"] not in ("GST", "Language")
            and not (r["label"] == "On IndiaMART" and r.get("api") == "context.insights")]
    customer = _customer_type(run)
    if not customer["missing"]:
        rows.insert(2, customer)
    # No API carries a language: read it by rule from the state the API returned (labelled), not from the model.
    state = _field(run.get("contract") or {}, "state")
    if state:
        from backend.genome.roster import language_for_state
        name = LANG_NAME.get(language_for_state(str(state["value"]), "hi-IN"), "Hindi")
        rows.append(_row("Language", name, state.get("origin"), state.get("api"), state.get("pointer"), f"from the state ({state['value']}) — confirm on the call"))
    eng = gen.get("engagement_with_indiamart") or {}
    groups: dict[str, list] = {}
    rec_by_id = {r.get("id"): r for r in gen.get("record") or [] if isinstance(r, dict)}
    for ins in eng.get("insights") or []:
        if not isinstance(ins, dict) or ins.get("gap") or ins.get("group") == "Data gaps":
            continue  # owner, 2026-10-10: what is not in the data is left out, not listed
        key = GEN_GROUPS.get(ins.get("group"), "relationship")
        ev = []
        for e in ins.get("evidence") or []:
            s = src.get((e or {}).get("record_id"))
            if s:
                label = str((e or {}).get("label") or "").strip()
                # The model sometimes labels evidence with the bare id ("r2", "f3"): show the record's or fact's own name.
                ev.append({**s, "label": s["label"] if not label or re.fullmatch(r"[rf]\d+", label) else label})
        # Owner, 2026-10-10: "clicking it shows full insight" — the records each insight cites travel with it.
        cited = []
        for e in ins.get("evidence") or []:
            r = rec_by_id.get((e or {}).get("record_id"))
            if r:
                out = r.get("outcome") if isinstance(r.get("outcome"), dict) else {"label": r.get("outcome")}
                cited.append({"id": r.get("id"), "date": r.get("date"), "type": r.get("type"), "outcome": out.get("label"), "text": r.get("text")})
        groups.setdefault(key, []).append({"id": ins.get("id"), "text": ins.get("text"), "confidence": ins.get("confidence") or "G", "evidence": ev[:4],
                                           "gap": bool(ins.get("gap")), "note": ins.get("note"), "group": ins.get("group"), "records": cited[:6],
                                           "facts": [e.get("label") for e in ins.get("evidence") or [] if isinstance(e, dict) and str(e.get("record_id", "")).startswith("f")][:4]})
    order = ["relationship", "platform_use", "issues", "reachability", "data_gaps"]
    engagement = {"groups": [{"key": k, "title": GROUP_TITLE[k], "items": groups[k]} for k in order if k in groups],
                  "count": sum(len(v) for k, v in groups.items() if k != "data_gaps"), "span": eng.get("range") or "dates not in file",
                  "last_contact": eng.get("last_contact"), "doubts": eng.get("data_doubts") or [], "origin": "llm"}
    by = gen.get("engagement_with_buyers") or {}
    stats = []
    for s in by.get("stats") or []:
        if not isinstance(s, dict):
            continue
        try:
            f = float(s.get("n")) / float(s.get("of")) if s.get("of") else None
        except (TypeError, ValueError, ZeroDivisionError):
            f = None
        stats.append({"n": s.get("n"), "d": s.get("of"), "label": s.get("label"), "f": f, "origin": "llm", "api": "sarvam.aggregate",
                      "pointer": "/engagement_with_buyers/stats"})
    api_buyers = buyers(run)
    stats = api_buyers["stats"] or stats  # counted from the APIs; the model's own counts only if no API had one
    buyers_block = {"stats": stats[:5], "catalogue": api_buyers.get("catalogue") or by.get("catalogue") or [], "insights": [
        {"text": i.get("text"), "confidence": i.get("confidence") or "G", "note": i.get("note") or ""} for i in by.get("insights") or [] if isinstance(i, dict)][:4]}
    persona = gen.get("persona") or {}
    dials, voice = persona.get("dials") or {}, persona.get("voice") or {}

    def dial(name: str, left: str, right: str) -> dict:
        d = dials.get(name) or {}
        return {"pos": max(2, min(98, int(d.get("pos", 50)) if isinstance(d.get("pos"), (int, float)) else 50)), "value": d.get("value") or "—",
                "c": d.get("confidence") or "G", "why": (d.get("why") or "")[:110], "left": d.get("left") or left, "right": d.get("right") or right,
                "sources": [src[x] for x in d.get("from_insights") or [] if x in src][:2], "bounds": d.get("bounds")}
    plan_raw = gen.get("call_plan") if isinstance(gen.get("call_plan"), dict) else None
    dials_out = {"voice": {"value": f"{voice.get('gender') or 'Neutral default'}, {voice.get('language') or 'Hindi'}", "c": voice.get("confidence") or "G",
                           "why": (voice.get("why") or "")[:110], "sources": []},
                 "pace": dial("pace", "Slow", "Brisk"), "language": dial("mix", voice.get("language") or "Hindi", "English"),
                 "formality": dial("formality", "Casual", "Formal"), "tone": dial("warmth", "Reserved", "Warm")}
    if plan_raw:
        trig = plan_raw.get("trigger") or {}
        steps = {"opening": {"label": "Opening", "text": (plan_raw.get("opening") or {}).get("line") or "—", "c": "M",
                             "why": (plan_raw.get("opening") or {}).get("why") or "", "sources": []},
                 "personalise": {"label": "Points to cover", "text": " · ".join(x.get("text", "") for x in plan_raw.get("points_to_cover") or [] if isinstance(x, dict)) or "—",
                                 "c": "M", "why": "From the insights", "sources": []},
                 "objection": {"label": "Avoid", "text": " · ".join(plan_raw.get("avoid") or []) or "—", "c": "M", "why": "What the data says to stay away from", "sources": []},
                 "escalation": {"label": "Questions", "text": " · ".join(plan_raw.get("questions_to_ask") or []) or "—", "c": "M", "why": "", "sources": []},
                 "next": {"label": "Next step", "text": plan_raw.get("next_step") or "—", "c": "M", "why": plan_raw.get("purpose") or "", "sources": []}}
        trigger = {"type": trig.get("type"), "detail": trig.get("detail"), "purpose": plan_raw.get("purpose"), "other": plan_raw.get("other_triggers") or []}
    else:
        note = gen.get("call_plan_note") or "No reason to call in the data."
        steps = {k: {"label": lab, "text": note if k == "opening" else "—", "c": "G", "why": "No call plan" if k == "opening" else "", "sources": []}
                 for k, lab in (("opening", "No call plan"), ("personalise", "Points to cover"), ("objection", "Avoid"), ("escalation", "Questions"), ("next", "Next step"))}
        trigger = None
    guesses = sum(1 for d in dials_out.values() if d["c"] == "G")
    plan_block = {"headline": persona.get("title") or "Persona", "dials": dials_out, "steps": steps, "guesses": guesses, "settings": len(dials_out),
                  "low_data": guesses >= 4, "origin": "llm", "trigger": trigger, "summary": persona.get("summary")}
    log = []
    for r in gen.get("record") or []:
        if not isinstance(r, dict):
            continue
        s = src.get(r.get("id")) or {}
        out = r.get("outcome") if isinstance(r.get("outcome"), dict) else {"label": r.get("outcome"), "tone": "neu"}
        log.append({"at": r.get("date"), "date": r.get("date"), "type": r.get("type"), "outcome": out.get("label"), "disp": out.get("tone") or "neu",
                    "text": r.get("text"), "origin": s.get("origin"), "api": s.get("api"), "pointer": s.get("pointer")})
    record_block = {"log": log[:40], "counts": [[str(c[0]), str(c[1])] for c in gen.get("counts") or [] if isinstance(c, (list, tuple)) and len(c) == 2][:6],
                    "last": log[0]["date"] if log else None, "rules": [], "gaps": gen.get("gaps") or [], "total": len(log),
                    "doubts": eng.get("data_doubts") or []}
    ev = meta.get("evaluation") or {}
    quality = {"score": ev.get("score"), "passed": ev.get("passed"), "total": ev.get("total"), "critical": ev.get("critical"), "warnings": ev.get("warnings"),
               "failed": [{"check": c["check"], "critical": c["critical"], "detail": c["detail"]} for c in ev.get("checks") or [] if not c["ok"]],
               "corrected": ev.get("corrected") or [], "raw_score": ev.get("raw_score"), "attempts": len(meta.get("attempts") or []), "ms": meta.get("ms"),
               "expected": [t.get("type") for t in meta.get("expected_triggers") or []], "tags": b.get("persona_tags") or [], "flags": b.get("flags") or [],
               "segment": gen.get("segment"), "engine": "Persona Generator v0.3"}
    products = api_basics["products"] or [{"name": str(x.get("name") if isinstance(x, dict) else x)[:80], "origin": "llm", "api": "company.detail",
                                           "pointer": "/DATA/PRDNAV"} for x in b.get("products") or [] if x][:8]
    flags = list(dict.fromkeys((b.get("flags") or []) + api_basics.get("flags", [])))
    return {"basics": {"rows": rows, "products": products, "flags": flags}, "engagement": engagement,
            "buyers": buyers_block, "plan": plan_block, "record": record_block, "quality": quality}


def display_seller(run: dict) -> dict:
    """The seller as the brief and persona page show it: a private seller's placeholder ("Private seller · GLID …") is
    replaced by the identity the live company record confirmed in this run (owner: "dont make things anywhere")."""
    seller = dict(run.get("seller") or {})
    fields = (run.get("contract") or {}).get("fields") or {}
    live = lambda k: (fields.get(k) or {}).get("value") if (fields.get(k) or {}).get("availability") == "present" else None  # noqa: E731
    if str(seller.get("company_name") or "").startswith("Private seller") or not seller.get("synthetic", True):
        for key in ("company_name", "contact_name", "city", "state"):
            if live(key):
                seller[key] = str(live(key)).strip()
        seller["identity"] = "confirmed" if live("company_name") else seller.get("identity", "pending")
    return seller


def build(run: dict, similar_count=None) -> dict:
    """The whole brief for one run. ``similar_count(factor, value)`` (optional) counts indexed personas sharing a value."""
    seller = display_seller(run)
    gen_meta = (run.get("persona") or {}).get("generated") or {}
    if isinstance(gen_meta.get("persona"), dict) and gen_meta["persona"].get("basics"):
        origins = {f.get("origin") for f in ((run.get("contract") or {}).get("fields") or {}).values()}
        mode = "live data" if "live_api" in origins else "mock data" if "mock" in origins else "supplied data"
        return {"seller": seller, "call_type": "Seller call", "data_mode": mode, **from_generated(run, gen_meta["persona"], gen_meta),
                "persona_origin": "llm", "engine": "generator",
                "pipeline": ["APIs (guide keys only)", "Persona Generator v0.3", "automatic checks", "call prompt", "Sarvam voice", "live adapter"]}
    rec = record(run)
    for rule in rec["rules"]:
        if similar_count:
            try:
                rule["similar"] = similar_count("primary_objection", str(_f(run.get("persona") or {}, "primary_objection").get("value")))
            except Exception:
                rule["similar"] = None
    origins = {f.get("origin") for f in ((run.get("contract") or {}).get("fields") or {}).values()}
    mode = "live data" if "live_api" in origins else "mock data" if "mock" in origins else "supplied data"
    return {"seller": seller, "call_type": "Seller pitch", "data_mode": mode, "basics": basics(run), "engagement": engagement(run),
            "buyers": buyers(run), "plan": plan(run), "record": rec, "persona_origin": (run.get("persona") or {}).get("origin"),
            "pipeline": ["profile", "persona engine", "call prompt (seller pitch or buyer VANI)", "Sarvam voice", "live adapter", "switch log"]}
