"""HTTP API for V5 (``/api/v5``). Contract summary: PRODUCTION_PLAN.md §6.

Access: synthetic sellers (``syn-``/``gen-``) are public so judges can barge in without a password;
private sellers (``real-``), admin, persona edits and live upstream calls need the PM session.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.genome import engines, floor, meetings, prompts, roster
from backend.genome.live_speech import AudioBuffer, Tickets, relay_realtime_stt
from backend.genome.contract import ORIGINS
from backend.genome import parsers
from backend.genome.registry import SECRET_MARK, gateway_base, host_allowed, referenced_secrets
from backend.genome.sarvam import BudgetExceeded, SarvamError


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCreate(Body):
    seller_id: str = Field(max_length=40)
    origin: str = Field(default="floor", pattern="^(floor|search|api|queue|talk|persona)$")  # talk = Talk as Seller; persona = Open persona
    flow: str = Field(default="seller", pattern="^(seller|buyer)$")


class Rerun(Body):
    url: str | None = Field(default=None, max_length=1000)
    params: dict | None = None
    body: dict | None = None


class CallStart(Body):
    run_id: str = Field(max_length=40)
    seed_turns: list[dict] = Field(default_factory=list, max_length=20)
    opening: bool = False  # Talk as Seller: the call starts from scratch with the bot's opening as its first turn


class SpeakOpts(Body):
    """Ask the turn endpoint to start the reply's speech itself (no second request, socket opened during the LLM call)."""
    model: str | None = Field(default=None, max_length=30)
    voice: str = Field(default="auto", max_length=60)
    format: str = Field(default="mp3", pattern="^(mp3|pcm)$")


class Turn(Body):
    text: str = Field(min_length=1, max_length=600)
    speak: SpeakOpts | None = None


class AutoOpts(Body):
    speak: SpeakOpts | None = None


class Speak(Body):
    text: str = Field(min_length=1, max_length=1200)
    language: str = Field(default="hi-IN", max_length=8)
    speaker: str = Field(default="ritu", max_length=60)
    pace: float = Field(default=1.0, ge=0.5, le=2.0)
    transliterate: bool = True
    model: str | None = Field(default=None, max_length=30)  # engines.TTS_MODELS; None = Admin default
    format: str = Field(default="mp3", pattern="^(mp3|pcm)$")  # streamed only: pcm = raw chunks for Web Audio


class Translate(Body):
    text: str = Field(min_length=1, max_length=1000)
    target: str = Field(max_length=8)
    model: str | None = Field(default=None, max_length=30)


class ListenTicket(Body):
    language: str = Field(default="hi-IN", max_length=8)
    model: str | None = Field(default=None, max_length=30)
    mode: str | None = Field(default=None, max_length=20)
    place: str = Field(default="call", pattern="^(call|buyer)$")  # whose words recognition should expect


class LiveStart(Body):
    seller_id: str = Field(default="", max_length=40)
    run_id: str | None = Field(default=None, max_length=40)
    agent: str = Field(default="payal", pattern="^(payal|buyer)$")  # buyer = the VANI buyer-requirement agent (5.7)
    buyer_call_id: str | None = Field(default=None, max_length=40)
    app_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{3,80}$")  # any agent of the workspace (5.10.2)


class LiveEvents(Body):
    events: list[dict] = Field(default_factory=list, max_length=50)


class MeetingCreate(Body):
    run_id: str = Field(max_length=40)
    call_id: str | None = Field(default=None, max_length=40)
    mode: str = Field(pattern="^(online|in_person)$")
    address_kind: str | None = Field(default=None, max_length=20)
    address_text: str | None = Field(default=None, max_length=300)
    start: str = Field(max_length=40)
    note: str = Field(default="", max_length=300)


class Suggestion(Body):
    factor: str = Field(max_length=64)
    value: Any
    reason: str = Field(default="", max_length=500)
    author: str = Field(default="PM", max_length=60)
    suggestion_id: str | None = Field(default=None, max_length=40)


class BuyerStart(Body):
    tile_id: str = Field(max_length=40)
    shown: int = Field(default=99, ge=1, le=40)
    policy: str = Field(default="compact", pattern="^(compact|original|requirement)$")
    talk: bool = False  # Talk as Buyer: the call starts from scratch with VANI's opening; the operator is the buyer


class ApiPatch(Body):
    changes: dict
    expected_revision: int


class SecretPut(Body):
    value: str = Field(max_length=4096)


class SecretBulk(Body):
    value: str = Field(min_length=1, max_length=4096)
    names: list[str] = Field(min_length=1, max_length=20)


class SettingsPatch(Body):
    changes: dict
    expected_revision: int


class Rollback(Body):
    revision: int
    expected_revision: int


TOOLS_LLM_PROMPT = "Return JSON {\"tags\": [3 short persona tags], \"opening\": \"one Hinglish line\"} for this seller."


class CallEvent(Body):
    kind: str = Field(pattern="^(agent|start|end|switch|language|tone|meeting|tool|reply|note)$")
    label: str = Field(max_length=160)
    detail: str = Field(default="", max_length=600)
    origin: str | None = Field(default=None, pattern="^(internal|llm|live_api|elastic)$")


class PromptEdit(Body):
    text: str = Field(max_length=60000)


class ApiTest(Body):
    seller_id: str = Field(default="syn-91000101", max_length=40)


class VoiceSession(Body):
    seller_id: str | None = Field(default=None, max_length=40)
    glid: str | None = Field(default=None, max_length=20)
    run_id: str | None = Field(default=None, max_length=40)
    direction: str = Field(default="seller_outbound", pattern="^(seller_outbound|buyer_inbound)$")
    provider: str = Field(default="other", max_length=40)
    room: str | None = Field(default=None, max_length=120)


class SarvamTry(Body):
    tool: str = Field(max_length=60)
    text: str = Field(default="", max_length=600)
    language: str = Field(default="hi-IN", max_length=8)
    model: str | None = Field(default=None, max_length=30)
    speaker: str | None = Field(default=None, max_length=60)


class AgentChat(Body):
    seller_id: str = Field(default="syn-91000101", max_length=40)
    text: str = Field(min_length=1, max_length=500)
    interaction_id: str | None = Field(default=None, max_length=120)
    end: bool = False


class BuyerAgentChat(Body):
    buyer_call_id: str = Field(default="", max_length=40)
    text: str = Field(min_length=1, max_length=500)
    interaction_id: str | None = Field(default=None, max_length=120)
    end: bool = False


class CallbackPaste(Body):
    callback_url: str = Field(min_length=10, max_length=2000)


USED_IN = {
    "sarvam_tools_llm_complete": ("In the main flow", "Persona aggregation, locality extraction, every bot turn, the simulated seller and Buyer VANI run on sarvam-105b-conversations."),
    "sarvam_tools_tts_speak": ("In the main flow", "Teleprompter ▶ and 🔊 Voice play lines in the persona's voice — Bulbul v4 flash (default) or v3, chosen in ⚙ Engines."),
    "sarvam_tools_transliterate": ("In the main flow", "Romanised Hinglish lines are transliterated to Devanagari before speech so Bulbul pronounces them naturally."),
    "sarvam_tools_translate": ("In the main flow", "Teleprompter 🌐 translates a line into the seller's language (Telugu, Tamil, Bengali …)."),
    "sarvam_tools_identify_language": ("In the main flow", "Confirms a language switch from what the seller actually said — never from geography."),
    "sarvam_tools_stt_transcribe": ("In the main flow", "Speak as the seller: your microphone clip is transcribed and treated as the seller's turn."),
    "sarvam_tools_stt_realtime": ("In the main flow", "🎙 Talk live (call workspace, buyer desk): Saaras v4 realtime streaming with live words, end-of-turn on a pause and barge-in."),
    "sarvam_tools_tts_stream": ("In the main flow", "Streamed speech (default): Bulbul's WebSocket — the first sound plays ≈0.3 s after the request instead of ≈1.5 s."),
    "sarvam_tools_stt_batch_submit": ("Recording studio", "Batch transcription with diarization of uploaded calls."),
    "sarvam_tools_stt_batch_status": ("Recording studio", "Polls diarization jobs."),
    "sarvam_tools_stt_batch_download": ("Recording studio", "Fetches speaker-segmented transcripts for review."),
    "sarvam_tools_text_analytics": ("Retired by Sarvam", "Replaced by structured extraction with the chat model."),
    "sarvam_tools_vision_digitise": ("Planned", "Read a GST certificate or visiting card to confirm the meeting address."),
    "sarvam_tools_vision_extract": ("Planned", "Extract address fields from an uploaded document."),
    "sarvam_tools_vision_job_status": ("Planned", "Status for document jobs."),
    "sarvam_tools_pronunciation_list": ("Planned", "Brand dictionary so 'IndiaMART', 'TrustSEAL' are pronounced correctly."),
    "sarvam_tools_pronunciation_get": ("Planned", "Read a pronunciation entry."),
    "sarvam_tools_pronunciation_create": ("Planned", "Add a brand/product pronunciation."),
    "sarvam_tools_pronunciation_delete": ("Planned", "Remove a pronunciation entry."),
    "sarvam_tools_document_translate": ("Planned", "Translate the executive handoff note for regional teams."),
    "sarvam_tools_document_translate_status": ("Planned", "Status for document translation."),
    "sarvam_tools_dubbing_submit": ("Planned", "Dub a recorded pitch into other languages for training."),
    "sarvam_tools_dubbing_status": ("Planned", "Status for dubbing jobs."),
    "sarvam_tools_voice": ("Composite", "Speak + listen helper used in harness tests."),
    "sarvam_tools_dub": ("Composite", "Dub helper used in harness tests."),
    "sarvam_tools_localize": ("Composite", "Translate + transliterate helper — same chain as the teleprompter."),
    "sarvam_tools_recall": ("Composite", "Recall helper for previous outputs."),
    "sarvam_code_languages": ("Build time", "Validated the language list offered in Admin."),
    "sarvam_code_speakers": ("Build time", "Validated the Bulbul v3 voices and their Bulbul v4 flash counterparts offered to personas."),
    "sarvam_code_api_reference": ("Build time", "API reference used while building."),
    "sarvam_code_pricing": ("Build time", "Sized the daily budgets."),
    "sarvam_code_snippet": ("Build time", "Request snippets used for the integrations."),
    "sarvam_code_recommend_model": ("Build time", "Chose sarvam-105b-conversations for low-latency JSON."),
    "sarvam_code_validate_request": ("Build time", "Validated request bodies."),
    "sarvam_tools_set_api_key": ("Maintenance", "Key set server-side for the designated organisation only."),
    "sarvam_tools_upgrade": ("Maintenance", "Package pinned at 0.2.12; upgrades are deliberate."),
}


