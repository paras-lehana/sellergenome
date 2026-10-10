"""Provision narrowly scoped research roles using a private infra credential file.

Run on the authorized rig host. The existing app password/roles are preserved.
The separate publisher password is generated once into a mode0600 environment
file and is never printed or passed in a command-line argument.
"""

import argparse
import os
import secrets
from pathlib import Path

import httpx


def read_env(path):
    """Read simple private environment values without shell evaluation."""
    return {
        key: value.strip().strip("\"'")
        for line in path.read_text().splitlines()
        if "=" in line and not line.lstrip().startswith("#")
        for key, value in [line.split("=", 1)]
    }


def main():
    """Create prefix-scoped roles; never grant a research worker cluster privileges."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--infra-env", type=Path, required=True)
    parser.add_argument("--app-env", type=Path, required=True)
    parser.add_argument("--publisher-env", type=Path, required=True)
    args = parser.parse_args()
    infra, app = read_env(args.infra_env), read_env(args.app_env)
    existing = (
        read_env(args.publisher_env) if args.publisher_env.exists() else {}
    )
    password = existing.get("ELASTICSEARCH_PASSWORD") or secrets.token_urlsafe(
        40
    )
    with httpx.Client(
        base_url=args.url,
        auth=("elastic", infra["ELASTIC_PASSWORD"]),
        timeout=30,
    ) as client:
        writer = {
            "cluster": [],
            "indices": [
                {
                    "names": [
                        "sellergenome-raw-*",
                        "sellergenome-analytics-*",
                        "sellergenome-persona-fields*",
                    ],
                    "privileges": ["create_index", "manage", "read", "write"],
                }
            ],
        }
        reader = {
            "cluster": [],
            "indices": [
                {
                    "names": [
                        "sellergenome-raw-*",
                        "sellergenome-analytics-*",
                    ],
                    "privileges": ["read", "view_index_metadata"],
                },
                {
                    "names": ["sellergenome-persona-fields*"],
                    "privileges": ["create_index", "manage", "read", "write"],
                },
            ],
        }
        for name, role in [
            ("sellergenome_publisher", writer),
            ("sellergenome_reader", reader),
        ]:
            client.put("/_security/role/" + name, json=role).raise_for_status()
        client.put(
            "/_security/user/sellergenome_publisher",
            json={
                "password": password,
                "roles": ["sellergenome_publisher"],
                "enabled": True,
            },
        ).raise_for_status()
        username = app["ELASTICSEARCH_USERNAME"]
        result = client.get("/_security/user/" + username)
        result.raise_for_status()
        roles = result.json()[username]["roles"]
        client.put(
            "/_security/user/" + username,
            json={"roles": sorted(set(roles + ["sellergenome_reader"]))},
        ).raise_for_status()
    if not args.publisher_env.exists():
        fd = os.open(
            args.publisher_env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(fd, "w") as handle:
            handle.write(
                "# Private bounded raw-index publisher; no cluster-wide privileges.\n"
            )
            handle.write(
                "ELASTICSEARCH_USERNAME=sellergenome_publisher\nELASTICSEARCH_PASSWORD="
                + password
                + "\n"
            )
    print(
        "Scoped publisher and aggregate reader roles configured; no secrets emitted."
    )


if __name__ == "__main__":
    main()
