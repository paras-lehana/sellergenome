---
name: sellergenome-vani-bot
description: Change or debug SellerGenome's built-in voice bot VANI — seller-side turns (conversation.py), live signal detection (frustration, confusion, interest, rush, language switch, stop, bot question), the switch log with cooldown and explicit-request bypass, delivery factors (pace, language, sentence length, warmth), the teleprompter (opening, next question, objections, handoff), the simulated seller in Auto mode, take-over, and the buyer-side VANI on the buyer desk. Use when the bot says something wrong, switches too late/early or not at all, ignores "Hindi mein boliye" or "stop", sounds pushy, quotes a price, or when working on the switch log PS04 scores.
---

# VANI (built-in bot)

PS04 weights switch timing at 25%; the switch log is the evidence. Every live change is a row with signal, cue,
method, confidence, level, changes `{key: [old, new]}`, reason, explicit, applied, held_by_cooldown, override.

## Signals (`backend/genome/signals.py`)

Lexicon cues in romanised Hinglish, Devanagari, English and Telugu, incl. stop and "are you a bot" cues. Confidence:
explicit 0.95, word list 0.75, script detection 0.7. Order: **stop > language > frustration > confusion > rush >
interest**. Effects: confusion → pace −0.12, short sentences, simplify · rush → pace +0.12, short · frustration →
pace −0.06, warmth/empathy high, acknowledge first · interest → medium sentences, move toward the meeting · language →
switch language (explicit requests apply immediately).

## Switch rules (`backend/genome/conversation.py`)

Levels decay with a half-life of 3 turns. Cooldown per factor: `persona.cooldown_seconds` 8 / `cooldown_turns` 1.
An explicit request ("Hindi mein boliye") bypasses cooldown; a repeated signal (level ≥ 1.0) overrides it; held
switches are still logged. Stop / do-not-call ends **this call** immediately (outcome `do_not_call`, `call.stopped`;
later turns get 409). No cross-call suppression list exists yet — a production DNC list would be the vendor's job.
"Aap bot ho?" → the bot confirms it is IndiaMART's virtual assistant (D10) — never claims to be human.

## Replies

`sarvam-105b-conversations` via the gateway (skill `sellergenome-sarvam-llm`), 350 tokens, T 0.4; ≤ 10 words (short)
or 18 (medium) since 5.6.0 (owner: "sentences are too long … directly come to point") — one sentence, one idea, one
question; a busy seller gets a direct meeting ask; no prices, discounts, ROI or guarantees. `seller_facts` hold only
real data: confirmed company (never the "Private seller" placeholder), products and the demand/last-contact line from
`teleprompter.product_label`/`value_line`, the generator plan's purpose/next step. On any LLM error/limit it answers a
canned fallback line — if users report "the same line again", check the gateway's 30 requests/min limit and budgets.
In Auto mode a failed simulated-seller LLM call returns 409 (no fallback); Auto pauses after
`budgets.max_auto_turns_per_call` (24); turns are limited to 40/min per visitor. The bot introduces itself as
`app.bot_name` ("Payal" by default), not "VANI".
Turn endpoints: `POST /api/v5/calls`, `/calls/{id}/turn` (you are the seller / take over), `/calls/{id}/auto` (Sarvam
plays the seller from IndiaMART's archetype training prompts in `docs/inputs/sales-training-prompts/`); both accept
`speak` so speech starts inside the request (skill `sellergenome-voice-engines`).

**Prompts live in constants + Admin (5.9.2):** `conversation.BOT_TURN_SYSTEM`, `DIRECTIVES`, `SIM_SELLER_SYSTEM`,
`ARCHETYPE_NOTE`, the training files, and `buyer.REQUIREMENT_POLICY` / `COMPACT_POLICY` / `REWRITE_PROMPT` are the
defaults; call sites read `prompts.text(id)`, so an Admin → Prompts edit wins. Keep `.format` placeholders and `{{ }}`.

## Teleprompter (`backend/genome/teleprompter.py`)

Stages greeting → qualifying → package → meeting → wrap-up. Opening (5.6.0, owner: the most important words in the
first 15–20) = a short intro (D10 disclosure; Hindi "Namaste {name} ji, main {bot}, IndiaMART ki virtual assistant.",
no double space without a name; English/regional: English intro) + either `persona.teleprompter.opening_hook` (the
generator's plan — skill `sellergenome-persona-generator`) or `value_line()`: "Aap {product} ka kaam karte hain —
{first real reason}" (enquiries 30 days → local buy leads in the city → last meeting date → last bot call date; a mock
never speaks for a real seller) + the meeting ask "Kya ek chhoti meeting rakh lein — IndiaMART aapke business mein
kaise madad kar sakta hai?". Objection lines are one short sentence each.

**Talk as Seller** (5.6.0): floor tile → `POST /api/v5/runs {origin: "talk"}` → `sessionStorage sg-talk-<run>` →
`POST /api/v5/calls {opening: true}` puts the teleprompter opening in as the first bot turn; no seed, Auto off, the
operator types or speaks as the seller. **Switch visibility**: the call pane shows a *Persona switch* banner (latest
applied switch, flashes once, toast) and accented "⇄ Persona switch" lines in the transcript. Objection lines per type (price → never quote, executive shows the plan; lead_quality →
acknowledge and escalate; not_interested → ask to call next month …). Handoff: complaint, refund, pricing details or a
request for a person → human executive. Packages are matched by evidence and **never priced**.

## Buyer-side VANI (`backend/genome/buyer.py`, `/buyer`)

**5.7.0:** policy `requirement` = the owner's VANI buyer-requirement prompt (product → number of pieces, never the word
"quantity" → specifications one at a time → close with the sellers' link on WhatsApp; no name/city/transfer). Code
enforces product 4 / pieces 3 / each specification 2 (`attempts["spec:<name>"]`), the confirm-before-product-change rule
(`spec_names` reload), dispositions BL Approved → BL Enriched/Deleted with the owner's outcomes, and speaks the owner's
closing lines verbatim (`CLOSES`). *Talk as Buyer* (`POST /api/v5/buyer/calls {talk:true}`) starts from scratch with
`requirement_opening()`; `adaptations` (language, limit, product) drive the page's *VANI adapted* banner. The same flow
runs on Sarvam as a separate agent (skill `sellergenome-sarvam-voice-agent`). Replies use a male voice by default (shubh).

Answers buyers whose seller missed a PNS call: one question per turn, ask limits (product 4, quantity 3, each spec 2,
name/city/state 3, transfer consent 2 — `buyer.*` settings), transfers to a live alternate seller only with consent,
otherwise posts a buy lead; Hindi, English, Telugu. Prompt policies compact/original (original needs the operator).
Tools are mocked (test-only actions). Listening on the buyer desk uses **no seller keyterms** (D32).

## Testing a change

Offline: conversation/signals tests in `tests/test_v5_genome.py` and friends. Live: browser case 50 (take over, type
"jaldi boliye, time nahi hai" → rush switch logged, shorter reply; "Hindi mein boliye" → explicit switch bypasses
cooldown), cases 54 (buyer flow), 74 and 81 (talk live, answered in ≈2 s). Never pre-script a switch for a demo — PS04
scores that lower.
