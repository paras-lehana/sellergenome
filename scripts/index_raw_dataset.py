"""Explicit private raw CSV indexing; verify every source before publishing aliases.

Reads the configured private Elasticsearch credentials. Receipts contain counts,
hashes and index names only. No source rows, IDs, summaries or secrets are printed.
"""

import argparse
import asyncio
import csv
import json
from pathlib import Path

from backend.config import Settings
from backend.research import (
    RAW_PREFIX,
    file_hash,
    raw_alias,
    raw_document,
    raw_mapping,
)
from backend.search import SearchIndex


async def bulk(client, index, documents):
    """Inspect every item, refusing alias publication after a partial failure."""
    payload = "".join(
        json.dumps({"index": {"_index": index, "_id": doc["id"]}})
        + "\n"
        + json.dumps(doc, ensure_ascii=False)
        + "\n"
        for doc in documents
    )
    response = await client.post(
        "/_bulk",
        content=payload.encode(),
        headers={"Content-Type": "application/x-ndjson"},
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors") or any(
        item["index"].get("status", 500) >= 300 for item in data["items"]
    ):
        raise RuntimeError(
            "Raw bulk item failed; source values are withheld. Inspect private Elasticsearch logs."
        )


async def index_dataset(
    dataset, catalog, batch_size=500, limit_files=0, source_file=None
):
    """Replay stable row IDs and publish each file only after verified full counts."""
    if not 50 <= batch_size <= 1000:
        raise ValueError("Batch size must be between 50 and 1000")
    if limit_files < 0:
        raise ValueError("File limit must be nonnegative")
    config, receipt = Settings(), {"status": "running", "files": [], "rows": 0}
    search = SearchIndex(config)
    by_name = {path.name: path for path in dataset.rglob("*.csv")}
    sources = (
        catalog["files"][:limit_files] if limit_files else catalog["files"]
    )
    if source_file:
        sources = [
            source for source in sources if source["name"] == source_file
        ]
        if not sources:
            raise ValueError("Select a source file from the verified catalog")
    # Complete preflight first: a changed/missing later file must not partially publish.
    for source in sources:
        path = by_name.get(source["name"])
        if path is None or file_hash(path) != source["sha256"]:
            raise RuntimeError("Input differs from the verified catalog")
    async with search.client() as client:
        for source in sources:
            path = by_name[source["name"]]
            alias = raw_alias(path)
            # Content-addressed generations retain earlier snapshots for explicit rollback.
            index = alias + "-v2-" + source["sha256"][:12]
            response = await client.head("/" + index)
            if response.status_code == 404:
                response = await client.put(
                    "/" + index, json=raw_mapping(source["columns"])
                )
                response.raise_for_status()
            else:
                response.raise_for_status()
            pending, count, example = [], 0, None
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for line, row in enumerate(csv.DictReader(handle), 2):
                    doc = raw_document(row, path, line, source["sha256"])
                    pending.append(doc)
                    count += 1
                    if example is None:
                        example = doc["id"]
                    if len(pending) >= batch_size:
                        await bulk(client, index, pending)
                        pending.clear()
                if pending:
                    await bulk(client, index, pending)
            if count != source["rows"]:
                raise RuntimeError(
                    "Source count differs from catalog; alias not published"
                )
            (await client.post("/" + index + "/_refresh")).raise_for_status()
            response = await client.get("/" + index + "/_count")
            response.raise_for_status()
            if response.json()["count"] != count:
                raise RuntimeError(
                    "Raw index count mismatch; alias not published"
                )
            response = await client.get("/" + index + "/_doc/" + example)
            response.raise_for_status()
            read = response.json()["_source"]
            if (
                read["source_sha256"] != source["sha256"]
                or read["source_row"] != 2
            ):
                raise RuntimeError("Source lineage readback failed")
            family_alias = (
                RAW_PREFIX + "-family-" + source["family"].replace("_", "-")
            )
            response = await client.get("/_alias/" + alias)
            old = response.json() if response.is_success else {}
            if not response.is_success and response.status_code != 404:
                response.raise_for_status()
            actions = []
            for previous in old:
                if previous != index:
                    actions.extend(
                        [
                            {"remove": {"index": previous, "alias": alias}},
                            {
                                "remove": {
                                    "index": previous,
                                    "alias": family_alias,
                                    "must_exist": False,
                                }
                            },
                        ]
                    )
            actions.extend(
                [
                    {"add": {"index": index, "alias": alias}},
                    {"add": {"index": index, "alias": family_alias}},
                ]
            )
            (
                await client.post("/_aliases", json={"actions": actions})
            ).raise_for_status()
            entry = {
                "source_file": path.name,
                "family": source["family"],
                "sha256": source["sha256"],
                "alias": alias,
                "index": index,
                "rows": count,
                "verified": True,
            }
            receipt["files"].append(entry)
            receipt["rows"] += count
            print(json.dumps(entry), flush=True)
    receipt["status"] = "complete"
    return receipt


def main():
    """CLI limits are explicit; a sample run never claims full-dataset coverage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/catalog.json")
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit-files", type=int, default=0)
    parser.add_argument(
        "--source-file", help="Explicit bounded replay of one verified CSV"
    )
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        index_dataset(
            args.dataset,
            json.loads(args.catalog.read_text()),
            args.batch_size,
            args.limit_files,
            args.source_file,
        )
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "files": len(result["files"]),
                "rows": result["rows"],
            }
        )
    )


if __name__ == "__main__":
    main()
