---
name: sellergenome-security-privacy-audit
description: Audit SellerGenome for leaks and policy breaks before a commit, release or demo — staged-diff secret scan, private paths, approved-GLID enforcement for live upstream calls, synthetic-only exposure to anonymous visitors and to Sarvam, PII scrubbing before any LLM, never-mention content in personas/briefs, keys write-only in Admin, gateway URL exposure, and the do-not-do list (telephony, campaigns, volume deletes). Use before every commit/push, when adding an endpoint, data source, log, screenshot or LLM call, when the owner shares a key or private file, and whenever someone asks "is this safe", "audit", "security review" or "can we show this publicly".
---

# Security and privacy audit

## Before every commit

```bash
cd /opt/sellergenome/.worktrees/v5 && git add -A
.claude/skills/sellergenome-security-privacy-audit/scripts/scan_staged.sh
```

It fails on staged private paths (`private/`, `dataset/`, `runtime/`, sqlite/db/env/audio files, venvs) and, in
added lines (shown with their file name), on Sarvam keys (`sk_<seg>_<24>` / `sk_samvaad_<seg>_<24>` — the patterns were
verified to catch all five real keys by count, never by printing them), JWTs/employee AKs, ngrok gateway URLs,
env-style secret assignments, Indian mobile numbers (hex-safe boundaries; the documented test numbers 9876543210 and
9999999999 are allowed) and personal emails; embedded `data:image` screenshots are skipped. A hit means: unstage,
scrub, re-scan. Never "fix" it by weakening a pattern — extend the allow-list only for documented fake values, and
re-verify against the real key shapes (count matches in the env files) after any pattern change.

## What may never be committed, logged or shown

Keys and tokens (Sarvam `SARVAM_V4_API_KEY`, `SARVAM_VOICE_AGENTS_API_KEY` "deploy with code", tool and plug-in keys,
`OPERATOR_API_KEY`, `IM_EMPLOYEE_AK` JWTs, company/leads tokens, LLM internal key, ES/PM passwords), the ngrok gateway
URL (an open proxy that publishes sample keys), raw dataset rows, recordings, runtime databases, private inputs (the
credentials-bearing handout, the PM's original design file, the production persona sample), seller names from private
data, phone numbers, emails. Secrets live only in `/opt/secrets/env_files/*.env` (0600) or the Admin write-only store
(`private/v4/config/genome-secrets.json`); API responses carry only fingerprints and expiry.

If the owner pastes a key in chat: use it only via env/Admin, never echo it, and add "rotate this key" to the pending
list (the Sarvam deploy key from 5.3 still awaits rotation).

## Runtime rules to verify when code changes

| Rule | Where enforced | How to check |
|---|---|---|
| Live upstream only for signed-in users and allowed GLIDs, every hop (request, queue, retry, page, LMS contacts): `upstream.glid_access` dev = the 77 approved, prod = any numeric GLID (D51) | `service._approved` → `source_policy.require_approved` (dev; missing manifest denies all) or `valid_glid` (prod); executor refuses anonymous in both | executor policy tests; Admin *Test as* a non-approved id → refused |
| Anonymous sees synthetic sellers only; no admin, writes, live calls, gateway URL, key fingerprints | `api.py` `can_see`, `require_operator`, `public_api`; `/architecture` hides gateway | browser cases 58, 66; `curl` anonymous endpoints for `real-` ids → 403/404 |
| Mutations same-origin | `main.py` middleware (Origin must match) | POST with a foreign Origin → 403 |
| Admin URL edits only to allow-listed hosts | `config/genome/allowed_hosts.yaml` | a non-listed URL is refused (case 65) |
| Expired JWT refused before sending | registry `_live` | Admin → APIs → Keys shows expiry in red |
| No PII / never-mention data to an LLM | `parsers.scrub`, `persona_safe`, `sts_for_persona`, `persona_generator.build_input` + output check | skill `sellergenome-indiamart-data-rules`; qa_call.py never-mention scan |
| Sarvam Voice Agents only for synthetic sellers; no telephony/campaigns/deployments | `sarvam-live/sessions` 403 for `real-`; `mcp_call.py` refuses those tools | case 79 |
| Failed live call stays failed (never silently mock) | registry executor | inspector shows FAIL honestly (case 63) |
| Bot never claims to be human; stop/do-not-call is immediate | teleprompter/conversation/Payal prompt | conversation tests, case 70 |

## Infrastructure don'ts

Never `docker compose down -v` or delete volumes/indices (3-step confirmation + verified backup if ever needed);
never print resolved compose configs, `nginx -T` or env files; never write Sarvam OAuth/state files as root; never
edit the production worktree directly; never touch the "Persona-bot" Sarvam agent; never delete Sarvam resources
without the owner's OK.

## Audit report format

List findings as: what, where (file:line or URL), impact, fix, status. Fix what is in scope; list the rest as pending
with an owner decision when it needs one.
