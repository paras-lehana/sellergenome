"""Publish aggregate facts and import real Kibana charts using its supported API.

Never write to .kibana indices. Credentials are read from private environment files,
not command arguments; the generated NDJSON contains only safe saved-object specs.
"""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

from scripts.configure_research_access import read_env
from backend.deconstruction import FIELD_ALIAS
from backend.research import RAW_PREFIX, raw_alias

FACT_ALIAS = "sellergenome-analytics-facts"


def facts(report):
    """One typed fact per chart bucket, retaining source and denominator."""
    result = []

    def add(chart, category, value, denominator, source, series="all"):
        result.append(
            {
                "id": hashlib.sha256(
                    f"{chart}:{category}:{series}".encode()
                ).hexdigest(),
                "chart": chart,
                "category": category,
                "series": series,
                "value": value,
                "denominator": denominator,
                "source_definition": source,
                "report_version": report["version"],
                "method": "Verified full-scan aggregate; association, not causal uplift.",
                "generated_at": report["generated_at"],
            }
        )

    for item in report["families"]:
        add(
            "inventory",
            item["family"],
            item["count"],
            report["total_rows"],
            "inventory",
        )
    for cue in report["cues"]:
        add(
            "cue_mentions",
            cue["trait"],
            cue["mentions"],
            report["summary"]["populated"],
            "summaries",
        )
        for actor, count in cue["actors"].items():
            add(
                "cue_actors",
                cue["trait"],
                count,
                cue["mentions"],
                "summaries",
                actor,
            )
    for family, value in report["duplicates"].items():
        for kind in [
            "exact_extras",
            "distinct_content_extras",
            "conflicting_keys",
        ]:
            add(
                "duplicate_quality",
                family,
                value[kind],
                dict((v["family"], v["count"]) for v in report["families"])[
                    family
                ],
                "duplicates",
                kind,
            )
    for field, missing in report["null_counts"]["seller_files"].items():
        add(
            "seller_missingness",
            field,
            missing,
            report["unique_counts"]["seller_files:sellers"],
            "seller_fields",
        )
    return result


def vega(title, index, group, chart_type="bar", query=None):
    """Saved visualization queries actual ES aggregates, not inline fixture data."""
    body = {"size": 0, "aggs": {"groups": group}}
    if query:
        body["query"] = query
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title,
        "data": {
            "url": {"index": index, "body": body},
            "format": {"property": "aggregations.groups.buckets"},
        },
        "mark": {"type": chart_type, "tooltip": True, "color": "#237569"},
        "encoding": {
            "x": {
                "field": "key_as_string"
                if "date_histogram" in group
                else "key",
                "type": "nominal",
                "axis": {"labelAngle": -25, "title": "Source category"},
            },
            "y": {
                "field": "total.value" if "aggs" in group else "doc_count",
                "type": "quantitative",
                "axis": {"title": "Recorded count"},
            },
        },
        "config": {
            "view": {"stroke": None},
            "axis": {"labelFontSize": 11, "titleFontSize": 12},
        },
    }
    identifier = "sg-chart-" + hashlib.sha256(title.encode()).hexdigest()[:12]
    return {
        "id": identifier,
        "type": "visualization",
        "attributes": {
            "title": title,
            "description": "SellerGenome verified historical research; source selection and actor limits apply. No causal uplift claim.",
            "visState": json.dumps(
                {
                    "title": title,
                    "type": "vega",
                    "params": {"spec": json.dumps(spec)},
                    "aggs": [],
                }
            ),
            "uiStateJSON": "{}",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": '{"query":{"query":"","language":"kuery"},"filter":[]}'
            },
        },
        "references": [],
    }


