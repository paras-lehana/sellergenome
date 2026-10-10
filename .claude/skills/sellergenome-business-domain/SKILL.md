---
name: sellergenome-business-domain
description: IndiaMART and hackathon domain knowledge for SellerGenome — the PS04 Persona Design problem and its scoring, how IndiaMART's seller and buyer calls work today (activity → hot lead → 15-minute window → context → vendor push → VANI bot call → meeting → executive), what GLID, custtype/custtypeweight, PNS, BL, STS, LMS, MCAT, ISQ, TrustSEAL, hot-lead types (PIM, SCHD…) mean, and the honesty rules for any claim. Use when writing product copy, docs, demo scripts or prompts, when a term is unclear, when deciding whether a feature answers PS04, or before stating any number or outcome to the owner, PMs or judges.
---

# IndiaMART × Sarvam domain (PS04 Persona Design)

SellerGenome gives IndiaMART's seller-calling voice bot (VANI) a persona built per seller from evidence, adapts it
live during the call, and makes every decision inspectable. Read `references/ps04-problem-statement.md` for the
exact brief and `references/glossary.md` for terms.

## What PS04 asks and how we answer

| PS04 asks (weight) | SellerGenome answer | Where |
|---|---|---|
| Persona per seller from seller data — voice, language, tone, **a reason for every choice** | Owner's Persona Generator prompt v0.3 on Sarvam: dials with H/M/G confidence and evidence chips; rules persona as fallback | skill `sellergenome-persona-generator` |
| Conversation plan: opening, personalisation, objection playbook, escalation, guardrails; delivery changes, never facts/prices | Call plan only when the data gives a reason (open request → follow-up due → hot lead → unresolved); fixed guardrails | brief, teleprompter |
| Past-call evidence with strength (15%) | Records r…/facts f… cited per insight; STS/PNS/enquiry evidence | `sellergenome-indiamart-data-rules` |
| Live switches: frustration, confusion, interest, language, rush, with a **switch log** (timing 25%) | `signals.py` + `conversation.py` switch log (cooldown, explicit bypass, held switches logged) | skill `sellergenome-vani-bot` |
| Distinct personas (20%) and fit (25%) | 14 hero sellers across 9 archetypes and 8 languages; evaluation suite | `sellergenome-persona-evaluation` |
| Natural voice in 2+ languages on Sarvam (15%) | Bulbul v4 streamed, Saaras v4 realtime, Payal on Sarvam Voice Agents | `sellergenome-voice-engines`, `sellergenome-sarvam-voice-agent` |
| One slide | `/slide` exists but its copy is V1 — it lacks a sample persona spec (gap) | skill `sellergenome-demo-judging` |

FAQ rules that bind: no fixed persona library; a pre-scripted switch scores lower; persona never changes product or
price; missing fields fall back to defaults at lower confidence.

## How IndiaMART's calls work (and where we act)

**Seller side:** a platform activity (e.g. `activityid 2368` viewed the Star Supplier package) → SALES-HOTLEAD-GENERATOR
makes a hot lead → IndiaMART waits ~15 minutes (exploration window), re-checks the data type (PIM…) and assigns an
executive → context fetch (Global Context ≈0.23 s, three address candidates, `ExtractLocalityV1` LLM ≈18.1 of 19.1 s,
last meeting date, Insights API: summary + 7 categories + *doubts*) → a ≈28-field payload is pushed to the calling
vendor (SquadStack; Arrowhead is another) → **VANI calls; its only job is to book a meeting** → a human executive
meets and sells. Doubts are things to confirm on the call, not facts.

**Buyer side:** a buyer's PNS call the seller doesn't answer goes to the help desk → buyer-side VANI answers (never
claims to be human, one question per turn; ask limits product 4, quantity 3, each spec 2, name/city/state 3, transfer
consent 2) → transfers to a live alternate seller only with consent, or posts a buy lead. Hindi, English, Telugu.

**Hot-lead types:** SCHD demo request, PIM payment interested, PAM payment attempted, OLP online payment, OLPR online
payment renewal, PUA premium user activity, UA user activity, TF toll-free, UATF unassigned toll-free. Anything else
(e.g. PNSM) is not a reason to call.

## Terms that are easy to confuse

- **custtypeweight** <1000 paid, >1000 free, =1000 or missing *unknown*; **custtype** is a different code. Since 5.5
  paid/free comes from `kycdetails.customer_type` first.
- **Enquiry** goes to one seller; a **buy lead (BL)** is given to IndiaMART and bought by sellers (local/national).
- **PNS** = masked number that can ring several of the seller's phones (the owner's brief wins over "Preferred
  Number Service" in the training prompts).
- **"Meeting Fixed"** is a *claim* by the bot/evaluator, not a held meeting or a sale.
- **STS** = Sales Tracking System history; **LMS** = buyer–seller message centre (25 per page, newest first).
- Seller ids: `syn-` hand-written hero, `gen-` generated from a hash, `real-` a seller from the supplied dataset or an
  approved test GLID (PM only; live upstream for the 77 approved, or any GLID when `upstream.glid_access` = prod).

## Honesty rules for every claim (owner and judges check these)

1. Label simulations (the call floor), mocks and live calls as what they are; never say streaming, diarization, LLM
   inference or an integration ran when a fallback did.
2. Historical correlations and cohort shares are associations, not causal uplift; Wilson intervals are call-level.
3. Text cues are not acoustic emotion; no certainty about mood or sarcasm.
4. Never invent prices, ROI, discounts or guarantees; packages are never priced.
5. The bot is IndiaMART's named virtual assistant and never claims to be human (D10 — still "needs owner
   confirmation"; Admin can switch `app.disclosure_mode` to `explicit_ai`).
6. Evaluator JSON and machine transcripts are weak labels; no recording was listened to; no ASR is hallucination-free.
7. Generator quality scores are rule checks, not human judgement; real sellers lack Global Context (userCtx 404).
8. Zero, blank and missing are different; unknown is not false.

When stating a number, take it from `docs/research/facts.md` (canonical numbers with denominators) and name the
denominator — e.g. answer rate 50.77% by attempts vs 65.43% per-seller mean are both "true".
