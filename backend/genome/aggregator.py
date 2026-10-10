"""Persona aggregation with Sarvam (owner points 10–12; PS04 persona spec).

Input: the assembled production contract → a compact, PII-free evidence list where every item has
an id. Output: summary, tags with scores, the PS04 persona factors (voice, language, tone, plan),
LLM-proposed *novel* factors and teleprompter hints — every element citing evidence ids that we
map back to (api, JSON pointer, origin) so the UI can open the exact source.

If Sarvam is unavailable or the budget is spent, a deterministic rules persona is produced and
labelled ``origin: rules`` — never presented as model output.
"""

from __future__ import annotations

import json
import re
from typing import Any

STANDARD_FACTORS = {
    "primary_objection": "price | time | trust | confusion | lead_quality | existing_executive | not_interested | none",
    "buying_stage": "exploring | evaluating | ready | not_now",
    "language": "BCP-47 code the seller should be spoken to in",
    "hinglish_mix": "none | low | medium | high — how much English to mix into Hindi",
    "formality": "formal | respectful | casual",
    "warmth": "low | medium | high",
    "empathy": "low | medium | high",
    "pace": "number 0.8–1.25 (1.0 = normal speech rate)",
    "voice": "speaker name from the allowed list",
    "sentence_length": "short | medium",
    "opening": "how to open this call (one sentence, not the script)",
    "personalization": "list of 2–4 hooks tied to evidence",
    "objection_playbook": "list of {objection, approach} — never quote prices",
    "next_action": "the single best next step (usually a meeting type)",
    "escalation": "list of rules for handing off or ending the call",
    "best_call_window": "when to call, if evidence says",
}
KEYWORD_FACTORS = {"language", "hinglish_mix", "formality", "warmth", "empathy", "voice", "sentence_length",
                   "primary_objection", "buying_stage"}
ENUMS = {
    "hinglish_mix": {"none", "low", "medium", "high"}, "formality": {"formal", "respectful", "casual"},
    "warmth": {"low", "medium", "high"}, "empathy": {"low", "medium", "high"}, "sentence_length": {"short", "medium"},
    "primary_objection": {"price", "time", "trust", "confusion", "lead_quality", "existing_executive", "not_interested", "none"},
    "buying_stage": {"exploring", "evaluating", "ready", "not_now"},
}
STRENGTHS = {"strong", "weak", "guess", "default"}

SYSTEM_PROMPT = """You are SellerGenome's persona analyst for IndiaMART's seller-calling voice assistant (VANI).
Build the persona VANI should use when it calls this seller. Personas change DELIVERY only — never facts, prices,
policies or promises. You must ground every choice in the numbered evidence you are given.

Return ONE JSON object with exactly these keys:
{
 "summary": "2-3 sentences: who this seller is, what they want now, what to be careful about",
 "archetype": "one of: price_sensitive, curious, rushed, confused, frustrated, interested, uninterested, already_with_executive, language_switch, balanced",
 "tags": [{"tag": "short-kebab-case", "score": 0-100, "reason": "<=120 chars", "evidence": ["ids"]}],   // 4-7 tags
 "factors": {
   "<factor>": {"value": ..., "score": 0-100, "strength": "strong|weak|guess", "reason": "<=160 chars", "evidence": ["ids"]}
 },
 "novel_factors": [{"name": "snake_case", "value": "short value", "value_type": "keyword|number|text", "score": 0-100, "reason": "<=120 chars", "evidence": ["ids"]}],  // 0-5 facts about THIS seller not covered by the factors
 "teleprompter": {"opening_hook": "one natural line in the seller's language/style referencing the trigger", "first_question": "...", "confirm_first": ["doubts to clear early"], "avoid": ["things not to say"]},
 "headline": "3-6 words on HOW to talk to this seller, e.g. 'Patient, slow-paced, Hindi' or 'Brief, formal, never pushy'",
 "insights": [{"group": "intent|decision|objections|reachability|buyers|profile", "text": "<=110 chars, one plain fact about THIS seller", "confidence": "high|medium|guess", "evidence": ["ids"]}]  // 4-10 insights, at most 3 per group, most useful first
}

Factors to fill (all of them, EVERY one as an object like {"value": "short", "score": 70, "strength": "weak", "reason": "why", "evidence": ["i:objection_or_blocker"]}): FACTORS_HERE
Rules:
- "evidence" must list ids from the evidence list only. No evidence → strength "guess" and score <= 40.
- strength "strong" only when the evidence states it directly (e.g. seller asked for a language).
- language: do NOT infer language from city or state; use explicit evidence, else keep the profile language as a guess.
- Hindi speakers who use English words → hinglish_mix medium/high. Romanised text is fine.
- Never invent prices, discounts, ROI or guarantees. Package names are allowed; prices are not.
- If a doubt exists, put it in teleprompter.confirm_first.
- insights: what an executive should know before calling — intent (what they want / did), decision (who decides, how soon),
  objections (what stopped them), reachability (do they pick up, when), buyers (how they treat their own buyers: replies,
  pickups), profile (business facts that matter). "high" only when the evidence states it; facts only, no advice.
- Bot or buyer statements are not seller preferences.
- Keep every string short. JSON only."""


