"""Every prompt SellerGenome sends to a model, in one catalogue (owner, 2026-10-10: "the prompts (all, described also where
they are used) should be visible in admin in a prompt section. Should be also editable from there").

Each entry says where the prompt is used, which model call carries it and when an edit takes effect. The default text
stays in the module that uses it (so code review still sees it); an Admin edit is stored in the V5 store and wins until it
is reset. ``text(id)`` is what every call site reads. Edits are checked before they are saved: a prompt that would break
its call (a missing placeholder, a Sarvam build rule, the gateway's length limit) is refused with the reason.
"""
from __future__ import annotations

import importlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
KIND = "prompt_override"
HISTORY = 10
_store = None


def bind(store) -> None:
    """Called once by the service so call sites can read Admin edits."""
    global _store
    _store = store


def _const(module: str, name: str) -> Callable[[], str]:
    return lambda: getattr(importlib.import_module(f"backend.genome.{module}"), name)


def _file(path: Path, limit: int | None = None) -> Callable[[], str]:
    def read() -> str:
        try:
            text = path.read_text()
        except OSError:
            return ""
        return text[:limit] if limit else text
    return read


def _env_file(var: str) -> Callable[[], str]:
    return lambda: _file(Path(os.getenv(var, "") or "/nonexistent"))()


def _note(key: str) -> Callable[[], str]:
    return lambda: importlib.import_module("backend.genome.conversation").ARCHETYPE_NOTE.get(key, "")


TRAINING = ROOT / "docs" / "inputs" / "sales-training-prompts"
NOW_BUILD_PAYAL = "On the next Build of Payal on Sarvam (button below, or Admin → Sarvam voice agent)."
NOW_BUILD_VANI = "On the next Build of VANI on Sarvam (button below, or Admin → Buyer VANI)."
NEXT_TURN = "On the next model call — no restart."

