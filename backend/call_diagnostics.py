"""Reproducible missed-meeting candidates, never causal or acoustic judgments.

The rules inspect machine summaries and supplied role-labelled turns. Exact spans
are retained privately. A matched word is a review candidate, not ground truth.
"""

import hashlib
import re
from collections import defaultdict

from backend.insights import EMPTY

VERSION = "call-diagnostics-1.3"
# Order is a review priority only, not a probability or causal ranking.
RULES = [
    {
        "id": "contact_stop",
        "label": "Stop/contact refusal",
        "pattern": r"\b(do not call|don't call|stop calling|remove (?:my|the) number|dobara (?:phone|call) mat|call mat)\b",
        "action": "Confirm and honor contact restrictions; do not rebut a stop request.",
    },
    {
        "id": "language",
        "label": "Language/understanding barrier",
        "pattern": r"\b(language barrier|(?:could not|couldn't|did not|didn't|unable to) understand|(?:spoke|speaking|responded) (?:only )?in (?:Gujarati|Telugu|Tamil|Kannada|Bengali|Marathi)|hindi mein|english mein|samajh nahi|samajh nahin)\b",
        "action": "Confirm the preferred language and use a supported speaker; ask one short confirmation.",
    },
    {
        "id": "identity",
        "label": "Identity/authority unresolved",
        "pattern": r"\b(wrong (?:number|person)|not the (?:owner|right person)|unable to confirm|not able to confirm|could not confirm|owner (?:was |is )?(?:not available|unavailable)|galat number|malik nahi)\b",
        "action": "Confirm business and decision-maker identity before a package pitch; do not assume the recipient is the seller.",
    },
    {
        "id": "service",
        "label": "Existing service problem",
        "pattern": r"\b(refund|complaint|unresolved|service issue|poor (?:service|quality)|no (?:leads|enquiries)|leads nahi|service kharab)\b",
        "action": "Acknowledge the issue, capture a concrete example and route an accountable follow-up before selling.",
    },
    {
        "id": "timing",
        "label": "Busy/callback timing",
        "pattern": r"\b(busy|unavailable|call (?:back|later)|callback|reschedul\w*|asked (?:the agent )?to wait|baad me|baad mein|abhi time nahi|time nahi hai)\b",
        "action": "Ask permission for a short call or agree a specific callback window; preserve the seller's answer.",
    },
    {
        "id": "price",
        "label": "Price/value question",
        "pattern": r"\b(pric(?:e|ing)|cost|budget|payment|expensive|charges|mehenga|mahanga|paisa|paise|free chahiye)\b",
        "action": "Ask which outcome matters and explain only catalog-backed benefits and prices; do not invent ROI.",
    },
    {
        "id": "continuity",
        "label": "Existing executive/meeting",
        "pattern": r"\b(already (?:in (?:touch|contact)|met|spoke|scheduled)|existing executive|executive (?:had |has )?(?:called|visited)|pehle baat|meeting ho gayi)\b",
        "action": "Confirm the prior executive and agreed next action; avoid duplicate meeting requests.",
    },
    {
        "id": "declined",
        "label": "Interest or need declined",
        "pattern": r"\b(not interested|no interest|not required|no (?:need|requirement)|refused|declined|not looking|nahi chahiye|nahin chahiye|zarurat nahi|interest nahi)\b",
        "action": "Clarify once if welcome, then respect the refusal; do not reinterpret silence as interest.",
    },
    {
        "id": "clarity",
        "label": "Explanation or information needed",
        "pattern": r"\b(explain|clarif\w*|confus\w*|more (?:details|information)|asked (?:about|for)\s+(?:more\s+)?(?:details|information|explanations?|clarification|features|benefits|packages?|plans?|pricing|prices?|costs?|charges)|samjha|bataiye|kya fayda)\b",
        "action": "Answer one specific question in plain language, then check understanding before asking for a meeting.",
    },
    {
        "id": "no_response",
        "label": "No usable seller response",
        "pattern": r"\b(no response|without (?:any |a )?(?:further )?response|did not respond|didn't respond|no (?:further )?(?:conversation|discussion)|before (?:the )?(?:lead|seller) could respond|immediately after (?:the )?(?:greeting|introduction))\b",
        "action": "Review audio and connection evidence; there may be no actual sales conversation to rebut.",
    },
    {
        "id": "early_end",
        "label": "Conversation ended early",
        "pattern": r"\b(disconnect\w*|ended (?:abruptly|immediately)|cut (?:short|off)|hung up|hang up|transcript ends|call (?:ended|dropped))\b",
        "action": "Inspect the ending and turn coverage before attributing rejection; offer a consented retry only when appropriate.",
    },
    {
        "id": "unconfirmed",
        "label": "No clear next step recorded",
        "pattern": r"\b(no (?:clear |definitive )?outcome|without (?:a )?(?:clear )?(?:outcome|confirmation)|not confirmed|no meeting|meeting (?:was )?not (?:fixed|confirmed|scheduled))\b",
        "action": "Ask one explicit next-step question; distinguish willingness, booked time and completed meeting.",
    },
]
RULE_BY_ID = {item["id"]: item for item in RULES}
NEED_IDS = [
    "timing",
    "clarity",
    "price",
    "service",
    "continuity",
    "language",
    "identity",
    "contact_stop",
    "declined",
]
PERSONAS = {
    "timing": "Time-constrained / callback first",
    "clarity": "Needs a concrete explanation",
    "price": "Value and price questions",
    "service": "Service recovery first",
    "continuity": "Relationship continuity",
    "language": "Language confirmation first",
    "identity": "Identity confirmation first",
    "contact_stop": "Contact boundaries first",
    "declined": "Respect a stated lack of need",
    "unclassified": "No supported need detected — confirm first",
}
RESPONSES = [
    (
        "respect_stop",
        "Stop acknowledgement wording",
        r"\b(won't call|will not call|no further calls|remove.*number|dobara.*nahi)\b",
    ),
    (
        "callback",
        "Callback/time offered",
        r"\b(call (?:back|later)|callback|convenient|suitable time|baad|kab|samay|time)\b",
    ),
    (
        "clarification",
        "Clarification/question",
        r"\b(explain|understand|clarif\w*|which|what|samjh\w*|bata\w*|kaun|kya)\b|\?",
    ),
    (
        "value",
        "Value/benefit explanation",
        r"\b(benefit|business|enquir\w*|lead|visibility|growth|fayda|vyapar|badh\w*)\b",
    ),
    (
        "handoff",
        "Executive/support follow-up",
        r"\b(executive|support|complaint|resolve|manager|team)\b",
    ),
    (
        "meeting",
        "Meeting proposed",
        r"\b(meet\w*|visit|appointment|milna|milne)\b",
    ),
    (
        "acknowledgement",
        "Acknowledgement only",
        r"\b(ok(?:ay)?|sure|thank|understood|theek|thik|ji|achha|accha)\b",
    ),
]


