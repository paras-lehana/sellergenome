"""Teleprompter: what to say next, why, and what never to say (owner point 8).

Built from the persona (delivery), the contract (facts, doubts, insights) and the call stage.
Lines are written the way IndiaMART's bot actually speaks — romanised Hinglish for Hindi sellers,
plain English for English sellers; other languages are translated by Sarvam on demand.
The opening follows decision D10: a *named virtual assistant*, never a claim to be human.
"""

from __future__ import annotations

STAGES = ["greeting", "qualifying", "package", "meeting", "wrap_up"]
STAGE_LABELS = {"greeting": "Greeting", "qualifying": "Qualifying", "package": "Package", "meeting": "Meeting", "wrap_up": "Wrap-up"}

ACTIVITY_HI = {
    2368: "aapne Star Supplier package dekha", 2369: "aapne export package dekha", 2370: "aapne TrustSEAL ki details dekhi",
    2371: "aapne Leader package dekha", 2203: "aapne naye products add kiye", 2412: "aap Lead Manager explore kar rahe the",
    2501: "aapne buy leads ka top-up liya", 2210: "aapki kuch buyer calls miss hui", 2650: "aapne renewal page dekha",
    2655: "aapne renewal page dekha", 2101: "aapne naya product add kiya", 2115: "aapne products update kiye",
    2380: "aapki enquiries kaafi badh gayi hain",
}


def _first_name(name: str) -> str:
    return (name or "").split()[0] if name else ""


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _field(assembled: dict | None, seller: dict, name: str):
    """A contract value the opening may speak: present, and never a mock for a real seller."""
    f = ((assembled or {}).get("fields") or {}).get(name) or {}
    if f.get("availability") != "present" or (f.get("origin") == "mock" and not seller.get("synthetic", True)):
        return None
    return f.get("value")


def _count(value) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _day(at) -> str:
    """'2026-08-02 11:00:00' or '02-08-2026' → '2 August'."""
    text = str(at or "").strip()
    try:
        if len(text) >= 10 and text[:4].isdigit() and text[4] == "-":
            return f"{int(text[8:10])} {MONTHS[int(text[5:7]) - 1]}"
        if len(text) >= 10 and text[2] in "-/" and text[6:10].isdigit():
            return f"{int(text[:2])} {MONTHS[int(text[3:5]) - 1]}"
    except (ValueError, IndexError):
        pass
    return ""


def product_label(seller: dict, persona: dict, assembled: dict | None) -> str:
    """What the seller deals in, from real data only: the generator's checked products, else the storefront's first
    product, else the supplied dataset's top category (a synthetic seller's own catalogue counts as its data)."""
    cats = [str(c).strip() for c in (assembled or {}).get("categories") or [] if str(c).strip()]
    if cats:  # "Aap Candle aur Jar Candle ka kaam karte hain" reads better than one long product name
        return " aur ".join(" ".join(c.split()[:3]) for c in cats[:2])
    gen = ((((persona.get("generated") or {}).get("persona") or {}).get("basics") or {}).get("products")) or []
    names = [str(x).strip() for x in gen if str(x).strip()]
    live = _field(assembled, seller, "products")
    names += [x.strip() for x in str(live or "").split(",") if x.strip()]
    signals = (assembled or {}).get("dataset_signals") or {}
    names += [str((signals.get(k) or {}).get("value") or "").strip() for k in ("top_category_1", "top_parent_category")]
    if seller.get("synthetic", True):
        names += [p.get("name", "") for p in seller.get("products") or []]
    name = next((n for n in names if n), "")
    return " ".join(name.split()[:4])


def value_line(seller: dict, persona: dict, assembled: dict | None, hindi: bool) -> str:
    """Owner, 2026-10-10: the seller has little time — say what they deal in, then one real reason (buyer demand or the
    last contact). Never a number or date that the data does not hold."""
    product = product_label(seller, persona, assembled)
    enq, city = _count(_field(assembled, seller, "enquiries_received_30d")), str(_field(assembled, seller, "city") or seller.get("city") or "").strip()
    local = _count(_field(assembled, seller, "local_buylead_count"))
    meeting = _day(_field(assembled, seller, "last_meeting_date"))
    calls = [h for h in (assembled or {}).get("history") or [] if "bot call" in str(h.get("kind", "")).lower() and _day(h.get("at"))
             and str(h.get("summary") or "").strip() and (seller.get("synthetic", True) or h.get("origin") != "mock")]
    last = max(calls, key=lambda h: str(h.get("at")), default=None)
    if hindi:
        lead_in = f"Aap {product} ka kaam karte hain" if product else ""
        if enq:
            why = f"pichhle 30 din mein aapko {enq} buyer {'enquiry aayi hai' if enq == 1 else 'enquiries aayi hain'}"
        elif local and city:
            why = f"{city} ke buyers ki {local} {'requirement' if local == 1 else 'requirements'} IndiaMART par hain"
        elif meeting:
            why = f"{meeting} ko IndiaMART ke saath aapki meeting hui thi"
        elif last:
            why = f"{_day(last.get('at'))} ko IndiaMART se aapki baat hui thi"
        else:
            why = ""
        line = " — ".join(x for x in (lead_in, why) if x)
    else:
        lead_in = f"You deal in {product}" if product else ""
        if enq:
            why = f"{enq} buyer {'enquiry' if enq == 1 else 'enquiries'} reached you in the last 30 days"
        elif local and city:
            why = f"IndiaMART has {local} buyer {'requirement' if local == 1 else 'requirements'} from {city}"
        elif meeting:
            why = f"we met on {meeting}"
        elif last:
            why = f"we last spoke on {_day(last.get('at'))}"
        else:
            why = ""
        line = " — ".join(x for x in (lead_in, why) if x)
    return f"{line[:1].upper()}{line[1:]}." if line else ""


