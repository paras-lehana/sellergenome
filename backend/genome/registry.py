"""API registry and executor (owner points 5 and 10).

Each upstream the pipeline can hit is one ``ApiDef``: endpoint template, parameters, headers,
where its key goes, mode (live / mock / off), timeout, cache TTL and the cache-read / persist
switches. Admins edit these in **Admin → APIs**; edits are revisioned in
``PRIVATE_CONFIG_DIR/genome-apis.json``. Endpoints may only point at allow-listed hosts.

``ApiExecutor.call`` is the single path every hit takes:
    Elastic cache read → (live | mock | dataset | redash) → async persist → hit log
and returns a result that carries the exact request (secrets redacted), response, timings,
origin and the Elasticsearch query that was used — everything the inspector shows.
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import hashlib
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlencode

import httpx
import yaml

from backend.genome import mock_upstream as mocks
from backend.genome.settings import ROOT, private_dir, utc_now

SECRET_MARK = "[server-held]"


@dataclass
class ApiDef:
    id: str
    label: str
    group: str
    kind: str  # http | redash | dataset | internal
    method: str = "GET"
    url: str = ""
    params: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    secret_name: str = ""
    mode: str = "mock"  # live | mock | off
    timeout_seconds: float = 8.0
    ttl_hours: float = 24.0
    cache_read: bool = True
    persist: bool = True
    contract: str = "sample"  # sample (real sample seen) | guess (documented guess) | fixed | n/a
    reachable: str = "unknown"  # what we measured from this server
    notes: str = ""
    editable: bool = True
    body_format: str = "json"  # json | form (application/x-www-form-urlencoded)
    via_gateway: bool = False  # IndiaMART-internal host → sent through the configurable pass-through gateway


# IndiaMART's internal hosts do not resolve from this server. A pass-through gateway (the "IM API
# Portal", Admin → APIs → Gateway) forwards <gateway base> + the original path to the original host.
# URLs below are the ORIGINAL upstream URLs from docs/inputs/indiamart-apis (API-REQUIRED.md); the
# executor swaps scheme+host for the gateway at send time, so switching the gateway off sends direct.
COMPANY_BASE = "http://company.imutils.com/wservce/company/detail"
LEADS_BASE = "http://leads.imutils.com/wservce/buyleads"
MERP_BASE = "http://merp.intermesh.net/go/api"
TESTED = "2026-10-09 via gateway, approved GLIDs"

DEFAULTS: list[ApiDef] = [
    # ---- IndiaMART · seller context (live via the gateway for approved GLIDs) ----
    ApiDef("company.alias", "Company API · GLID → showroom alias", "IndiaMART · Seller", "http", "GET",
           COMPANY_BASE + "/glusrid/{glid}/alias//cat_link//token/{secret}/req_server/home_server/modid/tdw/",
           secret_name="COMPANY_TOKEN", mode="live", ttl_hours=720, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="First hop. URL_DETAIL.FREESHOWROOM_ALIAS feeds the detail call; URL_DETAIL.CUSTWT is custtypeweight. Token sits in the path."),
    ApiDef("company.detail", "Company API · company details", "IndiaMART · Seller", "http", "GET",
           COMPANY_BASE + "/glusrid//alias/{alias}/cat_link/index.html/token/{secret}/req_server/home_server/modid/tdw/",
           secret_name="COMPANY_TOKEN", mode="live", ttl_hours=720, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="DATA.COMPANYDETAIL (company, BIZ, CUSTWT, addresses, locality), PRDSERV products with price and ISQ, FACTSHEET (GST, turnover, year)."),
    # 5.12.1 (owner, 2026-10-10): the competitor API. mcatid = the seller's main category from the company API
    # (DATA.BREADCRUMB.MCAT_ID); city-wise with city=<lower-case, hyphenated city>.
    ApiDef("market.listing", "Products listing · competitors in the seller's category and city", "IndiaMART · Seller", "http", "GET",
           "http://company.imutils.com/wservce/products/listing/",
           # 5.13.5: the city filter is `cityflname` (lower case, hyphens: "navi-mumbai"); `city` was ignored (cityid 0, all-India
           # rows). An unknown city answers an error, and the orchestrator then asks again for all of India.
           params={"token": "{secret}", "modid": "DIR", "mcatid": "{mcatid}", "start": "1", "end": "28", "cityflname": "{city_slug}"},
           secret_name="COMPANY_TOKEN", mode="live", timeout_seconds=12.0, ttl_hours=24, contract="sample", via_gateway=True,
           reachable="through the gateway since 2026-10-10 (route /wservce/products/listing/)",
           notes="The first 28 search results in the seller's main category and city (data[]: COMPANY, gl_id, city, member_since, "
                 "wt_avg, total_ratings_cnt, Trustseal_url, glusr_paid_url, prd_price): the competitors the agents name on the call."),
    # 5.10.1: userCtx (404 on every call) is replaced by generateContextUID, also at the gateway (route generate-context-uid).
    ApiDef("context.global", "Global Context · generateContextUID (mapping_id)", "IndiaMART · Seller", "http", "POST",
           MERP_BASE + "/globalcontext/v1/generateContextUID",
           body={"AK": "{secret}", "empid": "{empid|int}", "mapping_type": 1, "mapping_value": "{glid}", "source_id": 1,
                 "data_segments": "kycdetails"},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=24, contract="guess", via_gateway=True,
           reachable="gateway route added 2026-10-10 (generate-context-uid); replaces userCtx, which answered 404",
           notes="mapping_type 1 = GLID, source_id 1. Returns the context UID getContext takes as mapping_id (plus KYC when it "
                 "sends the segment). No response seen yet, so the UID is looked for under several keys."),
    ApiDef("context.getcontext", "Global Context · getContext", "IndiaMART · Seller", "http", "GET",
           MERP_BASE + "/globalcontext/v1/x/getContext",
           params={"AK": "{secret}", "empid": "{empid}", "mapping_id": "{mapping_id}", "source_uid": "{source_uid}", "data_segments": "all"},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=24, contract="guess", via_gateway=True,
           reachable=f"reachable · {TESTED}: 'Mapping Not Found' by source_uid; needs mapping_id from generateContextUID",
           notes="Vintage, last 10 activities, nature of business, transcripts, summaries, buy-lead and call counts, tickets. "
                 "Called with mapping_id when generateContextUID returns one, otherwise with source_uid = GLID (the API accepts either)."),
    ApiDef("context.insights", "Insights API · seller insights", "IndiaMART · Seller", "http", "POST", "",
           body={"glid": "{glid}"}, ttl_hours=24, contract="sample", reachable="endpoint not supplied",
           notes="Seller summary, 7 insight categories and doubts — shape copied from the production hot-lead log."),
    ApiDef("leads.purchased", "Buy leads · last 20 purchased", "IndiaMART · Seller", "http", "GET",
           LEADS_BASE + "/purchasedleads/",
           params={"AK": "{secret}", "token": "{secret:LEADS_TOKEN}", "glusrid": "{glid}", "modid": "MY", "type": "B",
                   "start": "1", "end": "20", "complaint": "0", "live_leads": "1"},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=6, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}", notes="Only offer ids; each is expanded by Buy-lead details."),
    ApiDef("leads.detail", "Buy-lead details ×N", "IndiaMART · Seller", "http", "POST",
           LEADS_BASE + "/detail/",
           body={"token": "{secret:COMPANY_TOKEN}", "AK": "{secret}", "modid": "MY", "offerid": "{offerid}", "glusrid": "{glid}"},
           body_format="form", secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=168, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="Product, quantity, order value, buyer city and requirement history per purchased lead. The doc's token "
                 "(LEADS_TOKEN) answers 'Authentication failed' here; the company token (COMPANY_TOKEN) works."),
    ApiDef("enquiry.list", "Enquiries · buyers who contacted the seller", "IndiaMART · Seller", "http", "POST",
           "http://enq2.intermesh.net/enquiry/getEnquiryList/",
           body={"supplier_glid": "{glid}", "modid": "MERP", "enquiry_id": ""}, mode="live", ttl_hours=6, contract="sample",
           via_gateway=True, reachable=f"200 · {TESTED}",
           notes="Latest 10 direct enquiries: product, specs asked, date, buyer city/state and verification flags. No buyer GLID."),
    ApiDef("pns.insights", "PNS call insights", "IndiaMART · Seller", "http", "POST",
           "http://audio.imutils.com/pns-call-insights",
           body={"USR_ID": "{glid|int}", "AK": "{secret}", "MODID": "PNS", "EMP_ID": "{empid|int}", "PAGE_INDEX": 1},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=24, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="Calls on the seller's masked PNS number with extracted intent, products, outcome and next steps. Mixed-party: rows where "
                 "this GLID was the BUYER are kept apart. The doc's PNS key expired on 2026-10-08; the employee key works."),
    ApiDef("lms.messages", "LMS · messages with a buyer ×N", "IndiaMART · Seller", "http", "POST",
           "http://lms.imutils.com/addressbook/conversationList/",
           body={"user_glid": "{glid|int}", "contact_glid": "{contact_glid|int}", "modid": "SELLERMY", "from": 1, "to": 25,
                 "app_version_no": "1.23.5", "AK": "{secret}"},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=2, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="25 messages per page, newest first. Live only when BOTH GLIDs are approved test GLIDs (contacts come from PNS and buy leads)."),
    ApiDef("sts.history", "STS · user history", "IndiaMART · Seller", "http", "GET",
           MERP_BASE + "/common/v1/newhistory",
           params={"AK": "{secret}", "api": "1", "empid": "{empid}", "glid": "{glid}", "fns_mod_id": "914", "platform": "NSD",
                   "subduration": "1", "tab": "history", "modid": "weberp", "duration": "1", "native": "yes"},
           secret_name="IM_EMPLOYEE_AK", mode="live", ttl_hours=24, contract="sample", via_gateway=True,
           reachable=f"200 · {TESTED}",
           notes="Every touchpoint newest first (payments, tickets, complaints, calls, meetings) as one HTML blob — parsed into dated entries; "
                 "emails and phone numbers are removed before anything reaches the persona."),
    ApiDef("packages.catalog", "Package catalogue", "IndiaMART · Seller", "http", "GET", "",
           ttl_hours=24, contract="guess", reachable="endpoint not supplied",
           notes="No verified prices were supplied, so none are ever spoken."),
    # ---- Redash (fixed, read-only, approved GLIDs only; through the gateway since 5.10.1 — routes redash-query-results, redash-jobs) ----
    ApiDef("redash.hotlead", "Redash · bot-call dispositions", "Redash", "redash", "POST",
           "https://redash.intermesh.net/api/query_results", body={"data_source_id": 13, "query": "fixed SQL: redash.data_hotlead_disposition_dtl"},
           secret_name="REDASH_API_KEY", mode="live", ttl_hours=24, contract="fixed", reachable="reachable", via_gateway=True,
           notes="Live only for the 77 approved test GLIDs. Synthetic sellers use a mock of the same rows.", editable=False),
    ApiDef("redash.pns", "Redash · PNS calls", "Redash", "redash", "POST",
           "https://redash.intermesh.net/api/query_results", body={"data_source_id": 8, "query": "fixed SQL: im_dwh_rpt.pns_call_all_records"},
           secret_name="REDASH_API_KEY", mode="live", ttl_hours=24, contract="fixed", reachable="reachable", via_gateway=True, editable=False),
    # ---- Supplied dataset (real, private) ----
    ApiDef("dataset.seller", "Supplied dataset · seller file row", "Supplied dataset", "dataset", "POST",
           "elasticsearch://sellergenome-raw-family-seller-files/_search", mode="live", ttl_hours=0, cache_read=False, persist=False,
           contract="fixed", reachable="reachable", notes="58 original columns for real sellers. Not used for synthetic sellers.", editable=False),
    ApiDef("dataset.calls", "Supplied dataset · past bot calls", "Supplied dataset", "dataset", "POST",
           "elasticsearch://sellergenome-raw-family-bot-calls/_search", mode="live", ttl_hours=0, cache_read=False, persist=False,
           contract="fixed", reachable="reachable", notes="Latest answered bot calls with summaries and outcomes.", editable=False),
    # ---- Buyer side (VANI tools) ----
    ApiDef("buyer.caller", "Buyer details · caller", "IndiaMART · Buyer", "http", "GET", "", params={"buyer_glid": "{buyer_glid}"},
           ttl_hours=720, contract="guess", reachable="endpoint not supplied"),
    ApiDef("buyer.product_context", "Tool · requested_another_product", "IndiaMART · Buyer", "http", "POST", "",
           body={"product_name": "{product}"}, ttl_hours=168, contract="guess", reachable="endpoint not supplied",
           notes="Returns quantity units and specification options for a confirmed product."),
    ApiDef("buyer.seller_by_city", "Tool · update_ast_buy_variables_by_city", "IndiaMART · Buyer", "http", "POST", "",
           body={"city": "{city}", "state": "{state}", "product": "{product}"}, ttl_hours=0, cache_read=False,
           contract="guess", reachable="endpoint not supplied", notes="Live alternate seller availability by buyer location."),
    ApiDef("buyer.assisted_buy", "Tool · ast_buy_confirmed", "IndiaMART · Buyer", "http", "POST", "",
           body={"call_id": "{call_id}"}, ttl_hours=0, cache_read=False, persist=True, contract="guess",
           reachable="endpoint not supplied", notes="Test transfer only — no real call is connected."),
    ApiDef("buyer.lead_post", "Buy lead · post (test)", "IndiaMART · Buyer", "http", "POST", "",
           body={"lead": "{lead}"}, ttl_hours=0, cache_read=False, contract="guess", reachable="endpoint not supplied",
           notes="Test submission only — production leads are never written."),
]

# Mocks a real seller may still use: not about the seller (IndiaMART's own package list, buyer-desk tools).
NOT_SELLER_SPECIFIC = {"packages.catalog", "buyer.caller", "buyer.product_context", "buyer.seller_by_city", "buyer.assisted_buy",
                       "buyer.lead_post"}

ALLOWED_KEYS = {"label", "method", "url", "params", "body", "headers", "secret_name", "mode", "timeout_seconds",
                "ttl_hours", "cache_read", "persist", "notes", "body_format", "via_gateway"}


def allowed_hosts() -> list[str]:
    data = yaml.safe_load((ROOT / "config" / "genome" / "allowed_hosts.yaml").read_text())
    extra = [h.strip() for h in os.getenv("GENOME_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return list(data.get("hosts", [])) + extra


def host_allowed(url: str) -> bool:
    if not url:
        return True
    parts = urlsplit(url.replace("{", "").replace("}", ""))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return any(fnmatch.fnmatch(parts.hostname, pattern) for pattern in allowed_hosts())


PLACEHOLDER = re.compile(r"\{([a-z_]+(?::[A-Za-z0-9_]+)?)(\|int)?\}")


def referenced_secrets(api: ApiDef) -> list[str]:
    """Names used as ``{secret:NAME}`` anywhere in the API's URL, params, body or headers."""
    text = json.dumps([api.url, api.params, api.body, api.headers])
    return sorted({m.group(1).split(":", 1)[1] for m in PLACEHOLDER.finditer(text) if m.group(1).startswith("secret:")})


