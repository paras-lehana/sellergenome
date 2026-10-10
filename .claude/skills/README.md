# SellerGenome project skills

Standard Claude Code skills (`<name>/SKILL.md` with `name` + `description` frontmatter; optional `scripts/`,
`references/`, `assets/`; `evals/evals.json` with test prompts and checkable expectations). They distil the project
docs (AGENTS.md, docs/architecture, docs/business, docs/contracts, docs/research, delivery reports) into procedures
with working scripts. **Agents should load the matching skill instead of re-reading the docs**; the docs stay the
long-form record. Start with `sellergenome-onboarding`.

| Skill | Use it for | Bundled |
|---|---|---|
| **Start** | | |
| [`sellergenome-onboarding`](sellergenome-onboarding/SKILL.md) | What the product is, where everything lives, the rules, which skill for which job | — |
| **Domain and data** | | |
| [`sellergenome-business-domain`](sellergenome-business-domain/SKILL.md) | PS04 scoring, IndiaMART call flows and terms, honesty rules for claims | `references/glossary.md`, `references/ps04-problem-statement.md` |
| [`sellergenome-dataset-analysis`](sellergenome-dataset-analysis/SKILL.md) | The 908,856-row private corpus, facts sheet, PM study, raw indices, privacy | — |
| [`sellergenome-indiamart-data-rules`](sellergenome-indiamart-data-rules/SKILL.md) | How to read Global Context, STS, PNS, enquiries, storefront (the API guide); never-mention list | `references/consolidated-api-guide.md` |
| [`sellergenome-indiamart-apis`](sellergenome-indiamart-apis/SKILL.md) | Registry, live/mock/off, ngrok gateway, expiring AK keys, approved GLIDs, adding an API | — |
| **Persona** | | |
| [`sellergenome-call-pipeline`](sellergenome-call-pipeline/SKILL.md) | Barge-in run phases, SSE events, architecture graph nodes, inspector forks, fallbacks | — |
| [`sellergenome-persona-generator`](sellergenome-persona-generator/SKILL.md) | Owner prompt v0.3, input records/facts, ≈45 checks, repair, polish, hand-off to bot and brief | `references/checks.md` |
| [`sellergenome-persona-evaluation`](sellergenome-persona-evaluation/SKILL.md) | Live scenario suite, comparing runs, adding edge cases, eval results in ES | `scripts/run_eval.sh`, `scripts/summarize.py` |
| [`sellergenome-pre-call-brief`](sellergenome-pre-call-brief/SKILL.md) | The PM's brief blocks, evidence chips, Why call, Quality panel | — |
| **Conversation and voice** | | |
| [`sellergenome-vani-bot`](sellergenome-vani-bot/SKILL.md) | Built-in bot turns, signals, switch log and cooldown, teleprompter, buyer-side VANI | — |
| [`sellergenome-meeting-desk`](sellergenome-meeting-desk/SKILL.md) | Meeting booking: Jitsi link, .ics, Google Calendar link, Payal's book_meeting | — |
| [`sellergenome-sarvam-llm`](sellergenome-sarvam-llm/SKILL.md) | LLM calls via llm.lehana.in: 16k system limit, 30 req/min, budgets, fallbacks | — |
| [`sellergenome-voice-engines`](sellergenome-voice-engines/SKILL.md) | Bulbul/Saaras/Mayura ids, streaming TTS, realtime STT proxy, engine options | — |
| [`sellergenome-voice-latency-lab`](sellergenome-voice-latency-lab/SKILL.md) | Measuring answer times and naturalness; draft-version sweeps; fake-mic browser timing | — |
| [`sellergenome-sarvam-voice-agent`](sellergenome-sarvam-voice-agent/SKILL.md) | Payal on Sarvam Voice Agents: build, versions, VAD, live browser calls, tests, sign-in recovery | `scripts/signin_listener.py` |
| [`sellergenome-sarvam-mcp`](sellergenome-sarvam-mcp/SKILL.md) | Voice Agents MCP (OAuth, 41 tools, policy) and the model-API MCP | `scripts/mcp_call.py` |
| [`sellergenome-voice-plugin`](sellergenome-voice-plugin/SKILL.md) | Contract for external voice bots: sessions, context, events, REAL CALL tiles | — |
| **Elastic and Kibana** | | |
| [`sellergenome-elastic`](sellergenome-elastic/SKILL.md) | V5 indices, strict mappings, cache keys, outbox, read-only query helper | `scripts/es.py` |
| [`sellergenome-kibana`](sellergenome-kibana/SKILL.md) | Authenticated gateway, PM access, dashboards, adding V5 charts | — |
| **Building** | | |
| [`sellergenome-admin-settings`](sellergenome-admin-settings/SKILL.md) | Revisioned settings, Admin overrides, rollback, budgets, adding an option end to end | — |
| [`sellergenome-backend-development`](sellergenome-backend-development/SKILL.md) | FastAPI module map, auth/visibility, new endpoint/page recipes, gotchas | — |
| [`sellergenome-ui-command-center`](sellergenome-ui-command-center/SKILL.md) | Design system, pages→modules, phone layer, CSP/versioning, UI rules (from next-level-website-designer) | `references/from-next-level-designer.md` |
| [`sellergenome-architecture-diagrams`](sellergenome-architecture-diagrams/SKILL.md) | Accurate architecture/flow/sequence diagrams via the root archify skill | — |
| **Quality, release and operations** | | |
| [`sellergenome-testing`](sellergenome-testing/SKILL.md) | Pytest in the app image, node --check, which test each change needs | `scripts/run_tests.sh` |
| [`sellergenome-ui-audit`](sellergenome-ui-audit/SKILL.md) | Contrast, targets, overflow, CLS, breakpoints, WebKit parity with the root QA tools | `scripts/ui_audit.sh` |
| [`sellergenome-live-qa`](sellergenome-live-qa/SKILL.md) | Real-browser checks on both mirrors × sizes × engines with bundled Playwright scripts | `scripts/__pycache__`, `scripts/qa_call.py`, `scripts/qa_floor_barge.py`, `scripts/qa_matrix.sh` |
| [`sellergenome-security-privacy-audit`](sellergenome-security-privacy-audit/SKILL.md) | Staged-diff scan, privacy rules, approved GLIDs, exposure checks, don'ts | `scripts/scan_staged.sh` |
| [`sellergenome-release-deploy`](sellergenome-release-deploy/SKILL.md) | Version bump → tests → ff-merge → restart → healthy → push; preview; rollback | `scripts/release.sh`, `scripts/wait_healthy.sh`, `assets/compose.v5-preview.yml` |
| [`sellergenome-incident-runbook`](sellergenome-incident-runbook/SKILL.md) | Symptom → cause → fix for production problems | — |
| **Delivery** | | |
| [`sellergenome-delivery-closeout`](sellergenome-delivery-closeout/SKILL.md) | One forwardable report, prompts/tasks logs, decisions, A–G reply | `scripts/embed_screenshots.py` |
| [`sellergenome-demo-judging`](sellergenome-demo-judging/SKILL.md) | Judge/PM demo path, pre-demo checklist, honest caveats, the slide, the public repository for judges | `scripts/export_public_repo.py`, `assets/public-README.md` |

## Conventions

- Names are `sellergenome-<topic>` (kebab-case, ≤ 64 chars); descriptions say what the skill does **and** when to use it
  (they are the trigger). Bodies stay short; detail goes to `references/`; repeatable steps go to `scripts/`.
- Scripts are read-only or explicit about what they change; secrets are read from env files inside the container and
  never printed; destructive operations are refused or need the owner.
- Validate after editing: `python3 <skill-creator>/scripts/quick_validate.py .claude/skills/<name>`; keep
  `evals/evals.json` in step with the skill. When a skill's facts change (code moved, new decision), update the skill in
  the same commit as the code — skills that drift are worse than no skills.
- Root (user-level) skills these build on: `next-level-website-designer`, `archify`, `webapp-testing`,
  `lehana-platform`, `agent-context-maintenance`, `skill-creator`.
