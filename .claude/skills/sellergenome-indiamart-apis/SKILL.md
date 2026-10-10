---
name: sellergenome-indiamart-apis
description: Operate and extend SellerGenome's connections to IndiaMART's internal APIs — the API registry (company, Global Context userCtx/getContext, insights, buy leads, enquiries, PNS, LMS, STS, Redash, dataset, buyer tools), modes live/mock/off, the pass-through ngrok gateway, employee AK keys that expire in ~24 h, the 77 approved GLIDs, Admin → APIs editing/testing/reset, and adding a new upstream API end to end. Use when live data is missing or failing (404, 401 expired token, ERR_NGROK, "Mapping Not Found"), the owner sends a new gateway URL or key, an API must be added or re-pointed, or someone asks whether data is live or mocked.
---

# IndiaMART APIs, gateway and keys

IndiaMART hosts (`company.imutils.com`, `leads.imutils.com`, `enq2.intermesh.net`, `lms.imutils.com`,
`audio.imutils.com`, `merp.intermesh.net`) are unreachable from this server. A pass-through gateway (the "IM API
Portal", run inside IndiaMART's network behind ngrok) forwards `<gateway base> + original path`. Field meanings are in
skill `sellergenome-indiamart-data-rules`; test results in `docs/architecture/sources-and-apis.md`.

## The registry (`backend/genome/registry.py` → `DEFAULTS`)

`company.alias` → `company.detail` · `context.global` (generateContextUID since 5.10.1) → `context.getcontext` · `context.insights` ·
`leads.purchased` → `leads.detail` ×N · `enquiry.list` · `pns.insights` · `lms.messages` ×N · `sts.history` ·
`packages.catalog` · `redash.hotlead`, `redash.pns` · `dataset.seller`, `dataset.calls` · buyer tools
(`buyer.caller`, `buyer.product_context`, `buyer.seller_by_city`, `buyer.assisted_buy`, `buyer.lead_post`).
Registry URLs stay the **original** upstream URLs; the executor swaps scheme+host for the gateway at send time.

Executor policy (`ApiExecutor.call`): `off` → skipped · synthetic seller on a live API → its production-shaped mock ·
real seller → live only for an operator **and** an allowed GLID (`upstream.glid_access`: dev = approved, prod = any) **and** a URL; never a seller-specific mock (D18) ·
a failed live call stays failed (errors inside HTTP 200 — `RESPONSE.CODE "401"`, `{"status":401}` — count as
failures) → cache read/write (skill `sellergenome-elastic`) → hit log (Admin → APIs → hit log).

## Status (2026-10-10, 5.6.0)

- 5.12 / 5.13.5: **`market.listing`** returns the first 28 IndiaMART search results in the seller's main category.
  It is live through the gateway since 2026-10-10.
  - Endpoint: `company.imutils.com/wservce/products/listing/` with `token=COMPANY_TOKEN`, `modid=DIR`, `mcatid`,
    `start=1`, `end=28` and **`cityflname`**.
    - `mcatid` is `parsers.main_mcat` (company detail `BREADCRUMB.MCAT_ID`, else the commonest item mcat).
    - The city goes in `cityflname`, lower case with hyphens (`parsers.city_slug`). Plain `city` is ignored: `cityid`
      stays 0 and the rows are all-India.
    - An unknown slug answers an error, and `orchestrator.listing_with_fallback` asks again with no city.
  - Shape: `data[]` with `COMPANY`, `gl_id`, `city`, `member_since` ("14 yrs"), `wt_avg`, `total_ratings_cnt`,
    `Trustseal_url`, `glusr_paid_url`, `prd_price` and `unit`.
    - The top level has `cityid`, `mcatname`, `out_total_unq_count` (sellers across India), `city_bar` (distinct
      sellers per city, all-India call only) and `priceData` (price bands).
    - Rows also carry phones, GST and addresses: `parsers.listing_sellers` keeps only the public facts.
    - `parsers.listing_market` gives the seller's rank in the first results, or "not among them".
  - It runs in phase 2, for real sellers only. `agent_context.call_facts` turns it into `competitors` and the
    first sentence of `market_snapshot`. Global Context `top3mcat_competitors` is the fallback.
  - Any gateway call retries once on a dropped connection (`RemoteProtocolError`, `ConnectError`, `ReadError`).
- Gateway **up** again after the owner restarted the tunnel (new URL set in Admin → APIs → Gateway and in the env file);
  the gateway check covered 10/10 of our APIs. The tunnel URL changes on every restart — ERR_NGROK_3200 means "ask the
  owner for the new URL", not a code bug.
- `IM_EMPLOYEE_AK` (JWT, `sellergenome-im-apis.env`) lasts ~24 h — the current one expires 2026-10-10 08:00 UTC.
  Expired keys are refused before sending (D19); Admin → APIs → Keys shows the countdown.
- 5.10.1: `context.global` is **generateContextUID** (POST, JSON body `mapping_type 1`, `mapping_value` = GLID,
  `source_id 1`, `data_segments "kycdetails"`, employee AK). It replaces userCtx, which answered 404 on every call. Its
  UID becomes getContext's `mapping_id` (`orchestrator.mapping_id` tries several keys, because the shape is unconfirmed).
  Global Context fields fall back to getContext's answer (`contract.global_context_source`). Without a UID, getContext
  says "Mapping Not Found". No Insights API endpoint exists yet (skipped).
- 5.10.1: **Redash goes through the gateway** too (routes `redash-query-results`, `redash-jobs`). `Genome._redash` hands
  `ConnectorService` the gateway, and `approve_endpoint(..., gateway=)` allows that origin for Redash over HTTPS on the
  fixed paths only.
- Approved GLIDs: 77 test GLIDs + the owner-provided real seller `23456789` (78). The owner's rule: live details only
  for GLIDs the owner explicitly provides; every other test uses the test GLIDs.
- Real-data gotchas fixed in 5.6.0 (keep them fixed): **Redash returns ids as floats** (`23456789.0`) — the scope
  guard compares integer text (`source_normalization._id_text`); a **free seller has an empty `FREESHOWROOM_ALIAS`**,
  but the glusrid `company.alias` call already returns the full `DATA.COMPANYDETAIL` record — the orchestrator reuses it
  as `company.detail` (labelled "reused from company.alias") instead of skipping; an empty purchased-leads list
  (`offerids [null]`) emits a `leads.detail` skip event saying why.

## Common jobs

**New gateway URL** (owner sends it): Admin → APIs → Gateway → paste URL (any allow-listed host is accepted; tunnels
use the `*.ngrok-free.app|dev`, `*.ngrok.app` suffixes) → *Check gateway* (lists covered paths, TCP reachability) → or set `UPSTREAM_GATEWAY_BASE_URL` in
`/srv/sellergenome-v4/sellergenome-im-apis.env` and recreate. Never commit or print the URL; it is shown to
signed-in users only (D17).

**Fresh AK**: Admin → APIs → Keys → set `IM_EMPLOYEE_AK` (write-only; fingerprint + expiry shown) — the same JWT
works for leads/getContext/PNS/STS/LMS. Other keys: `USERCTX_API_KEY` (was userCtx's; unused since 5.10.1 — generateContextUID takes the AK), `COMPANY_TOKEN` (also
for buy-lead **details** — the doc's leads token fails there), `LEADS_TOKEN` (purchase list). `LMS_API_KEY` /
`PNS_API_KEY` in `seller-persona-v4.env` are V4-only; the V5 registry uses the AK.

**Test one API live**: Admin → APIs → row → *Test as* `real-5678901` → inspector shows route (gateway/direct), keys
with expiry, request/response. **Always finish with *Reset to default*** — overrides persist in
`private/v4/config/genome-apis.json` and silently change production runs.

**Add a new upstream API** (all steps, or it half-works):
1. `ApiDef(...)` in `DEFAULTS`: id (dotted = graph node id), label, group, kind `http|redash|dataset`, method, url
   template, params/body/headers, `secret_name`, mode, `ttl_hours`, contract, `body_format`, `via_gateway`.
   Placeholders: `{glid}`, `{empid}`, `{secret}`, `{secret:NAME}`, `{env:NAME}`, `{x|int}`.
2. Host on `config/genome/allowed_hosts.yaml` (code review, not a browser action).
3. Mock in `backend/genome/mock_upstream.py` in the real shape + mapping in `ApiExecutor._mock`; add to
   `NOT_SELLER_SPECIFIC` if not per seller.
4. Parser in `backend/genome/parsers.py` (scrub PII); contract candidates in `contract.CANDIDATES`; persona input in
   `persona_generator.build_input` if the persona should use it. If the API carries never-mention data (ratings star
   split, one-star counts, buyer names, prices — skill `sellergenome-indiamart-data-rules`), keep it out of the input,
   add names to `refs["forbidden"]`, and record an owner decision before sending any part of it to the model.
5. Orchestrator: `SELLER_SOURCES_FIRST`/`_SECOND` or a chain/fan-out; chained values in `api.py` `node_values` and
   `test_values` so inspector re-runs and Admin Test work.
6. Graph: id in `LEFT`/`RIGHT` + `LABEL` in `frontend/cc/graph.js` (unknown ids are silently dropped).
7. Tests: executor policy (synthetic / private / off) + parser on a real-shaped payload.
8. Docs: registry row in `docs/architecture/sources-and-apis.md`.

GLID access (5.9.6, D51): Admin → APIs → Gateway → `upstream.glid_access`. **dev** (default): manifest at
`APPROVED_GLIDS_PATH` (`private/v4/approved/approved_glids.json`, mounted read-only) via
`backend/source_policy.require_approved` — a missing manifest denies everything and the password cannot override it;
LMS needs both GLIDs approved; example GLIDs in docs or curl samples are not approval. **prod**: any numeric GLID
(`source_policy.valid_glid`) for signed-in users, Redash included (`ConnectorService(allow_any_glid=True)`), LMS test
contacts skipped. One switch: `service.Genome._approved` / `glid_access()`.
