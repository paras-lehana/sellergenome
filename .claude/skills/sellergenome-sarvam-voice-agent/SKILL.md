---
name: sellergenome-sarvam-voice-agent
description: Build, change, test or debug "Payal" — SellerGenome's seller-outreach voice agent hosted on Sarvam Voice Agents (Samvaad) — its prompt, knowledge bases, API tools that call SellerGenome, listening/VAD and voice settings, draft vs committed versions, live browser calls through Sarvam's conversational SDK, the smoke call and the eval suite. Use whenever the task mentions Payal, Sarvam Voice Agents, Samvaad, the "deploy with code" key, Admin → Sarvam voice agent, /sarvam "Talk to Payal" or "Call Payal", answer times of the Sarvam bot, or rebuilding/committing the agent.
---

# Payal on Sarvam Voice Agents

Payal is the second bot beside the built-in VANI: Sarvam hosts the voice loop; SellerGenome supplies knowledge
(Sarvam KBs) and per-call context (our API, called by her tools). Code: `backend/genome/sarvam_voice.py`;
UI: `/sarvam` (`frontend/cc/voiceagent.js`), Admin → Sarvam voice agent, call workspace with bot = Payal
(`frontend/cc/payal-live.js`). Long-form doc: `docs/architecture/sarvam-voice-agent.md`.

## Hard rules (owner's)

- Only the designated org `01a110b3-f038-7adb-b627-6863a3785c40`, workspace `01a110b3-f03e-79cd-b765-aefbf9b8b01f`,
  app `Payal---Ind-7ae5e937-a82f`. Never touch the workspace's **"Persona-bot"** agent (it belongs to the separate
  `sarvam_voice_agent` MVP outside the worktree).
- Synthetic sellers only (`roster.is_public`). No phone numbers, telephony, deployments, campaigns or test calls to
  real numbers. Deleting any Sarvam resource needs the owner's OK (the orphan KB `indiamart-seller-playbook` is waiting).
- Keys never leave the server and never enter Git or logs: `SARVAM_VOICE_AGENTS_API_KEY` (the "deploy with code"
  `sk_samvaad_…` key, header `X-API-Key`), `SARVAM_VOICE_WORKSPACE_ID`, `SARVAM_VOICE_ORG_ID` in
  `/srv/sellergenome-v4/sellergenome-sarvam-voice.env`; tool key `SARVAM_AGENT_TOOL_KEY` ↔ Sarvam secret
  `sellergenome_tool_key`.
- `private/v4/config/sarvam-voice-{oauth,state}.json` are uid 1000, mode 0600. **Never write them as root** — a root
  script once clobbered the state. Run builds through Admin, the API, or `docker exec` into the container.

## Prompt shape (5.6.0 owner notes; 5.7.0 English intro)

Intro `INTRO`: "Namaste {{seller_name}} ji, this is Payal, IndiaMART's virtual assistant. You deal in {{products}}, right?"
(products = the first two, joined with "and"; the yes doubles as the identity check). Turns of about 15 words, one idea;
within 20 words one real reason (enquiries from {{seller_city}}/the region, or the last meeting from the brief), then a
direct ask for a short meeting. Scenario "Opening gets to the point" checks it. A prompt edit reaches Sarvam only after
**Build** (commits). **Write prompt, intro and nudges in English** — Sarvam generates each language from the English
source (agents playbook "Author in English"); the 5.6.0 Hinglish intro broke that and was reverted in 5.7.0.

**Admin-editable (5.9.2):** the build sends `prompts.text("sarvam.payal.prompt"|"sarvam.payal.intro")` (VANI:
`sarvam.vani.*`), so an Admin → Prompts edit goes to Sarvam on the next Build; text-chat openings change at once. The
same playbook checks run before an edit is saved.

