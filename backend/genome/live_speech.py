"""Realtime speech for the browser (5.3): streamed Bulbul audio and streaming Saaras listening.

The browser never holds a Sarvam key. It asks the API for a single-use ticket (rate-limited, budget taken), then:

- **Streamed speech** — ``GET /api/v5/speak/stream/{ticket}.mp3`` relays Bulbul's WebSocket MP3 chunks as they are
  made, so an ``<audio>`` element starts playing after the first chunk (≈0.3 s) instead of after the whole line.
- **Realtime listening** — ``WS /api/v5/listen/realtime`` carries 16 kHz PCM from the microphone to Sarvam's
  ``speech-to-text-realtime`` socket and relays its events back: ``vad.speech_start`` (the page stops the bot's audio —
  barge-in), ``transcript.partial`` (words while you speak) and ``transcript.final`` (the turn).

Protocols are Sarvam's (``sarvamai`` SDK 0.1.36, read 2026-10-09): TTS ``config`` → ``text`` → ``flush`` → ``audio``…
``event:final``; realtime STT ``audio_input`` (base64 PCM, ≤16 000 bytes per frame on ``fast``) → partial/final events.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from urllib.parse import urlencode

RELAYED = {"session.begin", "vad.speech_start", "vad.speech_end", "transcript.partial", "transcript.final", "error", "session.end",
           "config.updated"}
MAX_FRAME = 16000


class Tickets:
    """Short-lived, single-purpose tokens that carry already-validated parameters from a POST to a GET/WebSocket."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def issue(self, kind: str, params: dict, ttl: float = 60, uses: int = 1) -> str:
        now = time.monotonic()
        self.items = {k: v for k, v in self.items.items() if v["expires"] > now}
        token = secrets.token_urlsafe(18)
        self.items[token] = {"kind": kind, "params": params, "expires": now + ttl, "uses": uses}
        return token

    def take(self, token: str, kind: str) -> dict | None:
        item = self.items.get(token or "")
        if not item or item["kind"] != kind or item["expires"] <= time.monotonic():
            return None
        item["uses"] -= 1
        if item["uses"] <= 0:
            self.items.pop(token, None)
        return item["params"]


class AudioBuffer:
    """Bulbul's streamed chunks, started when the link is issued so audio is already flowing when the browser asks.

    Every reader replays from the first chunk (Safari may fetch an audio URL twice) and then follows live chunks.
    """

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.done = False
        self.error: Exception | None = None
        self.changed = asyncio.Condition()
        self.task: asyncio.Task | None = None

    def start(self, chunks) -> "AudioBuffer":
        self.task = asyncio.create_task(self._fill(chunks))
        return self

    async def _fill(self, chunks) -> None:
        try:
            async for chunk in chunks:
                async with self.changed:
                    self.chunks.append(chunk)
                    self.changed.notify_all()
        except Exception as error:  # noqa: BLE001 — surfaced to the reader as a 503 before any bytes
            self.error = error
        finally:
            async with self.changed:
                self.done = True
                self.changed.notify_all()

    async def read(self):
        i = 0
        while True:
            async with self.changed:
                while i >= len(self.chunks) and not self.done:
                    await self.changed.wait()
                if i >= len(self.chunks):
                    return
                chunk = self.chunks[i]
            i += 1
            yield chunk


def realtime_query(settings, params: dict) -> str:
    """Query string for Sarvam's realtime STT socket from validated params plus the Admin VAD settings."""
    rt = settings.get("engines.realtime", {}) or {}
    q = {"language_code": params.get("language") or "hi-IN", "model": params["model"], "mode": params["mode"],
         "encoding": "linear16", "sample_rate": "16000", "stream_type": rt.get("stream_type", "fast"),
         "silence_duration_ms": str(int(rt.get("silence_duration_ms", 700))), "threshold": str(rt.get("threshold", 0.5)),
         "min_speech_duration_ms": str(int(rt.get("min_speech_duration_ms", 250))),
         "prefix_padding_ms": str(int(rt.get("prefix_padding_ms", 300)))}
    terms = [str(t)[:64] for t in (params.get("keyterms") if "keyterms" in params else settings.get("engines.keyterms") or [])][:50]
    if params["model"] == "saaras:v4" and terms:
        q["keyterms"] = json.dumps(terms, ensure_ascii=False)  # keyterms are a saaras:v4 feature
    return urlencode(q)


