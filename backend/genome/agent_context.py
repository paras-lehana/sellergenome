"""What every calling agent knows (5.12, owner, 2026-10-10, GLID 12345678).

Two parts, shared by every agent prompt (built-in Payal, Payal on Sarvam, Manjula Executive v2, VANI) and every set of
call variables:

- ``INDIAMART_KNOWLEDGE`` and ``SALES_PLAYBOOK``: how IndiaMART works for a seller, and how a good IndiaMART executive
  runs the call. Distilled from the team's reference agent and its evaluation rubric (docs/refenrenceCode/
  livekit_agent_sarvam: opening and introduction, business understanding, buy lead / enquiry and MDC pitch, profile
  enrichment, Lead Manager, PNS, competitor references, plan and pricing, closure and follow-up) and from the playbook
  knowledge base. Written in English, third person, with no quoted lines (Sarvam's prompt playbook).
- ``call_facts(run)``: talking points from the seller's own live data. These are the competitors (Global Context
  ``top3mcat_competitors``), the market in the seller's categories, payment status (plan pitched, payment attempted,
  auto-debit mandate, last paid invoice), buyers who reached out, products, and the reason for the call. Empty when an
  API does not have it, never invented.
"""
from __future__ import annotations

import json
import re
from typing import Any

INDIAMART_KNOWLEDGE = """IndiaMART is India's largest online B2B marketplace. Crores of registered buyers, from small shops to large companies, search it every day for suppliers, and lakhs of sellers list their products on it. Buyers find a seller in three ways, and the agent explains them in plain words when useful.
A buy lead is a requirement a buyer posts, for example a product and a quantity. Sellers in that category can see it and contact the buyer. Paid plans give a monthly quota of buy leads, and a seller who responds within the first few hours wins most of them.
An enquiry is a message a buyer sends directly to one seller from that seller's catalogue. Replying fast, with a price range and photos, is the cheapest way to win business, because the buyer already chose this seller.
A PNS call is a buyer calling the masked phone number IndiaMART shows on the seller's page. Missed PNS calls appear in the seller app so the seller can call back.
The Lead Manager in the IndiaMART seller app keeps every enquiry, buy lead and buyer call in one list with follow-up reminders.
The catalogue drives everything: leads follow the products a seller lists. Complete products, with photos, a price and the right category, rank higher in search and bring more relevant buyers. Removing categories the seller does not deal in stops irrelevant leads.
TrustSEAL is IndiaMART's verification of a seller's business documents. Verified sellers show a badge that buyers trust more.
Paid plans: MDC, the Mini Dynamic Catalog, is the entry plan that gives a seller a paid catalogue with better visibility and buy-lead access, usually taken as an annual plan. Star Supplier and Leading Supplier give priority listing, a larger buy-lead quota and stronger placement in the seller's categories. An export plan reaches international buyers, and a buy-lead top-up lets a seller test lead quality without a long commitment. Prices are never quoted on a call: an executive shares current pricing in the meeting, because it depends on city, category and plan. A payment is never taken on the call: the executive shares a secure payment link or completes it in the meeting.
In every category, the paid sellers in the same city appear first to buyers. A free seller in a busy category is mostly invisible next to them, which is why the number of paid sellers and buy leads in the seller's own categories is the strongest reason to upgrade."""