def gateway_base(settings) -> str:
    """The configured pass-through gateway base URL, or '' when the gateway is switched off."""
    if not settings.get("upstream.gateway_enabled", False):
        return ""
    return str(settings.get("upstream.gateway_base_url", "") or "").rstrip("/")


def via_gateway(settings, url: str) -> tuple[str, str | None]:
    """Swap scheme+host for the gateway; the gateway forwards the original path to the original host."""
    base = gateway_base(settings)
    if not base or not url:
        return url, None
    parts = urlsplit(url)
    path = parts.path or "/"
    return base + path + (f"?{parts.query}" if parts.query else ""), base


def upstream_error(payload: Any) -> str | None:
    """IndiaMART APIs often answer HTTP 200 with an error inside the body — surface those as failures.

    Seen on 2026-10-09: ``RESPONSE.CODE "401" Authentication failed`` (buy leads), ``Code 401 expired token``
    (PNS), ``{"status": 401, "message": "Authentication failure"}`` (STS), ``code 400 Provide either …``
    (getContext). An empty-but-valid answer ("Mapping Not Found", zero rows) is NOT an error.
    """
    node = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else payload
    if not isinstance(node, dict):
        return None
    inner = node.get("RESPONSE") if isinstance(node.get("RESPONSE"), dict) else node
    for key in ("CODE", "Code", "code", "status", "URL_STATUS"):  # URL_STATUS: the company API's {"Status": "Forbidden", "URL_STATUS": "403"}
        raw = inner.get(key)
        try:
            code = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if code >= 400:
            reason = inner.get("MESSAGE") or inner.get("Reason") or inner.get("message") or inner.get("error") or inner.get("Status") or ""
            return f"upstream {code}: {str(reason)[:160]}".strip()
    if isinstance(inner.get("error"), str) and inner.get("error") and str(inner.get("status", "")).lower() not in ("success", "200"):
        return f"upstream error: {inner['error'][:160]}"
    return None


