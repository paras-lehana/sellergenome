"""Speech and bot engines — one catalogue for every place SellerGenome speaks, listens or hands a call to a bot.

Admin → Voice & engines sets the defaults (``engines.*`` in settings.yaml). Each place that uses an engine — the call
workspace, the buyer desk, /sarvam and the Admin tests — has its own picker that overrides the default for that
browser. Choices are additive (decision D25): Bulbul v4 sits next to v3, streaming next to REST, realtime listening
next to clips, Payal on Sarvam next to the built-in bot. Every value is checked against this allow-list before it
reaches Sarvam.

Model ids were read from api.sarvam.ai's own validation errors on 2026-10-09 (the API lists what it accepts), and the
latencies were measured from this server the same day (docs/architecture/voice-engines.md).
"""

from __future__ import annotations

from typing import Any

TTS_MODELS = {
    "bulbul:v4-flash": {"label": "Bulbul v4 flash", "note": "Latest Sarvam voice model — 224 persona voices, including Hinglish customer-support voices."},
    "bulbul:v3": {"label": "Bulbul v3", "note": "The 5.2 voice model — 37 voices, temperature control."},
}
TTS_TRANSPORTS = {
    "stream": {"label": "Streaming (WebSocket)", "note": "Audio starts while the rest is still being made — first sound in ≈0.3 s (measured 246–284 ms)."},
    "rest": {"label": "One request per line (REST)", "note": "The whole line is made first — ≈1.5 s before the first sound."},
}
STT_MODELS = {
    "saaras:v4": {"label": "Saaras v4", "note": "Latest; keyterms bias, code-mixed output keeps English words in English (measured 397 ms per clip)."},
    "saaras:v3": {"label": "Saaras v3", "note": "The 5.2 model (measured 508 ms per clip)."},
    "saarika:v2.5": {"label": "Saarika v2.5", "note": "Transcription-only model; no output modes."},
}
STT_REALTIME_MODELS = {
    "saaras:v4": {"label": "Saaras v4 (realtime)", "note": "Streaming with live partial transcripts; keyterms supported."},
    "saaras:v3-realtime": {"label": "Saaras v3 realtime", "note": "Streaming model without keyterms."},
}
STT_MODES = {
    "codemix": "Hindi in Devanagari, English words in English — “मेरा phone number है”",
    "transcribe": "Everything in the spoken language’s script",
    "translit": "Romanised — “mera phone number hai”",
    "verbatim": "Word for word, fillers kept, numbers as spoken",
    "translate": "Translated to English",
}
LISTEN = {
    "realtime": {"label": "Realtime (streaming)", "note": "Words appear while you speak; the turn ends on a pause (Sarvam’s voice detection); speaking over the bot stops its audio."},
    "clip": {"label": "Clip (record, then transcribe)", "note": "Press to start and stop; the clip is transcribed after you stop."},
}
BOTS = {
    "builtin": {"label": "SellerGenome VANI (built-in)", "note": "Our pipeline: persona switches, teleprompter, signals and the switch log; Sarvam LLM, Bulbul and Saaras underneath."},
    "sarvam_agent": {"label": "Payal on Sarvam Voice Agents", "note": "Sarvam’s hosted agent, full-duplex voice in the browser: Sarvam handles listening, interruptions and turn-taking."},
}
TRANSLATE_MODELS = {
    "sarvam-translate:v1": {"label": "Sarvam Translate v1", "note": "All 22 scheduled languages, formal register."},
    "mayura:v1": {"label": "Mayura v1", "note": "The 5.2 model; supports the colloquial modes."},
}
LLM_MODELS = {
    "sarvam-105b-conversations": {"label": "sarvam-105b-conversations", "note": "Tuned for dialogue (the default)."},
    "sarvam-105b": {"label": "sarvam-105b", "note": "General model."},
}
REASONING = ["low", "medium", "high"]
PAYAL_VAD = {"basic-vad": "Platform default", "hybrid-vad": "Hybrid voice detection"}
# Romanised Hinglish ("Ji bilkul …") before speech: Bulbul v4 reads it correctly (round trip through Saaras returned the exact
# words, 2026-10-10), so the ~670 ms transliteration call is only needed for v3.
TRANSLITERATE = {"auto": "Only for Bulbul v3 (v4 reads romanised Hinglish directly — saves ≈0.7 s per line)",
                 "always": "Convert romanised Hindi to Devanagari before every line (5.3 behaviour)",
                 "never": "Send the text as written"}