def call_id(attempt: str) -> str:
    """Opaque review key; the actual attempt ID is available only after login."""
    return "review-" + hashlib.sha256(attempt.encode()).hexdigest()[:24]


def matches(text: str, source: str, actor: str) -> list[dict]:
    """Find first non-negated cue per rule; still mark broad semantics unreviewed."""
    results = []
    for rule in RULES:
        for match in re.finditer(rule["pattern"], text, re.I):
            prefix = text[max(0, match.start() - 35) : match.start()]
            # Do not turn "not busy", "no complaint" or "not a language barrier"
            # into positive diagnoses. Explicit refusal rules include their own no.
            if re.search(
                r"\b(?:not|never|no|without)\s+(?:(?:a|any|longer)\s+)?$",
                prefix,
                re.I,
            ):
                continue
            attributed = actor
            if source == "summary":
                sentence = text[
                    max(
                        text.rfind(".", 0, match.start()),
                        text.rfind("\n", 0, match.start()),
                    )
                    + 1 : match.start()
                ]
                actors = re.findall(
                    r"\b(seller|lead|customer|buyer|bot|agent|executive)\b",
                    sentence,
                    re.I,
                )
                if actors:
                    named = actors[-1].lower()
                    attributed = (
                        "seller"
                        if named in {"seller", "lead", "customer"}
                        else "bot"
                        if named in {"bot", "agent"}
                        else named
                    )
            results.append(
                {
                    "reason": rule["id"],
                    "label": rule["label"],
                    "source": source,
                    "actor": attributed,
                    "start": match.start(),
                    "end": match.end(),
                    "matched": match.group(),
                    "strength": "weak",
                    "reviewed": False,
                }
            )
            break
    return results


def clean_turns(rows: list[dict], seller: str) -> tuple[list[dict], dict]:
    """Exact copies collapse; conflicting/invalid/foreign slots break adjacency."""
    slots, rejected = defaultdict(dict), defaultdict(int)
    for row in rows:
        if str(row.get("fk_glusr_usr_id", "")) != seller:
            rejected["identity_mismatch"] += 1
            continue
        try:
            ordinal = int(row["turn_no"])
            start, end = float(row["start_sec"]), float(row["end_sec"])
            if ordinal < 0 or not (0 <= start <= end < float("inf")):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            rejected["invalid_timing_or_ordinal"] += 1
            continue
        signature = (row.get("speaker"), row.get("text"), start, end)
        if signature in slots[ordinal]:
            rejected["exact_duplicate"] += 1
        slots[ordinal][signature] = {
            "turn_no": ordinal,
            "speaker": row.get("speaker", "unknown"),
            "text": row.get("text", ""),
            "start_sec": start,
            "end_sec": end,
            "source_file": row.get("_file"),
            "source_row": row.get("_row"),
        }
    clean = []
    for ordinal, variants in sorted(slots.items()):
        if len(variants) != 1:
            rejected["conflicting_slots"] += 1
            continue
        clean.append(next(iter(variants.values())))
    return clean, dict(rejected)


