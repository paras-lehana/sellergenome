---
name: sellergenome-incident-runbook
description: Diagnose and fix SellerGenome production problems fast from the symptom — /api/v5 404s after a restart, live IndiaMART data failing (ngrok offline, expired AK, userCtx 404, Mapping Not Found), Elasticsearch 403/strict-mapping errors and a growing outbox, LLM 400/429 and VANI's fallback line, Sarvam STT 400, Payal not connecting or wrong version, blank seller identities from a stale Admin override, stale data after a release, overflow on phones, missing Kibana access. Use whenever something on sellergenome.lehana.in / aidhunik.com is broken, slow, empty or "not live", or a user reports an error, before changing code.
---

# Incident runbook

First look (read-only):

```bash
curl -s https://sellergenome.lehana.in/health | python3 -m json.tool | head -20     # version, storage, started
docker inspect -f '{{.State.Health.Status}}' sellergenome-v4
docker logs --since 15m sellergenome-v4 2>&1 | grep -iE "error|traceback|403|429|timeout" | tail -30
curl -s https://sellergenome.lehana.in/api/v5/status | python3 -m json.tool | head -30   # elastic status, outbox, budget
```

| Symptom | Likely cause | Fix |
|---|---|---|
| Workspace agents (Manjula, Payal 6.0 …) missing from the agent picker; only the built-in agents and our own agents remain | Voice MCP sign-in revoked (`/api/v5/debug/voice` → `workspace_agents_error`: invalid_grant, "refresh token does not exist"); before 5.13.7 a double refresh after a restart caused it | The owner reconnects: Admin → Sarvam voice agent → *Get sign-in link* → sign in → paste the callback address → *Connect*. Since 5.13.7 the picker serves the last known list meanwhile (skill `sellergenome-sarvam-voice-agent` → Sign-in) |
| `/api/v5/*` 404, old UI, during/after deploy | container not healthy yet → Traefik serves V3 rollback (priority 140) | wait: `sellergenome-release-deploy/scripts/wait_healthy.sh`; if unhealthy, `docker logs` |
| New page path 404 only via public URL | path missing from the Traefik rule | add to both compose copies, recreate (skill `sellergenome-release-deploy`) |
| UI shows old behaviour after a release | `app.version` not bumped → cached `?v=` assets; or only one of the two version files bumped; shared modules are imported without `?v=` and `/static` sends no Cache-Control | bump both, restart; ask users for a hard reload |
| Console error "Cannot read properties of undefined (reading 'status')" right after a deploy | the status poll was answered by the V3 rollback (404 body) | fixed in 5.5.2 (guard in `main.js` `renderStatus`); if it returns, look for another caller assuming a V5 payload |
| V5 pages 404 but `/` works after a rollback | V3.1 router (priority 140) has no V5 paths | expected while V5 is stopped; bring V5 back |
| Private sellers have blank identity ("Private seller · GLID") | (a) leftover Admin → APIs override (e.g. company.alias = Mock — real sellers never get mocks, so the call is skipped) in `private/v4/config/genome-apis.json`; (b) gateway offline → `company.alias` fails and `company.detail` is skipped. Only sellers with cached company responses keep a name, and any Admin → APIs edit bumps the registry revision and drops that cache | open the Company node's inspector: "Turned off"/mock note = (a) → *Reset to default*; HTTP 404 / `ERR_NGROK_3200` = (b) → new gateway URL (Admin → APIs → Gateway, or the env file + `docker compose up -d` — a plain restart does not reload env files). New runs fix it; finished runs keep the blank |
| A free private seller stays "Private seller · GLID" although company.alias is OK | no showroom alias → detail skipped (before 5.6.0) | fixed in 5.6.0: the alias record is reused as company.detail; if it returns, check `/DATA/COMPANYDETAIL/DIR_SEARCH_COMPANY` is in the alias response |
| Redash hot-lead/PNS FAIL "rows outside the requested test seller scope" for an approved real GLID | Redash sends ids as floats (`123.0`) | fixed in 5.6.0 (`source_normalization._id_text`); a genuine other-seller id must still fail |
| A real seller's brief lists a product the seller never listed ("… (Premium)") | products read from the placeholder profile | fixed in 5.6.0; check `invented_products` and that `build_input` reads `PRDSERV` |
| Live sources FAIL, `ERR_NGROK_3200`, timeouts | gateway tunnel offline or URL changed | owner restarts the tunnel and sends the new URL → Admin → APIs → Gateway → *Check gateway* |
| Live sources "401 / expired token" (even with HTTP 200) | `IM_EMPLOYEE_AK` JWT expired (~24 h) | Admin → APIs → Keys card → new AK (owner provides) |
| userCtx 404, getContext "Mapping Not Found" | gateway maps userCtx to the public host | upstream fix by the gateway owner; report honestly (FAIL), never mock real sellers |
| Synthetic data looks like an old shape after a release | stale mock in the ES cache (fixed in 5.5 by salting mock keys with the version) | confirm both versions bumped; force a re-run from the inspector |
| ES status "unavailable", outbox growing | 403: app role lacks the prefix; 400: strict-mapping conflict | role change by the `elastic` superuser; fix `MAPPINGS` (never change a field type) and restart — don't truncate the outbox (skill `sellergenome-elastic`) |
| Persona says "Rules"/fallback; LLM errors in logs | `400 System prompt exceeds 16000 chars`, gateway 429 (30 req/min/IP), `BudgetExceeded`, Sarvam error | shorten system prompt (prompt_parts), stop parallel suites, raise budget in Admin if justified (skill `sellergenome-sarvam-llm`) |
| VANI repeats a canned line | LLM call failed (limit/budget) → fallback line | as above; check `/api/v5/status` budget |
| "Speak as seller" → Sarvam HTTP 400 | MIME `audio/webm;codecs=opus` | `stt_mime()` normalises it — check a new code path uses it |
| Buyer turn contains "TrustSEAL"/seller words never said | seller keyterms used on the buyer desk | `engines.buyer_keyterms` must stay empty (D32) |
| Payal call won't start | `real-` seller (403 by design), visitor limit 6/h, missing/wrong `SARVAM_VOICE_WORKSPACE_ID` ("Invalid API key format"), budget | Admin → Sarvam voice agent → *Test the Voice API key*; use a synthetic seller |
| Payal behaves like old settings | draft vs committed confusion | read committed version from `agents get` (skill `sellergenome-sarvam-mcp`); rebuild/commit |
| Sarvam build state lost / permission error | state file written as root | restore ownership uid 1000, mode 0600; run builds via Admin only |
| Page scrolls sideways on a phone | grid child without `min-width: 0`, long unbroken text, top-bar items | skill `sellergenome-ui-command-center`; add a browser case |
| Kibana login loops / 401 | PM password changed without re-running portal config; session expired | skill `sellergenome-kibana` |
| aidhunik mirror 403/1010 to scripts | Cloudflare blocks Python urllib's default client | use a browser UA / Playwright; not an app error |
| Floor shows no REAL CALL tile for a plug-in call | wrong `X-Voice-Plugin-Key`, no events, or idle > 120 s | skill `sellergenome-voice-plugin` |

After the fix: add a browser case (or test) for the bug, note it in VALIDATION/AGENTS, and close out (skill
`sellergenome-delivery-closeout`). If data or volumes are involved, stop and ask — never delete to "reset".
