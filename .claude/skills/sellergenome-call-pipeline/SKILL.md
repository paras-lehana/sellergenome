---
name: sellergenome-call-pipeline
description: Understand and change what happens when someone barges into a call in SellerGenome — the preparation run (orchestrator phases, parallel source fetches, contract assembly, persona engine choice, locality, persona index, teleprompter), its server-sent events and the architecture graph nodes with origin colours, the inspector's re-run/fork, run states (completed/degraded/failed) and caching/fallbacks. Use when a run is slow, stuck, degraded or shows the wrong node/origin, when adding a pipeline step or graph node, when changing what a run fetches, or when explaining "what happens after Barge in" to a PM or judge.
---

# The preparation run (barge-in → workspace)

## Browser side

`floor.js` → `POST /api/v5/runs {seller_id, origin}` (visibility `can_see`; 20 runs/min public, 60 operator) →
navigate to `/call/{run}` → `call.js` fetches `GET /api/v5/architecture` (planned mode per node) and opens SSE
`GET /api/v5/runs/{id}/stream` (`?after=`/`Last-Event-ID` resume; `end` ≈4 s after completion so ES persist acks
arrive; 90 s cap) → the "Barging in" console streams events → workspace (persona, call, architecture panes).

## Server phases (`backend/genome/orchestrator.py` `_execute`)

1. `trigger`, `orchestrator` events.
2. **Phase 1 (parallel, semaphore `runs.max_concurrent_sources`, timeout `runs.timeout_seconds`)**:
   `SELLER_SOURCES_FIRST` incl. chains `company.alias → company.detail` (skipped without alias) and
   `context.global → context.getcontext` (mapping_id, else source_uid).
3. `contract.assemble` → `_confirm_identity` for `real-` sellers ("identity from live APIs") → `facts` event.
4. **Phase 2**: `SELLER_SOURCES_SECOND` + fan-outs `leads.detail:<offerid>` (≤ `upstream.max_leads`) and
   `lms.messages:<contact>` (both GLIDs allowed; in `upstream.glid_access` = prod the test contacts are skipped).
   Phase 1 and phase 2 each get their own `runs.timeout_seconds` (25 s), so a slow upstream can make a run take
   ≈50 s before the persona step.
5. **Phase 3 persona** by `persona.engine`: `generator` (`_generate`, cache kind `generations`, key prompt + input +
   model + app version) or `aggregator` (`_aggregate`, cache `aggregations`); TTL `llm.aggregation_cache_hours`.
   `SarvamError`/`BudgetExceeded`/`ValueError` → rules persona, origin `rules`, event status `fallback`.
   A generation/aggregation cache hit comes from SQLite but is emitted with origin `elastic_cache`, so the graph shows
   an ES hit — know this when explaining origins.
6. **Phase 4** `sarvam.locality` (cache `localities`).
7. **Phase 5** `persona.index` (ES queue), `teleprompter`, `voice.plugin` (waiting), `run` done → status
   `completed`; timeout → `degraded`; other exception → `failed`.

Each source hit goes through the API executor (skill `sellergenome-indiamart-apis`) and emits cache/response/skip/error
events with its origin.

## Events and the graph

`emit(run, node, kind, status, label, detail, origin=, ms=, ref=, extra={"es_query": …})` → SQLite `events` + ES `events` + in-memory
waiters. Kinds: start, cache, response, skip, error, fact, llm, index, persist, done. Graph node ids
(`frontend/cc/graph.js`): `trigger`, `gateway`, `orchestrator`, `elastic`, the 16 sources, `contract`
(`facts`/`identity` map here), `sarvam.aggregate` (label "Sarvam · persona generator v0.3" under the generator),
`sarvam.locality`, `persona.index`, `teleprompter`, `voice.plugin`; fan nodes `leads.detail`, `lms.messages`.
**Unknown node ids are silently ignored.** Origin colours: live_api, elastic_cache, local_db/supplied_export, mock,
llm, default, blank (skill `sellergenome-ui-command-center`).

Inspector: `GET /api/v5/runs/{id}/nodes/{node}` serves `results[node]` (request, response, timing, ES query);
`POST …/rerun` creates a **labelled fork** — the original receipt is never overwritten; live only for operators.

## Adding a pipeline step / node

1. Emit events from `_execute` with a new node id and put the inspector payload in `results["<node>"]`.
2. Frontend: add the id to `LABEL`, its position in `layout()` and an edge in `graph.js`; add it to the right `FLOW`
   stage in `call.js` (business-flow stepper: Seller activity → ~15 min window → Context fetch → Persona → Bot call →
   Meeting fixed → Human executive). A new origin also needs `BADGE`/`STRIPE` and `contract.ORIGINS`.
3. Respect the fallback rule: a failed step degrades the run with an honest event, it never pretends success.
4. Test: run a synthetic hero in the browser and read the barge console; check the node's inspector.

## Debugging a run

`GET /api/v5/runs/{id}` (status, timings) · the events list in the barge console · `/api/v5/status` (ES outbox,
budget) · `docker logs --tail 100 sellergenome-v4`. One app worker only: runs, SSE waiters, rate buckets and tickets
live in process memory — a restart drops in-flight runs.
