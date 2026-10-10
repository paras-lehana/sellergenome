---
name: sellergenome-persona-generator
description: Change, debug or explain how SellerGenome generates a seller persona with the owner's Persona Generator prompt v0.3 (Sarvam LLM) — the prompt file, the input builder (records r…/facts f…), the 37 automatic checks, the repair round, the pure-rule polish step, and how the result drives the live bot's opening, the teleprompter and the PM pre-call brief. Use for any edit to config/genome/prompts/persona_generator_v0.3.md or backend/genome/persona_generator.py, when a brief shows a wrong insight, trigger, opening or quality score, when switching persona.engine between generator and aggregator, or when the owner sends a new prompt version.
---

# Persona generator (owner prompt v0.3)

Since 5.5 every persona comes from the owner's prompt. The owner judges the result by the live brief and the
bot's first line, so a change is only done when both look right on the live site.

## The pipeline (backend/genome/persona_generator.py)

```
build_input(seller, results, today, lead) → (seller_data, refs)
generate(sarvam, seller_data, refs, today) :
    chat_json(system, user, T 0.2, ≤4000 tokens)       # sarvam-105b-conversations via llm.lehana.in
    evaluate(out) ── critical failure? ── one repair round (the failed rules + previous JSON, T 0.1)
    polish(out)  → evaluate again                        # delivered score; raw_score = the model's own reply
to_persona(result, rules_persona, settings)  → factors, tags, teleprompter.opening_hook/first_question/avoid
brief.from_generated(run, gen, meta)          → the PM brief (basics, engagement, buyers, plan, record, quality)
```

The orchestrator calls this in phase 3 when `persona.engine: generator` (`orchestrator._generate`), caches by
system prompt + input + model + **app version** (`store "generations"`), emits node `sarvam.aggregate`, and falls back
to the rules persona on `SarvamError`, `BudgetExceeded` or `ValueError` — the run never fails because the LLM did.

## Input: what the model sees

`seller_data` = `lead`, `global_context`, `sts_history`, `pns_details`, `enquiries`, `company_attributes`, plus:

- `records` — dated events newest first with ids `r1…` (hot lead, executive calls, meetings, call notes, tickets,
  system notices). The model copies them into `record` and cites them; chips in the brief open their API.
- `facts` — counted values with ids `f1…`. **Every label names the measure, window and key**, e.g.
  "buyer phone calls received, last 90 days (pns_received_90_days)". A terse label ("pns received 90 days") was once
  read as buy leads. Derived numbers the model would otherwise compute (tenure → "on IndiaMART since 2022") are facts.

