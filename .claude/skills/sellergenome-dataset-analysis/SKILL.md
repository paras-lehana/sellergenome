---
name: sellergenome-dataset-analysis
description: Work with SellerGenome's supplied IndiaMART corpus (16 private CSVs, 908,856 rows — sellers, bot calls, evaluator outputs, call turns, a 5,000-call recording sample, executive calls) and the analysis built on it — the canonical facts sheet, the /analysis atlas, the PM study (/pm, call-diagnostics cohorts), raw Elasticsearch indices and research dashboards. Use when someone asks for a number about calls, sellers, outcomes or objections, wants a new aggregate or chart, re-profiles or re-indexes the dataset, builds or verifies the PM study, or plans an evaluation — and whenever privacy of the private dataset is at stake.
---

# Dataset and analysis

The corpus is private and sits outside Git at `/opt/sellergenome/dataset/` (never copy rows, ids,
transcripts or recording URLs into the repo, logs, prompts or public pages). Everything public is an aggregate.

## The corpus (facts in `docs/research/facts.md`, method in `docs/research/data-research.md`)

| Family | Rows | Notes |
|---|---|---|
| Seller files (2 parts) | 149,363 sellers × 58 cols | no `custtype`, no language column |
| Bot calls (4 periods, 1 Apr–30 Sep 2026) | 269,455 answered calls × 13 | outcome labels are bot/evaluator claims |
| Evaluator outputs (7 parts) | 278,332 | valid JSON ≠ truth |
| Call turns | 139,555 turns / 12,788 calls | only 2,207 of 2,494 sample attempts keep usable turns |
| Recording sample | 5,000 | outcome-balanced; all 452 personalised-trial calls; **no audio has been listened to** |
| Executive calls | 67,151 (Aug–Sep) | no audio |

16 CSVs, prefix `ps07_` (the problem is PS04), 348,723,578 bytes. Joins on **string** `fk_glusr_usr_id` and
`attempt_id`. The V3 ZIP is a byte-identical duplicate — never re-import it.

Facts people ask for: Meeting Fixed 31,054 (11.5%, a claim) · Not Interested 43.2% · Call Later 16.2% · General 29.1% ·
41 "Meeting Fixed" labels have `meeting_fixed=0` · median call 22 s, 59.3% under 30 s · answer rate 50.77% by attempts
vs 65.43% per-seller mean (different denominators — say which). Flags are `1.0`/`0.0`/blank with 69,401 missing per
flag (missing ≠ false); positives: DNC 1,178, frustration 139, asked-if-bot 125, already-with-executive 4,205.
Seller snapshots span the whole window — beware leakage when predicting.

## Scripts (all explicit, private outputs, no PII on stdout)

| Job | Script |
|---|---|
| Profile every CSV into aggregate metadata | `scripts/profile_dataset.py --dataset dataset --output data/catalog.json` |
| Normalised private seller DB (aliases, controlled summaries) | `scripts/import_dataset.py --dataset dataset …` |
| Index the raw CSVs into ES (`sellergenome-raw-ps07-*-v2-*`, receipts with counts/hashes only) | `scripts/index_raw_dataset.py --dataset … --receipt …` then `scripts/verify_research_index.py` |
| Aggregate evidence atlas for `/analysis` | `scripts/build_analysis_report.py --dataset …` |
| PM study + private review DB | `scripts/build_pm_analysis.py --dataset … --private-output …`; audit with `scripts/verify_pm_analysis.py --private-input … --receipt …` |
| Kibana research dashboards (saved-objects API, never write `.kibana`) | `scripts/install_research_dashboards.py --es-url … --infra-env … --publisher-env …` |
| Historical summaries into a staging DB | `scripts/import_call_insights.py --dataset … --db …` |
| Rule personas into ES (zero provider calls) | `scripts/index_dataset.py --generate-personas`, `scripts/index_persona_fields.py` |

Run them from the worktree with the private paths. Public aggregates land in Git-tracked `data/catalog.json`,
`data/analysis-report.json`, `data/pm-analysis.json` (aggregate-only by construction); private sidecars and receipts go
to `private/` or `/root/data/qa-artifacts/…`, never `docs/`. `index_dataset.py`/`index_persona_fields.py` write the
legacy `seller-persona-v4-*` and `sellergenome-persona-fields` namespaces, not V5.

## The PM study (`/pm`, `docs/contracts/pm-analysis.md`, evaluator `call-diagnostics-1.3`)

The original seven lexical categories (callback, contact suppression, executive continuity, explanation, language,
price, service) predate the diagnostic evaluator; General calls 78,365 / 44,936 sellers (early ending top cue 38.45%);
596 of 677 calls with an eligible seller cue (88.04%, call-level; the sample is 136/156 = 87.18%) have an observable
next bot response — **not** a rebuttal success rate. Ten conversational cohorts in `data/pm-analysis.json`
with meeting-fixed shares (e.g. price 29.0%, declined 5.4%); each cohort's strategy is a *hypothesis*. Method limits:
call-level Wilson intervals not adjusted for repeat sellers; `causal_reason` stays null; cohorts look back.

## Answering a data question

1. Check `docs/research/facts.md` first; reuse its number and denominator.
2. Otherwise compute an aggregate from the private files or the raw ES indices (skill `sellergenome-elastic`);
   print only counts/shares, never rows.
3. Say what the label means ("Meeting Fixed is a claim"), the denominator and the window.
4. If the number will be shown in the product or docs, add it to `facts.md` with its source and date.

## Planning an evaluation (`docs/research/evaluation-plan.md`)

Split by time (Apr–Jul dev, Aug validation, Sep test) and keep each seller on one side; a 100-call human gold set;
metric gates; five experiments. The proposed p95 < 1.5 s latency was never measured on that plan — measure before
claiming (skill `sellergenome-voice-latency-lab`).
