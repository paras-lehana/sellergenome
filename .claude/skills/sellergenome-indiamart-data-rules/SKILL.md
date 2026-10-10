---
name: sellergenome-indiamart-data-rules
description: How SellerGenome must read and interpret IndiaMART seller data — Global Context, STS history, PNS buyer calls, enquiries and the company storefront — per the human-evaluated Consolidated API Guide (2026-10-09). Use whenever you parse, map, summarise, count or show any IndiaMART API field; decide paid/free, answered calls, meetings, language or hot-lead type; decide what may never be mentioned to a seller or an LLM; or change parsers.py, contract.py, mock_upstream.py or the persona input. Also use when the owner sends a new or revised API guide.
---

# IndiaMART data rules (Consolidated API Guide)

The guide in `references/consolidated-api-guide.md` is the owner's human-evaluated description of the five APIs
that feed a persona. It outranks guesses from samples, older docs and field names. This skill turns it into the
rules SellerGenome applies and shows where each rule lives in code, so a change to the data path keeps them.

Read the guide section for the API you are touching before changing a parser. The table below is the short form.

## The five APIs (seller id = glid everywhere)

| API | Registry id | Gives | Parser |
|---|---|---|---|
| Global Context | `context.global` (userCtx) | `data.{kycdetails, connectdetails, ticketsdetails, activitydetails, BLdetails}` | `parsers.global_context` |
| STS History | `sts.history` | one URL-encoded HTML blob of dated entries, newest first; no glid | `parsers.sts_entries` → `sts_for_persona` |
| PNS Details | `pns.insights` | AI-extracted buyer phone calls (`data[]`, `extracted_data.metadata`) | `parsers.pns_calls`, `pns_stats` |
| Enquiry | `enquiry.list` | `{status, code, error, response.enquiries[]}`; no glid | `parsers.enquiries`, `enquiry_stats` |
| Company Attribute | `company.detail` | `DATA.COMPANYDETAIL`, `FACTSHEET`, `PRDNAV`, `HEADER`, `URL_DETAIL` (beside DATA) | read in `persona_generator.build_input` |

STS and Enquiry responses carry no glid: attach the glid you requested before joining anything.

## Rules that are easy to get wrong

1. **Paid or Free** = `kycdetails.customer_type`. `custtypeweight` (<1000 paid, >1000 free, =1000/missing unknown)
   is only the fallback (`contract.classify_customer`, decision D39). `custtype` is a different field.
2. **A call row is not an answered call.** `call_answered_date` exists even when nobody picked up. In code
   (`parsers.global_context` → `answered`): a summary or the seller's own transcript lines = answered; a note alone =
   unknown (shown as "Note only"); nothing is ever marked "not answered" from the timestamp alone.
3. **Meetings** count only when `meet_sucessful_date` (sic, one "c") is filled. `meet_sts_update` follows a 01–07
   template; split on the numbers (`parsers.meeting_note`) and **drop 02 Decision Maker Name & No** (a person + phone).
4. **Language** — no API has a language field. The guide's order is the seller's own words (transcript `Customer:`
   lines) and PNS languages, else the state's language marked **G**. In code (`persona_generator.language_hint`):
   `lead.language` if given → PNS buyer-call languages → transcript script mix (Latin share → Hindi/English) → state
   (`STATE_LANGUAGE`, unlisted states → Hindi, G). Synthetic sellers always carry `lead.language` from the roster
   (`orchestrator._generate`), so the fallback chain matters for real sellers.
5. **STS: person vs system.** A real STS log shows channels `Online Payment`, `WA_9696`, `SP_UPDATE_STS_HIST` and
   `…PENNY DROP CONSUMER` are automated; `WebERP` is a person unless the updater is an auto process
   (`parsers.automated`, decision D35). Merge repeated automated notices into one entry with a date range.
6. **PNS is AI-extracted.** Records disagree; trust patterns; count **distinct buyers and days**; a `callback` is a
   *promise*, never proof it was kept; never judge how responsive the seller is (1 of 10 buyer queries had an answer).