**Real sellers and every workspace agent (5.10.2–5.10.4, owner: production, consent for IndiaMART data):**
- Real sellers may be called by any Sarvam agent, but only signed in and from a prepared run (`run_id` required). Their
  variables come from `run_context(run)`: live company, contact, city, products, demand line, last contact, persona
  title and plan. They never come from `roster.profile` (that is a generated story). `context()` (the on-start tool)
  uses `latest_run()`.
- `workspace_agents()` lists every agent with `committed_version` and `callable`, and `agent_spec(app_id)` reads the
  agent's declared variables, intro and languages. Both are cached for 10 min. A live start with `app_id` sends only
  the declared variables. The URL proxy allows only the session's own `app_id`.
- **Sarvam calls only committed versions** ("App not found for the interaction type"). Drafts show "(draft — not
  callable yet)". The two Manjula agents (Manjula Singh's) were committed with the admin's approval on 2026-10-10.

**Other agents' variable names (5.11.0):** Manjula's agents and Persona-bot read `seller_context`, `persona_summary`,
`tone_directive`, `preferred_language`, `caller_mood`, `seller_state`, `business_category`, `year_established` and
`session_id`. Manjula's state lists them in `agent_variables_in_context`, and her prompt expects the persona generator's
output. `shared_variables(run)` fills them; synthetic sellers use `seller_brief`. `start_overrides(..., keys=)` sends
each agent only what it declares: Payal gets INPUTS. Check a new agent's `agent_variables` first.

## What every agent knows (5.12, `backend/genome/agent_context.py`)

Every prompt shares two blocks, so the agents never drift apart: Payal, Manjula Executive v2, the built-in bot's
`BOT_TURN_SYSTEM` and VANI's "About IndiaMART" paragraph.
- **`INDIAMART_KNOWLEDGE`:** buy leads, enquiries, PNS, Lead Manager, catalogue, TrustSEAL, and the MDC / Star /
  Leading / export / top-up plans. Prices and payments are never taken on the call.
- **`SALES_PLAYBOOK`:** the order is payment first, then a free meeting, then a callback.
  - The meeting ask tries up to three different angles: market numbers, a named competitor, missed buyers, a catalogue
    quick win, and "free, no commitment".
  - Competitors are named but never run down; buyers are named by product only.
  - Products are covered at the overall level; specifications only when the seller asks.
  - The current call reason and the latest records weigh most.

`call_facts(run)` turns a prepared real run into the talking points, and `profile_facts(p)` does the same for a
synthetic seller. The points are `competitors`, `market_snapshot`, `payment_status`, `buyer_activity`,
`product_overview` and `call_reason`.
- **Competitors:** from `market.listing` (preferred), else Global Context `top3mcat_competitors`. The top three are
  named, plus "and N more".
- **Payment:** from STS "Product Pitched - X", the NACH status, the last paid invoice and the hot-lead type.

`sarvam_voice.start_overrides(seller, delivery, run, keys)` sends only the variables an agent declares. Manjula-style
agents use other variable names (`seller_context`, `persona_summary`, `tone_directive`, `caller_mood`, `seller_state`
…). For real sellers, `real_context` fills an empty value from the seller's previous completed runs: gateway
"Server disconnected" failures used to leave the variables blank.

**Manjula Executive v2 (SellerGenome)** (`backend/genome/sarvam_agents.py`, state file `sarvam-manjula-v2-state.json`):
- Built with `POST /api/v5/admin/sarvam-agents/manjula-v2/build`.
- Copies the language and TTS voice of the PM's "Manjula Excutive". Her agent is read, never changed.
- Binds Payal's knowledge bases and gets the tools `query_knowledge_base` and `end_interaction`.
- Its prompt and intro are Admin prompts `sarvam.manjula_v2.prompt` / `.intro`.
- The default agent is Admin `sarvam_voice.default_agent`: `builtin` in the YAML, and the v2 app id in production. A
  browser's own pick overrides it.
- Sarvam serves only committed versions: an uncommitted agent answers "App not found for the interaction type".

## Sign-in and the agent list (5.13.7 incident)

