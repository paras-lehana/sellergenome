# SellerGenome

A living, self-explaining persona for every IndiaMART seller, built for the IndiaMART × Sarvam voice hackathon (PS04).

Enter a seller's GLID. The first facts arrive in under a second. Within seconds, eight live IndiaMART sources are fused into a 360-degree persona written by Sarvam's 105B model: the company profile, Global Context, STS history, enquiries, buy leads, PNS calls, chats and past bot calls. Every insight opens the exact API response that proves it. The persona then drives a live voice call that adapts its language, pace and tone mid-call, and every change is logged. On 103 live runs for real sellers, the first facts came at a median of 0.7 s and the full persona at a median of about 8 s; a recent persona is reused in about a second.

- **Live portal:** https://sellergenome.lehana.in (mirror https://sellergenome.aidhunik.com). The synthetic demo sellers are open to everyone. Real sellers need a sign-in.
- **Version:** see `config/genome/settings.yaml` → `app.version` and [CHANGELOG.md](CHANGELOG.md).

## How it works

1. **One GLID, every source.** `backend/genome/orchestrator.py` calls IndiaMART's APIs in parallel through a gateway; they are declared in `backend/genome/registry.py`. They cover the profile, Global Context, STS, enquiries, purchased buy leads, PNS call insights, chats, Redash hot-lead dispositions and the category's competitor listing. Phone numbers, emails, staff names and amounts are removed before any model sees the data. Every raw response is stored in Elasticsearch, which doubles as the cache.
2. **A persona written by Sarvam and checked by code.** The Persona Generator prompt is `config/genome/prompts/persona_generator_v0.3.md`. `backend/genome/persona_generator.py` numbers the evidence as records and facts that the model must cite. It produces:
   - basic details;
   - 7 to 12 insights on engagement with IndiaMART and with buyers;
   - four delivery dials: pace, Hindi-English mix, formality and warmth;
   - a call plan, only when the data gives a reason.

   About 40 automatic checks run on every persona, and a critical failure triggers a repair round.
3. **Explainable by design.** Evidence chips open the API response behind them. Each insight carries a confidence of High, Medium or Guess, and conflicts are shown as doubts. The architecture bar shows every API hit, cache read, model call and index write.
4. **Real-time Elasticsearch.** `backend/genome/elastic.py` and `persona_index.py` store each persona whole and as one document per factor, so personas are searchable across sellers and chartable in Kibana.
5. **Voice on Sarvam.** The built-in engine uses Bulbul v4 streamed speech and Saaras v4 real-time listening; it lives in `backend/genome/conversation.py`, `signals.py`, `sarvam.py` and `live_speech.py`. Any Sarvam Voice Agent can be picked instead (`sarvam_voice.py`, `sarvam_agents.py`, `sarvam_buyer.py`).
   - Every agent gets the persona and the talking points from `backend/genome/agent_context.py`:
     - competitors in the seller's category and city;
     - the market;
     - payment status;
     - buyers who reached out;
     - the reason for the call;
     - IndiaMART product knowledge.
   - Agents push for a pending payment first, then for a free meeting.
   - The engine reads rush, confusion, frustration, interest or a language switch and adapts mid-call, with a cooldown. Facts, prices and compliance lines never change.
6. **Control.** The *Talk as Seller* workspace shows the persona, the call and a live log of every API answer, Elasticsearch write, model call and switch. Admin manages the APIs, expiring keys, an API health test and every prompt.

## Repository map

| Path | What is in it |
|---|---|
| `backend/genome/` | The V5 pipeline: API registry and gateway, orchestrator, persona generator and checks, conversation engine, Sarvam LLM/TTS/STT/Voice Agent clients, Elasticsearch writer, FastAPI routes (`api.py`) |
| `frontend/cc/` | The Command Center UI: plain JavaScript modules with no build step (`talk.js` is the call workspace, `persona.js` the persona page, `admin.js` Admin) |
| `config/genome/` | Settings (every option is editable from Admin) and the Persona Generator prompt |
| `tests/` | The automated suite (pytest, more than 370 tests) |
| `integrations/voice_plugin/` | The contract for plugging in any external voice bot |
| `scripts/` | The voice-latency lab, the persona evaluation runner and dataset tools |
| `deploy/` | Dockerfiles and compose files |
| `data/genome/` | Synthetic demo sellers and call scripts (every name and number is fictional) |
| `.claude/skills/` | 31 project skills: the quickest map of every feature, from the IndiaMART data rules to the Sarvam voice agents ([index](.claude/skills/README.md)) |
| `AGENTS.md`, `CHANGELOG.md` | The rules agents follow on this project, and the release history |

The team's internal notes under `docs/`, the supplied dataset, keys, gateway addresses and runtime data are not part of this copy, so links to `docs/` inside the skills and `AGENTS.md` do not resolve here.

## Run it

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                    # the test suite needs no keys or network
DATA_DIR=./runtime uvicorn backend.main:app --port 8000
# or: docker build -f deploy/Dockerfile.sellergenome -t sellergenome . && docker run -p 8000:8000 sellergenome
```

Without keys, the UI, the synthetic sellers and the tests still work. Live IndiaMART data needs the API gateway and its keys, and Sarvam needs `SARVAM_API_KEY`. Both are set through Admin or the environment; `.env.example` lists the variables.