def dashboard(identifier, title, charts):
    """Use referenced saved panels, allowing export/import without inline secrets."""
    panels, references = [], []
    for index, chart in enumerate(charts):
        ref = "panel_" + str(index)
        panels.append(
            {
                "type": "visualization",
                "panelRefName": ref,
                "panelIndex": str(index),
                "embeddableConfig": {},
                "gridData": {
                    "x": (index % 2) * 24,
                    "y": (index // 2) * 16,
                    "w": 24,
                    "h": 16,
                    "i": str(index),
                },
            }
        )
        references.append(
            {"name": ref, "type": "visualization", "id": chart["id"]}
        )
    return {
        "id": identifier,
        "type": "dashboard",
        "attributes": {
            "title": title,
            "description": "Actual ES queries over audited CSV snapshots and persona fields. These fixed full-corpus charts do not represent current activity, validated speaker roles or causal conversion uplift.",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": '{"useMargins":true,"hidePanelTitles":false}',
            "version": 1,
            "timeRestore": True,
            "timeFrom": "2026-04-01T00:00:00.000Z",
            "timeTo": "2026-10-01T00:00:00.000Z",
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": '{"query":{"query":"","language":"kuery"},"filter":[]}'
            },
        },
        "references": references,
    }


def saved_objects(report):
    """Provide per-file/family data views plus separate persona and quality dashboards."""
    views = []
    targets = [
        ("raw-" + source["name"], raw_alias(Path(source["name"])))
        for source in report["sources"]
    ]
    targets += [
        (
            "family-" + item["family"],
            RAW_PREFIX + "-family-" + item["family"].replace("_", "-"),
        )
        for item in report["families"]
    ]
    targets += [
        ("persona-fields", FIELD_ALIAS),
        ("aggregate-facts", FACT_ALIAS),
    ]
    for name, target in targets:
        views.append(
            {
                "id": "sg-view-"
                + hashlib.sha256(name.encode()).hexdigest()[:12],
                "type": "index-pattern",
                "attributes": {
                    "title": target,
                    "name": "SellerGenome · " + name,
                    "fields": "[]",
                    "fieldFormatMap": "{}",
                    "runtimeFieldMap": "{}",
                },
                "references": [],
            }
        )
    bot = RAW_PREFIX + "-family-bot-calls"
    charts = [
        vega(
            "Source inventory — rows by family",
            RAW_PREFIX + "-family-*",
            {"terms": {"field": "family", "size": 6}},
        ),
        vega(
            "Answered-call outcomes — historical source labels",
            bot,
            {"terms": {"field": "outcome", "size": 8}},
        ),
        vega(
            "Monthly answered calls — April to September",
            bot,
            {
                "date_histogram": {
                    "field": "observed_at",
                    "calendar_interval": "month",
                    "format": "yyyy-MM",
                }
            },
        ),
        vega(
            "Meeting disagreements — raw flag vs label",
            bot,
            {"terms": {"field": "bot_version", "size": 8}},
            query={"term": {"meeting_conflict": True}},
        ),
        vega(
            "Summary availability — unknown stays unknown",
            bot,
            {"terms": {"field": "summary_available", "size": 2}},
        ),
        vega(
            "Persona choice strength — actual field documents",
            FIELD_ALIAS,
            {"terms": {"field": "strength", "size": 6}},
        ),
        vega(
            "Summary cues — unvalidated lexical mentions",
            FACT_ALIAS,
            {
                "terms": {"field": "category", "size": 12},
                "aggs": {"total": {"sum": {"field": "value"}}},
            },
            query={"term": {"chart": "cue_mentions"}},
        ),
        vega(
            "Seller missingness — count of unknown values",
            FACT_ALIAS,
            {
                "terms": {
                    "field": "category",
                    "size": 20,
                    "order": {"total": "desc"},
                },
                "aggs": {"total": {"sum": {"field": "value"}}},
            },
            query={"term": {"chart": "seller_missingness"}},
        ),
    ]
    return (
        views
        + charts
        + [
            dashboard(
                "sg-corpus-atlas",
                "SellerGenome · corpus and data quality",
                charts[:5] + [charts[7]],
            ),
            dashboard(
                "sg-persona-evidence",
                "SellerGenome · persona deconstruction and summary evidence",
                [charts[5], charts[6], charts[1], charts[3]],
            ),
        ]
    )


