"""Explicit bounded materialization of existing personas; zero provider calls."""

import argparse
import asyncio
import json

from backend.config import Settings
from backend.deconstruction import deconstruct, index_fields
from backend.persona import generate_persona
from backend.search import SearchIndex
from backend.store import Store


async def run(limit, include_private):
    """Default selects synthetic demos; private historical examples are explicit."""
    config = Settings()
    store = Store(config.db_path)
    search = SearchIndex(config)
    with store.connect() as db:
        sql = "SELECT payload FROM sellers WHERE json_extract(payload,'$.source_mode')='demo' ORDER BY id LIMIT ?"
        sellers = [json.loads(row[0]) for row in db.execute(sql, (limit,))]
        if include_private:
            # Existing LLM evidence is selected locally; no source API/model is called.
            row = db.execute(
                "SELECT s.payload FROM sellers s JOIN evidence e ON s.id=e.seller_id WHERE json_extract(e.payload,'$.llm_insights[0].method')='llm_summary_inference' LIMIT 1"
            ).fetchone()
            if row:
                sellers.append(json.loads(row[0]))
    results, total = [], 0
    for seller in sellers:
        events = store.seller_evidence(seller["id"])
        persona = store.get("personas", seller["id"])
        if not persona or not all(
            "strength" in item for item in persona.get("fields", {}).values()
        ):
            persona = generate_persona(
                seller,
                events,
                config.values["persona"],
                (persona or {}).get("revision", 0) + 1,
            )
            store.put("personas", seller["id"], persona)
        receipt = await index_fields(
            search, deconstruct(persona, seller, events)
        )
        total += receipt["documents"]
        results.append({"source_mode": seller.get("source_mode"), **receipt})
    return {
        "status": "complete",
        "personas": len(sellers),
        "fields": total,
        "provider_calls": 0,
        "receipts": results,
    }


def main():
    """Bound local selection so an accidental whole-corpus sweep is impossible."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--include-private", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("Select between1 and50 personas")
    print(json.dumps(asyncio.run(run(args.limit, args.include_private))))


if __name__ == "__main__":
    main()
