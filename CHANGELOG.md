# Changelog

## 5.13.7 — 2026-10-10 · Workspace agents never disappear because of Sarvam's sign-in

- **What happened:** Manjula, Manjula Excutive, Payal 6.0 and the other workspace agents vanished from the agent picker
  (owner: "those other agents are again lost").
  - The Voice MCP sign-in was revoked: Sarvam answered "refresh token does not exist".
  - Sarvam rotates refresh tokens. After tonight's restarts, two requests (the agent list and the debug panel)
    refreshed with the same token at once, and the reuse revoked the sign-in.
  - Every agent other than Payal also needs its call spec from that sign-in, so those agents could not start either.
- **One refresh at a time:** a process-wide lock, and the state is re-read under it. A failed refresh is recorded and
  the message says how to reconnect (Admin → Sarvam voice agent → Get sign-in link → Connect).
- **The last good agent list and every agent's call spec are kept on disk** (`sarvam-workspace-agents.json`).
  - When the sign-in fails, the picker still lists every agent, and any of them can start: calls use the runtime key,
    not the sign-in.
  - The debug panel says it is showing the last known agents.
- The sign-in itself must be renewed once by the owner (Sarvam account login).

## 5.13.6 — 2026-10-10 · City-wise seller count labelled correctly

- With the city filter on, the listing's seller total is the city search's own total (3,142 for one Mumbai category
  against 27,522 across India). The talking point now says "the Mumbai search lists N sellers" and keeps "across India"
  for all-India answers.

## 5.13.5 — 2026-10-10 · Competitors from IndiaMART search, city-wise

- **The competitor listing is live** (the gateway now forwards `/wservce/products/listing/`).
  - The city filter is `cityflname` (lower case, hyphens: `navi-mumbai`). The endpoint ignored `city`, `cityname`
    and `city_name`, and answered with all-India rows (`cityid` 0).
  - When the listing does not know the city it answers an error, and the run then asks again for all of India.
- **The parser reads the confirmed answer:** `data[]` rows with the company, GLID, city, rating, years on
  IndiaMART, TrustSEAL, paid listing and price shown. Phones, GST numbers, addresses and emails are never kept.
- **Agents get richer talking points.**
  - Competitors: the first three sellers in the seller's city, each with city, TrustSEAL, rating and years on
    IndiaMART, plus "and N more in the first 28 search results".
  - Market (weighted first): whether the first results are all paid, TrustSEAL sellers with prices, and whether this
    seller appears among them (or at which position), plus how many sellers list the category across India.

## 5.13.4 — 2026-10-10 · Clean payment line for demo sellers

- The talking points for a synthetic seller joined bullet lines as "…in August.; - On 12 August…". The persona card and
  the voice agent's payment status both showed this. Bullet lines are now one clause list.

## 5.13.3 — 2026-10-10 · Submission and the public repository for judges

- The hackathon submission is filled in `docs/submission.md` (owner, point 9): pitch, approach, repository and impact.
  The timing uses the measured values from 103 live runs: first facts at a median of 0.7 s, the full persona at a
  median of about 8 s, and about 1 s when a recent persona is reused.
- `export_public_repo.py` (skill `sellergenome-demo-judging`) builds the judges' copy.
  - Copied: the code, tests and every skill.
  - Left out: the internal docs, which the public `.gitignore` also hides.
  - Replaced: the README with a judge-facing one, real GLIDs with placeholders and private paths with neutral ones.
  - Checks: the copy is compared against the live keys, the gateway address and every approved GLID before anything
    is committed.
  - Verified: the copy passes all 376 tests and boots with no keys.
- The prompt-catalogue test skips the simulated-seller training prompts when their internal source file is absent, as
  it is in the public copy.
- Workspace: the architecture's stage labels wrap to two lines, and the agent name drops the "(SellerGenome)" suffix
  in the call header.

## 5.13.2 — 2026-10-10 · Live log and architecture strip fit

- A call entry in the live log grew to the full height of the pane. Its class `call` collided with the classic
  workspace's full-height `.call` layout. The log's classes are now `k-…`.
- The architecture strip's stages share the width (the API stage gets the most), so Voice agent and Outcome are no
  longer cut off at 1440 px.

## 5.13.1 — 2026-10-10 · Workspace polish

- Manjula Executive v2 opened a synthetic seller with "You deal in , right?": synthetic profiles have no category, so
  the opening's category now falls back to the first product or the product line, for every agent.
- The architecture's Trigger, Elasticsearch, agent and outcome boxes open the events behind them in the side bar.
  Before, they asked for an API receipt that does not exist (404).

## 5.13.0 — 2026-10-10 · The call workspace: persona left, call middle, live log right, architecture below

