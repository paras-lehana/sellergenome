---
name: sellergenome-architecture-diagrams
description: Draw accurate SellerGenome diagrams — system architecture (VPS → rig Traefik → app → Elasticsearch/Sarvam/IndiaMART gateway), the IndiaMART business flow (activity → hot lead → 15-minute window → context → persona → bot call → meeting → executive), the barge-in request sequence, the persona-generator data flow, and the run/call lifecycles — using the root `archify` skill with facts taken from this project's code, not from memory. Use when the owner, a PM or a judge asks for an architecture/flow/sequence diagram, a slide visual, or a picture of how SellerGenome works, or when the in-app architecture bar needs a matching static diagram.
---

# Architecture diagrams (via the root `archify` skill)

Rendering is done by `/root/.claude/skills/archify` (typed JSON → validated standalone HTML/SVG, PNG export). Read its
SKILL.md for the commands; on this host the headless browser comes from `ARCHIFY_CHROME`
(`~/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome`). Use `--repo-root
/opt/sellergenome/.worktrees/v5` so evidence points at real files. Keep outputs out of Git unless the
owner wants them in docs: `/root/data/qa-artifacts/<date>-diagrams/.archify/<type>-<slug>-<ts>/`; embed a PNG in the
delivery document.

## Facts to draw from (check the file before drawing — the code wins over old docs)

**Architecture** (`architecture` type)
- Internet → Cloudflare DNS → VPS Traefik (TLS, hybrid router) → rig Traefik (`web`, router `sellergenome-v4-vm`,
  priority 141; V3 rollback 140) → container `sellergenome-v4` (FastAPI `backend/main.py`, V5 at `/`, V4 at `/v4`,
  source mounted read-only from `.worktrees/production`).
- Inside: `/api/v5` router → orchestrator, API executor (registry), persona generator / aggregator, conversation
  (VANI), buyer bot, Sarvam voice agent service, live speech relays, voice plug-in; SQLite `v5.sqlite3`.
- Outside: Elasticsearch 8.19 `seller-persona-elasticsearch` (`sellergenome-v5-*`) + Kibana via the authenticated
  gateway (elastic/kibana.lehana.in); `llm.lehana.in` on the VPS → Sarvam LLM; Sarvam Bulbul/Saaras WebSockets
  (direct); Sarvam Voice Agents (Payal; browser SDK via our signing proxy; MCP for builds); IndiaMART APIs through the
  ngrok pass-through gateway (approved GLIDs only); Redash (V5 uses 2 fixed read-only queries, `redash.hotlead` and `redash.pns`; V4 had 4); external voice bots through
  the plug-in contract.
- Boundaries: public anonymous (synthetic only) vs operator; private data (`private/`, `/opt/secrets`) never public.

**Business flow** (`workflow`): seller activity → hot lead (SALES-HOTLEAD-GENERATOR) → ~15 min exploration window →
data-type re-check + executive assignment → context fetch (Global Context, addresses, locality LLM, insights) → vendor
push (~28 fields) → VANI/Payal bot call (book a meeting only) → meeting fixed → human executive. SellerGenome acts at
context → persona → bot call. The in-app stepper `FLOW` in `frontend/cc/call.js` shows 7 stages (activity · ~15 min
window · context · persona · bot call · meeting · human executive) — it folds hot lead, re-check and vendor push in.

**Barge-in** (`sequence`): browser `POST /api/v5/runs` → orchestrator task → SSE `/runs/{id}/stream` events → phase 1
parallel sources (company alias→detail, userCtx→getContext, …) each via executor (policy → ES cache → mock/live via
gateway → persist) → contract assembly → phase 2 (+ lead/chat fan-outs) → persona (generator: Sarvam via gateway →
checks → repair → polish) → locality → ES index → teleprompter → workspace. See skill `sellergenome-call-pipeline`.

**Persona generator** (`dataflow`): five APIs → parsers + scrubbing (never-mention removed) → records r…/facts f… →
prompt v0.3 (system ≤16k) → Sarvam → evaluate → repair → polish → `to_persona` (bot delivery, teleprompter hook) +
`brief.from_generated` (PM brief) + ES personas/persona-evals. See skill `sellergenome-persona-generator`.

**Lifecycles** (`lifecycle`): run `running → completed | degraded (sources timed out) | failed` (`orchestrator.py`); call stages
greeting → qualifying → package → meeting → wrap_up (a stop sets `stopped` with outcome `do_not_call` — there is no
"ended" stage); Payal versions draft → committed.

## Rules

- Label simulations and mocks as such (the floor is simulated; IndiaMART data for synthetic sellers is mocked).
- No secrets, gateway URLs, private seller names or real GLIDs beyond the documented approved test ids.
- One diagram per question; prefer the architecture bar's vocabulary (node names in `graph.js` `LABEL`) so the
  picture matches what judges see in the app.
