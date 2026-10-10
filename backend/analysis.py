"""Public aggregate research and fixed Elasticsearch queries, never raw rows."""

import json
from pathlib import Path

import httpx
from fastapi import HTTPException

from backend.deconstruction import FIELD_ALIAS
from backend.research import RAW_PREFIX


def report(path: Path) -> dict:
    """Load the reviewed aggregate artifact, keeping an unavailable build explicit."""
    if not path.exists():
        raise HTTPException(
            503, "The verified analysis report has not been built."
        )
    return json.loads(path.read_text())


async def live_aggregate(search, view: str, period: str):
    """Only named, bounded aggregate contracts can reach Elasticsearch."""
    aliases = {
        "outcomes": RAW_PREFIX + "-family-bot-calls",
        "versions": RAW_PREFIX + "-family-bot-calls",
        "durations": RAW_PREFIX + "-family-bot-calls",
        "conflicts": RAW_PREFIX + "-family-bot-calls",
    }
    if view not in aliases or period not in [
        "all",
        *[f"2026-{month:02d}" for month in range(4, 10)],
    ]:
        raise HTTPException(
            422, "Select a supported aggregate and historical period."
        )
    query = (
        {"match_all": {}} if period == "all" else {"term": {"month": period}}
    )
    if view in {"outcomes", "versions"}:
        aggregate = {
            "groups": {
                "terms": {
                    "field": "outcome"
                    if view == "outcomes"
                    else "bot_version",
                    "size": 12,
                }
            }
        }
    elif view == "durations":
        aggregate = {
            "groups": {
                "range": {
                    "field": "duration_seconds",
                    "ranges": [
                        {"key": "Under30s", "to": 30},
                        {"key": "30–89s", "from": 30, "to": 90},
                        {"key": "90–179s", "from": 90, "to": 180},
                        {"key": "180s+", "from": 180},
                    ],
                }
            }
        }
    else:
        aggregate = {
            "groups": {
                "filters": {
                    "filters": {
                        "Disputed": {"term": {"meeting_conflict": True}},
                        "Other calls": {"term": {"meeting_conflict": False}},
                    }
                }
            }
        }
    try:
        async with search.client() as client:
            response = await client.post(
                "/" + aliases[view] + "/_search",
                json={
                    "size": 0,
                    "track_total_hits": True,
                    "query": query,
                    "aggs": aggregate,
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503,
            "The raw Elasticsearch aggregate is unavailable; the dated report snapshot remains available.",
        ) from None
    buckets = data["aggregations"]["groups"]["buckets"]
    if isinstance(buckets, dict):
        buckets = [{"key": key, **value} for key, value in buckets.items()]
    return {
        "view": view,
        "period": period,
        "engine": "elasticsearch",
        "index": aliases[view],
        "source": "Privately indexed historical CSV rows",
        "denominator": data["hits"]["total"]["value"],
        "took_ms": data["took"],
        "items": [
            {"label": bucket["key"], "count": bucket["doc_count"]}
            for bucket in buckets
        ],
        "limitation": "Real index query over historical data; this does not fetch current seller context or establish conversion uplift.",
    }


async def persona_fields(
    search, seller_id: str, source_mode: str, strength=None
):
    """Seller and privacy scope are supplied by the authenticated controller."""
    filters = [
        {"term": {"seller_id": seller_id}},
        {"term": {"source_mode": source_mode}},
    ]
    if strength:
        filters.append({"term": {"strength": strength}})
    try:
        async with search.client() as client:
            response = await client.post(
                "/" + FIELD_ALIAS + "/_search",
                json={
                    "size": 30,
                    "track_total_hits": True,
                    "query": {"bool": {"filter": filters}},
                    "sort": [{"field_name": "asc"}],
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503,
            "Indexed persona fields are not available; no illustrative data was substituted.",
        ) from None
    return {
        "engine": "elasticsearch",
        "index": FIELD_ALIAS,
        "seller_id": seller_id,
        "source_mode": source_mode,
        "total": data["hits"]["total"]["value"],
        "items": [hit["_source"] for hit in data["hits"]["hits"]],
        "took_ms": data["took"],
        "limitation": "Strength/confidence explain provenance; heuristic confidence is not a calibrated probability.",
    }