def needs_transliteration(model: str, policy: str) -> bool:
    return policy == "always" or (policy == "auto" and model == "bulbul:v3")

# Persona voices (Bulbul v3 names, chosen by the persona factor "voice") → their Bulbul v4 counterparts. v4 voices speak
# every supported language (checked 2026-10-09), so one mapping serves all languages.
V4_FOR_PERSONA = {"ritu": "ritu_hi_customer", "priya": "ishita_enhi_customer", "kavya": "kavya_hi_conversational",
                  "shubh": "shubh_hi_customer", "rahul": "rahul_hi_conversational", "amit": "amit_hi_conversational"}
FEMALE = {"ritu", "priya", "kavya", "ishita", "simran", "sanchita", "shalini", "roopa", "aparna", "shreya", "suhani", "sarika",
          "chhavi", "tanya", "shilpa", "aarti", "chaitra", "vandana", "suchitra", "neha", "pooja", "kavitha", "shruti", "rupali"}
# The voices offered in pickers (the full catalogue is accepted too): conversational and customer voices fit a sales call.
V4_PICKS = ["ritu_hi_customer", "ritu_hi_conversation", "ishita_enhi_customer", "ishita_enhi_customer_expressive", "simran_enhi_customer",
            "shalini_enhi_customer", "kavya_hi_conversational", "aarti_hi_customer", "sanchita_hi_assistant", "roopa_hi_conversational",
            "shubh_hi_customer", "shubh_enhi_banking", "sunny_enhi_customer", "rahul_hi_conversational", "amit_hi_conversational",
            "aditya_hi_sales", "tarun_hi_conversational", "mohit_hi_conversational"]
V3_VOICES = ["aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev", "ishita", "shreya",
             "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "anand", "tanya", "tarun", "sunny", "mani",
             "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali"]
# Every Bulbul v4 flash voice (224, read from the API on 2026-10-09). Shape: <name>_<lang>_<style>.
V4_PATTERN = r"^[a-z]+_(hi|en|enhi|bn|as|gu|kn|ml|mr|od|pa|ta|te)_[a-z_]+$"

LOCATIONS = {
    "call": {"label": "Call workspace", "uses": ["bot", "tts_model", "tts_voice", "tts_transport", "listen", "stt_realtime_model", "stt_model",
                                                  "stt_mode", "translate_model"]},
    "buyer": {"label": "Buyer desk", "uses": ["tts_model", "tts_voice", "tts_transport", "listen", "stt_realtime_model", "stt_model", "stt_mode"]},
    "sarvam": {"label": "Sarvam page (Payal + try)", "uses": ["tts_model", "tts_voice", "tts_transport", "translate_model"]},
}
KINDS = ("bot", "tts_model", "tts_voice", "tts_transport", "listen", "stt_model", "stt_realtime_model", "stt_mode", "translate_model")


def voice_ok(model: str, voice: str) -> bool:
    import re
    if model == "bulbul:v3":
        return voice in V3_VOICES
    return bool(re.match(V4_PATTERN, voice or ""))


def voice_for(model: str, persona_voice: str, override: str = "") -> str:
    """The speaker to send: an explicit, valid override wins; otherwise the persona's voice in the model's catalogue."""
    if override and override != "auto" and voice_ok(model, override):
        return override
    persona_voice = (persona_voice or "ritu").lower()
    if model == "bulbul:v3":
        return persona_voice if persona_voice in V3_VOICES else "ritu"
    if voice_ok(model, persona_voice):
        return persona_voice
    return V4_FOR_PERSONA.get(persona_voice, "ritu_hi_customer")


def contrast_voice(model: str, bot_voice: str) -> str:
    """A voice of the other gender for the simulated seller, so two speakers can be told apart by ear."""
    female = bot_voice.split("_")[0] in FEMALE
    if model == "bulbul:v3":
        return "shubh" if female else "priya"
    return "rahul_hi_conversational" if female else "kavya_hi_conversational"


def defaults(settings) -> dict:
    return {k: settings.get(f"engines.{k}") for k in KINDS} | {"keyterms": settings.get("engines.keyterms", []),
                                                                "realtime": settings.get("engines.realtime", {}),
                                                                "transliterate": settings.get("engines.transliterate", "auto")}


