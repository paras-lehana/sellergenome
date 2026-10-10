"""Provision project-scoped native Elastic login and a dedicated Kibana Space.

Credentials are read/write private files only. Existing service accounts, indices,
default-space objects and provider keys are not modified or reset.
"""

import argparse
import json
import os
import secrets
import base64
import subprocess
from pathlib import Path

import httpx

from scripts.configure_research_access import read_env

USER = "sellergenome_pm"
SPACE = "sellergenome"
ROLE = "sellergenome_workshop"


def space_objects(objects: list[dict]) -> list[dict]:
    """Use stable globally unique IDs; copying default IDs can change deep links."""
    identifiers = {
        item["id"]: "sg-pm-" + item["id"].removeprefix("sg-")
        for item in objects
    }
    return [
        {
            **item,
            "id": identifiers[item["id"]],
            "references": [
                {**ref, "id": identifiers.get(ref["id"], ref["id"])}
                for ref in item.get("references", [])
            ],
        }
        for item in objects
    ]


def provision(
    es_url: str,
    kibana_url: str,
    infra_path: Path,
    qa_path: Path,
    output_path: Path,
    bundle_path: Path,
    gateway_path: Path,
) -> dict:
    """Use supported APIs and verify login/permissions before saving access config."""
    infra, qa = read_env(infra_path), read_env(qa_path)
    existing = read_env(output_path) if output_path.exists() else {}
    password = existing.get("ELASTIC_PM_PASSWORD") or secrets.token_urlsafe(40)
    auth = ("elastic", infra["ELASTIC_PASSWORD"])
    with (
        httpx.Client(base_url=es_url, auth=auth, timeout=60) as es,
        httpx.Client(
            base_url=kibana_url,
            auth=auth,
            timeout=90,
            headers={"kbn-xsrf": "sellergenome-publication"},
        ) as kibana,
    ):
        current = es.get("/_security/user/" + USER)
        if current.is_success:
            metadata = current.json()[USER].get("metadata", {})
            if metadata.get("managed_by") != "seller-persona-publication":
                raise RuntimeError(
                    "Native username already belongs to another owner"
                )
        elif current.status_code != 404:
            current.raise_for_status()
        existing_space = kibana.get("/api/spaces/space/" + SPACE)
        if existing_space.status_code == 404:
            kibana.post(
                "/api/spaces/space",
                json={
                    "id": SPACE,
                    "name": "SellerGenome",
                    "initials": "SG",
                    "color": "#237569",
                    "description": "SellerGenome indexed seller evidence and persona deconstruction.",
                    "disabledFeatures": [],
                },
            ).raise_for_status()
        else:
            existing_space.raise_for_status()
            if existing_space.json().get("name") != "SellerGenome":
                raise RuntimeError(
                    "Kibana Space already belongs to another owner"
                )
        kibana.put(
            "/api/security/role/" + ROLE,
            json={
                "elasticsearch": {
                    "cluster": ["monitor"],
                    "indices": [
                        {
                            "names": ["seller-persona-*", "sellergenome-*"],
                            "privileges": ["read", "view_index_metadata"],
                            "allow_restricted_indices": False,
                        },
                        # Exact metadata-only permission for Kibana's global
                        # Streams readiness check. No logs documents/write access.
                        {
                            "names": ["logs"],
                            "privileges": ["view_index_metadata"],
                            "allow_restricted_indices": False,
                        },
                    ],
                },
                "kibana": [
                    {"base": ["all"], "feature": {}, "spaces": [SPACE]},
                    # Kibana8.19 checks Streams readiness from every page.
                    # Its verified plugin requires global read for that check;
                    # no stream management or other-Space saved-object privilege.
                    {
                        "base": [],
                        "feature": {"streams": ["read"]},
                        "spaces": ["*"],
                    },
                ],
            },
        ).raise_for_status()
        es.put(
            "/_security/user/" + USER,
            json={
                "password": password,
                "roles": [ROLE],
                "full_name": "SellerGenome PM",
                "metadata": {"managed_by": "seller-persona-publication"},
                "enabled": True,
            },
        ).raise_for_status()
        objects = space_objects(
            [
                json.loads(line)
                for line in bundle_path.read_bytes().splitlines()
                if line
            ]
        )
        content = (
            "\n".join(json.dumps(item) for item in objects) + "\n"
        ).encode()
        imported = kibana.post(
            "/s/" + SPACE + "/api/saved_objects/_import",
            params={"overwrite": "true"},
            files={
                "file": ("sellergenome.ndjson", content, "application/ndjson")
            },
        )
        imported.raise_for_status()
        result = imported.json()
        if not result.get("success") or result.get("successCount") != len(
            objects
        ):
            raise RuntimeError(
                "SellerGenome Space import did not verify every object"
            )
        for identifier in ("sg-pm-corpus-atlas", "sg-pm-persona-evidence"):
            kibana.get(
                "/s/" + SPACE + "/api/saved_objects/dashboard/" + identifier
            ).raise_for_status()
    with httpx.Client(
        base_url=es_url, auth=(USER, password), timeout=30
    ) as es:
        es.get("/").raise_for_status()
        count = es.get("/sellergenome-raw-family-*/_count")
        count.raise_for_status()
        assert count.json()["count"] == 908856
        assert es.get("/_security/user").status_code == 403
        denied = es.post(
            "/sellergenome-raw-family-bot-calls/_update/permission-probe",
            json={"doc": {"test": True}},
        )
        assert denied.status_code == 403
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(descriptor, "w") as handle:
        handle.write("ELASTIC_PM_USERNAME=" + USER + "\n")
        handle.write("ELASTIC_PM_PASSWORD=" + password + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(output_path)
    # Preserve the owner's short PM password without weakening Elastic's policy.
    # The public gateway validates this salted hash, then authenticates as the
    # separately scoped native user. Neither credential belongs in source/config.
    gateway_path.mkdir(parents=True, exist_ok=True)
    gateway_path.chmod(0o750)
    os.chown(gateway_path, 0, 101)
    hashed = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=qa["PM_PASSWORD"] + "\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    encoded = base64.b64encode((USER + ":" + password).encode()).decode()
    for name, content in {
        "htpasswd": USER + ":" + hashed + "\n",
        "native-auth.conf": 'proxy_set_header Authorization "Basic '
        + encoded
        + '";\n',
    }.items():
        destination = gateway_path / name
        destination.write_text(content)
        destination.chmod(0o640)
        os.chown(destination, 0, 101)
    return {
        "status": "verified",
        "username": USER,
        "space": SPACE,
        "saved_objects": len(objects),
        "raw_rows": 908856,
        "security_admin_denied": True,
        "data_write_denied": True,
    }


def main() -> None:
    """Explicit authorized bootstrap; passwords never become CLI values/output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--es-url", required=True)
    parser.add_argument("--kibana", default="http://127.0.0.1:5602")
    parser.add_argument("--infra-env", type=Path, required=True)
    parser.add_argument("--qa-env", type=Path, required=True)
    parser.add_argument("--output-env", type=Path, required=True)
    parser.add_argument("--gateway-secrets", type=Path, required=True)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("deploy/kibana/sellergenome.ndjson"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            provision(
                args.es_url,
                args.kibana,
                args.infra_env,
                args.qa_env,
                args.output_env,
                args.bundle,
                args.gateway_secrets,
            )
        )
    )


if __name__ == "__main__":
    main()