- **Talk as Seller opens a three-pane workspace on one screen** (owner, point 8). Barge in still uses the full
  workspace (`?view=full`).
  - **Left, full height: the persona.** It shows *What the agent knows* (reason to call, payment, market, competitors,
    buyers, products), Basic details, Engagement with IndiaMART and Engagement with buyers. Each card shows its top
    items, with *Show more*. Every value and chip opens the side bar with the API response, the value highlighted, as
    on the persona page. *Book a meeting* and *Debug* are collapsed at the bottom.
  - **Middle: the call.** It shows the agent dropdown, Start/End, the state, the current delivery (language, pace,
    warmth, formality, or the Sarvam agent's tone) and the persona-switch banner. The transcript scrolls by itself.
    Still one agent per call, and a change restarts it.
  - **Right: a live log of everything being done.** This covers each IndiaMART API with its source and time, Elastic
    cache hits, raw responses written to Elasticsearch, the persona documents indexed, and Sarvam's persona generator,
    analyst and locality calls. It also covers the call: the agent started (with the variables sent), persona and
    language switches, tone changes, reply times, barge-ins, the meeting booked and the call ended. Call events are
    recorded on the run and queued to Elasticsearch's events index, and the write is logged too. Each new entry flashes
    green and scrolls into view. Clicking an entry opens the side bar: the API's request and response, or the agent's
    start variables, or the write. Clicking outside the side bar closes it.
  - **Bottom, 30 % of the screen across the call and log: the architecture, horizontal.** Trigger → IndiaMART APIs (in
    parallel) → Elasticsearch (cache, writes, persona index) → Persona on Sarvam → Voice agent → Outcome. Running boxes
    breathe orange, finished ones are green and failed ones red; each box opens its request and response.
- **A wider loading bar with the APIs** (owner, point 10). The running bar is twice as thick, and under it every API
  being loaded shows as a chip: breathing orange while loading, green when loaded, red on failure. This applies on
  Talk as Seller and on the persona page.
- New `POST /api/v5/runs/{id}/call-events`. The brief carries `talking_points`, the same facts the agents get.

## 5.12.1 — 2026-10-10 · Competitor API, and no empty agent variables after a gateway hiccup

- **Competitor API** (owner): `/wservce/products/listing/` with `mcatid`, the seller's main category from the company
  API (`DATA.BREADCRUMB.MCAT_ID`, else the most common product category), and `city`, lower case and hyphenated. It is
  called right after the company details for real sellers, and its sellers become the agents' `competitors`; Global
  Context's competitor list remains the fallback. It is part of Admin → API health. **The gateway does not forward this
  path yet** ("No API configured for this path"), so the route must be added there.
- **No empty agent variables after a gateway hiccup.** A live run lost the company, enquiries and leads calls ("Server
  disconnected without sending a response"), and Manjula v2 opened with "Namaste Sir ji … You deal in , right?". Now:
  - the executor retries a dropped connection once;
  - an agent variable missing from this run is taken from the seller's last good run;
  - the opening's category falls back to the product line.

## 5.12.0 — 2026-10-10 · Agents that know IndiaMART and the seller: payment first, competitors, buyers, Manjula Executive v2

- **IndiaMART knowledge in every agent** (owner, from the reference agent in docs/refenrenceCode/livekit_agent_sarvam):
  buy leads, enquiries, PNS, Lead Manager, catalogue quality, TrustSEAL, the plans (MDC, Star or Leading Supplier,
  export, buy-lead top-up), why paid sellers rank first in a city, and that prices and payments are never taken on the
  call. It is shared by the built-in Payal, Payal on Sarvam, Manjula Executive v2 and (buyer side) VANI.
- **A sales playbook** (owner's points 3–6):
  - Payment first when there is payment activity: ask what got stuck, explain the plan's value, and have the executive
    finish it with a secure link.
  - Then the meeting, without giving up on the first hesitation: up to three different angles (market, a competitor by
    name, buyers or demand being missed, a catalogue quick win, the meeting is free), then a callback.
  - Competitors by name, factually.
  - Buyers who reached out.
  - Products at the product-line level, with trade expertise.
- **Talking points from live data** (`agent_context.call_facts`), sent to every agent as call variables:
  - Competitors: Global Context `top3mcat_competitors`, with names, plans and member-since; three named and the rest
    counted.
  - The market in the seller's top categories: paid sellers and buy leads in the city.
  - Payment status: the plan pitched in the last meeting (STS "Product Pitched"), payment notes, the auto-debit (NACH)
    mandate, the last paid invoice, the hot-lead type.
  - Buyer activity: the persona's buyer column, the last buy lead looked at, the last enquiry's product (no buyer
    name).
  - The product line, and the call plan's reason.
  - For the owner's test seller this means the agents now know three things: the plan pitched in the last meeting and
    its stopped auto-debit, how long the seller has been free, and which paid sellers are ahead in the city.
- **Manjula Executive v2**, a new Sarvam agent built by SellerGenome (Admin → Prompts → Build). It has the PM's
  Manjula Excutive voice and language settings and the new prompt; her agent is untouched.
  `sarvam_voice.default_agent` (Admin) picks the agent Talk as Seller starts with when a browser has no choice of its
  own.

## 5.11.3 — 2026-10-10 · Basic details from Global Context; PIM default; no empty fields or "Live API" tags

- **Turnover comes from the Global Context answer** (`kycdetails.gst_turnover`, from generateContextUID or getContext,
  whichever holds it). The company page and the supplied file only fill in when Global Context has none. "NA" counts as
  empty, so the row is left out rather than showing "NA".
- **On IndiaMART** now reads Global Context `registered_since_with_indiamart` ("1 Y 4 M" → "1 year 4 months") instead of
  the insights API's mock, so real sellers get it too. **Business type** falls back to Global Context `company_type`.
- **Hot lead type defaults to PIM** (Payment Interested) when the record gives none or a blank. Real sellers' Global
  Context carries no `data_type`, so it was always empty. The row says "(default)"; the persona generator sees PIM too.
- **Fields with no data are left out:** no "Not in file" in Basic details, products, buyer stats, catalogue, the record
  drawer or the persona page attributes, and no "Data gaps" group.
- **No origin tags on data:** the persona page, the brief and the call pane no longer show "Live API", "Live data" or
  "Dataset" next to values. A small ↗ / *source* link still opens the exact response. A demo seller's mock data is still
  labelled *Mock*. Operator views (inspector, architecture graph, Admin, API health) keep their origin tags.
- The call pane's Global context hint names generateContextUID (not userCtx). Admin help texts follow GLID access
  (dev: approved test GLIDs; prod: any GLID).

## 5.11.2 — 2026-10-10 · Talk page polish

- Switching the agent while the built-in one was answering left a "Cannot read properties of null" error. A reply that
  lands after End or an agent change is now dropped, because it belongs to a call that has ended.
- The agent's name and id appear once, in the picker, and are no longer repeated next to the call state.

## 5.11.1 — 2026-10-10 · Changing the agent mid-call restarts it

- Every page used to mount into the same container, so listeners a page added there outlived it. On Talk as Seller,
  the seller floor's leftover agent-picker handler saved the new agent first; the page's own handler then saw the agent
  as already chosen and did not restart the call (live QA: switching to Manjula mid-call opened no session). The router
  now gives each page a fresh container, which ends the whole class of double handling.
- The built-in agent's opening no longer holds the call in "starting": End or an agent change acts at once.

## 5.11.0 — 2026-10-10 · A simple Talk as Seller page: one agent, one call

- **One call card on top** (owner: "just give simple calling interface where we choose agent and it is the only
  agent … the ui is soooo cluttered"). Talk as Seller and the persona page's *Start call* open a focused page: the
  seller's name, the Agent dropdown (built-in or any Sarvam workspace agent, with its id), one *Start call / End call*
  button, the call state and the live transcript.
  - Persona (basics, IndiaMART engagement, buyers), the conversation plan, *Book a meeting* and *Debug* sit below,
    collapsed by default.
  - A link opens the full workspace (architecture, trace, engines). Barge in still uses the full workspace.
- **Exactly one agent per call.** The page used to start the built-in agent's opening line by itself, so choosing
  Manjula meant two voices at once (echo); a second click while connecting could also open a second session. Now the
  chosen agent is the only one that starts. Opening Talk as Seller starts the call with it, and changing the agent
  ends the call and restarts it with the new one. The built-in agent keeps the microphone open (speak, pause, it
  answers; speaking over it stops it); typing works too.
- **Manjula and other agents get the seller's context.** Manjula's and Persona-bot's prompts read their own variables
  (`seller_context`, `persona_summary`, `tone_directive`, `preferred_language`, `caller_mood`, `seller_state`,
  `business_category`, `year_established`, `session_id`), and only four of them were filled, so the rest kept Manjula's
  test values (a sample seller's name and company). Now every agent gets them from the seller's run:
  - `seller_context` is the persona generator's output as text: basics, IndiaMART insights, buyers, the call plan and
    the dated record.
  - `persona_summary`, the tone from the persona dials, the language, and the mood from the archetype.

  Each agent still receives only the variables it declares.

## 5.10.6 — 2026-10-10 · Shorter generator reply: the record is filled by code

- With generateContextUID live, 12345678 has 15 records and 40 facts. The model copied them all back into `record`,
  then broke the JSON right after it (`"counts" , "gaps" , "guardrails" ]`), and the persona fell back to rules. It is
  not a length problem: the reply stopped normally at about 3.5k tokens.
- The model now returns `record` empty, and code fills it from the input's records (same ids, dates, types and
  outcomes, with the prompt's tone rule). Replies are shorter and faster, and the place where the JSON broke is gone.
- The retry after an unreadable reply now stays at 7,000 tokens: the gateway refused 10,500.

## 5.10.5 — 2026-10-10 · Long personas no longer cut off

- With 7 to 12 insights, a seller with a lot of history (23456789: 13 records, 62 facts) sometimes got a reply cut
  off at the token limit. The JSON did not parse, and the persona fell back to rules with no IndiaMART insights. The
  generator now has 7,000 tokens of room (was 4,000) and retries once with 10,500 when a reply is cut off. The log
  says why a reply did not parse ("truncated at max_tokens").

## 5.10.4 — 2026-10-10 · Draft-only agents say so

- Sarvam starts a live call only on a committed version. Calling Manjula Excutive answered "App not found for the
  interaction type": both Manjula agents were drafts. With the admin's approval they were committed on Sarvam (v1;
  Sarvam opened draft v2 for further edits), and they are callable now.
- The agent list now checks each agent's versions. An agent that only has a draft is marked "(draft — not callable
  yet)", and the call button says to commit it in Sarvam first, instead of failing with an error.

## 5.10.3 — 2026-10-10 · Real-seller Sarvam calls: text-shaped fields

- The first live real-seller call to a Sarvam agent failed (HTTP 500): a real run carries the seller's trigger as text,
  and the variable builder expected an object. Every lookup now accepts either shape.

## 5.10.2 — 2026-10-10 · Real sellers on the Sarvam agents, every workspace agent, fuller Engagement with IndiaMART

- **Real sellers go to the Sarvam agents** (owner: "send everything to sarvam … NO RESTRICTIONS … we have all consent").
  Payal on Sarvam, or any other agent, can call a real seller. The agent's variables come from the seller's prepared
  run: live company name, contact, city, products, demand line, last contact, the persona title, and the plan's trigger
  and opening. Nothing is generated for a real seller. A real seller needs signing in and a prepared run (*Talk as
  Seller*); text chat to Payal and its on-start tool use the latest run too.
- **Every agent in the Sarvam workspace** (owner: "there are many other like manjula executive, i want all of them").
  The Agent dropdown on the seller floor, the call header, the buyer desk and buyer calls now lists the built-in
  pipeline and all six workspace agents: Payal, Payal 6.0 preview, Manjula Excutive, Manjula, VANI and Persona-bot.
  - Each shows its name, app id, version and status.
  - Choosing one and pressing *Call … (live voice)* calls that agent. It gets only the variables it declares, in a
    language it supports, and its own opening line.
  - The server opens only the agent the session was started for, within SellerGenome's workspace.
  - The Debug panel lists every workspace agent and the chosen one.
- **Engagement with IndiaMART** (owner: "have more insights, also clicking it shows full insight").
  - The persona prompt asks for 7 to 12 insights, one point each (it was 5 to 9); the check follows.
  - Clicking an insight opens it in place: the full sentence, its group and confidence, the note, the facts it rests
    on, and the full text of every record it cites (date, type, outcome).
- userCtx stays replaced by generateContextUID (Abhinav, 5.10.1): the old userCtx endpoint is no longer called.

## 5.10.1 — 2026-10-10 · Global Context via generateContextUID; Redash through the gateway; agent switch before the call starts

- **Global Context (`context.global`) now calls `generateContextUID`** (POST `/go/api/globalcontext/v1/generateContextUID`,
  `{AK, empid, mapping_type: 1, mapping_value: <GLID>, source_id: 1, data_segments: "kycdetails"}`, employee AK) through the
  gateway. It replaces `userCtx`, which answered 404 on all 82 live calls. The owner replaced it at the gateway too
  (route `generate-context-uid`).
- The context UID it returns is passed to getContext as `mapping_id`. The response shape is not confirmed yet, so the UID
  is looked for under the likely keys (`mapping_id`, `context_uid`, `contextUID`, `uid`, at the top level or under `data`).
- Global Context fields, the persona input and the brief use generateContextUID's answer when it carries the data segments.
  Otherwise they use getContext's answer, which has the same shape. Every field and evidence row names the API that
  actually answered.
- **Redash goes through the gateway** (routes `redash-query-results` and `redash-jobs`), so every IndiaMART API now uses the
  one ngrok gateway. The endpoint policy accepts the configured gateway origin for Redash only, over HTTPS only and on the
  same fixed paths. The approved-GLID check is unchanged.
- The architecture graph names the node "Global ctx · context UID". Tests: 346 → 364.
- On iPhone-size Safari (WebKit QA), switching the agent before the call had started threw "call.say_next" on an empty
  call. The call panel now redraws only once the call exists; the header and the Debug panel update straight away.

## 5.10.0 — 2026-10-10 · Running bar while things load; choose the agent on screen

- **Running bar at the top** (owner: "make the part where talk to seller or open persona (same for buyer) is being
  loaded … look like its loading"). A thin bar runs across the top, with a short label, from the click on *Talk as
  Seller*, *Open persona*, *Barge in* or *Talk as Buyer* until the run has prepared, the persona page is ready or the
  buyer call has connected. Under reduced motion it pulses instead.
- **Agent picker on screen** (owner: "choose the agent from the screen itself … during talk to seller, or on the main
  screen, once selected you show there the agent id, name"):
  - **Seller floor and the call header:** *Payal · built-in* (the SellerGenome pipeline) or *Payal on Sarvam*.
  - **Buyer desk and buyer calls:** *VANI · built-in* or *VANI on Sarvam*.
  - **What it shows:** the chosen agent's name and id, the Sarvam app id and version, or the built-in pipeline's LLM,
    speech and listening models.
  - **Switching mid-call:** the live voice stops, and if you were talking, the other agent's voice starts right away.
  - **Real sellers:** they stay on the built-in agent, as before; the Sarvam option says why.
  - **Shared choice:** the floor and the call page use one choice; the buyer desk and buyer calls use another.

## 5.9.9 — 2026-10-10 · No invented figures in a persona

- For GLID 12345678 the model wrote "Active on 28 of the last 28 days", but no such field was in the input; it copied
  the prompt's style example. The check "Every number comes from the input" now counts as critical, so the repair
  round runs. Any insight that still carries a figure the input does not have is dropped before it is shown
  ("Corrected by code" lists it).
- Dates in the input ("2026-10-08") match days in the text ("8 Oct"), so real dates are never taken for invented
  numbers.

## 5.9.8 — 2026-10-10 · Call history from the executives' STS entries reaches the persona

- **GLID 12345678 showed no call history** although STS returned five dated entries. Executives also write through
  NSDMERP and log click-to-call through MERPC2C, but only "WebERP" was known as a person's tool. So the meeting, the
  calls and the follow-up were sent to the Persona Generator as *system notices*, and the prompt rightly sets those
  aside.
  - Every ERP desk tool now counts as a person's entry, unless the updater or the channel is a process (penny-drop
    consumers stay automated).
  - "Sales Appointment …" becomes a **Meeting**.
  - A click-to-call log becomes an **Executive call**, answered or not answered, with its length.
  - "TBRO / follow up / call back" becomes a **follow-up note**. "…call back later, payment follow up" was being
    swallowed into "Online payment attempted".
  - Dates written "13-Oct-2026" are read, so a TBRO due within 3 days opens the *follow_up_due* call plan, as the prompt
    says.
- **Privacy:** executive names, "Name - 112259" employee ids and masked phone numbers are removed from STS text before
  it reaches the model (the prompt's "never include" list).

## 5.9.7 — 2026-10-10 · Two-way talk fix and a Debug panel on the call page

- **Talk live heard nothing on the owner's browser** (mic allowed; the listening connection opened, then 0 transcripts
  in 12 s). The same page with a fake microphone worked end to end, so the server and Sarvam were fine. The page made
  its audio engine (AudioContext) only after the ticket request and the permission prompt, so the click no longer
  counted, the engine could stay paused, and no audio frames left the page. It is now created and resumed first.
- **Microphone check:** 3 s after listening starts, the page checks whether audio really flows and says why if not: the
  audio engine is paused, the mic is muted or taken by another app, or the mic sends silence (it names the device).
  The numbers go to the server log too: frames, level peak, engine state, sample rate, browser.
- **🛠 Debug on the call page** (owner: "which agent id is being used, which account on sarvam, its name"). One panel
  shows:
  - this call's LLM, speech and listening models and end-of-turn timing;
  - live microphone numbers;
  - Payal and VANI on Sarvam: agent name, agent id, version, voice, build time, live availability;
  - the Sarvam account the agents are managed with: login email, organisation, workspace, MCP sign-in expiry, and key
    fingerprints. This part is for signed-in users only, and the token is never sent.

## 5.9.6 — 2026-10-10 · GLID access: dev or prod

- **Admin → APIs → Gateway → GLID access** (also Admin search "glid access"; setting `upstream.glid_access`, D51):
  - **dev** (default) keeps the original rule: live IndiaMART APIs only for the approved test GLIDs.
  - **prod** lets any numeric GLID a signed-in user enters reach the live APIs, so clients can test their own sellers.
    Search shows the real seller instead of a generated one, LMS reads chats with the seller's own buyers (the two
    test contacts are skipped) and Redash answers for that GLID too.
- Sign-in is still required in both modes: signed out, a GLID shows the locked "Real IndiaMART seller — sign in" result
  and never triggers a live call. Only `dev` or `prod` can be saved (anything else is refused with the reason).
- The call page's gateway line says which mode is on; `/api/v5/architecture` reports it.

## 5.9.5 — 2026-10-10 · API health readable on phones

- At 390 px the health table's five columns squeezed key names into one letter per line. On phones each API is now a
  small card: name with result and time on the right, key and note full width below.

## 5.9.4 — 2026-10-10 · API health summary counts errors, not skips

- Only the five buyer-call tools get the "no live endpoint" skip. The insights API and the package catalogue keep
  their own result (the catalogue's mock is not about the seller, so it answers; insights stays skipped with its
  reason).
- The card and toast now say "N OK · N errors · N skipped", and turn amber only when something actually failed.

## 5.9.3 — 2026-10-10 · API health without false alarms

- The first live run of *Test every API* (real GLID, new key) showed 11/19 OK, and 7 of the 8 failures were false
  alarms: the five buyer-call tools have no live endpoint (mock only), and buy-lead details needs an offer id from a
  purchased lead. Both are now *Skipped* with the reason. The remaining real failure is userCtx (Global Context), which
  answers 404 at the gateway.

## 5.9.2 — 2026-10-10 · Every prompt in Admin, and a health test of every API after a key change

- **Admin → Prompts:** all 29 prompts SellerGenome sends to a model are listed and editable in one place. Each one says
  where it is used, which model call carries it, its source and when an edit takes effect. The groups are the built-in
  Payal seller call (with its four live-adaptation lines), the simulated seller (its template, seven training prompts
  and four behaviour notes), the persona pipeline (Generator v0.3, analyst, locality), built-in VANI (requirement,
  compact, original, rewrite) and the Payal and VANI agents on Sarvam (prompt and opening line, with a *Build on Sarvam*
  button).
  - Edits are checked before they are saved: placeholders, literal JSON braces, `FACTORS_HERE`, Sarvam's seven sections,
    the space after tool names, known `{{variables}}` and length limits. *Reset to default* brings back the version in
    code, and the last 10 edits are kept.
  - Admin search finds prompts too, by title, usage or text.
  - The default texts are unchanged; the refactored seller-bot, simulated-seller and rewrite prompts render
    byte-for-byte as before.
- **Admin → APIs → API health:** *Test every API* calls each live IndiaMART API once for one seller (default the
  owner's real GLID), using the keys saved now. It shows the result, HTTP code, time, the key used with its expiry, and
  the upstream note. It also runs by itself after a key or *One key for every API* is saved.

## 5.9.1 — 2026-10-10 · Persona switches heard in Devanagari loanwords

- On a live Payal call, Sarvam transcribed "time nahi hai" as "टाइम नहीं है मेरे पास", which the signal lexicon did not
  know, so no persona switch showed. The rush, interest and confusion cues now include the Devanagari spellings of
  English loanwords (टाइम नहीं, बिज़ी, एक मिनट, मीटिंग रख, क्लियर नहीं …). The same cues serve VANI calls.

## 5.9.0 — 2026-10-10 · The owner's Payal notes, completed on the Sarvam agent via MCP

- **Meeting or demand reference:** two new per-call variables for Payal on Sarvam, filled by `load_seller_context` and
  sent at the start of browser calls:
  - `demand_line`, e.g. "12 buyer enquiries for … in the last 30 days";
  - `last_contact`, e.g. "meeting with an IndiaMART executive on 19 August".

  Her second line now gives that reason within 20 words, then asks directly for the meeting. The build updates the
  existing on-start tool's mappings in place.
- **Persona switch on Payal's calls:** Payal's prompt now has explicit adaptation rules (rushed → one short line and
  the meeting ask; confused → one idea in simple words; annoyed → acknowledge, no pitch; interested → straight to the
  meeting). On a live Payal call, SellerGenome reads the seller's words and shows a *Persona switch* banner and toast
  with what was heard and how Payal adapts.
- Kept from 5.6–5.7: the product-first English intro (Sarvam writes the Hindi), turns of about 15 words, and the
  direct meeting ask.

## 5.8.9 — 2026-10-10 · The company API needs its own token

- With the new employee key (AK) pasted into COMPANY_TOKEN too, the company API answered
  `{"Status": "Forbidden", "URL_STATUS": "403"}` inside HTTP 200, and the run treated it as success, so the identity fell
  back to "Private seller". That answer is now a failure, and COMPANY_TOKEN was restored to its environment value.
- "One key for every API" now ticks only the slots that take the AK (IM_EMPLOYEE_AK, USERCTX_API_KEY) by default, and
  says why COMPANY_TOKEN and LEADS_TOKEN are separate. Each key saved in Admin gets *Use environment value*.

## 5.8.8 — 2026-10-10 · Key expiry in plain sight; one key for every API

- Admin shows a banner on every section when an API key has expired or expires within 2 hours ("IM_EMPLOYEE_AK expired
  2 h 10 min ago · 6 APIs"), with *Paste a new key →*. The Keys card opens by itself and shows a live countdown ("3 h 20 min
  left" / "expired … ago") plus the expiry time in IST.
- **One key for every API:** paste a key once and save it into every ticked IndiaMART key slot. All slots are ticked
  by default, and the Sarvam and plug-in keys are never touched. `POST /api/v5/admin/secrets/bulk` (operator only).

## 5.8.7 — 2026-10-10 · Language row from the API's state

- The Basic details "Language" row was the persona model's guess (SARVAM chip). No IndiaMART API carries a language, so
  it is now read by rule from the state the company API returned, with that API as its source and the note "from the
  state — confirm on the call". Basic details now show only LIVE API and DATASET sources.

## 5.8.6 — 2026-10-10 · "Real seller", never "private seller"; nature of business from the right column

- Every label that said "private seller" (search chip, floor tile, call header, sign-in notes) now says "real seller".
- "Nature of business" read the supplied dataset's business-type column (so it said "Proprietorship"); it now reads the
  live company record's BIZ, then the dataset's nature-of-business column ("Trader - Retailer").

## 5.8.5 — 2026-10-10 · Facts from the APIs, not the model; Elastic cache reads off

- Owner: "data should be from APIs primarily". In the persona brief and on the persona page, Basic details (seller,
  company, customer type, nature of business, city, state, turnover, business type, hot-lead type), products and the
  buyer counts are now read straight from the API fields. Each chip names its API and origin (LIVE, DATASET …).
  - Customer type comes from Global Context, else the live company record's weight (1199 → Free).
  - The persona model is kept for what needs judgement: the inferred language (labelled), insights, dials and the plan.
- Admin setting `elastic.cache_reads` is off (revision 4): every run calls the APIs; Elastic still stores responses.

## 5.8.4 — 2026-10-10 · Quieter seller floor (owner)

- Removed the *Prep runs today*, *Median prep time* and *ES cache hit rate* cards (the three remaining cards share the row).
- Removed the explanatory note and confirmation time from the real-seller (live identity) tile.
- Removed the *Simulated* chips from the call tiles, the buyer tiles and Floor events; the page banner still says the
  floor is simulated.

## 5.8.3 — 2026-10-10 · Fix: attributes list in the persona flow

- 5.8.2 broke the attributes list in the "how it is built" flow: a variable shadowed another one in the same function,
  so it threw before rendering. The live QA re-run caught it within minutes; fixed.

## 5.8.2 — 2026-10-10 · Quieter "how it is built" flow

- Each API row shows one short line (size or reason) instead of repeating the synthetic-seller policy note; the full text
  is in its tooltip and the inspector.
- The attributes table has fixed columns, so the origin chips line up.
- A phone number never appears in the attributes, not even masked: it reads "present · withheld".

## 5.8.1 — 2026-10-10 · Inspector race fixed; persona identity survives a failed live call

- Closing the inspector (Esc) while it was still loading made the late answer throw "Cannot read properties of null"
  (found by the persona-page QA; it affected the call page too). Late answers are now dropped.
- When a run cannot reach the company record (for example, after the IndiaMART key expires), the persona page shows the
  identity an earlier live run confirmed, with its date, instead of "Private seller · GLID …".

## 5.8.0 — 2026-10-10 · Standalone persona page (PM pointers); no invented profile for a real GLID

- **Open persona → how it is built → the persona page** (PM): every seller tile has *Open persona*. It starts a run and
  opens `/persona/{run}`, which first shows the flow:
  - the APIs being called, live, each with its origin (live / cache / dataset / mock), time and result;
  - the attributes they filled, grouped, with "Not in file" for gaps and a link to each source;
  - the persona generator's input, then "Persona ready", and then the persona page.
- **The persona page is the main dashboard, standalone and clean**, in the PM's sample layout: header (name, company,
  city, GLID, data mode), Basic details · Engagement with IndiaMART · Engagement with buyers, the Conversation plan
  (dials and five steps), and the full-record link.
  - No quality panel or pipeline text; those live in *How this persona was built*.
  - A green **Start call** starts the call from the persona: the bot opens as the executive and you answer as the seller.
  - The call page gained a *Persona page* button.
- **23456789 showed as "Khan Traders":** signed out, a search for a GLID that the site doesn't know returns a generated
  demo seller — that is the made-up name. A GLID that belongs to a real seller (approved, or in the supplied dataset) now
  returns "Real IndiaMART seller — sign in", never an invented profile. Signed in, search, the brief and the persona page
  show the identity the live company record confirmed instead of "Private seller · GLID …".
- New page path `/persona/` added to the Traefik rule (both compose copies).

## 5.7.6 — 2026-10-10 · An explicit language request switches in code

- On one live run the built-in VANI ignored "please speak English" and kept Hindi, so no banner showed. An explicit
  request (the same detector the seller side uses for "Hindi mein boliye") now switches the call's language in code
  before the model answers; the banner, the model's instructions and the English rewrite guard all follow.

## 5.7.5 — 2026-10-10 · Buyer desk clock, two options enforced, current side panel

From the 5.7.4 browser matrix:
- Every buyer-desk tile showed "ON AIR NaN:NaN": buyer tiles never carried an on-air time (pre-existing). They do now,
  and so do the REAL CALL tiles of live Sarvam calls.
- The built-in VANI still listed three options on some calls and mixed Telugu words into English: code now detects
  more than two options of the asked specification, or another language after an English switch (Hindi or Telugu),
  and rewrites the reply once.
- The buyer desk's side panel described only the old production flow; it now explains Talk as Buyer (the owner's
  requirement flow, also on Sarvam) and Barge in (the production flow).

## 5.7.4 — 2026-10-10 · English means English

- When the buyer has switched to English and the built-in VANI's reply still carries Hindi words, the reply is
  rewritten once in plain English before it is spoken (one extra Sarvam call, only then). The guard note says so.

## 5.7.3 — 2026-10-10 · Built-in VANI keeps the buyer's language, two options at most

From live browser QA of Talk as Buyer:
- After "please speak English" the built-in VANI recorded the switch but still answered in Hinglish, listed three
  options and read the answer back. The policy now carries these three rules with a wrong and a right example each.
- The buyer call's architecture pane names the requirement policy; *Re-run with original prompt* is hidden on Talk as
  Buyer calls (they start from scratch).

## 5.7.2 — 2026-10-10 · VANI on Sarvam: two options at most, no read-backs

From the first live text chat and headless voice call with the VANI buyer agent:
- The agent read out all three Power options; the prompt now says it names at most two, never the whole list (the
  owner's rule).
- After "pachaas piece, das HP wala" it read the values back and asked to confirm; a clear answer is now accepted as
  said and the agent moves to the next open item (the owner's "do not repeat his answer back").

## 5.7.1 — 2026-10-10 · VANI build fixes from the first live build on Sarvam

- Sarvam refused the prompt because `call tool:end_interaction.` had a full stop right after the tool name; the
  playbook wants a space before punctuation after a call directive. Fixed, and a test now asserts it.
- On-end tools cannot let the model fill fields ("no model in the loop"), so `report_requirement` now sends Sarvam's
  after-call output variables (product, pieces, unit, specifications, details, disposition, outcome). The build
  updates an older row in place.

## 5.7.0 — 2026-10-10 · VANI buyer agent on Sarvam, Talk as Buyer, the owner's buyer-requirement prompt

- **A second Sarvam Voice Agent, "VANI · IndiaMART buyer requirement", built through the Sarvam Voice MCP** (Admin →
  Sarvam voice agent → *Build / update VANI on Sarvam*). It answers a buyer whose call a seller missed, notes the
  product, the number of pieces and the specifications, and closes with the sellers' links on WhatsApp. Same sign-in,
  workspace and keys as Payal.
  - Its prompt is the owner's VANI prompt, restructured to Sarvam's agents playbook: the seven sections in order,
    English third-person source text (Sarvam writes the Hindi and Telugu), outcomes captured after the call, tools
    created before the prompt names them, a rule order and a confidentiality guardrail.
  - Tools: `load_buyer_context` (on start), `requested_another_product_tool` and `report_requirement` (on end, a test
    lead only). It also has an eval suite of four scenarios and a male voice, because the owner's lines use masculine
    forms.
- **Talk as Buyer** on the buyer desk: every tile has *Barge in* (secondary, left) and a green *Talk as Buyer →* (main).
  The call starts from scratch: VANI answers the missed call, and you are the buyer.
- **The owner's buyer-requirement flow drives the built-in VANI too** (policy `requirement`):
  - product → number of pieces (never the word "quantity") → specifications, one at a time → close;
  - code enforces the ask limits (product 4, pieces 3, each specification 2), the product-change rule and dispositions
    (BL Approved / BL Enriched / BL Deleted with the owner's outcomes);
  - the closing lines are the owner's, verbatim.
- **Visible adaptation on the buyer side:** a *VANI adapted* banner (language switch, ask limit reached → left to the
  seller, product changed → options reloaded) flashes once, with a toast.
- **Call or chat with VANI on Sarvam from the buyer call page:** voice through Sarvam's browser SDK (the same proxy as
  Payal, key stays on the server; a REAL CALL tile appears on the buyer desk) or text through MCP `send_chat`. Sarvam's
  captured requirement shows in the lead pane. A headless SDK smoke call is available for operators.
- **Payal's intro is English source text again:** "Namaste {{seller_name}} ji, this is Payal, IndiaMART's virtual
  assistant. You deal in {{products}}, right?" The 5.6.0 Hinglish intro and its quoted Hinglish meeting ask broke
  Sarvam's "author in English" rule; the platform writes the Hindi version. Same content: products first, then the
  direct meeting ask.

## 5.6.2 — 2026-10-10 · Products first even when the data gives a reason to call

- When the persona's call plan supplies the reason (an open ticket, a follow-up), the opening still names what the
  seller deals in first: "…virtual assistant. Aap <categories> ka kaam karte hain. <reason>?" — unless the reason
  already names the product. Found in live QA of Talk as Seller (a plan opening skipped the products).

## 5.6.1 — 2026-10-10 · Opening names the seller's categories; dataset history labelled

From the first live run on the approved real seller after 5.6.0:
- The opening named one long product ("Aap <a long product name> ka kaam karte hain"); it now names the storefront's
  top two product categories by product count (else the dataset's top categories; never a mock for a real seller).
- "1 buyer enquiries aayi hain" → "1 buyer enquiry aayi hai" (singular/plural in Hindi and English).
- An insight that rests only on supplied-dataset counts gets the note "Supplied dataset, Apr–Sep 2026 — history, not
  today" (the model wrote "in the last 90 days" for a snapshot window).

## 5.6.0 — 2026-10-10 · Real seller data end to end, Talk as Seller, a shorter product-first Payal

- **Real sellers get real data only (owner: "dont make things anywhere").** The persona generator now reads the
  supplied dataset row (business type, nature of business, GST year, categories, engagement counts — labelled as the
  Apr–Sep 2026 snapshot) and every past VANI bot call (dataset + live Redash hot-lead dispositions, merged by time;
  "Meeting Fixed" is shown as the bot's claim). A private seller's products, company and contact come only from the
  live company record — never from the placeholder profile (it once produced an invented "… (Premium)" product); a new
  critical check removes any product not in the input and any "Private seller · GLID" placeholder. A seller who asked
  not to be called gets no call plan.
- **Live fixes found on GLID 23456789:** Redash returns ids as floats (`23456789.0`), which the scope guard refused —
  ids are now compared as integers (another seller's id still fails). A free seller has no showroom alias, so the
  company-detail step was skipped and identity stayed "pending" — the alias response already holds the full company
  record and is reused (labelled). An empty purchased-leads list now says why lead details were not fetched.
- **Talk as Seller.** Floor tiles have *Barge in* (secondary, left) and a green *Talk as Seller →* (main, right; 10.7:1
  dark / 5.4:1 light): a fresh run pulls all the data, then the call starts from scratch — the bot opens as the
  executive and you answer as the seller (no seeded lines, no simulated seller).
- **Real sellers on the floor (signed in only):** Admin → Call floor → `pinned_real_sellers` pins approved GLIDs at
  the top. Their tiles simulate nothing — identity comes from the last live run's company record — and offer only
  *Talk as Seller*. Search finds the same tile.
- **Shorter, product-first opening (owner's Payal notes).** "Namaste <name> ji, main Payal, IndiaMART ki virtual
  assistant. Aap … ka kaam karte hain — pichhle 30 din mein aapko 2 buyer enquiries aayi hain. Kya ek chhoti meeting
  rakh lein — IndiaMART aapke business mein kaise madad kar sakta hai?" The reason is the first real fact available:
  enquiries (30 days), local buy leads, the last meeting, or the last bot call — never a mock for a real seller. Bot
  replies are capped at 10/18 words (was 14/28), one idea each, with a direct meeting ask when the seller is busy;
  objection lines were shortened. The generator prompt asks for the same (products + a dated reason within 20 words,
  sentences of 12 words or fewer; a new warning check).
- **Payal (Sarvam Voice Agent):** intro names the products ("Aap … ka kaam karte hain, sahi?"), turns of about 15
  words, the reason within 20 words (enquiries in the region or the last meeting), then the direct meeting ask; a new
  test scenario. Takes effect after Admin → Sarvam voice agent → Build. Real sellers stay excluded from Sarvam Voice
  Agents.
- **Persona switches are visible:** a banner above the teleprompter names the latest switch (signal, reason, the
  changes such as "pace 1.0 → 1.12") and flashes when a new one applies, with a toast; the transcript marks each
  switch with an accented "⇄ Persona switch" line.
- **Admin is searchable:** one box searches every setting in every section (including the ones with custom pages) and
  every API; matches are editable in place and saved as one revision; the section list shows match counts; Esc clears.

## 5.5.2 — 2026-10-10 · No console error while the site restarts

- The top-bar status dots no longer throw when the status request is answered by the V3 rollback during a restart
  (found by the live-QA matrix's first run after the 5.5.1 deploy).

## 5.5.1 — 2026-10-10 · Persona index usable on phones; readable Barge-in buttons

- **`/genome` on phones:** the search card was squashed to ~20 px and the facet panel ran 18 px off-screen at 390 px
  (an inline two-column grid beat the phone layer). The layout and the search form now collapse to one column.
- **Barge-in / on-air buttons:** text on on-air red is a new token `--onair-ink` — 6.68:1 in dark mode (white was
  2.94:1, failing WCAG AA), white 5.62:1 in light mode.
- Both found by the new UI-audit skill's first run.

## Project skills — 2026-10-10 (tooling and docs; the app stays 5.5.0)

- **31 standard Claude Code skills** in `.claude/skills/` (index `.claude/skills/README.md`, start with
  `sellergenome-onboarding`): domain, data rules, IndiaMART APIs, call pipeline, persona generator and evaluation,
  pre-call brief, VANI, meetings, Sarvam LLM, voice engines, latency lab, Payal, Sarvam MCP, voice plug-in, Elastic,
  Kibana, settings, backend, UI, UI audit, diagrams, testing, live QA, security/privacy audit, release, incidents,
  closeout, demo. Each has a trigger description, references and an eval set (52 test prompts with expectations).
- **Working scripts** proven on the live system: release + wait-healthy, test runner (pytest in the image +
  `node --check`), staged-diff secret scan, live QA (call, anonymous barge-in, full matrix), Elastic read-only query
  helper, persona evaluation runner + report comparer, Sarvam Voice MCP helper (refuses telephony/campaign/delete
  tools), UI audit wrapper around the root next-level-website-designer tools, screenshot embedding; a safe V5 preview
  compose (the checked-in V4 preview compose would replace production).
- AGENTS.md, CLAUDE.md, docs/README.md and PRODUCTION_PLAN (§15–16) point agents to the skills first.

## 5.5.0 — 2026-10-10 · Personas from the owner's Persona Generator v0.3, fed the way the API guide says

- **The persona now comes from the owner's prompt v0.3** (`config/genome/prompts/persona_generator_v0.3.md`, changes listed
  at its top): basics, a persona with four dials, cited insights with H/M/G confidence, engagement with buyers, a call
  plan only when the data gives a reason (open request → follow-up due → hot lead → unresolved last conversation), the
  record, gaps and guardrails. Admin → Persona generator switches back to the 5.x aggregator.
- **Inputs follow the Consolidated API Guide (2026-10-09):** Global Context in its documented shape (KYC, calls and
  meetings, tickets, activity windows, demand), STS split into person and system entries (calibrated on a real log),
  PNS distinct buyers/days and promised callbacks, enquiries with verification checks and quantity, storefront facts.
  Never sent to the model: amounts, buyer/staff names, employee ids, refund/legal/fraud/verification notes, ticket text,
  competitor names, ratings, First Copy enquiries. Paid/Free from `customer_type`.
- **Every persona is checked** (≈45 rules from the prompt and the guide), repaired once by the model when a critical rule
  breaks, then corrected in code where the rule is arithmetic (dial bounds, evidence ids, withheld gaps, native-speaker
  openings, plans the data does not support). Quality panel in the brief; scores in Elasticsearch.
- **The bot's first line follows the plan:** the teleprompter speaks its greeting plus the plan's reason and first
  question (an open ticket is no longer opened with "you looked at the renewal page"); *Why call* in the call pane.
- **Measured live** (25 synthetic scenarios + 2 real sellers): quality 75.2 → 96.9/100 across three scripted runs, then 98.1 and 98.9 in live runs from Admin; 26/27 with no
  critical issue; median 5.7 s per persona. Admin → Persona generator runs the suite; results go to `persona-evals`.
- Docs: [persona-generator.md](docs/architecture/persona-generator.md), what the guide changed in
  [sources-and-apis.md](docs/architecture/sources-and-apis.md), decisions D33–D40.

## 5.4.0 — 2026-10-10 · Faster, more natural calls (tuned in a live voice lab)

- **VANI answers in ≈1.9 s** from the seller's last sound (was ≈3.2 s): Bulbul v4 reads romanised Hinglish directly, so
  transliteration runs only for v3 (Admin option); the reply's speech starts inside the turn request with the Bulbul
  socket opened while the LLM writes; streamed speech plays as raw PCM through Web Audio.
- **Payal answers ≈0.1 s faster and more consistently** (hybrid voice detection, 3 silence frames; median 1.33 s on live
  calls, no talk-over); her pace and Sarvam voice are Admin settings; the build records the committed version correctly.
- **Reply times on screen:** "answered in N s" on VANI's lines and Payal's panel.
- **Listening board:** Admin → Compare voices with female/male Bulbul v4 sets, pace and "use as default".
- Buyer desk listens without seller keyterms (fixes an invented "TrustSEAL."). `scripts/voice_lab.py` for repeatable tests.

## 5.3.0 — 2026-10-10 · Voice engines: Bulbul v4, Saaras v4 realtime, live Payal calls with Sarvam's SDKs

- **Engines are choices, not replacements:** Admin → Voice & engines sets defaults for bot, Bulbul model/voice/delivery,
  listening, Saaras models and transcript style, translation and LLM; ⚙ Engines pickers on the call workspace, buyer desk
  and `/sarvam` override them per browser. Bulbul v3, REST speech, clip listening and the built-in bot stay available.
- **Streamed Bulbul v4 flash** (224 voices): first sound ≈0.4 s instead of ≈1.5 s; the persona's voice maps into v4.
- **Realtime listening (Saaras v4):** words appear while you speak, a pause ends the turn, speaking over the bot stops it.
- **Live voice calls with Payal on Sarvam** from the browser (Sarvam's conversational SDK, bundled; the key stays on the
  server) in the call workspace and on `/sarvam`; shown on the floor as a REAL CALL. Admin smoke call with Sarvam's Python
  SDK. Payal now uses hybrid voice detection and IndiaMART hotwords (version 5).
- Buyer desk: spoken replies and live/clip listening. Fixed: signed-in top bar overflowed 3 px in WebKit at 390 px.

## 5.2.0 — 2026-10-09 · PM pre-call brief, audible calls, Payal on Sarvam Voice Agents

- **Fix:** "Speak as seller" failed with a Sarvam HTTP error — Chrome records `audio/webm;codecs=opus` and Sarvam STT rejects the codec suffix. The type is normalised (WebM/MP4/OGG) and Sarvam's own reason is shown on any failure.
- **Hear the call:** bot lines play in the persona's Sarvam voice, the simulated seller in a second voice; Auto waits for speech; 🔊 toggle remembered.
- **PM's VANI Persona Studio** integrated: basic details with origins, Engagement with IndiaMART (Sarvam-grouped, cited insights with H/M/G confidence and dated evidence chips), engagement with buyers, conversation plan with live dials and the current step, full record drawer, pipeline footer — in the persona pane, a compact plan in the call pane and a full-width "Pre-call brief".
- **Payal on Sarvam's Voice Agents platform**, built through the Sarvam Voice MCP: two knowledge bases (IndiaMART playbook + 14 seller briefs), agent with Hinglish voice, 8 languages, variables, three API tools calling our API (context, meeting booking, outcome), voicemail, nudges, eval suite; committed versions. `/sarvam` hosts a live text conversation with it (spoken replies optional) and maps all 41 Voice MCP tools.
- Admin: Sarvam voice agent (connect via Sarvam SSO, build, rotate tool key, eval, Voice API key test); Sarvam key replaceable in Admin → Keys; budgets raised for spoken calls.

## 5.1.0 — 2026-10-09 · Live IndiaMART APIs + architecture side bar

- **Live data**: the APIs from the owner's API doc now run live through a configurable pass-through gateway for approved test GLIDs — company alias + details, purchased buy leads + details, enquiries, PNS call insights, LMS chats, STS history, Global Context userCtx → getContext. Admin → APIs → Gateway sets the URL (allow-listed), on/off and headers; *Check gateway* shows coverage.
- Real persona evidence: STS complaints/payments/tickets, enquiries, purchased leads, seller-side PNS calls and chats are parsed (`backend/genome/parsers.py`), scrubbed of contact and bank details, cited by Sarvam and shown as source-backed evidence cards.
- Private sellers: identity confirmed from live APIs (no invented names), latest real STS activity as the trigger, and no seller-specific mocks ever.
- Keys: JWT expiry shown in Admin → APIs → Keys; expired keys refused before sending; `{secret:NAME}` and `{x|int}` placeholders; form-encoded bodies; errors inside HTTP 200 shown as failures.
- Call workspace: the architecture graph is a right-side **hidable bar** (button, `]` key, remembered) with a business-flow stepper and "you are here"; new gateway node, fan-out nodes (buy-lead details ×N, LMS chats ×N), MOCK/LIVE planning before a run, and "not run" after.
- Inspector: edit the URL template and params/body and hit it (fork), or save as the API default (signed in); nodes the run did not need still open with *Hit it now*; sibling chips for fan-out receipts; route and key expiry shown.
- Fixed: Edit & send previously started from the masked request, so a live re-send would have sent `[server-held]` as the key.

## 5.0.1 — 2026-10-09

- Live-QA fixes: stable generated identities with dataset hints, readable switch log, in-flight call requests cancelled when leaving a call, admin section tabs on one line.

## 5.0.0 — 2026-10-09 · V5 Command Center

- New main portal at `/`: a live (simulated, labelled) seller call floor and buyer desk; **barge in** to any call or searched seller and watch real context preparation stream in.
- Three-pane call workspace: persona (Sarvam summary, scored tags, cited PS04 factors, LLM-discovered novel factors, insights, doubts, 29-field origin table) · call (teleprompter with Sarvam speech/translation, live transcript, signal meters, switch log, packages, meeting desk) · live architecture graph with a parse-style inspector (request, response, Elastic query, re-run, edit-and-send fork).
- New `backend/genome` package and `/api/v5`: production persona contract with per-field origin; 14 synthetic hero sellers and deterministic mocks for any GLID; editable API registry (allow-listed hosts, write-only keys, modes, TTL, cache/persist switches, hit log); Elastic cache + async response store; Sarvam `sarvam-105b-conversations` aggregation and locality; typed persona-factor index with suggestions/edits.
- Live call engine: Hinglish/Devanagari/Telugu signals, recency-weighted levels, per-factor cooldown, Sarvam-simulated seller from IndiaMART's sales-training archetypes, take-over by typing or speaking.
- Buyer-side VANI: the supplied production prompt rendered with its template conditions and run on Sarvam (signed in), or the compact policy; ask limits, tool preconditions and consent enforced in code.
- Meeting desk with real Jitsi links, `.ics` files and Google Calendar links; voice-bot plug-in API with client, harness and LiveKit sketch; Sarvam studio for all 36 MCP tools; API-first admin.
- V4 console preserved under `/v4`; documentation restructured (`docs/README.md` start here, business flow, glossary, decisions, facts sheet, V5 architecture).
- Elasticsearch app role extended to `sellergenome-v5-*`.

## 4.1.4 — 2026-10-09

- Added seven collapsed explanations beside analysis charts, including exact category rules and a worked matrix calculation.
- Built a verified5,000-call persona-cohort study and78,365-call General diagnostics with explicit evidence/response denominators.
- Added versioned deterministic evaluator, synthetic demos and authenticated historical call/GLID review with source spans and safe rerun.
- Kept private review in a paired read-only sidecar; no corpus audio fetching, upstream API calls, source mutation or provider spending.
- Added negative/actor/duplicate/conflict/privacy tests and mobile-friendly disclosure/table browser acceptance.

## Elastic access1.0.0 — 2026-10-08

- Published authenticated Elasticsearch/Kibana at the requested exact subdomains and both mirrors through backend TLS→rig routing; existing platform routes and data nodes preserved.
- Added a read-only Nginx gateway with PM-password hash validation and a separate strong prefix-scoped native account. No host database ports or anonymous raw data access.
- Added a dedicated SellerGenome Kibana Space with24 data views,eight visualizations,two dashboards and stable namespaced IDs/references; original default objects remain intact.
- Added explicit bootstrap/reinstall and public API/Chromium/WebKit validation scripts,access/rollback guide and direct dashboard links. Configured Secure/Strict native cookies and disabled incompatible optional vendor feedback intercepts using verified CLI flags.
- The SellerGenome application remains3.1.0; this is its new Elastic access gateway release,not a change to persona/speech behavior.

## 3.1.0 — 2026-10-08 (public SellerGenome)

- Published both SellerGenome domains via the requested Domain Handler; `/walkthrough` opens the full PM workshop and `/analysis` opens the evidence atlas. Removed the18080 application binding; retained V2 portfolio and future path namespace.
- Added lossless private raw indexing for16 CSVs/908856 rows, typed/flattened search, source/date lineage, versioned file/family aliases, prefix-scoped publishing and independent replay/readback tools.
- Added70 actual persona-choice documents with value/reason/strength/freshness/citations, guarded revision writes and honest projection receipts. Fixed automatic persona regeneration resetting revision to1.
- Added ten sourced research chapters,45 numeric profiles, fixed real ES aggregate controls, safe export, denominator-aware rates, corrected summary lengths and prioritized evaluation recommendations.
- Added24 Kibana data views, eight actual Vega charts, two dashboards and verified supported import tooling. Elasticsearch/Kibana remain private.
- Added cross-domain browser/voice evidence,141 unit/API tests, public routing/rollback and developer handoff. No automatic paid corpus inference, raw anonymous export or invented causal uplift.

## 3.0.0 — 2026-10-08 (isolated V3 preview)

- Added protected approved-GLID source registry, eleven independent Disabled/Sample/Live adapters, four fixed Redash query contracts and durable remote-job checkpoints.
- Added revisioned file-backed Admin settings, validation/rollback, write-only immutable credential versions and protected server endpoint registry.
- Preserved new private inputs without duplicate corpus import; indexed historical summaries, actor-aware insights, source revisions and all meeting-flag conflicts.
- Added bounded cached gateway insight extraction and explicit durable selected-seller cohorts, with cancellation and no automatic rebilling after unknown completion.
- Grounded persona choices and wording, actual inferred-change cooldown, separate suppressed decisions and historical association denominators.
- Added shared-password server sessions, full operator access, login throttling, expiry/logout/password revocation including live voice, and browser secret removal.
- Redesigned Studio / Workshop / Admin with35 paired Demo/Live cases, offline synthetic speech assets and256 precise feedback anchors; durable inbox/history/export and acknowledged saves.
- Added model-specific speech controls and server-stage timings. V3 stays default; provider-deprecated or beta-restricted pitch profiles remain honestly unavailable until the provider permits them.
- Added loopback18080 deployment with separate data/config/indices, preview snapshot tooling and generation-safe bulk backfill; final public hosting is deferred by the owner.

## 2.0.1 — 2026-10-08

- Updated the public pitch slide to the verified voice/diarization scope and remaining evaluation work.

- Suppress stale streamed audio generations after manual or detected interruption.
- Show configured voices/languages and batch limits; paginate/filter original recordings.
- Start synthetic browser microphone fixtures after upstream readiness and verify actual saved signals rather than verbatim ASR text.

## 2.0.0 — 2026-10-08

- Added private primary/backup Sarvam configuration with persistent quota-only failover and account-pinned jobs. Real exhausted-primary/backup validation passed.
- Added six real bilingual synthetic voice examples with contrasted configured voices and pace; playback requires no anonymous inference.
- Added durable consented batch transcription/diarization, chunk timelines, role assignment, corrections, owner isolation, cancellation and explicit audio deletion. Reviewed seller speech commits with persona invalidation and indexing intent.
- Added safe original-recording lookup by dataset ID. Normalize bounded uploads to mono 16 kHz WAV; low-rate source MP3s returned empty provider batch output until normalized.
- Added authenticated same-origin duplex WebSocket rehearsal with partial/final recognition, VAD endpointing, streamed MP3 speech, interruption, one-use tickets, microphone cleanup and bounded live duration.
- Shared text/live conversation rules preserve source suppression, freshness, switches, outcomes and method labels. Added Recording Studio and updated configuration/status/roadmap/API/developer documentation.
- Added real provider and cross-engine browser evaluation scripts, meaningful quota/ownership/cancellation/review/normalization regression tests. Real-world acoustic/gold-corpus and upstream-access limits remain explicit.

## 1.1.1 — 2026-10-07

- Versioned all portal module imports to prevent mixed releases from cached CDN assets.
- Put refresh controls above history, collapse older jobs, enlarge mobile source text and preserve open receipts while polling.
- Show when a concurrent request reuses an active job; isolate parallel browser cases by source and scrub private IDs before screenshots.

## 1.1.0 — 2026-10-07

- Added durable operator-only source jobs with idempotency, leased restart recovery, safe failure receipts, bounded transient retries and cancellation fencing.
- Added opt-in per-seller/source refresh schedules with configurable limits, pause/enable/edit controls and coalescing after downtime.
- Added a transactional indexing outbox with per-item Elasticsearch bulk receipts and generation-aware acknowledgment.
- Applied source revisions, contact suppression, persona invalidation and job completion atomically; unchanged/late observations preserve existing personas and source times.
- Added Integrations activity controls, authenticated history, mobile layouts and page-scoped polling that preserves focused controls and form values.
- Updated API schemas, worker readiness, plan, developer guide and browser regression workflow. Internal access/Sarvam credit blockers remain explicit.
- Fixed first-visit polling identity and separated public/operator request counters; normalized connector summaries now carry validated field values into bounded model context.

## 1.0.0 — 2026-10-07

- Researched and profiled every supplied dataset before application implementation.
- Added the full eight-section seller-persona portal and FastAPI backend.
- Imported private structured seller evidence and recording cohorts; generated per-seller explainable baselines and indexed persona intelligence in Elasticsearch.
- Added real validated LLM enrichment, live adaptation, persistent switch logs, explicit AI disclosure and readiness outcomes.
- Added protected timestamped event ingestion, stale-persona invalidation and contact-suppression enforcement during active rehearsals.
- Added Sarvam speech adapters, bounded microphone capture, read-only upstream connectors and optional local transcription; external access and quota limitations are visible.
- Deployed both portfolio subpaths with isolated Elasticsearch/Kibana and verified existing portfolio routes remain unchanged.
- Added operator/privacy boundaries, commented configuration, mobile/keyboard/browser regressions, full planning and developer handoff.

## 4.0.0 — 2026-10-09

- Added SellerGenome operations dashboard, actual preparation traces, request/response inspection and unified navigation with dark/light/mobile layouts.
- Preserved production inputs with lossless parsing/provenance; added independent snapshots/cache, durable delivery/history and separate established/incidental persona resolution.
- Added searchable typed factor projections and ten Kibana operational charts without changing raw corpus indices.
- Added persistent four-flow buyer collection, original prompt comparison, catalog guidance and idempotent test meeting/Calendar adapters.
- Added Sarvam36-tool Lab, designated-account inference/speech, Code Tools bundle and LiveKit worker handoff; external workspace/calendar/company contracts stay explicit dependencies.
- Connected effective V4 personas to existing owner-bound speech rehearsals; retained Workshop, Recording Studio, feedback, analysis and V2 deployment.

## 4.0.1 — 2026-10-09

- Serialize buyer response/tool interactions so fast transfer clicks wait for saved consent. Display one authoritative product-confirmation value.
- Separate execution modes within every V4 Kibana chart and label averages with their actual units.
- Serve trailing-slash operations pages directly so the backend proxy cannot redirect mirror visitors onto the primary domain and lose their session.

## 4.0.2 — 2026-10-09

- Normalize slash-alias page selection so Buyer, Meetings, Sarvam and Admin retain their correct interface on either mirror.

## 4.0.3 — 2026-10-09

- Reject stale run polling responses and preserve expanded suggestion drafts during progressive updates. Offline browser checks wait for the actual failure before reconnecting.

## 4.0.4 — 2026-10-09

- Persist explicit execution modes on buyer rehearsals and incidental observations; missing legacy mode metadata is unclassified instead of assumed historical.
- Keep the current authenticated workspace visible during a failed route refresh; connectivity loss must not replace a draft with a misleading login screen.