The Voice MCP OAuth (`VoiceAuth`, `sarvam-voice-oauth.json`) lists the workspace agents and fetches their call specs.
Live calls use the runtime key instead.
- **Refresh tokens rotate.** `token()` refreshes under the process-wide `_REFRESH_LOCK` and re-reads the state inside
  it. Never refresh outside the lock or from a second process: a reused refresh token revokes the whole sign-in
  ("refresh token does not exist").
- **Never run `VoiceAuth()` in a throwaway container** with the state mounted read-only either. It cannot save the
  rotated token, and that leaves a dead one behind.
- **Saved copy:** `workspace_agents()` saves the last good list and `agent_spec()` saves each spec to
  `sarvam-workspace-agents.json`. On failure both serve the saved copy (`stale: true`), and `/api/v5/debug/voice`
  says "Showing the last known agents".
- **Fix (the owner signs in; about a minute):**
  1. Run `scripts/signin_listener.py` on the rig, in the background.
  2. The owner keeps port 53682 forwarded in VS Code (Ports panel).
  3. Get the link with Admin → Sarvam voice agent → *Get sign-in link*.
  4. The owner signs in. Sarvam now shows "Sign in to approve" with a code: approve it in the window that opens.
  5. The browser lands on the listener, which completes the sign-in inside the container and shows "connected".
  - Without the listener, the 127.0.0.1 page hangs if VS Code still forwards the port. With no forward it fails to
    load. Either way, the address in the bar can be pasted into Admin → *Connect*.
  - The code expires within minutes. The same link can be reopened as long as nobody presses *Get sign-in link* again.

## The second agent: VANI buyer requirement (5.7.0, `backend/genome/sarvam_buyer.py`)

