---
name: sellergenome-persona-evaluation
description: Measure how good SellerGenome's generated personas are — run the live scenario suite (14 hero sellers, 11 rule-based edge cases, 2 real sellers from cached live data) from Admin or from any worktree, read and compare reports, add a new edge case, and record results in Elasticsearch and the delivery document. Use whenever someone asks "how good is the persona", "test the prompt", "did this change improve quality", wants a before/after comparison, a regression check after touching the persona generator or parsers, or a new scenario covering a rule from the prompt or API guide.
---

# Persona evaluation

A number without a comparison is noise. Always run a baseline, change one thing, run again, and report both the
**delivered** score (after the code fixes) and the **model's own** score (`raw_score`), plus what failed.

## Ways to run

| When | How | Cost / time |
|---|---|---|
| Code is deployed | Admin → Persona generator → *Run the suite on Sarvam* (or `POST /api/v5/admin/persona-generator/eval?only=Name,Other` with the operator key). Rows stream in; *Open* shows a case; results go to ES `sellergenome-v5-persona-evals` | ≈25 LLM calls, ≈4 min; a second start returns 409 |
| Unmerged code in a worktree | `scripts/run_eval.sh OUT_DIR [name filters…]` (this skill) — runs `scripts/persona_eval.py` from the worktree inside the app container's network with the app's env files, worktree read-only | 27 cases ≈5 min; filters run a subset |
| Compare two reports | `scripts/summarize.py NEW.json [OLD.json]` | offline |

`OUT_DIR` belongs outside Git: `/root/data/qa-artifacts/<date>-<topic>/raw/`. Fetch the latest Admin run with
`curl -s -H "x-operator-key: $KEY" https://sellergenome.lehana.in/api/v5/admin/persona-generator > admin.json`
(`summarize.py` unwraps `last_eval`) or a specific run with `GET /api/v5/admin/persona-generator/eval/{id}`.

Differences that matter when comparing: **only Admin runs are indexed in ES** (`run_eval.sh` writes JSON only);
the Admin suite runs the 25 synthetic cases, the script adds the 2 real sellers (`REAL` in `scripts/persona_eval.py`);
the script pins `today = 2026-10-10` while Admin uses the real date (and `enquiry_stats` always uses the real clock),
so compare like with like — script vs script, Admin vs Admin, same day.
The LLM gateway allows 30 requests/min per caller: never run two suites at once, and avoid running one during a demo.

## Reading a report

- `summary`: cases, `mean_score` (delivered), `mean_raw_score`, `no_critical`, `repaired` (cases that needed the repair
  round), `scenario_checks` (edge-case expectations met), `plan_matches` (main trigger = expected), `median_ms`,
  `top_failures` and `top_raw_failures`.
- Per row: `score`, `raw_score`, `attempts`, `expected` vs `plan`, `failed` (checks with detail), `corrected`
  (what polish changed), `scenario_checks`, `persona` (full output).
- A failure that appears only in `top_raw_failures` is being fixed by code; one in `top_failures` reaches users.
- Real-seller rows depend on cached live responses (`REAL` GLIDs in `scripts/persona_eval.py`); if the gateway is
  offline they still run from the cache.

Typical history (2026-10-10): 75.2 → 95.9 → 96.9 (script) → 98.1 → 98.9 (Admin, 25 synthetic). Most remaining misses
are model judgement: three-sentence openings, insights over 18 words, trigger order, dial nudges.

## Before you trust a low score, check the checker

Run 1 lost points to false positives in our own checks (turnover bands as "amounts", a buyer name token inside the
seller's company name, dial bounds the code sets anyway). For every new failure: open the case, read the text the
check quoted, and decide *model wrong* vs *check wrong* vs *input wrong* (e.g. a terse fact label). Fix the right
layer (skill `sellergenome-persona-generator` explains check vs polish vs input).

## Adding an edge case

Edge cases live in `backend/genome/persona_eval.py` → `EDGE`: `(name, base hero seller id, edit(p, results, lead,
today), [(scenario check label, lambda out: bool), …])`. Write one when a rule from the prompt or the API guide has no
case yet:

1. Pick a hero (`roster.heroes()`, ids `syn-91000101…114`) whose data is closest; edit only what the rule needs
   (e.g. set `lead["data_type"] = "PNSM"`, add a meeting note with "work to be done", make contact = company).
2. Make the expectation observable on the output JSON (`call_plan` is None, trigger type, opening contains the
   placeholder, a data doubt exists, a dial position).
3. If the rule changes the expected trigger, add it to `test_expected_triggers_follow_the_prompt_rules`
   (offline test) so the expectation itself is tested.
4. Run only the new case first: `run_eval.sh OUT "New case name"`, then the full suite.

## Reporting

Put the run table (delivered, model, no critical, repaired, scenario, plan right, median) in the delivery document
and `docs/architecture/persona-generator.md`; keep raw JSON in qa-artifacts. Kibana can chart
`sellergenome-v5-persona-evals` by `eval_id`/`prompt`; `es.py agg sellergenome-v5-persona-evals eval_id score`
(skill `sellergenome-elastic`) gives the same numbers in a terminal.
