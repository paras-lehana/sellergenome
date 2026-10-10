"""Buyer-side VANI (owner point 13; decision D12).

When a buyer's call to a seller is not picked up, VANI answers as IndiaMART's Help Desk virtual
assistant, understands the requirement, collects details within strict ask limits and either
transfers the buyer to a live alternate seller (explicit consent only) or posts a buy lead.

Two policies, same guard rails:
- ``original`` — the owner-supplied production prompt, rendered with its Jinja conditions and the
  call's variables, executed on Sarvam every turn (faithful, heavier: ~25k tokens for the main flow).
- ``compact``  — SellerGenome's distilled policy of the same rules (fast, default for Barge in).
- ``requirement`` — the owner's "VANI Buyer Requirement Agent" prompt (2026-10-10): product → number of pieces →
  specifications → close with the sellers' link on WhatsApp; no name, city or transfer. Used by *Talk as Buyer*, and the
  same flow runs as a separate Sarvam Voice Agent (``sarvam_buyer``). Closing lines are the owner's, verbatim.
Either way, code enforces the Collection Control limits, tool preconditions and consent — the model
can propose, never override.
"""

from __future__ import annotations

import ast
import json
import re
import secrets
import time
from datetime import datetime, timezone

from backend.genome import prompts, signals
from backend.genome.sarvam import BudgetExceeded, SarvamError

FLOW_RANGES = {"new_requirement": (1, 1243), "enrichment": (1244, 1898), "resume_identity": (1899, 2243), "connection_retry": (2244, 2308)}
FIELDS = ["product", "quantity", "specification", "name", "city", "state", "transfer"]

PRODUCT_CONTEXT = {
    "default": {"units": ["Piece", "Kg", "Set"], "specs": {}},
    "air compressor": {"units": ["Piece", "Unit"], "specs": {"Power": ["5 HP", "10 HP", "15 HP"], "Type": ["Screw", "Reciprocating"]}},
    "solar water heater": {"units": ["Piece", "Unit"], "specs": {"Capacity": ["100 L", "200 L", "300 L"], "Type": ["ETC", "FPC"]}},
    "harvester": {"units": ["Piece", "Set"], "specs": {"Part": ["Cutter bar blade", "Belt", "Bearing"]}},
    "corrugated box": {"units": ["Piece", "Box"], "specs": {"Ply": ["3 Ply", "5 Ply", "7 Ply"], "Size": ["Custom"]}},
    "kurti": {"units": ["Piece"], "specs": {"Fabric": ["Cotton", "Georgette"], "Size": ["S-XXL"]}},
    "coffee machine": {"units": ["Piece"], "specs": {"Type": ["Espresso", "Bean to cup"], "Groups": ["1", "2"]}},
    "tyre": {"units": ["Piece", "Set"], "specs": {"Size": ["12.4-28", "13.6-28", "14.9-28"]}},
    "handi": {"units": ["Piece"], "specs": {"Material": ["Copper", "Aluminium", "Brass"], "Capacity": ["5 L", "10 L", "20 L"]}},
}
SELLERS_BY_STATE = {
    "Haryana": ("Gurugram Air Systems", "Gurugram"), "Maharashtra": ("Sahyadri Solar Solutions", "Pune"),
    "Andhra Pradesh": ("Krishna Agro Spares", "Vijayawada"), "Rajasthan": ("Pink City Packaging", "Jaipur"),
    "Uttar Pradesh": ("Lucknow Chikan Kala", "Lucknow"), "Kerala": ("Malabar Cafe Equipments", "Kochi"),
    "Punjab": ("Doaba Tyres", "Jalandhar"), "Telangana": ("Charminar Copperware", "Hyderabad"),
}

COMPACT_POLICY = """You are {bot}, a Business Development Executive at the IndiaMART Help Desk — a VIRTUAL ASSISTANT (never claim to be human).
The buyer called a seller who did not pick up; the call came to you. Understand the requirement and collect enough for a genuine,
actionable buy lead. If a live alternate seller is available, connecting the buyer is the top priority.
Languages: Hindi (romanised), English, Telugu only. Reply in the buyer's language. Never promise price, availability, delivery or callbacks.
One question per turn. Never re-ask confirmed information. Short replies (under 25 words), polite fillers ("ji", "bilkul").
Collection order: confirm product → quantity (units: {units}) → specifications ({specs}) → name → city and state.
A short "yes/haan" answers only your previous question; a greeting is not consent.
Tools you may request (the system checks preconditions):
- product_context: only after the product is confirmed.
- seller_by_city: only when both city and state are known.
- assisted_buy: only after the buyer clearly agrees to be connected to the available seller.
- post_lead: when collection is complete or exhausted and no transfer happens.
Return JSON: {{"reply": str, "captured": {{"product"?: str, "quantity"?: str, "specs"?: {{name: value}}, "name"?: str, "city"?: str, "state"?: str}},
 "asked_field": "product|quantity|specification|name|city|state|transfer|none", "intents": [max 2 of GREETING, AFFIRMATION, PRODUCT_CHANGE,
 ASK_FOR_SELLER, PRICE_QUERY, COMPLAINT, TALKING_TO_AI, LANGUAGE_SWITCH, OFF_TOPIC, AMBIGUOUS, BUYER_PROVIDING_CONTEXT],
 "tool": null | "product_context" | "seller_by_city" | "assisted_buy" | "post_lead", "consent_to_transfer": true|false,
 "disposition": null | "BL Enriched" | "BL Deleted" | "Connected"}}"""


