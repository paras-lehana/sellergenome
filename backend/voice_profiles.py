"""Documented Sarvam model capabilities bound configurable presentation profiles.

The registry is a maximum supported envelope, not a claim of account access.
Reference: https://docs.sarvam.ai/api-reference/text-to-speech/convert and /stream
"""

import math
import hashlib

from backend.provider_errors import ProviderUnavailable

CAPABILITIES = {
    "bulbul:v4-flash": {
        "speakers": [
            "shubh_en_recovery",
            "aparna_hi_customer",
            "simran_enhi_customer",
        ],
        "default_speaker": "shubh_en_recovery",
        "pace_min": 0.5,
        "pace_max": 2.0,
        "pitch_supported": True,
        "pitch_min": -0.5,
        "pitch_max": 0.5,
        "max_text_chars": 2500,
        "sample_rate": 24000,
        "streaming": True,
        "experimental": True,
    },
    "bulbul:v3": {
        "speakers": "shubh aditya ritu priya neha rahul pooja rohan simran kavya amit dev ishita shreya ratan varun manan sumit roopa kabir aayan ashutosh advait anand tanya tarun sunny mani gokul vijay shruti suhani mohit kavitha rehan soham rupali".split(),
        "default_speaker": "shubh",
        "pace_min": 0.5,
        "pace_max": 2.0,
        "pitch_supported": False,
        "max_text_chars": 2500,
        "sample_rate": 24000,
        "streaming": True,
    },
    "bulbul:v2": {
        "speakers": "anushka manisha vidya arya abhilash karun hitesh".split(),
        "default_speaker": "anushka",
        "pace_min": 0.3,
        "pace_max": 3.0,
        "pitch_supported": True,
        "pitch_min": -0.75,
        "pitch_max": 0.75,
        "max_text_chars": 1500,
        "sample_rate": 22050,
        "streaming": True,
    },
}


def public_profiles(config):
    voice = config.values["voice"]
    selected = voice.get("tts_profile", "v3")
    configured = voice.get("tts_profiles", {})
    output = []
    for name, model in (
        ("v3", "bulbul:v3"),
        ("v2_pitch", "bulbul:v2"),
        ("v4_pitch", "bulbul:v4-flash"),
    ):
        options = configured.get(name, {})
        cap = dict(CAPABILITIES[model])
        cap["pace_min"] = max(
            cap["pace_min"], float(options.get("pace_min", cap["pace_min"]))
        )
        cap["pace_max"] = min(
            cap["pace_max"], float(options.get("pace_max", cap["pace_max"]))
        )
        cap["max_text_chars"] = min(
            cap["max_text_chars"],
            int(options.get("max_text_chars", cap["max_text_chars"])),
        )
        cap["sample_rate"] = options.get("sample_rate", cap["sample_rate"])
        cap["streaming"] = cap["streaming"] and options.get("streaming", True)
        cap["pitch_supported"] = cap["pitch_supported"] and options.get(
            "pitch_supported", True
        )
        if cap["pitch_supported"]:
            cap["pitch_min"] = max(
                cap["pitch_min"],
                float(options.get("pitch_min", cap["pitch_min"])),
            )
            cap["pitch_max"] = min(
                cap["pitch_max"],
                float(options.get("pitch_max", cap["pitch_max"])),
            )
        speakers = [
            speaker
            for speaker in options.get("speakers", cap["speakers"])
            if speaker in cap["speakers"]
        ]
        receipt = getattr(config, "voice_capability_receipts", {}).get(
            model, {}
        )
        slot_key = getattr(
            config,
            "sarvam_backup_key"
            if receipt.get("credential_slot") == "backup"
            else "sarvam_key",
            "",
        )
        current_receipt = (
            receipt
            if not receipt.get("credential_fingerprint")
            or hashlib.sha256(slot_key.encode()).hexdigest()
            == receipt["credential_fingerprint"]
            else {}
        )
        output.append(
            {
                "id": name,
                "model": model,
                **cap,
                "speakers": speakers,
                "configured_enabled": options.get("enabled", name == "v3"),
                "enabled": options.get("enabled", name == "v3")
                and current_receipt.get("status") != "deprecated",
                "selected": name == selected,
                "availability": current_receipt.get(
                    "status", "documented_capability_account_access_unverified"
                ),
                "capability_reason": current_receipt.get("reason"),
                "capability_checked_at": current_receipt.get("checked_at"),
            }
        )
    return output