# id, group, title, where it is used, model call label, check, extra
CATALOG: list[dict] = [
    # Seller side — the built-in call engine (Barge in, Talk as Seller)
    {"id": "seller.bot_turn", "group": "Seller call · built-in Payal", "title": "Payal's reply on a seller call",
     "used": "Every bot turn on a seller call (Barge in, Talk as Seller, auto-play). The system prompt; the user message carries "
             "seller_facts from live APIs and the last 12 turns.", "label": "bot-turn", "where": "backend/genome/conversation.py · _bot_reply",
     "check": "format", "names": ["bot_name", "language", "max_words", "warmth", "empathy", "formality", "directives"],
     "default": _const("conversation", "BOT_TURN_SYSTEM"), "applies": NEXT_TURN},
    *[{"id": f"seller.directive.{k}", "group": "Seller call · built-in Payal", "title": f"Live adaptation: {t}",
       "used": f"Added to Payal's reply prompt ({{directives}}) on the turn after the seller {w}.", "label": "bot-turn",
       "where": "backend/genome/conversation.py · DIRECTIVES", "check": "plain",
       "default": (lambda k=k: importlib.import_module("backend.genome.conversation").DIRECTIVES[k]), "applies": NEXT_TURN}
      for k, t, w in (("acknowledge", "acknowledge frustration", "sounds frustrated"),
                      ("simplify", "simplify", "sounds confused"),
                      ("advance", "move to the meeting", "shows interest"),
                      ("bot_question", "honest about being a bot", "asks whether this is a bot"))],
    {"id": "seller.simulated", "group": "Seller call · simulated seller", "title": "Simulated seller (auto-play)",
     "used": "Auto-play on the call floor: the model plays the seller. {archetype_prompt} is the training prompt for the seller's "
             "archetype (below), {archetype_note} its one-line behaviour.", "label": "sim-seller",
     "where": "backend/genome/conversation.py · _simulated_seller", "check": "format",
     "names": ["archetype_prompt", "seller_name", "company", "category", "city", "speaks", "archetype_note", "concern"],
     "default": _const("conversation", "SIM_SELLER_SYSTEM"), "applies": NEXT_TURN},
    *[{"id": f"seller.archetype.{stem}", "group": "Seller call · simulated seller", "title": f"Training prompt: {title}",
       "used": f"{{archetype_prompt}} of the simulated seller for {who} (first 2,600 characters are sent).",
       "label": "sim-seller", "where": f"docs/inputs/sales-training-prompts/{file}", "check": "plain",
       "default": _file(TRAINING / file), "applies": NEXT_TURN, "limit": 2600}
      for stem, file, title, who in (("pricesensitive", "pricesensitive.txt", "price-sensitive seller", "price-sensitive sellers"),
                                     ("curious", "curious.txt", "curious seller", "curious and interested sellers"),
                                     ("confused", "confused.txt", "confused seller", "confused sellers"),
                                     ("frustrated", "frustrated.txt", "frustrated seller", "frustrated sellers"),
                                     ("uninterested", "uninterested.txt", "uninterested seller", "uninterested sellers"),
                                     ("regional", "regional.txt", "regional-language seller", "language-switch sellers"),
                                     ("voice_agent_clean", "voice-agent-clean.md", "busy / already-served seller",
                                      "rushed sellers, sellers with an executive, and any other archetype"))],
    *[{"id": f"seller.archetype_note.{k}", "group": "Seller call · simulated seller", "title": f"Behaviour note: {k.replace('_', ' ')}",
       "used": f"{{archetype_note}} of the simulated seller when the archetype is {k.replace('_', ' ')}.", "label": "sim-seller",
       "where": "backend/genome/conversation.py · ARCHETYPE_NOTE", "check": "plain", "default": _note(k), "applies": NEXT_TURN}
      for k in ("rushed", "already_with_executive", "language_switch", "interested")],
    # Persona pipeline
    {"id": "persona.generator", "group": "Persona", "title": "Persona Generator v0.3",
     "used": "Each preparation run (Open persona, Barge in, Talk as Seller): turns the minimised API input into the persona JSON. "
             "The ## Input section goes into the user message; HTML comments are not sent. The system part must stay under "
             "16,000 characters (the gateway's limit).", "label": "persona-generator / persona-generator-repair",
     "where": "config/genome/prompts/persona_generator_v0.3.md", "check": "persona_generator",
     "default": _file(ROOT / "config" / "genome" / "prompts" / "persona_generator_v0.3.md"), "applies": "On the next preparation run."},
    {"id": "persona.aggregator", "group": "Persona", "title": "Persona analyst (rules overlay)",
     "used": "Each preparation run: builds factors, tags, insights and the teleprompter from the numbered evidence. FACTORS_HERE is "
             "replaced with the factor list.", "label": "aggregate", "where": "backend/genome/aggregator.py · SYSTEM_PROMPT",
     "check": "token", "token": "FACTORS_HERE", "default": _const("aggregator", "SYSTEM_PROMPT"), "applies": "On the next preparation run."},
    {"id": "persona.locality", "group": "Persona", "title": "Locality from addresses",
     "used": "Each preparation run with addresses: picks the locality a field executive should visit (cached per address set).",
     "label": "locality", "where": "backend/genome/orchestrator.py · _locality", "check": "plain",
     "default": _const("orchestrator", "LOCALITY_PROMPT"), "applies": "On the next run with new addresses."},
    # Buyer side — built-in VANI
    {"id": "buyer.requirement", "group": "Buyer call · built-in VANI", "title": "VANI requirement policy (Talk as Buyer)",
     "used": "Every VANI turn on Talk as Buyer: the owner's VANI Buyer Requirement prompt, structured. Code still enforces the ask "
             "limits, product change and closings.", "label": "buyer-requirement", "where": "backend/genome/buyer.py · REQUIREMENT_POLICY",
     "check": "format", "names": ["product", "seller", "specs", "units"], "default": _const("buyer", "REQUIREMENT_POLICY"), "applies": NEXT_TURN},
    {"id": "buyer.compact", "group": "Buyer call · built-in VANI", "title": "VANI compact policy (buyer Barge in)",
     "used": "VANI turns on buyer tiles with the compact policy (the default for Barge in).", "label": "buyer-compact",
     "where": "backend/genome/buyer.py · COMPACT_POLICY", "check": "format", "names": ["bot", "units", "specs"],
     "default": _const("buyer", "COMPACT_POLICY"), "applies": NEXT_TURN},
    {"id": "buyer.original", "group": "Buyer call · built-in VANI", "title": "VANI original IndiaMART prompt",
     "used": "VANI turns with the original policy: the lines of the flow being simulated are rendered with the call's variables.",
     "label": "buyer-original", "where": "BUYER_ORIGINAL_PROMPT_PATH (owner's file, not in Git)", "check": "plain",
     "default": _env_file("BUYER_ORIGINAL_PROMPT_PATH"), "applies": NEXT_TURN, "allow_empty": True},
    {"id": "buyer.rewrite", "group": "Buyer call · built-in VANI", "title": "VANI reply rewrite",
     "used": "Only when VANI's reply breaks a rule code checks (wrong language after a switch, more than two options): one rewrite.",
     "label": "buyer-rewrite", "where": "backend/genome/buyer.py · REWRITE_PROMPT", "check": "format", "names": ["fixes"],
     "default": _const("buyer", "REWRITE_PROMPT"), "applies": NEXT_TURN},
    # Sarvam Voice Agents (built through the MCP)
    {"id": "sarvam.payal.prompt", "group": "Sarvam voice agents", "title": "Payal on Sarvam: agent prompt",
     "used": "The prompt of the Payal agent on Sarvam Voice Agents (Live voice on the call page, Payal text chat). Pushed by the "
             "Build; Sarvam's rules apply: seven ## sections, no ###, a space after a tool name, only known {{variables}}.",
     "label": "Sarvam agent (Payal)", "where": "backend/genome/sarvam_voice.py · PROMPT", "check": "sarvam_prompt", "agent": "sarvam_voice",
     "default": _const("sarvam_voice", "PROMPT"), "applies": NOW_BUILD_PAYAL, "build": "/api/v5/admin/sarvam-voice/build"},
    {"id": "sarvam.payal.intro", "group": "Sarvam voice agents", "title": "Payal on Sarvam: opening line",
     "used": "Payal's first sentence on Sarvam, and the opening shown in Payal text chat.", "label": "Sarvam agent (Payal)",
     "where": "backend/genome/sarvam_voice.py · INTRO", "check": "sarvam_intro", "agent": "sarvam_voice",
     "default": _const("sarvam_voice", "INTRO"), "applies": NOW_BUILD_PAYAL + " Text-chat openings change at once.",
     "build": "/api/v5/admin/sarvam-voice/build"},
    {"id": "sarvam.vani.prompt", "group": "Sarvam voice agents", "title": "VANI on Sarvam: agent prompt",
     "used": "The prompt of the VANI buyer agent on Sarvam Voice Agents (Talk as Buyer → VANI on Sarvam).",
     "label": "Sarvam agent (VANI)", "where": "backend/genome/sarvam_buyer.py · PROMPT", "check": "sarvam_prompt", "agent": "sarvam_buyer",
     "default": _const("sarvam_buyer", "PROMPT"), "applies": NOW_BUILD_VANI, "build": "/api/v5/admin/sarvam-buyer/build"},
    {"id": "sarvam.vani.intro", "group": "Sarvam voice agents", "title": "VANI on Sarvam: opening line",
     "used": "VANI's first sentence on Sarvam, and the opening shown in VANI text chat.", "label": "Sarvam agent (VANI)",
     "where": "backend/genome/sarvam_buyer.py · INTRO", "check": "sarvam_intro", "agent": "sarvam_buyer",
     "default": _const("sarvam_buyer", "INTRO"), "applies": NOW_BUILD_VANI + " Text-chat openings change at once.",
     "build": "/api/v5/admin/sarvam-buyer/build"},
    {"id": "sarvam.manjula_v2.prompt", "group": "Sarvam voice agents", "title": "Manjula Executive v2 on Sarvam: agent prompt",
     "used": "The prompt of Manjula Executive v2 (5.12): IndiaMART knowledge, payment first then a persistent meeting ask, competitors, "
             "market, buyers and product expertise. Built by SellerGenome as a copy of the PM's Manjula Excutive voice; her agent is untouched.",
     "label": "Sarvam agent (Manjula v2)", "where": "backend/genome/sarvam_agents.py · PROMPT", "check": "sarvam_prompt", "agent": "sarvam_agents",
     "default": _const("sarvam_agents", "PROMPT"), "applies": "On the next Build of Manjula Executive v2 (button below).",
     "build": "/api/v5/admin/sarvam-agents/manjula-v2/build"},
    {"id": "sarvam.manjula_v2.intro", "group": "Sarvam voice agents", "title": "Manjula Executive v2 on Sarvam: opening line",
     "used": "Manjula Executive v2's first sentence.", "label": "Sarvam agent (Manjula v2)", "where": "backend/genome/sarvam_agents.py · INTRO",
     "check": "sarvam_intro", "agent": "sarvam_agents", "default": _const("sarvam_agents", "INTRO"),
     "applies": "On the next Build of Manjula Executive v2.", "build": "/api/v5/admin/sarvam-agents/manjula-v2/build"},
    # Tools
    {"id": "tools.llm_test", "group": "Tools", "title": "Sarvam tools: LLM test",
     "used": "Admin/tools playground → LLM complete: a quick check that the Sarvam model answers JSON.", "label": "llm-complete",
     "where": "backend/genome/api.py · sarvam_tools_llm_complete", "check": "plain", "default": _const("api", "TOOLS_LLM_PROMPT"),
     "applies": NEXT_TURN},
]
BY_ID = {e["id"]: e for e in CATALOG}
SARVAM_HEADINGS = ["Persona", "Environment & Situation", "Objective", "Speaking style rules", "Facts", "Conversation guidelines", "Guardrails"]


