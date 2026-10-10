#!/usr/bin/env python3
"""Idempotent bulk indexing and optional per-seller rule persona materialization."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import Settings
from backend.persona import generate_persona
from backend.search import SearchIndex
from backend.store import Store


async def run(args: argparse.Namespace) -> None:
    """Index explicit application projections; provider calls are never implicit."""
    config = Settings()
    if not config.es_url:
        raise SystemExit("ELASTICSEARCH_URL is required; no fallback success.")
    store = Store(Path(args.db) if args.db else config.db_path)
    search = SearchIndex(config)
    await search.ensure()
    totals = {}
    batch_size = config.values["elasticsearch"]["batch_size"]
    for table in args.tables.split(","):
        if table not in {"sellers", "evidence", "recordings", "personas"}:
            raise SystemExit("Unknown table")
        totals[table] = 0
        with store.connect() as db:
            cursor = db.execute(f"SELECT payload FROM {table} ORDER BY id")
            while rows := cursor.fetchmany(batch_size):
                documents = [json.loads(row[0]) for row in rows]
                # Embedded evidence is stored in its own searchable index.
                if table == "sellers":
                    documents = [
                        {
                            key: value
                            for key, value in doc.items()
                            if key != "evidence"
                        }
                        for doc in documents
                    ]
                totals[table] += await search.bulk(table, documents)
    if args.generate_personas:
        totals["generated_personas"] = 0
        with store.connect() as db:
            cursor = db.execute("SELECT payload FROM sellers ORDER BY id")
            while rows := cursor.fetchmany(batch_size):
                documents = []
                for row in rows:
                    seller = json.loads(row[0])
                    existing = store.get("personas", seller["id"])
                    # Preserve operator-reviewed AI personas across rule refresh jobs.
                    if existing and existing.get("method") != "rules":
                        continue
                    events = store.seller_evidence(seller["id"])
                    persona = generate_persona(
                        seller,
                        events,
                        config.values["persona"],
                        (existing or {}).get("revision", 0) + 1,
                    )
                    documents.append(persona)
                if documents:
                    await search.bulk("personas", documents)
                    with store.connect() as writer:
                        writer.executemany(
                            "INSERT OR REPLACE INTO personas(id,payload) VALUES (?,?)",
                            [
                                (
                                    doc["id"],
                                    json.dumps(
                                        {**doc, "indexed": True},
                                        ensure_ascii=False,
                                    ),
                                )
                                for doc in documents
                            ],
                        )
                    totals["generated_personas"] += len(documents)
                if args.limit and totals["generated_personas"] >= args.limit:
                    break
    print(json.dumps({"status": "indexed", "counts": totals}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument(
        "--tables", default="sellers,evidence,recordings,personas"
    )
    parser.add_argument("--generate-personas", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="0 means every seller; stops on a batch boundary",
    )
    asyncio.run(run(parser.parse_args()))