def main():
    """Verify imported object counts and alias readback before declaring success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--es-url", required=True)
    parser.add_argument("--kibana", default="http://127.0.0.1:5602")
    parser.add_argument("--infra-env", type=Path, required=True)
    parser.add_argument("--publisher-env", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=Path("data/analysis-report.json")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/kibana/sellergenome.ndjson"),
    )
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    records = facts(report)
    publisher, infra = read_env(args.publisher_env), read_env(args.infra_env)
    generation = hashlib.sha256(args.report.read_bytes()).hexdigest()[:12]
    physical = FACT_ALIAS + "-v1-" + generation
    mapping = {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                **{
                    key: {"type": "keyword"}
                    for key in [
                        "id",
                        "chart",
                        "category",
                        "series",
                        "source_definition",
                        "report_version",
                        "method",
                    ]
                },
                "value": {"type": "double"},
                "denominator": {"type": "long"},
                "generated_at": {"type": "date"},
            },
        },
    }
    with httpx.Client(
        base_url=args.es_url,
        auth=(
            publisher["ELASTICSEARCH_USERNAME"],
            publisher["ELASTICSEARCH_PASSWORD"],
        ),
        timeout=60,
    ) as es:
        response = es.head("/" + physical)
        if response.status_code == 404:
            es.put("/" + physical, json=mapping).raise_for_status()
        else:
            response.raise_for_status()
        payload = "".join(
            json.dumps({"index": {"_index": physical, "_id": item["id"]}})
            + "\n"
            + json.dumps(item)
            + "\n"
            for item in records
        )
        response = es.post(
            "/_bulk",
            content=payload,
            headers={"Content-Type": "application/x-ndjson"},
            params={"refresh": "wait_for"},
        )
        response.raise_for_status()
        if response.json().get("errors"):
            raise RuntimeError("Aggregate fact item failed")
        read = es.get("/" + physical + "/_count")
        read.raise_for_status()
        if read.json()["count"] != len(records):
            raise RuntimeError("Aggregate fact count mismatch")
        previous = es.get("/_alias/" + FACT_ALIAS)
        if not previous.is_success and previous.status_code != 404:
            previous.raise_for_status()
        actions = [
            {"remove": {"index": name, "alias": FACT_ALIAS}}
            for name in (previous.json() if previous.is_success else {})
            if name != physical
        ]
        actions.append({"add": {"index": physical, "alias": FACT_ALIAS}})
        es.post("/_aliases", json={"actions": actions}).raise_for_status()
    objects = saved_objects(report)
    content = "\n".join(json.dumps(item) for item in objects) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content)
    with httpx.Client(
        base_url=args.kibana,
        auth=("elastic", infra["ELASTIC_PASSWORD"]),
        headers={"kbn-xsrf": "sellergenome-import"},
        timeout=120,
    ) as kibana:
        response = kibana.post(
            "/api/saved_objects/_import",
            params={"overwrite": "true"},
            files={
                "file": ("sellergenome.ndjson", content, "application/ndjson")
            },
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("success") or result.get("successCount") != len(
            objects
        ):
            # Saved-object errors contain specs only; credentials are never emitted.
            raise RuntimeError(
                "Kibana import did not verify every object: "
                + json.dumps(result.get("errors", []))
            )
        for identifier in ["sg-corpus-atlas", "sg-persona-evidence"]:
            kibana.get(
                "/api/saved_objects/dashboard/" + identifier
            ).raise_for_status()
    print(
        json.dumps(
            {
                "status": "verified",
                "data_views": 24,
                "visualizations": 8,
                "dashboards": 2,
                "imported_objects": len(objects),
                "aggregate_facts": len(records),
                "raw_rows": report["total_rows"],
            }
        )
    )


if __name__ == "__main__":
    main()