def default(prompt_id: str) -> str:
    return BY_ID[prompt_id]["default"]() or ""


def override(prompt_id: str) -> dict | None:
    if _store is None:
        return None
    doc = _store.get(KIND, prompt_id)
    return doc if doc and doc.get("text") is not None else None


def text(prompt_id: str) -> str:
    """The prompt a call site sends: the Admin edit if there is one, else the default in code."""
    o = override(prompt_id)
    t = o["text"] if o else default(prompt_id)
    limit = BY_ID[prompt_id].get("limit")
    return t[:limit] if limit else t


def problems(prompt_id: str, value: str) -> list[str]:
    """Why an edit would break its call; empty when it is safe to save."""
    e = BY_ID[prompt_id]
    out: list[str] = []
    if not value.strip() and not e.get("allow_empty"):
        return ["The prompt is empty."]
    if len(value) > 60000:
        out.append("Longer than 60,000 characters.")
    check = e["check"]
    if check == "format":
        try:
            value.format(**{n: "x" for n in e["names"]})
        except (KeyError, IndexError) as error:
            out.append(f"Unknown placeholder {error} — allowed: " + ", ".join("{" + n + "}" for n in e["names"]) + ". Write literal braces as {{ and }}.")
        except ValueError as error:
            out.append(f"Braces don't parse ({error}). Write literal braces as {{{{ and }}}}.")
    elif check == "token" and e["token"] not in value:
        out.append(f"{e['token']} must stay in the text (it is replaced with the factor list).")
    elif check == "persona_generator":
        from backend.genome import persona_generator as pg
        body = re.sub(r"<!--.*?-->\s*", "", value, flags=re.DOTALL)
        start, end = body.find("## Input"), body.find("## Output")
        system = (body[:start] + body[end:]).strip() if -1 < start < end else body
        if len(system) > pg.SYSTEM_LIMIT - 400:
            out.append(f"The system part is {len(system):,} characters; the gateway accepts {pg.SYSTEM_LIMIT:,} including the input guide.")
        if "## Output" not in body:
            out.append("Keep the ## Output section — the persona JSON is checked against it.")
    elif check in ("sarvam_prompt", "sarvam_intro"):
        mod = importlib.import_module(f"backend.genome.{e['agent']}")
        unknown = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", value)) - set(mod.INPUTS))
        if unknown:
            out.append("Unknown {{variables}}: " + ", ".join(unknown) + ". Available: " + ", ".join(sorted(mod.INPUTS)) + ".")
        if check == "sarvam_prompt":
            heads = re.findall(r"^## (.+?)\s*$", value, flags=re.M)
            if heads != SARVAM_HEADINGS:
                out.append("Sarvam needs these ## sections in this order: " + ", ".join(SARVAM_HEADINGS) + ".")
            if "###" in value:
                out.append("No ### headings (Sarvam's playbook).")
            if re.search(r"tool:\w+[.,;:!?]", value):
                out.append("Put a space between a tool name and the punctuation after it (Sarvam refused \"tool:end_interaction.\").")
            if len(value) >= 16000:
                out.append("Sarvam prompts must stay under 16,000 characters.")
        elif len(value.split()) > 40:
            out.append("Keep the opening line under 40 words.")
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(prompt_id: str, value: str, by: str = "admin") -> dict:
    if prompt_id not in BY_ID:
        raise KeyError(prompt_id)
    issues = problems(prompt_id, value)
    if issues:
        raise ValueError(" ".join(issues))
    doc = (_store.get(KIND, prompt_id) if _store else None) or {"id": prompt_id, "history": []}
    if doc.get("text") is not None:
        doc["history"] = ([{"text": doc["text"], "at": doc.get("at"), "by": doc.get("by")}] + doc.get("history", []))[:HISTORY]
    doc.update({"text": None if value == default(prompt_id) else value, "at": _now(), "by": by})
    _store.put(KIND, prompt_id, doc)
    return item(prompt_id)