SENSITIVE_URL = re.compile(r"(https?://[^\s\"']*?)([?&](?:token|AK|key|signature)=)[^&\s\"']+", re.IGNORECASE)


def sanitize(payload: Any) -> Any:
    """Remove credentials an upstream echoes back (e.g. PNS recording URLs carry a download token)."""
    if isinstance(payload, dict):
        return {k: sanitize(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [sanitize(v) for v in payload]
    if isinstance(payload, str) and ("token=" in payload or "AK=" in payload or "key=" in payload or "signature=" in payload):
        return SENSITIVE_URL.sub(lambda m: m.group(1) + m.group(2) + "[removed]", payload)
    return payload


class ApiRegistry:
    def __init__(self, settings, directory: Path | None = None) -> None:
        self.settings = settings
        self.path = (directory or private_dir()) / "genome-apis.json"
        self._lock = threading.Lock()
        try:
            self._state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._state = {"revision": 0, "overrides": {}, "history": []}
        self._defaults = {d.id: d for d in DEFAULTS}

    @property
    def revision(self) -> int:
        return int(self._state["revision"])

    def get(self, api_id: str) -> ApiDef:
        base = self._defaults[api_id]
        merged = {**asdict(base), **self._state["overrides"].get(api_id, {})}
        return ApiDef(**merged)

    def all(self) -> list[ApiDef]:
        return [self.get(i) for i in self._defaults]

    def describe(self, api_id: str) -> dict:
        d = asdict(self.get(api_id))
        d["overridden"] = sorted(self._state["overrides"].get(api_id, {}))
        d["secret"] = self.settings.secret_status(d["secret_name"]) if d["secret_name"] else None
        # Extra keys referenced as {secret:NAME} (e.g. the leads token) — status only, never values.
        d["secrets"] = [self.settings.secret_status(n) for n in referenced_secrets(self.get(api_id))]
        d["endpoint_supplied"] = bool(d["url"])
        d["sent_via"] = gateway_base(self.settings) if d["via_gateway"] and gateway_base(self.settings) else None
        return d

    def update(self, api_id: str, changes: dict, expected_revision: int, author: str = "admin") -> dict:
        if api_id not in self._defaults:
            raise KeyError(api_id)
        if not self._defaults[api_id].editable and set(changes) - {"mode", "timeout_seconds", "ttl_hours", "cache_read", "persist"}:
            raise ValueError("This API is fixed by policy; only mode, timeout, TTL and cache switches can change.")
        unknown = set(changes) - ALLOWED_KEYS
        if unknown:
            raise ValueError(f"Unknown fields: {sorted(unknown)}")
        if "mode" in changes and changes["mode"] not in ("live", "mock", "off"):
            raise ValueError("mode must be live, mock or off.")
        if "method" in changes and changes["method"] not in ("GET", "POST"):
            raise ValueError("method must be GET or POST.")
        if "body_format" in changes and changes["body_format"] not in ("json", "form"):
            raise ValueError("body_format must be json or form.")
        if "via_gateway" in changes and not isinstance(changes["via_gateway"], bool):
            raise ValueError("via_gateway must be true or false.")
        if "url" in changes and not host_allowed(changes["url"]):
            raise ValueError("That host is not on the allow-list (config/genome/allowed_hosts.yaml).")
        for key in ("params", "body", "headers"):
            if key in changes and not isinstance(changes[key], dict):
                raise ValueError(f"{key} must be a JSON object.")
        if "timeout_seconds" in changes and not 0.5 <= float(changes["timeout_seconds"]) <= 30:
            raise ValueError("timeout_seconds must be between 0.5 and 30.")
        if "ttl_hours" in changes and not 0 <= float(changes["ttl_hours"]) <= 2160:
            raise ValueError("ttl_hours must be between 0 and 2160.")
        with self._lock:
            if expected_revision != self.revision:
                raise ValueError("APIs changed since you loaded them; reload and retry.")
            previous = copy.deepcopy(self._state["overrides"])
            overrides = copy.deepcopy(previous)
            overrides[api_id] = {**overrides.get(api_id, {}), **changes}
            self._state = {
                "revision": self.revision + 1, "overrides": overrides,
                "history": (self._state["history"] + [{"revision": self.revision + 1, "at": utc_now(), "author": author[:60],
                                                        "api": api_id, "changed": sorted(changes), "previous": previous}])[-100:],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1))
            os.replace(tmp, self.path)
        return self.describe(api_id)

    def reset(self, api_id: str, expected_revision: int, author: str = "admin") -> dict:
        with self._lock:
            if expected_revision != self.revision:
                raise ValueError("APIs changed since you loaded them; reload and retry.")
            previous = copy.deepcopy(self._state["overrides"])
            overrides = {k: v for k, v in previous.items() if k != api_id}
            self._state = {"revision": self.revision + 1, "overrides": overrides,
                           "history": (self._state["history"] + [{"revision": self.revision + 1, "at": utc_now(), "author": author[:60],
                                                                   "api": api_id, "changed": ["reset"], "previous": previous}])[-100:]}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=1))
            os.replace(tmp, self.path)
        return self.describe(api_id)

    def history(self) -> list[dict]:
        return [{k: h[k] for k in ("revision", "at", "author", "api", "changed")} for h in self._state["history"][-30:]]


