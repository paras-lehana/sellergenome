---
name: sellergenome-kibana
description: Give people access to and build views on SellerGenome's data in Kibana — the authenticated public gateway (elastic.lehana.in, kibana.lehana.in and aidhunik mirrors), the PM login and private browser handoff, the `sellergenome` Space with its research and V4 operations dashboards (Vega charts, data views), and how to add V5 dashboards (persona factors, eval scores, cache hit rate) by saved-object import. Use when a PM or judge needs Kibana, a dashboard is missing or broken, the PM password changes, you add charts for new V5 data, or someone asks to "show it in Kibana".
---

# Kibana and the Elastic gateway

## What exists

- Public, authenticated: https://kibana.lehana.in and https://elastic.lehana.in (+ `.aidhunik.com` mirrors) through
  container `sellergenome-elastic-gateway` (Nginx: Basic auth against an htpasswd hash, then injects the native
  Authorization header). Browser sign-in without a `sid` cookie goes to `/__native_login` → service
  `sellergenome-elastic-access` re-checks the PM password and creates a real Kibana session (1 h idle, 12 h max),
  allowing only `next` paths under `/s/sellergenome/`. Kibana REST under `/api/` stays Basic.
- Loopback Kibana for admin scripts: `http://127.0.0.1:5602`.
- PM user `sellergenome_pm` (role `sellergenome_workshop`): read on `seller-persona-*` and `sellergenome-*` (includes
  V5), Kibana `base:all` only in Space `sellergenome`. Reader/publisher accounts exist for research indices.
- Space `sellergenome`: dashboards `sg-pm-corpus-atlas` (6 panels), `sg-pm-persona-evidence` (4),
  `sg-v4-operations` (10); 31 data views (24 `sg-pm-view-*`, 7 `sg-v4-*`); 18 Vega charts.
- **No V5 data views or dashboards yet** (pending since 5.0.1; suggested again in 5.5).

Live gateway config is mounted from `.worktrees/v3` (`/root/docker/sellergenome-elastic/`), not v5 — editing the v5
copy of `deploy/elastic/` changes nothing in production.

## Add a V5 dashboard (saved-object import; never write `.kibana` directly)

1. Copy the pattern of `scripts/install_v4_dashboards.py` (imports into `http://127.0.0.1:5602/s/sellergenome` as the
   `elastic` superuser from `/opt/secrets/env_files/seller-persona-infra.env`, checks `successCount`, reads back).
2. Data views: `title: sellergenome-v5-<kind>`, `timeFieldName: at` (not for `factor-registry`).
   Ids must be unique and prefixed (`sg-v5-*`) — Kibana 8 object ids are global.
3. Charts worth having: eval score and raw score by `eval_id`/`prompt`, top `failed_checks`; personas by
   `call_plan_trigger`, `segment`, `engine`; factor `primary_objection` mix; novel factor trend; cache hit rate
   (`api-responses` by `origin`).
4. Vega panels ignore dashboard filters and the time picker — say so on the dashboard or use Lens.
5. Write the NDJSON to `/root/data/qa-artifacts/<date>-kibana/`, import, then open it signed in at 1440 and 390 and look.

## PM password change

Rerun `scripts/configure_elastic_portal.py --es-url … --infra-env … --qa-env … --output-env … --gateway-secrets …`
(ES has no host port — the URL must be reachable from inside network `seller-persona-private`), recreate the access
service (`docker compose up -d` in `/root/docker/sellergenome-elastic`), restart the gateway. `PM_PASSWORD`
(`/opt/secrets/env_files/seller-persona-v3-qa.env`) feeds only the gateway hash and the access-service check; the
native PM login uses a separate random `ELASTIC_PM_PASSWORD` in `sellergenome-elastic-public.env`; gateway hash in
`/opt/secrets/sellergenome-elastic-gateway/`. Kibana's own compose is `/root/docker/seller-persona` (a symlink to the
root checkout's `deploy/`).

## Safety

- Screenshots from Discover can show private fields — keep shared screenshots aggregate-only.
- To cut public access, stop only the gateway container; recreate only Kibana, never Elasticsearch.
- Never print the resolved compose config, `nginx -T` or env files.
- Public monitors only check that an unauthenticated request gets 401; Cloudflare may answer 403/1010 to Python urllib
  on the aidhunik mirror — check headers before calling it an app error.
- Browser cases 29–31 (legacy list in `docs/history/v4/AGENTS-v4.md`) cover gateway access.