def reset(prompt_id: str, by: str = "admin") -> dict:
    if prompt_id not in BY_ID:
        raise KeyError(prompt_id)
    doc = _store.get(KIND, prompt_id) or {"id": prompt_id, "history": []}
    if doc.get("text") is not None:
        doc["history"] = ([{"text": doc["text"], "at": doc.get("at"), "by": doc.get("by")}] + doc.get("history", []))[:HISTORY]
    doc.update({"text": None, "at": _now(), "by": by})
    _store.put(KIND, prompt_id, doc)
    return item(prompt_id)


def item(prompt_id: str, full: bool = True) -> dict:
    e = BY_ID[prompt_id]
    o = override(prompt_id)
    doc = (_store.get(KIND, prompt_id) if _store else None) or {}
    current = text(prompt_id) if not e.get("limit") else (o["text"] if o else default(prompt_id))
    out = {k: e.get(k) for k in ("id", "group", "title", "used", "label", "where", "applies", "names", "token", "build", "limit")}
    out.update({"edited": bool(o), "edited_at": doc.get("at") if o else None, "edited_by": doc.get("by") if o else None,
                "chars": len(current), "history": [{"at": h.get("at"), "by": h.get("by"), "chars": len(h.get("text") or "")} for h in doc.get("history", [])]})
    if full:
        out.update({"text": current, "default": default(prompt_id)})
    return out


def items() -> list[dict]:
    return [item(e["id"]) for e in CATALOG]
