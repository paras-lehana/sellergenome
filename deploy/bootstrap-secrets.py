#!/usr/bin/env python3
"""Create deployment secrets without printing values or modifying gateway state.

Run as root on the rig. Existing files are preserved. Platform keys travel over
SSH into process memory and directly into the root-only app environment file.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess

directory = Path("/opt/secrets/env_files")
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
app_path = directory / "seller-persona.env"
infra_path = directory / "seller-persona-infra.env"
if app_path.exists() or infra_path.exists():
    raise SystemExit(
        "Secret files already exist; rotate explicitly, never silently replace credentials."
    )

remote = """import json,subprocess
c=json.loads(subprocess.check_output(['docker','inspect','llm-service']))[0]
e=dict(s.split('=',1) for s in c['Config']['Env'] if '=' in s)
print(json.dumps({k:e.get(k,'') for k in ('ADMIN_INTERNAL_KEY','SMK_ANTIGRAVITY_KEY','SARVAM_API_KEY')}))
"""
result = subprocess.run(
    ["ssh", "-o", "BatchMode=yes", "root@backend", "python3", "-"],
    input=remote,
    text=True,
    capture_output=True,
    check=True,
)
platform = json.loads(result.stdout)
if not platform["ADMIN_INTERNAL_KEY"] or not platform["SMK_ANTIGRAVITY_KEY"]:
    raise SystemExit(
        "Required existing platform credentials absent; no files written."
    )

app = {
    "ELASTICSEARCH_URL": "http://elasticsearch:9200",
    "ELASTICSEARCH_USERNAME": "persona_app",
    "ELASTICSEARCH_PASSWORD": secrets.token_hex(32),
    "LLM_SERVICE_URL": "https://llm.lehana.in",
    "LLM_INTERNAL_KEY": platform["ADMIN_INTERNAL_KEY"],
    "LLM_API_KEY": platform["SMK_ANTIGRAVITY_KEY"],
    "LLM_PROVIDER": "antigravity",
    "LLM_MODEL": "gemini-3-flash",
    "SARVAM_API_KEY": platform["SARVAM_API_KEY"],
    "OPERATOR_API_KEY": secrets.token_urlsafe(48),
}
infra = {
    "ELASTIC_VERSION": "8.19.23",
    "ELASTIC_PASSWORD": secrets.token_hex(32),
    "KIBANA_SYSTEM_PASSWORD": secrets.token_hex(32),
    "KIBANA_ENCRYPTION_KEY": secrets.token_hex(32),
    "KIBANA_ANALYST_USERNAME": "persona_analyst",
    "KIBANA_ANALYST_PASSWORD": secrets.token_hex(32),
}
for path, values in ((app_path, app), (infra_path, infra)):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(
            "# Server-only deployment secrets. Never commit or print resolved values.\n"
        )
        for key, value in values.items():
            if any(c in value for c in "\r\n"):
                raise SystemExit(
                    "Unexpected multiline credential; refusing unsafe env serialization."
                )
            target.write(f"{key}={value}\n")
    print(path.name, "created mode0600; keys:", ", ".join(values))
