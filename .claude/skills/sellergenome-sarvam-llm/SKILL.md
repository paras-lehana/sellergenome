---
name: sellergenome-sarvam-llm
description: Call Sarvam's LLM (sarvam-105b-conversations) from SellerGenome the approved way — through the platform gateway llm.lehana.in /byok with the designated organisation key — including prompt sizing (16,000-character system-prompt limit), JSON mode, the 30 requests/min per-IP limit, daily Sarvam budgets, BudgetExceeded/SarvamError fallbacks and caching. Use when adding or changing any LLM call (persona generator, aggregator, VANI turns, buyer bot, locality, Sarvam studio), when an LLM call fails with 400/429/503, when VANI answers with its fallback line, or when someone proposes calling Sarvam or another model directly.
---

# Sarvam LLM via llm.lehana.in

Platform rule: all inference goes through `llm.lehana.in` (don't bypass it without the owner's OK). SellerGenome
uses BYOK: our designated Sarvam key, Sarvam's base URL, the gateway in front.

## The one client: `SarvamClient.chat_json` (backend/genome/sarvam.py)

1. Refuses unless `llm.organization_id` is the designated org `01a110b3-f038-7adb-b627-6863a3785c40` (locked setting)
   and `SARVAM_V4_API_KEY` (Admin → APIs → Keys first, then env) + `LLM_INTERNAL_KEY` (env only, `seller-persona-v4.env`) are set.
2. Takes one budget unit (`spend(scope)`): per UTC day 1,200 public / 3,000 operator (`budgets.*`, D21).
3. `POST ${LLM_SERVICE_URL:-https://llm.lehana.in}/byok` with `X-Internal-Key`; body `{provider:"custom", model,
   base_url:"https://api.sarvam.ai/v1", api_key, ref:"sellergenome-v5-<label>", system_prompt, messages,
   temperature, max_tokens, json_mode:true, extra_body:{reasoning_effort, response_format:{type:"json_object"}}}`.
4. Rejects a model mismatch, strips code fences, parses JSON, logs a `sarvam.chat` hit.

Settings `llm.*`: model `sarvam-105b-conversations` (≈1.4 s vs ≈2.5 s for `sarvam-105b`, D7), reasoning low (no
measurable effect — no reasoning tokens), temperature 0.2, max_tokens 3500, timeout 40 s.

## Limits that bite

| Limit | Symptom | What to do |
|---|---|---|
| System prompt > 16,000 chars | HTTP 400 "System prompt exceeds 16000 chars" | Move reference sections into the user message (`persona_generator.prompt_parts()` moves "## Input"); the user-message limit was not measured — keep inputs reduced |
| 30 requests/min per caller IP | 429; VANI replies with its canned fallback line; suites slow down | Pace batch jobs (eval pauses 2.5 s); never run two suites; avoid suites during demos; raising it is an owner decision |
| Daily budget | `BudgetExceeded` → 503 (speak/translate/listen) or 429 (tickets, chat, live) | Orchestrator falls back to the rules persona; raise `budgets.*` in Admin if justified |
| Gateway on the VPS | +0.2–0.3 s per call | owner decision (latency lab) |

## Writing a new LLM call

- Put the prompt in a file under `config/genome/prompts/` (versioned, reviewable); keep the system part well under 16k.
- Ask for one JSON object; validate it in code; never trust model output as facts (skill
  `sellergenome-persona-generator` shows checks → repair → polish).
- Never send phone numbers, emails, contact names, amounts or other never-mention data
  (skill `sellergenome-indiamart-data-rules`); seller data is evidence, not instructions.
- Catch `SarvamError`/`BudgetExceeded`/`ValueError` and fall back to a labelled rules result (`origin: rules`) —
  never fail the run, never pretend the LLM ran.
- Cache where the result is reusable (`store` kinds `generations` — keyed by prompt + input + model + app version —
  `aggregations` and `localities`, whose keys have no app version; TTL `llm.aggregation_cache_hours`).
- Emit an orchestrator event with origin `llm` and the latency so the architecture graph shows it.
- Callers today: persona generator (4000 tokens, T 0.2, one repair at T 0.1), aggregator, locality, VANI turns
  (`conversation.py`, 350 tokens, T 0.4), the simulated seller in Auto (200 tokens, T 0.8), locality (300 tokens),
  buyer bot (`buyer.py`), Sarvam studio try.