def build_prompt(seller: dict, evidence: list[dict], allowed_speakers: list[str], languages: list[str]) -> tuple[str, str]:
    factors = "; ".join(f"{k} ({v})" for k, v in STANDARD_FACTORS.items())
    from backend.genome import prompts  # Admin → Prompts can edit SYSTEM_PROMPT
    system = prompts.text("persona.aggregator").replace("FACTORS_HERE", factors)
    user = json.dumps({
        "seller": {"glid": seller["glid"], "company": seller.get("company_name"), "city": seller.get("city"),
                   "profile_language": seller.get("language"), "business": seller.get("business_nature"),
                   "trigger_activity": (seller.get("trigger") or {}).get("activity")},
        "allowed_voices": allowed_speakers, "allowed_languages": languages,
        "evidence": [{"id": e["id"], "text": e["text"]} for e in evidence],
    }, ensure_ascii=False)
    return system, user


def _sources(ids: Any, index: dict[str, dict]) -> list[dict]:
    out = []
    for i in ids if isinstance(ids, list) else []:
        item = index.get(str(i))
        if item:
            out.append({"id": item["id"], "api": item["api"], "pointer": item["pointer"], "origin": item["origin"], "text": item["text"][:200],
                        "label": item.get("label", ""), "at": item.get("at")})
    return out[:6]


INSIGHT_GROUPS = ("intent", "decision", "objections", "reachability", "buyers", "profile")
CONFIDENCE = {"high": "high", "medium": "medium", "guess": "guess", "strong": "high", "weak": "medium", "low": "guess"}


