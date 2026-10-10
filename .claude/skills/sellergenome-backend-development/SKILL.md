---
name: sellergenome-backend-development
description: Write or change SellerGenome backend code — the FastAPI app (backend/main.py) and the V5 package backend/genome (api.py router, service container, store, registry, orchestrator, persona modules, Sarvam clients), auth and visibility helpers, rate limits and budgets, page routes, CSP, and the extension recipes for a new /api/v5 endpoint, page, admin data or service. Use for any Python change in backend/, when adding an endpoint or page, when a request returns an unexpected 401/403/404/409/422/429/503, or when you need the module map before editing.
---

# Backend development (FastAPI, `backend/genome/`)

Start with `uvicorn backend.main:app` (no factory; lifespan builds V1–V4 services, then `Genome(...)` →
`await genome.start()`); the V5 router mounts as `genome_router(get_genome, is_operator, get_sarvam_lab)`. V4 lives in
flat modules (`operations*.py`, pages under `/v4`); V1–V3.1 studio/walkthrough/analysis/slide are still served.

## Module map (`backend/genome/`)

| Module | Purpose |
|---|---|
| `service.py` | `Genome` container wiring settings, store, elastic, registry, executor, sarvam, persona index, orchestrator, conversation, buyer, voice, sarvam_voice; approved-GLID adapter |
| `api.py` | `/api/v5` router + `can_see`, `require_operator`, `scope_of`, `limit`, `public_api` |
| `orchestrator.py` | preparation runs (skill `sellergenome-call-pipeline`) |
| `registry.py` | API registry + executor (skill `sellergenome-indiamart-apis`) |
| `settings.py` / `engines.py` | revisioned settings, catalogue + validation (skill `sellergenome-admin-settings`) |
| `store.py` | SQLite `DATA_DIR/v5.sqlite3`: `docs(kind,id)`, `events`, `hits` (20k), `outbox`, `budgets` |
| `elastic.py`, `persona_index.py` | ES indices, outbox flusher, cache, factor docs, search/facets |
| `contract.py`, `parsers.py`, `mock_upstream.py`, `roster.py` | contract (28 vendor fields + `custtypeweight`) + origins, payload normalisers, mocks, seller ids (`syn-`/`gen-` public, `real-` private) |
| `persona_generator.py`, `persona_eval.py`, `aggregator.py`, `brief.py`, `teleprompter.py` | persona + PM brief |
| `conversation.py`, `signals.py`, `buyer.py`, `floor.py`, `meetings.py` | calls, switch log, buyer bot, simulated floor, Jitsi/.ics/Google link |
| `sarvam.py`, `sarvam_voice.py`, `live_speech.py`, `voice_plugin.py` | Sarvam client/budget, Payal, speech relays, plug-in |

## Auth and visibility

`is_operator` = PM session cookie (`/api/auth/login`) **or** header `x-operator-key` = `OPERATOR_API_KEY`
(constant-time). Anonymous: synthetic sellers only, no admin/edits/live calls, no gateway URL or key fingerprints.
Mutations must be same-origin (middleware). Tool key (`sarvam-agent/*`) and plug-in key (`voice/*`) are separate.
Error mapping: `ValueError` → 422 (call/auto/buyer turn endpoints use 409; Auto also maps Sarvam errors to 409) ·
`SarvamError`/`BudgetExceeded` → 503 (speech) or 429 (tickets/chat/live) ·
settings revision mismatch → 409 · unknown/hidden seller → 404/403.

## Recipes

**New endpoint**: a `Body` model (`extra="forbid"`) → `@router.<verb>` inside `genome_router` using `svc()`,
`can_see`/`get_run`, `require_operator` where needed, `limit(request, kind, n)`, `scope_of` + `g.sarvam.spend` for
Sarvam work → logic in a `backend/genome/<module>.py` (stateful services wired in `service.Genome`) → access test in
`tests/test_v5_genome.py::test_api_access_rules` → frontend `api('/api/v5/...')`. `/api/` is already routed by Traefik.

**New page**: decorator on `command_center_page` in `main.py` → `ROUTES` + `NAV` in `frontend/cc/main.js` + a module
exporting `mount()` → add the path to the Traefik rule in both compose copies and recreate (skill
`sellergenome-release-deploy`).

**New external connection**: CSP `connect-src` in `main.py` (only self, own `wss:` and `wss://apps.sarvam.ai`
today); host allow-list for upstream URLs.

## Gotchas

- Two version strings (`config/settings.yaml` and `config/genome/settings.yaml`) — bump both.
- One app worker only (runs, SSE waiters, buckets, tickets are in memory).
- Every registry edit bumps a global revision → all ES cache keys change.
- `visitor()` reads the cookie `persona_v4_owner` (production name); the WS origin regex hard-codes production hosts.
- Docs drift: PRODUCTION_PLAN §6/overview list endpoints that don't exist (`/floor/stream`,
  `/sellers/{id}/fields`, `/calls/{id}/tts|stt`) — the real ones are `/runs/{id}/fields`, `/persona/{id}/suggest`,
  `/speak`, `/listen`; heroes are in `data/genome/heroes.json`; `docs/API.md` covers V1–V3.1 only. The code wins;
  `/docs` (OpenAPI) is the live reference.
- Run tests with skill `sellergenome-testing`; never edit the production worktree.