def genome_router(get: Callable[[], Any], is_operator: Callable[[Request], bool], lab: Callable[[], Any] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v5", tags=["v5"])
    buckets: dict[str, list[float]] = defaultdict(list)
    tickets = Tickets()

    def svc():
        g = get()
        if g is None:
            raise HTTPException(503, "V5 is starting.")
        return g

    def visitor(request: Request) -> str:
        return hashlib.sha256((request.cookies.get("persona_v4_owner") or (request.client.host if request.client else "x")).encode()).hexdigest()[:16]

    def limit(request: Request, kind: str, per_minute: int, window: float = 60) -> None:
        key = f"{kind}:{visitor(request)}"
        now = time.time()
        buckets[key] = [t for t in buckets[key] if now - t < window]
        if len(buckets[key]) >= per_minute:
            raise HTTPException(429, "Too many requests — try again in a minute." if window <= 60 else "Hourly limit reached — try again later.")
        buckets[key].append(now)

    def scope_of(request: Request) -> str:
        return "operator" if is_operator(request) else "public"

    def bad_choice(error: ValueError) -> HTTPException:
        return HTTPException(422, str(error))

    def require_operator(request: Request) -> None:
        if not is_operator(request):
            raise HTTPException(401, "Sign in with the PM password for this action.")

    def can_see(request: Request, seller_id: str) -> None:
        if not roster.is_public(seller_id) and not is_operator(request):
            raise HTTPException(401, "Private sellers need the PM password. Synthetic sellers are open to everyone.")

    def get_run(request: Request, run_id: str) -> dict:
        run = svc().store.get("runs", run_id)
        if not run:
            raise HTTPException(404, "Run not found.")
        can_see(request, run["seller_id"])
        return run

    # ---- status, floor, search ----------------------------------------------------------------

    @router.get("/status")
    async def status(request: Request):
        g = svc()
        return {"version": g.settings.get("app.version"), "elastic": {"status": g.elastic.status, "outbox": g.store.outbox_size()},
                "sarvam": {"configured": g.sarvam.configured, "model": g.settings.get("llm.model"), "budget": g.sarvam.budget()},
                "redash": {"mode": g.registry.get("redash.hotlead").mode}, "operator": is_operator(request),
                "voice_plugin": {"connected": len(g.voice.live_tiles())}, "origins": ORIGINS,
                "disclosure_mode": g.settings.get("app.disclosure_mode"), "bot_name": g.settings.get("app.bot_name")}

    @router.get("/floor")
    async def get_floor(request: Request, flow: str = "seller"):
        g = svc()
        if flow == "buyer":
            return floor.buyer_floor(g.settings, g.voice.live_tiles("buyer_inbound"))
        return floor.seller_floor(g.settings, g.store, g.voice.live_tiles(), operator=is_operator(request), hints_for=g._hints)

    @router.get("/floor/tile")
    async def tile(request: Request, seller_id: str):
        can_see(request, seller_id)
        try:
            roster.glid_of(seller_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        hints = svc()._hints(roster.glid_of(seller_id)) if seller_id.startswith("real-") else None
        t = floor.tile_for(seller_id, elapsed=0.0, spt=float(svc().settings.get("floor.seconds_per_turn", 4.5)), found=True, hints=hints,
                           store=svc().store)
        return t

    @router.get("/search")
    async def search(request: Request, q: str = ""):
        g = svc()
        q = q.strip()[:80]
        operator = is_operator(request)
        results = roster.search_public(q)
        glid = re.sub(r"^(syn|gen|real)-", "", q)
        if re.fullmatch(r"[0-9]{3,15}", glid):
            hero = roster.heroes().get(glid)
            if not hero:
                # A GLID that belongs to a real seller (approved for live APIs or in the supplied dataset) never gets an
                # invented mock profile — owner, 2026-10-10: 23456789 showed up as a generated "Khan Traders".
                in_dataset = bool(g.v4_store and g.v4_store.get("sellers", "real-" + glid))
                real = in_dataset or g._approved(glid)
                if real and operator:
                    sid = "real-" + glid
                    item = {**roster.summary(roster.profile(sid, g._hints(glid))), "kind": "private",
                            "note": ("Real seller · in the supplied dataset" if in_dataset else "Real seller") + (" · live APIs (prod: any GLID)" if g.glid_access() == "prod" else " · approved for live APIs" if g._approved(glid) else "")}
                    ident = floor.confirmed_identity(g.store, sid)
                    if ident:
                        item.update({k: v for k, v in ident.items() if k in ("company_name", "contact_name", "city") and v}, identity="confirmed")
                        item["note"] += " · identity from the live company record"
                    results.insert(0, item)
                elif real:
                    results.insert(0, {"seller_id": None, "glid": glid, "company_name": "Real IndiaMART seller", "contact_name": "", "city": "",
                                       "kind": "locked", "note": "Sign in to see this seller's live details — nothing is invented for a real GLID."})
                else:
                    gen = roster.profile("gen-" + glid)
                    results.insert(0, {**roster.summary(gen), "kind": "generated",
                                       "note": "No seller with this GLID in our data — a new production-shaped mock seller was generated."})
        elif operator and q and g.v4_store:
            for item in g.v4_store.sellers(q, "", 1, 6, "private")["items"]:
                gl = item["id"].removeprefix("real-")
                results.append({**roster.summary(roster.profile(item["id"], {"city": item.get("city"), "category": item.get("category")})),
                                "kind": "private", "note": f"Supplied dataset · {item.get('city')} · {item.get('category')}"})
        return {"items": results[:12], "operator": operator}

    # ---- runs ---------------------------------------------------------------------------------

    @router.post("/runs", status_code=202)
    async def create_run(body: RunCreate, request: Request):
        g = svc()
        can_see(request, body.seller_id)
        operator = is_operator(request)
        limit(request, "runs", int(g.settings.get("runs.operator_runs_per_minute" if operator else "runs.public_runs_per_minute", 20)))
        try:
            run = g.orchestrator.create(body.seller_id, flow=body.flow, origin=body.origin, operator=operator)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"run_id": run["id"], "seller": run["seller"], "stream": f"/api/v5/runs/{run['id']}/stream"}

    @router.get("/runs/{run_id}")
    async def read_run(run_id: str, request: Request):
        run = get_run(request, run_id)
        return svc().orchestrator.public_run(run, is_operator(request)) | {"events": svc().store.events(run_id)}

    @router.get("/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request, after: int = 0):
        get_run(request, run_id)
        g = svc()
        last = int(request.headers.get("last-event-id") or after or 0)

        async def gen():
            nonlocal last
            started = time.time()
            idle_since = None
            while time.time() - started < 90:
                if await request.is_disconnected():
                    return
                events = g.store.events(run_id, last)
                for e in events:
                    last = e["seq"]
                    yield f"id: {e['seq']}\nevent: step\ndata: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
                run = g.store.get("runs", run_id) or {}
                if run.get("status") != "running" and not events:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since > 4:  # let async persistence acks arrive
                        yield f"event: end\ndata: {json.dumps({'status': run.get('status')})}\n\n"
                        return
                if not events:
                    await g.orchestrator.wait(run_id, 1.0)
            yield "event: end\ndata: {\"status\": \"timeout\"}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def public_api(d: dict | None, operator: bool) -> dict | None:
        """Registry descriptor for the inspector; key fingerprints and the gateway URL are for signed-in users only."""
        if not d or operator:
            return d
        hide = lambda x: {"name": x["name"], "configured": x["configured"]} if x else None  # noqa: E731
        return {**d, "secret": hide(d.get("secret")), "secrets": [hide(x) for x in d.get("secrets") or []],
                "sent_via": "configured gateway" if d.get("sent_via") else None}

    @router.get("/runs/{run_id}/nodes/{node}")
    async def node(run_id: str, node: str, request: Request):
        run = get_run(request, run_id)
        result = (run.get("results") or {}).get(node)
        g = svc()
        api_id = node.split(":")[0]
        api = g.registry.describe(api_id) if api_id in {a.id for a in g.registry.all()} else None
        if not result and not api:
            raise HTTPException(404, "No receipt for this node in this run.")
        # An API this run never needed (e.g. no purchased leads to expand) still opens: config + "Hit it now".
        mapping = [f for f in ((run.get("contract") or {}).get("fields") or {}).values() if (f.get("api") or "").split(":")[0] == api_id]
        return {"node": node, "result": result, "api": public_api(api, is_operator(request)), "fields_fed": mapping,
                "registry_revision": g.registry.revision, "can_save": is_operator(request) and bool(api and api.get("editable")),
                "events": [e for e in g.store.events(run_id) if e.get("node") == node or e.get("ref") == node]}

    @router.post("/runs/{run_id}/nodes/{node}/rerun")
    async def rerun(run_id: str, node: str, body: Rerun, request: Request):
        run = get_run(request, run_id)
        g = svc()
        api_id = node.split(":")[0]
        if api_id not in {a.id for a in g.registry.all()}:
            raise HTTPException(422, "Only API nodes can be re-run.")
        limit(request, "rerun", 20)
        seller = g.orchestrator._seller(run["seller_id"])
        values = node_values(run, node, seller)
        if body.url and not host_allowed(body.url):
            raise HTTPException(422, "That host is not on the allow-list (config/genome/allowed_hosts.yaml).")
        override = {k: v for k, v in (("url", body.url), ("params", body.params), ("body", body.body)) if v is not None} or None
        result = await g.executor.call(api_id, seller, values, run_id=run_id, force=True, operator=is_operator(request), override=override)
        run.setdefault("reruns", []).append({"node": node, "at": result["retrieved_at"], "edited": bool(override), "status": result["status"]})
        g.store.put("runs", run_id, run)
        return {"result": result, "label": "Edited request — a fork; the original receipt is unchanged." if override else "New receipt; the original receipt is unchanged."}

    def node_values(run: dict, node: str, seller: dict) -> dict:
        """Placeholder values a node was run with (fan-out keys and chained ids)."""
        from backend.genome.orchestrator import mapping_id
        results = run.get("results") or {}
        api_id, _, key = node.partition(":")
        values: dict[str, Any] = {}
        if api_id == "leads.detail" and key:
            values["offerid"] = key
        if api_id == "lms.messages" and key:
            values["contact_glid"] = key
        if api_id == "company.detail":
            values["alias"] = ((results.get("company.alias") or {}).get("response") or {}).get("URL_DETAIL", {}).get("FREESHOWROOM_ALIAS", "")
        if api_id == "context.getcontext":
            m = mapping_id(results.get("context.global"))
            values.update({"mapping_id": m} if m else {"source_uid": seller["glid"]})
        return values

    # 5.13 (owner: "show in the right logs like elastic data saved etc everything that is being done"): what happens on the
    # call (agent started, persona or language switch, meeting) becomes a run event — on the live log, in the run's trace
    # and queued to Elasticsearch (the events index), like every preparation step.
    @router.post("/runs/{run_id}/call-events")
    async def call_event(run_id: str, body: CallEvent, request: Request):
        run = get_run(request, run_id)
        limit(request, "call-events", 120)
        ev = svc().orchestrator.emit(run, f"call.{body.kind}", body.kind, "ok", body.label[:120], body.detail[:400], origin=body.origin or "internal")
        g = svc()
        return {"seq": ev["seq"], "elastic": {"queued": bool(g.elastic.enabled and g.settings.get("elastic.persist_responses", True)),
                                               "index": g.elastic.alias("events")}}

    @router.get("/runs/{run_id}/brief")
    async def run_brief(run_id: str, request: Request):
        """The PM's pre-call brief (VANI Persona Studio structure) for a finished run."""
        from backend.genome import brief
        run = get_run(request, run_id)
        if not run.get("contract") or not run.get("persona"):
            raise HTTPException(409, "The brief is ready once preparation finishes.")
        g = svc()
        count = lambda factor, value: len(g.persona_index.local_search(factor, value, None))  # noqa: E731
        out = brief.build(run, similar_count=count)
        seller = out.get("seller") or {}
        if str(seller.get("company_name") or "").startswith("Private seller"):
            # This run could not reach the company record (e.g. an expired key): show the identity an earlier live run
            # confirmed, labelled with its date — real data, never invented.
            ident = floor.confirmed_identity(g.store, run["seller_id"])
            if ident:
                seller.update({k: v for k, v in ident.items() if k in ("company_name", "contact_name", "city") and v},
                              identity="earlier_run", identity_note=f"Identity from the live company record of an earlier run ({str(ident.get('confirmed_at') or '')[:10]})")
        # 5.13: the talking points every calling agent gets (competitors, market, payment, buyers), shown on the workspace.
        from backend.genome import agent_context
        if run["seller_id"].startswith("real-"):
            ctx = g.sarvam_voice.real_context(run)
            out["talking_points"] = {k: ctx.get(k, "") for k in ("call_reason", "payment_status", "market_snapshot", "competitors", "buyer_activity", "product_overview")}
        else:
            out["talking_points"] = agent_context.profile_facts(roster.profile(run["seller_id"]))
        return out

    @router.get("/runs/{run_id}/fields")
    async def fields(run_id: str, request: Request):
        run = get_run(request, run_id)
        c = run.get("contract") or {}
        return {"fields": c.get("fields"), "groups": c.get("groups"), "completeness": c.get("completeness"),
                "classification": c.get("classification"), "origins": ORIGINS}

    # ---- live call ----------------------------------------------------------------------------

    @router.post("/calls")
    async def start_call(body: CallStart, request: Request):
        run = get_run(request, body.run_id)
        if run.get("status") not in ("completed", "degraded"):
            raise HTTPException(409, "Context is still being prepared.")
        return svc().conversation.start(run, body.seed_turns, is_operator(request), opening=body.opening)

    def get_call(request: Request, call_id: str) -> tuple[dict, dict]:
        g = svc()
        call = g.conversation.get(call_id)
        if not call:
            raise HTTPException(404, "Call not found.")
        run = get_run(request, call["run_id"])
        return call, run

    @router.get("/calls/{call_id}")
    async def read_call(call_id: str, request: Request):
        return get_call(request, call_id)[0]

    # ---- speech started inside the turn request (5.4) -------------------------------------------------
    # The page used to get the reply, then ask for its speech (a second round trip, a transliteration call and a fresh
    # socket). With "speak", the socket to Bulbul opens while the LLM writes, and the reply comes back with a link that is
    # already streaming.

    def speak_model(g, opts: SpeakOpts | None) -> str | None:
        if not opts:
            return None
        try:
            return engines.check("tts_model", opts.model or g.settings.get("engines.tts_model", "bulbul:v4-flash"))
        except ValueError as error:
            raise bad_choice(error) from error

    async def discard(task) -> None:
        if not task:
            return
        task.cancel()
        try:
            ws = await task
            await ws.close()
        except BaseException:  # noqa: BLE001 — cancelled or failed to connect; nothing to close
            pass

    async def line_speech(g, scope: str, turn_i: int, text: str, language: str, speaker: str, pace: float, model: str, pre,
                          fmt: str = "mp3") -> dict | None:
        """Start one line's streamed speech now; returns its short-lived link."""
        try:
            first = g.sarvam.tts_request(text, language, speaker, pace, model, fmt)  # validated voice and pace
            g.sarvam.spend(scope)
        except (ValueError, BudgetExceeded, SarvamError):
            await discard(pre)
            return None
        convert = language == "hi-IN" and not re.search(r"[ऀ-ॿ]", text) and engines.needs_transliteration(
            model, g.settings.get("engines.transliterate", "auto"))

        async def chunks():
            spoken = text
            if convert:
                try:
                    spoken = (await g.sarvam.transliterate(text, "hi-IN", scope))["text"] or text
                except (SarvamError, BudgetExceeded):
                    pass
            ws = None
            if pre:
                try:
                    ws = await pre
                except Exception:  # noqa: BLE001 — fall back to a fresh socket
                    ws = None
            async for chunk in g.sarvam.tts_stream({**first, "text": spoken[:1500]}, scope, ws=ws):
                yield chunk

        buffer = AudioBuffer().start(chunks())
        token = tickets.issue("tts", {"req": first, "scope": scope, "buffer": buffer}, ttl=90, uses=2)
        return {"turn": turn_i, "url": f"/api/v5/speak/stream/{token}.{first['format']}", "format": first["format"], "speaker": first["speaker"],
                "pace": first["pace"], "model": model,
                "transport": "stream", "prestarted": True, "preopened": bool(pre), "transliterated": convert}

    def bot_voice(opts: SpeakOpts, persona_voice: str) -> str:
        return opts.voice if opts.voice and opts.voice != "auto" else persona_voice

    @router.post("/calls/{call_id}/turn")
    async def turn(call_id: str, body: Turn, request: Request):
        call, run = get_call(request, call_id)
        limit(request, "turns", 40)
        g = svc()
        model = speak_model(g, body.speak)
        pre = asyncio.create_task(g.sarvam.tts_connect(model)) if model else None
        try:
            out = await g.conversation.seller_turn(call, run, body.text, source="typed")
        except ValueError as error:
            await discard(pre)
            raise HTTPException(409, str(error)) from error
        if model:
            last = out["call"]["transcript"][-1]
            d = out["call"]["delivery"]
            item = await line_speech(g, scope_of(request), last["i"], last["text"], d.get("language", "hi-IN"),
                                     bot_voice(body.speak, d.get("voice", "ritu")), float(d.get("pace", 1)), model, pre, body.speak.format) if last["who"] == "bot" else None
            if not item and last["who"] != "bot":
                await discard(pre)
            out["speech"] = [item] if item else []
        return out

    @router.post("/calls/{call_id}/auto")
    async def auto(call_id: str, request: Request, body: AutoOpts | None = None):
        call, run = get_call(request, call_id)
        limit(request, "turns", 40)
        g = svc()
        opts = body.speak if body else None
        model = speak_model(g, opts)
        pres = [asyncio.create_task(g.sarvam.tts_connect(model)) for _ in range(2)] if model else [None, None]
        try:
            out = await g.conversation.auto_turn(call, run)
        except (ValueError, SarvamError, BudgetExceeded) as error:
            for p in pres:
                await discard(p)
            raise HTTPException(409, str(error)) from error
        if model:
            d, lines = out["call"]["delivery"], out["call"]["transcript"]
            voice = bot_voice(opts, d.get("voice", "ritu"))
            speech = []
            seller = next((t for t in reversed(lines) if t["who"] == "seller" and t.get("source") == "simulated_seller"), None)
            bot = lines[-1] if lines and lines[-1]["who"] == "bot" else None
            if seller:
                speech.append(await line_speech(g, scope_of(request), seller["i"], seller["text"], d.get("language", "hi-IN"),
                                                engines.contrast_voice(model, engines.voice_for(model, voice)), 1.0, model, pres[0], opts.format))
            else:
                await discard(pres[0])
            if bot:
                speech.append(await line_speech(g, scope_of(request), bot["i"], bot["text"], d.get("language", "hi-IN"), voice,
                                                float(d.get("pace", 1)), model, pres[1], opts.format))
            else:
                await discard(pres[1])
            out["speech"] = [x for x in speech if x]
        return out

    # ---- speech: engines, speak (REST / streamed), translate, listen (clip / realtime) -------------------

    @router.get("/engines")
    async def engines_catalogue():
        """Every speech and bot choice with the Admin defaults — what each place's picker renders (decision D25)."""
        g = svc()
        payal = g.sarvam_voice.live_config()
        return {**engines.catalogue(g.settings), "payal": {k: payal[k] for k in ("available", "reason", "app_id")}}

    async def prepared_speech(g, body: Speak, scope: str) -> tuple[str, dict | None]:
        """Romanised Hindi becomes Devanagari first when the policy says so (Admin → Voice & engines: only Bulbul v3 by
        default — v4 reads romanised Hinglish correctly and skipping the call saves ≈0.7 s)."""
        text, translit = body.text, None
        model = body.model or g.settings.get("engines.tts_model", "bulbul:v4-flash")
        convert = engines.needs_transliteration(model, g.settings.get("engines.transliterate", "auto"))
        if convert and body.transliterate and body.language == "hi-IN" and not re.search(r"[ऀ-ॿ]", text):
            translit = await g.sarvam.transliterate(text, "hi-IN", scope)
            text = translit["text"] or text
        return text, translit

    @router.post("/speak")
    async def speak(body: Speak, request: Request):
        g = svc()
        limit(request, "speak", 20)
        scope = scope_of(request)
        try:
            text, translit = await prepared_speech(g, body, scope)
            audio = await g.sarvam.tts(text, body.language, body.speaker, body.pace, scope, body.model)
            return {**audio, "spoken_text": text, "transliterated": bool(translit), "transliterate_ms": (translit or {}).get("ms")}
        except ValueError as error:
            raise bad_choice(error) from error
        except (SarvamError, BudgetExceeded) as error:
            raise HTTPException(503, str(error)) from error

    @router.post("/speak/stream")
    async def speak_stream(body: Speak, request: Request):
        """Streamed speech: returns an audio URL that plays while Bulbul is still speaking (first sound ≈0.3 s)."""
        g = svc()
        limit(request, "speak", 20)
        scope = scope_of(request)
        try:
            text, translit = await prepared_speech(g, body, scope)
            req = g.sarvam.tts_request(text, body.language, body.speaker, body.pace, body.model, body.format)
            g.sarvam.spend(scope)
        except ValueError as error:
            raise bad_choice(error) from error
        except (SarvamError, BudgetExceeded) as error:
            raise HTTPException(503, str(error)) from error
        # Bulbul starts now, so the first chunk is usually waiting by the time the browser requests the link.
        buffer = AudioBuffer().start(g.sarvam.tts_stream(req, scope))
        token = tickets.issue("tts", {"req": req, "scope": scope, "buffer": buffer}, ttl=60, uses=2)  # Safari may fetch twice
        return {"url": f"/api/v5/speak/stream/{token}.{req['format']}", "format": req["format"],
                "content_type": "audio/L16;rate=24000" if req["format"] == "pcm" else "audio/mpeg", "speaker": req["speaker"], "pace": req["pace"],
                "model": req["model"], "language": body.language, "transport": "stream", "spoken_text": text,
                "transliterated": bool(translit), "transliterate_ms": (translit or {}).get("ms")}

    @router.get("/speak/stream/{token}.mp3")
    @router.get("/speak/stream/{token}.pcm")
    async def speak_stream_audio(token: str):
        p = tickets.take(token, "tts")
        if not p:
            raise HTTPException(404, "This audio link has expired — press play again.")
        chunks = p["buffer"].read()
        try:
            first = await chunks.__anext__()  # fail as a proper error before any audio bytes are sent
        except StopAsyncIteration as error:
            raise HTTPException(503, str(p["buffer"].error or "No audio came back.")) from error

        async def body():
            yield first
            async for chunk in chunks:
                yield chunk

        pcm = p["req"].get("format") == "pcm"
        return StreamingResponse(body(), media_type="audio/L16;rate=24000;channels=1" if pcm else "audio/mpeg",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.post("/translate")
    async def translate(body: Translate, request: Request):
        limit(request, "speak", 20)
        try:
            return await svc().sarvam.translate(body.text, body.target, scope_of(request), model=body.model)
        except ValueError as error:
            raise bad_choice(error) from error
        except (SarvamError, BudgetExceeded) as error:
            raise HTTPException(503, str(error)) from error

    @router.post("/listen")
    async def listen(request: Request, file: UploadFile = File(...), language: str = Form("unknown"),
                     model: str = Form(""), mode: str = Form("")):
        limit(request, "speak", 20)
        content = await file.read()
        if len(content) > 3 * 1024 * 1024:
            raise HTTPException(413, "Clip too large (max 3 MB / ~25 s).")
        try:
            return await svc().sarvam.stt(content, file.filename or "clip.webm", file.content_type or "audio/webm",
                                          scope_of(request), language, model or None, mode or None)
        except ValueError as error:
            raise bad_choice(error) from error
        except (SarvamError, BudgetExceeded) as error:
            raise HTTPException(503, str(error)) from error

    @router.post("/listen/ticket")
    async def listen_ticket(body: ListenTicket, request: Request):
        """A single-use ticket for one realtime listening session (rate-limited; one Sarvam call from the budget)."""
        g = svc()
        rt = g.settings.get("engines.realtime", {}) or {}
        limit(request, "listen-rt", int(rt.get("sessions_per_visitor_per_minute", 6)))
        try:
            model = engines.check("stt_realtime_model", body.model or g.settings.get("engines.stt_realtime_model", "saaras:v4"))
            mode = engines.check("stt_mode", body.mode or g.settings.get("engines.stt_mode", "codemix"))
        except ValueError as error:
            raise bad_choice(error) from error
        language = body.language if re.fullmatch(r"[a-z]{2,3}-IN|auto", body.language or "") else "hi-IN"
        scope = scope_of(request)
        try:
            g.sarvam.spend(scope)
        except BudgetExceeded as error:
            raise HTTPException(429, str(error)) from error
        if not g.sarvam.configured:
            raise HTTPException(503, "Designated Sarvam key is not configured.")
        terms = g.settings.get("engines.buyer_keyterms" if body.place == "buyer" else "engines.keyterms") or []
        token = tickets.issue("listen", {"model": model, "mode": mode, "language": language, "scope": scope, "keyterms": terms}, ttl=30)
        return {"ticket": token, "path": "/api/v5/listen/realtime", "model": model, "mode": mode, "language": language,
                "max_seconds": rt.get("max_seconds", 90), "sample_rate": 16000, "frame_ms": 100}

    WS_ORIGIN = re.compile(r"^https://((rig|vps)-)?sellergenome\.(lehana\.in|aidhunik\.com)$|^http://(localhost|127\.0\.0\.1|testserver)(:\d+)?$")

    @router.websocket("/listen/realtime")
    async def listen_realtime(websocket: WebSocket, ticket: str = ""):
        """Microphone PCM in, Sarvam realtime transcription events out. The ticket carries the validated choices."""
        origin = websocket.headers.get("origin")
        if origin and not WS_ORIGIN.match(origin):
            await websocket.close(code=1008)
            return
        p = tickets.take(ticket, "listen")
        if not p:
            await websocket.close(code=1008)
            return
        g = svc()
        await websocket.accept()
        await relay_realtime_stt(websocket, g.sarvam, g.settings, p, p["scope"])
        try:
            await websocket.close()
        except RuntimeError:
            pass

    # ---- meetings -----------------------------------------------------------------------------

    @router.post("/meetings")
    async def create_meeting(body: MeetingCreate, request: Request):
        run = get_run(request, body.run_id)
        g = svc()
        call = g.conversation.get(body.call_id) if body.call_id else None
        try:
            m = meetings.plan(g.settings, run, call, body.mode, body.address_kind, body.address_text, body.start, body.note)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        g.store.put("meetings", m["id"], m)
        return m

    @router.get("/meetings/{meeting_id}.ics")
    async def meeting_ics(meeting_id: str, request: Request):
        m = svc().store.get("meetings", meeting_id)
        if not m:
            raise HTTPException(404, "Meeting not found.")
        can_see(request, m["seller_id"])
        return Response(meetings.ics(m), media_type="text/calendar",
                        headers={"Content-Disposition": f'attachment; filename="{meeting_id}.ics"'})

    # ---- persona -------------------------------------------------------------------------------

    @router.get("/persona/{seller_id}")
    async def persona(seller_id: str, request: Request):
        can_see(request, seller_id)
        g = svc()
        record = g.store.get("personas", seller_id)
        if not record:
            raise HTTPException(404, "No persona yet — barge into a call first.")
        factors = [d for d in g.store.list("factors", 3000) if d.get("seller_id") == seller_id]
        return {"record": record, "factors": factors,
                "suggestions": [s for s in g.store.list("suggestions", 500) if s["seller_id"] == seller_id]}

    @router.post("/persona/{seller_id}/suggest")
    async def suggest(seller_id: str, body: Suggestion, request: Request):
        require_operator(request)
        return svc().persona_index.suggest(seller_id, body.factor, body.value, body.reason, body.author)

    @router.post("/persona/{seller_id}/apply")
    async def apply(seller_id: str, body: Suggestion, request: Request):
        require_operator(request)
        try:
            return svc().persona_index.apply(seller_id, body.factor, body.value, body.reason, body.author, body.suggestion_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    # ---- persona index explorer ----------------------------------------------------------------

    @router.get("/genome/search")
    async def genome_search(request: Request, factor: str = "", value: str = "", q: str = ""):
        g = svc()
        scope = None if is_operator(request) else True
        query = g.persona_index.search_query(factor or None, value or None, scope, q or None)
        try:
            data, ms = await g.elastic.search("persona-factors", query)
            hits = [h["_source"] for h in data["hits"]["hits"]]
            total = data["hits"]["total"]["value"]
            source = "elasticsearch"
        except Exception as error:
            hits, ms, total, source = g.persona_index.local_search(factor or None, value or None, scope), 0.0, None, f"sqlite fallback ({type(error).__name__})"
            total = len(hits)
        return {"query": query, "index": g.elastic.alias("persona-factors"), "ms": round(ms, 1), "total": total, "hits": hits, "source": source}

    @router.get("/genome/facets")
    async def genome_facets(request: Request):
        g = svc()
        scope = None if is_operator(request) else True
        query = g.persona_index.facets_query(scope)
        try:
            data, ms = await g.elastic.search("persona-factors", query)
            return {"query": query, "ms": round(ms, 1), "aggregations": data.get("aggregations"), "source": "elasticsearch"}
        except Exception as error:
            return {"query": query, "ms": 0, "aggregations": None, "source": f"unavailable ({type(error).__name__})"}

    @router.get("/genome/registry")
    async def genome_registry(request: Request):
        g = svc()
        return {"items": sorted(g.store.list("registry", 500), key=lambda r: -r.get("count", 0)),
                "mapping_index": g.elastic.alias("persona-factors"), "counts": await g.elastic.counts()}

    # ---- buyer desk ----------------------------------------------------------------------------

    @router.post("/buyer/calls")
    async def buyer_start(body: BuyerStart, request: Request):
        g = svc()
        if body.policy == "original" and not is_operator(request) and not body.talk:
            raise HTTPException(401, "The original production prompt is private — sign in to run it.")
        tiles = {t["id"]: t for t in floor.buyer_floor(g.settings)["tiles"]}
        calls = {f"buyer-{c['id']}": c for c in floor.scripts()["buyer"]}
        source = tiles.get(body.tile_id) or ({"call": calls[body.tile_id], "turns": calls[body.tile_id]["turns"]} if body.tile_id in calls else None)
        if not source:
            raise HTTPException(404, "Buyer call not found.")
        call = g.buyer.start({**source, "shown": body.shown}, is_operator(request), body.policy, talk=body.talk)
        call["tile_id"], call["shown"] = body.tile_id, body.shown
        g.store.put("buyer_calls", call["id"], call)
        return call

    @router.get("/buyer/calls/{call_id}")
    async def buyer_read(call_id: str, request: Request):
        call = svc().store.get("buyer_calls", call_id)
        if not call:
            raise HTTPException(404, "Buyer call not found.")
        return call

    @router.post("/buyer/calls/{call_id}/turn")
    async def buyer_turn(call_id: str, body: Turn, request: Request):
        g = svc()
        call = g.store.get("buyer_calls", call_id)
        if not call:
            raise HTTPException(404, "Buyer call not found.")
        limit(request, "turns", 40)
        model = speak_model(g, body.speak)
        pre = asyncio.create_task(g.sarvam.tts_connect(model)) if model else None
        try:
            out = await g.buyer.turn(call, body.text)
        except ValueError as error:
            await discard(pre)
            raise HTTPException(409, str(error)) from error
        if model:
            last = out["call"]["transcript"][-1]
            # VANI on the requirement policy speaks with a male voice (the owner's lines use masculine forms: samajh gaya).
            default_voice = "shubh" if out["call"].get("policy") == "requirement" else "priya"
            item = await line_speech(g, scope_of(request), len(out["call"]["transcript"]) - 1, last["text"], out["call"].get("language", "hi-IN"),
                                     bot_voice(body.speak, default_voice), 1.0, model, pre, body.speak.format) if last.get("who") == "bot" else None
            if not item and last.get("who") != "bot":
                await discard(pre)
            out["speech"] = [item] if item else []
        return out

    @router.get("/buyer/prompt")
    async def buyer_prompt(request: Request, flow: str = "new_requirement"):
        require_operator(request)
        from backend.genome.buyer import FLOW_RANGES, render
        g = svc()
        original = g.buyer.original_prompt()
        if not original:
            raise HTTPException(503, "Original prompt is not mounted on this server.")
        a, b = FLOW_RANGES.get(flow, FLOW_RANGES["new_requirement"])
        source = "\n".join(original.splitlines()[a - 1:b])
        rendered = render(source, {"bot_name": g.settings.get("app.bot_name"), "product_name": "Industrial Air Compressor", "is_enrich": "0",
                                   "ast_seller_pns": "", "buyer_name": "", "updated_buyer_name": "", "buyer_city": "",
                                   "quantity_unit_options": '["Piece"]', "specification_options": '{"Power":["10 HP"]}', "lead_type": "LIVE",
                                   "triangulation_response_value": "0", "triangulation_response_city": "", "language_name": "hindi"})
        return {"flow": flow, "lines": [a, b], "source_chars": len(source), "rendered_chars": len(rendered),
                "approx_tokens": len(rendered) // 4, "rendered_preview": rendered[:6000]}

    # ---- admin ---------------------------------------------------------------------------------

    @router.get("/admin/apis")
    async def admin_apis(request: Request):
        require_operator(request)
        g = svc()
        stats = g.store.hit_stats(time.time() - 86400)
        items = []
        for api in g.registry.all():
            d = g.registry.describe(api.id)
            d["hits_24h"] = stats.get(api.id, {}).get("count", 0)
            last = g.store.hits(api.id, 1)
            d["last_hit"] = last[0] if last else None
            items.append(d)
        internal = [{"id": k, "hits_24h": v["count"]} for k, v in stats.items() if k.startswith("sarvam.")]
        return {"revision": g.registry.revision, "items": items, "internal": internal, "history": g.registry.history()}

    @router.patch("/admin/apis/{api_id}")
    async def admin_patch_api(api_id: str, body: ApiPatch, request: Request):
        require_operator(request)
        try:
            return svc().registry.update(api_id, body.changes, body.expected_revision)
        except KeyError as error:
            raise HTTPException(404, "Unknown API.") from error
        except ValueError as error:
            raise HTTPException(409 if "reload" in str(error) else 422, str(error)) from error

    @router.post("/admin/apis/{api_id}/reset")
    async def admin_reset_api(api_id: str, body: Rollback, request: Request):
        require_operator(request)
        try:
            return svc().registry.reset(api_id, body.expected_revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.put("/admin/apis/{api_id}/secret")
    async def admin_secret(api_id: str, body: SecretPut, request: Request):
        require_operator(request)
        g = svc()
        api = g.registry.get(api_id)
        if not api.secret_name:
            raise HTTPException(422, "This API has no key.")
        return g.settings.set_secret(api.secret_name, body.value)

    def key_names() -> list[str]:
        g = svc()
        names = {a.secret_name for a in g.registry.all() if a.secret_name and a.kind == "http"} | {n for a in g.registry.all() for n in referenced_secrets(a)}
        return sorted(names) + ["SARVAM_V4_API_KEY", "SARVAM_VOICE_AGENTS_API_KEY", "VOICE_PLUGIN_KEY"]

    @router.get("/admin/keys")
    async def admin_keys(request: Request):
        """Every key the APIs use: where it comes from, fingerprint, JWT expiry, and which APIs need it."""
        require_operator(request)
        g = svc()
        used: dict[str, list[str]] = {}
        for a in g.registry.all():
            for n in ([a.secret_name] if a.secret_name else []) + referenced_secrets(a):
                used.setdefault(n, []).append(a.id)
        used.setdefault("SARVAM_V4_API_KEY", ["Sarvam model APIs: persona LLM, speech, translation (designated org only)"])
        used.setdefault("SARVAM_VOICE_AGENTS_API_KEY", ["Sarvam Voice Agents REST: browser voice calls and analytics for Payal (needs the workspace id)"])
        return {"items": [{**g.settings.secret_status(n), "used_by": used.get(n, ["voice plug-in"])} for n in key_names()]}

    @router.post("/admin/secrets/bulk")
    async def admin_secrets_bulk(body: SecretBulk, request: Request):
        """One key for every API (owner, 2026-10-10): save the same value into each ticked IndiaMART key slot. Sarvam and
        plug-in keys are never overwritten this way."""
        require_operator(request)
        allowed = [n for n in key_names() if n not in ("SARVAM_V4_API_KEY", "SARVAM_VOICE_AGENTS_API_KEY", "VOICE_PLUGIN_KEY")]
        bad = [n for n in body.names if n not in allowed]
        if bad:
            raise HTTPException(422, f"Not an IndiaMART API key: {', '.join(bad)}")
        g = svc()
        return {"saved": [g.settings.set_secret(n, body.value.strip()) | {"name": n} for n in dict.fromkeys(body.names)]}

    @router.put("/admin/secrets/{name}")
    async def admin_named_secret(name: str, body: SecretPut, request: Request):
        require_operator(request)
        if name not in key_names():
            raise HTTPException(422, "Unknown secret.")
        return svc().settings.set_secret(name, body.value.strip())

    @router.post("/admin/gateway/check")
    async def admin_gateway_check(request: Request):
        """Ask the gateway which paths it forwards and whether their hosts answer (TCP). Never returns its sample keys."""
        require_operator(request)
        g = svc()
        base = gateway_base(g.settings)
        if not base:
            return {"ok": False, "error": "Gateway is switched off or has no URL."}
        if not host_allowed(base):
            return {"ok": False, "error": "Gateway host is not allow-listed."}
        headers = {str(k): str(v) for k, v in (g.settings.get("upstream.gateway_headers", {}) or {}).items()}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                catalog = (await client.get(f"{base}/_portal/catalog", headers=headers)).json()
                try:
                    conn = (await client.get(f"{base}/_portal/connectivity", headers=headers)).json()
                except (httpx.HTTPError, ValueError):
                    conn = []
        except (httpx.HTTPError, ValueError) as error:
            return {"ok": False, "base": base, "error": f"{type(error).__name__}: {str(error)[:160]}", "ms": round((time.monotonic() - started) * 1000)}
        reach = {r.get("id"): r for r in conn if isinstance(r, dict)}
        paths = []
        for a in (catalog.get("apis") or []) if isinstance(catalog, dict) else []:
            r = reach.get(a.get("id"), {})
            paths.append({"id": a.get("id"), "name": a.get("name"), "method": a.get("method"), "path": a.get("path"),
                          "upstream": a.get("upstream"), "reachable": r.get("reachable"), "connect_ms": r.get("connect_ms"),
                          "resolved_from": (a.get("resolution") or {}).get("source")})
        ours = []
        for api in g.registry.all():
            if not api.via_gateway or not api.url:
                continue
            path = urlsplit(api.url.split("{")[0]).path
            match = next((x for x in paths if x["path"] and (path.startswith(x["path"]) or x["path"].startswith(path))), None)
            ours.append({"api": api.id, "label": api.label, "path": path, "gateway_path": match["path"] if match else None,
                         "covered": bool(match), "reachable": match.get("reachable") if match else None})
        return {"ok": True, "base": base, "ms": round((time.monotonic() - started) * 1000), "paths": paths, "ours": ours,
                "note": "Sample keys published by the gateway are never read or returned here."}

    async def test_values(g, api_id: str, seller: dict) -> dict:
        """Admin → Test: run the hops a chained API depends on, so the test is a real request."""
        from backend.genome.orchestrator import mapping_id
        values: dict[str, Any] = {}
        if api_id == "company.detail":
            alias = await g.executor.call("company.alias", seller, force=True, operator=True)
            values["alias"] = ((alias.get("response") or {}).get("URL_DETAIL") or {}).get("FREESHOWROOM_ALIAS", "") if alias["status"] == "ok" else ""
        if api_id == "leads.detail":
            ids = await g.executor.call("leads.purchased", seller, force=True, operator=True)
            values["offerid"] = (parsers.lead_ids(ids.get("response")) or [""])[0]
        if api_id == "market.listing":
            detail = await g.executor.call("company.detail", seller, await test_values(g, "company.detail", seller), force=True, operator=True)
            values.update({"mcatid": parsers.main_mcat(detail.get("response")) or "", "city_slug": parsers.city_slug(seller.get("city"))})
        if api_id == "lms.messages":
            contacts, _ = g.orchestrator._lms_contacts(seller, {})
            values["contact_glid"] = contacts[0] if contacts else ""
        if api_id == "context.getcontext":
            ctx = await g.executor.call("context.global", seller, force=True, operator=True)
            m = mapping_id(ctx)
            values.update({"mapping_id": m} if m else {"source_uid": seller["glid"]})
        return values

    @router.post("/admin/apis/{api_id}/test")
    async def admin_test(api_id: str, body: ApiTest, request: Request):
        require_operator(request)
        g = svc()
        try:
            seller = g.orchestrator._seller(body.seller_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if api_id not in {a.id for a in g.registry.all()}:
            raise HTTPException(404, "Unknown API.")
        return await g.executor.call(api_id, seller, await test_values(g, api_id, seller), force=True, operator=True)

    @router.post("/admin/apis/test-all")
    async def admin_test_all(body: ApiTest, request: Request):
        """Health of every IndiaMART API after a key change (owner, 2026-10-10): each live API called once for an approved
        GLID — no cache, in parallel — with its status, HTTP code, time, the upstream reason and the key it needs."""
        require_operator(request)
        g = svc()
        try:
            seller = g.orchestrator._seller(body.seller_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        keys = {n: g.settings.secret_status(n) for n in key_names()}
        apis = [a for a in g.registry.all() if a.kind in ("http", "redash") and a.mode != "off"]

        async def one(a) -> dict:
            started = time.monotonic()
            try:
                if a.group == "IndiaMART · Buyer" and not a.url:
                    r = {"status": "skipped", "note": "No live endpoint configured (a buyer-call tool; it answers from mocks in buyer calls)."}
                else:
                    values = await test_values(g, a.id, seller)
                    if a.id == "leads.detail" and not values.get("offerid"):
                        r = {"status": "skipped", "note": "Needs an offer id from a purchased lead; this seller has none right now."}
                    else:
                        r = await asyncio.wait_for(g.executor.call(a.id, seller, values, force=True, operator=True),
                                                   timeout=float(a.timeout_seconds or 10) + 5)
            except Exception as error:  # noqa: BLE001 — a test reports, never raises
                r = {"status": "error", "error": type(error).__name__, "note": str(error)[:200]}
            k = keys.get(a.secret_name or "") or {}
            return {"api": a.id, "label": a.label, "status": r.get("status"), "http_status": r.get("http_status"), "origin": r.get("origin"),
                    "ms": round((time.monotonic() - started) * 1000), "note": (r.get("note") or r.get("error") or "")[:200],
                    "key": a.secret_name, "key_expired": k.get("expired"), "key_expires_at": k.get("expires_at")}
        results = await asyncio.gather(*[one(a) for a in apis])
        return {"seller_id": body.seller_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": sum(r["status"] == "ok" for r in results),
                "errors": sum(r["status"] == "error" for r in results), "skipped": sum(r["status"] == "skipped" for r in results), "total": len(results),
                "items": sorted(results, key=lambda r: (r["status"] != "error", r["status"] != "skipped", r["api"]))}

    # Owner, 2026-10-10: every prompt visible in Admin with where it is used, and editable there.
    @router.get("/admin/prompts")
    async def admin_prompts(request: Request):
        require_operator(request)
        return {"items": prompts.items()}

    @router.put("/admin/prompts/{prompt_id}")
    async def admin_prompt_save(prompt_id: str, body: PromptEdit, request: Request):
        require_operator(request)
        if prompt_id not in prompts.BY_ID:
            raise HTTPException(404, "No such prompt.")
        try:
            return prompts.save(prompt_id, body.text, by="operator")
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.delete("/admin/prompts/{prompt_id}")
    async def admin_prompt_reset(prompt_id: str, request: Request):
        require_operator(request)
        if prompt_id not in prompts.BY_ID:
            raise HTTPException(404, "No such prompt.")
        return prompts.reset(prompt_id, by="operator")

    # Owner, 2026-10-10: "i also want details on the screen which agent id is being used, which account on sarvam, its
    # name. debugging is getting difficult for me" — the call page's Debug panel reads this.
    @router.get("/debug/voice")
    async def debug_voice(request: Request):
        g = svc()
        eng = g.settings.get("engines", {}) or {}
        def agent(state: dict, cfg: dict) -> dict:
            a = state.get("agent") or {}
            return {"name": a.get("name"), "app_id": a.get("id"), "version": state.get("version"), "voice": state.get("voice"),
                    "built_at": state.get("built_at"), "languages": state.get("languages"), "live_available": cfg.get("available"),
                    "live_reason": cfg.get("reason")}
        pay_cfg, vani_cfg = g.sarvam_voice.live_config(), g.sarvam_buyer.live_config()
        out = {"version": g.settings.get("app.version"),
               "builtin": {"bot": eng.get("bot"), "llm_model": g.settings.get("llm.model"), "llm_route": "llm.lehana.in → Sarvam",
                           "llm_reasoning": g.settings.get("llm.reasoning_effort"), "tts_model": eng.get("tts_model"), "tts_voice": eng.get("tts_voice"),
                           "tts_transport": eng.get("tts_transport"), "listen": eng.get("listen"), "stt_realtime_model": eng.get("stt_realtime_model"),
                           "stt_model": eng.get("stt_model"), "stt_mode": eng.get("stt_mode"),
                           "vad": {k: (eng.get("realtime") or {}).get(k) for k in ("silence_duration_ms", "threshold", "max_seconds")}},
               "payal": agent(g.sarvam_voice.state(), pay_cfg), "vani": agent(g.sarvam_buyer.state(), vani_cfg),
               "sarvam": {"org_id": pay_cfg.get("org_id"), "workspace_id": pay_cfg.get("workspace_id")}, "operator": is_operator(request)}
        out["default_agent"] = str(g.settings.get("sarvam_voice.default_agent", "builtin") or "builtin")   # Admin: the agent Talk as Seller starts with
        out["manjula_v2"] = {k: g.manjula_v2.state().get(k) for k in ("agent", "version", "built_at")}
        try:  # every agent of the workspace, for the on-screen picker
            out["workspace_agents"] = await g.sarvam_voice.workspace_agents()
            if getattr(g.sarvam_voice, "_agents_error", None):   # 5.13.7: the last good list, while the sign-in is broken
                out["workspace_agents_error"] = f"Showing the last known agents. {g.sarvam_voice._agents_error[:160]}"
        except Exception as error:  # noqa: BLE001 — the panel still shows our own agents
            out["workspace_agents"] = []
            out["workspace_agents_error"] = str(error)[:160]
        if is_operator(request):
            auth = g.sarvam_voice.auth.status()
            out["sarvam"].update({"account": g.sarvam_voice.auth.account(), "mcp_connected": auth.get("connected"),
                                  "mcp_expires_at": auth.get("expires_at")})
            out["keys"] = {n: {k: v for k, v in g.settings.secret_status(n).items() if k in ("configured", "source", "fingerprint")}
                           for n in ("SARVAM_V4_API_KEY", "SARVAM_VOICE_AGENTS_API_KEY")}
        return out

    @router.post("/admin/sarvam-agents/manjula-v2/build")
    async def manjula_v2_build(request: Request):
        """Build (or rebuild) Manjula Executive v2 on Sarvam from the current prompt; the PM's Manjula Excutive is untouched."""
        require_operator(request)
        return await svc().manjula_v2.build()

    @router.get("/architecture")
    async def architecture(request: Request):
        """Static architecture for the side bar before a run: every source, its mode and how it is reached."""
        g = svc()
        op = is_operator(request)
        base = gateway_base(g.settings)
        items = [{"id": a.id, "label": a.label, "group": a.group, "kind": a.kind, "mode": a.mode, "method": a.method,
                  "via_gateway": a.via_gateway and bool(base), "contract": a.contract, "reachable": a.reachable,
                  "endpoint_supplied": bool(a.url)} for a in g.registry.all()]
        return {"items": items, "gateway": {"enabled": bool(base), "base": base if op else ("configured" if base else None),
                                            "glid_access": g.glid_access()},
                "operator": op, "revision": g.registry.revision}

    @router.get("/admin/hits")
    async def admin_hits(request: Request, api: str = "", count: int = 150):
        require_operator(request)
        return {"items": svc().store.hits(api or None, min(count, 500))}

    @router.get("/admin/settings")
    async def admin_settings(request: Request):
        require_operator(request)
        g = svc()
        return {**g.settings.public(), "secrets": [g.settings.secret_status(n) for n in ("VOICE_PLUGIN_KEY", "SARVAM_V4_API_KEY")],
                "allowed_hosts": __import__("backend.genome.registry", fromlist=["allowed_hosts"]).allowed_hosts(),
                "elastic": await g.elastic.counts(), "outbox": g.store.outbox_size(), "budget": g.sarvam.budget()}

    @router.patch("/admin/settings")
    async def admin_patch_settings(body: SettingsPatch, request: Request):
        require_operator(request)
        g = svc()
        try:
            engines.validate_settings(body.changes, g.settings.get("engines.tts_model", "bulbul:v4-flash"))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        try:
            return g.settings.update(body.changes, body.expected_revision)
        except KeyError as error:
            raise HTTPException(422, f"Unknown setting {error}") from error
        except ValueError as error:
            raise HTTPException(409 if "reload" in str(error) else 422, str(error)) from error

    @router.post("/admin/settings/rollback")
    async def admin_rollback(body: Rollback, request: Request):
        require_operator(request)
        try:
            return svc().settings.rollback(body.revision, body.expected_revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    # ---- Persona generator (5.5): prompt, scenario suite, results --------------------------------------------------

    @router.get("/admin/persona-generator")
    async def persona_generator_info(request: Request):
        require_operator(request)
        from backend.genome import persona_generator as pg
        g = svc()
        text = pg.load_prompt()
        changes = re.search(r"<!--(.*?)-->", text, re.DOTALL)
        system, guide = pg.prompt_parts()
        evals = sorted(g.store.list("persona_evals", 10), key=lambda e: e.get("started_at", ""), reverse=True)
        last = evals[0] if evals else None
        if last:
            last = {**last, "rows": [{k: v for k, v in r.items() if k != "persona"} for r in last.get("rows") or []]}
        return {"engine": g.settings.get("persona.engine", "generator"), "prompt_file": pg.PROMPT_FILE.name, "prompt": text,
                "changes": changes.group(1).strip() if changes else "", "system_chars": len(system), "input_guide_chars": len(guide),
                "system_limit": pg.SYSTEM_LIMIT, "last_eval": last, "evals": [{k: e.get(k) for k in ("id", "started_at", "finished_at", "status", "summary")} for e in evals]}

    @router.get("/admin/persona-generator/eval/{eval_id}/case/{index}")
    async def persona_generator_case(eval_id: str, index: int, request: Request):
        require_operator(request)
        doc = svc().store.get("persona_evals", eval_id)
        if not doc or index >= len(doc.get("rows") or []):
            raise HTTPException(404, "No such case.")
        return doc["rows"][index]

    @router.post("/admin/persona-generator/eval")
    async def persona_generator_eval(request: Request, only: str = ""):
        """Run the scenario suite live in the background (14 heroes + edge cases; ~25 LLM calls, paced for the gateway)."""
        require_operator(request)
        from backend.genome import persona_eval as pe
        from backend.genome import persona_generator as pg
        g = svc()
        running = [e for e in g.store.list("persona_evals", 10) if e.get("status") == "running"]
        if running:
            raise HTTPException(409, f"An evaluation is already running ({running[0]['id']}).")
        today = datetime.now(parsers.IST).date()
        cases = pe.scenarios(today)
        if only:
            cases = [c for c in cases if any(o.strip().lower() in c["name"].lower() for o in only.split(",") if o.strip())]
        eval_id = "pe-" + hashlib.sha256(f"{time.time()}".encode()).hexdigest()[:8]
        doc = {"id": eval_id, "status": "running", "started_at": datetime.now(parsers.IST).isoformat(timespec="seconds"),
               "total": len(cases), "rows": [], "prompt": pg.PROMPT_FILE.name}
        g.store.put("persona_evals", eval_id, doc)

        def on_row(row: dict) -> None:
            doc["rows"].append(row)
            g.store.put("persona_evals", eval_id, doc)
            if g.elastic.enabled and "error" not in row:
                g.elastic.queue("persona-evals", f"{eval_id}:{len(doc['rows'])}", {
                    "id": f"{eval_id}:{len(doc['rows'])}", "eval_id": eval_id, "name": row["name"], "kind": row["kind"], "glid": str(row.get("glid") or ""),
                    "score": row["score"], "raw_score": row.get("raw_score"), "critical": row["critical"], "raw_critical": row.get("raw_critical"),
                    "warnings": row["warnings"], "scenario_ok": row["scenario_ok"],
                    "scenario_total": len(row["scenario_checks"]), "expected": row["expected"], "plan": row["plan"] or "none", "attempts": row["attempts"],
                    "ms": row["ms"], "failed_checks": [c["check"] for c in row["failed"]], "prompt": pg.PROMPT_FILE.name,
                    "model": g.settings.get("llm.model"), "at": doc["started_at"], "persona": row.get("persona")})

        async def work() -> None:
            try:
                rows = await pe.run(g.sarvam, cases, today, on_row=on_row)
                doc.update(status="done", summary=pe.summary(rows))
            except Exception as error:  # noqa: BLE001
                doc.update(status="error", error=f"{type(error).__name__}: {str(error)[:200]}")
            doc["finished_at"] = datetime.now(parsers.IST).isoformat(timespec="seconds")
            g.store.put("persona_evals", eval_id, doc)
        asyncio.create_task(work())
        return {"id": eval_id, "cases": len(cases)}

    @router.get("/admin/persona-generator/eval/{eval_id}")
    async def persona_generator_eval_status(eval_id: str, request: Request):
        require_operator(request)
        doc = svc().store.get("persona_evals", eval_id)
        if not doc:
            raise HTTPException(404, "No such evaluation.")
        return {**doc, "rows": [{k: v for k, v in r.items() if k != "persona"} for r in doc.get("rows") or []]}

    # ---- Payal on Sarvam Voice Agents (built through the Sarvam Voice MCP) ------------------------

    def tool_key_ok(request: Request) -> None:
        """Sarvam's API tools authenticate with the workspace secret we stored there; several header shapes accepted."""
        import secrets as _secrets
        expected = svc().settings.secret("SARVAM_AGENT_TOOL_KEY")
        auth = request.headers.get("authorization", "")
        supplied = (request.headers.get("x-api-key") or request.headers.get("x-sellergenome-key") or request.headers.get("api-key")
                    or (auth[7:] if auth.lower().startswith("bearer ") else "") or request.query_params.get("key", ""))
        if not expected or not supplied or not _secrets.compare_digest(expected, supplied):
            raise HTTPException(401, "Tool key missing or wrong.")

    @router.get("/sarvam-agent/context")
    async def agent_context(request: Request, seller_id: str = ""):
        """On-start tool: the persona context Payal's variables are filled from (synthetic demo sellers only)."""
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        ctx = svc().sarvam_voice.context(seller_id or None)
        svc().sarvam_voice.record_call("start", {"seller_id": ctx["seller_id"], "company": ctx["company_name"]})
        return ctx

    @router.post("/sarvam-agent/meetings")
    async def agent_meeting(request: Request):
        """Run tool: book the meeting the seller agreed (a test meeting with a working link; nobody is invited)."""
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        slot = str(body.get("slot") or "").strip()[:80] or "the agreed time"
        mode = "in_person" if "person" in str(body.get("mode", "")).lower() or "visit" in str(body.get("mode", "")).lower() else "online"
        room = hashlib.sha256(f"{body.get('seller_id')}{slot}{time.time()}".encode()).hexdigest()[:10]
        link = svc().settings.get("meetings.meeting_link_base", "https://meet.jit.si/IndiaMART-SellerGenome-") + room if mode == "online" else None
        item = svc().sarvam_voice.record_call("meeting", {"seller_id": body.get("seller_id"), "slot": slot, "mode": mode, "link": link or ""})
        where = "online — the executive will share the joining link" if mode == "online" else "at the seller's place"
        return {"ok": True, "slot_label": slot, "mode": mode, "meeting_link": link, "booked_at": item["at"],
                "confirmation": f"Done. The meeting with an IndiaMART executive is booked for {slot}, {where}."}

    @router.post("/sarvam-agent/outcome")
    async def agent_outcome(request: Request):
        """On-end tool: what the call established (stored and shown on /sarvam)."""
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        svc().sarvam_voice.record_call("end", {k: v for k, v in body.items() if isinstance(v, (str, int, float, bool))})
        return {"ok": True}

    # VANI buyer agent on Sarvam (5.7): its tools call these with the same tool key as Payal's.
    @router.get("/sarvam-buyer-agent/context")
    async def buyer_agent_context(request: Request, call_id: str = ""):
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        ctx = svc().sarvam_buyer.context(call_id or None)
        svc().sarvam_buyer.record("start", {"call": ctx["buyer_call_id"], "product": ctx["product_name"]})
        return ctx

    @router.post("/sarvam-buyer-agent/product")
    async def buyer_agent_product(request: Request):
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return svc().sarvam_buyer.product(body.get("buyer_call_id"), str(body.get("product_name") or ""))

    @router.post("/sarvam-buyer-agent/outcome")
    async def buyer_agent_outcome(request: Request):
        tool_key_ok(request)
        limit(request, "agent-tool", 120)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return svc().sarvam_buyer.outcome(body if isinstance(body, dict) else {})

    @router.get("/sarvam-buyer")
    async def sarvam_buyer_status(request: Request):
        return svc().sarvam_buyer.public_status(is_operator(request))

    @router.post("/sarvam-buyer/chat")
    async def sarvam_buyer_chat(body: BuyerAgentChat, request: Request):
        """Text chat with the VANI buyer agent on Sarvam (MCP send_chat). Synthetic buyer-desk calls only."""
        from backend.genome.sarvam_voice import VoiceAgentError
        limit(request, "agent-chat", 12 if not is_operator(request) else 40)
        g = svc()
        try:
            g.sarvam.spend(scope_of(request))
            return await g.sarvam_buyer.chat(body.buyer_call_id, body.text, body.interaction_id, body.end)
        except BudgetExceeded as error:
            raise HTTPException(429, str(error)) from error
        except VoiceAgentError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/admin/sarvam-buyer/build")
    async def sarvam_buyer_build(request: Request):
        require_operator(request)
        return await svc().sarvam_buyer.build("https://sellergenome.lehana.in")

    @router.post("/admin/sarvam-buyer/smoke")
    async def sarvam_buyer_smoke(request: Request, call_id: str = "buyer-b1"):
        """Headless voice call to the VANI buyer agent through the Python SDK — times the opening and each answer."""
        from backend.genome.sarvam_voice import VoiceAgentError
        require_operator(request)
        g = svc()
        try:
            g.sarvam.spend("operator")
            return await asyncio.wait_for(g.sarvam_buyer.smoke(g.sarvam, call_id), timeout=110)
        except asyncio.TimeoutError as error:
            raise HTTPException(504, "The smoke call took longer than 110 s.") from error
        except (VoiceAgentError, SarvamError, BudgetExceeded) as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/sarvam-voice")
    async def sarvam_voice_status(request: Request):
        return svc().sarvam_voice.public_status(is_operator(request))

    @router.post("/sarvam-voice/chat")
    async def sarvam_voice_chat(body: AgentChat, request: Request):
        """Talk to Payal on Sarvam's platform over text (send_chat through the Voice MCP). Synthetic sellers only."""
        from backend.genome.sarvam_voice import VoiceAgentError
        limit(request, "agent-chat", 12 if not is_operator(request) else 40)
        g = svc()
        try:
            g.sarvam.spend("operator" if is_operator(request) else "public")  # counts against the daily Sarvam budget
            return await g.sarvam_voice.chat(body.seller_id, body.text, body.interaction_id, body.end)
        except BudgetExceeded as error:
            raise HTTPException(429, str(error)) from error
        except VoiceAgentError as error:
            raise HTTPException(409, str(error)) from error

    # Live voice calls to Payal from the browser (Sarvam conversational SDK, bundled in frontend/cc/vendor). The SDK's
    # baseUrl points here, so it asks *this* server for the signed WebSocket URL; the Voice Agents key stays here.

    @router.get("/sarvam-live")
    async def sarvam_live_status():
        cfg = svc().sarvam_voice.live_config()
        return {"available": cfg["available"], "reason": cfg["reason"], "app_id": cfg["app_id"]}

    @router.post("/sarvam-live/sessions")
    async def sarvam_live_start(body: LiveStart, request: Request):
        from backend.genome.sarvam_voice import VoiceAgentError
        g = svc()
        spec = None
        ours = {(g.sarvam_voice.state().get("agent") or {}).get("id"), (g.sarvam_buyer.state().get("agent") or {}).get("id")}
        if body.app_id and body.app_id not in ours:  # Manjula, Payal 6.0 … (owner, 2026-10-10: "i want all of them")
            try:
                spec = await g.sarvam_voice.agent_spec(body.app_id)
            except VoiceAgentError as error:
                raise HTTPException(404, str(error)) from error
        if body.agent == "buyer":  # buyer-desk calls: VANI on Sarvam, or another workspace agent
            if not is_operator(request):
                limit(request, "payal-live", int(g.settings.get("sarvam_voice.live_calls_per_visitor_per_hour", 6)), window=3600)
            try:
                g.sarvam.spend(scope_of(request))
                return g.sarvam_buyer.live_start(body.buyer_call_id or "", visitor(request), spec)
            except BudgetExceeded as error:
                raise HTTPException(429, str(error)) from error
            except VoiceAgentError as error:
                raise HTTPException(409, str(error)) from error
        if not roster.is_public(body.seller_id):
            # Real sellers (owner, 2026-10-10: production, IndiaMART data with consent): signed in, from a prepared run.
            can_see(request, body.seller_id)
            if not body.run_id:
                raise HTTPException(422, "Prepare the seller first (Talk as Seller) — the agent gets the real seller's data from that run.")
        if not is_operator(request):
            limit(request, "payal-live", int(g.settings.get("sarvam_voice.live_calls_per_visitor_per_hour", 6)), window=3600)
        run = None
        if body.run_id:
            run = get_run(request, body.run_id)
            if run["seller_id"] != body.seller_id:
                raise HTTPException(422, "The run belongs to another seller.")
        delivery = {k: (((run or {}).get("persona") or {}).get("factors", {}).get(k) or {}).get("value") for k in ("language", "pace")}
        try:
            g.sarvam.spend(scope_of(request))
            return g.sarvam_voice.live_start(body.seller_id, run, visitor(request), delivery, spec)
        except BudgetExceeded as error:
            raise HTTPException(429, str(error)) from error
        except VoiceAgentError as error:
            raise HTTPException(409, str(error)) from error

    def live_auth(request: Request, session_id: str) -> dict:
        from backend.genome.sarvam_voice import VoiceAgentError
        try:
            return svc().sarvam_voice.live_session(session_id, request.headers.get("x-live-token", ""))
        except VoiceAgentError as error:
            raise HTTPException(401, str(error)) from error

    @router.get("/sarvam-live/s/{session_id}/orgs/{org}/workspaces/{ws}/apps/{app}/url")
    async def sarvam_live_url(session_id: str, org: str, ws: str, app: str, request: Request, interaction_type: str = "call"):
        """The path the SDK builds from its baseUrl; org/workspace/app must be ours, the token must match the session."""
        from backend.genome.sarvam_voice import VoiceAgentError
        session = live_auth(request, session_id)
        cfg = svc().sarvam_voice.live_config()
        allowed = (session.get("live") or {}).get("app_id") or cfg["app_id"]   # the agent this session was opened for
        if (org, ws, app) != (cfg["org_id"], cfg["workspace_id"], allowed):
            raise HTTPException(403, "Only the agent this call was opened for, in SellerGenome's Sarvam workspace, can be called from here.")
        try:
            return await svc().sarvam_voice.live_signed_url(session, interaction_type)
        except VoiceAgentError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/sarvam-live/s/{session_id}/events")
    async def sarvam_live_events(session_id: str, body: LiveEvents, request: Request):
        """Transcript lines and SDK events from the page → the REAL CALL tile on the floor and SellerGenome's signals."""
        session = live_auth(request, session_id)
        limit(request, "payal-live-events", 120)
        g = svc()
        events = []
        for e in body.events[:50]:
            kind = e.get("type")
            if kind == "transcript" and e.get("speaker") in ("bot", "seller", "buyer") and isinstance(e.get("text"), str):
                events.append({"type": "transcript", "speaker": e["speaker"], "text": e["text"][:600]})
            elif kind == "tool" and isinstance(e.get("name"), str):
                events.append({"type": "tool", "name": e["name"][:60], "details": str(e.get("details", ""))[:300]})
            elif kind == "signal" and isinstance(e.get("signal"), str):
                events.append({"type": "signal", "signal": e["signal"][:40], "method": "sarvam_event"})
            elif kind == "ended":
                events.append({"type": "ended"})
            if isinstance(e.get("interaction_id"), str) and not session["live"].get("interaction_id"):
                session["live"]["interaction_id"] = e["interaction_id"][:80]
        out = g.voice.ingest(session, events)
        if any(e["type"] == "ended" for e in events):
            agent = g.sarvam_buyer if (session.get("live") or {}).get("agent") == "buyer" else g.sarvam_voice
            (agent.record if agent is g.sarvam_buyer else agent.record_call)("live_end", {"session": session_id, "lines": len(session.get("transcript") or [])})
        return out

    @router.post("/admin/sarvam-voice/connect")
    async def sarvam_voice_connect(request: Request):
        require_operator(request)
        return {"authorize_url": await svc().sarvam_voice.auth.begin(),
                "note": "Sign in with the hackathon Sarvam org. The browser then lands on a 127.0.0.1 page that may not load — copy that address and paste it back."}

    @router.post("/admin/sarvam-voice/complete")
    async def sarvam_voice_complete(body: CallbackPaste, request: Request):
        from backend.genome.sarvam_voice import VoiceAgentError
        require_operator(request)
        try:
            return await svc().sarvam_voice.auth.complete(body.callback_url)
        except VoiceAgentError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/admin/sarvam-voice/build")
    async def sarvam_voice_build(request: Request, rotate_key: bool = False):
        require_operator(request)
        base = str(request.base_url).rstrip("/").replace("http://", "https://")
        if "lehana.in" not in base and "aidhunik.com" not in base:
            base = "https://sellergenome.lehana.in"
        return await svc().sarvam_voice.build(base.replace("sellergenome.aidhunik.com", "sellergenome.lehana.in"), rotate_key=rotate_key)

    @router.post("/admin/sarvam-voice/test-key")
    async def sarvam_voice_test_key(request: Request):
        """Check the Voice Agents API key against Sarvam (read-only analytics boards list)."""
        require_operator(request)
        g = svc()
        key, ws = g.settings.secret("SARVAM_VOICE_AGENTS_API_KEY"), g.sarvam_voice.live_config()["workspace_id"]
        org = g.settings.get("llm.organization_id")
        if not key:
            return {"ok": False, "note": "No Voice Agents API key configured (Admin → Keys)."}
        if not ws:
            return {"ok": False, "note": "Set the workspace id first (Admin → Sarvam voice agent) — the key is checked against it."}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://apps.sarvam.ai/api/analytics/v1/{org}/{ws}/boards", headers={"X-API-Key": key})
        detail = r.text[:200].replace(key, "[key]")
        return {"ok": r.status_code < 400, "status": r.status_code, "note": "Key accepted — browser voice calls can be enabled." if r.status_code < 400 else detail}

    @router.post("/admin/sarvam-voice/smoke")
    async def sarvam_voice_smoke(request: Request, seller_id: str = "syn-91000101"):
        """Headless voice call to Payal through the Python SDK — times the opening and each answer."""
        from backend.genome.sarvam_voice import VoiceAgentError
        require_operator(request)
        g = svc()
        try:
            g.sarvam.spend("operator")
            return await asyncio.wait_for(g.sarvam_voice.smoke(g.sarvam, seller_id), timeout=110)
        except asyncio.TimeoutError as error:
            raise HTTPException(504, "The smoke call took longer than 110 s.") from error
        except (VoiceAgentError, SarvamError, BudgetExceeded) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/admin/sarvam-voice/eval")
    async def sarvam_voice_eval(request: Request):
        from backend.genome.sarvam_voice import VoiceAgentError
        require_operator(request)
        try:
            return await svc().sarvam_voice.run_eval()
        except VoiceAgentError as error:
            raise HTTPException(409, str(error)) from error

    # ---- voice plug-in -------------------------------------------------------------------------

    def plugin(request: Request) -> None:
        if not svc().voice.key_ok(request.headers.get("x-voice-plugin-key", "")):
            raise HTTPException(401, "A valid X-Voice-Plugin-Key is required (Admin → Voice plug-in).")

    @router.post("/voice/sessions")
    async def voice_register(body: VoiceSession, request: Request):
        plugin(request)
        g = svc()
        if body.run_id:
            run = g.store.get("runs", body.run_id)
            if not run:
                raise HTTPException(404, "Run not found.")
        else:
            seller_id = body.seller_id or (f"real-{body.glid}" if body.glid and not roster.heroes().get(body.glid) else f"syn-{body.glid}")
            run = g.orchestrator.create(seller_id, origin="api", operator=True)
            for _ in range(60):
                await asyncio.sleep(0.25)
                run = g.store.get("runs", run["id"])
                if run["status"] != "running":
                    break
        return g.voice.register(run, body.provider, body.direction, body.room)

    @router.get("/voice/sessions/{session_id}/context")
    async def voice_context(session_id: str, request: Request):
        plugin(request)
        g = svc()
        session = g.store.get("voice_sessions", session_id)
        if not session:
            raise HTTPException(404, "Session not found.")
        return g.voice.context(session, g.store.get("runs", session["run_id"]))

    @router.post("/voice/sessions/{session_id}/events")
    async def voice_events(session_id: str, request: Request):
        plugin(request)
        g = svc()
        session = g.store.get("voice_sessions", session_id)
        if not session:
            raise HTTPException(404, "Session not found.")
        payload = await request.json()
        events = payload.get("events") if isinstance(payload, dict) and "events" in payload else [payload]
        return g.voice.ingest(session, [e for e in events if isinstance(e, dict)])

    @router.get("/voice/sessions/{session_id}")
    async def voice_session(session_id: str, request: Request):
        g = svc()
        session = g.store.get("voice_sessions", session_id)
        if not session:
            raise HTTPException(404, "Session not found.")
        can_see(request, session["seller_id"])
        return {k: v for k, v in session.items() if k != "live"}  # the live-call token hash and visitor stay server-side

    # ---- Sarvam studio -------------------------------------------------------------------------

    @router.get("/sarvam/tools")
    async def sarvam_tools(request: Request):
        g = svc()
        inventory = lab().inventory() if lab and lab() else {"items": [], "version": "0.2.12"}
        items = []
        for t in inventory["items"]:
            where, how = USED_IN.get(t["name"], ("Available", "Discovered in the pinned MCP inventory."))
            items.append({"name": t["name"], "group": t.get("group"), "description": t.get("description", "")[:400],
                          "live_status": t.get("live_status"), "where": where, "how": how,
                          "try": t["name"] in TRY_TOOLS, "last_receipt": (t.get("last_receipts") or [None])[0]})
        return {"items": items, "version": inventory.get("version"), "organization_id": g.settings.get("llm.organization_id"),
                "budget": g.sarvam.budget(), "model": g.settings.get("llm.model")}

    TRY_TOOLS = {"sarvam_tools_translate", "sarvam_tools_transliterate", "sarvam_tools_identify_language",
                 "sarvam_tools_tts_speak", "sarvam_tools_llm_complete"}

    @router.post("/sarvam/try")
    async def sarvam_try(body: SarvamTry, request: Request):
        g = svc()
        limit(request, "speak", 20)
        scope = "operator" if is_operator(request) else "public"
        text = body.text or "Namaste, main IndiaMART ki virtual assistant Payal bol rahi hoon. Kya do minute baat ho sakti hai?"
        try:
            if body.tool == "sarvam_tools_translate":
                return {"tool": body.tool, "input": text, **(await g.sarvam.translate(text, body.language, scope, model=body.model))}
            if body.tool == "sarvam_tools_transliterate":
                return {"tool": body.tool, "input": text, **(await g.sarvam.transliterate(text, "hi-IN", scope))}
            if body.tool == "sarvam_tools_identify_language":
                return {"tool": body.tool, "input": text, **(await g.sarvam.identify(text, scope))}
            if body.tool == "sarvam_tools_tts_speak":
                translit = await g.sarvam.transliterate(text, "hi-IN", scope) if body.language == "hi-IN" and not re.search(r"[ऀ-ॿ]", text) else {"text": text}
                return {"tool": body.tool, "input": text, "spoken_text": translit["text"],
                        **(await g.sarvam.tts(translit["text"] or text, body.language, body.speaker or "ritu", 1.0, scope, body.model))}
            if body.tool == "sarvam_tools_llm_complete":
                r = await g.sarvam.chat_json(prompts.text("tools.llm_test"), text, scope=scope, max_tokens=200)
                return {"tool": body.tool, "input": text, "output": r["data"], "ms": r["ms"], "model": r["model"]}
        except ValueError as error:
            raise bad_choice(error) from error
        except (SarvamError, BudgetExceeded) as error:
            raise HTTPException(503, str(error)) from error
        raise HTTPException(422, "This tool has no quick try; use Run (demo/live) instead.")

    @router.post("/sarvam/tools/{name}/run")
    async def sarvam_run(name: str, request: Request, mode: str = "demo"):
        if mode == "live":
            require_operator(request)
        if not (lab and lab()):
            raise HTTPException(503, "Sarvam lab is not available.")
        try:
            return await lab().run(name, mode, None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