Real sellers (5.6.0): `supplied_dataset` (the seller's dataset row — history to Sep 2026, never "today"),
`bot_call_history` (dataset calls + live Redash hot-lead rows merged by minute; "meeting claimed by the bot" is not a
held meeting) and "Bot call" records; `lead.categories`/`state`/`do_not_call` from the dataset; `lead.products`,
`company_name`, `contact_name` from the company record's `PRDSERV`/`COMPANYDETAIL`. A private seller's placeholder
profile (`roster.profile("real-…")`) is never a source — its products are generated ("X (Premium)") and its company is
"Private seller · GLID …". Synthetic sellers keep their own catalogue. Tests: `tests/test_v56_real_seller.py`.

Everything on the never-mention list is removed before this point — see skill `sellergenome-indiamart-data-rules`.
`refs` holds what the checks need: forbidden names (minus tokens of the seller's own name/company), competitors,
transcripts, record/fact ids, expected language.

**STS classification (5.9.8, GLID 12345678 "no call history"):** `parsers.automated()` treats every ERP desk tool
(WebERP, NSDMERP, MERPC2C click-to-call) as a person unless the updater *or channel* is a process (penny-drop
consumers). Person entries map to record types in `_records`: "Sales Appointment" → **Meeting**, C2C log with
"Call Status" → **Executive call** (Answered/Not answered + seconds), TBRO/follow up/call back → **Call note "Follow-up
set"**. `sts_kind` order matters (a "payment follow up" note is a follow-up). `_dates_in` reads "13-Oct-2026", so a TBRO
within 3 days opens *follow_up_due*. `parsers.staff_safe` removes executive names, "Name - 112259" ids and masked
phones before the model. If a seller "shows no history", rebuild the input from the run's stored responses
(`build_input(roster.profile(sid), run["results"], today, lead)`) and look at the record types first.

**Invented figures (5.9.9):** `invented_numbers()` is a *critical* check (repair round), and `polish` drops any insight
still carrying a number absent from the input; dates normalise ("2026-10-08" ↔ "8 Oct"). The model tends to copy the
prompt's style example ("Active on … of the last 30 days") when no `bl_active_days` fact exists.

**Long personas (5.10.5–5.10.6):** the model returns `record` as `[]`. `polish` fills it from the input `records`, with
`record_tone()` setting the tone. Copying 15 records made it break the JSON right after the list. The budget is 7,000
`max_tokens`, with one retry at the same 7,000 (the gateway refuses about 10.5k). `sarvam.chat_json` logs "truncated at
max_tokens" or "invalid JSON (finish …)". The prompt asks for 7–12 insights, and the brief carries each insight's cited
records (`records`, `facts`) for the expandable view.

## Editing the prompt safely

0. Check Admin → Prompts → *Persona Generator v0.3* first: an Admin edit (5.9.2) overrides this file until it is
   reset, so a file change would not show.
1. Edit `config/genome/prompts/persona_generator_v0.3.md`. Add a numbered line to the HTML change list at the top
   (stripped before sending) saying what changed and why — Admin → Persona generator shows that list.
2. The gateway refuses system prompts over **16,000 characters** (HTTP 400). `prompt_parts()` moves the
   "## Input" section into the user message; check the system part of the prompt you are editing from the worktree
   root on the host (the module only needs the standard library):
   `python3 -c "from backend.genome import persona_generator as pg; print(len(pg.prompt_parts()[0]))"`.
   (Running it in the live container would measure production's prompt, not yours.)
3. Keep the prompt gender-neutral ("the seller"); the owner's text once said he/his in 9 places and the model copied it.
   `tests/test_v55_persona_generator.py::test_polish_handles_hindi_english_and_company_as_contact` guards this.
4. Prefer a clearer rule or a short example over more MUSTs (the greeting-counts-as-a-sentence example fixed most
   three-sentence openings).
5. Measure before and after with skill `sellergenome-persona-evaluation`; report delivered *and* model scores.

## Checks vs polish — where a fix belongs

| Put it in `evaluate()` (a check) | Put it in `polish()` (a code fix) |
|---|---|
| Needs judgement or rewriting: trigger order, opening length, ≤18 words, no he/she, numbers not in input, never-mention | Pure rule or arithmetic: dial bounds (±25 pace/mix, ±15 formality/warmth, +15 when G), guardrails text, evidence ids that don't exist, "no evidence → no insight", withheld fields in gaps, a plan whose trigger isn't in the data, the native-speaker placeholder, "Name ji" when the contact is the company |

Make a check **critical** only when the output is wrong or unsafe (it costs 25 points and triggers the repair round).
Critical today: not a JSON object, missing sections, wrong segment, invented record ids, H insight without evidence,
invalid evidence ids, dial position not 0–100, plan presence vs expected triggers, unknown hot-lead type as trigger,
open request without a ticket in progress, never-mention, transcript quotes, **products only from the input**
(`invented_products`: an item must equal or be a whole-word part of an input product/category/PRDNAV/enquiry name) and
**no placeholder identity**. Polish removes invented products, replaces a placeholder name/company with the company
record's, and drops a plan when `lead.do_not_call`. Warning added in 5.6.0: opening sentences of 14 words or fewer
(the prompt asks for 12 — the owner wants short lines). Everything else is a warning (4 points).
`evaluate()` has up to 40 checks (the per-dial check runs four times; 30 run when no plan is expected). Every polish change is listed in `evaluation.corrected` ("Corrected by code").

Expected triggers (`expected_triggers`) follow the prompt's order: open_request (ticket in progress) →
follow_up_due (callback/TBRO/appointment/follow-up with a date ≤ today+3, incl. "after the 10th", "10 tareekh",
"kal" against the record date; no later call/meeting) → hot_lead (type in the table) → unresolved_last_interaction
(pending item in the latest note, nothing later).

## How the output reaches the call

- **Spoken opening**: `teleprompter.opening()` = the bot's short greeting ("Namaste X ji, main Payal, IndiaMART ki
  virtual assistant.") + `teleprompter.opening_hook` when the plan has one; otherwise `teleprompter.value_line()` —
  the seller's product (generator basics → storefront → dataset category) and the first real reason (enquiries in
  30 days → local buy leads → last meeting → last bot call; never a mock for a real seller) — then the meeting ask.
  `to_persona` sets the hook from the plan's opening minus its greeting and "IndiaMART se VANI" (`opening_hook()`).
  For a non-Hindi/English seller the plan opening is the placeholder `[line to be written and checked by a native
  speaker]` (never spoken); the hook becomes a short English line per trigger (`TRIGGER_HOOK_EN`).
  If you don't set the hook, the teleprompter falls back to `teleprompter.ACTIVITY_HI[activity_id]` ("aapne renewal
  page dekha"), which contradicts the plan.
- **Delivery**: dials → pace 0.85–1.20×, Hinglish mix, formality, warmth, language (`to_delivery`).
- **Brief**: `brief.from_generated` — chips use the record/fact's own label when the model labels evidence "r2";
  products may be strings; the Quality panel shows score, failed checks, corrections and the model's own score.

Cache: generations are reused for `llm.aggregation_cache_hours` (24 h) keyed by prompt + input + model + app version.
A change to `evaluate`/`polish`/`to_persona` alone does not change the key — bump `app.version` (both settings files)
or the old result is served.

## Verify on the live site

After deploying (skill `sellergenome-release-deploy`) run
`.claude/skills/sellergenome-live-qa/scripts/qa_call.py https://sellergenome.lehana.in OUT chromium 1440x900 syn-91000105 --admin`
and look at the screenshots: Why call, the teleprompter line, the Quality panel, products, no never-mention hits.
Check a regional seller too (`syn-91000104` Tamil, `syn-91000112` Punjabi).

## References

- `references/checks.md` — every check with severity and the bug it came from.
- `docs/architecture/persona-generator.md` — the long-form design and run history.
- Decisions D33–D40 in `docs/business/decisions.md`; D40 (support/deactivation as a trigger) awaits the owner.
