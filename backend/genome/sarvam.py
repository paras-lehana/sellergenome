"""Sarvam for V5 — designated organisation only (decision D7, D14).

- Chat: ``sarvam-105b-conversations`` through the platform gateway ``llm.lehana.in/byok`` (custom
  provider → api.sarvam.ai/v1). JSON mode, low reasoning.
- Speech and language: Bulbul TTS (REST or streaming WebSocket), Saaras STT, translate, transliterate,
  language ID — direct with the designated key. Models are chosen per place (``engines.py``, decision D25).
Every call is metered against a daily budget (public or operator scope) and recorded in the API hit
log, so Admin shows exactly what Sarvam was asked and how long it took.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.genome import engines

ORGANIZATION = "01a110b3-f038-7adb-b627-6863a3785c40"
SARVAM_BASE = "https://api.sarvam.ai"


class BudgetExceeded(RuntimeError):
    pass


class SarvamError(RuntimeError):
    pass


KEY_NAME = "SARVAM_V4_API_KEY"
# Sarvam speech-to-text accepts these MIME types only — and none with parameters such as ";codecs=opus",
# which is exactly what Chrome's MediaRecorder reports (found 2026-10-09: every browser clip failed with 400).
STT_TYPES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave", "audio/aac", "audio/x-aac", "audio/ogg",
             "audio/opus", "audio/flac", "audio/mp4", "audio/x-m4a", "audio/webm", "video/webm", "audio/amr"}
STT_BY_EXT = {"webm": "audio/webm", "ogg": "audio/ogg", "oga": "audio/ogg", "mp4": "audio/mp4", "m4a": "audio/mp4", "mp3": "audio/mpeg",
              "wav": "audio/wav", "aac": "audio/aac", "flac": "audio/flac", "opus": "audio/opus"}


def stt_mime(mime: str, filename: str) -> str:
    """'audio/webm;codecs=opus' → 'audio/webm'; unknown types fall back to the file extension, then octet-stream."""
    base = (mime or "").split(";", 1)[0].strip().lower()
    if base in STT_TYPES:
        return base
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    return STT_BY_EXT.get(ext, "application/octet-stream")


def error_detail(error: Exception) -> str:
    """Sarvam's own reason ("Invalid file type …", "Rate limit …") instead of a bare exception name."""
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = error.response.json()
            msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error") or body.get("message")
        except ValueError:
            msg = error.response.text[:200]
        return f"HTTP {error.response.status_code}: {str(msg or '')[:220]}".strip()
    return f"{type(error).__name__}: {str(error)[:160]}" if str(error) else type(error).__name__


