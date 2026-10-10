"""Live signal detection for seller turns (PS04 §3.5).

Five signals — frustration, confusion, interest, rush, language switch — plus stop/do-not-call and
"am I talking to a bot". Cues cover romanised Hinglish (how sellers actually speak on these calls),
Devanagari, English and Telugu. Every detection returns the cue that triggered it, so the switch
log can show *why*. Text cues are not acoustic emotion; the method is always recorded.
"""

from __future__ import annotations

import re

LANGUAGE_NAMES = {
    "hindi": "hi-IN", "हिंदी": "hi-IN", "हिन्दी": "hi-IN", "english": "en-IN", "angrezi": "en-IN", "अंग्रेजी": "en-IN",
    "telugu": "te-IN", "తెలుగు": "te-IN", "tamil": "ta-IN", "தமிழ்": "ta-IN", "marathi": "mr-IN", "मराठी": "mr-IN",
    "gujarati": "gu-IN", "ગુજરાતી": "gu-IN", "bengali": "bn-IN", "bangla": "bn-IN", "বাংলা": "bn-IN",
    "punjabi": "pa-IN", "ਪੰਜਾਬੀ": "pa-IN", "kannada": "kn-IN", "malayalam": "ml-IN",
}

CUES: dict[str, list[str]] = {
    "stop": [r"\b(stop calling|do not call|don'?t call|remove my number|call mat (karo|kariye|karna)|phone mat karo|dobara call mat)\b",
             r"कॉल मत|फोन मत|दोबारा कॉल"],
    "frustration": [r"\b(frustrat\w*|angry|annoyed|irritat\w*|fed up|useless|bakwas|bekaar|pareshan|naraz|roz call|baar baar|"
                    r"already told|kitni baar|complaint|refund|time waste|dimag kharab|bahut ho gaya|faltu)\b",
                    r"परेशान|नाराज़|बेकार|बकवास|कितनी बार|రోజూ|విసుగు"],
    "confusion": [r"\b(samajh nahi|samjha nahi|samjhi nahi|kya matlab|matlab\??$|confus\w*|don'?t understand|not clear|"
                  r"what do you mean|phir se (bolo|boliye|batao)|dobara (bolo|boliye|batao)|kya\?|kaun sa|clear nahi)\b",
                  r"समझ नहीं|क्या मतलब|क्लियर नहीं|फिर से बोलिए|అర్థం కాలేదు|புரியல"],
    "rush": [r"\b(jaldi|jaldi se|time nahi|samay nahi|busy (hoon|hun|hu)|in a meeting|meeting mein|one minute|ek minute|"
             r"quickly|short mein|seedha bolo|seedhe point|point pe aao|hurry|bas (do|ek) minute|baad mein baat)\b",
             # Sarvam writes English loanwords in Devanagari ("टाइम नहीं है मेरे पास" — live Payal call, 5.9.0).
             r"जल्दी|समय नहीं|व्यस्त|टाइम नहीं|टाइम नही|बिज़ी|बिजी|मीटिंग में हूँ|एक मिनट|बाद में बात|सीधा बोल|త్వరగా|టైం లేదు"],
    "interest": [r"\b(interested|achha laga|accha hai|batao (aur|zara)|aur batao|tell me more|kaise milega|kitne leads|"
                 r"demo|meeting (rakh|fix)|kab (aa|mil) sakte|sounds good|theek hai (aage|chalo)|next step|kya fayda|"
                 r"how does it work|kaise kaam)\b",
                 r"दिलचस्प|और बताइए|मीटिंग रख|मीटिंग फिक्स|डेमो|ఆసక్తి|చెప్పండి"],
    "bot_question": [r"\b(are you a (bot|robot|machine)|bot ho|robot ho|machine ho|insaan ho|real person|computer se)\b",
                     r"रोबोट|मशीन से"],
}


HINDI_MARKERS = {
    "hai", "hain", "nahi", "nahin", "kya", "aap", "aapka", "aapke", "mera", "mere", "meri", "hoon", "hun", "kar", "karo", "karna", "kariye",
    "boliye", "bolo", "ji", "theek", "thik", "haan", "han", "batao", "bataiye", "abhi", "kaise", "kyun", "kyon", "mujhe", "humko", "hum",
    "bhai", "kitne", "kitna", "kitni", "milenge", "milega", "milegi", "rakh", "rakho", "rakhiye", "kal", "aaj", "wala", "wale", "wali",
    "bhi", "toh", "to", "ki", "ka", "ke", "ko", "se", "mein", "me", "par", "pe", "ye", "yeh", "woh", "wo", "achha", "accha", "hoga",
    "hogi", "chahiye", "dijiye", "sakte", "sakta", "sakti", "lekin", "aur", "matlab", "samajh", "baat", "baje", "din", "paisa", "paise",
}
ENGLISH_MARKERS = {"the", "is", "are", "you", "i", "what", "how", "will", "can", "please", "my", "your", "this", "that", "with",
                   "for", "about", "would", "should", "do", "does", "have", "it", "we", "they", "be", "not", "and"}