def _client_note(c: dict) -> str:
    """One line from the page's microphone check — enough to tell a paused audio engine from a silent or muted mic."""
    t = c.get("track") or {}
    ua = str(c.get("ua") or "")
    browser = next((b for b in ("Edg/", "Firefox/", "Chrome/", "Safari/") if b in ua), "?").rstrip("/")
    return (f"{c.get('frames')} frames, peak {c.get('peak')}, {c.get('rate')} Hz, audio engine {c.get('ctx')}, "
            f"mic {'muted' if t.get('muted') else t.get('state') or '?'}, {browser}")


async def relay_realtime_stt(browser, sarvam_client, settings, params: dict, scope: str) -> dict:
    """Pump microphone frames to Sarvam and Sarvam's events back until either side stops or the time cap passes.

    ``browser`` is a Starlette WebSocket already accepted. Binary frames are 16-bit PCM mono at 16 kHz; text frames
    may be ``{"event": "flush"}`` (end the current turn now) or ``{"event": "end"}``.
    """
    import websockets
    from backend.genome.sarvam import error_detail
    started = time.monotonic()
    limit = float((settings.get("engines.realtime", {}) or {}).get("max_seconds", 90))
    stats = {"frames": 0, "partials": 0, "finals": 0, "first_partial_ms": None, "error": None}
    url = "wss://api.sarvam.ai/speech-to-text-realtime/ws?" + realtime_query(settings, params)
    try:
        async with websockets.connect(url, additional_headers={"api-subscription-key": sarvam_client.key()}, open_timeout=10) as up:

            async def upstream():
                while time.monotonic() - started < limit:
                    msg = await browser.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    if msg.get("bytes"):
                        frame = msg["bytes"]
                        for i in range(0, len(frame), MAX_FRAME):
                            await up.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(frame[i:i + MAX_FRAME]).decode()}))
                        stats["frames"] += 1
                    elif msg.get("text"):
                        try:
                            control = json.loads(msg["text"]).get("event")
                        except (ValueError, AttributeError):
                            control = None
                        if control == "stats":  # the page's own microphone check (frames, level, audio engine state)
                            try:
                                c = json.loads(msg["text"])
                                stats["client"] = {k: c.get(k) for k in ("frames", "peak", "rate", "ctx", "track", "ua")}
                            except (ValueError, AttributeError):
                                pass
                        elif control == "flush":
                            await up.send(json.dumps({"event": "flush"}))
                        elif control == "end":
                            await up.send(json.dumps({"event": "end"}))
                            return

            async def downstream():
                async for raw in up:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    kind = event.get("event")
                    if kind not in RELAYED:
                        continue
                    if kind == "transcript.partial":
                        stats["partials"] += 1
                        if stats["first_partial_ms"] is None:
                            stats["first_partial_ms"] = round((time.monotonic() - started) * 1000)
                    elif kind == "transcript.final":
                        stats["finals"] += 1
                    elif kind == "error":
                        stats["error"] = str(event.get("message", ""))[:200]
                    out = {k: event.get(k) for k in ("event", "text", "utterance_idx", "language", "language_confidence", "message",
                                                     "is_fatal", "code") if event.get(k) is not None}
                    await browser.send_text(json.dumps(out, ensure_ascii=False))

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            done, pending = await asyncio.wait(tasks, timeout=limit + 5, return_when=asyncio.FIRST_COMPLETED)
            if tasks[0] in done and not tasks[1].done():
                # The browser said "end" or left: give Sarvam a moment to send the last final transcript.
                await asyncio.wait({tasks[1]}, timeout=2.5)
            for t in tasks:
                if not t.done():
                    t.cancel()
    except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as error:
        stats["error"] = error_detail(error)
        try:
            await browser.send_text(json.dumps({"event": "error", "message": "Sarvam realtime listening is unavailable right now.",
                                                "is_fatal": True}))
        except Exception:  # noqa: BLE001 — the browser may already be gone
            pass
    ms = (time.monotonic() - started) * 1000
    sarvam_client._log("sarvam.stt", stats["error"] is None, ms,
                       f"realtime {params['model']} {params['mode']}: {stats['finals']} turns, {stats['partials']} partials, "
                       f"first partial {stats['first_partial_ms']} ms · {stats['frames']} frames from the browser"
                       + (f" · page check: {_client_note(stats['client'])}" if stats.get("client") else "")
                       + (f" · {stats['error']}" if stats["error"] else ""), scope)
    return stats