def resolve(settings, requested: dict | None = None) -> dict:
    """Admin defaults overlaid with a place's choices; anything outside the catalogue falls back to the default."""
    out = defaults(settings)
    for k, v in (requested or {}).items():
        if v in (None, "", "default") or k not in KINDS:
            continue
        allowed = {"bot": BOTS, "tts_model": TTS_MODELS, "tts_transport": TTS_TRANSPORTS, "listen": LISTEN, "stt_model": STT_MODELS,
                   "stt_realtime_model": STT_REALTIME_MODELS, "stt_mode": STT_MODES, "translate_model": TRANSLATE_MODELS}.get(k)
        if allowed is not None and v in allowed:
            out[k] = v
        elif k == "tts_voice" and (v == "auto" or voice_ok(out.get("tts_model") or "bulbul:v4-flash", v)):
            out[k] = v
    return out


def check(kind: str, value: Any) -> Any:
    """Validate one model choice from a request body; raise ValueError with the allowed values otherwise."""
    allowed = {"tts_model": TTS_MODELS, "stt_model": STT_MODELS, "stt_realtime_model": STT_REALTIME_MODELS, "stt_mode": STT_MODES,
               "translate_model": TRANSLATE_MODELS, "tts_transport": TTS_TRANSPORTS}[kind]
    if value not in allowed:
        raise ValueError(f"{kind} must be one of {', '.join(allowed)}")
    return value


def catalogue(settings) -> dict:
    """Public description of every choice (what the pickers render) plus the effective Admin defaults."""
    def opts(d: dict) -> list[dict]:
        return [{"id": k, **(v if isinstance(v, dict) else {"label": k, "note": v})} for k, v in d.items()]
    return {
        "defaults": defaults(settings),
        "locations": LOCATIONS,
        "options": {
            "bot": opts(BOTS), "tts_model": opts(TTS_MODELS), "tts_transport": opts(TTS_TRANSPORTS), "listen": opts(LISTEN),
            "stt_model": opts(STT_MODELS), "stt_realtime_model": opts(STT_REALTIME_MODELS),
            "stt_mode": [{"id": k, "label": k, "note": v} for k, v in STT_MODES.items()],
            "translate_model": opts(TRANSLATE_MODELS), "llm_model": opts(LLM_MODELS), "reasoning_effort": REASONING,
            "payal_vad": [{"id": k, "label": k, "note": v} for k, v in PAYAL_VAD.items()],
            "transliterate": [{"id": k, "label": k, "note": v} for k, v in TRANSLITERATE.items()],
        },
        "voices": {"bulbul:v4-flash": V4_PICKS, "bulbul:v3": V3_VOICES},
        "persona_voice_map": V4_FOR_PERSONA,
        "llm": {"model": settings.get("llm.model"), "reasoning_effort": settings.get("llm.reasoning_effort")},
    }


SETTING_CHOICES = {"engines.bot": BOTS, "engines.tts_model": TTS_MODELS, "engines.tts_transport": TTS_TRANSPORTS, "engines.listen": LISTEN,
                   "engines.stt_model": STT_MODELS, "engines.stt_realtime_model": STT_REALTIME_MODELS, "engines.stt_mode": STT_MODES,
                   "engines.translate_model": TRANSLATE_MODELS, "llm.model": LLM_MODELS, "engines.realtime.stream_type": {"fast": 1, "balanced": 1},
                   "sarvam_voice.vad": PAYAL_VAD, "engines.transliterate": TRANSLITERATE,
                   "persona.engine": {"generator": 1, "aggregator": 1}}


def validate_settings(changes: dict, current_tts_model: str) -> None:
    """Admin saves are checked against the catalogue, so a typo can never reach Sarvam as a model name."""
    for path, value in changes.items():
        if path in SETTING_CHOICES and value not in SETTING_CHOICES[path]:
            raise ValueError(f"{path} must be one of {', '.join(SETTING_CHOICES[path])}")
        if path == "llm.reasoning_effort" and value not in REASONING:
            raise ValueError(f"llm.reasoning_effort must be one of {', '.join(REASONING)}")
        if path == "engines.tts_voice" and value != "auto":
            model = changes.get("engines.tts_model", current_tts_model)
            if not voice_ok(model, str(value)):
                raise ValueError(f"engines.tts_voice {value!r} is not a {model} voice")
        if path in ("engines.keyterms", "engines.buyer_keyterms") and (not isinstance(value, list) or len(value) > 50
                                                                    or any(not isinstance(t, str) or len(t) > 64 for t in value)):
            raise ValueError(f"{path} must be a list of at most 50 terms of at most 64 characters")
