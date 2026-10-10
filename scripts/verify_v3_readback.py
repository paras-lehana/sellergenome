"""Bounded V3 read-only verification; no corpus materialization or private output."""

import asyncio
import json

from backend.config import Settings
from backend.search import SearchIndex
from backend.store import Store


async def verify():
    config = Settings()
    if config.es_prefix != "seller-persona-v3":
        raise RuntimeError("Use isolated V3 namespace")
    search, store = SearchIndex(config), Store(config.db_path)
    result = {"aliases": {}, "sqlite": {}, "indexed": {}}
    # JSON aggregation happens inside SQLite; only one small count row enters Python.
    with store.connect() as db:
        counts = db.execute("""SELECT COUNT(*) AS observations,
            COALESCE(SUM(json_extract(payload,'$.kind')='call_summary'),0) AS summaries,
            COALESCE(SUM(json_extract(payload,'$.kind')='call_outcome'),0) AS outcome_only,
            COALESCE(SUM(json_extract(payload,'$.meeting.conflict')=1),0) AS conflicts,
            COALESCE(SUM(json_array_length(json_extract(payload,'$.llm_insights'))),0) AS llm_insights
            FROM evidence WHERE id LIKE 'call-summary-%' """).fetchone()
        result.update(
            summary_observations=counts["observations"],
            populated_summaries=counts["summaries"],
            outcome_only_observations=counts["outcome_only"],
            meeting_conflicts=counts["conflicts"],
            indexed_model_insights_expected=counts["llm_insights"],
        )
        result["outbox_pending"] = db.execute(
            "SELECT COUNT(*) FROM index_outbox"
        ).fetchone()[0]
        for kind in ["sellers", "evidence", "personas", "recordings"]:
            result["sqlite"][kind] = db.execute(
                f"SELECT COUNT(*) FROM {kind}"
            ).fetchone()[0]
    async with search.client() as client:
        for kind in [
            "sellers",
            "evidence",
            "personas",
            "recordings",
            "switches",
        ]:
            alias = config.es_prefix + "-" + kind
            response = await client.get("/_alias/" + alias)
            response.raise_for_status()
            targets = list(response.json())
            search.targets[kind] = alias
            search.alias_indices[kind] = targets
            result["aliases"][alias] = targets
            if kind == "switches":
                continue
            response = await client.get("/" + alias + "/_count")
            response.raise_for_status()
            result["indexed"][kind] = response.json()["count"]
        # At most one persona document is fetched. Only safe schema/provenance fields leave here.
        read = await search.readback("personas", "demo-asha")
        document = read.get("document") or {}
        result["generated_persona"] = {
            "found": read.get("verified", False),
            "physical_index": read.get("index"),
            "method": document.get("method"),
            "model": document.get("model"),
            "derived_fields": len(document.get("derived_fields", [])),
            "field_count": len(document.get("fields", {})),
            "strength_labels": sorted(
                {
                    x.get("strength", "missing")
                    for x in document.get("fields", {}).values()
                }
            ),
        }
        response = await client.post(
            "/" + search.name("evidence") + "/_count",
            json={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"source_mode": "private"}},
                            {"term": {"meeting.conflict": True}},
                        ]
                    }
                }
            },
        )
        response.raise_for_status()
        result["indexed_meeting_conflicts"] = response.json()["count"]
        response = await client.post(
            "/" + search.name("evidence") + "/_search",
            json={
                "size": 0,
                "query": {"term": {"source_mode": "private"}},
                "aggs": {
                    "model_insights": {"nested": {"path": "llm_insights"}}
                },
            },
        )
        response.raise_for_status()
        result["indexed_model_insights"] = response.json()["aggregations"][
            "model_insights"
        ]["doc_count"]
    result["consistent_counts"] = result["sqlite"] == result["indexed"]
    result["historical_corpus_verified"] = (
        result["populated_summaries"] == 258736
        and result["outcome_only_observations"] == 1
        and result["meeting_conflicts"] == 41
        and result["indexed_meeting_conflicts"] == 41
    )
    result["storage"] = await search.status()
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(verify())