7. **Enquiries.** ISQ items are `{DESC, RESPONSE}`. Distinct buyer = trimmed `buyerName` + `buyerMemberSince`.
   Quality = how many of mobile/email/address/GST checks pass; seriousness = a quantity was given. The list may be
   capped — the true total is `activitydetails.enq_last_30_days`; when they disagree, report both as a data doubt.
   **Skip and never mention "First Copy" (counterfeit) enquiries** (`parsers.FIRST_COPY` regex covers replica/clone).
8. **Tickets**: use only type, date and whether in progress (`wip_tickets` / `current.tickets`). Never their content.
9. **Turnover** is a band ("40 L - 1.5 Cr"); bands are allowed, exact amounts are not.
10. **Hot-lead types** (the reason for the call): SCHD, PIM, PAM, OLP, OLPR, PUA, UA, TF, UATF with the meanings in
    the guide (`persona_generator.HOT_LEAD_TYPES`). PNSM and anything else is **not** a trigger.

## Never mention — to a seller, in a persona, or to an LLM

Buyer names, numbers, ids, an individual buyer's city, quotes from buyer calls/enquiries, recording links; exact
prices, amounts or turnover figures (bands OK); GST number, address, email, phones, coordinates; IndiaMART staff
names and employee ids; buyer-seller conflict, refund, legal or operations tickets, fraud/risk flags, complaint counts,
raw ticket history; transcript quotes; competitor **names** (saying "competitors pay less" is fine), one-star ratings
and the star split; First Copy enquiries; any claim the seller ignored a buyer or missed a follow-up the data doesn't show.

Enforce it **before** the model sees the data, then check again after:

- `parsers.scrub` (contact details, links, account numbers, staff names) and `parsers.persona_safe` (drops whole
  sentences about refund/legal/fraud/verification; masks amounts) on call, meeting, executive-comment and STS text.
  Storefront strings (FACTSHEET values, `COMP_TAGLINE`, category and product names) are passed unscrubbed after the
  title filter — scrub them too if a new field could carry contact details;
- `parsers.sts_for_persona` (amounts → `[amount]`, employee ids → `[id]`, ops notices dropped, ticket → type only);
- `persona_generator.build_input` (spec names only, no buyer text, no ratings, no competitor list, no transcripts);
- `persona_generator.evaluate` never-mention check (critical) on the output.

Input caps in `build_input`: enquiries 10 (the API returns the latest 10), seller-side PNS calls 15, products 6,
categories and factsheet rows 8 each. Global Context is read from `context.global` (userCtx) only — the
`context.getcontext` output is not used by the generator.

## When you change a parser or mapping

1. Read the guide section; quote the rule you implement in a code comment only if it is non-obvious.
2. Make the mock in `backend/genome/mock_upstream.py` produce the **same shape** (mocks and live share parsers).
   Mock responses are cached in Elasticsearch under a key salted with the app version, so bump the version.
3. Add a test in `tests/test_v55_persona_generator.py` (or a new test file) using the real shape — see the
   enquiry `{DESC, RESPONSE}` and STS channel tests there.
4. Run the persona evaluation (skill `sellergenome-persona-evaluation`) to see the effect on real outputs.
5. Record a decision in `docs/business/decisions.md` if a rule changed, and update
   `docs/architecture/sources-and-apis.md` ("what the guide changed" table).

## When the owner sends a new guide

Convert it (`unzip -p file.docx word/document.xml` → strip tags, keep tables), diff it against
`references/consolidated-api-guide.md`, list what is new per API, then apply the steps above. Store the new copy here
and the original in the task folder (skill `sellergenome-delivery-closeout`).

## Still unknown (do not invent)

Live Global Context for real sellers (userCtx answers 404 through the gateway — only mocks follow the documented
shape); STS human-entry formats beyond the one automated sample; the free value of `URL_DETAIL.URL_TYPE`.