REQUIREMENT_POLICY = """You are VANI, IndiaMART's virtual assistant, with a male voice: Hindi first-person verbs are masculine ("samajh gaya").
A buyer called a seller on IndiaMART, the seller did not pick up, and the call came to you. Your only job is to understand what the buyer needs and
collect what a seller needs to respond: the product, its specifications and the number of pieces. Never claim to be a human or the seller. Never
promise anything about price, stock, delivery or availability: the seller will share those. Polite, patient, conversational, never an
interrogation, never argue.
Context: product the buyer was looking at: {product}. Seller the buyer tried to call: {seller}. Specification questions, in order (name: options):
{specs}. Units for the number of pieces: {units}. The opening line already asked whether the requirement is for the product. Your only source is
this call: you cannot see WhatsApp, email, images, documents or earlier calls. Languages: Hindi (romanised Hinglish), English or Telugu (speak as
people do on the phone and keep English words such as seller, product, details, size, price). Never translate brand names, model codes or
specification values. Reply in the buyer's language.
Flow: 1) Confirm the product. A bare "haan" or "ji" confirms only when it answers the confirmation; a greeting is not a confirmation. If the buyer
is unsure, say in one sentence what the product is generally used for and ask again in different words. If the buyer names a different product,
confirm it the same way; once agreed set tool "product_context" with captured.product = its English name (never before the buyer confirms). Once
the product is confirmed, in the same turn say you are noting the requirement and the sellers' link will come on WhatsApp, and ask the first
question. 2) Number of pieces: ask with one unit from the list. Never use the word "quantity" or its Hindi equivalent. Accept an exact number, an
estimate, a range (keep the larger number) or a custom unit that counts items; a budget, or the size of one item, is not a number of pieces.
3) Specifications: in the given order, one at a time; offer at most two options and then "ya kuch aur?"; accept any sensible custom value; "any" or
"koi bhi" means Any; if the buyer leaves it to the seller, put that in additional_details and move on; if the buyer looks confused, say in one line
what the specification means. 4) When everything is collected or closed, close with "requirement_noted".
How to ask: one question per turn, under 20 words. After a valid answer say one short respectful filler (ji, ji bilkul, theek hai, accha, samajh
gaya, koi baat nahi) and ask the next question; do not repeat the answer back. Capture anything the buyer volunteers; skip what is already known or
obvious from the product name ("Front Bumper" means Position is Front). Never ask for name or city, never ask "anything else?" or any open question.
Other situations: price or discount: never quote, the seller will share it, continue. Seller's number: you do not have the original seller's
details; suitable sellers' link comes on WhatsApp. Wants only the original seller, only a human, or refuses a bot: do not push; after a second
request close "same_seller". Already shared: you cannot see it, continue; refuses to repeat: close "particular_seller". Order status or delivery:
you cannot check, the seller can help, ask if there is a product requirement; if not close "same_seller". Complaint: apologise once, IndiaMART
Customer Support handles it, ask if there is a product requirement; if it continues close "complaint". Job: careers.indiamart.com, check once for a
product requirement, else close "job". Wants to sell on IndiaMART: Seller Help Desk "five times nine six", check once, else close "selling".
Counterfeit or banned (first copy, alcohol): decline firmly in one line and invite a legitimate product; repeated: close "banned". Image or
catalogue: you cannot view images from outside this call, continue. Off-topic: back to the product; after two such turns close "no_requirement".
Cannot hear: ask once; the second time close "no_requirement". Another language: you speak only Hindi, English or Telugu; ask the buyer's choice.
Speech: natural spoken sentences for text-to-speech, only commas, periods and question marks; numbers as a person says them ("two hundred and
fifty"); units in full ("fifteen kilogram"). Never say variable names, tools, JSON or your reasoning.
Rules that are easy to break (live QA, 2026-10-10):
- Write the whole reply in the language you put in "language". When the buyer asks for English, set "en-IN" and reply only in English from then on
  (wrong: "Ji bilkul. Power kya lena hai?"; right: "Sure. Which power do you need, 5 HP or 10 HP, or something else?").
- Name at most TWO options for a specification, never three or more (wrong: "5 HP, 10 HP ya 15 HP?"; right: "5 HP ya 10 HP, ya kuch aur?").
- Never repeat the buyer's answer back (wrong: "Aapko fifty piece chahiye, theek hai."; right: "Theek hai." and the next question).
Return JSON: {{"reply": str, "language": "hi-IN|en-IN|te-IN", "product_confirmed": bool, "captured": {{"product"?: str, "quantity"?: str,
 "quantity_unit"?: str, "specs"?: {{name: value}}, "additional_details"?: str}}, "asked_field": "product|quantity|specification|none",
 "asked_spec": str|null, "intents": [max 2 of GREETING, AFFIRMATION, PRODUCT_CHANGE, ASK_FOR_SELLER, PRICE_QUERY, COMPLAINT, TALKING_TO_AI,
 LANGUAGE_SWITCH, OFF_TOPIC, AMBIGUOUS, ORDER_STATUS, JOB, SELLING, BANNED], "tool": null | "product_context",
 "close": null | "requirement_noted" | "no_requirement" | "banned" | "same_seller" | "particular_seller" | "job" | "selling" | "complaint"}}"""

# The owner's closing lines, verbatim (Hindi; English as given; Telugu uses the model's own line). (disposition, outcome, hi, en)
REWRITE_PROMPT = ("Rewrite this reply for a phone call: {fixes}. Keep the same meaning and the same single question, under 25 words. "
                  "Return JSON {{\"reply\": str}}.")