# ------------------------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------------------------

def _fill(template: Any, values: dict, secret: str, reveal: bool, lookup: Callable[[str], str] | None = None) -> Any:
    """Fill ``{glid}``, ``{alias}``, ``{secret}``, ``{secret:NAME}``, ``{env:NAME}`` and ``{name|int}``.

    With ``reveal=False`` secrets and env values print as ``[server-held]`` — that copy is what
    the browser sees; the revealed copy only ever leaves the server as the real request.
    A string that is exactly one ``{name|int}`` placeholder becomes a number (PNS and LMS want ints).
    """
    if isinstance(template, dict):
        return {k: _fill(v, values, secret, reveal, lookup) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, values, secret, reveal, lookup) for v in template]
    if not isinstance(template, str):
        return template

    def value_of(name: str) -> str:
        if name == "secret":
            return secret if reveal else SECRET_MARK
        if name.startswith("secret:"):
            return (lookup(name[7:]) if lookup else "") if reveal else SECRET_MARK
        if name.startswith("env:"):
            return os.getenv(name[4:], "") if reveal else SECRET_MARK
        return str(values.get(name, ""))

    whole = PLACEHOLDER.fullmatch(template)
    if whole and whole.group(2):
        text = value_of(whole.group(1))
        return int(text) if text.isdigit() else text
    return PLACEHOLDER.sub(lambda m: value_of(m.group(1)), template)


