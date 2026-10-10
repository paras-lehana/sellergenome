"""Voice bot plug-in contract (owner point 9; decision D13).

Any external bot — LiveKit Agents or Sarvam Voice Agents — registers a call, pulls the prepared
persona context, and pushes transcript/signal/switch/tool/outcome events. Its calls then show on the
floor as REAL CALL tiles, and SellerGenome's own signal detection can bump the context revision so
the bot picks up a new delivery at its next turn boundary. Full contract: docs/architecture/voice-plugin.md.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from backend.genome import roster, signals

EVENT_TYPES = {"transcript", "signal", "switch", "mood", "tool", "outcome", "ended"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class VoicePlugin:
    def __init__(self, settings, store) -> None:
        self.settings, self.store = settings, store

    def key_ok(self, supplied: str) -> bool:
        expected = self.settings.secret("VOICE_PLUGIN_KEY")
        return bool(expected) and secrets.compare_digest(expected, supplied or "")

    def register(self, run: dict, provider: str, direction: str, room: str | None) -> dict:
        session = {"id": "vs-" + secrets.token_hex(6), "run_id": run["id"], "seller_id": run["seller_id"], "glid": run["glid"],
                   "provider": provider[:40], "direction": direction, "room": (room or "")[:120], "status": "connected",
                   "created_at": utc_now(), "last_event_epoch": time.time(), "context_revision": 1,
                   "transcript": [], "signals": [], "switches": [], "tools": [], "outcome": None,
                   "base_language": (((run.get("persona") or {}).get("factors") or {}).get("language") or {}).get("value", "hi-IN")}
        self.store.put("voice_sessions", session["id"], session)
        return session

    def context(self, session: dict, run: dict) -> dict:
        persona = run.get("persona") or {}
        f = persona.get("factors", {})
        tele = run.get("teleprompter") or {}
        contract = run.get("contract") or {}
        facts = []
        for name in ("company_name", "city", "products", "last_call_summary", "last_meeting_date", "custtypeweight"):
            field = (contract.get("fields") or {}).get(name)
            if field and field.get("availability") == "present":
                facts.append({"field": name, "value": field["value"], "origin": field["origin"], "retrieved_at": field.get("retrieved_at")})
        for h in (contract.get("history") or [])[:5]:
            facts.append({"field": "history", "value": h.get("summary"), "at": h.get("at"), "origin": h.get("origin")})
        delivery = session.get("delivery_override") or {}
        value = lambda k, d=None: delivery.get(k, (f.get(k) or {}).get("value", d))  # noqa: E731
        return {
            "context_revision": session["context_revision"],
            "seller": {"glid": run["glid"], "company": run["seller"].get("company_name"), "city": run["seller"].get("city")},
            "voice": {"provider_voice": value("voice", "ritu"), "pace": value("pace", 1.0), "pitch": None, "language": value("language", "hi-IN"),
                      "hinglish_mix": value("hinglish_mix", "medium")},
            "delivery": {k: value(k) for k in ("formality", "warmth", "empathy", "sentence_length")},
            "prompt_sections": {
                "identity": f"You are {self.settings.get('app.bot_name', 'Payal')}, IndiaMART's virtual assistant. Never claim to be human; if asked, say you are a virtual assistant.",
                "goal": "Understand the seller's need and book a meeting with a human IndiaMART executive.",
                "seller_facts": facts, "persona": persona.get("summary"),
                "to_confirm": tele.get("confirm_first", []),
                "objection_playbook": (f.get("objection_playbook") or {}).get("value", []),
                "guardrails": ["Never quote prices, discounts, ROI or guarantees", "Do-not-call and stop requests end the call immediately",
                               "Personas change delivery only — never facts or policy"],
            },
            "teleprompter": {k: tele.get(k) for k in ("opening", "next_question", "objection", "handoff")},
            "switch_rules": {k: {kk: vv for kk, vv in v.items() if kk != "reason"} | {"reason": v["reason"]} for k, v in signals.RULES.items()},
            "cooldown": {"seconds": self.settings.get("persona.cooldown_seconds", 8), "turns": self.settings.get("persona.cooldown_turns", 1)},
        }

    def ingest(self, session: dict, events: list[dict]) -> dict:
        accepted = 0
        before = len(session.get("signals") or [])
        for e in events[:50]:
            kind = e.get("type")
            if kind not in EVENT_TYPES:
                continue
            accepted += 1
            stamp = {"at": utc_now(), **{k: v for k, v in e.items() if k in ("speaker", "text", "lang", "t_ms", "signal", "method", "confidence",
                                                                             "factor", "from", "to", "reason", "label", "valence", "arousal",
                                                                             "name", "args", "result_status", "details", "duration_ms")}}
            if kind == "transcript":
                session["transcript"].append(stamp)
                if stamp.get("speaker") in ("seller", "buyer") and stamp.get("text"):
                    current = (session.get("delivery_override") or {}).get("language") or session.get("base_language", "hi-IN")
                    for d in signals.detect(str(stamp["text"]), current):
                        session["signals"].append({**d, "at": stamp["at"], "detected_by": "sellergenome"})
                        if d["signal"] == "language_switch" and d.get("explicit"):
                            session.setdefault("delivery_override", {})["language"] = d["target_language"]
                            session["context_revision"] += 1
            elif kind == "signal":
                session["signals"].append({**stamp, "detected_by": "bot"})
            elif kind == "switch":
                session["switches"].append(stamp)
            elif kind == "tool":
                session["tools"].append(stamp)
            elif kind == "outcome":
                session["outcome"] = stamp
            elif kind == "ended":
                session["status"] = "ended"
        session["last_event_epoch"] = time.time()
        session["transcript"] = session["transcript"][-200:]
        self.store.put("voice_sessions", session["id"], session)
        return {"accepted": accepted, "context_revision": session["context_revision"],
                "new_signals": [{k: x.get(k) for k in ("signal", "explicit", "target_language", "cue", "detected_by")} for x in (session.get("signals") or [])[before:]]}

    def live_tiles(self, direction: str = "seller_outbound") -> list[dict]:
        """REAL CALL tiles: seller calls for the seller floor, buyer calls (the VANI buyer agent) for the buyer desk."""
        idle = float(self.settings.get("voice_plugin.idle_timeout_seconds", 120))
        out = []
        for s in self.store.list("voice_sessions", 30):
            if s["status"] != "connected" or time.time() - s["last_event_epoch"] > idle or s.get("direction", "seller_outbound") != direction:
                continue
            if direction == "buyer_inbound":
                turns = [{"who": "bot" if t.get("speaker") == "bot" else "buyer", "text": t.get("text", "")} for t in s["transcript"][-8:]]
                out.append({"id": s["id"], "real": True, "turns": turns, "elapsed_s": 9999, "turn_seconds": 1, "captured": {},
                            "on_air_s": round(9999 + time.time() - datetime.fromisoformat(s["created_at"]).timestamp(), 1),
                            "signal": (s["signals"][-1]["signal"] if s["signals"] else None),
                            "call": {"id": s["id"], "buyer": "Live caller", "buyer_glid": "—", "product": s.get("product") or "Requirement",
                                     "city": "", "missed_seller": s.get("missed_seller") or "seller", "flow": "live_sarvam",
                                     "language": (s.get("delivery_override") or {}).get("language") or s.get("base_language", "hi-IN")},
                            "provider": s["provider"]})
                continue
            try:
                profile = roster.profile(s["seller_id"])
                summary = roster.summary(profile)
            except ValueError:
                summary = {"seller_id": s["seller_id"], "company_name": s["seller_id"]}
            turns = [{"who": "bot" if t.get("speaker") == "bot" else "seller", "text": t.get("text", "")} for t in s["transcript"][-8:]]
            out.append({"id": s["id"], "seller": summary, "turns": turns, "elapsed_s": 9999, "turn_seconds": 1,
                        "on_air_s": round(time.time() - datetime.fromisoformat(s["created_at"]).timestamp(), 1),
                        "stage": "live", "signal": (s["signals"][-1]["signal"] if s["signals"] else None), "switch": None,
                        "language": (s.get("delivery_override") or {}).get("language") or s.get("base_language"), "genome": [], "real": True,
                        "run_id": s["run_id"], "provider": s["provider"]})
        return out
