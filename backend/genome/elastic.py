"""Elasticsearch for V5: API-response cache/store, persona and factor index, run events, suggestions.

Indices are ``<prefix>-<kind>-v1`` behind aliases ``<prefix>-<kind>`` with ``dynamic: strict``
mappings. LLM-invented factor names are stored as *values* in typed slots, never as new fields
(see docs/architecture/elastic-persona-index.md).

Writes go through the SQLite outbox (``GenomeStore.enqueue``) and are flushed in the background,
so a slow or unavailable cluster never blocks a call preparation.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

KEYWORD = {"type": "keyword"}
DATE = {"type": "date"}

MAPPINGS: dict[str, dict] = {
    "api-responses": {
        "id": KEYWORD, "api": KEYWORD, "seller_id": KEYWORD, "glid": KEYWORD, "origin": KEYWORD,
        "status": KEYWORD, "cache_key": KEYWORD, "run_id": KEYWORD, "http_status": {"type": "integer"},
        "duration_ms": {"type": "float"}, "at": DATE, "expires_at": DATE,
        "request": {"type": "object", "enabled": False}, "response": {"type": "object", "enabled": False},
    },
    "personas": {
        "id": KEYWORD, "seller_id": KEYWORD, "glid": KEYWORD, "revision": {"type": "integer"}, "latest": {"type": "boolean"},
        "summary": {"type": "text"}, "archetype": KEYWORD, "language": KEYWORD, "city": KEYWORD, "tags": KEYWORD,
        "completeness": {"type": "float"}, "model": KEYWORD, "origin": KEYWORD, "at": DATE, "synthetic": {"type": "boolean"},
        "document": {"type": "object", "enabled": False},
        # 5.5 persona generator
        "engine": KEYWORD, "schema_version": KEYWORD, "segment": KEYWORD, "call_plan_trigger": KEYWORD, "quality_score": {"type": "float"},
        "quality_critical": {"type": "integer"}, "dial_pace": {"type": "integer"}, "dial_mix": {"type": "integer"},
        "dial_formality": {"type": "integer"}, "dial_warmth": {"type": "integer"}, "generated": {"type": "object", "enabled": False},
    },
    "persona-evals": {
        "id": KEYWORD, "eval_id": KEYWORD, "name": {"type": "text", "fields": {"raw": KEYWORD}}, "kind": KEYWORD, "glid": KEYWORD,
        "score": {"type": "float"}, "raw_score": {"type": "float"}, "critical": {"type": "integer"}, "raw_critical": {"type": "integer"},
        "warnings": {"type": "integer"}, "scenario_ok": {"type": "integer"},
        "scenario_total": {"type": "integer"}, "expected": KEYWORD, "plan": KEYWORD, "attempts": {"type": "integer"}, "ms": {"type": "float"},
        "failed_checks": KEYWORD, "prompt": KEYWORD, "model": KEYWORD, "at": DATE, "persona": {"type": "object", "enabled": False},
    },
    "persona-factors": {
        "id": KEYWORD, "seller_id": KEYWORD, "glid": KEYWORD, "factor": KEYWORD, "novel": {"type": "boolean"},
        "value_type": KEYWORD, "value_keyword": KEYWORD, "value_number": {"type": "double"}, "value_bool": {"type": "boolean"},
        "value_text": {"type": "text"}, "score": {"type": "float"}, "strength": KEYWORD, "origin": KEYWORD,
        "language": KEYWORD, "archetype": KEYWORD, "city": KEYWORD, "revision": {"type": "integer"},
        "latest": {"type": "boolean"}, "synthetic": {"type": "boolean"}, "author": KEYWORD, "reason": {"type": "text"},
        "at": DATE, "expires_at": DATE, "sources": {"type": "object", "enabled": False},
    },
    "factor-registry": {
        "factor": KEYWORD, "status": KEYWORD, "count": {"type": "integer"}, "first_seen": DATE, "last_seen": DATE,
        "example": {"type": "keyword", "ignore_above": 256}, "value_type": KEYWORD,
    },
    "events": {
        "run_id": KEYWORD, "seq": {"type": "integer"}, "node": KEYWORD, "kind": KEYWORD, "origin": KEYWORD,
        "status": KEYWORD, "ms": {"type": "float"}, "at": DATE, "seller_id": KEYWORD, "flow": KEYWORD,
        "label": {"type": "text"},
    },
    "suggestions": {
        "id": KEYWORD, "seller_id": KEYWORD, "factor": KEYWORD, "status": KEYWORD, "author": KEYWORD,
        "from_value": {"type": "keyword", "ignore_above": 256}, "to_value": {"type": "keyword", "ignore_above": 256},
        "reason": {"type": "text"}, "at": DATE,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class GenomeElastic:
    def __init__(self, settings, store) -> None:
        self.settings = settings
        self.store = store
        self.url = os.getenv("ELASTICSEARCH_URL", "").rstrip("/")
        user = os.getenv("ELASTICSEARCH_USERNAME", "")
        self.auth = (user, os.getenv("ELASTICSEARCH_PASSWORD", "")) if user else None
        self.status = "not_configured" if not self.url else "not_checked"
        self.ready = False
        self._flush_task: asyncio.Task | None = None
        # Called with (run_id, node, ok, ms, idx) after each flushed write so runs can show it.
        self.on_flushed: Callable[[str | None, str | None, bool, float, str], None] | None = None

    # --- plumbing -------------------------------------------------------------------------------

    @property
    def prefix(self) -> str:
        return self.settings.get("elastic.index_prefix", "sellergenome-v5")

    def alias(self, kind: str) -> str:
        return f"{self.prefix}-{kind}"

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, auth=self.auth,
                                 timeout=self.settings.get("elastic.timeout_seconds", 6))

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def ensure(self) -> None:
        """Create physical indices and aliases once; tolerate an unavailable cluster."""
        if not self.url or self.ready:
            return
        try:
            async with self.client() as c:
                for kind, props in MAPPINGS.items():
                    alias, physical = self.alias(kind), self.alias(kind) + "-v1"
                    head = await c.head("/" + physical)
                    if head.status_code not in (200, 404):
                        raise ValueError(f"Cannot inspect {physical}: HTTP {head.status_code} (check the app user's index privileges)")
                    if head.status_code == 404:
                        body = {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
                                "mappings": {"dynamic": "strict", "properties": props},
                                "aliases": {alias: {"is_write_index": True}}}
                        r = await c.put("/" + physical, json=body)
                        if r.status_code not in (200, 201) and "resource_already_exists" not in r.text:
                            r.raise_for_status()
                    else:
                        r = await c.put(f"/{physical}/_mapping", json={"properties": props})
                        r.raise_for_status()
            self.ready, self.status = True, "ready"
        except (httpx.HTTPError, ValueError) as error:
            self.status = f"unavailable: {type(error).__name__}"

    async def start(self) -> None:
        await self.ensure()
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()

    async def _flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(float(self.settings.get("elastic.flush_interval_seconds", 1.0)))
                await self.flush()
            except asyncio.CancelledError:
                return
            except Exception:  # never let the loop die; items stay in the outbox
                await asyncio.sleep(5)

    async def flush(self) -> int:
        """Write due outbox items. Returns the number written."""
        if not self.url:
            return 0
        if not self.ready:
            await self.ensure()
            if not self.ready:
                return 0
        items = self.store.due(50)
        written = []
        async with self.client() as c:
            for item in items:
                started = time.monotonic()
                try:
                    r = await c.put(f"/{item['idx']}/_doc/{item['doc_id']}", json=json.loads(item["body"]))
                    r.raise_for_status()
                    written.append(item["id"])
                    if self.on_flushed:
                        self.on_flushed(item.get("run_id"), item.get("node"), True,
                                        (time.monotonic() - started) * 1000, item["idx"])
                except httpx.HTTPError as error:
                    self.store.retry_later(item["id"], item["attempts"] + 1, f"{type(error).__name__}: {str(error)[:200]}")
                    if self.on_flushed:
                        self.on_flushed(item.get("run_id"), item.get("node"), False, 0, item["idx"])
        self.store.done(written)
        return len(written)

    def queue(self, kind: str, doc_id: str, body: dict, run_id: str | None = None, node: str | None = None) -> None:
        self.store.enqueue(self.alias(kind), doc_id, body, run_id=run_id, node=node)

    # --- API response cache -----------------------------------------------------------------------

    def cache_query(self, cache_key: str) -> dict:
        return {
            "size": 1,
            "query": {"bool": {"filter": [
                {"term": {"cache_key": cache_key}},
                {"range": {"expires_at": {"gt": "now"}}},
                {"terms": {"status": ["ok"]}},
            ]}},
            "sort": [{"at": "desc"}],
        }

    async def cache_get(self, cache_key: str) -> tuple[dict | None, dict, float]:
        """Return (hit document or None, the exact query, ms)."""
        query = self.cache_query(cache_key)
        started = time.monotonic()
        if not self.url:
            return None, query, 0.0
        try:
            async with self.client() as c:
                r = await c.post(f"/{self.alias('api-responses')}/_search", json=query)
                r.raise_for_status()
                hits = r.json()["hits"]["hits"]
            return (hits[0]["_source"] if hits else None), query, (time.monotonic() - started) * 1000
        except (httpx.HTTPError, KeyError, ValueError):
            return None, query, (time.monotonic() - started) * 1000

    # --- search helpers -------------------------------------------------------------------------

    async def search(self, kind: str, body: dict) -> tuple[dict, float]:
        started = time.monotonic()
        if not self.url:
            raise RuntimeError("Elasticsearch is not configured.")
        async with self.client() as c:
            r = await c.post(f"/{self.alias(kind)}/_search", json=body)
            r.raise_for_status()
            return r.json(), (time.monotonic() - started) * 1000

    async def raw_search(self, index: str, body: dict) -> tuple[dict, float]:
        """Query an existing read-only index (e.g. the supplied dataset's raw indices)."""
        started = time.monotonic()
        if not self.url:
            raise RuntimeError("Elasticsearch is not configured.")
        async with self.client() as c:
            r = await c.post(f"/{index}/_search", json=body)
            r.raise_for_status()
            return r.json(), (time.monotonic() - started) * 1000

    async def counts(self) -> dict[str, Any]:
        if not self.url:
            return {"status": self.status}
        out: dict[str, Any] = {"status": self.status}
        try:
            async with self.client() as c:
                for kind in MAPPINGS:
                    r = await c.get(f"/{self.alias(kind)}/_count")
                    out[kind] = r.json().get("count") if r.status_code == 200 else None
        except httpx.HTTPError:
            out["status"] = "unavailable"
        return out