def _fill_params(template: dict, values: dict, secret: str, reveal: bool, lookup=None) -> dict:
    """Like ``_fill`` but drops a parameter whose template is a lone placeholder that filled to ''.

    getContext takes ``mapping_id`` OR ``source_uid``; sending the other one empty is an error upstream.
    """
    out = {}
    for key, raw in (template or {}).items():
        value = _fill(raw, values, secret, reveal, lookup)
        if value == "" and isinstance(raw, str) and PLACEHOLDER.fullmatch(raw):
            continue
        out[key] = value
    return out


def cache_key(api_id: str, values: dict, revision: int, salt: str = "") -> str:
    material = json.dumps({"api": api_id, "values": {k: values[k] for k in sorted(values)}, "rev": revision, **({"salt": salt} if salt else {})},
                          sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


class ApiExecutor:
    """Runs one API hit with caching, policy checks, persistence and logging."""

    def __init__(self, settings, registry: ApiRegistry, elastic, store, approved: Callable[[str], bool],
                 redash: Callable[[str, str], Any] | None = None) -> None:
        self.settings, self.registry, self.elastic, self.store = settings, registry, elastic, store
        self.approved = approved
        self.redash = redash  # async (source, glid) -> list[dict] using the V4 read-only connector

    async def call(self, api_id: str, seller: dict, values: dict | None = None, *, run_id: str | None = None,
                   force: bool = False, operator: bool = False, override: dict | None = None) -> dict:
        """Execute one API for one seller. ``override`` = edited params for Edit & send (never persisted as a cache hit)."""
        api = self.registry.get(api_id)
        values = {"glid": seller["glid"], "empid": str(self.settings.get("upstream.employee_id", "")), **(values or {})}
        result: dict[str, Any] = {
            "api": api.id, "label": api.label, "group": api.group, "kind": api.kind, "contract": api.contract,
            "status": "ok", "origin": None, "mode": api.mode, "http_status": None, "ms": 0.0,
            "request": self._describe(api, values, override), "response": None, "cache": None,
            "retrieved_at": utc_now(), "note": "", "error": None, "edited": bool(override),
        }
        synthetic = bool(seller.get("synthetic"))
        # ---- policy: which mode actually runs -------------------------------------------------
        effective = api.mode
        if api.mode == "mock" and not synthetic and api.id not in NOT_SELLER_SPECIFIC:
            result.update(status="skipped", origin="off",
                          note="Mock data is never mixed into a real seller's persona — this API has no live endpoint yet.")
            self._log(api.id, seller, result, run_id)
            return result
        if api.mode == "off":
            result.update(status="skipped", origin="off", note="Turned off in Admin → APIs.")
            self._log(api.id, seller, result, run_id)
            return result
        if api.kind == "dataset":
            if synthetic:
                result.update(status="skipped", origin="off", note="Synthetic seller — the supplied dataset has no row for it.")
                self._log(api.id, seller, result, run_id)
                return result
        if api.kind == "redash" and api.mode == "live":
            if synthetic:
                effective = "mock"
                result["note"] = "Synthetic seller: Redash is never queried for it; served by a mock of the same rows."
            elif not self.approved(seller["glid"]):
                result.update(status="skipped", origin="off",
                              note="Live upstream calls are allowed only for the 77 approved test GLIDs; the supplied dataset covers this seller.")
                self._log(api.id, seller, result, run_id)
                return result
        if api.kind == "http" and api.mode == "live":
            if synthetic:
                effective = "mock"
                result["note"] = "Synthetic seller: never sent upstream; served by the production-shaped mock."
            elif not operator:
                result.update(status="skipped", origin="off", note="Live upstream calls need the PM session.")
                self._log(api.id, seller, result, run_id)
                return result
            elif not self.approved(seller["glid"]):
                result.update(status="skipped", origin="off", note="Not an approved test GLID — no live request sent.")
                self._log(api.id, seller, result, run_id)
                return result
            elif not (api.url or (override or {}).get("url")):
                result.update(status="error", origin="live_api", error="not_configured",
                              note="Endpoint was never supplied — add it in Admin → APIs. No request sent.")
                self._log(api.id, seller, result, run_id)
                return result
        result["mode"] = effective
        if effective != "live" or not operator:  # the gateway URL is an open proxy — signed-in users only
            req = result["request"]
            req["gateway"] = "configured" if req.get("gateway") else None
            req["via"] = None
            req["headers"] = {k: v for k, v in req["headers"].items() if k not in self._gateway_headers()}
        # ---- cache read -----------------------------------------------------------------------
        # A mock's shape changes with the release (5.5 rebuilt Global Context in the guide's shape), so a cached mock is
        # only reused by the same version; live responses keep their keys.
        key = cache_key(api.id, values, self.registry.revision, salt=f"mock {self.settings.get('app.version', '')}" if effective == "mock" else "")
        use_cache = (api.cache_read and self.settings.get("elastic.cache_reads", True) and not force and not override
                     and api.ttl_hours > 0 and self.elastic.enabled)
        if use_cache:
            hit, query, ms = await self.elastic.cache_get(key)
            result["cache"] = {"index": self.elastic.alias("api-responses"), "key": key, "query": query,
                               "hit": bool(hit), "ms": round(ms, 1)}
            if hit:
                result.update(origin="elastic_cache", response=hit.get("response"), ms=round(ms, 1),
                              http_status=hit.get("http_status"), note=f"Served from Elasticsearch; originally {hit.get('origin')} at {hit.get('at')}.",
                              cached_origin=hit.get("origin"), cached_at=hit.get("at"))
                self._log(api.id, seller, result, run_id)
                return result
        # ---- execute --------------------------------------------------------------------------
        started = time.monotonic()
        try:
            if effective == "mock":
                result["origin"] = "mock"
                result["response"] = self._mock(api.id, seller, values)
                low, high = self.settings.get("runs.mock_latency_ms", [60, 260])
                await asyncio.sleep(random.uniform(low, high) / 1000)
                result["http_status"] = 200
            elif api.kind == "dataset":
                result["origin"] = "local_db"
                result["response"], result["http_status"] = await self._dataset(api.id, seller["glid"]), 200
            elif api.kind == "redash":
                result["origin"] = "live_api"
                # The V4 connector polls Redash; bound it by this API's timeout so one slow query cannot stall the run.
                try:
                    rows = await asyncio.wait_for(self.redash("redash_hotlead" if api.id == "redash.hotlead" else "redash_pns", seller["glid"]),
                                                  timeout=api.timeout_seconds) if self.redash else []
                except asyncio.TimeoutError as error:
                    raise TimeoutError(f"Redash did not answer within {api.timeout_seconds:g} s (Admin → APIs → timeout).") from error
                result["response"], result["http_status"] = {"rows": rows, "row_count": len(rows)}, 200
            else:
                result["origin"] = "live_api"
                result["response"], result["http_status"], problem = await self._live(api, values, override)
                if problem:  # HTTP 200 with an error inside the body — a failed Live call stays failed
                    result.update(status="error", error="UpstreamError", note=problem)
        except Exception as error:  # every failure stays visible — never silently replaced by a mock
            result.update(status="error", error=type(error).__name__, note=self._scrub(str(error)[:300]) or type(error).__name__)
        result["ms"] = round((time.monotonic() - started) * 1000, 1)
        # ---- persist (async, via outbox) ------------------------------------------------------
        if (result["status"] == "ok" and api.persist and self.settings.get("elastic.persist_responses", True)
                and self.elastic.enabled and api.ttl_hours > 0 and not override):
            expires = datetime.now(timezone.utc) + timedelta(hours=api.ttl_hours)
            doc_id = hashlib.sha256(f"{key}:{result['retrieved_at']}".encode()).hexdigest()[:32]
            self.elastic.queue("api-responses", doc_id, {
                "id": doc_id, "api": api.id, "seller_id": seller["seller_id"], "glid": seller["glid"],
                "origin": result["origin"], "status": "ok", "cache_key": key, "run_id": run_id,
                "http_status": result["http_status"], "duration_ms": result["ms"], "at": result["retrieved_at"],
                "expires_at": expires.isoformat(timespec="milliseconds"), "request": result["request"], "response": result["response"],
            }, run_id=run_id, node=f"persist:{api.id}")
            result["persist"] = {"index": self.elastic.alias("api-responses"), "doc_id": doc_id, "status": "queued"}
        self._log(api.id, seller, result, run_id)
        return result

    # ---- helpers ---------------------------------------------------------------------------------

    def _describe(self, api: ApiDef, values: dict, override: dict | None) -> dict:
        o = override or {}
        url_template = o.get("url") or api.url
        params = _fill_params(o.get("params", api.params), values, "", reveal=False)
        body = _fill(o.get("body", api.body), values, "", reveal=False)
        url = _fill(url_template, values, "", reveal=False) if url_template else "(endpoint not supplied)"
        if params and api.method == "GET" and url_template:
            url = url + ("&" if "?" in url else "?") + urlencode(params, safe="[]{}-:,")
        sent, base = via_gateway(self.settings, url) if (api.via_gateway and url_template) else (url, None)
        return {"method": api.method, "url": url, "via": sent if base else None, "gateway": base,
                "params": params, "body": body if api.method == "POST" else None,
                "body_format": api.body_format if api.method == "POST" else None,
                "headers": {**(self._gateway_headers() if base else {}), **{k: SECRET_MARK for k in api.headers}, "Accept": "application/json"}}

    def _gateway_headers(self) -> dict:
        headers = self.settings.get("upstream.gateway_headers", {}) or {}
        return {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}

    def _scrub(self, text: str) -> str:
        """Never let a key appear in a note, even if an exception message echoes a URL."""
        for name in {*(a.secret_name for a in self.registry.all() if a.secret_name),
                     *(n for a in self.registry.all() for n in referenced_secrets(a))}:
            value = self.settings.secret(name)
            if value and len(value) >= 6:
                text = text.replace(value, SECRET_MARK)
        return text

    def _mock(self, api_id: str, seller: dict, values: dict) -> Any:
        simple = {
            "company.alias": mocks.company_alias, "company.detail": mocks.company_detail, "context.global": mocks.global_context,
            "context.getcontext": mocks.get_context, "context.insights": mocks.insights, "leads.purchased": mocks.leads_purchased,
            "enquiry.list": mocks.enquiry_list, "pns.insights": mocks.pns_insights, "sts.history": mocks.sts_history,
            "packages.catalog": mocks.package_catalog,
        }
        if api_id in simple:
            return simple[api_id](seller)
        if api_id == "leads.detail":
            return mocks.lead_detail(seller, values.get("offerid", ""))
        if api_id == "lms.messages":
            return mocks.lms_messages(seller, values.get("contact_glid", ""))
        if api_id == "redash.hotlead":
            rows = mocks.redash_hotlead(seller)
            return {"rows": rows, "row_count": len(rows)}
        if api_id == "redash.pns":
            rows = [{"fk_receiver_glid": int(seller["glid"]), "call_date": b["last_at"], "prime_mcat": b["product"]}
                    for b in seller.get("buyers", []) if b["channel"] == "pns"]
            return {"rows": rows, "row_count": len(rows)}
        raise ValueError(f"No mock for {api_id}")

    async def _dataset(self, api_id: str, glid: str) -> dict:
        if api_id == "dataset.seller":
            body = {"size": 1, "query": {"term": {"seller_id": glid}}}
            data, _ = await self.elastic.raw_search("sellergenome-raw-family-seller-files", body)
            hits = data["hits"]["hits"]
            return {"query": body, "found": bool(hits), "row": hits[0]["_source"].get("raw") if hits else None,
                    "source_file": hits[0]["_source"].get("source_file") if hits else None}
        body = {"size": 5, "query": {"term": {"seller_id": glid}}, "sort": [{"observed_at": {"order": "desc", "unmapped_type": "date"}}]}
        data, _ = await self.elastic.raw_search("sellergenome-raw-family-bot-calls", body)
        calls = []
        for h in data["hits"]["hits"]:
            raw = h["_source"].get("raw") or {}
            calls.append({k: raw.get(k) for k in ("attempt_id", "call_start_time", "disposition_label", "lead_call_duration",
                                                  "lead_call_summary", "lead_bot_version", "meeting_fixed")})
        return {"query": body, "calls": calls, "total": data["hits"]["total"]["value"] if isinstance(data["hits"]["total"], dict) else len(calls)}

    async def _live(self, api: ApiDef, values: dict, override: dict | None) -> tuple[Any, int, str | None]:
        o = override or {}
        url_template = o.get("url") or api.url
        if not host_allowed(url_template):
            raise ValueError("Endpoint host is not allow-listed.")
        secret = self.settings.secret(api.secret_name) if api.secret_name else ""
        if api.secret_name and not secret:
            raise ValueError(f"Key {api.secret_name} is not configured (Admin → APIs → Keys).")
        status = self.settings.secret_status(api.secret_name) if api.secret_name else {}
        if status.get("expired"):
            raise ValueError(f"Key {api.secret_name} expired at {status.get('expires_at')} — paste a fresh one in Admin → APIs → Keys. No request sent.")
        missing = [n for n in referenced_secrets(api) if not self.settings.secret(n)]
        if missing:
            raise ValueError(f"Key {', '.join(missing)} is not configured (Admin → APIs → Keys).")
        lookup = self.settings.secret
        url = _fill(url_template, values, secret, reveal=True, lookup=lookup)
        params = _fill_params(o.get("params", api.params), values, secret, reveal=True, lookup=lookup)
        body = _fill(o.get("body", api.body), values, secret, reveal=True, lookup=lookup)
        headers = _fill({**api.headers, **o.get("headers", {})}, values, secret, reveal=True, lookup=lookup)
        if api.via_gateway:
            url, base = via_gateway(self.settings, url)
            if base:
                if not host_allowed(base):
                    raise ValueError("Gateway host is not allow-listed (config/genome/allowed_hosts.yaml).")
                headers = {**self._gateway_headers(), **headers}
        headers.setdefault("Accept", "application/json")
        async with httpx.AsyncClient(timeout=api.timeout_seconds, follow_redirects=False) as client:
            for attempt in (1, 2):   # 5.12.1: one retry when the gateway drops the connection (live: "Server disconnected")
                try:
                    if api.method == "GET":
                        r = await client.get(url, params=params or None, headers=headers)
                    elif api.body_format == "form":
                        r = await client.post(url, data={k: str(v) for k, v in body.items()}, headers=headers)
                    else:
                        r = await client.post(url, json=body, headers=headers)
                    break
                except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError):
                    if attempt == 2:
                        raise
        if len(r.content) > 2 * 1024 * 1024:
            raise ValueError("Response larger than 2 MiB.")
        try:
            payload = r.json()
        except ValueError:
            payload = {"text": r.text[:4000]}
        payload = sanitize(payload)
        if r.status_code >= 400:
            detail = (payload.get("error") or payload.get("text") or "") if isinstance(payload, dict) else ""
            return payload, r.status_code, f"HTTP {r.status_code}{': ' + str(detail)[:160] if detail else ''}"
        return payload, r.status_code, upstream_error(payload)

    def _log(self, api_id: str, seller: dict, result: dict, run_id: str | None) -> None:
        self.store.add_hit(api_id, {
            "seller_id": seller["seller_id"], "glid": seller["glid"], "run_id": run_id, "status": result["status"],
            "origin": result["origin"], "mode": result["mode"], "ms": result["ms"], "http_status": result["http_status"],
            "note": result["note"][:200], "url": result["request"]["url"][:300], "edited": result.get("edited", False),
        })