def script_language(text: str) -> str | None:
    """Language implied by the script of the text (not by geography)."""
    counts = {
        "hi-IN": len(re.findall(r"[ऀ-ॿ]", text)), "te-IN": len(re.findall(r"[ఀ-౿]", text)),
        "ta-IN": len(re.findall(r"[஀-௿]", text)), "bn-IN": len(re.findall(r"[ঀ-৿]", text)),
        "gu-IN": len(re.findall(r"[઀-૿]", text)), "pa-IN": len(re.findall(r"[਀-੿]", text)),
        "kn-IN": len(re.findall(r"[ಀ-೿]", text)), "ml-IN": len(re.findall(r"[ഀ-ൿ]", text)),
    }
    best = max(counts, key=counts.get)
    if counts[best] >= 4:
        return best
    words = re.findall(r"[a-zA-Z']+", text.lower())
    if len(words) >= 4:
        share = sum(w in HINDI_MARKERS for w in words) / len(words)
        english = sum(w in ENGLISH_MARKERS for w in words)
        if share >= 0.2:
            return "hi-IN"
        if share == 0 and english >= 3 and len(words) >= 6:
            return "en-IN"
    return None


def detect(text: str, current_language: str) -> list[dict]:
    """Return detected signals for one seller turn, strongest first."""
    value = text.lower().strip()
    found: list[dict] = []
    # Explicit language request ("Hindi mein boliye", "speak in English", "తెలుగులో")
    for name, code in LANGUAGE_NAMES.items():
        idx = value.find(name)
        if idx < 0:
            continue
        before, after = value[max(0, idx - 25):idx], value[idx + len(name): idx + len(name) + 18]
        negated = re.search(r"(not|nahi|mat|don'?t|no)\s*$", before) or re.match(r"\s*(nahi|mat|not)\b", after)
        asked = re.match(r"\s*(mein|me|में|lo|లో|il|la|please|bolo|boliye|baat|chalega|speak|pe)\b", after) or \
            re.search(r"(speak|talk|switch to|in|continue in|prefer)\s*$", before)
        if asked and not negated and code != current_language:
            found.append({"signal": "language_switch", "confidence": 0.95, "explicit": True, "cue": text[max(0, idx - 10): idx + len(name) + 8].strip(),
                          "target_language": code, "method": "text_explicit"})
            break
    for signal, patterns in CUES.items():
        for pattern in patterns:
            m = re.search(pattern, value, flags=re.IGNORECASE)
            if m:
                explicit = signal == "stop"
                found.append({"signal": signal, "confidence": 0.95 if explicit else 0.75, "explicit": explicit,
                              "cue": m.group(0)[:60], "method": "text_lexicon"})
                break
    if not any(f["signal"] == "language_switch" for f in found):
        implied = script_language(text)
        if implied and implied != current_language and not (implied == "hi-IN" and current_language == "en-IN" and len(value) < 25):
            found.append({"signal": "language_switch", "confidence": 0.7, "explicit": False, "cue": "script/words",
                          "target_language": implied, "method": "script_detection"})
    order = {"stop": 0, "language_switch": 1, "frustration": 2, "confusion": 3, "rush": 4, "interest": 5, "bot_question": 6}
    return sorted(found, key=lambda f: order.get(f["signal"], 9))


# Switch rules: signal → changes to delivery (PS04: confusion slower/simpler, rush shorter, language match,
# frustration softer + acknowledge, interest deeper + next step).
RULES = {
    "confusion": {"pace": -0.12, "sentence_length": "short", "simplify": True, "reason": "Seller is confused → slower, simpler, one idea at a time"},
    "rush": {"pace": +0.12, "sentence_length": "short", "reason": "Seller is in a hurry → shorter replies, straight to the point"},
    "frustration": {"pace": -0.06, "warmth": "high", "empathy": "high", "acknowledge": True, "reason": "Seller is frustrated → softer tone, acknowledge before anything else"},
    "interest": {"sentence_length": "medium", "advance": True, "reason": "Seller is interested → go one level deeper and move to the next step"},
}
