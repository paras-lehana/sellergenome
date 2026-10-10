---
name: sellergenome-sarvam-mcp
description: Operate Sarvam's two MCP servers for SellerGenome — the Voice Agents MCP (https://mcp.sarvam.ai/voice-agents, OAuth 2.1 + PKCE, 41 tools for agents, KBs, secrets, evals, boards, chat) that builds and manages Payal, and the older model-API `sarvam-mcp` package (36 sarvam_tools_*/sarvam_code_* tools behind /v4/sarvam and /sarvam). Use when connecting or re-authorising the Voice MCP, calling an MCP tool from code or a one-off script, reading agent/KB/version state, deciding which MCP tool is allowed, or debugging MCP errors (session id, isError, token refresh, KB ids, dry runs).
---

# Sarvam MCP servers

## 1. Voice Agents MCP (builds Payal)

- Endpoint `https://mcp.sarvam.ai/voice-agents`: JSON-RPC over streamable HTTP, protocol `2025-06-18`,
  `Mcp-Session-Id` header, SSE or JSON replies. Client: `VoiceMCP` in `backend/genome/sarvam_voice.py`.
- **Auth (OAuth 2.1 + PKCE, `VoiceAuth`)**: dynamic client registration at `/register`, loopback redirect
  `http://127.0.0.1:53682/callback`, S256, `resource=<MCP URL>`. Admin → Sarvam voice agent → *Connect* gives the link;
  the operator signs in to the hackathon org and pastes back the `127.0.0.1` address (or forwards port 53682 in VS Code)
  → *Complete*. Access token 24 h + refresh token; refreshed automatically when <10 min remain. Tokens live in
  `private/v4/config/sarvam-voice-oauth.json` (uid 1000 — never write it as root).

### Calling a tool

Run inside the container so the token file stays owned by uid 1000:

```bash
docker exec -i -e PYTHONPATH=/app -w /app sellergenome-v4 python - agents '{"operation":"list"}' \
  < .claude/skills/sellergenome-sarvam-mcp/scripts/mcp_call.py
```

`scripts/mcp_call.py` refuses telephony/deployment/campaign tools and KB deletes, refuses `agents` operations other
than list/get (a `commit` would publish the draft outside Build) and refuses `run_eval` unless you add `--yes` after the
owner agreed to spend minutes. The container's `/app` is the **production** worktree, so piped scripts import
production code. In code:
`async with VoiceMCP(VoiceAuth(), log) as m: await m.call(tool, args, quiet=False)` — the result is the parsed JSON of
the text content; `isError` raises unless `quiet`; the log redacts `value` and shows only the prompt's length.

### The 41 tools by policy

| Group | Tools | Policy |
|---|---|---|
| Agents | `agents`, `configure_agent`, `configure_voicemail`, `configure_app_tool`, `create_api_tool` | via Build; draft first |
| Agents (unused) | `create_validator_tool`, `create_verifier_tool`, `upload_agent_code` | not used by SellerGenome |
| Knowledge | `knowledge_base`, `configure_kb`, `kb_file_upload_begin`, `kb_file_upload_commit` | via Build |
| Knowledge (destructive) | `delete_kb_file`, `delete_kb` | **owner OK first** |
| Secrets | `secrets`, `configure_secret` | via Build only |
| Evals | `evals`, `configure_test_suite`, `configure_scenario`, `run_eval` | `run_eval` costs minutes — ask |
| Runtime / analytics | `analytics`, `send_chat` | allowed (send_chat = /sarvam text chat) |
| Boards | `boards`, `configure_board*` (6), `run_board_widget`, `export_board_widget` | allowed, read-mostly |
| Telephony / deploy | `telephony`, `configure_deployment`, `configure_connection`, `configure_endpoint`, `verify_phone_number`, `place_test_call` | **never** |
| Campaigns | `campaigns`, `configure_campaign`, `stream_cohort_users`, `upload_cohort` | **never** |

The per-tool "where SellerGenome uses it" map is in `frontend/cc/voiceagent.js` (it calls `configure_app_tool` "not
needed", but the build uses it to rotate the tool key in place).

### Read the platform's playbook before writing an agent

`resources/read sarvam://playbooks/agents` (≈34 KB; also `…/evals`, `…/knowledge_base`, `sarvam://schemas/agent-config`,
`sarvam://examples/agent-config`). Rules SellerGenome follows (asserted for VANI in `tests/test_v57_buyer_agent.py`):
prompt sections Persona → Environment & Situation → Objective → Speaking style rules → Facts → Conversation guidelines →
Guardrails, `##` only; English, third person ("the agent", "the user"), no quoted spoken lines, no em dashes, no timing
directives; stop-and-wait after every question; outcomes are after-call variables (never named in the prompt); create
tools and variables before the prompt names them, then prompt + `tool_names` in one write; `call tool:<name>`; built-in
`end_interaction` takes the closing as its end message; one on_start and one on_end tool per agent; never promise what
no tool does; voicemail, nudges and the goal are configured, not written; intro under 25 words, English (translations
generated).

### Quirks learnt the hard way

- `knowledge_base list` returns names without ids — record the id at create time (state file); an existing unrecorded
  name aborts the build. KB names are 3–32-char slugs.
- The commit reply returns the **next draft** number, not the committed one.
- Dry runs accepted shapes the real write refused — always read back after writing.
- The prompt may only name tools listed on the agent state: write prompt + `tool_names` in one call.
- Output variables must be post-call only; stored secrets attach only to Sarvam-allow-listed URLs (hence the static
  `X-SellerGenome-Key` header on our tools).
- Payal is v2v: `interaction_type=chat` is refused; use `send_chat` (`{text, id}`; the id continues the conversation;
  no variable injection). The goal `evaluation_config` never applied.
- Chats appear in Sarvam logs under the MCP sign-in account.

## 2. Model-API MCP (`sarvam-mcp` 0.2.12)

36 tools (`sarvam_tools_*`, `sarvam_code_*`) for STT/TTS/translate/LLM, run as a subprocess by
`backend/sarvam_lab.py`. In production `SARVAM_MCP_PYTHON=/opt/sarvam-mcp/bin/python` (a venv built into the image from
`integrations/sarvam_mcp/requirements.lock`); the host venv `/opt/sellergenome/venv_sarvam_mcp` is only
the code's fallback (`scripts/sarvam_mcp_runner.py`). Coverage and
where each tool is used: `docs/contracts/v4-sarvam-tool-coverage.md`, inventory `data/sarvam-mcp-inventory.json`;
`/sarvam` shows the map (browser case 56). Use the designated key only.

There is also a Sarvam docs MCP (`https://docs.sarvam.ai/_mcp/server`) registered by the separate MVP — useful for
looking up API parameters; primary-source facts we rely on are in `docs/architecture/voice-engines.md`.
