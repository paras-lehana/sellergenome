"""Owner-bound, bounded duplex broker with VAD barge-in and real streaming audio."""

import asyncio
import base64
import hashlib
import json
import secrets
import time

from fastapi import HTTPException, WebSocketDisconnect

from backend.provider_errors import ProviderUnavailable
from backend.sarvam import quota_error


class LiveService:
    def __init__(self, config, providers):
        self.config, self.pool = config, providers.sarvam
        self.tickets = {}
        self.active = set()

    def grant(
        self, identifier, owner, speaker=None, *, profile=None, pitch=None
    ):
        now = time.monotonic()
        self.tickets = {
            key: value for key, value in self.tickets.items() if value[2] > now
        }
        if len(self.tickets) >= 100:
            raise HTTPException(429, "Live connection ticket limit reached.")
        token = secrets.token_urlsafe(32)
        self.tickets[hashlib.sha256(token.encode()).hexdigest()] = (
            identifier,
            owner,
            now + self.config.values["voice"]["live_ticket_seconds"],
            speaker,
            profile or self.config.values["voice"].get("tts_profile", "v3"),
            pitch,
        )
        return {
            "ticket": token,
            "expires_in": self.config.values["voice"]["live_ticket_seconds"],
            "sample_rate": self.config.values["voice"]["live_sample_rate"],
            "encoding": "linear16",
            "max_seconds": self.config.values["voice"]["live_max_seconds"],
            "profile": profile
            or self.config.values["voice"].get("tts_profile", "v3"),
            "pitch": pitch,
        }

    def consume(self, token, identifier, owner):
        grant = self.tickets.pop(
            hashlib.sha256(token.encode()).hexdigest(), None
        )
        if (
            grant
            and grant[0] == identifier
            and grant[1] == owner
            and grant[2] > time.monotonic()
        ):
            return {
                "speaker": grant[3],
                "profile": grant[4],
                "pitch": grant[5],
            }
        return False

    async def run(
        self,
        websocket,
        identifier,
        owner,
        process_turn,
        read_session,
        speaker,
        *,
        profile=None,
        pitch=None,
    ):
        voice = self.config.values["voice"]
        if (
            identifier in self.active
            or len(self.active) >= voice["live_max_connections"]
        ):
            await websocket.send_json(
                {
                    "event": "error",
                    "message": "Live connection already active or capacity reached.",
                }
            )
            return
        self.active.add(identifier)
        send_lock = asyncio.Lock()
        response_task = None
        generation = 0
        deadline = time.monotonic() + voice["live_max_seconds"]
        started = time.monotonic()
        audio_bytes = 0
        finals = 0
        speech_end_at = None
        recognition_ms = None
        timing_receipts = []

        async def send(event):
            async with send_lock:
                await websocket.send_json(event)

        def record_timing(receipt):
            timing_receipts.append(receipt)
            if hasattr(self.pool, "store"):
                self.pool.store.put(
                    "metadata",
                    "live-timings-" + identifier,
                    {
                        "session_id": identifier,
                        "owner": owner,
                        "items": timing_receipts[-100:],
                    },
                )

        async def respond(text, epoch, opening=False, language_hint=None):
            start = time.monotonic()
            measured_recognition = recognition_ms
            model_ms = None
            turn_processing_ms = None
            try:
                if opening:
                    session = read_session()
                    reply, persona = (
                        session["turns"][0]["text"],
                        session["persona"],
                    )
                else:
                    result = (
                        await process_turn(text, language_hint=language_hint)
                        if language_hint
                        else await process_turn(text)
                    )
                    turn_processing_ms = round(
                        (time.monotonic() - start) * 1000
                    )
                    model_ms = result.get("timings", {}).get("model_ms")
                    if epoch != generation:
                        return
                    await send(
                        {
                            "event": "turn",
                            "generation": epoch,
                            "result": result,
                        }
                    )
                    reply, persona = result["reply"], result["persona"]
                    if result["status"] != "active":
                        return
                language = persona["fields"]["language"]["value"]
                pace = persona["fields"]["pace"]["value"]
                first = True
                async for chunk in self.pool.tts_stream(
                    reply,
                    language,
                    speaker,
                    pace,
                    profile=profile,
                    pitch=pitch,
                ):
                    if epoch != generation:
                        return
                    if first:
                        receipt = {
                            "generation": epoch,
                            "recognition_ms": measured_recognition,
                            "recognition_definition": "provider_speech_end_to_final_receipt",
                            "model_ms": model_ms,
                            "turn_processing_ms": turn_processing_ms,
                            "first_audio_ms": round(
                                (time.monotonic() - start) * 1000
                            ),
                            "first_audio_definition": "final_transcript_to_server_first_audio_chunk",
                            "hardware_playback_measured": False,
                        }
                        record_timing(receipt)
                        await send(
                            {
                                "event": "latency",
                                "generation": epoch,
                                "stage_timings": receipt,
                                "response_audio_ms": round(
                                    (time.monotonic() - start) * 1000
                                ),
                            }
                        )
                        first = False
                    await send(
                        {"event": "audio", "generation": epoch, **chunk}
                    )
                await send({"event": "audio.end", "generation": epoch})
            except asyncio.CancelledError:
                raise
            except ProviderUnavailable as error:
                await send(
                    {
                        "event": "error",
                        "message": str(error),
                        "stage": "reply_audio",
                    }
                )
            except (TimeoutError, ValueError, HTTPException) as error:
                message = (
                    error.detail
                    if isinstance(error, HTTPException)
                    else "Live response timed out or failed validation."
                )
                await send(
                    {"event": "error", "message": message, "stage": "response"}
                )

        async def interrupt():
            nonlocal generation
            dispatched = time.monotonic()
            generation += 1
            if response_task and not response_task.done():
                response_task.cancel()
            latency = round((time.monotonic() - dispatched) * 1000, 3)
            record_timing(
                {
                    "generation": generation,
                    "interruption_dispatch_ms": latency,
                    "hardware_stop_measured": False,
                }
            )
            await send(
                {
                    "event": "interruption",
                    "generation": generation,
                    "interruption_dispatch_ms": latency,
                }
            )

        async def attempt():
            nonlocal response_task, audio_bytes, finals
            params = {
                "model": voice["realtime_model"],
                "language_code": "auto",
                "stream_type": "fast",
                "mode": "transcribe",
                "encoding": "linear16",
                "sample_rate": voice["live_sample_rate"],
                "endpointing": "vad",
                "silence_duration_ms": voice["vad_silence_ms"],
                "min_speech_duration_ms": voice["vad_min_speech_ms"],
                "threshold": voice["vad_threshold"],
                "return_timestamps": "true",
            }
            async with self.pool.websocket(
                "/speech-to-text-realtime/ws", params
            ) as (socket, slot):
                await send(
                    {
                        "event": "ready",
                        "generation": generation,
                        "slot": slot,
                        "model": voice["realtime_model"],
                        "max_seconds": voice["live_max_seconds"],
                    }
                )
                if response_task is None:
                    response_task = asyncio.create_task(
                        respond("", generation, opening=True)
                    )

                async def browser():
                    nonlocal audio_bytes
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        content = message.get("bytes")
                        if content is not None:
                            if (
                                not content
                                or len(content) > voice["live_frame_bytes"]
                                or len(content) % 2
                            ):
                                raise ValueError("Invalid PCM frame")
                            audio_bytes += len(content)
                            duration = audio_bytes / (
                                voice["live_sample_rate"] * 2
                            )
                            if (
                                duration > time.monotonic() - started + 3
                                or duration > voice["live_max_seconds"]
                            ):
                                raise ValueError("Audio rate limit")
                            await socket.send(
                                json.dumps(
                                    {
                                        "event": "audio_input",
                                        "audio": base64.b64encode(
                                            content
                                        ).decode(),
                                    }
                                )
                            )
                        elif message.get("text"):
                            if len(message["text"]) > 200:
                                raise ValueError("Invalid live control")
                            control = json.loads(message["text"])
                            if control.get("event") == "end":
                                await socket.send(json.dumps({"event": "end"}))
                                return
                            if control.get("event") == "interrupt":
                                await interrupt()
                            elif control.get("event") == "flush":
                                await socket.send(
                                    json.dumps({"event": "flush"})
                                )

                async def provider():
                    nonlocal \
                        response_task, \
                        finals, \
                        speech_end_at, \
                        recognition_ms
                    async for raw in socket:
                        event = json.loads(raw)
                        kind = event.get("event")
                        if kind == "vad.speech_start":
                            await interrupt()
                            await send({"event": kind})
                        elif kind == "transcript.partial":
                            await send(
                                {
                                    "event": kind,
                                    "text": str(event.get("text", ""))[:2000],
                                    "language": event.get("language_code"),
                                }
                            )
                        elif kind == "transcript.final":
                            recognition_ms = (
                                round(
                                    (time.monotonic() - speech_end_at) * 1000
                                )
                                if speech_end_at is not None
                                else None
                            )
                            speech_end_at = None
                            text = str(event.get("text", "")).strip()[:2000]
                            if not text:
                                continue
                            finals += 1
                            if (
                                finals
                                > self.config.values["persona"]["max_turns"]
                            ):
                                raise ValueError("Turn limit")
                            await send(
                                {
                                    "event": kind,
                                    "text": text,
                                    "language": event.get("language_code"),
                                    "start_s": event.get("start_s"),
                                    "end_s": event.get("end_s"),
                                    "recognition_ms": recognition_ms,
                                }
                            )
                            if response_task and not response_task.done():
                                response_task.cancel()
                                await asyncio.gather(
                                    response_task, return_exceptions=True
                                )
                            response_task = asyncio.create_task(
                                respond(
                                    text,
                                    generation,
                                    language_hint=event.get("language_code"),
                                )
                            )
                        elif kind == "error":
                            if quota_error(None, event):
                                self.pool.mark_exhausted(slot)
                                raise ProviderUnavailable(
                                    "Streaming quota exhausted; switching to the funded backup slot.",
                                    "sarvam",
                                    402,
                                )
                            raise ProviderUnavailable(
                                "Sarvam realtime recognition failed. No transcript was substituted.",
                                "sarvam",
                            )
                        elif kind in {
                            "vad.speech_end",
                            "session.begin",
                            "session.end",
                        }:
                            if kind == "vad.speech_end":
                                speech_end_at = time.monotonic()
                            await send(
                                {
                                    key: value
                                    for key, value in event.items()
                                    if key
                                    in {
                                        "event",
                                        "audio_duration_s",
                                        "billed_audio_duration_s",
                                    }
                                }
                            )

                tasks = [
                    asyncio.create_task(browser()),
                    asyncio.create_task(provider()),
                ]
                try:
                    done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

        try:
            try:
                await attempt()
            except ProviderUnavailable as error:
                if (
                    error.code == 402
                    and self.pool.slots()
                    and time.monotonic() < deadline
                ):
                    await send(
                        {
                            "event": "fallback",
                            "message": "Quota exhausted; continuing with backup.",
                        }
                    )
                    await attempt()
                else:
                    raise
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ProviderUnavailable, ValueError, TimeoutError) as error:
            try:
                message = (
                    str(error)
                    if isinstance(error, ProviderUnavailable)
                    else "Live stream reached a limit or failed validation."
                )
                await send({"event": "error", "message": message})
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            if response_task:
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
            self.active.discard(identifier)
