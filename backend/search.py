"""Explicit Elasticsearch mappings and bounded, privacy-scoped search."""

import json

import httpx

from backend.config import Settings


class SearchIndex:
    """Use a dedicated prefix with no cluster-wide write operations."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.targets = {}
        self.alias_indices = {}
        self.last_status = (
            "not_configured" if not config.es_url else "not_checked"
        )

    def name(self, kind: str) -> str:
        """Version index schemas to make future migrations explicit."""
        assert kind in {
            "sellers",
            "evidence",
            "personas",
            "switches",
            "recordings",
        }
        return self.targets.get(kind, f"{self.config.es_prefix}-{kind}-v1")

    def client(self) -> httpx.AsyncClient:
        """Keep secrets inside the client and preserve certificate validation."""
        auth = (
            (self.config.es_username, self.config.es_password)
            if self.config.es_username
            else None
        )
        return httpx.AsyncClient(
            base_url=self.config.es_url,
            auth=auth,
            timeout=self.config.values["elasticsearch"]["timeout_seconds"],
        )

    async def status(self) -> dict:
        """Distinguish configured, reachable and actually indexed states."""
        if not self.config.es_url:
            return {
                "status": "not_configured",
                "engine": "sqlite",
                "indexed_documents": 0,
            }
        try:
            async with self.client() as client:
                response = await client.get(
                    "/"
                    + ",".join(
                        self.name(kind)
                        for kind in (
                            "sellers",
                            "evidence",
                            "personas",
                            "switches",
                            "recordings",
                        )
                    )
                    + "/_count",
                    params={"ignore_unavailable": "true"},
                )
                response.raise_for_status()
                count = response.json().get("count", 0)
            self.last_status = "connected"
            return {
                "status": "connected",
                "engine": "elasticsearch",
                "indexed_documents": count,
                "schema": "v3_aliases"
                if self.targets
                and all(
                    not target.endswith("-v1")
                    for target in self.targets.values()
                )
                else "legacy_or_uninitialized",
            }
        except (httpx.HTTPError, ValueError):
            self.last_status = "unavailable"
            return {
                "status": "unavailable",
                "engine": "sqlite",
                "indexed_documents": None,
            }

    async def ensure(self) -> None:
        """Create bounded mappings; arbitrary model keys cannot explode schema."""
        if not self.config.es_url:
            return
        common = {
            "id": {"type": "keyword"},
            "seller_id": {"type": "keyword"},
            "source_mode": {"type": "keyword"},
            "name": {"type": "text"},
            "category": {"type": "text"},
            "city": {"type": "keyword"},
            "persona_tag": {"type": "keyword"},
            "language": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "text": {"type": "text"},
            "source_ref": {"type": "keyword"},
            "observed_at": {"type": "date"},
            "ingested_at": {"type": "date"},
            "generated_at": {"type": "date"},
            "expires_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "confidence": {"type": "float"},
            "method": {"type": "keyword"},
            "model": {"type": "keyword"},
            "ttl_hours": {"type": "float"},
            "metrics": {"type": "flattened"},
            "traits": {"type": "flattened"},
            "signal": {"type": "keyword"},
            "reason": {"type": "text"},
            "session_id": {"type": "keyword"},
            "at": {"type": "date"},
            "cohort": {"type": "keyword"},
            "duration_seconds": {"type": "float"},
            "fields": {"type": "object", "enabled": False},
            "plan": {"type": "object", "enabled": False},
            "strength": {"type": "keyword"},
            "actor_role": {"type": "keyword"},
            "source": {"type": "keyword"},
            "source_updated_at": {"type": "date"},
            "content_hash": {"type": "keyword"},
            "schema_version": {"type": "keyword"},
            "insight_traits": {"type": "keyword"},
            "meeting": {
                "properties": {
                    "reported": {"type": "boolean"},
                    "conflict": {"type": "boolean"},
                    "status": {"type": "keyword"},
                    "source_label": {"type": "keyword"},
                }
            },
            "insights": {
                "type": "nested",
                "dynamic": False,
                "properties": {
                    "trait": {"type": "keyword"},
                    "value": {"type": "keyword"},
                    "actor_role": {"type": "keyword"},
                    "strength": {"type": "keyword"},
                    "evidence_ids": {"type": "keyword"},
                    "reason": {"type": "text"},
                },
            },
            "llm_insights": {
                "type": "nested",
                "dynamic": False,
                "properties": {
                    "trait": {"type": "keyword"},
                    "value": {"type": "keyword"},
                    "actor_role": {"type": "keyword"},
                    "strength": {"type": "keyword"},
                    "evidence_ids": {"type": "keyword"},
                    "model": {"type": "keyword"},
                    "reason": {"type": "text"},
                },
            },
            "derived_fields": {
                "type": "nested",
                "properties": {
                    "name": {"type": "keyword"},
                    "strength": {"type": "keyword"},
                    "value": {"type": "keyword"},
                    "reason": {"type": "text"},
                    "confidence": {"type": "float"},
                    "evidence_ids": {"type": "keyword"},
                    "method": {"type": "keyword"},
                    "model": {"type": "keyword"},
                    "prompt_version": {"type": "keyword"},
                    "observed_at": {"type": "date"},
                    "computed_at": {"type": "date"},
                    "expires_at": {"type": "date"},
                },
            },
        }
        async with self.client() as client:
            for kind in (
                "sellers",
                "evidence",
                "personas",
                "switches",
                "recordings",
            ):
                physical = f"{self.config.es_prefix}-{kind}-v3"
                alias = f"{self.config.es_prefix}-{kind}"
                response = await client.put(
                    f"/{physical}",
                    json={
                        "settings": {
                            "number_of_shards": 1,
                            "number_of_replicas": 0,
                        },
                        "mappings": {"dynamic": False, "properties": common},
                    },
                )
                if (
                    response.status_code == 400
                    and response.json().get("error", {}).get("type")
                    == "resource_already_exists_exception"
                ):
                    pass
                else:
                    response.raise_for_status()
                mapping = await client.put(
                    f"/{physical}/_mapping",
                    json={"dynamic": False, "properties": common},
                )
                mapping.raise_for_status()
                alias_result = await client.get(f"/_alias/{alias}")
                if alias_result.status_code == 200:
                    self.targets[kind] = alias
                    self.alias_indices[kind] = list(alias_result.json())
                elif alias_result.status_code == 404:
                    legacy = await client.head(
                        f"/{self.config.es_prefix}-{kind}-v1"
                    )
                    if legacy.status_code == 404:
                        response = await client.post(
                            "/_aliases",
                            json={
                                "actions": [
                                    {
                                        "add": {
                                            "index": physical,
                                            "alias": alias,
                                            "is_write_index": True,
                                        }
                                    }
                                ]
                            },
                        )
                        response.raise_for_status()
                        self.targets[kind] = alias
                        self.alias_indices[kind] = [physical]
                    else:
                        legacy.raise_for_status()
                        # Existing installations stay readable until explicit verified migration.
                        self.targets[kind] = (
                            f"{self.config.es_prefix}-{kind}-v1"
                        )
                else:
                    alias_result.raise_for_status()

    async def migrate_v3(self):
        """Explicit maintenance operation: copy, count-check, then atomically switch aliases.

        Quiesce writers first. Never delete legacy indices: they are rollback material.
        """
        await self.ensure()
        if not self.config.es_url:
            return {"status": "not_configured", "items": []}
        actions, receipts = [], []
        async with self.client() as client:
            for kind in (
                "sellers",
                "evidence",
                "personas",
                "switches",
                "recordings",
            ):
                source = self.name(kind)
                physical = f"{self.config.es_prefix}-{kind}-v3"
                alias = f"{self.config.es_prefix}-{kind}"
                if source == alias and self.alias_indices.get(kind) == [
                    physical
                ]:
                    receipts.append({"kind": kind, "status": "alias_ready"})
                    continue
                before = await client.get(f"/{source}/_count")
                before.raise_for_status()
                copied = await client.post(
                    "/_reindex",
                    json={
                        "source": {"index": source},
                        "dest": {"index": physical},
                    },
                    timeout=300,
                )
                copied.raise_for_status()
                if copied.json().get("failures"):
                    raise RuntimeError(
                        "V3 migration returned document failures; aliases unchanged."
                    )
                refreshed = await client.post(f"/{physical}/_refresh")
                refreshed.raise_for_status()
                after = await client.get(f"/{physical}/_count")
                after.raise_for_status()
                if before.json()["count"] != after.json()["count"]:
                    raise RuntimeError(
                        "V3 count verification failed; aliases unchanged."
                    )
                for old in self.alias_indices.get(kind, []):
                    actions.append({"remove": {"index": old, "alias": alias}})
                actions.append(
                    {
                        "add": {
                            "index": physical,
                            "alias": alias,
                            "is_write_index": True,
                        }
                    }
                )
                receipts.append(
                    {
                        "kind": kind,
                        "status": "verified",
                        "source_count": before.json()["count"],
                        "target_count": after.json()["count"],
                    }
                )
            if actions:
                switched = await client.post(
                    "/_aliases", json={"actions": actions}
                )
                switched.raise_for_status()
        for kind in (
            "sellers",
            "evidence",
            "personas",
            "switches",
            "recordings",
        ):
            self.targets[kind] = f"{self.config.es_prefix}-{kind}"
            self.alias_indices[kind] = [f"{self.config.es_prefix}-{kind}-v3"]
        return {
            "status": "verified_aliases",
            "items": receipts,
            "legacy_indices_retained": True,
        }

    async def readback(self, kind, identifier):
        if not self.config.es_url:
            return {"verified": False, "reason": "not_configured"}
        async with self.client() as client:
            result = await client.get(f"/{self.name(kind)}/_doc/{identifier}")
            if result.status_code == 404:
                return {"verified": False, "reason": "document_missing"}
            result.raise_for_status()
            data = result.json()
            return {
                "verified": bool(data.get("found")),
                "index": data.get("_index"),
                "id": identifier,
                "document": data.get("_source"),
            }

    async def insights(
        self, query, seller_id, mode, trait="", outcome="", actor=""
    ):
        if not self.config.es_url:
            return None
        filters = [
            {"term": {"source_mode": mode}},
            {
                "terms": {
                    "kind": [
                        "call_summary",
                        "call_outcome",
                        "redash_hotlead",
                        "pns",
                        "historical_call",
                    ]
                }
            },
        ]
        for field, value in (
            ("seller_id", seller_id),
            ("insight_traits", trait),
            ("meeting.status", outcome),
        ):
            if value:
                filters.append({"term": {field: value}})
        if actor:
            filters.append(
                {
                    "bool": {
                        "minimum_should_match": 1,
                        "should": [
                            {"term": {"actor_role": actor}},
                            {
                                "nested": {
                                    "path": "insights",
                                    "query": {
                                        "term": {"insights.actor_role": actor}
                                    },
                                }
                            },
                            {
                                "nested": {
                                    "path": "llm_insights",
                                    "query": {
                                        "term": {
                                            "llm_insights.actor_role": actor
                                        }
                                    },
                                }
                            },
                        ],
                    }
                }
            )
        body = {
            "size": 40,
            "track_total_hits": True,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {
                            "simple_query_string": {
                                "query": query,
                                "fields": ["text"],
                                "default_operator": "and",
                            }
                        }
                    ]
                    if query
                    else [],
                }
            },
        }
        try:
            async with self.client() as client:
                response = await client.post(
                    f"/{self.name('evidence')}/_search", json=body
                )
                response.raise_for_status()
                result = response.json()["hits"]
            return {
                "items": [hit["_source"] for hit in result["hits"]],
                "total": result["total"]["value"],
                "engine": "elasticsearch",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    async def put(self, kind: str, identifier: str, payload: dict) -> bool:
        """Return a truthful indexing result without swallowing failed writes."""
        if not self.config.es_url:
            return False
        try:
            async with self.client() as client:
                response = await client.put(
                    f"/{self.name(kind)}/_doc/{identifier}",
                    params={"refresh": "wait_for"},
                    json=payload,
                )
                response.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError):
            self.last_status = "write_failed"
            return False

    async def bulk(self, kind: str, documents: list[dict]) -> int:
        """Bulk is idempotent by document ID; inspect item failures explicitly."""
        lines = []
        for document in documents:
            lines.extend(
                [
                    json.dumps(
                        {
                            "index": {
                                "_index": self.name(kind),
                                "_id": document["id"],
                            }
                        }
                    ),
                    json.dumps(document, ensure_ascii=False),
                ]
            )
        async with self.client() as client:
            response = await client.post(
                "/_bulk",
                content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            result = response.json()
        if result.get("errors"):
            failures = sum(
                1 for item in result["items"] if item["index"]["status"] >= 300
            )
            raise RuntimeError(
                f"Elasticsearch rejected {failures} bulk documents; inspect mappings."
            )
        return len(documents)

    async def deliver_batch(self, items: list[dict]) -> list[bool]:
        """Acknowledge each outbox document separately, including partial failures."""
        if not items or not self.config.es_url:
            return [False] * len(items)
        lines = []
        for item in items:
            lines.extend(
                [
                    json.dumps(
                        {
                            "index": {
                                "_index": self.name(item["kind"]),
                                "_id": item["document_id"],
                            }
                        }
                    ),
                    json.dumps(item["payload"], ensure_ascii=False),
                ]
            )
        try:
            async with self.client() as client:
                response = await client.post(
                    "/_bulk",
                    params={"refresh": "wait_for"},
                    content="\n".join(lines) + "\n",
                    headers={"Content-Type": "application/x-ndjson"},
                )
                response.raise_for_status()
                receipts = response.json()["items"]
            if len(receipts) != len(items):
                return [False] * len(items)
            return [
                200 <= receipt.get("index", {}).get("status", 0) < 300
                for receipt in receipts
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            self.last_status = "write_failed"
            return [False] * len(items)

    async def evidence(
        self, query: str, seller_id: str, mode: str
    ) -> dict | None:
        """Scope all queries to an authorized dataset before matching text."""
        if not self.config.es_url:
            return None
        filters = [{"term": {"source_mode": mode}}]
        if seller_id:
            filters.append({"term": {"seller_id": seller_id}})
        body = {
            "size": 40,
            "track_total_hits": True,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {
                            "simple_query_string": {
                                "query": query,
                                "fields": ["text"],
                                "default_operator": "and",
                            }
                        }
                    ]
                    if query
                    else [],
                }
            },
        }
        try:
            async with self.client() as client:
                response = await client.post(
                    f"/{self.name('evidence')}/_search", json=body
                )
                response.raise_for_status()
                result = response.json()["hits"]
            return {
                "items": [hit["_source"] for hit in result["hits"]],
                "total": result["total"]["value"],
                "engine": "elasticsearch",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    async def sellers(
        self, query: str, persona: str, page: int, size: int, mode: str
    ) -> dict | None:
        """Search the indexed seller projection; SQLite is an explicit fallback."""
        if not self.config.es_url or (page - 1) * size >= 10000:
            return None
        filters = [{"term": {"source_mode": mode}}]
        if persona and persona != "all":
            filters.append({"term": {"persona_tag": persona}})
        must = []
        if query:
            city = query.replace("*", "").replace("?", "")
            must = [
                {
                    "bool": {
                        "should": [
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": ["name", "category"],
                                    "type": "phrase_prefix",
                                }
                            },
                            {"term": {"id": query}},
                            {
                                "wildcard": {
                                    "city": {
                                        "value": "*" + city + "*",
                                        "case_insensitive": True,
                                    }
                                }
                            },
                        ],
                        "minimum_should_match": 1,
                    }
                }
            ]
        body = {
            "size": size,
            "from": (page - 1) * size,
            "track_total_hits": True,
            "sort": [{"id": "asc"}],
            "query": {"bool": {"filter": filters, "must": must}},
        }
        try:
            async with self.client() as client:
                response = await client.post(
                    f"/{self.name('sellers')}/_search", json=body
                )
                response.raise_for_status()
                result = response.json()["hits"]
            return {
                "items": [hit["_source"] for hit in result["hits"]],
                "total": result["total"]["value"],
                "page": page,
                "page_size": size,
                "engine": "elasticsearch",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return None