SALES_PLAYBOOK = """How a strong IndiaMART executive runs this call. The agent behaves like an executive who already knows the account, never like someone reading a file.
Payment first when there is payment activity. When the payment status shows a plan was pitched, a payment was attempted or an auto-debit mandate stopped, the agent says it noticed the seller was taking a plan, asks whether something got stuck, and offers to help finish it. Typical blockers are a failed or rejected auto-debit, a bank limit, or a doubt about the plan. The agent explains the value of that plan for this seller's products and city, and offers that the executive completes it with a secure link or in person. The agent never asks for card, bank, OTP or UPI details.
Then the meeting. If the seller does not want to pay now, the agent moves to a short meeting with an executive, online or at the seller's place. When the seller is unsure, the agent does not give up after one no. It tries up to three different angles, one per turn, before accepting a callback: the market (paid sellers and buy leads in the seller's own categories and city), the competitors who are ahead (by name), buyers who reached out or the demand being missed, a quick win on the catalogue, and the fact that the meeting is free and commits to nothing. Only an explicit stop request, abuse, or a third clear refusal ends the attempt; then the agent asks for a callback time and closes warmly.
Competitors. When the competitors variable names sellers, the agent mentions one or two of them, by name and plan, as sellers in the same category and city who are ahead on IndiaMART, factually and without running them down. It uses this to create urgency, not fear. When no competitor is known, the agent speaks of paid sellers in the category in general.
Buyers. The agent mentions the buyers who reached out to the seller, or the absence of them, with the window, for example enquiries or buyer calls in the last thirty days, or the last buy lead the seller looked at. Buyer names, phone numbers and their cities are never spoken.
Products and expertise. The agent names what the seller deals in at the level of the overall product line, not exact item names, and goes into specifications only when the seller asks or when it helps to show expertise. The agent persuades like an expert in the seller's trade: who buys these products, seasonal or bulk demand, how buyers search for them, what wins enquiries (photos, prices, specifications), and one practical business idea for growth. It never invents a fact about the seller.
Weighting. The reason for the call, the payment status and anything discussed in the last meeting or call come first. Market, competitors and buyers support the ask. Everything else is background."""


