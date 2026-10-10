"""Explicit paid synthetic Sarvam evaluation. Audio/receipts stay outside git.

Run with deployment env exported, --output private path. No real sellers are used.
"""

import argparse
import asyncio
import base64
import json
import sys
import time
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import Settings
from backend.providers import Providers
from backend.speech import SpeechService
from backend.store import Store


def pcm16(path, rate=16000):
    with wave.open(str(path), "rb") as reader:
        assert reader.getnchannels() == 1 and reader.getsampwidth() == 2
        original = reader.getframerate()
        source = array("h", reader.readframes(reader.getnframes()))
    target = array("h")
    for index in range(int(len(source) * rate / original)):
        position = index * original / rate
        left = int(position)
        right = min(left + 1, len(source) - 1)
        fraction = position - left
        target.append(
            round(source[left] * (1 - fraction) + source[right] * fraction)
        )
    return target.tobytes()


async def evaluate(output):
    output.mkdir(parents=True, exist_ok=True)
    config = Settings()
    config.data_dir = output / "state"
    config.data_dir.mkdir(exist_ok=True)
    config.db_path = config.data_dir / "persona.sqlite3"
    provider = Providers(config)
    store = Store(config.db_path)
    speech = SpeechService(config, store, provider)
    receipts = {
        "scope": "synthetic engineering test; not a human gold corpus",
        "tts": [],
        "stt": [],
        "batch": [],
        "stream": {},
    }
    texts = {
        "asha": (
            "ritu",
            0.9,
            "Hello, I am Vani, an AI assistant. Let us review the useful enquiries for your business, one step at a time.",
            "नमस्ते, मैं वाणी हूँ, एक एआई सहायक। हम आपके व्यवसाय की ज़रूरत को समझकर, एक आसान अगला कदम चुन सकते हैं।",
        ),
        "kabir": (
            "rahul",
            1.15,
            "Hello, I am Vani, an AI assistant. I will keep this short. Would you like to review the next useful enquiry?",
            "नमस्ते, मैं वाणी हूँ, एक एआई सहायक। मैं संक्षेप में बात करूँगा। क्या आप अगली उपयोगी पूछताछ देखना चाहेंगे?",
        ),
        "ravi": (
            "shubh",
            0.85,
            "Hello, I am Vani, an AI assistant. I understand that repeating your concern is frustrating. Let us focus on the issue first.",
            "नमस्ते, मैं वाणी हूँ, एक एआई सहायक। बार-बार समस्या बताना कठिन होता है। पहले आपकी परेशानी समझते हैं।",
        ),
    }
    manifest = []
    for seller, (speaker, pace, english, hindi) in texts.items():
        for language, text in [("en-IN", english), ("hi-IN", hindi)]:
            identifier = seller + "-" + language[:2]
            start = time.monotonic()
            result = await provider.synthesize(text, language, speaker, pace)
            content = base64.b64decode(result["audio_base64"], validate=True)
            (output / (identifier + ".wav")).write_bytes(content)
            with wave.open(str(output / (identifier + ".wav"))) as reader:
                duration = reader.getnframes() / reader.getframerate()
            item = {
                "id": identifier,
                "seller": seller.title(),
                "language": language,
                "speaker": speaker,
                "pace": pace,
                "text": text,
                "synthetic": True,
                "provider": "sarvam",
                "model": result["model"],
                "duration_seconds": round(duration, 2),
                "request_ms": round((time.monotonic() - start) * 1000),
            }
            manifest.append(item)
            receipts["tts"].append(item)
            print(
                "Generated",
                identifier,
                "duration",
                round(duration, 2),
                flush=True,
            )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    for language in ["en-IN", "hi-IN"]:
        path = output / ("asha-" + language[:2] + ".wav")
        result = await provider.transcribe(
            path.read_bytes(), "test.wav", "audio/wav", language
        )
        receipts["stt"].append(result)
        print("REST STT", language, bool(result["text"]), flush=True)
    # Alternating four known synthetic turns, distinct voices and 500 ms gaps.
    for language in ["en", "hi"]:
        parts = [
            output / (seller + "-" + language + ".wav")
            for seller in ["asha", "ravi", "asha", "ravi"]
        ]
        combined = output / ("two-speaker-" + language + ".wav")
        truth, cursor = [], 0
        with wave.open(str(combined), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(24000)
            for index, part in enumerate(parts):
                with wave.open(str(part), "rb") as reader:
                    content = reader.readframes(reader.getnframes())
                duration = len(content) / 48000
                truth.append(
                    {
                        "speaker": "ritu" if index % 2 == 0 else "shubh",
                        "start": cursor,
                        "end": cursor + duration,
                    }
                )
                writer.writeframes(content + b"\0" * 24000)
                cursor += duration + 0.5
        job = speech.create(
            combined.read_bytes(),
            "wav",
            "evaluation",
            language + "-IN",
            2,
            "",
            "2026-10-07T00:00:00+00:00",
            True,
        )
        for _ in range(90):
            try:
                await speech.step(job)
            except Exception as error:
                receipts["batch"].append(
                    {
                        "language": language,
                        "error": str(error)
                        if type(error).__name__ == "ProviderUnavailable"
                        else type(error).__name__,
                    }
                )
                print(
                    "Batch failed", language, type(error).__name__, flush=True
                )
                break
            job = speech.get(job["id"], "evaluation")
            if job["status"] in {"completed", "failed"}:
                receipts["batch"].append(
                    {
                        "language": language,
                        "job": speech.public(job),
                        "known_turns": truth,
                    }
                )
                print(
                    "Batch",
                    language,
                    job["status"],
                    "speakers",
                    job.get("speaker_count"),
                    flush=True,
                )
                break
            await asyncio.sleep(5)
        (output / "receipts.json").write_text(
            json.dumps(receipts, ensure_ascii=False, indent=2)
        )
    mp3 = bytearray()
    try:
        async for chunk in provider.sarvam.tts_stream(
            texts["asha"][2], "en-IN", "ritu", 0.9
        ):
            mp3.extend(base64.b64decode(chunk["audio"]))
        (output / "stream.mp3").write_bytes(mp3)
        receipts["stream"]["tts_bytes"] = len(mp3)
        print("Streaming TTS", len(mp3), flush=True)
    except Exception as error:
        receipts["stream"]["tts_error"] = (
            str(error)
            if type(error).__name__ == "ProviderUnavailable"
            else type(error).__name__
        )
        print("Streaming TTS failed", type(error).__name__, flush=True)
    try:
        events = []
        async with provider.sarvam.websocket(
            "/speech-to-text-realtime/ws",
            {
                "model": config.values["voice"]["realtime_model"],
                "language_code": "en-IN",
                "stream_type": "fast",
                "encoding": "linear16",
                "sample_rate": 16000,
                "endpointing": "vad",
            },
        ) as (socket, slot):

            async def receive():
                async for raw in socket:
                    event = json.loads(raw)
                    events.append(event)
                    if event.get("event") in {"transcript.final", "error"}:
                        return

            task = asyncio.create_task(receive())
            pcm = pcm16(output / "asha-en.wav") + b"\0" * 64000
            for offset in range(0, len(pcm), 6400):
                await socket.send(
                    json.dumps(
                        {
                            "event": "audio_input",
                            "audio": base64.b64encode(
                                pcm[offset : offset + 6400]
                            ).decode(),
                        }
                    )
                )
                await asyncio.sleep(0.2)
                if task.done():
                    break
            await asyncio.wait_for(task, 15)
        receipts["stream"]["stt_events"] = events
        print("Streaming STT", [e.get("event") for e in events], flush=True)
    except Exception as error:
        receipts["stream"]["stt_error"] = (
            str(error)
            if type(error).__name__ == "ProviderUnavailable"
            else type(error).__name__
        )
        print("Streaming STT failed", type(error).__name__, flush=True)
    (output / "receipts.json").write_text(
        json.dumps(receipts, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(evaluate(parser.parse_args().output))
