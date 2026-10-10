<!-- Copy of docs/business/glossary.md (2026-10-10) for agents; when the two differ, docs/business/glossary.md wins and this copy should be refreshed. -->

# Glossary

> IndiaMART, hackathon and SellerGenome terms in one place. If a document uses a term that is not
> here, add it here.

## IndiaMART business terms

| Term | Meaning |
|---|---|
| **GLID** | Global user ID of an IndiaMART user (seller or buyer). In data files it appears as `glid`, `fk_glusr_usr_id`, `USR_ID`, `SELLER_GLID`, `BUYER_GLID`. |
| **glusr** | The global user record; `addressGlusr` is the address on the seller's profile. |
| **custtype** | IndiaMART's internal customer type code for a seller (e.g. `53`). Lower usually means a more premium customer; some types have names. |
| **custtypeweight** | Numeric weight of the customer type. **Below 1000 = paid seller, above 1000 = free seller, exactly 1000 = unknown** (the supplied rule is ambiguous at 1000). Not the same field as `custtype`. |
| **Hot lead** | An activity-triggered sales opportunity (e.g. a seller viewed a paid package). Produced by `SALES-HOTLEAD-GENERATOR-CONSUMER`. |
| **SHL / `lead_shl_trigger`** | The hot-lead meeting flag in the bot-call table: whether a meeting was fixed. The raw flag and its interpretation are kept separately (41 calls disagree with their summaries). |
| **Activity ID** | Code of the platform activity that created a hot lead (production sample: `2368`). |
| **Data type (`PIM`, …)** | Lead data type re-checked after the 15-minute delay. |
| **Exploration window** | The ~15 minutes IndiaMART waits after the activity before the bot calls, so the seller can keep exploring. Configurable (0 = real time). |
| **VANI** | IndiaMART's voice bot. Seller-side VANI calls sellers to set up meetings; buyer-side VANI answers buyers whose seller did not pick up. |
| **Executive / SE** | Human IndiaMART sales executive. Does the meeting and the sale after the bot books it. |
| **SquadStack, Arrowhead** | Calling vendors that run the bot calls. The production log pushes the persona payload to SquadStack's vendor API. |
| **PNS / PNS call** | Masked-number call: a buyer calls a PNS number that can ring one or more of the seller's phones. PNS call insights include buyer intent, products, specifications and next steps. |
| **Enquiry** | A buyer's quotation request sent directly to a specific seller. |
| **BL / Buy lead** | A buyer's requirement given to IndiaMART (not to one seller); sellers buy/pick it up. "Local" and "national" counts in the payload. |
| **Assisted buy / AST** | Buyer-side flow that connects a buyer to an available live alternate seller (`ast_seller_pns`, `ast_buy_confirmed_tool`). |
| **BL enrichment** | Buyer-side flow that improves an existing lead's details. |
| **MCAT** | Micro-category — the fine-grained product category (e.g. "Wooden Dining Chairs"). |
| **ISQ** | Item specification questions — product attributes asked in enquiries. |
| **STS** | Sales Tracking System — IndiaMART's record of interactions with a seller ("Called at 5 PM…", "Paid ₹50,000 by cheque"). |
| **LMS** | Lead Management System / message centre — buyer–seller chat history (25 messages per page, newest first). |
| **Global Context API** | `userCtx` — the seller context service that populates the vendor payload (KYC, activity, etc.). |
| **Company API** | `company.imutils.com` company-detail service: GLID → showroom alias → company details, products. |
| **Insights API** | Returns `seller_summary`, seven insight categories and `doubts` for a seller. |
| **Redash** | IndiaMART's SQL query tool. SellerGenome runs four fixed, read-only queries (recordings/transcripts, hot-lead summaries, executive calls, PNS calls). |
| **TrustSEAL, packages** | Paid seller packages. SellerGenome only states catalog-backed package facts and never invents a price. |
| **DNC** | Do not call — always honoured immediately. |

## Hackathon terms

| Term | Meaning |
|---|---|
| **PS04** | Problem Statement 04, *Persona Design* — the problem we are judged on. |
| **Persona spec** | Voice, language, tone and conversation plan for one seller, with a reason and confidence for every choice. |
| **Switch log** | One row per live persona change: signal, change, reason, time. |
| **The five signals** | Frustration, confusion, interest, language switch, rush. |
| **Sarvam** | Indian voice-AI provider and hackathon sponsor: STT, TTS (Bulbul), translation, LLM (`sarvam-105b`, `sarvam-105b-conversations`), Voice Agents, MCP. |

## SellerGenome terms

| Term | Meaning |
|---|---|
| **Live floor** | The landing dashboard showing ongoing (simulated) seller or buyer calls. |
| **Barge in** | Opening a call from the floor or from search. Starts a real context-preparation run for that seller. |
| **Preparation run** | One traced execution: every source fetch, cache lookup, LLM call and index write, with timings. |
| **Origin** | Where a field's value came from: `live_api`, `elastic_cache`, `local_db`, `mock`, `default`, `blank` (plus `conflicting`). |
| **Established / incidental / effective persona** | Reviewed long-term persona / recent observations (including operator corrections) / the run-time resolution of both (default 80/20 influence). |
| **Persona factor** | One deconstructed persona attribute (e.g. `objection.primary = fund_issue`) stored as its own Elasticsearch document with typed value slots, evidence and confidence. |
| **Persona index** | The Elasticsearch index of persona factors — makes personas searchable and chartable. |
| **Teleprompter** | What to say next: opening, next question, objection response, handoff. |
| **Approved GLIDs** | The 77 internal test GLIDs that may be sent to live upstream APIs in `dev` GLID access; `prod` allows any GLID for signed-in users (D51). |
| **Demo / Sample / Local / Live** | Synthetic scenario / deterministic fixture / supplied historical data / real upstream call. |