class SarvamClient:
    def __init__(self, settings, store) -> None:
        self.settings, self.store = settings, store

    def key(self) -> str:
        """Designated-organisation key: Admin → APIs → Keys first, then the deployment env file."""
        return self.settings.secret(KEY_NAME)

    # --- budget ---------------------------------------------------------------------------------

    def spend(self, scope: str) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        limit = int(self.settings.get(f"budgets.{scope}_sarvam_calls_per_day", 300))
        if not self.store.take_budget(day, scope, limit):
            raise BudgetExceeded(f"Today's {scope} Sarvam budget ({limit} calls) is used up.")

    def budget(self) -> dict:
        day = datetime.now(timezone.utc).date().isoformat()
        return {scope: {"used": self.store.budget_used(day, scope),
                        "limit": int(self.settings.get(f"budgets.{scope}_sarvam_calls_per_day", 0))}
                for scope in ("public", "operator")}

    @property
    def configured(self) -> bool:
        return bool(self.key())

    def _log(self, api: str, ok: bool, ms: float, note: str, scope: str, run_id: str | None = None) -> None:
        self.store.add_hit(api, {"status": "ok" if ok else "error", "origin": "llm" if api == "sarvam.chat" else "sarvam",
                                 "mode": "live", "ms": round(ms, 1), "note": note[:200], "scope": scope, "run_id": run_id,
                                 "url": api})

    # --- chat (via gateway) -------------------------------------------------------------------------

    async def chat_json(self, system: str, user: str, *, scope: str, max_tokens: int | None = None,
                        temperature: float | None = None, run_id: str | None = None, label: str = "chat") -> dict:
        """Return {"data": parsed JSON, "ms", "model", "usage", "request"} or raise SarvamError/BudgetExceeded."""
        if self.settings.get("llm.organization_id") != ORGANIZATION:
            raise SarvamError("Wrong Sarvam organisation configured; request blocked.")
        if not self.key() or not os.getenv("LLM_INTERNAL_KEY"):
            raise SarvamError("Designated Sarvam key or gateway key is not configured.")
        self.spend(scope)
        model = self.settings.get("llm.model", "sarvam-105b-conversations")
        body = {
            "provider": "custom", "model": model, "base_url": self.settings.get("llm.provider_base_url"),
            "api_key": self.key(), "ref": f"sellergenome-v5-{label}", "system_prompt": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature if temperature is not None else self.settings.get("llm.temperature", 0.2),
            "max_tokens": max_tokens or self.settings.get("llm.max_tokens", 1400), "json_mode": True,
            "extra_body": {"reasoning_effort": self.settings.get("llm.reasoning_effort", "low"),
                           "response_format": {"type": "json_object"}},
        }
        url = os.getenv("LLM_SERVICE_URL", "https://llm.lehana.in").rstrip("/") + "/byok"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.settings.get("llm.timeout_seconds", 40)) as client:
                r = await client.post(url, headers={"X-Internal-Key": os.getenv("LLM_INTERNAL_KEY", "")}, json=body)
                r.raise_for_status()
                result = r.json()
            if result.get("model") not in (None, model):
                raise SarvamError("Unexpected model in response; rejected.")
            raw = result["choices"][0]["message"]["content"]
            finish = result["choices"][0].get("finish_reason")
            if not isinstance(raw, str):
                raise SarvamError("Empty model output.")
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            try:
                data = json.loads(text[text.find("{"): text.rfind("}") + 1]) if "{" in text else {}
            except ValueError as error:
                # 5.10.5: say WHY — a reply cut at max_tokens is the usual cause (a long persona: 13 records, 62 facts).
                ms = (time.monotonic() - started) * 1000
                why = "truncated at max_tokens" if finish == "length" else f"invalid JSON (finish {finish})"
                self._log("sarvam.chat", False, ms, f"{label}: JSONDecodeError · {why} · max_tokens {body['max_tokens']}", scope, run_id)
                raise SarvamError(f"Sarvam chat failed: JSONDecodeError ({why})") from error
            ms = (time.monotonic() - started) * 1000
            self._log("sarvam.chat", True, ms, f"{label}: {model}", scope, run_id)
            return {"data": data, "ms": round(ms, 1), "model": model, "usage": result.get("usage"),
                    "request": {"method": "POST", "url": url + " → api.sarvam.ai/v1/chat/completions", "model": model,
                                "reasoning_effort": body["extra_body"]["reasoning_effort"], "max_tokens": body["max_tokens"],
                                "system_prompt": system, "user": user[:6000], "api_key": "[server-held]"}}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            ms = (time.monotonic() - started) * 1000
            self._log("sarvam.chat", False, ms, f"{label}: {type(error).__name__}", scope, run_id)
            raise SarvamError(f"Sarvam chat failed: {type(error).__name__}") from error

    # --- direct REST ----------------------------------------------------------------------------

    async def _post(self, path: str, body: dict, api: str, scope: str) -> tuple[dict, float]:
        if not self.key():
            raise SarvamError("Designated Sarvam key is not configured.")
        self.spend(scope)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=SARVAM_BASE, timeout=30,
                                         headers={"api-subscription-key": self.key()}) as client:
                r = await client.post(path, json=body)
                r.raise_for_status()
                data = r.json()
            ms = (time.monotonic() - started) * 1000
            self._log(api, True, ms, path, scope)
            return data, ms
        except (httpx.HTTPError, ValueError) as error:
            ms = (time.monotonic() - started) * 1000
            detail = error_detail(error)
            self._log(api, False, ms, f"{path}: {detail}", scope)
            raise SarvamError(f"Sarvam {path} failed — {detail}") from error

    def tts_request(self, text: str, language: str, speaker: str, pace: float, model: str | None = None, fmt: str = "mp3") -> dict:
        """Validated Bulbul parameters shared by REST and streaming: the model from the catalogue, a speaker that model has.

        ``fmt``: ``mp3`` (an <audio> element can play it) or ``pcm`` (raw 24 kHz 16-bit chunks the page plays through Web
        Audio as they arrive — no media-element buffering, ≈0.6 s sooner on a call; measured 2026-10-10).
        """
        model = engines.check("tts_model", model or self.settings.get("engines.tts_model", "bulbul:v4-flash"))
        return {"text": text[:1500], "language": language, "speaker": engines.voice_for(model, speaker),
                "pace": round(max(0.5, min(2.0, float(pace))), 2), "model": model, "format": "pcm" if fmt == "pcm" else "mp3"}

    async def tts(self, text: str, language: str, speaker: str, pace: float, scope: str, model: str | None = None) -> dict:
        p = self.tts_request(text, language, speaker, pace, model)
        data, ms = await self._post("/text-to-speech", {
            "text": p["text"], "target_language_code": language, "speaker": p["speaker"],
            "model": p["model"], "pace": p["pace"], "speech_sample_rate": 24000}, "sarvam.tts", scope)
        return {"audio_base64": (data.get("audios") or [""])[0], "content_type": "audio/wav", "ms": round(ms, 1),
                "speaker": p["speaker"], "pace": p["pace"], "language": language, "model": p["model"], "transport": "rest"}

    async def tts_connect(self, model: str):
        """Open Bulbul's streaming socket ahead of time (≈125 ms) — e.g. while the LLM is still writing the reply."""
        import websockets
        return await websockets.connect(f"wss://api.sarvam.ai/text-to-speech/ws?model={model}&send_completion_event=true",
                                        additional_headers={"api-subscription-key": self.key()}, open_timeout=10, max_size=2 ** 22)

    async def tts_stream(self, p: dict, scope: str, ws=None):
        """Bulbul over Sarvam's streaming WebSocket: yields MP3 chunks as they are made (first audio ≈0.19 s after the text).

        ``p`` comes from :meth:`tts_request`; the budget is taken by the caller when the stream is issued. ``ws`` may be a
        socket already opened with :meth:`tts_connect` for the same model.
        """
        import websockets
        started = time.monotonic()
        first = None
        size = 0
        pre = ws is not None
        try:
            async with (ws or await self.tts_connect(p["model"])) as ws:
                codec = {"output_audio_codec": "linear16"} if p.get("format") == "pcm" else {"output_audio_codec": "mp3", "output_audio_bitrate": "64k"}
                await ws.send(json.dumps({"type": "config", "data": {
                    "language_code": p["language"], "speaker": p["speaker"], "pace": p["pace"], "speech_sample_rate": 24000,
                    "min_buffer_size": 30, "max_chunk_length": 150, **codec}}))
                await ws.send(json.dumps({"type": "text", "data": {"text": p["text"]}}))
                await ws.send(json.dumps({"type": "flush"}))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
                    if msg.get("type") == "audio":
                        chunk = base64.b64decode(msg["data"]["audio"])
                        if first is None:
                            first = (time.monotonic() - started) * 1000
                        size += len(chunk)
                        yield chunk
                    elif msg.get("type") == "event":
                        break
                    elif msg.get("type") == "error":
                        raise SarvamError(str((msg.get("data") or {}).get("message") or msg)[:200])
            self._log("sarvam.tts", True, (time.monotonic() - started) * 1000,
                      f"stream {p['model']} {p['speaker']}: first audio {round(first or 0)} ms, {size} bytes{' (pre-opened)' if pre else ''}", scope)
        except (OSError, ValueError, asyncio.TimeoutError, websockets.WebSocketException, SarvamError) as error:
            self._log("sarvam.tts", False, (time.monotonic() - started) * 1000, f"stream: {error_detail(error)}", scope)
            raise SarvamError(f"Sarvam streaming speech failed — {error_detail(error)}") from error

    async def translate(self, text: str, target: str, scope: str, source: str = "auto", model: str | None = None) -> dict:
        model = engines.check("translate_model", model or self.settings.get("engines.translate_model", "mayura:v1"))
        body = {"input": text[:1000], "source_language_code": source, "target_language_code": target, "model": model}
        if model == "mayura:v1":
            body["mode"] = "modern-colloquial"  # Mayura's register options; Sarvam Translate is formal only
        data, ms = await self._post("/translate", body, "sarvam.translate", scope)
        return {"text": data.get("translated_text", ""), "source": data.get("source_language_code"), "ms": round(ms, 1), "model": model}

    async def transliterate(self, text: str, language: str, scope: str) -> dict:
        data, ms = await self._post("/transliterate", {"input": text[:1000], "source_language_code": language,
                                                       "target_language_code": language}, "sarvam.transliterate", scope)
        return {"text": data.get("transliterated_text", ""), "ms": round(ms, 1)}

    async def identify(self, text: str, scope: str) -> dict:
        data, ms = await self._post("/text-lid", {"input": text[:1000]}, "sarvam.langid", scope)
        return {"language": data.get("language_code"), "script": data.get("script_code"), "ms": round(ms, 1)}

    async def stt(self, content: bytes, filename: str, mime: str, scope: str, language: str = "unknown",
                  model: str | None = None, mode: str | None = None) -> dict:
        if not self.key():
            raise SarvamError("Designated Sarvam key is not configured.")
        model = engines.check("stt_model", model or self.settings.get("engines.stt_model", "saaras:v4"))
        mode = engines.check("stt_mode", mode or self.settings.get("engines.stt_mode", "codemix"))
        form = {"model": model, "language_code": language}
        if model.startswith("saaras"):
            form["mode"] = mode  # Saarika has no output modes
        self.spend(scope)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=SARVAM_BASE, timeout=40,
                                         headers={"api-subscription-key": self.key()}) as client:
                r = await client.post("/speech-to-text", files={"file": (filename, content, stt_mime(mime, filename))}, data=form)
                r.raise_for_status()
                data = r.json()
            ms = (time.monotonic() - started) * 1000
            self._log("sarvam.stt", True, ms, f"speech-to-text {model} {form.get('mode', '')}".strip(), scope)
            return {"text": data.get("transcript", ""), "language": data.get("language_code"), "ms": round(ms, 1), "model": model,
                    "mode": form.get("mode")}
        except (httpx.HTTPError, ValueError) as error:
            ms = (time.monotonic() - started) * 1000
            detail = error_detail(error)
            self._log("sarvam.stt", False, ms, f"speech-to-text: {detail}", scope)
            raise SarvamError(f"Sarvam speech-to-text failed — {detail}") from error