def _obj(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _data(run: dict, api: str) -> dict:
    r = _obj(_obj(run.get("results")).get(api))
    resp = r.get("response") if r.get("status") == "ok" else None
    return _obj(_obj(resp).get("data")) if isinstance(resp, dict) else {}


def _blank(v: Any) -> bool:
    return v in (None, "", "-", "NA", "None", "null", [], {}) or str(v).strip().lower() in ("", "-", "na", "none", "null")


PLAN = {"LEADER": "Leading Supplier plan", "STAR": "Star Supplier plan", "CATALOG": "paid catalogue (MDC) plan", "TSCATALOG": "TrustSEAL catalogue plan"}
HOT_LEAD = {"SCHD": "asked for a demo", "PIM": "showed interest in a paid plan", "PAM": "attempted a payment", "OLP": "made an online payment",
            "OLPR": "renewed online", "PUA": "premium activity", "UA": "recent activity on IndiaMART"}


def call_facts(run: dict) -> dict:
    """Talking points from a real seller's run. Every value is text, '' when the data has none."""
    gc = {**_data(run, "context.global"), **_data(run, "context.getcontext")}   # generateContextUID may carry KYC too
    act, kyc = _obj(gc.get("activitydetails")), _obj(gc.get("kycdetails"))
    persona = _obj(run.get("persona"))
    gen = _obj(_obj(persona.get("generated")).get("persona"))
    fields = _obj(_obj(run.get("contract")).get("fields"))
    val = lambda k: _obj(fields.get(k)).get("value") if _obj(fields.get(k)).get("availability") == "present" else None  # noqa: E731
    records = [r for r in gen.get("record") or [] if isinstance(r, dict)]

    # Competitors: the products-listing API (company-API mcat, city-wise) when it answered, else Global Context's list.
    from backend.genome import parsers as _p
    listing = _obj(_obj(run.get("results")).get("market.listing"))
    own_glid = str(_obj(run.get("seller")).get("glid") or "")
    rows = _p.listing_sellers(listing.get("response"), own_glid) if listing.get("status") == "ok" else []
    shelf = _p.listing_market(listing.get("response"), own_glid) if rows else {}
    comps = [f"{c['company']} ({'; '.join(x for x in (c.get('city'), c.get('plan') if c.get('plan') != 'paid' else '', 'TrustSEAL' if c.get('trustseal') else '', c.get('rating'), c.get('years')) if x)})"
             .replace(" ()", "") for c in rows][:10]
    for c in ([] if comps else act.get("top3mcat_competitors") or []):
        if isinstance(c, dict) and not _blank(c.get("competitor_company")):
            bits = [str(c.get("competitor_city") or "").strip(), PLAN.get(str(c.get("custtype_name") or "").upper(), str(c.get("custtype_name") or "").title()),
                    f"on IndiaMART since {c['competitor_membersince']}" if not _blank(c.get("competitor_membersince")) else ""]
            comps.append(f"{c['competitor_company']} ({', '.join(b for b in bits if b)})")
    # Market in the seller's top categories.
    top = (act.get("top3mcat_data") or [{}])[0] if isinstance(act.get("top3mcat_data"), list) and act.get("top3mcat_data") else {}
    top = _obj(top)
    mcats = top.get("mcat_list")
    if isinstance(mcats, str):
        try:
            mcats = json.loads(mcats)
        except ValueError:
            mcats = []
    cats = [m.get("mcat_name") for m in mcats or [] if isinstance(m, dict) and m.get("mcat_name")][:3]
    city = str(val("city") or _obj(run.get("seller")).get("city") or kyc.get("city") or "the seller's city")
    market = []
    if top.get("local_paid_sellers") not in (None, ""):
        market.append(f"{top['local_paid_sellers']} paid sellers in {city} in these categories"
                      + (f", {top['hyperlocal_paid_sellers']} of them close by" if top.get("hyperlocal_paid_sellers") not in (None, "") else ""))
    if top.get("local_bl") not in (None, ""):
        market.append(f"{top['local_bl']} buy leads in {city} in these categories" + (f" ({top['hyperlocal_bl']} close by)" if top.get("hyperlocal_bl") not in (None, "") else ""))
    market_text = (f"Top categories: {', '.join(cats)}. " if cats else "") + ("; ".join(market) + "." if market else "")
    if shelf.get("shown"):   # 5.13.5: where the seller stands in IndiaMART search for the main category, weighted first
        where = f"in {shelf['city']}" if shelf.get("city") else "across India"
        n = shelf["shown"]
        extras = [t for t, k in (("TrustSEAL", "trustseal"), ("a price shown", "with_price")) if shelf.get(k) == n]
        first = f"The first {n} IndiaMART search results for {shelf.get('category') or 'the main category'} {where}"
        if shelf.get("paid") == n or extras:
            first += f" are all {'paid sellers' if shelf.get('paid') == n else 'sellers'}" + (f" with {' and '.join(extras)}" if extras else "")
        first += (f"; this seller is at position {shelf['own_rank']}" if shelf.get("own_rank") else "; this seller is not among them")
        total = shelf.get("sellers_india")   # the search's own total: city-scoped when the city filter applied
        india = (f"; the {shelf['city']} search lists {total:,} sellers" if shelf.get("city") else f"; {total:,} sellers list it across India") if total else ""
        market_text = f"{first}{india}. {market_text}".strip()

    # Payment: plan pitched, payment attempted, auto-debit mandate, last paid invoice, hot-lead type.
    pay = []
    from backend.genome import parsers
    sts = _obj(_obj(run.get("results")).get("sts.history"))
    for e in parsers.sts_entries(sts.get("response")) if sts.get("status") == "ok" else []:
        m = re.search(r"Product Pitched\s*-\s*([A-Za-z][^|]{1,40}?)(?=\s+\d+[.\s]|\s*\||$)", str(e.get("text") or ""), re.I)
        if m:   # "5. Product Pitched - MDC annual" in an executive's meeting note
            pay.append(f"plan pitched by the executive on {str(e.get('at'))[:10]}: {m.group(1).strip()}")
    for r in records:
        text = str(r.get("text") or "")
        if re.search(r"payment", text, re.I) and r.get("type") in ("System notice", "Call note"):
            pay.append(f"{parsers.staff_safe(text)[:120].rstrip('.')} ({r.get('date')})")
    for n in kyc.get("nach_status") or []:
        if isinstance(n, dict) and not _blank(n.get("S_NAME")):
            pay.append(f"auto-debit (NACH) mandate for {str(n['S_NAME']).strip(' .')}: {n.get('DIS_TEXT') or 'status unknown'}")
    if not _blank(kyc.get("customer_type")):
        pay.append(f"currently a {kyc['customer_type']} seller")
    if not _blank(kyc.get("payment_invoice_date")):
        pay.append(f"last paid invoice {kyc['payment_invoice_date']}")
    if not _blank(kyc.get("renewal_date")):
        pay.append(f"renewal date {kyc['renewal_date']}" + (f" ({kyc['renewal_status']})" if not _blank(kyc.get("renewal_status")) else ""))
    dt = str(val("data_type") or "").upper()
    if dt in HOT_LEAD:
        pay.append(f"hot lead: the seller {HOT_LEAD[dt]}")

    # Buyers who reached out (the persona's third column) plus the last lead the seller looked at.
    buyers = []
    by = _obj(gen.get("engagement_with_buyers"))
    for s in by.get("stats") or []:
        if isinstance(s, dict) and s.get("label"):
            buyers.append(f"{s.get('n')} {s['label']}")
    for i in by.get("insights") or []:
        if isinstance(i, dict) and i.get("text"):
            buyers.append(str(i["text"]).rstrip("."))
    lead = _obj(act.get("LAST_LEAD"))
    if not _blank(lead.get("TITLE")):
        buyers.append(f"last buy lead the seller looked at: {lead['TITLE']}" + (f" on {str(lead.get('DATE_R'))[:10]}" if lead.get("DATE_R") else ""))
    enq = _obj(act.get("LAST_ENQUIRY"))
    if not _blank(enq.get("PRODUCT_NAME")):   # "Enquiry for Round Tin Box from <buyer> via IndiaMART" → the product only
        product = re.sub(r"\s+from\s+.*$", "", re.sub(r"^enquiry\s+for\s+", "", str(enq["PRODUCT_NAME"]), flags=re.I), flags=re.I).strip()
        if product:
            buyers.append(f"last enquiry was for {product}")

    # Products at the level of the product line.
    products = [str(p) for p in _obj(gen.get("basics")).get("products") or [] if p][:6]
    nature = kyc.get("gst_nature_of_business") or val("business_nature")
    product_text = ", ".join(dict.fromkeys(cats + products)) or str(val("products") or "")
    if nature and not _blank(nature):
        product_text = f"{product_text} (business: {nature})" if product_text else f"business: {nature}"
    if not _blank(kyc.get("approved_product_count")):
        product_text += f"; {kyc['approved_product_count']} approved products listed"

    plan = _obj(gen.get("call_plan"))
    reason = " ".join(x for x in [_obj(plan.get("trigger")).get("detail"), plan.get("purpose"), f"Next step: {plan['next_step']}" if plan.get("next_step") else ""] if x) \
        or str(gen.get("call_plan_note") or "")
    more = (f"; and {len(rows) - 3} more in the first {shelf.get('shown') or len(rows)} search results" if rows
            else f"; and {len(comps) - 3} more paid sellers in these categories")
    comps_text = "; ".join(comps[:3]) + (more if len(rows or comps) > 3 else "")
    return {"competitors": comps_text, "market_snapshot": market_text.strip(), "payment_status": "; ".join(dict.fromkeys(pay)),
            "buyer_activity": "; ".join(dict.fromkeys(buyers))[:700], "product_overview": product_text[:400], "call_reason": reason[:500]}


def profile_facts(p: dict) -> dict:
    """The same talking points for a synthetic demo seller (from its production-shaped profile)."""
    c = _obj(p.get("counts"))
    ins = _obj(p.get("insights"))
    buyers = [f"{b.get('channel')}: {b.get('qty')} {b.get('product')}" for b in p.get("buyers") or [] if isinstance(b, dict)][:3]
    if c.get("enquiries_received_30d"):
        buyers.insert(0, f"{c['enquiries_received_30d']} enquiries received in the last 30 days")
    # insight text may hold "- " bullet lines; speak them as one clause list (5.13.3: "August.; - On 12 August")
    pay = [ln.strip().lstrip("-• ").rstrip(".; ") for x in (ins.get("purchase_intent") or [])[:2] if isinstance(x, str)
           for ln in x.splitlines() if ln.strip().lstrip("-• ")]
    return {"competitors": "", "market_snapshot": (f"{c['local_buylead_count']} buy leads in {p.get('city')} in the seller's categories."
                                                   if c.get("local_buylead_count") else ""),
            "payment_status": "; ".join(pay), "buyer_activity": "; ".join(buyers),
            "product_overview": ", ".join(x["name"] for x in p.get("products", [])[:4] if isinstance(x, dict)),
            "call_reason": str((p.get("trigger") or {}).get("activity") or "")}
