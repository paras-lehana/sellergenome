# Seller Persona deployment

## Current Elastic access — 8 October 2026

Open https://elastic.lehana.in and https://kibana.lehana.in; both have
aidhunik.com mirrors. Use username `sellergenome_pm` and the existing privately
supplied PM password in the browser's authentication dialog. The gateway verifies
its salted hash, then uses a separate strong private native account scoped to
project data and the SellerGenome Space. No anonymous data access or host9200
port is introduced. The dashboard Space is `/s/sellergenome` with IDs
`sg-pm-corpus-atlas` and `sg-pm-persona-evidence`.

See [public access and recovery](../docs/contracts/elastic-publication.md). Existing
loopback5602 remains for authenticated maintenance APIs; use HTTPS for browser
access because native session cookies are now Secure. Historical private-only
notes below are superseded. The Elasticsearch container/volume was not recreated.

The source stays at `/opt/sellergenome`. `/root/docker/seller-persona`
contains symlinks to the versioned files in this directory, so operations and
review use one source of truth. Docker images contain dependencies only.

## Configuration and startup

Secrets are outside git and have mode `0600`:

- `/opt/secrets/env_files/seller-persona.env`: application/operator/provider keys
  and the scoped Elasticsearch username/password.
- `/opt/secrets/env_files/seller-persona-infra.env`: Elasticsearch bootstrap
  administrator, Kibana system password and encryption secret. The app never
  receives this file.

For first installation on the rig, run
`python3 /opt/sellergenome/deploy/bootstrap-secrets.py` as root.
It uses the already-authorized SSH connection to copy platform credentials
privately and refuses to replace existing files. Provision a provider-specific
hackathon key in that private file if it differs from the platform account.

Use the same environment file on every Compose command. Printing a resolved
`docker compose config` exposes secrets; use `config --quiet` for validation.

```bash
cd /root/docker/seller-persona
docker compose --env-file /opt/secrets/env_files/seller-persona-infra.env config --quiet
docker compose --env-file /opt/secrets/env_files/seller-persona-infra.env up -d elasticsearch
# Run after Elasticsearch is ready; passwords stay in process memory.
python3 /opt/sellergenome/deploy/bootstrap-elastic.py
docker compose --env-file /opt/secrets/env_files/seller-persona-infra.env up -d kibana
docker volume create seller-persona-data
# If the offline importer populated runtime/persona.sqlite3, migrate it BEFORE
# the first app start (the helper refuses to overwrite a volume database).
python3 /opt/sellergenome/deploy/migrate-runtime.py
docker compose --env-file /opt/secrets/env_files/seller-persona-infra.env up -d --build app
# Run after app mappings and Kibana are ready.
python3 /opt/sellergenome/deploy/bootstrap-kibana.py
```

The app listens on container port 8000. Traefik strips the exact public prefix
`/hackathon/seller-persona`; assets and API URLs still use `PUBLIC_BASE_PATH` in
the browser. Elasticsearch has no published port and belongs only to this
project's private Docker network. Kibana is loopback-only on rig port 5602.

## Routing

Public URLs:

- https://portfolio.aidhunik.com/hackathon/seller-persona
- https://portfolio.lehana.in/hackathon/seller-persona

The VPS owns HTTPS and forwards only this prefix through the owned
`vm-seller-persona` gateway (hostname `http://rig.lehana.in`). Its healthcheck
uses the exact application path and `portfolio.lehana.in` Host header. The
shared `vm-traefik` gateway was already unhealthy during launch and was left
unchanged. The owned route has priority 150, above the existing
portfolio root route's 130. It requires a path boundary, so names such as
`/hackathon/seller-persona-other` remain on the existing portfolio site.
No profile container, shared gateway service or DNS record needs changing.

After the application is healthy, install the additive VPS route:

```bash
ssh root@backend python3 - /root/traefik_dynamic.yml < deploy/install-route.py
```

The installer adds only this router and its gateway, validates YAML, backs up the config and writes in place to
preserve Docker's file bind mount. Verify Traefik's live router API and both
public URLs. Do not restart shared Traefik unless a verified reload problem
requires it.

## Private Kibana access

Open an SSH tunnel from your own workstation:

```bash
ssh -L 5602:127.0.0.1:5602 root@rig.lehana.in
```

Visit `http://127.0.0.1:5602` and use the project analyst account provisioned by
`bootstrap-elastic.py`. Retrieve credentials from the secret store locally;
never put them in chat, screenshots, browser bundles, README or git.

The private dashboard is
`http://127.0.0.1:5602/app/dashboards#/view/seller-persona-evidence`.
It has five project data views and ten panels for indexed counts, profile,
method, language, source type, demo/supplied mode, adaptation signals and stored
inference provenance. `bootstrap-kibana.py` recreates its owned saved objects;
`kibana-dashboard.ndjson` is the export from this pinned Kibana version, including
migration metadata and linked data views. It contains configuration only, not
seller documents. Kibana Saved Objects import can restore it on a compatible
installation. Refresh data views after any schema migration.

## Maintenance and rollback

Code-only changes: restart the app after tests. Dependency changes: rebuild the
app. Environment, labels or healthcheck changes: recreate the affected service.
One app worker preserves straightforward SQLite write coordination.

Rollback an application release by checking out the previous known-good commit
and recreating only `app`. To remove public routing, remove only
`http.routers.portfolio-seller-persona` and `http.services.vm-seller-persona`
from the VPS YAML in place. Do not restore
an entire old VPS file over unrelated newer changes. Existing portfolio routes
then regain this subpath automatically.

Never use `docker compose down -v`. Elasticsearch snapshots must use its native
snapshot API; use SQLite's backup API for its live database. Named-volume
filesystem copies alone are not a verified recoverable Elasticsearch backup.
The deployment plan records backup readiness and remaining production work.
