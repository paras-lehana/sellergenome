"""Seller identities for V5: hero sellers, generated sellers for any GLID, and private sellers.

Identifier forms
- ``syn-<glid>``  hand-written synthetic hero (``data/genome/heroes.json``) — public
- ``gen-<glid>``  generated synthetic seller for a GLID we have no data for — public
- ``real-<glid>`` a seller from the supplied dataset or an approved test GLID — PM only

Every profile has the same schema as a hero, so the production-shaped mocks
(``mock_upstream``) can serve any seller. Generation is deterministic: the same GLID always
produces the same seller, so a judge sees a consistent story on every visit.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEROES_PATH = ROOT / "data" / "genome" / "heroes.json"

INSIGHT_CATEGORIES = [
    "purchase_intent", "objection_or_blocker", "last_interaction", "market_opportunity",
    "competitor_landscape", "engagement_history", "catalog_gaps",
]

FIRST_NAMES = ["Arjun", "Pooja", "Ramesh", "Sunita", "Karan", "Nisha", "Vijay", "Rekha", "Ajay", "Swati",
               "Manoj", "Divya", "Suresh", "Anjali", "Prakash", "Meera", "Rahul", "Seema", "Naveen", "Lata",
               "Ashok", "Priya", "Dinesh", "Kiran", "Satish", "Usha", "Gopal", "Rani", "Mahesh", "Asha"]
SURNAMES = ["Sharma", "Verma", "Yadav", "Patil", "Iyer", "Nair", "Reddy", "Khan", "Singh", "Jain",
            "Bansal", "Chauhan", "Kulkarni", "Pillai", "Mishra", "Pandey", "Shah", "Desai", "Rao", "Ghosh"]
COMPANY_FORMS = ["{s} Industries", "{s} Enterprises", "{s} Traders", "{s} & Sons", "Shree {s} Udyog",
                 "{s} Manufacturing Co.", "New {s} Agencies", "{s} Exports", "{s} Engineering Works"]
CITIES = [
    ("Delhi", "Delhi", "110020", "Okhla Phase 2", "hi-IN"), ("Jaipur", "Rajasthan", "302013", "Vishwakarma Industrial Area", "hi-IN"),
    ("Ahmedabad", "Gujarat", "382445", "Vatva GIDC", "gu-IN"), ("Surat", "Gujarat", "395023", "Udhna", "gu-IN"),
    ("Mumbai", "Maharashtra", "400072", "Saki Naka", "mr-IN"), ("Pune", "Maharashtra", "411026", "Bhosari MIDC", "mr-IN"),
    ("Chennai", "Tamil Nadu", "600058", "Ambattur", "ta-IN"), ("Coimbatore", "Tamil Nadu", "641021", "Kurichi", "ta-IN"),
    ("Bengaluru", "Karnataka", "560058", "Peenya", "kn-IN"), ("Hyderabad", "Telangana", "500037", "Balanagar", "te-IN"),
    ("Kolkata", "West Bengal", "700039", "Topsia", "bn-IN"), ("Lucknow", "Uttar Pradesh", "226012", "Talkatora", "hi-IN"),
    ("Ludhiana", "Punjab", "141010", "Focal Point", "pa-IN"), ("Indore", "Madhya Pradesh", "452015", "Sanwer Road", "hi-IN"),
    ("Kochi", "Kerala", "682030", "Kakkanad", "ml-IN"), ("Rajkot", "Gujarat", "360004", "Aji GIDC", "gu-IN"),
]
CATALOG = [
    ("Packaging Machine", [("Shrink Wrapping Machine", "Piece"), ("Carton Sealer", "Piece"), ("Band Sealer", "Piece")], {"Automation": "Semi-automatic"}),
    ("Industrial Pump", [("Centrifugal Pump", "Piece"), ("Submersible Pump", "Piece"), ("Gear Pump", "Piece")], {"Power": "3 HP"}),
    ("Office Chair", [("Ergonomic Office Chair", "Piece"), ("Visitor Chair", "Piece"), ("Executive Chair", "Piece")], {"Material": "Mesh"}),
    ("LED Light", [("LED Panel Light", "Piece"), ("LED Street Light", "Piece"), ("LED Flood Light", "Piece")], {"Wattage": "50 W"}),
    ("Cotton Fabric", [("Cotton Poplin Fabric", "Meter"), ("Cotton Cambric", "Meter"), ("Printed Cotton Fabric", "Meter")], {"GSM": "120"}),
    ("Steel Pipe", [("MS ERW Pipe", "Kg"), ("GI Pipe", "Kg"), ("SS 304 Pipe", "Kg")], {"Grade": "IS 1239"}),
    ("Spices", [("Turmeric Powder", "Kg"), ("Red Chilli Powder", "Kg"), ("Coriander Powder", "Kg")], {"Packaging": "1 Kg pouch"}),
    ("Solar Panel", [("Mono PERC Solar Panel", "Piece"), ("Solar Inverter", "Piece"), ("Solar Street Light", "Piece")], {"Capacity": "540 Wp"}),
]
ARCHETYPES = ["price_sensitive", "curious", "rushed", "confused", "frustrated", "interested", "uninterested", "already_with_executive"]
SPEAKERS_BY_ARCHETYPE = {
    "price_sensitive": "ritu", "curious": "kavya", "rushed": "rahul", "confused": "priya", "frustrated": "shubh",
    "interested": "kavya", "uninterested": "amit", "already_with_executive": "amit", "language_switch": "rahul",
}
TRIGGERS = [
    (2368, "Viewed the Star Supplier package page"), (2369, "Viewed the export (global) package"),
    (2370, "Viewed the TrustSEAL page"), (2371, "Viewed the Leader package"), (2203, "Added new products"),
    (2412, "Explored the Lead Manager"), (2501, "Bought a buy-lead top-up"), (2210, "Missed several PNS calls"),
]


def glid_of(identifier: str) -> str:
    """Return the numeric GLID inside ``syn-``, ``gen-``, ``real-`` or a bare number."""
    value = re.sub(r"^(syn|gen|real)-", "", (identifier or "").strip())
    if not re.fullmatch(r"[0-9]{1,15}", value):
        raise ValueError("A seller identifier must contain a numeric GLID.")
    return value


@lru_cache(maxsize=1)
def heroes() -> dict[str, dict]:
    data = json.loads(HEROES_PATH.read_text())
    out = {}
    for hero in data["sellers"]:
        hero = {**hero, "synthetic": True, "kind": "hero", "seller_id": f"syn-{hero['glid']}"}
        out[hero["glid"]] = hero
    return out


STATE_LANGUAGE = {
    "uttar pradesh": "hi-IN", "delhi": "hi-IN", "haryana": "hi-IN", "rajasthan": "hi-IN", "madhya pradesh": "hi-IN", "bihar": "hi-IN",
    "uttarakhand": "hi-IN", "himachal pradesh": "hi-IN", "jharkhand": "hi-IN", "chhattisgarh": "hi-IN", "chandigarh": "hi-IN",
    "punjab": "pa-IN", "gujarat": "gu-IN", "maharashtra": "mr-IN", "karnataka": "kn-IN", "kerala": "ml-IN", "tamil nadu": "ta-IN",
    "telangana": "te-IN", "andhra pradesh": "te-IN", "west bengal": "bn-IN", "odisha": "od-IN", "goa": "en-IN",
}


def language_for_state(state: str, default: str = "hi-IN") -> str:
    """A first guess only — the persona confirms language from what the seller says, never from geography alone."""
    return STATE_LANGUAGE.get((state or "").strip().lower(), default)


def is_public(seller_id: str) -> bool:
    return seller_id.startswith(("syn-", "gen-"))


def _rng(glid: str, salt: str = "") -> random.Random:
    return random.Random(int(hashlib.sha256(f"{glid}:{salt}".encode()).hexdigest()[:16], 16))


def generate(glid: str, hints: dict | None = None, kind: str = "generated") -> dict:
    """Deterministic production-shaped seller for any GLID.

    ``hints`` (from the supplied dataset) keep the story coherent with what we really know
    about a private seller — e.g. its city and top category — but the generated fields are
    still labelled ``mock`` by the sources that serve them.
    """
    hints = hints or {}
    rng = _rng(glid)
    # Draw every random value in a fixed order; hints only override, so the same GLID always gets the
    # same name and company whether or not dataset hints are available.
    first, last = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
    company = rng.choice(COMPANY_FORMS).format(s=last)
    drawn_city = rng.choice(CITIES)
    city_row = next((c for c in CITIES if hints.get("city") and c[0].lower() == str(hints["city"]).lower()), None)
    if not city_row and hints.get("city"):
        city_row = (str(hints["city"]).title(), drawn_city[1], drawn_city[2], drawn_city[3], drawn_city[4])
    city, state, pin, locality, language = city_row or drawn_city
    if hints.get("state"):
        state = hints["state"]
    cat_name, items, isq = rng.choice(CATALOG)
    if hints.get("category"):
        cat_name = hints["category"]
        items = [(f"{hints['category']}", "Piece"), (f"{hints['category']} (Premium)", "Piece")]
    archetype = rng.choice(ARCHETYPES)
    weight = rng.choice([310, 480, 650, 1100, 1199, 1199, 1250, 1300, 1500])
    paid = weight < 1000
    trigger = rng.choice(TRIGGERS)
    tenure = rng.randint(200, 4200)
    counts = {
        "pns_received_30d": rng.randint(0, 30), "pns_picked_30d": 0,
        "local_buylead_count": rng.randint(2, 40), "national_buylead_count": rng.randint(200, 4000),
        "enquiries_received_30d": rng.randint(0, 30),
    }
    counts["pns_picked_30d"] = rng.randint(0, counts["pns_received_30d"]) if counts["pns_received_30d"] else 0
    products = []
    price_rng = _rng(glid, "prices")
    for name, unit in items:
        priced = price_rng.random() < 0.5
        products.append({"name": name, "unit": unit, "isq": isq,
                         "price": f"₹ {price_rng.randint(2, 900) * 10:,} / {unit}" if priced else None})
    unpriced = sum(1 for p in products if not p["price"])
    today = date(2026, 10, 9)
    last_call_day = today - timedelta(days=rng.randint(2, 20))
    meeting_day = today - timedelta(days=rng.randint(25, 90)) if rng.random() < 0.4 else None
    outcome_by_arch = {
        "price_sensitive": ("Call Later", "Asked about cost; said budget is tight this month."),
        "curious": ("General", "Asked several questions about how leads are distributed."),
        "rushed": ("Busy", "Said busy, call later."),
        "confused": ("General", "Did not understand the difference between buy lead and enquiry."),
        "frustrated": ("Not Interested", "Complained about irrelevant leads."),
        "interested": ("General", "Interested, asked for a meeting next week."),
        "uninterested": ("Not Interested", "Not interested in paid services right now."),
        "already_with_executive": ("Not Interested", "Already in touch with an IndiaMART executive."),
    }
    outcome, summary = outcome_by_arch[archetype]
    insights = {
        "purchase_intent": [f"{first} {trigger[1].lower()} — a signal worth a short qualification call.",
                            f"- Activity {trigger[0]} recorded today.\n- {'Paid' if paid else 'Free'} seller for {tenure // 365} years."],
        "objection_or_blocker": [{
            "price_sensitive": "Cost is the recurring concern.", "curious": "Wants to understand before deciding.",
            "rushed": "Time is short during working hours.", "confused": "Unclear how IndiaMART leads work.",
            "frustrated": "Unhappy with lead relevance.", "interested": "No blocker recorded.",
            "uninterested": "Says business is slow.", "already_with_executive": "Prefers the existing executive.",
        }[archetype], f"- Last call note: \"{summary}\""],
        "last_interaction": [f"Last call on {last_call_day:%d %B}: {outcome.lower()}.", f"- Bot call, {rng.randint(15, 200)} s."],
        "market_opportunity": [f"{city} had buyer demand for {cat_name.lower()} last month.",
                               f"- {counts['local_buylead_count']} local buy leads, {counts['national_buylead_count']} national."],
        "competitor_landscape": [f"{rng.randint(1, 4)} paid sellers in {city} list {cat_name.lower()}.", "- Competitor prices are visible on their catalogs."],
        "engagement_history": [f"Picked {counts['pns_picked_30d']} of {counts['pns_received_30d']} PNS calls in 30 days.",
                               f"- {counts['enquiries_received_30d']} enquiries received in 30 days."],
        "catalog_gaps": [f"{unpriced} of {len(products)} products show no price.", "- No brochure attached."],
    }
    buyer_rng = _rng(glid, "buyers")
    buyers = []
    for i in range(buyer_rng.randint(1, 4)):
        b_city = buyer_rng.choice(CITIES)[0]
        product = buyer_rng.choice(products)["name"]
        buyers.append({
            "glid": str(70000000 + int(hashlib.sha256(f"{glid}{i}".encode()).hexdigest()[:6], 16) % 999999),
            "name": f"{buyer_rng.choice(FIRST_NAMES)} {buyer_rng.choice(SURNAMES)}",
            "city": b_city, "channel": buyer_rng.choice(["enquiry", "pns", "buylead"]),
            "product": product, "qty": f"{buyer_rng.choice([5, 10, 25, 50, 100, 200])} {products[0]['unit']}",
            "last_at": (today - timedelta(days=buyer_rng.randint(1, 14))).isoformat(),
            "intent": buyer_rng.choice(["High", "Medium", "Low"]),
        })
    return {
        "glid": glid, "seller_id": f"{'real' if kind == 'private' else 'gen'}-{glid}", "synthetic": kind != "private",
        "kind": kind, "contact_name": f"{first} {last}", "company_name": company,
        "business_nature": hints.get("business_type") or rng.choice(["Manufacturer", "Trader", "Wholesaler", "Manufacturer and Exporter"]),
        "legal_status": rng.choice(["Individual - Proprietor", "Partnership", "Private Limited Company"]),
        "employees": rng.choice(["Upto 10 People", "11 to 25 People", "26 to 50 People"]),
        "year_established": str(rng.randint(1995, 2022)),
        "city": city, "state": state, "pincode": pin, "locality": locality,
        "address_profile": f"{rng.randint(1, 300)}, {locality}, {city}, {state} {pin}",
        "address_gst": f"{rng.randint(1, 300)} {locality.upper()} {city.upper()} {state.upper()} {pin}",
        "language": language, "speaks": "Hinglish" if language == "hi-IN" else "mixed",
        "archetype": archetype, "speaker": SPEAKERS_BY_ARCHETYPE[archetype],
        "custtype": 25 if paid else 60, "custtypeweight": weight, "custtype_name": "Star Supplier" if paid else "FCP",
        "data_type": "PIM", "tenure_days": tenure, "turnover_band": hints.get("turnover") or rng.choice(["Upto ₹40 lakh", "₹40 lakh – 1.5 crore", "₹1 – 5 crore"]),
        "products": products,
        "top_mcats": [{"mcat_name": cat_name, "product_count": rng.randint(3, 20)}],
        "counts": counts,
        "competitors": [f"{rng.choice(SURNAMES)} {rng.choice(['Traders', 'Industries', 'Mart'])}: {rng.choice(['Jan', 'May', 'Aug', 'Nov'])} {rng.randint(2015, 2026)}" for _ in range(rng.randint(1, 3))],
        "last_call": {"summary": summary, "start": f"{last_call_day.isoformat()} {rng.randint(10, 18)}:{rng.randint(10, 59)}:00", "by": "bot"},
        "last_meeting_date": meeting_day.strftime("%d-%m-%Y") if meeting_day else "",
        "trigger": {"activity_id": trigger[0], "activity": trigger[1]},
        "insights": insights,
        "doubts": [],
        "buyers": buyers,
        "sts": [{"date": last_call_day.isoformat(), "by": "Bot", "note": summary}],
        "lms": [{"date": f"{buyers[0]['last_at']} 11:00", "from": "buyer", "text": f"Need {buyers[0]['qty']} {buyers[0]['product']}. Please share rate."}] if buyers else [],
        "bot_calls": [{"date": last_call_day.isoformat(), "outcome": outcome, "duration_s": rng.randint(15, 200), "summary": summary}],
    }


def profile(seller_id: str, hints: dict | None = None) -> dict:
    """Full seller profile for any identifier (hero, generated, or private with dataset hints)."""
    glid = glid_of(seller_id)
    if seller_id.startswith("real-"):
        # A private seller's identity is never invented: it reads "pending" until the live company API
        # (or the supplied dataset) confirms it during a run. The generated story only seeds tile animation.
        hints = {k: v for k, v in (hints or {}).items() if v and str(v).strip().lower() not in ("unknown", "na", "n/a", "none", "null", "-")}
        p = generate(glid, hints, kind="private")
        return {**p, "company_name": f"Private seller · GLID {glid}", "contact_name": "", "trigger": {},
                "city": hints.get("city") or "", "state": hints.get("state") or "", "archetype": "unknown", "identity": "pending",
                "language": language_for_state(hints.get("state", ""), "hi-IN")}
    hero = heroes().get(glid)
    if hero:
        return hero
    return generate(glid, hints, kind="generated")


def search_public(query: str, limit: int = 8) -> list[dict]:
    """Search hero sellers by name, company, city, product or GLID (public)."""
    q = (query or "").strip().lower()
    out = []
    for hero in heroes().values():
        haystack = " ".join([hero["glid"], hero["contact_name"], hero["company_name"], hero["city"], hero["state"],
                             " ".join(p["name"] for p in hero["products"])]).lower()
        if not q or q in haystack:
            out.append(summary(hero))
    return out[:limit]


def summary(p: dict) -> dict:
    return {
        "seller_id": p["seller_id"], "glid": p["glid"], "contact_name": p["contact_name"],
        "company_name": p["company_name"], "city": p["city"], "state": p["state"],
        "language": p["language"], "archetype": p["archetype"], "synthetic": p.get("synthetic", True),
        "kind": p.get("kind"), "category": (p.get("top_mcats") or [{}])[0].get("mcat_name"),
        "trigger": p.get("trigger", {}).get("activity"), "identity": p.get("identity", "synthetic" if p.get("synthetic", True) else "pending"),
    }
