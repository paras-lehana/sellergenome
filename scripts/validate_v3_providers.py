"""Explicit funded V3 API validation with synthetic speech only.

Reads a private PM_PASSWORD environment file, never prints credentials, seller
records or audio base64. Receipts and short audio assets stay outside Git.
"""

import argparse
import base64
import io
import json
import time
import wave
from pathlib import Path

import httpx


def validate(base, secrets_path, output):
    output.mkdir(parents=True, exist_ok=True)
    private = dict(
        line.split("=", 1)
        for line in secrets_path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    receipt = {
        "scope": "synthetic provider API validation",
        "tts": [],
        "stt": [],
        "persona": {},
        "adaptation": {},
    }
    with httpx.Client(base_url=base, timeout=150) as client:
        client.get("/")
        response = client.post(
            "/api/auth/login", json={"password": private["PM_PASSWORD"]}
        )
        response.raise_for_status()
        response = client.post(
            "/api/sellers/demo-asha/persona", json={"use_llm": True}
        )
        receipt["persona"] = {"http_status": response.status_code}
        if response.is_success:
            value = response.json()
            receipt["persona"].update(
                method=value.get("method"),
                indexed=value.get("indexed"),
                fields=len(value.get("fields", {})),
                model=value.get("model"),
            )
        else:
            receipt["persona"]["reason"] = response.json().get("detail")
        cases = [
            (
                "v3-english",
                "v3",
                "en-IN",
                "shubh",
                None,
                "Hello, I am Vani, an AI assistant. I can explain one useful next step for your business.",
            ),
            (
                "v3-hindi",
                "v3",
                "hi-IN",
                "ritu",
                None,
                "नमस्ते, मैं वाणी हूँ, एक एआई सहायक। आपकी बात समझकर एक आसान अगला कदम चुनते हैं।",
            ),
        ]
        for identifier, profile, language, speaker, pitch, text in cases:
            start = time.monotonic()
            body = {
                "text": text,
                "profile": profile,
                "language": language,
                "speaker": speaker,
                "pace": 1.0,
            }
            if pitch is not None:
                body["pitch"] = pitch
            response = client.post("/api/voice/synthesize", json=body)
            entry = {
                "id": identifier,
                "profile": profile,
                "language": language,
                "pitch": pitch,
                "http_status": response.status_code,
                "roundtrip_ms": round((time.monotonic() - start) * 1000),
            }
            if response.is_success:
                result = response.json()
                audio = base64.b64decode(result["audio_base64"], validate=True)
                (output / (identifier + ".wav")).write_bytes(audio)
                with wave.open(io.BytesIO(audio)) as reader:
                    entry.update(
                        sample_rate=reader.getframerate(),
                        duration_seconds=round(
                            reader.getnframes() / reader.getframerate(), 2
                        ),
                        channels=reader.getnchannels(),
                        bytes=len(audio),
                        model=result.get("model"),
                    )
                if profile == "v3":
                    recognized = client.post(
                        "/api/voice/transcribe",
                        files={
                            "file": (identifier + ".wav", audio, "audio/wav")
                        },
                        data={"language": language},
                    )
                    receipt["stt"].append(
                        {
                            "language": language,
                            "http_status": recognized.status_code,
                            "nonempty_transcript": bool(
                                recognized.json().get("text")
                            )
                            if recognized.is_success
                            else False,
                            "model": recognized.json().get("model")
                            if recognized.is_success
                            else None,
                        }
                    )
            else:
                entry["reason"] = response.json().get("detail")
            receipt["tts"].append(entry)
            (output / "provider-api.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2)
            )
        response = client.post(
            "/api/voice/synthesize",
            json={
                "text": "Capability rejection test",
                "profile": "v3",
                "pitch": 0.2,
            },
        )
        receipt["unsupported_v3_pitch_http_status"] = response.status_code
        session = client.post(
            "/api/sessions", json={"seller_id": "demo-asha"}
        ).json()
        switches = []
        for text in [
            "I am in a hurry, keep this short.",
            "I am frustrated and angry.",
            "Please speak English.",
            "Do not call me again.",
        ]:
            response = client.post(
                "/api/sessions/" + session["id"] + "/turns",
                json={"text": text},
            )
            response.raise_for_status()
            value = response.json()
            switches.append(
                {
                    "signal": value["signal"],
                    "switched": bool(value.get("switch")),
                    "suppressed": bool(value.get("decision")),
                    "status": value["status"],
                    "language": value["persona"]["fields"]["language"][
                        "value"
                    ],
                }
            )
        receipt["adaptation"] = {
            "turns": switches,
            "outcome": client.post(
                "/api/sessions/" + session["id"] + "/finish"
            )
            .json()
            .get("recommendation"),
        }
        client.post("/api/auth/logout")
    (output / "provider-api.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2)
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18080")
    parser.add_argument("--secrets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate(args.base, args.secrets, args.output)