def evaluate(
    summary: str, turns: list[dict], *, label: str, flag=None
) -> dict:
    """Generate evidence-linked review candidates and first-response opportunities.

    Transcript response measures use an eligible seller cue and the immediately
    next, consecutive bot turn. Missing/gapped/unknown-role data stays unknown.
    Summary candidates alone never establish that a rebuttal happened.
    """
    # Preserve offsets against the original payload, including leading spaces.
    text = summary
    populated = text.strip().casefold() not in EMPTY
    evidence = matches(text, "summary", "mixed_summary") if populated else []
    seller_evidence = []
    episodes = {}
    for index, turn in enumerate(turns):
        if turn["speaker"] != "seller":
            continue
        cues = matches(turn["text"], f"turn:{turn['turn_no']}", "seller")
        seller_evidence.extend(cues)
        for cue in cues:
            reason = cue["reason"]
            if reason not in NEED_IDS or reason in episodes:
                continue
            following = turns[index + 1] if index + 1 < len(turns) else None
            eligible = (
                following
                and following["speaker"] == "bot"
                and following["turn_no"] == turn["turn_no"] + 1
            )
            response = "not_observed"
            if eligible:
                response = next(
                    (
                        key
                        for key, _, pattern in RESPONSES
                        if (key != "respect_stop" or reason == "contact_stop")
                        and re.search(pattern, following["text"], re.I)
                    ),
                    "other_response",
                )
            episodes[reason] = {
                "reason": reason,
                "seller_turn": turn["turn_no"],
                "response_turn": following["turn_no"] if eligible else None,
                "response": response,
                "resolved": "not_established",
                "status": "observed_response"
                if eligible
                else "no_eligible_adjacent_bot_turn",
            }
    evidence.extend(seller_evidence)
    reasons = list(dict.fromkeys(item["reason"] for item in evidence))
    # A bot's package pitch or buyer's budget must not define a seller persona.
    seller_needs = {
        item["reason"] for item in evidence if item["actor"] == "seller"
    }
    needs = [key for key in NEED_IDS if key in seller_needs] or [
        "unclassified"
    ]
    conflict = (label == "Meeting Fixed" and flag is False) or (
        label != "Meeting Fixed" and flag is True
    )
    status = (
        "disputed_outcome"
        if conflict
        else "meeting_claim"
        if label == "Meeting Fixed"
        else "candidate_reasons"
        if reasons
        else "insufficient_evidence"
    )
    return {
        "version": VERSION,
        "method": "deterministic_summary_and_turn_rules",
        "status": status,
        "source_label": label,
        "meeting_flag": flag,
        "meeting_conflict": conflict,
        "summary_available": populated,
        "usable_turns": len(turns),
        "reason_ids": reasons,
        "primary_reason": reasons[0] if reasons else "unknown",
        "persona_ids": needs,
        "evidence": evidence,
        "response_episodes": list(episodes.values()),
        "recommended_next_step": RULE_BY_ID[reasons[0]]["action"]
        if reasons
        else "Review the original audio or collect a clearer next-step confirmation; no specific reason is established.",
        "causal_reason": None,
        "human_reviewed": False,
        "limitations": [
            "General (talked) is a source label, not proof of meaningful conversation or rejection.",
            "Rules are English/Roman-Hindi cue screens, not exhaustive multilingual understanding.",
            "Supplied turn roles are unreviewed; missing response evidence is not proof the bot stayed silent.",
            "A response is not a resolved objection, a held meeting or a sale.",
        ],
    }


DEMOS = {
    "timing": {
        "summary": "The seller said they were busy and asked for a callback. No meeting was confirmed.",
        "turns": [
            {
                "turn_no": 1,
                "speaker": "seller",
                "text": "I am busy. Please call later.",
            },
            {
                "turn_no": 2,
                "speaker": "bot",
                "text": "What time would be convenient for a callback?",
            },
        ],
    },
    "early_end": {
        "summary": "The call ended abruptly after the greeting without any response from the seller.",
        "turns": [],
    },
    "unknown": {"summary": "The seller greeted the agent.", "turns": []},
}


def demo(name: str) -> dict:
    """Named synthetic examples exercise the same evaluator without source access."""
    item = DEMOS[name]
    return {
        "mode": "synthetic_demo",
        "summary": item["summary"],
        "turns": item["turns"],
        "evaluation": evaluate(**item, label="General (talked)", flag=False),
    }
