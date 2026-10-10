"""Minimal client for the SellerGenome voice plug-in API (/api/v5/voice)."""

from __future__ import annotations

import os

import httpx


class GenomeVoiceClient:
    def __init__(self, base_url: str | None = None, key: str | None = None, timeout: float = 30) -> None:
        self.base = (base_url or os.environ["SELLERGENOME_URL"]).rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout,
                                 headers={"X-Voice-Plugin-Key": key or os.environ["VOICE_PLUGIN_KEY"]})
        self.session_id: str | None = None
        self.revision = 0

    def register(self, glid: str, provider: str = "livekit", direction: str = "seller_outbound", room: str | None = None) -> dict:
        """Start (or reuse) context preparation for this seller and open a session."""
        r = self.http.post("/api/v5/voice/sessions", json={"glid": glid, "provider": provider, "direction": direction, "room": room})
        r.raise_for_status()
        session = r.json()
        self.session_id, self.revision = session["id"], session["context_revision"]
        return session

    def context(self) -> dict:
        """Persona prompt sections, voice, delivery, teleprompter and switch rules."""
        r = self.http.get(f"/api/v5/voice/sessions/{self.session_id}/context")
        r.raise_for_status()
        data = r.json()
        self.revision = data["context_revision"]
        return data

    def events(self, *events: dict) -> dict:
        """Push transcript/signal/switch/mood/tool/outcome/ended events. Returns the latest context revision."""
        r = self.http.post(f"/api/v5/voice/sessions/{self.session_id}/events", json={"events": list(events)})
        r.raise_for_status()
        return r.json()

    def changed(self, ack: dict) -> bool:
        """True when SellerGenome bumped the context (e.g. the seller asked for another language)."""
        return ack.get("context_revision", self.revision) != self.revision
