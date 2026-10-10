"""Wires every V5 component together; created once in the app lifespan."""

from __future__ import annotations

import os
from pathlib import Path

from backend.genome.buyer import BuyerDesk
from backend.genome.conversation import ConversationEngine
from backend.genome.elastic import GenomeElastic
from backend.genome.orchestrator import Orchestrator
from backend.genome.persona_index import PersonaIndex
from backend.genome.registry import ApiExecutor, ApiRegistry
from backend.genome.sarvam import SarvamClient
from backend.genome.settings import GenomeSettings
from backend.genome.store import GenomeStore
from backend.genome import prompts
from backend.genome.voice_plugin import VoicePlugin
from backend.genome.sarvam_buyer import BuyerVoiceAgent
from backend.genome.sarvam_voice import SarvamVoice


class Genome:
    def __init__(self, data_dir: Path, v4_config=None, v4_store=None) -> None:
        self.settings = GenomeSettings()
        self.store = GenomeStore(data_dir / "v5.sqlite3")
        prompts.bind(self.store)  # Admin → Prompts edits reach every call site
        self.elastic = GenomeElastic(self.settings, self.store)
        self.registry = ApiRegistry(self.settings)
        self.v4_config, self.v4_store = v4_config, v4_store
        self.executor = ApiExecutor(self.settings, self.registry, self.elastic, self.store, approved=self._approved,
                                    redash=self._redash if v4_config else None)
        self.sarvam = SarvamClient(self.settings, self.store)
        self.persona_index = PersonaIndex(self.elastic, self.store)
        self.orchestrator = Orchestrator(self.settings, self.store, self.elastic, self.executor, self.sarvam,
                                         self.persona_index, private_hints=self._hints)
        self.conversation = ConversationEngine(self.settings, self.store, self.sarvam)
        self.buyer = BuyerDesk(self.settings, self.store, self.sarvam, self.executor)
        self.voice = VoicePlugin(self.settings, self.store)
        self.sarvam_voice = SarvamVoice(self.settings, self.store)
        self.sarvam_buyer = BuyerVoiceAgent(self.settings, self.store, self.sarvam_voice)
        from backend.genome.sarvam_agents import ManjulaV2
        self.manjula_v2 = ManjulaV2(self.sarvam_voice)

    async def start(self) -> None:
        await self.elastic.start()

    async def stop(self) -> None:
        await self.elastic.stop()

    # --- adapters to V4 code we reuse -------------------------------------------------------------

    def glid_access(self) -> str:
        """upstream.glid_access (D51): "prod" lets any numeric GLID reach the live APIs; anything else is "dev"."""
        return "prod" if self.settings.get("upstream.glid_access", "dev") == "prod" else "dev"

    def _approved(self, glid: str) -> bool:
        """May this GLID call live upstream APIs? dev: the approved test manifest. prod: any numeric GLID.
        Live calls still need the PM session or operator key in both modes (registry executor)."""
        from backend.source_policy import SourcePolicyError, require_approved, valid_glid
        check = valid_glid if self.glid_access() == "prod" else require_approved
        try:
            check(glid)
            return True
        except SourcePolicyError:
            return False

    async def _redash(self, source: str, glid: str) -> list[dict]:
        """Use V4's read-only Redash connector (fixed SQL, approved GLIDs, bounded polling), through the gateway when
        the registry row says so (5.10.1) and a gateway is set — the same switch every other IndiaMART API follows."""
        from backend.connectors import ConnectorService
        from backend.genome.registry import gateway_base, host_allowed
        service = ConnectorService(self.v4_config.values, allow_any_glid=self.glid_access() == "prod")
        base = gateway_base(self.settings)
        if base and host_allowed(base) and self.registry.get("redash.hotlead" if source == "redash_hotlead" else "redash.pns").via_gateway:
            service.gateway_base = base
            service.gateway_headers = {str(k): str(v) for k, v in (self.settings.get("upstream.gateway_headers", {}) or {}).items()}
        records = await service.fetch(source, "real-" + glid)
        return [r.get("raw", r) if isinstance(r, dict) else r for r in records][:25]

    def _hints(self, glid: str) -> dict:
        """City/category we really know about a private seller (keeps generated mocks coherent)."""
        if not self.v4_store:
            return {}
        record = self.v4_store.get("sellers", "real-" + glid) or {}
        return {k: record.get(k) for k in ("city", "category") if record.get(k)} | (
            {"category": record["category"]} if record.get("category") else {})
