---
name: sellergenome-elastic
description: Query, extend and troubleshoot SellerGenome's Elasticsearch 8.19 data — the V5 indices (sellergenome-v5-personas, persona-factors, factor-registry, persona-evals, api-responses cache, events, suggestions), the strict mappings and ensure() mapping updates, the outbox, the API-response cache keys, and the older V4/V3 and raw research indices. Use whenever you need a count or document from ES, add or change a field or index, see ES 403/400/outbox growth, verify that writes reached the index, build an ES-backed feature (persona search, facets), or answer "is it stored in Elastic".
---

# Elasticsearch (sellergenome-v5-*)

Single node `seller-persona-elasticsearch` (alias `elasticsearch`, network `seller-persona-private`, volume
`seller-persona-elastic-data`, no host port). There is another ES on this host (`sarvam-voice-es`) — not ours.
**Never delete volumes, indices or the outbox; never `docker compose down -v`.**

## Query it (read-only helper, runs inside the app container with its credentials)

```bash
E=.claude/skills/sellergenome-elastic/scripts/es.py
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - indices                          < $E
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - mapping sellergenome-v5-personas < $E
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - count sellergenome-v5-api-responses '{"term":{"origin":"live_api"}}' < $E
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - agg sellergenome-v5-persona-evals eval_id score < $E
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - search sellergenome-v5-personas \
  '{"size":5,"query":{"term":{"call_plan_trigger":"open_request"}},"collapse":{"field":"seller_id"},"sort":[{"revision":"desc"}]}' < $E
```

Never print `os.environ` or the compose config. ES has no host port, so queries run inside the app container (whose
`/app` is the production worktree). `es.py` validates the index argument (letters, digits, `*,._-` only) so a crafted
index can't turn a read into a write. App health: `GET /api/v5/status` → `elastic.status`, `outbox`.

## V5 index inventory (prefix `elastic.index_prefix: sellergenome-v5`, locked; physical `-v1` behind an alias, 1 shard, 0 replicas, `dynamic: strict`)

| Kind | Holds | Id | Notes |
|---|---|---|---|
| `personas` | one doc per seller **revision** | `seller:r{rev}` | `latest` is true on all revisions — collapse on `seller_id`, sort `revision` desc. 5.5 fields: `engine`, `schema_version`, `segment`, `call_plan_trigger`, `quality_score`, `quality_critical`, `dial_pace/mix/formality/warmth`, `generated` (stored, not indexed) |
| `persona-factors` | one doc per seller × factor (typed slots `value_keyword/number/bool/text`) | `{seller}:{factor}` | LLM-invented factor names are **values**, never new fields |
| `factor-registry` | novel factor names, first/last seen | factor name | no `at` field |
| `persona-evals` | one doc per evaluated case | `{eval_id}:{n}` | `score`, `raw_score`, `critical`, `failed_checks`, `expected`, `plan`; `at` = run start; error rows not indexed |
| `api-responses` | responses with status ok, `persist` on, `ttl_hours > 0`, no override = the cache | new id per retrieval | `request`/`response` not indexed; dataset/ttl-0 APIs never stored |
| `events` | orchestrator run events | `{run}:{seq}` | |
| `suggestions` | PM suggestions/edits | | |

Older namespaces in the same cluster: `sellergenome-v4-preview-*` (V4 ops), `seller-persona-v4-*` (V1–V3.1 code; this
is what `ELASTICSEARCH_INDEX_PREFIX` controls — **not** V5), raw research `sellergenome-raw-*` (read-only for the app;
per-file aliases + `sellergenome-raw-family-*`; aggregate on `metrics.*`, never count the wildcard — it includes old
generations), `sellergenome-persona-fields`, `sellergenome-analytics-facts`.

## Access and roles

App user `sellergenome_v4_app` (env `ELASTICSEARCH_URL/USERNAME/PASSWORD` in `seller-persona-v4.env`): read/write/
manage on `sellergenome-v5-*`, `sellergenome-v4-preview-*`, `seller-persona-v4-*`; plus role `sellergenome_reader`
(read on `sellergenome-raw-*` and `sellergenome-analytics-*`, write on `sellergenome-persona-fields*`); nothing else. The role is maintained by hand as the `elastic` superuser — **a new prefix means a role change first**
(every V5 write got 403 until `sellergenome-v5-*` was added). PM read access through the public gateway: skill
`sellergenome-kibana`.

## Add a field or index safely

1. Add the field to `MAPPINGS` in `backend/genome/elastic.py` **and** to the writer (`persona_index.py`, `api.py`, …)
   in the same release; never change an existing field's type.
2. Restart the app: `ensure()` PUTs `_mapping` onto the existing `-v1` index (how 5.5 added fields). A new kind only
   needs a `MAPPINGS` entry — index + alias are created.
3. Verify: `es.py mapping <alias>` shows the field, trigger one write, `es.py search` reads it back, and the UI shows it.
4. A strict-mapping conflict makes `ensure()` fail → `elastic.status` "unavailable", writes stop, the outbox grows and
   retries forever (no dead letter). Fix the mapping, don't truncate the outbox.
5. A type change needs a `-v2` index, reindex and an atomic `_aliases` swap (code hard-codes `-v1` today).

## The API-response cache

Key = `sha256({api, sorted values incl. glid/empid, rev: registry revision, salt})[:32]`; salt = `"mock <app.version>"`
only for mocks (5.5 fix — 5.4-shaped mocks were served to 5.5). Any Admin → APIs edit bumps the registry revision and
invalidates every key. Read when `api.cache_read` and `elastic.cache_reads` are on, not forced/overridden and
`ttl_hours > 0`; query = term `cache_key` + `expires_at > now` + `status: ok`, newest first → origin `elastic_cache`.
TTLs: company 720 h, context/insights/PNS/STS/Redash 24 h, leads/enquiries 6 h, lead detail 168 h, LMS 2 h, dataset 0.
Expired docs are never deleted (no ILM yet).

## Useful queries

- Factor search used by `/genome`: `GET /api/v5/genome/search?factor=primary_objection&value=price` (anonymous →
  synthetic only). The factor is `primary_objection` (AGENTS case 55 and the old Elastic doc said `objection_primary`).
- Latest live response for a GLID: `origin:live_api AND api:<id> AND glid:<glid> AND status:ok`, sort `at` desc.
- Personas: `call_plan_trigger:open_request AND quality_critical:0` (KQL).
- Eval history: `es.py agg sellergenome-v5-persona-evals eval_id score` (98.1 → 98.9 for the 5.5 runs).
