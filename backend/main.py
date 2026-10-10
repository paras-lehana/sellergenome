"""HTTP application for the seller-persona research and rehearsal portal."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, Field

from backend.config import ROOT, JobSettings, Settings
from backend.access import AccessService
from backend.workshop import WorkshopService
from backend.portal_api import portal_router
from backend.admin_config import ConfigurationError, ConfigurationConflict
from backend.demo import demo_sellers
from backend.connectors import (
    ConnectorError,
    ConnectorService,
    SOURCE_ALIASES,
    SOURCES,
)
from backend.jobs import JobConflict
from backend.persona import (
    adapt,
    attach_pattern_evidence,
    freshness,
    generate_persona,
    outcome,
    project_derived,
    utcnow,
)
from backend.providers import ProviderUnavailable, Providers
from backend.roadmap import roadmap
from backend.search import SearchIndex
from backend.store import Store
from backend.sync import SyncWorker
from backend.speech import SpeechService, AUDIO_TYPES
from backend.live import LiveService
from backend.recording_source import RecordingSource
from backend.insights import InsightService
from backend.analysis import (
    live_aggregate,
    persona_fields,
    report as analysis_report,
)
from backend.deconstruction import deconstruct, index_fields
from backend.insight_jobs import InsightCohortService

config = Settings()
store = Store(config.db_path)
access = AccessService(config, store)
workshop = WorkshopService(config, store)
search = SearchIndex(config)
providers = Providers(config)
started = datetime.now(ZoneInfo("Asia/Kolkata")).strftime(
    "%b %d, %Y at %I:%M %p IST"
)
session_locks: dict[str, asyncio.Lock] = {}
provider_semaphore = asyncio.Semaphore(3)


def readiness_map() -> dict:
    """Merge implementation readiness with observed provider outcomes."""
    result = roadmap()
    for item in result["items"]:
        provider = (
            "sarvam"
            if item["id"] == "voice"
            else "llm"
            if item["id"] == "llm"
            else None
        )
        checked = (
            store.get("metadata", f"provider-{provider}") if provider else None
        )
        if checked:
            item["status"] = checked["status"]
            item["description"] += " " + checked.get("message", "")
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Seed only synthetic fixtures; real ingestion is a separate explicit job."""
    global access, workshop
    access = AccessService(config, store)
    workshop = WorkshopService(config, store)
    # The revisioned private configuration supersedes legacy SQLite overrides.
    ConnectorService(config.values.get("connectors", {})).seed_targets(store)
    demos = demo_sellers()
    for seller in demos:
        # Keep fixture dates stable across restarts so freshness is observable.
        if not store.get("sellers", seller["id"]):
            for evidence in seller.pop("evidence"):
                store.put(
                    "evidence",
                    evidence["id"],
                    evidence,
                    seller_id=seller["id"],
                )
            store.put("sellers", seller["id"], seller)
    if config.es_url:
        try:
            await search.ensure()
            sellers = store.sellers("", "", 1, 20, "demo")["items"]
            await search.bulk("sellers", sellers)
            await search.bulk(
                "evidence",
                [
                    event
                    for seller in sellers
                    for event in store.seller_evidence(seller["id"])
                ],
            )
        except Exception:
            search.last_status = "initialization_failed"
    from backend.operations import OperationsService
    from backend.buyer_workflow import BuyerEngine
    from backend.test_actions import ActionService
    from backend.voice_adapters import VoiceAdapters
    app.state.operations = OperationsService(config, store, search)
    app.state.operations.buyer = BuyerEngine(config, app.state.operations.repo)
    app.state.operations.actions = ActionService(config, app.state.operations.repo)
    app.state.operations.voice = VoiceAdapters(config, app.state.operations.repo)
    from backend.sarvam_lab import SarvamLab
    app.state.operations.sarvam_lab = SarvamLab(config, app.state.operations.repo)
    await app.state.operations.start()
    app.state.sync_worker = SyncWorker(store, search, config)
    await app.state.sync_worker.start()
    app.state.speech = SpeechService(config, store, providers)
    app.state.live = LiveService(config, providers)
    app.state.live_authority = {}
    app.state.recording_source = RecordingSource(config)
    app.state.insights = InsightService(config, store, providers)
    app.state.insight_cohorts = InsightCohortService(
        config, store, app.state.insights
    )
    await app.state.speech.start()
    await app.state.insight_cohorts.start()
    # V5 Command Center (backend/genome): separate SQLite, ES namespace and settings.
    from backend.genome.service import Genome
    app.state.genome = Genome(config.data_dir, config, store)
    await app.state.genome.start()
    try:
        yield
    finally:
        await app.state.genome.stop()
        await app.state.operations.stop()
        await app.state.insight_cohorts.stop()
        await app.state.speech.stop()
        await app.state.sync_worker.stop()


app = FastAPI(
    title="Seller Persona API",
    version=config.values["app"]["version"],
    lifespan=lifespan,
    root_path=config.base_path,
    docs_url="/docs",
    redoc_url=None,
)


def is_operator(request: Request) -> bool:
    """Use server-verified password access or the existing automation key."""
    supplied = request.headers.get("x-operator-key", "")
    return bool(
        access.session(request.cookies.get(access.cookie_name))["operator"]
        or (
            config.operator_key
            and secrets.compare_digest(config.operator_key, supplied)
        )
    )


def operator(request: Request) -> None:
    """Never let public demo visitors access real seller records or provider credits."""
    if not is_operator(request):
        raise HTTPException(
            401,
            "Operator access is required. Enter the deployment operator key in Settings.",
        )


def owner(request: Request) -> str:
    """Hash browser ownership; no raw cookie value is stored in the session table."""
    cookie = request.cookies.get(access.owner_cookie, "")
    return (
        hashlib.sha256(cookie.encode()).hexdigest()
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cookie)
        else ""
    )


def set_owner_cookie(response: Response, request: Request) -> str:
    """Establish browser identity before polling, retaining existing session ownership."""
    cookie = request.cookies.get(access.owner_cookie, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", cookie):
        cookie = secrets.token_urlsafe(32)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        access.owner_cookie,
        cookie,
        httponly=True,
        secure=request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https",
        samesite="strict",
        max_age=86400 * config.values["security"]["session_cookie_days"],
        path=config.base_path or "/",
    )
    return cookie