def tts_parameters(
    config,
    text,
    language,
    speaker=None,
    pace=1.0,
    *,
    pitch=None,
    profile=None,
    streaming=False,
):
    voice = config.values["voice"]
    chosen = profile or voice.get("tts_profile", "v3")
    profiles = {item["id"]: item for item in public_profiles(config)}
    selected = profiles.get(chosen)
    if not selected or not selected["enabled"]:
        raise ProviderUnavailable(
            (selected.get("capability_reason") if selected else None)
            or "Selected speech profile is disabled or unsupported.",
            "sarvam",
            422,
        )
    if streaming and not selected["streaming"]:
        raise ProviderUnavailable(
            "Streaming is disabled for the selected voice profile.",
            "sarvam",
            422,
        )
    if not speaker:
        speaker = selected["default_speaker"]
    if speaker not in selected["speakers"]:
        raise ProviderUnavailable(
            "Speaker does not belong to the selected speech model profile.",
            "sarvam",
            422,
        )
    if language not in voice["languages"]:
        raise ProviderUnavailable(
            "Language is outside the configured voice allowlist.",
            "sarvam",
            422,
        )
    if (
        isinstance(pace, bool)
        or not isinstance(pace, (int, float))
        or not math.isfinite(pace)
        or not selected["pace_min"] <= pace <= selected["pace_max"]
    ):
        raise ProviderUnavailable(
            "Speech pace exceeds this model's supported range.", "sarvam", 422
        )
    maximum = min(
        voice.get("max_text_chars", selected["max_text_chars"]),
        selected["max_text_chars"],
    )
    if not isinstance(text, str) or not text.strip() or len(text) > maximum:
        raise ProviderUnavailable(
            "Text exceeds the selected speech model limit.", "sarvam", 422
        )
    options = voice.get("tts_profiles", {}).get(chosen, {})
    sample_rate = options.get(
        "sample_rate", voice.get("sample_rate", selected["sample_rate"])
    )
    allowed_rates = {8000, 16000, 22050, 24000}
    if selected["model"] in {"bulbul:v3", "bulbul:v4-flash"} and not streaming:
        allowed_rates |= {32000, 44100, 48000}
    if sample_rate not in allowed_rates:
        raise ProviderUnavailable(
            "Sample rate is unsupported by this model and transport.",
            "sarvam",
            422,
        )
    params = {
        "text": text,
        "language_code": language,
        "speaker": speaker,
        "pace": pace,
        "model": selected["model"],
        "speech_sample_rate": sample_rate,
    }
    if selected["model"] == "bulbul:v4-flash":
        params["enable_preprocessing"] = options.get(
            "enable_preprocessing", True
        )
    if pitch is not None:
        if not selected["pitch_supported"]:
            raise ProviderUnavailable(
                "Pitch is not supported by Bulbul v3 or is disabled for this profile. A pitch-capable profile also requires current provider model/account access.",
                "sarvam",
                422,
            )
        if (
            isinstance(pitch, bool)
            or not isinstance(pitch, (float, int))
            or not math.isfinite(pitch)
            or not selected["pitch_min"] <= pitch <= selected["pitch_max"]
        ):
            raise ProviderUnavailable(
                "Pitch exceeds the configured supported range.", "sarvam", 422
            )
        params["pitch"] = pitch
    elif selected["pitch_supported"]:
        params["pitch"] = float(options.get("pitch", 0.0))
        if (
            not selected["pitch_min"]
            <= params["pitch"]
            <= selected["pitch_max"]
        ):
            raise ProviderUnavailable(
                "Configured pitch exceeds the supported range.", "sarvam", 422
            )
    return params
