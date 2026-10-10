# The checks in `persona_generator.evaluate()`

Score = 100 − 25 × critical − 4 × warnings (floor 0). A critical failure on the first reply triggers one repair
round; the better of the two replies is kept, then `polish()` runs and the checks run again (delivered score).
"Origin" says which bug or rule produced the check, so you know what breaks if you remove it.

| # | Check | Severity | Origin / why |
|---|---|---|---|
| 1 | JSON object | critical | the reply must be a dict; if not, no other check runs |
| 2 | Every top-level section present | critical when missing | schema 0.3 |
| 3 | Segment follows customer_type | critical | guide: Paid/Free from `kycdetails.customer_type` (D39) |
| 4 | Guardrails exactly as given | warning (polish restores) | prompt: fixed three lines |
| 5 | Record only lists given records | critical | the model must copy `records`, never add one |
| 6 | Record types from the list | warning | Executive call, Meeting, Call note, Ticket, Hot lead, System notice |
| 7 | Every H insight has evidence | critical | guide: "no evidence, no insight" (polish drops evidence-less insights) |
| 8 | Evidence points to given records or facts | critical | run 1: evidence "kycdetails", "lead.language" |
| 9 | 5 to 9 IndiaMART insights (fewer with little data) | warning | code: `5 <= n+1 <= 10` on non-gap insights (4–9 pass, one gap insight assumed) when ≥ 3 records; ≥ 1 otherwise |
| 10 | Insight groups from the list | warning | Relationship, Platform use, Issues, Reachability, Data gaps |
| 11 | Insights at most 18 words | warning | prompt; most frequent model miss |
| 12 | Confidence is H, M or G | warning | prompt |
| 13 | Summary at most 30 words | warning | prompt |
| 14 | 2 to 5 persona tags | warning | prompt |
| 15 | Voice gender stays neutral | warning | prompt: "Neutral default" |
| 16 | Dial position 0–100 | critical when not a number in range | runs once per dial (4 checks); bounds are set by `enforce_bounds`, not scored |
| 17 | No call plan without a trigger | critical | prompt: never assume a reason to call |
| 18 | Reason given when there is no plan | warning | `call_plan_note` |
| 19 | Call plan present when a trigger exists | critical | expected triggers computed from data |
| 20 | Main trigger follows the rule order | warning | open_request → follow_up_due → hot_lead → unresolved |
| 21 | Opening: ≤ 2 sentences, ends with one question | warning | the greeting counts as a sentence; placeholder skipped |
| 22 | Opening has no gendered verb forms | warning | karunga/karungi … |
| 23 | Opening does not speak for the seller's company | warning | live: "Sri Venkateswara Agro Tools se baat ho rahi hai" |
| 24 | Hindi opening in Roman letters | warning | prompt: Hinglish in Roman script |
| 25 | Company name not addressed with 'ji' | warning (polish strips "Name ji,") | contact = company case |
| 26 | 2 to 4 points to cover | warning | prompt |
| 27 | 1 to 3 questions to ask | warning | prompt |
| 28 | Unknown hot-lead type is not a trigger | critical | PNSM edge case |
| 29 | An open request needs a ticket in progress | critical | live run 5: a question asked on a call became "open request" |
| 30 | Never-mention list respected | critical | phone, email, GSTIN, link, exact amount (turnover bands allowed), First Copy, refund/legal/fraud topics outside `call_plan.avoid`, forbidden buyer/staff names, employee ids. "Competitors pay less" is allowed; competitor *names* are not |
| 31 | No transcript quotes | critical | 6-word overlap with any transcript |
| 32 | No judgement of the seller's responsiveness | warning | guide: never judge responsiveness |
| 33 | No he/she — 'the seller' | warning | `.record` text is skipped (it restates executives' notes) |
| 34 | Every number comes from the input | warning | "3,900" is one number; a percentage equal to round(100·a/b) of two given counts is allowed |
| 35 | Buyer stats name their window | warning | guide: name the time window in every count |
| 36 | Gaps do not list withheld contact/GST fields | warning (polish removes) | the model lists them in ~40% of replies |
| 37 | Voice language follows the guide's order | warning | `refs.language` |

## False positives already fixed (don't reintroduce)

- Turnover bands ("40 L - 1.5 Cr") are not amounts (`BAND` regex is stripped before `MONEY`).
- A buyer name token that is part of the seller's own company ("Spice" in "Menon Spice Traders") is not a leak.
- "Hindi-English" is Hindi for the native-language rule (split on non-letters).
- `schema_version` "0.3" is not an invented number.
