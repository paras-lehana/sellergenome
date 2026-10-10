#!/usr/bin/env python3
"""Provision only this project's scoped accounts after private ES starts.

Run on the rig host as root. No credentials are returned to stdout. The app
role is restricted to seller-persona-* and persona-* indexes and cluster
monitoring. Kibana's analyst gets read-only data access and Kibana editing.
"""

import base64
import json
from pathlib import Path
import subprocess
import urllib.request


def read_env(path):
    return dict(
        line.split("=", 1)
        for line in Path(path).read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


app = read_env("/opt/secrets/env_files/seller-persona.env")
infra = read_env("/opt/secrets/env_files/seller-persona-infra.env")
container = json.loads(
    subprocess.check_output(
        ["docker", "inspect", "seller-persona-elasticsearch"]
    )
)[0]
ip = container["NetworkSettings"]["Networks"]["seller-persona-private"][
    "IPAddress"
]
credential = base64.b64encode(
    ("elastic:" + infra["ELASTIC_PASSWORD"]).encode()
).decode()


def request(method, path, body):
    req = urllib.request.Request(
        f"http://{ip}:9200/{path}",
        data=json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": "Basic " + credential,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


request(
    "PUT",
    "_security/role/persona_app",
    {
        "cluster": ["monitor"],
        "indices": [
            {
                "names": ["seller-persona-*", "persona-*"],
                "privileges": [
                    "read",
                    "write",
                    "create_index",
                    "view_index_metadata",
                    "manage",
                ],
            }
        ],
    },
)
request(
    "PUT",
    "_security/user/persona_app",
    {"password": app["ELASTICSEARCH_PASSWORD"], "roles": ["persona_app"]},
)
request(
    "POST",
    "_security/user/kibana_system/_password",
    {"password": infra["KIBANA_SYSTEM_PASSWORD"]},
)
request(
    "PUT",
    "_security/role/persona_analyst_data",
    {
        "cluster": ["monitor"],
        "indices": [
            {
                "names": ["seller-persona-*", "persona-*"],
                "privileges": ["read", "view_index_metadata"],
            }
        ],
    },
)
request(
    "PUT",
    "_security/user/" + infra["KIBANA_ANALYST_USERNAME"],
    {
        "password": infra["KIBANA_ANALYST_PASSWORD"],
        "roles": ["persona_analyst_data", "kibana_admin"],
    },
)
print(
    "Provisioned persona_app, kibana_system and persona_analyst; secrets not displayed."
)
