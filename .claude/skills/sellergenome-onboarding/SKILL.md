---
name: sellergenome-onboarding
description: Start here for any SellerGenome work (IndiaMART × Sarvam hackathon PS04, seller persona + voice bot command center at sellergenome.lehana.in) — what the product is, where the code, data, secrets and live container are, the non-negotiable rules, the first commands to orient yourself, and which of the 31 project skills to load for each kind of job. Use at the beginning of a SellerGenome session, when you are unsure which skill applies, when someone new asks how the project is organised, or before touching anything under /opt/sellergenome.
---

# SellerGenome — start here

SellerGenome gives IndiaMART's seller-calling voice bot (VANI) a persona built per seller from evidence, adapts it
live during the call, and makes every decision inspectable (hackathon problem PS04 *Persona Design*). Live:
https://sellergenome.lehana.in and mirror https://sellergenome.aidhunik.com (same paths). Version 5.5.2 (2026-10-10).

## Where things are

| What | Where |
|---|---|
| Develop | `/opt/sellergenome/.worktrees/v5` (branch `feat/v5-command-center`) |
| Live code | `.worktrees/production` (`feat/production-control-room`) — read-only mount of container `sellergenome-v4`; never edit |
| Backend / frontend | `backend/genome/` (FastAPI `/api/v5`), `frontend/cc/` (vanilla ES modules) |
| Config | `config/genome/settings.yaml` (+ Admin overrides), `config/settings.yaml` (version for `/health`) |
| Private data and state | `private/` (inputs, `v4/config`, `v4/data`), `/opt/sellergenome/dataset/` — never in Git |
| Secrets | `/opt/secrets/env_files/{seller-persona-v4,sellergenome-im-apis,sellergenome-sarvam-voice}.env` |
| Rules and browser cases | `AGENTS.md` (cases 1–89), decisions `docs/business/decisions.md` (D1–D40) |
| Logs and reports | `docs/prompts.md`, `docs/tasks.md`, `docs/tasks/<date>-<slug>/` |
| QA artifacts | `/root/data/qa-artifacts/` |

## Non-negotiables (owner + AGENTS.md)

1. Live upstream only for signed-in users and allowed GLIDs — `upstream.glid_access` dev = the 77 approved, prod = any
   GLID (D51); anonymous visitors and Sarvam Voice Agents see synthetic sellers only.
2. No phone numbers, emails, contact names, amounts or never-mention topics to an LLM or a public page.
3. Never commit secrets or private data — scan staged diffs. Never delete volumes/indices; never `down -v`.
4. Label simulations and mocks; a failed live call stays failed; the bot never claims to be human.
5. Changes are additive options with an Admin default; every release bumps the version (both settings files).
6. Done = live browser cases pass on both mirrors at 1440 and 390 (Chromium + WebKit) and you looked at screenshots.
7. Close out with the delivery document, logs and the A–G reply.

## First five minutes

```bash
cd /opt/sellergenome/.worktrees/v5 && git status --short && git log --oneline -3
curl -s https://sellergenome.lehana.in/health | python3 -m json.tool | head -8
docker inspect -f '{{.State.Health.Status}} {{.Config.Image}}' sellergenome-v4
grep -n '^## ' docs/tasks.md | tail -4   # then read from the last "Session" heading: tasks, Agent Deferred, Suggestions
```

## Which skill for which job

| Job | Skill |
|---|---|
| Domain terms, PS04 scoring, honest claims | `sellergenome-business-domain` |
| Numbers from the 908k-row corpus, PM study, raw indices | `sellergenome-dataset-analysis` |
| What an IndiaMART field means; never-mention rules | `sellergenome-indiamart-data-rules` |
| Gateway, keys, approved GLIDs, adding an upstream API | `sellergenome-indiamart-apis` |
| What happens on barge-in; graph nodes; run events | `sellergenome-call-pipeline` |
| Persona prompt, checks, polish, bot hand-off | `sellergenome-persona-generator` |
| "How good is the persona?" — eval suite, comparisons | `sellergenome-persona-evaluation` |
| PM pre-call brief blocks and chips | `sellergenome-pre-call-brief` |
| VANI turns, signals, switch log, teleprompter, buyer bot | `sellergenome-vani-bot` |
| Meeting desk, Jitsi/.ics/calendar | `sellergenome-meeting-desk` |
| Sarvam LLM via llm.lehana.in, limits, budgets | `sellergenome-sarvam-llm` |
| Bulbul/Saaras/Mayura engines, streaming, realtime STT | `sellergenome-voice-engines` |
| Latency and naturalness measurements | `sellergenome-voice-latency-lab` |
| Payal on Sarvam Voice Agents | `sellergenome-sarvam-voice-agent` |
| Sarvam Voice MCP / model MCP | `sellergenome-sarvam-mcp` |
| External voice bot integration | `sellergenome-voice-plugin` |
| Elasticsearch indices, mappings, cache, queries | `sellergenome-elastic` |
| Kibana, PM access, dashboards | `sellergenome-kibana` |
| Settings, Admin, budgets, new options | `sellergenome-admin-settings` |
| Python/FastAPI changes, new endpoint/page | `sellergenome-backend-development` |
| UI changes, design system, phone layer | `sellergenome-ui-command-center` |
| UI/accessibility audit | `sellergenome-ui-audit` |
| Tests | `sellergenome-testing` |
| Deploy, release, rollback, preview | `sellergenome-release-deploy` |
| Live browser verification | `sellergenome-live-qa` |
| Secrets, privacy, policy audit | `sellergenome-security-privacy-audit` |
| Something is broken in production | `sellergenome-incident-runbook` |
| Report, logs, A–G closeout | `sellergenome-delivery-closeout` |
| Judges/PM demo, slide | `sellergenome-demo-judging` |
| Architecture/flow diagrams | `sellergenome-architecture-diagrams` |

Root (user-level) skills that pair with these: `next-level-website-designer` (UI audit tools), `archify`
(diagrams), `webapp-testing` (Playwright patterns), `lehana-platform` (server/Traefik platform),
`agent-context-maintenance` (when changing these skills or AGENTS.md).
