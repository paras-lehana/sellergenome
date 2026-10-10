"""Independent, read-only raw lineage, searchable-column and projection checks.

Outputs only hashes, aliases and counts. Raw seller values never enter receipts.
"""

import argparse
import csv
import json
from pathlib import Path

import httpx

from backend.deconstruction import FIELD_ALIAS
from backend.research import RAW_PREFIX, file_hash, raw_alias, raw_document
from scripts.configure_research_access import read_env
from scripts.install_research_dashboards import FACT_ALIAS


def verify(dataset, catalog, url, env_file):
    """Compare active aliases against every verified original source file."""
    env = read_env(env_file)
    sources = {path.name: path for path in dataset.rglob("*.csv")}
    receipt = {"files": [], "rows": 0, "families": {}}
    with httpx.Client(
        base_url=url,
        auth=(env["ELASTICSEARCH_USERNAME"], env["ELASTICSEARCH_PASSWORD"]),
        timeout=60,
    ) as client:

        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        for source in catalog["files"]:
            path = sources[source["name"]]
            assert file_hash(path) == source["sha256"], "Source hash changed"
            alias = raw_alias(path)
            targets = get("/_alias/" + alias)
            assert len(targets) == 1, "File alias must select one generation"
            index = next(iter(targets))
            assert index == alias + "-v2-" + source["sha256"][:12]
            count = get("/" + alias + "/_count")["count"]
            assert count == source["rows"], "Raw count mismatch"
            mapping = get("/" + alias + "/_mapping")[index]["mappings"]
            assert mapping["properties"]["raw"]["type"] == "flattened"
            with path.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            expected = raw_document(row, path, 2, source["sha256"])
            actual = get("/" + alias + "/_doc/" + expected["id"])["_source"]
            assert actual == expected, (
                "Lossless source/date projection mismatch"
            )
            column = next((key for key, value in row.items() if value), None)
            assert column, "Source example is unexpectedly empty"
            response = client.post(
                "/" + alias + "/_search",
                json={
                    "size": 0,
                    "query": {
                        "bool": {
                            "filter": [
                                {"ids": {"values": [expected["id"]]}},
                                {"term": {"raw." + column: row[column]}},
                            ]
                        }
                    },
                },
            )
            response.raise_for_status()
            assert response.json()["hits"]["total"]["value"] == 1
            receipt["files"].append(
                {
                    "alias": alias,
                    "index": index,
                    "rows": count,
                    "sha256": source["sha256"],
                    "lossless_readback": True,
                    "flattened_term_query": True,
                }
            )
            receipt["rows"] += count
            family = source["family"]
            receipt["families"][family] = (
                receipt["families"].get(family, 0) + count
            )
        for family, count in receipt["families"].items():
            alias = RAW_PREFIX + "-family-" + family.replace("_", "-")
            assert get("/" + alias + "/_count")["count"] == count
        assert receipt["rows"] == 908856
        receipt["persona_fields"] = get("/" + FIELD_ALIAS + "/_count")["count"]
        receipt["aggregate_facts"] = get("/" + FACT_ALIAS + "/_count")["count"]
        assert receipt["persona_fields"] >= 70
        assert receipt["aggregate_facts"] == 101
        receipt["status"] = "verified"
    return receipt


def main():
    """Credentials stay in private files rather than command arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--catalog", type=Path, default=Path("data/catalog.json")
    )
    parser.add_argument("--es-url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        args.dataset,
        json.loads(args.catalog.read_text()),
        args.es_url,
        args.env_file,
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "rows": result["rows"],
                "files": len(result["files"]),
                "persona_fields": result["persona_fields"],
                "aggregate_facts": result["aggregate_facts"],
            }
        )
    )


if __name__ == "__main__":
    main()
