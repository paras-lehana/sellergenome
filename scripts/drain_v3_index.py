"""Bounded bulk outbox delivery for a new isolated Elasticsearch namespace.

Use --enqueue-snapshot only for explicit initial backfill. Each indexed snapshot
retains a generation/token claim; concurrent updates cannot lose indexing intent.
No raw document data or provider credentials are printed.
"""

import argparse
import asyncio
import json
import secrets
import time

from backend.config import Settings
from backend.search import SearchIndex
from backend.store import Store


async def run(batch_size=500, enqueue=False, max_batches=0):
    config = Settings()
    if config.es_prefix != "seller-persona-v3":
        raise RuntimeError(
            "This staging backfill requires the isolated V3 namespace."
        )
    search, store = SearchIndex(config), Store(config.db_path)
    await search.ensure()
    if enqueue:
        with store.connect() as db:
            for table in ["sellers", "evidence", "personas", "recordings"]:
                db.execute(
                    f"INSERT OR IGNORE INTO index_outbox(kind,document_id,available_at) SELECT ?,id,? FROM {table}",
                    (table, time.time()),
                )
    delivered = batches = 0
    while not max_batches or batches < max_batches:
        now, token = time.time(), secrets.token_hex(16)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM index_outbox WHERE available_at<=? AND lease_until<=? ORDER BY available_at LIMIT ?",
                (now, now, batch_size),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                if item["kind"] not in {
                    "sellers",
                    "evidence",
                    "personas",
                    "recordings",
                }:
                    raise RuntimeError("Unsupported outbox kind")
                payload = db.execute(
                    f"SELECT payload FROM {item['kind']} WHERE id=?",
                    (item["document_id"],),
                ).fetchone()
                if not payload:
                    raise RuntimeError("Outbox document missing")
                item["payload"] = json.loads(payload[0])
                item["lease_token"] = token
                items.append(item)
            db.executemany(
                "UPDATE index_outbox SET lease_token=?,lease_until=?,attempts=attempts+1 WHERE kind=? AND document_id=?",
                [
                    (token, now + 300, item["kind"], item["document_id"])
                    for item in items
                ],
            )
        if not items:
            break
        # Bulk endpoint does not need refresh=wait_for on every batch; readback
        # follows one explicit refresh when the import completes.
        lines = []
        for item in items:
            lines.extend(
                [
                    json.dumps(
                        {
                            "index": {
                                "_index": search.name(item["kind"]),
                                "_id": item["document_id"],
                            }
                        }
                    ),
                    json.dumps(item["payload"], ensure_ascii=False),
                ]
            )
        async with search.client() as client:
            response = await client.post(
                "/_bulk",
                content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            receipts = response.json()["items"]
        if len(receipts) != len(items):
            raise RuntimeError(
                "Bulk receipt count mismatch; leases retained for retry"
            )
        with store.connect() as db:
            for item, receipt in zip(items, receipts):
                if 200 <= receipt.get("index", {}).get("status", 0) < 300:
                    db.execute(
                        "DELETE FROM index_outbox WHERE kind=? AND document_id=? AND generation=? AND lease_token=?",
                        (
                            item["kind"],
                            item["document_id"],
                            item["generation"],
                            token,
                        ),
                    )
                    # A concurrent newer generation becomes eligible immediately.
                    db.execute(
                        "UPDATE index_outbox SET lease_until=0,lease_token=NULL WHERE kind=? AND document_id=? AND lease_token=?",
                        (item["kind"], item["document_id"], token),
                    )
                    delivered += 1
                else:
                    raise RuntimeError(
                        "Index rejected a document; inspect mapping, no success claimed"
                    )
        batches += 1
        if batches % 100 == 0:
            print(
                json.dumps({"delivered": delivered, "batches": batches}),
                flush=True,
            )
    async with search.client() as client:
        refreshed = await client.post("/seller-persona-v3-*/_refresh")
        refreshed.raise_for_status()
    with store.connect() as db:
        pending = db.execute("SELECT COUNT(*) FROM index_outbox").fetchone()[0]
    print(
        json.dumps(
            {
                "delivered": delivered,
                "batches": batches,
                "pending": pending,
                "storage": await search.status(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--enqueue-snapshot", action="store_true")
    parser.add_argument("--max-batches", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000 or args.max_batches < 0:
        parser.error("Batch bounds invalid")
    asyncio.run(run(args.batch_size, args.enqueue_snapshot, args.max_batches))
