#!/usr/bin/env python3
"""Create project data views and the private evidence dashboard in Kibana8.19.

Stable owned IDs make this repeatable. Only matching project saved objects are
overwritten. The live export includes migration metadata for portable backups.
No seller documents or credentials are written into the exported artifact.
"""

import base64
import json
from pathlib import Path
import urllib.request

secret_path = Path("/opt/secrets/env_files/seller-persona-infra.env")
secrets = dict(
    line.split("=", 1)
    for line in secret_path.read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
token = base64.b64encode(
    (
        secrets["KIBANA_ANALYST_USERNAME"]
        + ":"
        + secrets["KIBANA_ANALYST_PASSWORD"]
    ).encode()
).decode()
headers = {
    "Authorization": "Basic " + token,
    "Content-Type": "application/json",
    "kbn-xsrf": "seller-persona-bootstrap",
}
base = "http://127.0.0.1:5602"


def request(method, path, body=None, raw=False):
    req = urllib.request.Request(
        base + path,
        method=method,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read() if raw else json.load(response)


for kind in ("sellers", "evidence", "personas", "switches", "recordings"):
    request(
        "POST",
        "/api/data_views/data_view",
        {
            "override": True,
            "data_view": {
                "id": "persona-" + kind,
                "title": "seller-persona-" + kind + "-v1",
                "name": "Seller Persona · " + kind.title(),
                "allowNoIndex": True,
            },
        },
    )

objects = []
panels = []
references = []


def add_panel(kind, identifier, attributes, source, x, y, w, h):
    view_ref = "kibanaSavedObjectMeta.searchSourceJSON.index"
    attributes["kibanaSavedObjectMeta"] = {
        "searchSourceJSON": json.dumps(
            {
                "query": {"query": "", "language": "kuery"},
                "filter": [],
                "indexRefName": view_ref,
            }
        )
    }
    objects.append(
        {
            "type": kind,
            "id": identifier,
            "attributes": attributes,
            "references": [
                {
                    "name": view_ref,
                    "type": "index-pattern",
                    "id": "persona-" + source,
                }
            ],
        }
    )
    panel = str(len(panels) + 1)
    ref = "panel_" + panel
    panels.append(
        {
            "version": "8.19.23",
            "type": kind,
            "panelIndex": panel,
            "panelRefName": ref,
            "gridData": {"x": x, "y": y, "w": w, "h": h, "i": panel},
            "embeddableConfig": {},
        }
    )
    references.append({"name": ref, "type": kind, "id": identifier})


def visualization(identifier, title, source, field, x, y, w=24, h=15):
    aggs = [
        {
            "id": "1",
            "enabled": True,
            "type": "count",
            "schema": "metric",
            "params": {},
        }
    ]
    chart_type = "pie" if field else "metric"
    params = {"addTooltip": True, "addLegend": bool(field)}
    if field:
        params.update(
            {"type": "pie", "isDonut": True, "legendPosition": "right"}
        )
        aggs.append(
            {
                "id": "2",
                "enabled": True,
                "type": "terms",
                "schema": "segment",
                "params": {
                    "field": field,
                    "orderBy": "1",
                    "order": "desc",
                    "size": 8,
                    "otherBucket": False,
                },
            }
        )
    else:
        params["metric"] = {
            "percentageMode": False,
            "useRanges": False,
            "metricColorMode": "None",
            "colorSchema": "Green to Red",
            "colorsRange": [{"from": 0, "to": 10000}],
            "invertColors": False,
            "labels": {"show": True},
            "style": {
                "bgFill": "#000",
                "bgColor": False,
                "labelColor": False,
                "subText": "",
                "fontSize": 52,
            },
        }
    attrs = {
        "title": title,
        "description": "Live aggregation of this project's Elasticsearch documents.",
        "uiStateJSON": "{}",
        "version": 1,
        "visState": json.dumps(
            {
                "title": title,
                "type": chart_type,
                "params": params,
                "aggs": aggs,
            }
        ),
    }
    add_panel("visualization", identifier, attrs, source, x, y, w, h)


for offset, (kind, title) in enumerate(
    (
        ("sellers", "Indexed sellers"),
        ("evidence", "Evidence documents"),
        ("personas", "Current personas"),
    )
):
    visualization(
        "persona-count-" + kind, title, kind, None, offset * 16, 0, 16, 9
    )
for identifier, title, source, field, x, y in (
    (
        "seller-styles",
        "Seller delivery profiles",
        "sellers",
        "persona_tag",
        0,
        9,
    ),
    (
        "persona-methods",
        "Persona inference method",
        "personas",
        "method",
        24,
        9,
    ),
    (
        "seller-languages",
        "Configured delivery language",
        "sellers",
        "language",
        0,
        24,
    ),
    (
        "evidence-source-mode",
        "Evidence: demo vs supplied data",
        "evidence",
        "source_mode",
        24,
        24,
    ),
    ("switch-signals", "Live adaptation signals", "switches", "signal", 0, 39),
    ("evidence-kinds", "Evidence source types", "evidence", "kind", 24, 39),
):
    visualization("persona-" + identifier, title, source, field, x, y)
add_panel(
    "search",
    "persona-inference-provenance",
    {
        "title": "Indexed persona provenance",
        "description": "Inspect the exact stored method, model and trait payload.",
        "columns": ["seller_id", "method", "model", "generated_at", "traits"],
        "sort": [["generated_at", "desc"]],
        "version": 1,
    },
    "personas",
    0,
    54,
    48,
    18,
)

objects.append(
    {
        "type": "dashboard",
        "id": "seller-persona-evidence",
        "references": references,
        "attributes": {
            "title": "Seller Persona · Evidence & adaptation",
            "description": "Private project dashboard. Counts describe current indexed records, not a revision history; demo and supplied data are separate source_mode values. Delivery fields may use documented defaults: inspect provenance before treating them as observed preferences. Inference method remains explicit. These charts do not prove causal conversion uplift.",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": json.dumps(
                {"useMargins": True, "hidePanelTitles": False}
            ),
            "version": 1,
            "timeRestore": False,
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": json.dumps(
                    {"query": {"query": "", "language": "kuery"}, "filter": []}
                )
            },
        },
    }
)
result = request(
    "POST", "/api/saved_objects/_bulk_create?overwrite=true", objects
)
errors = [
    (entry["id"], entry.get("error", {}).get("statusCode"))
    for entry in result["saved_objects"]
    if entry.get("error")
]
if errors:
    raise SystemExit("Saved-object errors: " + str(errors))
export = request(
    "POST",
    "/api/saved_objects/_export",
    {
        "objects": [{"type": "dashboard", "id": "seller-persona-evidence"}],
        "includeReferencesDeep": True,
    },
    raw=True,
)
output = Path(__file__).parent / "kibana-dashboard.ndjson"
output.write_bytes(export)
print(
    "Created5 data views and10 dashboard panels; portable export:", output.name
)
print(
    "Private dashboard: http://127.0.0.1:5602/app/dashboards#/view/seller-persona-evidence"
)
