"""Shared, persistent Sarvam account pool. Never log keys or provider bodies."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, ConnectionClosed

from backend.provider_errors import ProviderUnavailable
from backend.store import Store
from backend.persona import utcnow
from backend.admin_config import secret_value, ensure_secret_version
from backend.voice_profiles import CAPABILITIES, tts_parameters


def quota_error(status: int | None, body: dict) -> bool:
    """Ordinary rate limits/auth errors must not silently spend another account."""
    if status == 402:
        return True
    return any(
        isinstance(error, dict)
        and error.get("code", error.get("type")) == "insufficient_quota_error"
        for error in [body, body.get("error"), body.get("data")]
    )


class SarvamPool:
    def __init__(self, config, store=None):
        self.config = config
        self.store = store or Store(config.db_path)
        self.keys = {
            "primary": config.sarvam_key,
            "backup": config.sarvam_backup_key,
        }
        self.refresh_keys()
        self.lock = asyncio.Lock()
        self.config.voice_capability_receipts = {
            model: receipt
            for model in CAPABILITIES
            if (
                receipt := self.store.get(
                    "metadata", "voice-capability-" + model
                )
            )
        }

    def refresh_keys(self):
        if os.getenv('SARVAM_SPEECH_SCOPE') == 'designated_v4':
            # New rehearsals use only the hackathon credential. credential(...,
            # version=...) below still resolves old jobs' immutable references.
            self.keys = {'primary':secret_value('SARVAM_V4_API_KEY'),'backup':''}
            return
        self.keys = {
            "primary": self.config.sarvam_key,
            "backup": self.config.sarvam_backup_key,
        }

    def credential(self, slot, version=None):
        if version:
            name = (
                "SARVAM_API_KEY"
                if slot == "primary"
                else "SARVAM_BACKUP_API_KEY"
            )
            value = secret_value(name, version)
            if not value:
                raise ProviderUnavailable(
                    "This speech job's pinned credential version is unavailable; no other account was used.",
                    "sarvam",
                    401,
                )
            return value
        self.refresh_keys()
        return self.keys.get(slot, "")

    def version(self, slot, key):
        name = (
            "SARVAM_API_KEY" if slot == "primary" else "SARVAM_BACKUP_API_KEY"
        )
        version = ensure_secret_version(name)
        return (
            version if version and secret_value(name, version) == key else None
        )

    def fingerprint(self, slot, version=None):
        return hashlib.sha256(
            self.credential(slot, version).encode()
        ).hexdigest()

    def exhausted(self, slot):
        version_saved = (
            self.store.get(
                "metadata", "sarvam-exhausted-" + self.fingerprint(slot)
            )
            or {}
        )
        if version_saved.get("exhausted") is True:
            return True
        saved = self.store.get("metadata", "sarvam-slot-" + slot) or {}
        return (
            saved.get("fingerprint") == self.fingerprint(slot)
            and saved.get("exhausted") is True
        )

    def mark_exhausted(self, slot, key=None):
        fingerprint = (
            hashlib.sha256(key.encode()).hexdigest()
            if key
            else self.fingerprint(slot)
        )
        self.store.put(
            "metadata", "sarvam-exhausted-" + fingerprint, {"exhausted": True}
        )
        self.store.put(
            "metadata",
            "sarvam-slot-" + slot,
            {
                "fingerprint": fingerprint,
                "exhausted": True,
            },
        )

    def slots(self):
        self.refresh_keys()
        return [
            slot
            for slot, key in self.keys.items()
            if key and not self.exhausted(slot)
        ]

    def public(self):
        return {
            "active_slot": next(iter(self.slots()), None),
            "slots": {
                slot: {
                    "configured": bool(key),
                    "exhausted": self.exhausted(slot) if key else False,
                }
                for slot, key in self.keys.items()
            },
        }

    def record_capability(
        self, model, status, reason, slot, key, transport="rest"
    ):
        if model not in CAPABILITIES:
            return
        receipt = {
            "model": model,
            "status": status,
            "reason": reason,
            "checked_at": utcnow(),
            "credential_slot": slot,
            "credential_fingerprint": hashlib.sha256(key.encode()).hexdigest(),
            "transport": transport,
        }
        self.store.put("metadata", "voice-capability-" + model, receipt)
        self.config.voice_capability_receipts[model] = receipt

    def capability_error(self, body, model, slot, key, transport="rest"):
        if not isinstance(body, dict) or model not in CAPABILITIES:
            return None
        nested = body.get("error", {})
        message = (
            str(
                nested.get("message", "")
                if isinstance(nested, dict)
                else nested
            )
            + " "
            + str(body.get("message", ""))
        )
        if "deprecated" in message.casefold():
            reason = "Sarvam reports that this model is deprecated. Bulbul v3 remains available, but it does not support pitch. No voice or model was substituted."
            self.record_capability(
                model, "deprecated", reason, slot, key, transport
            )
            return reason
        if "beta" in message.casefold() and (
            "not available" in message.casefold()
            or "access" in message.casefold()
        ):
            reason = "Sarvam requires beta access for this model on the configured account. No audio was generated or model substituted; an administrator must obtain provider access."
            self.record_capability(
                model, "beta_access_required", reason, slot, key, transport
            )
            return reason
        return None

    async def request(
        self,
        method,
        path,
        *,
        slot=None,
        key_version=None,
        return_pin=False,
        **kwargs,
    ):
        """Pin existing jobs to their account; only new requests can fail over."""
        candidates = [slot] if slot else self.slots()
        for selected in candidates:
            key = self.credential(selected, key_version)
            if not key:
                break
            version = key_version or (
                self.version(selected, key) if return_pin else None
            )
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    result = await client.request(
                        method,
                        self.config.values["voice"]["base_url"] + path,
                        headers={"api-subscription-key": key},
                        **kwargs,
                    )
                    try:
                        body = result.json()
                    except ValueError:
                        body = {}
                    if quota_error(result.status_code, body):
                        self.mark_exhausted(selected, key)
                        if slot:
                            raise ProviderUnavailable(
                                "The account for this speech job has exhausted its quota. Existing job IDs cannot move accounts.",
                                "sarvam",
                                402,
                            )
                        continue
                    if result.is_error:
                        reason = self.capability_error(
                            body,
                            kwargs.get("json", {}).get("model"),
                            selected,
                            key,
                        )
                        raise ProviderUnavailable(
                            reason
                            or "Sarvam rejected this request. Check model, format or account access; no result was substituted.",
                            "sarvam",
                            result.status_code,
                        )
                    if not isinstance(body, dict):
                        raise ProviderUnavailable(
                            "Sarvam returned an invalid result.", "sarvam"
                        )
                    if path == "/text-to-speech" and body.get("audios"):
                        self.record_capability(
                            kwargs.get("json", {}).get("model"),
                            "available",
                            "Provider returned audio for this model; hardware acoustics were not assessed.",
                            selected,
                            key,
                        )
                    return body, (
                        {
                            "slot": selected,
                            "version": version,
                            "fingerprint": hashlib.sha256(
                                key.encode()
                            ).hexdigest(),
                        }
                        if return_pin
                        else selected
                    )
            except httpx.HTTPError as error:
                raise ProviderUnavailable(
                    "Sarvam could not be reached. Retry later; no result was substituted.",
                    "sarvam",
                ) from error
        raise ProviderUnavailable(
            "No funded Sarvam slot is available. Rotate the private key configuration.",
            "sarvam",
            402,
        )

    @asynccontextmanager
    async def websocket(self, path, params):
        """Connect once, with quota-aware handshake failover. No blind reconnect billing."""
        socket = None
        selected = None
        for selected in self.slots():
            try:
                uri = (
                    self.config.values["voice"]["base_url"].replace(
                        "https://", "wss://"
                    )
                    + path
                    + "?"
                    + urlencode(params)
                )
                key = self.credential(selected)
                socket = await connect(
                    uri,
                    additional_headers={"api-subscription-key": key},
                    open_timeout=15,
                    close_timeout=3,
                    max_size=2**20,
                    max_queue=8,
                    proxy=None,
                )
                break
            except InvalidStatus as error:
                status = error.response.status_code
                try:
                    body = json.loads(error.response.body)
                except (ValueError, TypeError):
                    body = {}
                if quota_error(status, body):
                    self.mark_exhausted(selected)
                    continue
                reason = self.capability_error(
                    body, params.get("model"), selected, key, "websocket"
                )
                raise ProviderUnavailable(
                    reason
                    or "Sarvam streaming handshake failed. Check realtime model access.",
                    "sarvam",
                    status,
                ) from error
            except (OSError, TimeoutError) as error:
                raise ProviderUnavailable(
                    "Sarvam streaming connection failed.", "sarvam"
                ) from error
        if socket is None:
            raise ProviderUnavailable(
                "No funded Sarvam slot is available for streaming.",
                "sarvam",
                402,
            )
        try:
            yield socket, selected
        except ConnectionClosed as error:
            raise ProviderUnavailable(
                "Sarvam closed the speech stream. Start a new live rehearsal connection.",
                "sarvam",
            ) from error
        finally:
            await socket.close()

    async def tts_stream(
        self, text, language, speaker, pace, *, pitch=None, profile=None
    ):
        sent = False
        for attempt in range(2):
            try:
                async for chunk in self._tts_stream(
                    text, language, speaker, pace, pitch=pitch, profile=profile
                ):
                    sent = True
                    yield chunk
                return
            except ProviderUnavailable as error:
                if (
                    error.code == 402
                    and not sent
                    and attempt == 0
                    and self.slots()
                ):
                    continue
                raise

    async def _tts_stream(
        self, text, language, speaker, pace, *, pitch=None, profile=None
    ):
        audio_seen = False
        params = tts_parameters(
            self.config,
            text,
            language,
            speaker,
            pace,
            pitch=pitch,
            profile=profile,
            streaming=True,
        )
        async with self.websocket(
            "/text-to-speech/ws",
            {"model": params["model"], "send_completion_event": "true"},
        ) as (socket, slot):
            await socket.send(
                json.dumps(
                    {
                        "type": "config",
                        "data": {
                            **{
                                key: value
                                for key, value in params.items()
                                if key != "text"
                            },
                            "speech_sample_rate": str(
                                params["speech_sample_rate"]
                            ),
                            "output_audio_codec": "mp3",
                        },
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "type": "text",
                        "data": {"text": params["text"]},
                    }
                )
            )
            await socket.send(json.dumps({"type": "flush"}))
            async with asyncio.timeout(60):
                async for raw in socket:
                    event = json.loads(raw)
                    if event.get("type") == "audio":
                        if not audio_seen:
                            self.record_capability(
                                params["model"],
                                "available",
                                "Provider returned streaming audio chunks; hardware acoustics were not assessed.",
                                slot,
                                self.credential(slot),
                                "websocket",
                            )
                            audio_seen = True
                        self.store.put(
                            "metadata",
                            "provider-sarvam",
                            {
                                "status": "verified",
                                "at": utcnow(),
                                "message": "Actual Sarvam streaming speech returned MP3 audio chunks.",
                            },
                        )
                        yield {
                            "audio": event["data"]["audio"],
                            "content_type": "audio/mpeg",
                            "slot": slot,
                            "model": params["model"],
                            "pitch": params.get("pitch"),
                        }
                    elif (
                        event.get("type") == "event"
                        and event.get("data", {}).get("event_type") == "final"
                    ):
                        return
                    elif event.get("type") == "error":
                        exhausted = quota_error(None, event)
                        if exhausted:
                            self.mark_exhausted(slot)
                        reason = self.capability_error(
                            event,
                            params["model"],
                            slot,
                            self.credential(slot),
                            "websocket",
                        )
                        raise ProviderUnavailable(
                            reason
                            or "Sarvam streaming synthesis failed; no audio was fabricated.",
                            "sarvam",
                            402 if exhausted else None,
                        )
