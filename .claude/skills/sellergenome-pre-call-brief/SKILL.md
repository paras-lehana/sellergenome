---
name: sellergenome-pre-call-brief
description: Change what the PM's pre-call brief ("VANI Persona Studio") shows in SellerGenome — the four blocks (1 Basic details, 2 Engagement with IndiaMART with H/M/G evidence chips, 3 Engagement with buyers, Conversation plan with live dials and Why call) plus the Full record drawer and the Quality panel — and how backend/genome/brief.py maps both the 5.5 generator output and the older aggregator persona into it, with brief.js rendering in the persona pane, compact call-pane plan and full-width view. Use when a PM asks to add, rename, reorder or explain anything in the brief, when a chip opens the wrong source, a block shows "Not in file" unexpectedly, or the brief and the bot disagree.
---

# The pre-call brief

The structure comes from the PM's persona-studio design (`docs/inputs/pm-design/README.md`, structure only — the PM's
original file is private and never committed). Backend `backend/genome/brief.py`; frontend `frontend/cc/brief.js`
(+ `call.js` panes, `app.css` `.pm-*`). API: `GET /api/v5/runs/{id}/brief`.

## Two producers, one layout

`brief.build(run)` returns the generator brief when `persona.generated.persona.basics` exists (`engine: "generator"`,
via `from_generated`), else the aggregator/rules brief. Keep both working — Admin can switch `persona.engine`.

| Block | Generator source (`from_generated`) | Notes |
|---|---|---|
| 1 Basic details | `basics.rows` (k/v + source label), `products` (strings → `{name, api, pointer}`), `flags` | rows in prompt order: Customer type, Nature of business, City, State, Turnover (band), Business type, Hot lead type, Language, On IndiaMART, Last payment |
| 2 Engagement with IndiaMART | `engagement_with_indiamart.insights` grouped (Relationship, Platform use, Issues, Reachability, Data gaps) + `data_doubts` | each evidence `record_id` → chip via `_gen_sources` (records r… / facts f… from the input → API + pointer + origin); a bare "r2" label is replaced by the record's own label |
| 3 Engagement with buyers | `stats` (n/d + window label), `catalogue` (sentences → list, short facts → chips), `insights` | stats must name their window |
| Conversation plan | dials (voice, pace, language, formality, tone) + steps Opening / Points to cover / Avoid / Questions / Next step + **Why call** (trigger type, detail, other triggers) or "No call plan" | compact version in the call pane (`planCompact`), full in the brief; dials move live with switches |
| Full record | `record` log with dates, outcome tone, origin, source; counts; gaps; doubts | drawer, Esc closes |
| Quality | `evaluation`: score, failed checks, corrections, model's own score, attempts, ms | persona pane + full brief |

Chip gotchas: records that come from the lead (hot lead, last call summary, last meeting date) open `context.global`
(`_gen_sources` maps source `lead` there); a run without `results["sarvam.persona_generator"].input` (e.g. a fallback
run) has no evidence chips.

## Making a change

1. Decide whether the data exists in the generator output; if not, add it to the prompt schema (skill
   `sellergenome-persona-generator`) or derive it in `brief.py` from the run results — never invent.
2. Map it in `from_generated` (and the aggregator path if it should show for both engines); every value carries an
   origin and, when it comes from an API, an `api` + `pointer` so its chip opens the source in the inspector.
3. Render in `brief.js` using existing components (`.pm-panel`, `.pm-kv`, `.ev-chip`, `.pm-stat`, `.tagchip`,
   `conf()`), escaping every string (`esc`). Missing → "Not in file", not an empty chip.
4. Keep the bot consistent: if the plan's opening or questions change, `to_persona` feeds the teleprompter
   (`opening_hook`, `first_question`, `avoid`) — check the call pane's teleprompter line matches the brief.
5. Test offline (`test_generated_persona_feeds_the_bot_and_the_brief` pattern), then live: `qa_call.py` (skill
   `sellergenome-live-qa`) at 1440 and 390 — look at the brief, call pane and Quality panel; browser cases 69, 85–89.

Never show: phone, email, exact amounts, buyer names, staff names, ticket content, competitor names (skill
`sellergenome-indiamart-data-rules`); the qa_call.py never-mention scan checks the rendered text.