CLOSES = {
    "requirement_noted": ("BL Enriched", "BL Enriched",
                          "Aapki requirement note ho gayi hai. Suitable sellers ka link aapko WhatsApp par share kar diya jayega. Thank you for calling IndiaMart.",
                          "Your requirement is noted. I will share the sellers' link with you on WhatsApp. Thank you for calling IndiaMART."),
    "no_requirement": ("BL Deleted", "No Product Requirement",
                       "Theek hai. Agar bhavishya mein aapko kisi product ya service ki requirement ho toh IndiaMart se zaroor sampark kijiye. IndiaMart ko call karne ke liye dhanyavaad.",
                       "Alright. If you need any product or service in future, please do contact IndiaMART. Thank you for calling IndiaMART."),
    "banned": ("BL Deleted", "No Product Requirement", "Is product mein main madad nahi kar paunga. IndiaMart ko call karne ke liye dhanyavaad.",
               "I will not be able to help with this product. Thank you for calling IndiaMART."),
    "same_seller": ("BL Deleted", "No Product Requirement",
                    "Aap same seller ko das se pandra minute ke baad dobara call kar sakte hain. IndiaMart pe call karne ke liye dhanyavaad.",
                    "You can call the same seller again after ten to fifteen minutes. Thank you for calling IndiaMART."),
    "particular_seller": ("BL Deleted", "Particular Seller",
                          "Aap same seller ko das se pandra minute ke baad dobara call kar sakte hain. IndiaMart pe call karne ke liye dhanyavaad.",
                          "You can call the same seller again after ten to fifteen minutes. Thank you for calling IndiaMART."),
    "job": ("BL Deleted", "Job Related___BL Not Approved", "Samajh gaya. Thank you for calling Indiamart. Have a great day.",
            "Understood. Thank you for calling IndiaMART. Have a great day."),
    "selling": ("BL Deleted", "Selling Related", "Samajh gaya. Thank you for calling Indiamart. Have a great day.",
                "Understood. Thank you for calling IndiaMART. Have a great day."),
    "complaint": (None, None,
                  "Theek hai. Complaint register karne ke liye aap IndiaMart Customer Support se sampark kar sakte hain. IndiaMart ke valuable customer hone ke liye dhanyavaad, apka din shubh ho.",
                  "Alright. To register a complaint, please contact IndiaMART Customer Support. Thank you for being a valued IndiaMART customer, have a good day."),
}
REQUIREMENT_LIMITS = {"product": 4, "quantity": 3, "specification": 2}  # specification = per specification
# Hindi (Devanagari or romanised) and romanised Telugu words that should not appear once the buyer asked for English.
HINGLISH = re.compile(r"[ऀ-ॿఀ-౿]|\b(hai|hain|kya|chahiye|aapko|aapka|theek|kitne|kitna|lena|nahi|bilkul|accha|haan|kavala|kaavali|"
                      r"leda|meeku|cheppandi|unda|undi|enni|kaavaali|sare|avunu)\b", re.I)


def requirement_opening(product: str, seller: str, language: str) -> str:
    """VANI's first line on Talk as Buyer: who answers, that the seller is busy, and the product confirmation."""
    if language == "en-IN":
        return f"Namaste, this is VANI, IndiaMART's virtual assistant. {seller} is busy right now. Is your requirement for {product}?"
    if language == "te-IN":
        return f"Namaskaram, IndiaMART nundi VANI, virtual assistant. {seller} ippudu busy ga unnaru. Meeku {product} requirement unda?"
    return f"Namaste, IndiaMART se main VANI bol raha hoon, virtual assistant. {seller} abhi busy hain. Kya aapko {product} ki requirement hai?"


def product_context(product: str) -> dict:
    """Units and specification options for a product (the buyer tools' synthetic catalogue; a default for anything else)."""
    p = (product or "").lower()
    for key, ctx in PRODUCT_CONTEXT.items():
        if key != "default" and key in p:
            return ctx
    return PRODUCT_CONTEXT["default"]


def _unescape(text: str) -> str:
    return (text.replace("\\_", "_").replace("\\!", "!").replace("\\=", "=").replace("\\[", "[").replace("\\]", "]")
            .replace("\\>", ">").replace("\\<", "<").replace("\\-", "-").replace("\\*", "*").replace("﻿", ""))


class _Eval(ast.NodeVisitor):
    """Safe evaluator for template conditions: names, strings, numbers, ==, !=, >=, <=, <, >, and, or, not, in, tuples."""

    def __init__(self, variables: dict) -> None:
        self.v = variables

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BoolOp(self, node):
        values = [self.visit(x) for x in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            return not self.visit(node.operand)
        raise ValueError("unsupported")

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comp in zip(node.ops, node.comparators):
            right = self.visit(comp)
            ok = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.In: lambda a, b: a in b,
                  ast.NotIn: lambda a, b: a not in b, ast.GtE: lambda a, b: float(a or 0) >= float(b),
                  ast.LtE: lambda a, b: float(a or 0) <= float(b), ast.Gt: lambda a, b: float(a or 0) > float(b),
                  ast.Lt: lambda a, b: float(a or 0) < float(b)}[type(op)](left, right)
            if not ok:
                return False
            left = right
        return True

    def visit_Name(self, node):
        return self.v.get(node.id, "")

    def visit_Constant(self, node):
        return node.value

    def visit_Tuple(self, node):
        return tuple(self.visit(x) for x in node.elts)

    def generic_visit(self, node):
        raise ValueError(f"unsupported expression: {type(node).__name__}")