def deals_in(seller: dict, persona: dict, assembled: dict | None, hook: str, hindi: bool) -> str:
    """'Aap Tablets ka kaam karte hain. ' before a plan's reason — unless the reason already names the product."""
    product = product_label(seller, persona, assembled)
    if not product or product.split()[0].lower() in hook.lower():
        return ""
    return f"Aap {product} ka kaam karte hain. " if hindi else f"You deal in {product}. "


MEETING_ASK_HI = "Kya ek chhoti meeting rakh lein — IndiaMART aapke business mein kaise madad kar sakta hai?"
MEETING_ASK_EN = "Could we set up a short meeting on how IndiaMART can help your business?"


def opening(seller: dict, persona: dict, settings, assembled: dict | None = None) -> str:
    bot = settings.get("app.bot_name", "Payal")
    mode = settings.get("app.disclosure_mode", "named_assistant")
    lang = persona["factors"].get("language", {}).get("value", seller.get("language", "hi-IN"))
    name = _first_name(seller.get("contact_name", ""))
    hook = persona.get("teleprompter", {}).get("opening_hook", "")
    if name and hook.lower().startswith(name.lower()):
        hook = hook[len(name):].lstrip(" ,ji").lstrip(", ")
        hook = hook[:1].upper() + hook[1:]
    for greet in ("Namaste", "Namaskar", "Hello"):
        if hook.startswith(greet):
            hook = hook.split(",", 1)[-1].strip() if "," in hook[:40] else hook
    activity_id = (seller.get("trigger") or {}).get("activity_id")
    formal = persona["factors"].get("formality", {}).get("value") == "formal"
    if lang not in ("hi-IN", "en-IN"):
        # Regional language: a short English intro (understood everywhere) + Sarvam's hook in the seller's language.
        who = f" {name}" if name else ""
        intro = (f"Hello{who}, this is {bot}, IndiaMART's virtual assistant." if mode == "named_assistant"
                 else f"Hello{who}, I'm an AI assistant calling from IndiaMART.")
        if hook:
            ask = "" if hook.rstrip().endswith(("?", "？")) else "Is this a good time for two minutes?"
            return f"{intro} {deals_in(seller, persona, assembled, hook, hindi=False)}{hook} {ask}".strip()
        why = value_line(seller, persona, assembled, hindi=False) or (
            f"I noticed you {(seller.get('trigger') or {}).get('activity', '').lower()}." if activity_id else "")
        return f"{intro} {why} {MEETING_ASK_EN}".replace("  ", " ").strip()
    # Short intro (D10: a named virtual assistant), then the most important words: the seller's products and one real
    # reason, then a direct meeting ask (owner, 2026-10-10: "speak the most important lines in the first 15–20 words").
    who = f" {name}" if name else ""
    if lang == "en-IN":
        intro = (f"Hello{who}, this is {bot}, IndiaMART's virtual assistant." if mode == "named_assistant"
                 else f"Hello{who}, I'm an AI assistant calling from IndiaMART.")
        if hook:
            ask = "" if hook.rstrip().endswith("?") else "Do you have two minutes?"
            return f"{intro} {deals_in(seller, persona, assembled, hook, hindi=False)}{hook} {ask}".strip()
        why = value_line(seller, persona, assembled, hindi=False)
        if not why and activity_id:
            why = f"I noticed you {(seller.get('trigger') or {}).get('activity', '').lower()}."
        return f"{intro} {why} {MEETING_ASK_EN}".replace("  ", " ").strip()
    intro = (f"Namaste{who} ji, main {bot}, IndiaMART ki virtual assistant." if mode == "named_assistant"
             else f"Namaste{who} ji, main IndiaMART ki AI assistant.")
    if hook:
        ask = "" if hook.rstrip().endswith("?") else ("Kya aapse do minute baat ho sakti hai?" if not formal else "Kya main aapka do minute ka samay le sakti hoon?")
        # The plan's hook carries the reason (and usually the question); the products still come first.
        return f"{intro} {deals_in(seller, persona, assembled, hook, hindi=True)}{hook} {ask}".strip()
    why = value_line(seller, persona, assembled, hindi=True)
    if not why and activity_id in ACTIVITY_HI:
        why = f"Maine dekha ki {ACTIVITY_HI[activity_id]}."
    return f"{intro} {why} {MEETING_ASK_HI}".replace("  ", " ").strip()


