---
name: sellergenome-voice-plugin
description: Integrate an external voice bot (LiveKit, Sarvam Voice Agents, or any other provider built by another developer) with SellerGenome through the provider-neutral voice plug-in contract — register a call session, fetch the seller's persona context and prompt sections, stream transcripts/signals/switches/outcomes back so the call shows as a REAL CALL on the floor, and re-fetch context when a live switch changes the persona. Use when someone wants to connect a bot, asks what SellerGenome sends a bot, sees no tile for a real call, rotates the plug-in key, or edits voice_plugin.py, integrations/voice_plugin/ or docs/architecture/voice-plugin.md.
---

# Voice plug-in contract (decision D13)

SellerGenome prepares the persona; any bot can speak it. Code `backend/genome/voice_plugin.py`, endpoints in
`backend/genome/api.py`, harness `integrations/voice_plugin/{harness.py,client.py,livekit_agent_example.py}`,
spec `docs/architecture/voice-plugin.md`.

## Auth

Header `X-Voice-Plugin-Key` = `VOICE_PLUGIN_KEY`. The server reads it from the Admin secret store
(`genome-secrets.json`; rotate in Admin → Voice plug-in); `/opt/secrets/env_files/sellergenome-voice-plugin.env` is
only for the harness — update it after rotating. The key works only on `/api/v5/voice/*`. Never put it in a page or a repo.

## Flow

1. `POST /api/v5/voice/sessions {glid | seller_id, direction: seller_outbound | buyer_inbound, provider: livekit |
   sarvam_voice_agents | other, room?, run_id?}` → the session object `{id, run_id, context_revision, …}` (a
   preparation run starts; a non-hero `glid` becomes `real-<glid>` with an operator run, waiting up to 15 s). The spec
   doc says `session_id` — the code returns `id`.
2. `GET /api/v5/voice/sessions/{id}/context` → `voice`, `delivery`, `prompt_sections` (identity, goal, seller_facts
   = present contract fields + the first 5 history items, persona, to_confirm, objection_playbook, guardrails), `teleprompter`, `switch_rules`, `cooldown`.
   Put `prompt_sections` into the bot's system prompt; use `delivery` for TTS pace/voice/language.
3. `POST /api/v5/voice/sessions/{id}/events` — one event or `{events: [...]}` (≤ 50): `transcript`, `signal`,
   `switch`, `mood`, `tool`, `outcome`, `ended`. Seller transcripts run `signals.detect`; an explicit language switch
   bumps `context_revision` → the bot re-GETs context at its next turn boundary.
4. The floor shows a REAL CALL tile while events arrive; it leaves after 120 s idle. `GET /voice/sessions/{id}` is
   readable by anyone who can see the seller.

Payal's live browser calls reuse the same `voice_sessions` store and `ingest` (skill `sellergenome-sarvam-voice-agent`).

## Test it without a bot

`python integrations/voice_plugin/harness.py` (with `SELLERGENOME_URL` and the key from the env file) registers a
session, pulls context and posts a short scripted call → browser case 60: a REAL CALL tile appears and its events show
in the workspace. Rules for the bot side: never claim to be human (D10), stop/do-not-call ends the call immediately,
never quote prices, discounts, ROI or guarantees (the guardrail sent to bots), persona changes delivery only.

Doc drift to know: the spec's example `provider_voice: "anushka"` predates the `ritu` default; its pitch note is from
Bulbul v3 times (v4 has no pitch control).