def _condition(expr: str, variables: dict) -> bool:
    expr = re.sub(r"\|\s*int\b", "", expr.strip())
    try:
        return bool(_Eval(variables).visit(ast.parse(expr, mode="eval")))
    except (SyntaxError, ValueError, KeyError, TypeError):
        return False


def render(template: str, variables: dict) -> str:
    """Render {% if %}/{% elif %}/{% else %}/{% endif %} blocks and ﻿ var ﻿ placeholders."""
    # Placeholders are wrapped in U+FEFF in the supplied document: "\ufeff bot\\_name \ufeff" → value.
    # "\ufeff@ var\ufeff" marks a variable the bot sets; keep it readable as "@var".
    template = re.sub(r"\ufeff\s*@\s*([A-Za-z\\_]+)\s*\ufeff", lambda m: "@" + m.group(1).replace("\\_", "_"), template)
    template = re.sub(r"\ufeff\s*([A-Za-z\\_]+)\s*\ufeff",
                      lambda m: str(variables.get(m.group(1).replace("\\_", "_"), m.group(1).replace("\\_", "_"))), template)
    text = _unescape(template)
    tokens = re.split(r"(\{%.*?%\})", text, flags=re.S)
    out, stack = [], []  # stack of [branch_taken, currently_active]

    def active() -> bool:
        return all(s[1] for s in stack)
    for tok in tokens:
        m = re.fullmatch(r"\{%\s*(if|elif|else|endif)\s*(.*?)\s*%\}", tok, flags=re.S)
        if not m:
            if active():
                out.append(tok)
            continue
        kind, expr = m.group(1), m.group(2)
        if kind == "if":
            ok = active() and _condition(expr, variables)
            stack.append([ok, ok])
        elif kind == "elif" and stack:
            taken = stack[-1][0]
            ok = (not taken) and all(s[1] for s in stack[:-1]) and _condition(expr, variables)
            stack[-1] = [taken or ok, ok]
        elif kind == "else" and stack:
            taken = stack[-1][0]
            stack[-1] = [True, (not taken) and all(s[1] for s in stack[:-1])]
        elif kind == "endif" and stack:
            stack.pop()
    rendered = "".join(out)
    return re.sub(r"\n{3,}", "\n\n", rendered)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class BuyerDesk:
    def __init__(self, settings, store, sarvam, executor=None) -> None:
        self.settings, self.store, self.sarvam, self.executor = settings, store, sarvam, executor

    def original_prompt(self) -> str | None:
        """The owner's original VANI prompt (BUYER_ORIGINAL_PROMPT_PATH), or its Admin → Prompts edit."""
        return prompts.text("buyer.original") or None

    def start(self, tile: dict, operator: bool, policy: str = "compact", talk: bool = False) -> dict:
        call_info = tile.get("call") or tile
        limits = self.settings.get("buyer.limits", {})
        captured: dict = {}
        transcript = []
        if talk:  # Talk as Buyer: the call starts from scratch with VANI's opening; the operator is the buyer
            policy = "requirement"
            limits = dict(REQUIREMENT_LIMITS)
            language = call_info.get("language", "hi-IN")
            transcript.append({"who": "bot", "text": requirement_opening(call_info.get("product") or "the product",
                                                                         call_info.get("missed_seller") or "the seller", language),
                               "source": "opening", "at": utc_now()})
        for t in ([] if talk else tile.get("turns", [])[: max(1, int(tile.get("shown", len(tile.get("turns", [])))))]):
            transcript.append({"who": "bot" if t["who"] == "bot" else "buyer", "text": t["text"], "gloss": t.get("gloss"),
                               "scripted": True, "at": utc_now()})
            for k, v in (t.get("capture") or {}).items():
                captured[k] = v
        lead = {"product": captured.get("product"), "quantity": captured.get("quantity"),
                "specs": {k.split(":", 1)[1]: v for k, v in captured.items() if k.startswith("spec:")},
                "name": captured.get("name"), "city": captured.get("city"), "state": captured.get("state")}
        attempts = {f: 0 for f in FIELDS}
        for t in transcript:
            if t["who"] == "bot":
                asked = self._asked(t["text"])
                if asked:
                    attempts[asked] += 1
        if talk:
            lead = {"product": call_info.get("product"), "quantity": None, "quantity_unit": None, "specs": {}, "additional_details": None,
                    "name": None, "city": None, "state": None}
            attempts = {"product": 1, "quantity": 0}  # the opening asked the product confirmation once
        call = {"id": "bcall-" + secrets.token_hex(6), "flow": call_info.get("flow", "new_requirement"), "policy": policy, "talk": talk,
                "product_confirmed": False, "outcome": None, "adaptations": [],
                "spec_names": list(product_context(call_info.get("product") or "")["specs"]) if talk else [],
                "language": call_info.get("language", "hi-IN"), "buyer_glid": call_info.get("buyer_glid"),
                "missed_seller": call_info.get("missed_seller"), "operator": operator, "created_at": utc_now(),
                "transcript": transcript, "lead": lead, "attempts": attempts, "limits": limits, "closed": [],
                "tools": [], "intents": [], "available_seller": None, "consent": False, "disposition": None,
                "status": "collecting", "events": []}
        self.store.put("buyer_calls", call["id"], call)
        return call

    def _asked(self, text: str) -> str | None:
        t = text.lower()
        for field, pattern in (("quantity", r"kitn|how many|quantity|entha"), ("name", r"naam|name|peru"),
                               ("city", r"shehar|city|kahan se|ooru|kis jagah"), ("transfer", r"connect|jod|transfer"),
                               ("specification", r"kaun sa|which type|spec|size|capacity|material"),
                               ("product", r"kya aapko .* chahiye|requirement|product")):
            if re.search(pattern, t):
                return field
        return None

    def _product_context(self, product: str) -> dict:
        return product_context(product)

    async def turn(self, call: dict, text: str) -> dict:
        if call["status"] in ("transferred", "ended"):
            raise ValueError("This buyer call has ended.")
        call["transcript"].append({"who": "buyer", "text": text.strip()[:600], "at": utc_now(), "source": "typed"})
        ctx = self._product_context(call["lead"].get("product"))
        bot = self.settings.get("app.bot_name", "Payal")
        variables = self._variables(call, ctx, bot)
        if call["policy"] == "requirement":
            return await self._requirement_turn(call, ctx)
        if call["policy"] == "original" and self.original_prompt():
            start, end = FLOW_RANGES.get(call["flow"], FLOW_RANGES["new_requirement"])
            lines = self.original_prompt().splitlines()[start - 1:end]
            system = render("\n".join(lines), variables)
            system += ("\n\nOUTPUT FORMAT FOR THIS SIMULATION: return JSON {\"reply\": str, \"captured\": {...}, \"asked_field\": str, "
                       "\"intents\": [..], \"tool\": null|\"product_context\"|\"seller_by_city\"|\"assisted_buy\"|\"post_lead\", "
                       "\"consent_to_transfer\": bool, \"disposition\": null|str}.")
        else:
            system = prompts.text("buyer.compact").format(bot=bot, units=", ".join(ctx["units"]), specs=json.dumps(ctx["specs"], ensure_ascii=False))
        state = {"lead": call["lead"], "attempts": call["attempts"], "limits": call["limits"], "closed": call["closed"],
                 "available_seller": call["available_seller"], "missed_seller": call["missed_seller"]}
        history = [{"who": t["who"], "text": t["text"]} for t in call["transcript"][-14:]]
        scope = "operator" if call["operator"] else "public"
        try:
            reply = await self.sarvam.chat_json(system, json.dumps({"state": state, "conversation": history}, ensure_ascii=False),
                                                scope=scope, max_tokens=450, temperature=0.3, label=f"buyer-{call['policy']}")
            data, ms = reply["data"], reply["ms"]
        except (SarvamError, BudgetExceeded) as error:
            data, ms = {"reply": "Ji, ek minute — kya aap apni requirement dobara bata sakte hain?", "asked_field": "product"}, 0
            call["events"].append({"at": utc_now(), "kind": "fallback", "detail": str(error)[:200]})
        guard = self._apply(call, data, ctx)
        if guard.get("tool") == "seller_by_city":
            await self._run_tool(call, "seller_by_city", ctx)
            offer = self._next_question(call, ctx)
            if offer:
                guard["reply"] = offer
                guard["notes"].append("Live seller found → transfer offer (consent required).")
            guard["tool"] = None
        call["transcript"].append({"who": "bot", "text": guard["reply"], "at": utc_now(), "ms": ms, "policy": call["policy"],
                                   "intents": data.get("intents", [])[:2], "guard": guard["notes"]})
        if guard.get("tool"):
            await self._run_tool(call, guard["tool"], ctx)
        self.store.put("buyer_calls", call["id"], call)
        return {"call": call, "guard": guard}

    # ---- requirement policy (owner's VANI Buyer Requirement prompt) ----
    async def _requirement_turn(self, call: dict, ctx: dict) -> dict:
        lead = call["lead"]
        # An explicit language request ("please speak English") switches in code, not only when the model notices (live QA:
        # the model once kept Hindi). The model is then told the language; the rewrite guard keeps English clean.
        said = next((t["text"] for t in reversed(call["transcript"]) if t["who"] == "buyer"), "")
        wanted = next((d.get("target_language") for d in signals.detect(said, call["language"])
                       if d["signal"] == "language_switch" and d.get("explicit")), None)
        if wanted in ("hi-IN", "en-IN", "te-IN") and wanted != call["language"]:
            names = {"hi-IN": "Hindi", "en-IN": "English", "te-IN": "Telugu"}
            self._adapt(call, "language", f"Language switch: {names[call['language']]} → {names[wanted]} — VANI follows the buyer")
            call["language"] = wanted
        system = prompts.text("buyer.requirement").format(product=lead.get("product") or "the product", seller=call.get("missed_seller") or "the seller",
                                           specs=json.dumps(ctx["specs"], ensure_ascii=False), units=", ".join(ctx["units"]))
        state = {"lead": lead, "product_confirmed": call.get("product_confirmed"), "attempts": call["attempts"], "limits": call["limits"],
                 "closed": call["closed"], "language": call["language"]}
        history = [{"who": t["who"], "text": t["text"]} for t in call["transcript"][-14:] if t["who"] in ("bot", "buyer")]
        scope = "operator" if call["operator"] else "public"
        try:
            reply = await self.sarvam.chat_json(system, json.dumps({"state": state, "conversation": history}, ensure_ascii=False),
                                                scope=scope, max_tokens=400, temperature=0.3, label="buyer-requirement")
            data, ms = reply["data"], reply["ms"]
        except (SarvamError, BudgetExceeded) as error:
            data, ms = {"reply": "Ji, ek baar phir bata dijiye, aapko kya chahiye?", "asked_field": "product"}, 0
            call["events"].append({"at": utc_now(), "kind": "fallback", "detail": str(error)[:200]})
        guard = self._apply_requirement(call, data)
        fixes = self._reply_problems(call, data, guard["reply"], ctx)
        if fixes and call["status"] != "ended":
            # Rules the model keeps breaking (live QA): the buyer's language and at most two options. One rewrite, never a second.
            try:
                fixed = await self.sarvam.chat_json(prompts.text("buyer.rewrite").format(fixes="; ".join(fixes)), guard["reply"],
                                                    scope=scope, max_tokens=120, temperature=0.1, label="buyer-rewrite")
                if str(fixed["data"].get("reply") or "").strip():
                    guard["reply"] = str(fixed["data"]["reply"]).strip()[:500]
                    guard["notes"].append("Reply rewritten: " + "; ".join(fixes) + ".")
                    ms += fixed["ms"]
            except (SarvamError, BudgetExceeded):
                pass
        call["transcript"].append({"who": "bot", "text": guard["reply"], "at": utc_now(), "ms": ms, "policy": "requirement",
                                   "intents": [str(i) for i in (data.get("intents") or [])][:2], "guard": guard["notes"]})
        self.store.put("buyer_calls", call["id"], call)
        return {"call": call, "guard": guard}

    @staticmethod
    def _reply_problems(call: dict, data: dict, reply: str, ctx: dict) -> list[str]:
        """What a rewrite must fix: words from another language after an English switch, or more than two options."""
        fixes = []
        if call["language"] == "en-IN" and HINGLISH.search(reply):
            fixes.append("write it only in plain, polite Indian English")
        spec = str(data.get("asked_spec") or "").strip()
        options = ctx["specs"].get(spec) or next((o for n, o in ctx["specs"].items() if n.lower() in reply.lower()), [])
        named = [o for o in options if o.lower() in reply.lower()]
        if len(named) > 2:
            joiner = " or " if call["language"] == "en-IN" else " ya "
            fixes.append(f"name only two options, {named[0]}{joiner}{named[1]}, then invite any other answer")
        return fixes

    def _adapt(self, call: dict, kind: str, text: str) -> None:
        """A visible change in how VANI handles this buyer (shown as a banner on the call page)."""
        call.setdefault("adaptations", []).append({"kind": kind, "text": text, "turn": len(call["transcript"]), "at": utc_now()})

    def _apply_requirement(self, call: dict, data: dict) -> dict:
        """Code enforces the owner's ask limits, the product-change rule and the closings; the model proposes."""
        notes: list[str] = []
        lead = call["lead"]
        captured = data.get("captured") if isinstance(data.get("captured"), dict) else {}
        intents = [str(i) for i in (data.get("intents") or [])][:2]
        reply = str(data.get("reply") or "").strip()[:500]
        lang = data.get("language") if data.get("language") in ("hi-IN", "en-IN", "te-IN") else call["language"]
        if lang != call["language"]:
            names = {"hi-IN": "Hindi", "en-IN": "English", "te-IN": "Telugu"}
            self._adapt(call, "language", f"Language switch: {names[call['language']]} → {names[lang]} — VANI follows the buyer")
            notes.append(f"Language switch → {names[lang]}.")
            call["language"] = lang
        new_product = str(captured.get("product") or "").strip()[:120]
        if new_product and lead.get("product") and new_product.lower() != lead["product"].lower() and (
                "PRODUCT_CHANGE" in intents or data.get("tool") == "product_context"):
            if data.get("product_confirmed") or data.get("tool") == "product_context":
                lead.update(product=new_product, quantity=None, quantity_unit=None, specs={})
                call["attempts"] = {"product": call["attempts"].get("product", 1), "quantity": 0}
                call["closed"] = [c for c in call["closed"] if c == "product"]
                ctx = product_context(new_product)
                call["spec_names"] = list(ctx["specs"])
                call["tools"].append({"tool": "product_context", "at": utc_now(), "ms": 120.0, "origin": "mock",
                                      "result": {"product": new_product, "quantity_unit_options": ctx["units"], "specification_options": ctx["specs"]}})
                self._adapt(call, "product", f"Product changed to {new_product} — options reloaded (requested_another_product_tool)")
                notes.append("Product change confirmed → specification and unit options reloaded.")
                call["product_confirmed"] = True
            else:
                notes.append("Different product named — not switched until the buyer confirms it.")
        elif data.get("product_confirmed") and not call.get("product_confirmed"):
            call["product_confirmed"] = True
            call["disposition"] = call["outcome"] = "BL Approved"
            notes.append("Product confirmed → BL Approved.")
        for key in ("quantity", "quantity_unit", "additional_details"):
            if captured.get(key):
                lead[key] = str(captured[key])[:120]
        if isinstance(captured.get("specs"), dict):
            lead["specs"] = {**(lead.get("specs") or {}), **{str(k)[:40]: str(v)[:60] for k, v in captured["specs"].items()}}
        # Ask limits for the whole call: product 4, number of pieces 3, each specification 2.
        asked = data.get("asked_field") if data.get("asked_field") in ("product", "quantity", "specification") else None
        key = f"spec:{str(data.get('asked_spec') or '').strip()[:40]}" if asked == "specification" else asked
        if key and key != "spec:":
            limit = int(call["limits"].get("specification" if key.startswith("spec:") else key, 3))
            used = call["attempts"].get(key, 0)
            if used >= limit or key in call["closed"]:
                if key not in call["closed"]:
                    call["closed"].append(key)
                label = key.split(":", 1)[-1] if key.startswith("spec:") else {"product": "the product", "quantity": "the number of pieces"}[key]
                self._adapt(call, "limit", f"Ask limit reached for {label} ({used}/{limit}) — left to the seller")
                notes.append(f"Blocked: {label} already asked {used}/{limit} times.")
                if key == "product" and not call.get("product_confirmed"):
                    data["close"] = "no_requirement"
                else:
                    hindi = call["language"] != "en-IN"
                    nxt = self._next_requirement(call)
                    reply = ("Koi baat nahi, yeh aap seller se seedha discuss kar lijiye." if hindi else
                             "No problem, you can discuss that directly with the seller.") + (f" {nxt}" if nxt else "")
                    if not nxt:
                        data["close"] = "requirement_noted"
            else:
                call["attempts"][key] = used + 1
        call["intents"] = (call["intents"] + intents)[-10:]
        close = data.get("close") if data.get("close") in CLOSES else None
        if close:
            disposition, outcome, hi, en = CLOSES[close]
            if disposition:
                call["disposition"], call["outcome"] = disposition, outcome
            reply = en if call["language"] == "en-IN" else hi if call["language"] == "hi-IN" else (reply or en)
            call["status"] = "ended"
            ctx = product_context(lead.get("product") or "")
            requirement = {"product": lead.get("product") if call.get("product_confirmed") else None, "quantity": lead.get("quantity"),
                           "quantity_unit": lead.get("quantity_unit"), "specs": lead.get("specs"), "additional_details": lead.get("additional_details"),
                           "buyer_disposition": call["disposition"], "call_outcome": call["outcome"]}
            call["tools"].append({"tool": "post_lead", "at": utc_now(), "ms": 120.0, "origin": "mock",
                                  "result": {"status": "test_lead_accepted", "posted_to_production": False, "requirement": requirement,
                                             "units_offered": ctx["units"]}})
            if close == "requirement_noted":
                call["tools"].append({"tool": "whatsapp", "at": utc_now(), "ms": 0.0, "origin": "mock",
                                      "result": {"status": "test only — no message is sent", "would_send": "suitable sellers' link"}})
            notes.append(f"Closed: {close.replace('_', ' ')} → {call['disposition'] or 'no change'} / {call['outcome'] or 'no change'}.")
        return {"reply": reply or "Ji.", "notes": notes, "tool": None}

    def _next_requirement(self, call: dict) -> str | None:
        """The next question code asks when the model hit a limit: the number of pieces, then each open specification."""
        lead, att, lim = call["lead"], call["attempts"], call["limits"]
        hindi = call["language"] != "en-IN"
        ctx = product_context(lead.get("product") or "")
        if not lead.get("quantity") and "quantity" not in call["closed"] and att.get("quantity", 0) < int(lim.get("quantity", 3)):
            att["quantity"] = att.get("quantity", 0) + 1
            unit = ctx["units"][0].lower()
            return f"Aapko kitne {unit} chahiye?" if hindi else f"How many {unit}s do you need?"
        for name, options in ctx["specs"].items():
            key = f"spec:{name}"
            if name not in (lead.get("specs") or {}) and key not in call["closed"] and att.get(key, 0) < int(lim.get("specification", 2)):
                att[key] = att.get(key, 0) + 1
                two = " ya ".join(options[:2]) if hindi else " or ".join(options[:2])
                return f"{name} kaun sa chahiye, {two}, ya kuch aur?" if hindi else f"Which {name}, {two}, or something else?"
        return None

    def _variables(self, call: dict, ctx: dict, bot: str) -> dict:
        lead = call["lead"]
        seller = call["available_seller"] or {}
        return {"bot_name": bot, "product_name": lead.get("product") or "", "buyer_name": lead.get("name") or "",
                "updated_buyer_name": lead.get("name") or "", "buyer_city": lead.get("city") or "",
                "quantity_unit_options": json.dumps(ctx["units"]), "specification_options": json.dumps(ctx["specs"]),
                "is_enrich": "1" if call["flow"] == "enrichment" else "0", "lead_type": "LIVE",
                "ast_seller_pns": "08000000000" if seller else "", "ast_flow_live": "true" if seller else "false",
                "triangulation_response_value": "0", "triangulation_response_city": "", "pcd_pitch": "false",
                "off_topic_counter": "0", "language_name": {"te-IN": "telugu", "en-IN": "english"}.get(call["language"], "hindi"),
                "turn_intent": ", ".join(call["intents"][-2:]), "quantity": lead.get("quantity") or "",
                "specifications": json.dumps(lead.get("specs") or {}), "seller_name": seller.get("name", ""),
                "ast_seller_city": seller.get("city", ""), "ast_seller_company": seller.get("name", "")}

    def _apply(self, call: dict, data: dict, ctx: dict) -> dict:
        """Enforce Collection Control and tool preconditions on the model's proposal."""
        notes = []
        captured = data.get("captured") if isinstance(data.get("captured"), dict) else {}
        lead = call["lead"]
        if captured.get("product") and lead.get("product") and captured["product"].lower() != lead["product"].lower():
            if any(i == "PRODUCT_CHANGE" for i in data.get("intents", [])):
                lead.update(quantity=None, specs={})
                call["attempts"].update(product=0, quantity=0, specification=0)
                notes.append("Genuine product change → product-specific fields reset; name/location kept.")
        for key in ("product", "quantity", "name", "city", "state"):
            if captured.get(key):
                lead[key] = str(captured[key])[:120]
        if isinstance(captured.get("specs"), dict):
            lead["specs"] = {**(lead.get("specs") or {}), **{str(k)[:40]: str(v)[:60] for k, v in captured["specs"].items()}}
        asked = data.get("asked_field") if data.get("asked_field") in FIELDS else None
        reply = str(data.get("reply") or "").strip()[:500]
        if asked:
            limit = int(call["limits"].get(asked, 3))
            if call["attempts"][asked] >= limit or asked in call["closed"]:
                notes.append(f"Blocked: '{asked}' already asked {call['attempts'][asked]}/{limit} times — closed and deferred to the seller.")
                if asked not in call["closed"]:
                    call["closed"].append(asked)
                reply = self._next_question(call, ctx) or "Ji, baaki details seller aapse seedha discuss kar lenge."
            else:
                call["attempts"][asked] += 1
        call["intents"] = (call["intents"] + list(data.get("intents") or [])[:2])[-10:]
        tool = data.get("tool")
        if tool == "seller_by_city" and not (lead.get("city") and lead.get("state")):
            notes.append("Tool seller_by_city blocked: needs both city and state.")
            tool = None
        if tool == "product_context" and not lead.get("product"):
            notes.append("Tool product_context blocked: product not confirmed.")
            tool = None
        last_bot = next((t["text"] for t in reversed(call["transcript"][:-1]) if t["who"] == "bot"), "")
        last_buyer = next((t["text"] for t in reversed(call["transcript"]) if t["who"] == "buyer"), "")
        offered = bool(re.search(r"connect|jod|transfer|baat kar(a|wa)", last_bot, re.I))
        agrees = bool(re.search(r"\b(haan|han|ha|yes|ok|okay|theek|thik|bilkul|kar do|kardo|connect|jod do|zaroor|avunu|sare)\b", last_buyer, re.I))
        refuses = bool(re.search(r"\b(nahi|nahin|no|mat|don'?t|vaddu)\b", last_buyer, re.I))
        if call["available_seller"] and offered and agrees and not refuses:
            call["consent"] = True
            notes.append("Explicit consent after the transfer offer → assisted buy.")
        elif data.get("consent_to_transfer") is True and call["available_seller"] and offered:
            call["consent"] = True
        if tool == "assisted_buy" and not (call["available_seller"] and call["consent"]):
            notes.append("Tool assisted_buy blocked: needs an available seller AND explicit buyer consent.")
            tool = None
        if call["consent"] and call["available_seller"] and call["status"] == "collecting":
            tool = "assisted_buy"
            seller = call["available_seller"]
            reply = (f"Ji, main aapki call abhi {seller['name']} se connect kar rahi hoon. Dhanyavaad!" if call["language"] != "en-IN"
                     else f"Connecting you to {seller['name']} now. Thank you!")
        # Auto-advance: once city+state are known and no seller looked up yet, look one up.
        if not tool and lead.get("city") and lead.get("state") and not call["available_seller"] and not any(t["tool"] == "seller_by_city" for t in call["tools"]):
            tool = "seller_by_city"
            notes.append("City and state known → looking up a live alternate seller.")
        if data.get("disposition") and tool not in ("assisted_buy",):
            call["disposition"] = str(data["disposition"])[:40]
        return {"reply": reply or "Ji.", "notes": notes, "tool": tool}

    def _next_question(self, call: dict, ctx: dict) -> str | None:
        lead, att, lim = call["lead"], call["attempts"], call["limits"]
        hindi = call["language"] != "en-IN"
        seller = call.get("available_seller")
        if seller and not call["consent"] and att["transfer"] < int(lim.get("transfer", 2)):
            att["transfer"] += 1
            return (f"{seller['name']} ({seller['city']}) abhi available hain. Kya main aapki call unse connect kar doon?" if hindi
                    else f"{seller['name']} in {seller['city']} is available now. Shall I connect you?")
        order = [("quantity", not lead.get("quantity"), "Kitni quantity chahiye aapko?", "What quantity do you need?"),
                 ("name", not lead.get("name"), "Aapka shubh naam kya hai?", "May I know your name?"),
                 ("city", not lead.get("city"), "Aap kis shehar aur rajya se hain?", "Which city and state are you in?")]
        for field, needed, hi, en in order:
            if needed and att[field] < int(lim.get(field, 3)) and field not in call["closed"]:
                att[field] += 1
                return hi if hindi else en
        return None

    async def _run_tool(self, call: dict, tool: str, ctx: dict) -> None:
        started = time.monotonic()
        lead = call["lead"]
        if tool == "product_context":
            result = {"product": lead.get("product"), "quantity_unit_options": ctx["units"], "specification_options": ctx["specs"]}
        elif tool == "seller_by_city":
            _, city = SELLERS_BY_STATE.get(lead.get("state") or "", ("", lead.get("city") or ""))
            city = lead.get("city") or city
            product_word = (lead.get("product") or "Trade").split()[-1].title()
            name = f"{city} {product_word} House"
            result = {"available": True, "seller": {"name": name, "city": city, "rating": 4.4, "response_rate": "92%"},
                      "note": "Synthetic availability — no real seller is contacted."}
            call["available_seller"] = result["seller"]
            call["transcript"].append({"who": "system", "text": f"Live seller available: {name}, {city}. Ask for transfer consent.", "at": utc_now()})
        elif tool == "assisted_buy":
            result = {"status": "transferred_test", "posted_to_production": False, "seller": call["available_seller"]}
            call.update(status="transferred", disposition="Connected")
        else:  # post_lead
            result = {"status": "test_lead_accepted", "posted_to_production": False, "lead": lead}
            call.update(status="ended", disposition=call.get("disposition") or "BL Enriched")
        call["tools"].append({"tool": tool, "at": utc_now(), "ms": round((time.monotonic() - started) * 1000 + 120, 1),
                              "origin": "mock", "result": result})