def validate_insights(raw: Any, index: dict[str, dict]) -> list[dict]:
    """The PM's grouped insights: kept only in known groups, ≤3 per group; uncited ones become guesses."""
    out, per = [], {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        group = str(item.get("group", "")).lower().strip()
        if group not in INSIGHT_GROUPS or per.get(group, 0) >= 3:
            continue
        sources = _sources(item.get("evidence"), index)
        confidence = CONFIDENCE.get(str(item.get("confidence", "")).lower(), "medium")
        if not sources:
            confidence = "guess"
        per[group] = per.get(group, 0) + 1
        out.append({"group": group, "text": str(item["text"]).strip()[:140], "confidence": confidence, "sources": sources})
    return out[:10]


def _score(value: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def validate(raw: dict, evidence: list[dict], settings, seller: dict) -> dict:
    """Keep only well-formed, cited content; clamp numbers; normalise names. Raises on unusable output."""
    index = {e["id"]: e for e in evidence}
    if not isinstance(raw, dict) or not isinstance(raw.get("factors"), dict):
        raise ValueError("Model output has no factors.")
    speakers = settings.get("persona.speakers", [])
    languages = settings.get("persona.languages", [])
    pmin, pmax = settings.get("persona.pace_min", 0.8), settings.get("persona.pace_max", 1.25)
    factors: dict[str, dict] = {}
    for name in STANDARD_FACTORS:
        f = raw["factors"].get(name)
        if f is not None and not isinstance(f, dict):
            # Model returned a bare value: keep it, but it has no citation → "guess".
            f = {"value": f, "score": 40, "strength": "guess", "reason": "Model gave no evidence for this choice.", "evidence": []}
        if not isinstance(f, dict) or f.get("value") in (None, "", []):
            continue
        value = f["value"]
        if name == "pace":
            try:
                value = round(max(pmin, min(pmax, float(value))), 2)
            except (TypeError, ValueError):
                continue
        elif name == "language" and value not in languages:
            continue
        elif name == "voice" and value not in speakers:
            continue
        elif name in ENUMS and str(value).lower() not in ENUMS[name]:
            continue
        elif name in ENUMS:
            value = str(value).lower()
        if isinstance(value, str):
            value = value[:300]
        if isinstance(value, list):
            value = [v if isinstance(v, (str, dict)) else str(v) for v in value][:6]
        sources = _sources(f.get("evidence"), index)
        strength = f.get("strength") if f.get("strength") in STRENGTHS else "weak"
        score = _score(f.get("score"))
        if not sources:
            strength, score = "guess", min(score, 40)
        if strength == "strong" and len(sources) == 0:
            strength = "weak"
        factors[name] = {"value": value, "score": score, "strength": strength,
                         "reason": str(f.get("reason", ""))[:200], "sources": sources}
    tags = []
    for t in (raw.get("tags") or [])[:8]:
        if not isinstance(t, dict) or not t.get("tag"):
            continue
        tag = re.sub(r"[^a-z0-9-]+", "-", str(t["tag"]).lower()).strip("-")[:40]
        sources = _sources(t.get("evidence"), index)
        tags.append({"tag": tag, "score": _score(t.get("score")) if sources else min(_score(t.get("score")), 40),
                     "reason": str(t.get("reason", ""))[:160], "sources": sources})
    novel = []
    for n in (raw.get("novel_factors") or [])[:5]:
        if not isinstance(n, dict) or not n.get("name") or n.get("value") in (None, ""):
            continue
        name = re.sub(r"[^a-z0-9_]+", "_", str(n["name"]).lower()).strip("_")[:48]
        if not name or name in STANDARD_FACTORS:
            continue
        sources = _sources(n.get("evidence"), index)
        if not sources:
            continue  # a novel claim with no evidence is dropped, not stored
        value_type = n.get("value_type") if n.get("value_type") in ("keyword", "number", "text") else "keyword"
        novel.append({"name": name, "value": str(n["value"])[:200], "value_type": value_type,
                      "score": _score(n.get("score")), "reason": str(n.get("reason", ""))[:160], "sources": sources})
    tele = raw.get("teleprompter") if isinstance(raw.get("teleprompter"), dict) else {}
    archetype = str(raw.get("archetype") or seller.get("archetype") or "balanced")[:40]
    if len(factors) < 6:
        raise ValueError("Too few valid factors in model output.")
    return {
        "summary": str(raw.get("summary", ""))[:700], "archetype": archetype, "tags": tags, "factors": factors,
        "novel_factors": novel, "headline": str(raw.get("headline", ""))[:60].strip(),
        "insights": validate_insights(raw.get("insights"), index),
        "teleprompter": {"opening_hook": str(tele.get("opening_hook", ""))[:300],
                         "first_question": str(tele.get("first_question", ""))[:300],
                         "confirm_first": [str(x)[:200] for x in (tele.get("confirm_first") or [])][:3],
                         "avoid": [str(x)[:160] for x in (tele.get("avoid") or [])][:4]},
    }


# --- deterministic fallback -----------------------------------------------------------------------

RULES = {
    "price_sensitive": dict(formality="respectful", warmth="high", empathy="medium", pace=0.95, sentence_length="short",
                            primary_objection="price", buying_stage="evaluating",
                            opening="Acknowledge the earlier cost concern, then ask permission for two minutes",
                            next_action="Offer an in-person meeting to compare plans — no price on the call"),
    "curious": dict(formality="respectful", warmth="high", empathy="medium", pace=0.95, sentence_length="medium",
                    primary_objection="none", buying_stage="exploring",
                    opening="Thank them for exploring and offer to answer their questions",
                    next_action="Offer a demo meeting with an executive"),
    "rushed": dict(formality="respectful", warmth="medium", empathy="low", pace=1.15, sentence_length="short",
                   primary_objection="time", buying_stage="evaluating",
                   opening="State the purpose in one line and ask if two minutes now is fine",
                   next_action="Book a 15-minute slot at their preferred time"),
    "confused": dict(formality="respectful", warmth="high", empathy="high", pace=0.85, sentence_length="short",
                     primary_objection="confusion", buying_stage="exploring",
                     opening="Greet slowly, confirm language, then explain one thing at a time",
                     next_action="Offer an explanation meeting in their language"),
    "frustrated": dict(formality="formal", warmth="medium", empathy="high", pace=0.9, sentence_length="short",
                       primary_objection="lead_quality", buying_stage="not_now",
                       opening="Acknowledge the complaint first and do not pitch",
                       next_action="Escalate the complaint to a human executive"),
    "interested": dict(formality="respectful", warmth="high", empathy="medium", pace=1.0, sentence_length="medium",
                       primary_objection="none", buying_stage="ready",
                       opening="Reference their interest and move to the next step",
                       next_action="Book the meeting now"),
    "uninterested": dict(formality="respectful", warmth="medium", empathy="medium", pace=1.0, sentence_length="short",
                         primary_objection="not_interested", buying_stage="not_now",
                         opening="Respect their time; one useful fact, then ask permission",
                         next_action="Offer a callback later; stop if they decline"),
    "already_with_executive": dict(formality="formal", warmth="medium", empathy="medium", pace=1.0, sentence_length="short",
                                   primary_objection="existing_executive", buying_stage="evaluating",
                                   opening="Acknowledge their account manager and do not pitch",
                                   next_action="Hand off to the existing executive"),
    "language_switch": dict(formality="respectful", warmth="high", empathy="medium", pace=1.0, sentence_length="short",
                            primary_objection="none", buying_stage="exploring",
                            opening="Open in English, offer Hindi immediately",
                            next_action="Book a visit meeting"),
}


def rules_persona(seller: dict, assembled: dict, evidence: list[dict], settings) -> dict:
    archetype = seller.get("archetype", "curious") if seller.get("archetype") in RULES else "curious"
    rule = RULES[archetype]
    ev = {e["id"]: e for e in evidence}
    cite = lambda *ids: [{"id": e["id"], "api": e["api"], "pointer": e["pointer"], "origin": e["origin"], "text": e["text"][:200]}
                         for e in (ev.get(i) for i in ids) if e]
    language = seller.get("language", "hi-IN")
    factors = {
        "language": {"value": language, "score": 45, "strength": "guess", "reason": "Profile language; confirm on the call.", "sources": []},
        "hinglish_mix": {"value": "high" if language == "hi-IN" else "low", "score": 40, "strength": "guess", "reason": "Default for this language.", "sources": []},
        "voice": {"value": seller.get("speaker", "ritu"), "score": 40, "strength": "guess", "reason": "Archetype default voice.", "sources": []},
    }
    for k in ("formality", "warmth", "empathy", "pace", "sentence_length", "primary_objection", "buying_stage", "opening", "next_action"):
        factors[k] = {"value": rule[k], "score": 55, "strength": "weak", "reason": f"Rule for {archetype.replace('_', ' ')} sellers.",
                      "sources": cite("i:objection_or_blocker", "i:last_interaction")}
    factors["personalization"] = {"value": [i["headline"] for i in assembled["insights"][:3] if i.get("headline")], "score": 50,
                                  "strength": "weak", "reason": "Upstream insights.", "sources": cite("i:purchase_intent", "i:market_opportunity")}
    factors["objection_playbook"] = {"value": [{"objection": rule["primary_objection"], "approach": "Acknowledge, ask what matters most, offer the meeting"}],
                                     "score": 50, "strength": "weak", "reason": "Archetype playbook.", "sources": cite("i:objection_or_blocker")}
    factors["escalation"] = {"value": ["Do-not-call or stop request → end immediately", "Complaint or refund → human executive",
                                       "Asked if talking to a bot → confirm virtual assistant"], "score": 80, "strength": "strong",
                             "reason": "Policy.", "sources": []}
    return {
        "summary": f"{seller.get('company_name')} in {seller.get('city')}: {(assembled['insights'][0]['headline'] if assembled['insights'] else 'no upstream insights')}",
        "archetype": archetype,
        "tags": [{"tag": archetype.replace("_", "-"), "score": 55, "reason": "Rules archetype", "sources": cite("i:objection_or_blocker")}],
        "factors": factors, "novel_factors": [],
        "teleprompter": {"opening_hook": "", "first_question": "", "confirm_first": [d["doubt"][:200] for d in assembled["doubts"]][:2], "avoid": ["Quoting any price"]},
    }
