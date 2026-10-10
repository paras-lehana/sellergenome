"""Materialize one searchable document per actual persona delivery choice."""

import hashlib
import json
import math

from backend.persona import freshness

FIELD_ALIAS = "sellergenome-persona-fields"
FIELD_INDEX = FIELD_ALIAS + "-v1"


def deconstruct(persona: dict, seller: dict, events: list[dict]) -> list[dict]:
    """Keep inference strength, original dates and evidence separate from value."""
    evidence = {event["id"]: event for event in events}
    result = []
    for name, field in persona.get("fields", {}).items():
        value = field.get("value")
        confidence = field.get("confidence")
        if not isinstance(confidence, (int, float)) or not math.isfinite(
            confidence
        ):
            confidence = None
        citations = []
        for identifier in field.get("evidence_ids", []):
            event = evidence.get(identifier)
            citations.append(
                {
                    "id": identifier,
                    "available": event is not None,
                    "source": (event or {}).get(
                        "source", (event or {}).get("source_ref")
                    ),
                    "observed_at": (event or {}).get("observed_at"),
                    "actor_role": (event or {}).get("actor_role", "unknown"),
                    "freshness": freshness(event)["state"]
                    if event
                    else "unknown",
                }
            )
        result.append(
            {
                "id": hashlib.sha256(
                    (seller["id"] + ":" + name).encode()
                ).hexdigest(),
                "seller_id": seller["id"],
                "source_mode": seller.get("source_mode", "private"),
                "field_name": name,
                "value": json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value),
                "value_type": type(value).__name__,
                "value_data": {"value": value},
                "reason": field.get("reason", "No recorded explanation."),
                "strength": field.get("strength", "legacy_unclassified"),
                "confidence": confidence,
                "confidence_kind": field.get(
                    "confidence_kind", "heuristic_not_calibrated"
                ),
                "method": field.get("method", persona.get("method")),
                "model": field.get("model", persona.get("model")),
                "prompt_version": field.get("prompt_version"),
                "persona_revision": persona.get("revision", 1),
                "observed_at": field.get("observed_at"),
                "computed_at": field.get(
                    "computed_at", persona.get("generated_at")
                ),
                "expires_at": field.get("expires_at"),
                "evidence_ids": field.get("evidence_ids", []),
                "citations": citations,
                "schema_version": "persona-deconstruction-1",
                "policy": "Delivery decisions only; no change to prices, facts or contact permissions.",
            }
        )
    return result


def field_mapping() -> dict:
    """Allow controlled aggregations while retaining typed original values."""
    keywords = [
        "id",
        "seller_id",
        "source_mode",
        "field_name",
        "value",
        "value_type",
        "strength",
        "confidence_kind",
        "method",
        "model",
        "prompt_version",
        "evidence_ids",
        "schema_version",
        "policy",
    ]
    properties = {
        key: {"type": "keyword", "ignore_above": 4096} for key in keywords
    }
    properties.update(
        {
            "reason": {"type": "text"},
            "confidence": {"type": "float"},
            "persona_revision": {"type": "integer"},
            "value_data": {"type": "object", "enabled": False},
            "citations": {"type": "object", "enabled": False},
        }
    )
    properties.update(
        {
            key: {"type": "date"}
            for key in ["observed_at", "computed_at", "expires_at"]
        }
    )
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"dynamic": "strict", "properties": properties},
        "aliases": {FIELD_ALIAS: {}},
    }


async def index_fields(search, documents):
    """Verify bulk items and return an honest independent indexing receipt."""
    if not search.config.es_url:
        return {"status": "not_configured", "documents": 0}
    async with search.client() as client:
        response = await client.head("/" + FIELD_INDEX)
        if response.status_code == 404:
            response = await client.put(
                "/" + FIELD_INDEX, json=field_mapping()
            )
            # Another first caller may have created the same index.
            if response.status_code != 400:
                response.raise_for_status()
            else:
                (await client.head("/" + FIELD_INDEX)).raise_for_status()
        else:
            response.raise_for_status()
        payload = "".join(
            json.dumps(
                {
                    "index": {
                        "_index": FIELD_ALIAS,
                        "_id": doc["id"],
                        "version": max(1, doc["persona_revision"]),
                        "version_type": "external_gte",
                    }
                }
            )
            + "\n"
            + json.dumps(doc, ensure_ascii=False)
            + "\n"
            for doc in documents
        )
        response = await client.post(
            "/_bulk",
            content=payload.encode(),
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "wait_for"},
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        conflicts = sum(item["index"].get("status") == 409 for item in items)
        if any(
            item["index"].get("status", 500) >= 300
            and item["index"].get("status") != 409
            for item in items
        ):
            raise RuntimeError("Persona field indexing failed")
        return {
            "status": "indexed"
            if not conflicts
            else "newer_revision_retained",
            "documents": len(items) - conflicts,
            "index": FIELD_ALIAS,
        }