@app.middleware("http")
async def boundaries(request: Request, call_next):
    """Same-origin mutations and bounded traffic protect the public rehearsal."""
    # Traefik strips the prefix. Mounted StaticFiles needs a full ASGI path
    # consistent with root_path; otherwise it looks for static/static assets.
    path = request.scope["path"]
    if config.base_path and not (
        path == config.base_path or path.startswith(config.base_path + "/")
    ):
        request.scope["path"] = config.base_path + path
        request.scope["raw_path"] = request.scope["path"].encode()
    if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
        origin = request.headers.get("origin")
        if origin and origin not in {
            "https://portfolio.aidhunik.com",
            "https://portfolio.lehana.in",
            f"{request.url.scheme}://{request.url.netloc}",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            *config.values["voice"]["live_origins"],
        }:
            return JSONResponse(
                {"detail": "Cross-origin mutations are not allowed."},
                status_code=403,
            )
    if "/api/" in request.url.path:
        # Never trust an arbitrary forwarded-for header supplied by the client.
        identity = owner(request) or (
            request.client.host if request.client else "local"
        )
        scope = "operator" if is_operator(request) else "public"
        bucket = f"{scope}:{int(time.time() // 60)}:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        maximum = config.values["security"][
            "operator_requests_per_minute"
            if is_operator(request)
            else "public_requests_per_minute"
        ]
        if not store.allow_request(bucket, maximum):
            return JSONResponse(
                {"detail": "Request limit reached; try again in a minute."},
                status_code=429,
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), geolocation=(), microphone=(self)"
    )
    # connect-src: our own wss:// (realtime listening) and Sarvam's agent runtime, which the bundled
    # conversational SDK opens directly with a short-lived signed URL (live calls to Payal, 5.3).
    host = request.url.netloc
    own_ws = f"wss://{host}" if re.fullmatch(r"[A-Za-z0-9.-]+(:\d+)?", host or "") else ""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        f"connect-src 'self' {own_ws} wss://apps.sarvam.ai; media-src 'self' blob: data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    if "/api/" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    request: Request, error: RequestValidationError
):
    """Validation reports paths and constraints without reflecting secret inputs."""
    return JSONResponse(
        {
            "detail": [
                {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                for item in error.errors()
            ]
        },
        status_code=422,
    )


@app.exception_handler(ConfigurationError)
async def configuration_error(request: Request, error: ConfigurationError):
    """Only validated public error messages are returned by the configuration service."""
    return JSONResponse(
        {"detail": str(error)},
        status_code=409 if isinstance(error, ConfigurationConflict) else 422,
    )


@app.exception_handler(ProviderUnavailable)
async def provider_error(request: Request, error: ProviderUnavailable):
    """Only application-authored safe error messages cross the public boundary."""
    store.put(
        "metadata",
        f"provider-{error.provider}",
        {
            "status": "credits_exhausted"
            if error.code == 402
            else "request_failed",
            "at": utcnow(),
            "message": str(error),
        },
    )
    return JSONResponse(
        {
            "detail": str(error),
            "provider": error.provider,
            "provider_status": error.code,
        },
        status_code=503,
    )


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    """Expose standard process health and independent dependency readiness."""
    storage = await search.status()
    return {
        "status": "ok",
        "started": started,
        "host": os.getenv("SERVER_HOST", "local"),
        "version": config.values["app"]["version"],
        "service": "sellergenome",
        "environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        "storage": storage,
        "refresh": app.state.sync_worker.summary(),
        "speech": app.state.speech.summary(),
    }


@app.get("/api/overview")
async def overview():
    """Show measured aggregate counts, never fictitious performance claims."""
    catalog_path = ROOT / "data/catalog.json"
    catalog = (
        json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    )
    storage = await search.status()
    demos = store.sellers("", "", 1, 20, "demo")["items"]
    return {
        "stats": [
            {
                "label": "Dataset rows profiled",
                "value": catalog.get("total_rows", 0),
                "detail": f"{catalog.get('file_count', 0)} supplied CSV files; aggregate research only",
            },
            {
                "label": "Seller profiles",
                "value": catalog.get("rows_by_family", {}).get(
                    "seller_files", 0
                ),
                "detail": "Supplied research corpus; identities kept private",
            },
            {
                "label": "Recording samples",
                "value": catalog.get("rows_by_family", {}).get(
                    "recording_sample", 0
                ),
                "detail": "Metadata catalog; audio not downloaded",
            },
            {
                "label": "Indexed documents",
                "value": storage.get("indexed_documents") or 0,
                "detail": f"Search status: {storage['status']}",
            },
        ],
        "persona_distribution": [
            {"label": seller["persona_tag"], "count": 1} for seller in demos
        ],
        "activity": [
            {
                "title": "Dataset research complete",
                "description": "Join coverage, nulls, duplicate keys and evaluator schema drift documented.",
                "at": catalog.get("generated_at"),
            },
            {
                "title": "Persona intelligence",
                "description": "Inspect the evidence, confidence and freshness behind every choice.",
                "at": utcnow(),
            },
        ],
        "capabilities": readiness_map()["items"][:8],
        "mode": "Synthetic public rehearsal",
        "storage": storage,
        "private_records_imported": max(
            0, store.count("sellers") - len(demos)
        ),
        "sessions": store.count("sessions"),
        "sample_warning": "The six personas are illustrative. Their distribution is not a population estimate.",
    }


def get_seller(identifier: str, request: Request) -> dict:
    """Enforce access before returning any entity or its existence details."""
    if not identifier.startswith("demo-"):
        operator(request)
    seller = store.get("sellers", identifier)
    if not seller:
        raise HTTPException(404, "Seller not found.")
    if seller.get("source_mode") != "demo":
        operator(request)
    return seller


@app.get("/api/sellers")
async def sellers(
    request: Request,
    q: str = Query("", max_length=200),
    persona: str = Query("", max_length=60),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(24, ge=1, le=100),
    dataset: Literal["demo", "private"] = "demo",
):
    """Public and private listings cannot mix implicitly."""
    if dataset == "private":
        operator(request)
    indexed = await search.sellers(q, persona, page, page_size, dataset)
    return (
        indexed
        if indexed is not None
        else store.sellers(q, persona, page, page_size, dataset)
    )


@app.get("/api/sellers/{identifier}")
async def seller_detail(identifier: str, request: Request):
    """Expose individual reasons and evidence freshness alongside a seller."""
    seller = get_seller(identifier, request)
    seller["evidence"] = [
        {**event, "freshness": freshness(event)}
        for event in store.seller_evidence(identifier)
    ]
    seller["persona"] = store.get("personas", identifier)
    return seller


class EvidenceRequest(BaseModel):
    """A bounded, timestamped observation for incremental source updates."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    kind: Literal[
        "profile",
        "activity",
        "conversation",
        "call",
        "sts",
        "context",
        "company",
        "chat",
        "pns",
        "enquiry",
        "buyleads",
    ]
    text: str = Field(min_length=1, max_length=2000)
    observed_at: AwareDatetime
    source_ref: str = Field(min_length=1, max_length=160)
    ttl_hours: float = Field(24, gt=0, le=8760)
    confidence: float = Field(0.5, ge=0, le=1)
    contact_suppressed: bool | None = None


@app.post("/api/sellers/{identifier}/events")
async def ingest_event(
    identifier: str, body: EvidenceRequest, request: Request
):
    """Accept trusted connector observations without silently overwriting newer evidence."""
    operator(request)
    seller = get_seller(identifier, request)
    observed = body.observed_at.astimezone(timezone.utc)
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(
            422, "Source observation time cannot be in the future."
        )
    event = {
        **body.model_dump(mode="json", exclude_none=True),
        "observed_at": observed.isoformat(),
        "seller_id": identifier,
        "source_mode": seller["source_mode"],
        "ingested_at": utcnow(),
    }
    previous = store.get("evidence", body.id)
    if previous:
        if previous["seller_id"] != identifier:
            raise HTTPException(
                409, "This event ID belongs to another seller."
            )
        old_time = datetime.fromisoformat(previous["observed_at"])
        same = all(
            previous.get(key) == value
            for key, value in event.items()
            if key != "ingested_at"
        )
        if same:
            return {
                "status": "unchanged",
                "event_id": body.id,
                "needs_refresh": False,
            }
        if old_time >= observed:
            raise HTTPException(
                409,
                "A same-time or newer event already exists; supply a newer source revision.",
            )
    store.put("evidence", body.id, event, seller_id=identifier)
    if body.contact_suppressed is True:
        seller.setdefault("metrics", {})["do_not_call"] = True
        seller["updated_at"] = utcnow()
        store.put("sellers", identifier, seller)
        await search.put("sellers", identifier, seller)
    persona = store.get("personas", identifier)
    if persona:
        persona["needs_refresh"] = True
        persona["invalidated_at"] = utcnow()
        store.put("personas", identifier, persona)
        await search.put("personas", identifier, persona)
    indexed = await search.put("evidence", body.id, event)
    return {
        "status": "accepted",
        "event_id": body.id,
        "indexed": indexed,
        "needs_refresh": True,
        "contact_suppressed": seller.get("metrics", {}).get("do_not_call")
        is True,
    }


class PersonaRequest(BaseModel):
    """Expensive model inference is always an explicit operator choice."""

    use_llm: bool = False


@app.post("/api/sellers/{identifier}/persona")
async def create_persona(
    identifier: str, body: PersonaRequest, request: Request
):
    """Generate, validate, persist and index all derived fields with provenance."""
    seller = get_seller(identifier, request)
    if body.use_llm:
        operator(request)
    previous = store.get("personas", identifier)
    events = store.seller_evidence(identifier)
    persona = generate_persona(
        seller,
        events,
        config.values["persona"],
        (previous or {}).get("revision", 0) + 1,
    )
    if body.use_llm:
        async with provider_semaphore:
            persona = await providers.enrich(persona, events)
        store.put(
            "metadata",
            "provider-llm",
            {
                "status": "verified",
                "at": utcnow(),
                "message": "Structured persona inference passed field and citation validation.",
            },
        )
    attach_pattern_evidence(
        persona,
        (store.get("metadata", "call-summary-import") or {}).get(
            "associations"
        ),
    )
    persona["indexed"] = await search.put("personas", identifier, persona)
    store.put("personas", identifier, persona)
    await sync_persona_fields(persona, seller, events)
    return persona


async def sync_persona_fields(persona: dict, seller: dict, events: list):
    """Field indexing has its own receipt; failure cannot fake canonical success."""
    if config.es_url:
        try:
            persona["deconstruction"] = await index_fields(
                search, deconstruct(persona, seller, events)
            )
        except (httpx.HTTPError, RuntimeError):
            persona["deconstruction"] = {
                "status": "unavailable",
                "documents": 0,
            }


@app.get("/api/evidence")
async def evidence(
    request: Request,
    q: str = Query("", max_length=200),
    seller_id: str = Query("", max_length=120),
    dataset: Literal["demo", "private"] = "demo",
):
    """Search evidence in ES; disclose local fallback when search is unavailable."""
    if dataset == "private":
        operator(request)
    result = await search.evidence(q, seller_id, dataset)
    if result is None:
        with store.connect() as db:
            args = [dataset]
            condition = "json_extract(payload,'$.source_mode')=?"
            if seller_id:
                condition += " AND seller_id=?"
                args.append(seller_id)
            if q:
                condition += " AND instr(lower(json_extract(payload,'$.text')),lower(?))>0"
                args.append(q)
            total = db.execute(
                f"SELECT COUNT(*) FROM evidence WHERE {condition}", args
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT payload FROM evidence WHERE {condition} LIMIT 40",
                args,
            ).fetchall()
        result = {
            "items": [json.loads(row[0]) for row in rows],
            "total": total,
            "engine": "sqlite (Elasticsearch unavailable)",
        }
    result["items"] = [
        {**item, "freshness": freshness(item)} for item in result["items"]
    ]
    return result


class SessionRequest(BaseModel):
    """A session belongs to the browser which creates it."""

    seller_id: str = Field(min_length=1, max_length=120)
    use_llm: bool = False


def persona_expired(persona: dict | None) -> bool:
    """Stored persona materialization never bypasses source-time expiry."""
    if not persona or persona.get("needs_refresh"):
        return True
    now = datetime.now(timezone.utc)
    for field in persona.get("fields", {}).values():
        expires = field.get("expires_at")
        if expires and datetime.fromisoformat(expires) < now:
            return True
    return False


@app.post("/api/sessions")
async def new_session(
    body: SessionRequest, request: Request, response: Response
):
    """Start a synthetic or authorized private rehearsal, never an outbound call."""
    seller = get_seller(body.seller_id, request)
    if body.use_llm:
        operator(request)
    if (
        seller.get("metrics", {}).get("do_not_call") is True
        or seller.get("persona_tag") == "contact-suppressed"
    ):
        raise HTTPException(
            409,
            "Contact suppression is active; this seller cannot enter a calling rehearsal.",
        )
    persona = store.get("personas", seller["id"])
    if persona_expired(persona):
        events = store.seller_evidence(seller["id"])
        persona = generate_persona(
            seller,
            events,
            config.values["persona"],
            (persona or {}).get("revision", 0) + 1,
        )
        attach_pattern_evidence(
            persona,
            (store.get("metadata", "call-summary-import") or {}).get(
                "associations"
            ),
        )
        persona["indexed"] = await search.put(
            "personas", seller["id"], persona
        )
        store.put("personas", seller["id"], persona)
        await sync_persona_fields(persona, seller, events)
    identifier = secrets.token_urlsafe(24)
    cookie = set_owner_cookie(response, request)
    session_owner = hashlib.sha256(cookie.encode()).hexdigest()
    session = {
        "id": identifier,
        "seller_id": seller["id"],
        "source_mode": seller["source_mode"],
        "persona": persona,
        "turns": [
            {
                "role": "assistant",
                "text": persona["plan"]["opening"],
                "at": utcnow(),
                "method": "template",
            }
        ],
        "switches": [],
        "status": "active",
        "started_at": utcnow(),
        "use_llm": body.use_llm,
    }
    store.put("sessions", identifier, session, owner=session_owner)
    return session


@app.post('/api/operations/runs/{run_id}/rehearsal')
async def operations_rehearsal(run_id: str, request: Request, response: Response):
    """Plug the resolved V4 persona into the real existing speech/session engine."""
    import copy
    operator(request)
    ops=app.state.operations
    run=ops.repo.get('runs',run_id)
    if not run or run['status']!='completed' or not run.get('layers'):
        raise HTTPException(409,'Complete preparation before connecting a rehearsal.')
    ops.check_contact(run['seller_id'])
    seller_id=run['seller_id']
    if run['mode']=='sample':
        seller_id=run['subject_id']
        # Scenario preferences/stops must never mutate a displayed real seller.
        synthetic=copy.deepcopy(demo_sellers()[0])
        synthetic.update(id=seller_id,name='V4 synthetic rehearsal',source_mode='sample')
        if not store.get('sellers',seller_id):store.put('sellers',seller_id,synthetic)
    session=await new_session(SessionRequest(seller_id=seller_id,use_llm=False),request,response)
    persona=copy.deepcopy(session['persona'])
    for factor in run['layers']['effective']:
        key=factor['factor']
        if key in persona['fields']:
            persona['fields'][key].update(value=factor['value'],confidence=factor['confidence'],strength=factor['strength'],reason=factor['reason'],method='v4_effective_persona',citations=factor['citations'])
    persona['plan'].update(opening=run['teleprompter']['opening'])
    session.update(persona=project_derived(persona),operations_run_id=run_id,operations_context_revision=run['revision'])
    session['turns'][0]['text']=run['teleprompter']['opening']
    with store.connect() as db:
        db.execute('UPDATE sessions SET payload=? WHERE id=?',(json.dumps(session,ensure_ascii=False),session['id']))
    ops.repo.event(run_id,'voice-rehearsal','prepared',source='sarvam_websocket',mode=run['mode'],response={'session_id':session['id'],'context_revision':run['revision'],'outbound_dialing':False})
    return session


def get_session(identifier: str, request: Request) -> dict:
    """Check browser ownership and recheck private/operator privileges every time."""
    session = store.owned_session(identifier, owner(request))
    if not session:
        raise HTTPException(404, "Session not found in this browser.")
    if session["source_mode"] != "demo" or session.get("use_llm"):
        operator(request)
    return session


@app.get("/api/sessions/{identifier}")
async def read_session(identifier: str, request: Request):
    """Reload persisted turns and switches without exposing another browser."""
    return get_session(identifier, request)


class TurnRequest(BaseModel):
    """Bound transcript size before signal detection or prompt construction."""

    text: str = Field(min_length=1, max_length=2000)
    language: str | None = Field(None, max_length=12)


@app.post("/api/sessions/{identifier}/turns")
async def turn(identifier: str, body: TurnRequest, request: Request):
    """Serialize each session so simultaneous turns cannot overwrite switches."""
    return await process_turn(
        identifier,
        body.text,
        owner(request),
        is_operator(request),
        language_hint=body.language,
    )


def get_session_access(
    identifier: str, session_owner: str, authorized: bool
) -> dict:
    session = store.owned_session(identifier, session_owner)
    if not session:
        raise HTTPException(404, "Session not found in this browser.")
    if (
        session["source_mode"] != "demo" or session.get("use_llm")
    ) and not authorized:
        raise HTTPException(401, "Operator authorization required.")
    return session


async def process_turn(
    identifier: str,
    text: str,
    session_owner: str,
    authorized: bool,
    language_hint: str | None = None,
):
    """Shared domain turn path for HTTP and authenticated speech, with no synthetic requests."""
    session = get_session_access(identifier, session_owner, authorized)
    lock = session_locks.setdefault(identifier, asyncio.Lock())
    async with lock:
        session = get_session_access(identifier, session_owner, authorized)
        if session["status"] != "active":
            raise HTTPException(
                409, "The session has ended. Start a new rehearsal."
            )
        seller = store.get("sellers", session["seller_id"])
        if seller.get("metrics", {}).get("do_not_call") is True:
            session["status"] = "suppressed"
            store.put("sessions", identifier, session, owner=session_owner)
            raise HTTPException(
                409,
                "New contact-suppression evidence ended this rehearsal. Finish to review the outcome.",
            )
        baseline = store.get("personas", seller["id"])
        invalidated = (
            baseline
            and baseline.get("invalidated_at", "")
            > session["persona"]["generated_at"]
        )
        if persona_expired(session["persona"]) or invalidated:
            refreshed = generate_persona(
                seller,
                store.seller_evidence(seller["id"]),
                config.values["persona"],
                session["persona"]["revision"] + 1,
            )
            attach_pattern_evidence(
                refreshed,
                (store.get("metadata", "call-summary-import") or {}).get(
                    "associations"
                ),
            )
            # Fresh explicit live feedback remains stronger than a historical prior.
            for key, field in session["persona"]["fields"].items():
                if (
                    field.get("method") == "live_rules"
                    and field.get("expires_at", "") > utcnow()
                ):
                    refreshed["fields"][key] = field
            session["persona"] = project_derived(refreshed)
        number = sum(turn["role"] == "seller" for turn in session["turns"]) + 1
        if number > config.values["persona"]["max_turns"]:
            raise HTTPException(409, "Turn limit reached. Finish the session.")
        if not text.strip():
            raise HTTPException(422, "Enter a seller turn.")
        start = time.monotonic()
        session["timings"] = {}
        updated, switch, signal, reply = adapt(
            session["persona"],
            text,
            identifier,
            number,
            language_hint=language_hint,
        )
        session["persona"] = updated
        method = "live_rules"
        warning = None
        if session.get("use_llm") and signal != "stop":
            try:
                async with provider_semaphore:
                    reply = await providers.reply(
                        session,
                        text,
                        store.seller_evidence(session["seller_id"]),
                    )
                method = "llm"
            except ProviderUnavailable as error:
                warning = str(error)
            except asyncio.CancelledError:
                # Preserve recognized seller speech; the live broker suppresses interrupted audio.
                method = "live_rules_interrupted"
                warning = "Response generation interrupted by new speech; rule reply retained for review."
        session["turns"].extend(
            [
                {
                    "id": f"{identifier}-turn-{number}",
                    "role": "seller",
                    "text": text,
                    "signal": signal,
                    "at": utcnow(),
                },
                {
                    "role": "assistant",
                    "text": reply,
                    "at": utcnow(),
                    "method": method,
                },
            ]
        )
        decision = (
            switch if switch and switch.get("applied") is False else None
        )
        if decision:
            session.setdefault("decisions", []).append(decision)
            switch = None
        if switch:
            session["switches"].append(switch)
        if signal == "stop":
            session["status"] = "stopped"
        store.put("sessions", identifier, session, owner=session_owner)
        # Rehearsal persona revisions are not written over the seller's baseline.
        if switch:
            switch["indexed"] = await search.put(
                "switches", switch["id"], switch
            )
            store.put("sessions", identifier, session, owner=session_owner)
        return {
            "session_id": identifier,
            "reply": reply,
            "signal": signal,
            "persona": updated,
            "switch": switch,
            "switches": session["switches"],
            "decision": decision,
            "decisions": session.get("decisions", []),
            "turns": session["turns"],
            "status": session["status"],
            "latency_ms": round((time.monotonic() - start) * 1000),
            "timings": session.get("timings", {}),
            "method": method,
            "warning": warning,
        }


@app.post("/api/sessions/{identifier}/finish")
async def finish(identifier: str, request: Request):
    """Finish idempotently and retain a clear business next action."""
    get_session(identifier, request)
    async with session_locks.setdefault(identifier, asyncio.Lock()):
        session = get_session(identifier, request)
        if "outcome" not in session:
            session["outcome"] = outcome(session)
            session["finished_at"] = utcnow()
            session["status"] = "finished"
            store.put("sessions", identifier, session, owner=owner(request))
        return session["outcome"]


class SpeechRequest(BaseModel):
    """Synthesis accepts short operator-provided text, not arbitrary URLs."""

    text: str = Field(min_length=1, max_length=2000)
    language: str = "en-IN"
    speaker: str | None = Field(None, max_length=30)
    pace: float = Field(1.0, ge=0.3, le=3.0)
    pitch: float | None = Field(None, ge=-0.75, le=0.75)
    profile: Literal["v3", "v2_pitch", "v4_pitch"] | None = None


@app.post("/api/voice/synthesize")
async def speak(body: SpeechRequest, request: Request):
    """Generate actual provider audio; never substitute browser speech invisibly."""
    operator(request)
    from backend.voice_profiles import tts_parameters

    # Validate against the selected model, not the unrelated v3 voice list.
    try:
        tts_parameters(
            config,
            body.text,
            body.language,
            body.speaker,
            body.pace,
            pitch=body.pitch,
            profile=body.profile,
        )
    except ProviderUnavailable as error:
        raise HTTPException(422, str(error)) from error
    async with provider_semaphore:
        result = await providers.synthesize(
            body.text,
            body.language,
            body.speaker,
            body.pace,
            pitch=body.pitch,
            profile=body.profile,
        )
    store.put(
        "metadata",
        "provider-sarvam",
        {
            "status": "verified",
            "at": utcnow(),
            "message": "A speech generation call succeeded.",
        },
    )
    return result


@app.post("/api/voice/transcribe")
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("unknown"),
):
    """Bound uploads and avoid persisting private microphone recordings."""
    operator(request)
    mime = (file.content_type or "").split(";")[0]
    extensions = {
        "audio/webm": "webm",
        "video/webm": "webm",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/vnd.wave": "wav",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/ogg": "ogg",
    }
    if mime not in extensions:
        raise HTTPException(415, "Upload WAV, MP3, WebM, M4A or OGG audio.")
    maximum = config.values["voice"]["max_upload_mb"] * 1024 * 1024
    content = await file.read(maximum + 1)
    if not content or len(content) > maximum:
        raise HTTPException(413, "Audio must be non-empty and at most 12 MB.")
    if language not in ["unknown", *config.values["voice"]["languages"]]:
        raise HTTPException(422, "Unsupported language.")
    async with provider_semaphore:
        return await providers.transcribe(
            content, "rehearsal." + extensions[mime], mime, language
        )


@app.get("/api/voice/status")
async def voice_status():
    from backend.voice_profiles import public_profiles

    return {
        "profiles": public_profiles(config),
        "selected_profile": config.values["voice"].get("tts_profile", "v3"),
        "pool": providers.sarvam.public(),
        "batch_implemented": True,
        "live": "implemented",
        "model": config.values["voice"]["stt_model"],
        "realtime_model": config.values["voice"]["realtime_model"],
        "speakers": config.values["voice"]["speakers"],
        "default_speaker": config.values["voice"]["speaker"],
        "persona_speakers": config.values["voice"]["persona_speakers"],
        "batch": {
            "upload_mb": config.values["voice"]["batch_max_upload_mb"],
            "audio_seconds": config.values["voice"]["batch_max_audio_seconds"],
            "poll_seconds": config.values["voice"]["batch_poll_seconds"],
        },
        "languages": config.values["voice"]["languages"],
    }


@app.post("/api/voice/reset-quota/{slot}")
async def reset_voice_quota(
    slot: Literal["primary", "backup"], request: Request
):
    """After an operator tops up an unchanged key, permit a fresh quota check."""
    operator(request)
    store.put(
        "metadata",
        "sarvam-slot-" + slot,
        {
            "fingerprint": providers.sarvam.fingerprint(slot),
            "exhausted": False,
        },
    )
    return providers.sarvam.public()


@app.post("/api/speech-jobs", status_code=202)
async def create_speech_job(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    consent: bool = Form(False),
    language: str = Form("unknown"),
    num_speakers: int = Form(2),
    seller_id: str = Form(""),
    source_at: str = Form(""),
    synthetic: bool = Form(False),
):
    operator(request)
    if not consent:
        raise HTTPException(
            422, "Consent to processing this audio before uploading."
        )
    mime = (file.content_type or "").split(";")[0]
    if mime not in AUDIO_TYPES:
        raise HTTPException(415, "Upload WAV, MP3, WebM, M4A or OGG audio.")
    if (
        language not in ["unknown", *config.values["voice"]["languages"]]
        or not 1 <= num_speakers <= 20
    ):
        raise HTTPException(
            422, "Select a supported language and 1–20 speakers."
        )
    if seller_id:
        selected = get_seller(seller_id, request)
        if selected.get("source_mode") == "demo" and not synthetic:
            raise HTTPException(
                422, "Demo-linked audio must be marked synthetic."
            )
    try:
        timestamp = datetime.fromisoformat(source_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp > datetime.now(
            timezone.utc
        ) + timedelta(minutes=5):
            raise ValueError()
    except ValueError:
        raise HTTPException(
            422, "Provide the recording's actual source date with timezone."
        ) from None
    maximum = config.values["voice"]["batch_max_upload_mb"] * 1024 * 1024
    content = await file.read(maximum + 1)
    if not content or len(content) > maximum:
        raise HTTPException(
            413, "Audio exceeds the configured upload size or is empty."
        )
    job_owner = hashlib.sha256(
        set_owner_cookie(response, request).encode()
    ).hexdigest()
    job = app.state.speech.create(
        content,
        AUDIO_TYPES[mime],
        job_owner,
        language,
        num_speakers,
        seller_id,
        timestamp.isoformat(),
        synthetic,
    )
    return app.state.speech.public(job)


class RecordingTranscription(BaseModel):
    consent: bool
    language: str = "unknown"
    num_speakers: int = Field(2, ge=1, le=20)


@app.post("/api/recordings/{identifier}/transcribe", status_code=202)
async def transcribe_original_recording(
    identifier: str,
    body: RecordingTranscription,
    request: Request,
    response: Response,
):
    operator(request)
    if not body.consent:
        raise HTTPException(
            422,
            "Confirm permission to process this source recording with Sarvam.",
        )
    if body.language not in ["unknown", *config.values["voice"]["languages"]]:
        raise HTTPException(422, "Unsupported language.")
    recording = store.get("recordings", identifier)
    if not recording or not recording.get("observed_at"):
        raise HTTPException(404, "Dated recording metadata not found.")
    seller = get_seller(recording["seller_id"], request)
    if seller.get("metrics", {}).get("do_not_call") is True:
        raise HTTPException(
            409,
            "Contact suppression prevents processing this seller recording.",
        )
    # Download only from the fixed, private dataset lookup; no URL enters the response.
    content, extension = await app.state.recording_source.fetch(identifier)
    job_owner = hashlib.sha256(
        set_owner_cookie(response, request).encode()
    ).hexdigest()
    job = app.state.speech.create(
        content,
        extension,
        job_owner,
        body.language,
        body.num_speakers,
        seller["id"],
        recording["observed_at"],
    )
    job["recording_id"] = identifier
    app.state.speech.save(job)
    return app.state.speech.public(job)


@app.get("/api/speech-jobs")
async def speech_jobs(request: Request):
    operator(request)
    return {
        "items": [
            app.state.speech.public(job)
            for job in app.state.speech.list(owner(request))
        ]
    }


@app.get("/api/speech-jobs/{identifier}")
async def speech_job(identifier: str, request: Request):
    operator(request)
    return app.state.speech.public(
        app.state.speech.get(identifier, owner(request))
    )


@app.get("/api/speech-jobs/{identifier}/audio")
async def speech_job_audio(identifier: str, request: Request):
    operator(request)
    job = app.state.speech.get(identifier, owner(request))
    path = app.state.speech.directory / (identifier + "." + job["extension"])
    if not path.is_file():
        raise HTTPException(404, "Audio was deleted.")
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})


@app.post("/api/speech-jobs/{identifier}/cancel")
async def cancel_speech_job(identifier: str, request: Request):
    operator(request)
    return app.state.speech.public(
        app.state.speech.cancel(
            app.state.speech.get(identifier, owner(request))
        )
    )


@app.delete("/api/speech-jobs/{identifier}/audio")
async def delete_speech_audio(identifier: str, request: Request):
    operator(request)
    job = app.state.speech.get(identifier, owner(request))
    if job["status"] not in {"completed", "approved", "failed", "cancelled"}:
        raise HTTPException(409, "Cancel processing before deleting audio.")
    (
        app.state.speech.directory / (identifier + "." + job["extension"])
    ).unlink(missing_ok=True)
    (app.state.speech.directory / (identifier + ".normalized.wav")).unlink(
        missing_ok=True
    )
    job["audio_deleted_at"] = utcnow()
    app.state.speech.save(job)
    return {"deleted": True, "transcript_retained": True}


class ReviewedSegment(BaseModel):
    start: float = Field(ge=0, le=7200)
    end: float = Field(gt=0, le=7200)
    speaker: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=6000)


class TranscriptReview(BaseModel):
    confirmed: bool
    roles: dict[str, Literal["seller", "agent", "unknown"]]
    segments: list[ReviewedSegment] = Field(min_length=1, max_length=2000)


@app.post("/api/speech-jobs/{identifier}/approve")
async def approve_transcript(
    identifier: str, body: TranscriptReview, request: Request
):
    operator(request)
    if not body.confirmed or any(
        segment.end <= segment.start for segment in body.segments
    ):
        raise HTTPException(
            422, "Confirm human review and valid segment timings."
        )
    job = app.state.speech.get(identifier, owner(request))
    return app.state.speech.public(
        app.state.speech.approve(
            job,
            [segment.model_dump() for segment in body.segments],
            body.roles,
        )
    )


class LiveOptions(BaseModel):
    consent: bool = False
    speaker: str | None = Field(None, max_length=30)
    profile: Literal["v3", "v2_pitch", "v4_pitch"] | None = None
    pitch: float | None = Field(None, ge=-0.75, le=0.75)


@app.post("/api/sessions/{identifier}/live-ticket")
async def live_ticket(
    identifier: str, request: Request, body: LiveOptions | None = None
):
    operator(request)
    session = get_session(identifier, request)
    if not body or not body.consent:
        raise HTTPException(
            422,
            "Consent to live speech processing and automatic rehearsal turns first.",
        )
    if session["status"] != "active":
        raise HTTPException(409, "Start an active rehearsal first.")
    from backend.voice_profiles import tts_parameters

    selected_profile = body.profile or config.values["voice"].get(
        "tts_profile", "v3"
    )
    suggested_speaker = body.speaker
    if not suggested_speaker and selected_profile == "v3":
        suggested_speaker = config.values["voice"]["persona_speakers"].get(
            session["seller_id"], config.values["voice"]["speaker"]
        )
    try:
        parameters = tts_parameters(
            config,
            "Voice capability check.",
            "en-IN",
            suggested_speaker,
            config.values["voice"]["pace"],
            pitch=body.pitch,
            profile=selected_profile,
            streaming=True,
        )
    except ProviderUnavailable as error:
        raise HTTPException(422, str(error)) from error
    result = app.state.live.grant(
        identifier,
        owner(request),
        parameters["speaker"],
        profile=selected_profile,
        pitch=body.pitch,
    )
    app.state.live_authority[result["ticket"]] = {
        "expires": time.monotonic()
        + config.values["voice"]["live_ticket_seconds"],
        "session": request.cookies.get(access.cookie_name)
        if access.session(request.cookies.get(access.cookie_name))["operator"]
        else None,
    }
    app.state.live_authority = {
        key: value
        for key, value in app.state.live_authority.items()
        if value["expires"] > time.monotonic()
    }
    return result


@app.websocket("/api/sessions/{identifier}/live")
async def live_socket(websocket: WebSocket, identifier: str):
    # HTTP middleware does not protect WebSockets: validate independently before provider access.
    if (
        websocket.headers.get("origin")
        not in config.values["voice"]["live_origins"]
    ):
        await websocket.close(code=1008)
        return
    cookie = websocket.cookies.get(access.owner_cookie, "")
    session_owner = (
        hashlib.sha256(cookie.encode()).hexdigest() if cookie else ""
    )
    await websocket.accept()
    revocation = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw) > 300:
            raise ValueError()
        token = json.loads(raw).get("ticket", "")
        grant = (
            app.state.live.consume(token, identifier, session_owner)
            if isinstance(token, str)
            else False
        )
        authority = app.state.live_authority.pop(token, None)
        if (
            not grant
            or not authority
            or authority["expires"] <= time.monotonic()
        ):
            await websocket.close(code=1008)
            return

        def authorized_live_session():
            if (
                authority["session"]
                and not access.session(authority["session"])["operator"]
            ):
                raise HTTPException(
                    401, "Password session expired or was revoked."
                )
            return get_session_access(identifier, session_owner, True)

        async def watch_revocation():
            while True:
                await asyncio.sleep(1)
                if (
                    authority["session"]
                    and not access.session(authority["session"])["operator"]
                ):
                    await websocket.close(code=1008)
                    return

        async def live_turn(text, language_hint=None):
            authorized_live_session()
            return await process_turn(
                identifier,
                text,
                session_owner,
                True,
                language_hint=language_hint,
            )

        session = authorized_live_session()
        revocation = asyncio.create_task(watch_revocation())
        if session["status"] != "active":
            await websocket.close(code=1008)
            return
        speaker = grant["speaker"] or config.values["voice"][
            "persona_speakers"
        ].get(session["seller_id"], config.values["voice"]["speaker"])
        await app.state.live.run(
            websocket,
            identifier,
            session_owner,
            live_turn,
            authorized_live_session,
            speaker,
            profile=grant.get("profile"),
            pitch=grant.get("pitch"),
        )
    except (WebSocketDisconnect, ValueError, TimeoutError, HTTPException):
        pass
    finally:
        if revocation:
            revocation.cancel()
            await asyncio.gather(revocation, return_exceptions=True)
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.get("/api/voice/demos")
async def voice_demos():
    manifest = config.data_dir / "speech-demos" / "manifest.json"
    return {
        "items": json.loads(manifest.read_text()) if manifest.is_file() else []
    }


@app.get("/api/voice/demos/{identifier}/audio")
async def voice_demo_audio(identifier: str):
    if not re.fullmatch(r"[a-z0-9-]{1,60}", identifier):
        raise HTTPException(404, "Demo clip not found.")
    manifest = config.data_dir / "speech-demos" / "manifest.json"
    items = json.loads(manifest.read_text()) if manifest.is_file() else []
    if not any(item["id"] == identifier for item in items):
        raise HTTPException(404, "Demo clip not found.")
    return FileResponse(
        config.data_dir / "speech-demos" / (identifier + ".wav"),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/catalog")
async def catalog():
    """Only aggregate and schema metadata from the supplied files are public."""
    path = ROOT / "data/catalog.json"
    result = (
        json.loads(path.read_text())
        if path.exists()
        else {"status": "profiling_pending", "files": []}
    )
    return {
        **result,
        "storage": await search.status(),
        "imported": {
            table: store.count(table)
            for table in ("sellers", "evidence", "recordings", "personas")
        },
    }


@app.get("/api/recordings")
async def recordings(
    request: Request,
    cohort: str = Query("", max_length=80),
    page: int = Query(1, ge=1),
    dataset: Literal["demo", "private"] = "demo",
):
    """Anonymous visitors see aggregate cohorts, never recording locations."""
    condition = "WHERE json_extract(payload,'$.cohort')=?" if cohort else ""
    args = [cohort] if cohort else []
    with store.connect() as db:
        rows = db.execute(
            "SELECT COALESCE(json_extract(payload,'$.cohort'),'unclassified') AS cohort, COUNT(*) AS count FROM recordings GROUP BY cohort"
        ).fetchall()
        selected_count = db.execute(
            f"SELECT COUNT(*) FROM recordings {condition}", args
        ).fetchone()[0]
        breakdowns = {}
        for name, field in [
            ("duration", "duration_bucket"),
            ("outcome", "outcome"),
            ("transcript", "transcript_status"),
            ("bot_version", "bot_version"),
        ]:
            groups = db.execute(
                f"SELECT COALESCE(json_extract(payload,'$.{field}'),'unknown') AS label,COUNT(*) AS count FROM recordings {condition} GROUP BY label ORDER BY count DESC",
                args,
            ).fetchall()
            breakdowns[name] = [dict(row) for row in groups]
    cohorts = [{"label": row["cohort"], "count": row["count"]} for row in rows]
    result = {
        "items": [],
        "total": selected_count,
        "all_recordings": store.count("recordings"),
        "cohort": cohort or "all",
        "cohorts": cohorts,
        "breakdowns": breakdowns,
        "page": page,
        "mode": "Aggregate metadata only",
        "notice": "Recordings are private. Supplied URLs are not exposed; transcription requires audio access and review.",
    }
    if dataset == "private":
        operator(request)
        with store.connect() as db:
            rows = db.execute(
                f"SELECT payload FROM recordings {condition} LIMIT 30 OFFSET ?",
                [*args, (page - 1) * 30],
            ).fetchall()
        result["items"] = [json.loads(row[0]) for row in rows]
        result["mode"] = (
            "Private metadata; recordings remain on source systems"
        )
    return result


@app.get("/api/integrations")
async def integrations():
    """Readiness describes capability and credentials, not invented live success."""
    es = await search.status()
    items = [
        {
            "id": "elasticsearch",
            "name": "Elasticsearch",
            "status": es["status"],
            "configured": bool(config.es_url),
            "description": "Searchable evidence and derived persona fields.",
            "capabilities": [
                "versioned mappings",
                "bulk indexing",
                "evidence search",
            ],
        },
        {
            "id": "llm",
            "name": "LLM gateway",
            "status": "configured"
            if config.llm_api_key and config.llm_internal_key
            else "not_configured",
            "configured": bool(config.llm_api_key and config.llm_internal_key),
            "description": "Model-assisted persona inference and grounded rehearsal replies.",
            "capabilities": [
                "structured output",
                "evidence validation",
                "operator only",
            ],
        },
        {
            "id": "sarvam",
            "name": "Sarvam voice",
            "status": "configured"
            if (config.sarvam_key or config.sarvam_backup_key)
            else "not_configured",
            "configured": bool(config.sarvam_key or config.sarvam_backup_key),
            "description": "REST and live speech; durable batch speaker labels with human review. Quota backup is automatic.",
            "credential_pool": providers.sarvam.public(),
            "capabilities": [
                "STT",
                "TTS",
                "multiple languages",
                "batch diarization",
                "live duplex",
                "quota fallback",
            ],
        },
    ]
    descriptions = {
        "context": "Recent activities, customer tier and seller context",
        "company": "Company profile from the reference integration",
        "sts": "Sales tracking history; supplied source marked unavailable",
        "buyleads": "Recent buyer requirements",
        "enquiries": "Direct buyer enquiries",
        "chat": "Recent seller messages with bounded pagination",
        "pns": "Proxy-number call metadata",
        "redash_hotlead": "Bounded call summaries and raw meeting outcomes; fixed query on datasource 13",
        "redash_recordings": "Recording metadata, not transcript text; fixed query on datasource 13",
        "redash_executive": "Executive call count for the approved seller; fixed query on datasource 13",
        "redash_pns": "PNS warehouse call records; verified table on datasource 8",
    }
    for connector in ConnectorService(config.values).statuses():
        key = connector["id"]
        check = store.get("metadata", f"connector-{key}") or {}
        items.append(
            {
                **connector,
                "status": check.get("status", connector["status"])
                if check.get("mode") == connector.get("mode")
                and check.get("config_revision") == config.admin.revision
                else connector["status"],
                "last_result": {
                    key: check.get(key)
                    for key in (
                        "status",
                        "source_status",
                        "mode",
                        "config_revision",
                        "at",
                    )
                }
                if check
                else None,
                "description": descriptions.get(key, connector["name"]),
                "capabilities": [
                    "read-only",
                    "bounded records",
                    "operator only",
                ],
                "last_checked": check.get("at"),
            }
        )
    for item in items:
        if item["id"] in {"llm", "sarvam"}:
            checked = store.get("metadata", f"provider-{item['id']}")
            if checked:
                item.update(
                    status=checked["status"],
                    last_checked=checked["at"],
                    message=checked.get("message"),
                )
    return {"items": items, "refresh": app.state.sync_worker.summary()}


class SyncRequest(BaseModel):
    """Refresh only a known seller and a configured source."""

    seller_id: str = Field(min_length=1, max_length=120)


@app.post("/api/integrations/{identifier}/sync")
async def sync_source(identifier: str, body: SyncRequest, request: Request):
    """Connectors normalize read-only results without exposing raw private bodies."""
    operator(request)
    identifier, seller = validate_sync(identifier, body.seller_id, request)
    try:
        result = await app.state.sync_worker.fetch_apply(
            identifier, seller["id"]
        )
        store.put("metadata", f"connector-{identifier}", result)
        return result
    except (ConnectorError, JobConflict) as error:
        result = {
            "status": getattr(error, "code", "revision_conflict"),
            "at": utcnow(),
        }
        store.put("metadata", f"connector-{identifier}", result)
        raise HTTPException(
            503,
            f"Source {getattr(error, 'code', 'revision_conflict')}: {error}",
        ) from error


def validate_sync(
    source: str, seller_id: str, request: Request
) -> tuple[str, dict]:
    """Authorize fixed source templates and existing numeric private sellers."""
    operator(request)
    source = SOURCE_ALIASES.get(source, source)
    if source not in SOURCES:
        raise HTTPException(
            422, "This integration does not support seller sync."
        )
    try:
        source, target = ConnectorService(
            config.values.get("connectors", {})
        ).ensure_target(source, seller_id)
    except ConnectorError as error:
        raise HTTPException(422, str(error)) from error
    seller = get_seller(target, request)
    return source, seller


@app.exception_handler(JobConflict)
async def job_conflict(request: Request, error: JobConflict):
    """Expose only deliberate safe queue conflicts."""
    return JSONResponse({"detail": str(error)}, status_code=409)


class JobRequest(SyncRequest):
    """Idempotency keys identify one source/seller submission, never a URL."""

    request_key: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")


class RetryRequest(BaseModel):
    """A new request key permits safe retry while retaining the prior receipt."""

    request_key: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{1,120}$")


class ScheduleRequest(SyncRequest):
    """Schedules are absent by default and can be stored paused."""

    source: str = Field(min_length=1, max_length=30)
    interval_seconds: int = Field(3600, ge=300, le=86400)
    enabled: bool = False


class SchedulePatch(BaseModel):
    """Pausing future runs does not cancel an existing queued job."""

    interval_seconds: int | None = Field(None, ge=300, le=86400)
    enabled: bool | None = None


@app.post("/api/integrations/{identifier}/jobs", status_code=202)
async def queue_sync(identifier: str, body: JobRequest, request: Request):
    """Return immediately with a durable receipt, even when the source is unavailable."""
    identifier, seller = validate_sync(identifier, body.seller_id, request)
    job, created = app.state.sync_worker.repository.enqueue(
        identifier, seller["id"], body.request_key
    )
    return {"job": job, "created": created}


@app.get("/api/sync-jobs")
async def list_sync_jobs(
    request: Request, limit: int = Query(30, ge=1, le=100)
):
    """Private seller IDs in job history are operator-only."""
    operator(request)
    worker = app.state.sync_worker
    return {"items": worker.repository.list_jobs(limit), **worker.summary()}


@app.get("/api/sync-jobs/{identifier}")
async def read_sync_job(identifier: str, request: Request):
    """Read sanitized result counts and state without raw fetched context."""
    operator(request)
    job = app.state.sync_worker.repository.get(identifier)
    if not job:
        raise HTTPException(404, "Refresh job not found.")
    return job


@app.post("/api/sync-jobs/{identifier}/cancel")
async def cancel_sync_job(identifier: str, request: Request):
    """Fence a running result before it can mutate local evidence."""
    operator(request)
    try:
        return app.state.sync_worker.repository.cancel(identifier)
    except KeyError as error:
        raise HTTPException(404, "Refresh job not found.") from error


@app.post("/api/sync-jobs/{identifier}/retry", status_code=202)
async def retry_sync_job(
    identifier: str, request: Request, body: RetryRequest | None = None
):
    """Retry creates a linked job with its own bounded attempt budget."""
    operator(request)
    repository = app.state.sync_worker.repository
    previous = repository.get(identifier)
    if not previous:
        raise HTTPException(404, "Refresh job not found.")
    validate_sync(previous["source"], previous["seller_id"], request)
    job, created = repository.retry(
        identifier, body.request_key if body else None
    )
    return {"job": job, "created": created}


@app.post("/api/index-deliveries/retry")
async def retry_index_deliveries(request: Request):
    """Wake pending index deliveries; keep current in-flight claims intact."""
    operator(request)
    return {"made_due": app.state.sync_worker.repository.retry_index()}


@app.get("/api/sync-schedules")
async def sync_schedules(request: Request):
    """Return protected source/seller scheduling metadata."""
    operator(request)
    return {
        "items": app.state.sync_worker.repository.schedules(),
        "limits": {
            key: config.values["jobs"][key]
            for key in (
                "min_interval_seconds",
                "max_interval_seconds",
                "max_schedules",
            )
        },
    }


def save_schedule(body: ScheduleRequest, request: Request) -> dict:
    """Apply configured frequency bounds after the typed API bounds."""
    source, seller = validate_sync(body.source, body.seller_id, request)
    limits = config.values["jobs"]
    if (
        not limits["min_interval_seconds"]
        <= body.interval_seconds
        <= limits["max_interval_seconds"]
    ):
        raise HTTPException(
            422, "Refresh interval is outside the configured bounds."
        )
    return app.state.sync_worker.repository.save_schedule(
        source, seller["id"], body.interval_seconds, body.enabled
    )


@app.post("/api/sync-schedules")
async def create_sync_schedule(body: ScheduleRequest, request: Request):
    """Upsert a known source/seller pair; first run is one interval from now."""
    return save_schedule(body, request)


@app.patch("/api/sync-schedules/{identifier}")
async def patch_sync_schedule(
    identifier: str, body: SchedulePatch, request: Request
):
    """Enable, pause or reschedule a bounded source refresh."""
    operator(request)
    existing = next(
        (
            s
            for s in app.state.sync_worker.repository.schedules()
            if s["id"] == identifier
        ),
        None,
    )
    if not existing:
        raise HTTPException(404, "Refresh schedule not found.")
    if body.enabled is False:
        try:
            return app.state.sync_worker.repository.pause_schedule(
                identifier, body.interval_seconds
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    payload = {**existing, **body.model_dump(exclude_none=True)}
    return save_schedule(ScheduleRequest(**payload), request)


@app.get("/api/settings")
async def settings(request: Request):
    """Credential readiness is boolean; values are never echoed, even to operators."""
    return {**config.public(), "operator_authenticated": is_operator(request)}


class SettingsPatch(BaseModel):
    """Only low-risk nonsecret knobs are editable through the operator portal."""

    evidence_limit: int | None = Field(None, ge=3, le=30)
    max_turns: int | None = Field(None, ge=5, le=100)
    pace: float | None = Field(None, ge=0.5, le=2)


@app.patch("/api/settings")
async def update_settings(body: SettingsPatch, request: Request):
    """Persist validated overrides for process restarts."""
    operator(request)
    update = body.model_dump(exclude_none=True)
    changes = {
        ("voice." if key == "pace" else "persona.") + key: value
        for key, value in update.items()
    }
    config.admin.update(changes, config.admin.view()["revision"])
    # Retain a compatibility audit snapshot; it is no longer a second authority.
    saved = {**(store.get("metadata", "runtime_settings") or {}), **update}
    store.put("metadata", "runtime_settings", saved)
    return {"status": "saved", **config.public()}


@app.get("/api/roadmap")
async def delivery_map():
    """Expose completed, credential-dependent and planned work in one portal."""
    return readiness_map()


@app.get("/api/schema")
@app.get("/schema")
async def schema():
    """Expose validated input schemas and editable configuration metadata."""
    return {
        "version": config.values["app"]["version"],
        "fields": config.public()["schema"],
        "models": {
            model.__name__: model.model_json_schema()
            for model in [
                PersonaRequest,
                SessionRequest,
                TurnRequest,
                SpeechRequest,
                SyncRequest,
                SettingsPatch,
                EvidenceRequest,
                JobRequest,
                RetryRequest,
                ScheduleRequest,
                SchedulePatch,
                JobSettings,
            ]
        },
    }


def html_file(filename: str) -> HTMLResponse:
    """Inject deployment tokens into first-party static HTML only."""
    path = ROOT / "frontend" / filename
    if not path.exists():
        return HTMLResponse(
            "Persona Studio is being prepared.", status_code=503
        )
    return HTMLResponse(
        path.read_text()
        .replace("__BASE_PATH__", config.base_path)
        .replace("__APP_VERSION__", config.values["app"]["version"])
        .replace(
            "__PUBLIC_BASE_URL__",
            os.getenv(
                "PUBLIC_BASE_URL",
                "https://portfolio.aidhunik.com/hackathon/seller-persona",
            ).rstrip("/"),
        )
    )


@app.get("/pm", response_class=HTMLResponse)
@app.get("/walkthrough", response_class=HTMLResponse)
@app.get("/walkthrough/", response_class=HTMLResponse)
@app.get("/studio", response_class=HTMLResponse)
@app.get("/studio/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    """Serve the browser portal at the proxy-stripped root."""
    response = html_file("index.html")
    set_owner_cookie(response, request)
    return response


@app.get("/analysis", response_class=HTMLResponse)
@app.get("/analysis/", response_class=HTMLResponse)
async def analysis_page(request: Request):
    """Serve the public aggregate atlas; private drilldown still requires access."""
    response = html_file("analysis.html")
    set_owner_cookie(response, request)
    return response


@app.get("/api/analysis/report")
async def research_report():
    """Publish only the reviewed aggregate artifact, never source CSV rows."""
    return analysis_report(ROOT / "data/analysis-report.json")


@app.get("/api/analysis/live")
async def research_live(
    view: Literal[
        "outcomes", "versions", "durations", "conflicts"
    ] = "outcomes",
    period: Literal[
        "all", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"
    ] = "all",
):
    """The public live button runs a bounded, fixed aggregate ES contract."""
    return await live_aggregate(search, view, period)


@app.get("/api/personas/{identifier}/deconstruction")
async def indexed_persona_fields(
    identifier: str,
    request: Request,
    strength: Literal["strong", "weak", "default", "legacy_unclassified"]
    | None = None,
):
    """Authorize seller scope before exposing actual indexed persona decisions."""
    seller = get_seller(identifier, request)
    result = await persona_fields(
        search, identifier, seller.get("source_mode", "private"), strength
    )
    current = store.get("personas", identifier) or {}
    result["current_revision"] = current.get("revision")
    result["needs_refresh"] = persona_expired(current)
    result["revision_matches"] = all(
        item.get("persona_revision") == current.get("revision")
        for item in result["items"]
    )
    return result


@app.get("/api/analysis/export")
async def research_export():
    """An explicitly aggregate-only export supports independent verification."""
    return JSONResponse(
        analysis_report(ROOT / "data/analysis-report.json"),
        headers={
            "Content-Disposition": 'attachment; filename="sellergenome-analysis.json"'
        },
    )


@app.get("/slide", response_class=HTMLResponse)
async def slide():
    """Printable one-slide submission artifact."""
    return html_file("slide.html")


@app.get("/sw.js")
async def service_worker():
    """Network-only worker: never cache private APIs, credentials or recordings."""
    return FileResponse(
        ROOT / "frontend/sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": (config.base_path or "") + "/",
            "Cache-Control": "no-cache",
        },
    )


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


class InsightRequest(BaseModel):
    """Explicit bounded extraction never starts a full-corpus paid sweep."""

    use_llm: bool = False


@app.post("/api/sellers/{identifier}/insights")
async def extract_insights(
    identifier: str, body: InsightRequest, request: Request
):
    operator(request)
    get_seller(identifier, request)
    async with provider_semaphore:
        return await app.state.insights.extract(
            identifier, use_llm=body.use_llm
        )


@app.get("/api/insights")
async def insight_search(
    request: Request,
    q: str = Query("", max_length=200),
    seller_id: str = Query("", max_length=120),
    trait: str = Query("", max_length=80),
    outcome_filter: str = Query("", max_length=80),
    actor: str = Query("", max_length=30),
):
    operator(request)
    result = await search.insights(
        q, seller_id, "private", trait, outcome_filter, actor
    )
    if result is not None:
        return result
    if trait or outcome_filter or actor:
        raise HTTPException(
            503,
            "Elasticsearch is unavailable; structured insight filters require the search index. Retry after indexing recovery.",
        )
    items = store.seller_evidence(seller_id, 100) if seller_id else []
    items = [
        event
        for event in items
        if event.get("source_mode") == "private"
        and event.get("insights")
        and (
            not q or q.lower() in json.dumps(event, ensure_ascii=False).lower()
        )
    ]
    return {
        "items": items,
        "total": len(items),
        "engine": "sqlite bounded seller fallback",
        "limitation": "Elasticsearch unavailable; select a seller for fallback evidence.",
    }


def source_targets() -> dict:
    """Expose only protected approved IDs and explicit sample namespaces."""
    service = ConnectorService(config.values.get("connectors", {}))
    result = service.approved_targets()
    return result if isinstance(result, dict) else {"items": result}


async def workshop_run(
    scenario, body, request: Request, response: Response
) -> dict:
    """Execute the existing backend path and name what the result actually proves."""
    action = scenario["live_action"]
    selected = body.seller_id
    if not selected:
        demos = store.sellers("", "", 1, 1, "demo")["items"]
        selected = demos[0]["id"] if demos else None
    origin = (
        "synthetic_backend"
        if selected and selected.startswith("demo-")
        else "dataset"
    )
    defaults = scenario.get("live_input", {})
    options = body.input if isinstance(body.input, dict) else {}
    if action == "source_refresh":
        source = body.source or defaults.get("source", "context")
        if not body.seller_id:
            raise HTTPException(
                422,
                "Choose an approved source test target before running this source.",
            )
        result = await queue_sync(
            source, JobRequest(seller_id=body.seller_id), request
        )
        return {
            "status": "queued",
            "execution_origin": "source_job",
            "result": result,
            "limitation": "A queued job is not retrieval success. Inspect its receipt and indexing status.",
            "route": "integrations",
        }
    if action == "persona":
        result = await create_persona(
            selected,
            PersonaRequest(use_llm=options.get("use_llm", False)),
            request,
        )
        return {
            "status": "completed",
            "execution_origin": origin,
            "result": result,
            "limitation": "Heuristic confidence describes evidence quality, not a calibrated success probability.",
        }
    if action == "evidence":
        result = await seller_detail(selected, request)
        return {
            "status": "completed",
            "execution_origin": origin,
            "result": {
                "seller_id": selected,
                "evidence": result["evidence"],
                "persona": result["persona"],
            },
            "limitation": "Source freshness and actor attribution constrain what may be inferred.",
        }
    if action in {"adaptation", "outcome"}:
        session = await new_session(
            SessionRequest(seller_id=selected), request, response
        )
        session_owner = owner(request)
        if not session_owner:
            raise HTTPException(
                409,
                "Refresh the workshop once to establish browser ownership, then retry.",
            )
        texts = options.get("texts") or [
            options.get("text")
            or defaults.get("text")
            or "Please explain one thing at a time."
        ]
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= 8
            or any(
                not isinstance(text, str) or len(text) > 2000 for text in texts
            )
        ):
            raise HTTPException(
                422, "Supply one to eight short rehearsal turns."
            )
        results = []
        for text in texts:
            results.append(
                await process_turn(session["id"], text, session_owner, True)
            )
            if results[-1]["status"] != "active":
                break
        completed = await finish(session["id"], request)
        return {
            "status": "completed",
            "execution_origin": origin,
            "result": {
                "session_id": session["id"],
                "turns": results,
                "outcome": completed,
            },
            "limitation": "Signals are inferred from recognized words; this rehearsal does not validate acoustic emotion or business conversion.",
        }
    if action == "voice":
        from backend.voice_profiles import public_profiles

        return {
            "status": "action_required",
            "execution_origin": "capability_check",
            "action_required": "Open the live voice controls, consent to microphone processing, and start a rehearsal.",
            "route": "lab",
            "result": {
                "speech": await voice_status(),
                "profiles": public_profiles(config),
            },
            "limitation": "This receipt checked readiness only. Voice generation and interruption are measured in the consented live session.",
        }
    if action == "recordings":
        return {
            "status": "action_required",
            "execution_origin": "dataset",
            "route": "speech",
            "action_required": "Choose or upload a recording, confirm consent, then review speaker roles before ingesting seller evidence.",
            "result": await recordings(request, "", 1, "private"),
            "limitation": "Recording metadata is not a transcript; speaker clusters require human role review.",
        }
    if action == "outcomes":
        return {
            "status": "completed",
            "execution_origin": origin,
            "result": await seller_detail(selected, request),
            "limitation": "Meeting fixed does not mean a meeting was held or a sale completed.",
        }
    if action in {"readiness", "roadmap"}:
        return {
            "status": "completed",
            "execution_origin": "backend_status",
            "result": {
                "capabilities": readiness_map(),
                "storage": await search.status(),
                "jobs": app.state.sync_worker.summary(),
            },
            "limitation": "Readiness is separate from a verified successful source call or provider run.",
        }
    raise HTTPException(
        501,
        "This target workflow is not implemented; its illustrative Demo remains available.",
    )


app.include_router(
    portal_router(
        lambda: config,
        lambda: access,
        lambda: workshop,
        operator,
        is_operator,
        owner,
        set_owner_cookie,
        {"targets": source_targets, "run": workshop_run},
    )
)


class InsightCohortRequest(BaseModel):
    """Explicit bounded selection; never an implicit full-corpus inference job."""

    seller_ids: list[str] = Field(min_length=1, max_length=50)
    use_llm: bool = False


def cohort_owner(request: Request) -> str:
    operator(request)
    identity = owner(request)
    if not identity:
        raise HTTPException(
            409, "Open the portal to establish browser ownership first."
        )
    return identity


@app.post("/api/insight-jobs", status_code=202)
async def create_insight_job(body: InsightCohortRequest, request: Request):
    return {
        "job": app.state.insight_cohorts.create(
            cohort_owner(request), body.seller_ids, body.use_llm
        )
    }


@app.get("/api/insight-jobs")
async def list_insight_jobs(request: Request):
    return {"items": app.state.insight_cohorts.list(cohort_owner(request))}


@app.get("/api/insight-jobs/{identifier}")
async def get_insight_job(identifier: str, request: Request):
    service = app.state.insight_cohorts
    return service.public(service.get(identifier, cohort_owner(request)))


@app.post("/api/insight-jobs/{identifier}/cancel")
async def cancel_insight_job(identifier: str, request: Request):
    return app.state.insight_cohorts.cancel(identifier, cohort_owner(request))


from backend.operations_api import operations_router
app.include_router(operations_router(lambda: app.state.operations, operator, is_operator))

@app.get("/operations", response_class=HTMLResponse)
@app.get("/operations/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/meetings", response_class=HTMLResponse)
@app.get("/meetings/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/v4", response_class=HTMLResponse)
@app.get("/v4/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/v4/{page}", response_class=HTMLResponse, include_in_schema=False)
async def operations_page(request: Request, page: str = ""):
    """V4 operations console, kept intact under /v4 (and its old /operations, /meetings paths)."""
    if page and page not in {"buyer", "admin", "sarvam", "meetings"}:
        raise HTTPException(404, "Not found")
    response = html_file("operations.html")
    set_owner_cookie(response, request)
    return response


from backend.genome.api import genome_router

app.include_router(genome_router(lambda: getattr(app.state, "genome", None), is_operator,
                                 lambda: getattr(getattr(app.state, "operations", None), "sarvam_lab", None)))


@app.get("/", response_class=HTMLResponse)
@app.get("/buyer", response_class=HTMLResponse)
@app.get("/buyer/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/genome", response_class=HTMLResponse)
@app.get("/sarvam", response_class=HTMLResponse)
@app.get("/sarvam/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin", response_class=HTMLResponse)
@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/call/{run_id}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/persona/{run_id}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/buyer/call/{call_id}", response_class=HTMLResponse, include_in_schema=False)
async def command_center_page(request: Request, run_id: str = "", call_id: str = ""):
    """V5 Command Center shell; the client router renders the page for the path."""
    response = html_file("cc/index.html")
    set_owner_cookie(response, request)
    return response

# Public aggregates and operator-only local call inspection share the same study.
from backend.analysis_review_api import analysis_review_router

app.include_router(analysis_review_router(ROOT, lambda: config, operator))
