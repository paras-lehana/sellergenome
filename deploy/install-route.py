#!/usr/bin/env python3
"""Install only our VPS route; preserve unrelated YAML text and its bind inode.

Run on backend over SSH, after the rig application responds successfully.
PyYAML is used to validate both the current configuration and the result.
"""

import datetime
import pathlib
import re
import shutil
import sys

import yaml

path = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "/root/traefik_dynamic.yml"
)
original = path.read_text()
current = yaml.safe_load(original)
name = "portfolio-seller-persona"
rule = r"HostRegexp(`^(vps-)?portfolio\.(lehana\.in|aidhunik\.com)$`) && (Path(`/hackathon/seller-persona`) || PathPrefix(`/hackathon/seller-persona/`))"
desired = {
    "rule": rule,
    "entryPoints": ["websecure"],
    "service": "vm-seller-persona",
    "tls": {},
    "priority": 150,
}
service = {
    "loadBalancer": {
        "servers": [{"url": "http://rig.lehana.in"}],
        "healthCheck": {
            "path": "/hackathon/seller-persona/health",
            "hostname": "portfolio.lehana.in",
            "interval": "30s",
            "timeout": "5s",
        },
    }
}
existing = current["http"]["routers"].get(name)
existing_service = current["http"]["services"].get("vm-seller-persona")
if existing == desired and existing_service == service:
    print("Route already configured correctly; no changes.")
    raise SystemExit(0)
legacy = {**desired, "service": "vm-traefik"}
if existing and existing not in (desired, legacy):
    raise SystemExit(
        "Existing owned route differs; inspect before changing it."
    )
if existing_service and existing_service != service:
    raise SystemExit(
        "Existing owned gateway differs; inspect before changing it."
    )


def yaml_block(value):
    return (
        "\n".join(
            "    " + line
            for line in yaml.safe_dump(value, sort_keys=False).splitlines()
        )
        + "\n"
    )


block = yaml_block({name: desired})
updated = original
if existing:
    pattern = r"(?m)^    portfolio-seller-persona:\n(?:^[ \t]{6,}.*\n|^\n)*"
    updated, count = re.subn(pattern, lambda _: block + "\n", updated)
    if count != 1:
        raise SystemExit(
            "Unexpected owned router layout; refusing broad rewrite."
        )
else:
    needle = "  routers:\n"
    if updated.count(needle) != 1:
        raise SystemExit(
            "Unexpected router layout; refusing broad file rewrite."
        )
    updated = updated.replace(
        needle,
        needle
        + "\n    # Seller Persona: portfolio root stays with profiles.\n"
        + block,
        1,
    )
if not existing_service:
    needle = "  services:\n"
    if updated.count(needle) != 1:
        raise SystemExit(
            "Unexpected service layout; refusing broad file rewrite."
        )
    updated = updated.replace(
        needle,
        needle
        + "\n    # App-specific rig health; shared gateway remains unchanged.\n"
        + yaml_block({"vm-seller-persona": service}),
        1,
    )
parsed = yaml.safe_load(updated)
expected = dict(current["http"]["routers"])
expected[name] = desired
assert parsed["http"]["routers"] == expected
expected_services = dict(current["http"]["services"])
expected_services["vm-seller-persona"] = service
assert parsed["http"]["services"] == expected_services
assert parsed["http"].get("middlewares") == current["http"].get("middlewares")
backup = (
    str(path)
    + ".seller-persona."
    + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + ".bak"
)
shutil.copy2(path, backup)
# Docker binds this file by inode; write in place rather than rename/sed -i.
with path.open("r+") as target:
    target.write(updated)
    target.truncate()
print(
    "Configured portfolio-seller-persona priority150 and owned rig health gateway; backup:",
    backup,
)