Agent "VANI · IndiaMART buyer requirement (SellerGenome)", own state file `sarvam-buyer-state.json` (uid 1000), same
OAuth, workspace, tool key and build lock as Payal; `_ensure_agent` refuses to build over Payal. Build: Admin → Sarvam
voice agent → *Build / update VANI on Sarvam* (`POST /api/v5/admin/sarvam-buyer/build`). No knowledge base (the call is
its only source). Inputs: `buyer_call_id`, `product_name`, `seller_name`, `specification_options`,
`quantity_unit_options`; after-call outputs: `buyer_disposition`, `call_outcome` (owner's outcomes), `confirmed_product`,
`quantity`, `quantity_unit`, `specifications`, `additional_details`, `call_summary`. Tools:
`load_buyer_context` (on start, `/api/v5/sarvam-buyer-agent/context`), `requested_another_product_tool` (run,
`…/product`, saves the new options), `report_requirement` (on end, `…/outcome`, test lead on the buyer call). Male voice
(the owner's lines are masculine); Hindi/English/Telugu; 4 eval scenarios. `PROMPT` is the owner's VANI prompt
restructured to the playbook — `CHANGES` lists every deviation and why (shown in Admin). Runtime: text chat
`POST /api/v5/sarvam-buyer/chat` (send_chat + selection), live voice via `POST /api/v5/sarvam-live/sessions
{agent:"buyer", buyer_call_id}` (session records `live.agent/app_id`; the URL proxy checks the buyer app), REAL CALL tile
on the buyer desk (`voice.live_tiles("buyer_inbound")`), headless smoke `POST /api/v5/admin/sarvam-buyer/smoke`.
Synthetic buyer-desk calls only. Tests: `tests/test_v57_buyer_agent.py` (playbook shape is asserted).

## Build (idempotent) — Admin → Sarvam voice agent → Build, or `POST /api/v5/admin/sarvam-voice/build[?rotate_key=true]`

`SarvamVoice.build` (via the Voice MCP, skill `sellergenome-sarvam-mcp`): tools/list → KBs `indiamart-playbook`
(packages without prices, FAQ, objections) and `sellergenome-seller-briefs` (14 hero briefs; upload begin → PUT →
commit; `duplicate` = unchanged) → agent → secret → configure (9 input variables, 5 post-call outputs, Hindi-first
language list, intro line, Bulbul v4 voice, KB binding top_k 4) → API tools (`load_seller_context` on start,
`book_meeting`, `report_outcome` on end; static header `X-SellerGenome-Key`; base URL always the lehana mirror) →
prompt + tool names in **one** `configure_agent` write → nudges/listening (quiet) → voicemail → **commit** → record the
highest committed version → test suite with 3 scenarios.

Her tools call `/api/v5/sarvam-agent/{context,meetings,outcome}` (tool key, 120/min); `context` serves synthetic
sellers only and falls back to `syn-91000101`.

## Versions — experiment on the draft

Callers always get the latest **committed** version. Try settings on the **draft** (`?version=N` on the runtime URL;
`LAB_VERSION` in the voice lab). **Build re-applies the Admin `sarvam_voice.*` listening settings and voice every
time**, so put the winning values into Admin → Sarvam voice agent first, then Build (which commits). The commit reply returns the *next draft* number — read
the committed one from `agents get` with versions (a 5.3 doc once labelled the wrong version). Current: v5 committed.

## Listening and voice (Admin settings `sarvam_voice.*`, decision D31)

`hybrid-vad`, **3 silence frames** (1.33 s median answer vs 1.42 s at 6, no talk-over), interrupt during playback on,
hotwords (IndiaMART, Payal, BuyLead, TrustSEAL, Lead Manager, GST, catalogue, enquiry, meeting, executive), pace 1.15,
voice empty = auto "Ishita – Hinglish Support Agent" (Bulbul v4). Per-call VAD/pace/ASR overrides in
`interaction_start` are accepted but **ignored** — tune at agent level.

## Live browser calls (decision D27)

1. `POST /api/v5/sarvam-live/sessions` — synthetic seller only (403 for `real-`), 6/hour per visitor (operators
   unlimited), one budget call;
   returns session id + token, org/workspace/app, start overrides (variables, initial language, hotwords, pace
   0.8–1.3), opening line, 300 s cap. The response never contains the key (browser case 79).
2. The vendored SDK (`frontend/cc/vendor/sarvam-conv-ai-sdk.js`, rebuilt only with `scripts/build_sarvam_sdk.sh`,
   pinned 0.0.42, console logging stripped because it prints the signed URL) uses `baseUrl
   /api/v5/sarvam-live/s/{sid}/`; our proxy pins org/ws/app and signs the runtime URL server-side (≤3 per session).
3. `CspSafeAudio` loads the worklet from `/static/cc/audio-worklets.js` (the SDK's `blob:` worklet breaks the CSP).
   Payal's speech is detected by RMS > 900 (Sarvam streams comfort noise at 150–430) → "Payal answered in N s".
4. Events post to `/sarvam-live/s/{sid}/events` → a REAL CALL tile on the floor. Every `/sarvam-live/s/{sid}/…` call
   needs the `X-Live-Token` header; a session expires at max seconds + 60 s.

Text chat (`/sarvam` "Talk to Payal") uses MCP `send_chat` (`POST /api/v5/sarvam-voice/chat`, 12 turns/min for
visitors, 40 for operators — hard-coded; the `sarvam_voice.public_chat_turns_per_minute` setting is not read): Payal is
a v2v app, so the runtime refuses `interaction_type=chat`. The workspace id comes from `sarvam_voice.workspace_id`
first, then the env; the org is the code constant (the `SARVAM_VOICE_ORG_ID` env var is not read).

## Test

- Admin → *Test the Voice API key* (asks for the workspace id if missing — a wrong workspace says "Invalid API key format").
- Admin → *Run a smoke call*: headless Python-SDK call (1 operator budget unit, 110 s cap), reports answer times (≈1.3–1.7 s).
- Admin → eval suite (`run_eval`) costs Sarvam minutes — ask before running.
- Browser cases 70, 71, 75, 76, 79, 82 in AGENTS.md; `/sarvam` at 390 px (case 72).