def compose(seller: dict, assembled: dict, persona: dict, settings, stage: str = "greeting") -> dict:
    f = persona["factors"]
    hindi = f.get("language", {}).get("value", seller.get("language")) != "en-IN"
    objection = f.get("primary_objection", {}).get("value", "none")
    confirm = (persona.get("teleprompter", {}).get("confirm_first") or [d["doubt"] for d in assembled.get("doubts", [])])[:2]
    first_q = persona.get("teleprompter", {}).get("first_question") or (
        "Aapko zyada buyers chahiye, ya sahi buyers?" if hindi else "Do you need more buyers, or better-matched buyers?")
    # Short lines (owner, 2026-10-10: "take it short and directly come to point") — one idea, then the meeting ask.
    responses = {
        "price": ("Price hamare executive batayenge. Kya ek chhoti meeting rakh lein?",
                  "Our executive will explain pricing. Shall we set up a short meeting?"),
        "time": ("Bas ek minute lungi. Kab baat karna theek rahega?",
                 "Just a minute of your time. When is a better time?"),
        "lead_quality": ("Aapki baat sahi hai, main senior tak pahunchaungi. Kaisi leads aa rahi thi?",
                         "That's fair, and I'll escalate it. What leads were you getting?"),
        "confusion": ("Buy lead IndiaMART se milti hai, enquiry seedha aapko aati hai.",
                      "A buy lead comes through IndiaMART; an enquiry comes straight to you."),
        "existing_executive": ("Theek hai, main aapke account manager ko bata deti hoon.",
                               "Understood, I'll pass this to your account manager."),
        "not_interested": ("Theek hai. Kya agle mahine call kar sakti hoon?",
                           "Understood. May I call again next month?"),
        "trust": ("Sahi sawal. Executive aapko documents aur reference sellers dikha denge.",
                  "Fair question. Our executive can show documents and reference sellers."),
        "none": (MEETING_ASK_HI, MEETING_ASK_EN),
    }
    objection_line = responses.get(objection, responses["none"])[0 if hindi else 1]
    packages = match_packages(assembled, persona)
    return {
        "stage": stage, "stage_label": STAGE_LABELS.get(stage, stage), "stages": [{"id": s, "label": STAGE_LABELS[s]} for s in STAGES],
        "opening": opening(seller, persona, settings, assembled),
        "next_question": first_q,
        "objection": {"type": objection, "line": objection_line},
        "confirm_first": confirm,
        "handoff": "Hand off to a human executive if the seller complains, asks for a refund, wants pricing details, or asks for a person.",
        "avoid": (persona.get("teleprompter", {}).get("avoid") or []) + ["Quoting any price or discount", "Claiming to be a human"],
        "delivery": {k: f[k]["value"] for k in ("language", "pace", "voice", "formality", "warmth", "empathy", "sentence_length", "hinglish_mix") if k in f},
        "packages": packages,
        "why": [f"{k}: {v.get('reason', '')}" for k, v in f.items() if k in ("opening", "primary_objection", "pace", "language") and v.get("reason")],
    }


def match_packages(assembled: dict, persona: dict) -> list[dict]:
    """Rank catalogue packages by the seller's evidence. Never returns a price."""
    text = " ".join([i.get("headline", "") + " " + i.get("detail", "") for i in assembled.get("insights", [])]).lower()
    objection = persona["factors"].get("primary_objection", {}).get("value", "none")
    out = []
    for p in assembled.get("packages", []):
        score, why = 0, []
        name = p["name"].lower()
        if name.split()[0] in text or p["code"].replace("_", " ") in text:
            score += 3; why.append("seller viewed or asked about it")
        if p["code"] == "trustseal" and ("trust" in text or "document" in text):
            score += 2; why.append("trust/document questions in history")
        if p["code"] == "global" and ("export" in text or "international" in text):
            score += 2; why.append("export interest")
        if p["code"] == "buylead_topup" and objection in ("price", "lead_quality"):
            score += 2; why.append("lets the seller test lead quality before a bigger plan")
        if p["code"] == "star_supplier" and ("catalog" in text or "listing" in text or "monthly" in text):
            score += 1; why.append("listing/monthly plan context")
        if score:
            out.append({"code": p["code"], "name": p["name"], "features": p["features"], "why": why,
                        "price": None, "price_note": "Price not spoken — no verified catalogue price."})
    return sorted(out, key=lambda x: -len(x["why"]))[:3]
