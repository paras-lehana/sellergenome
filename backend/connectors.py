"""Bounded source adapters with explicit modes and test-only network egress.

Sample/live responses share typed normalization. Every live request, including
Redash polling and retries, rechecks current mode and protected target identity.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import httpx

from backend.admin_config import secret_value
from backend.source_normalization import (
    dotted,
    identity_matches,
    normalize,
    timestamp as source_timestamp,
)
from backend.source_policy import (
    SourcePolicyError,
    approve_endpoint,
    require_approved,
    valid_glid,
    safe_target_list,
    seed_approved_targets,
)
from backend.source_samples import sample_records, SAMPLE_DATE

SOURCES = {
    "sts": {
        "name": "Sales tracking history",
        "prefix": "STS",
        "method": "GET",
        "path": "/go/api/common/v1/newhistory",
        "auth": "query",
    },
    "context": {
        "name": "Seller context",
        "prefix": "CONTEXT",
        "method": "GET",
        "path": "/go/api/globalcontext/v1/userCtx",
        "auth": "query",
        "base_url": "http://prod-global-context.imbi.prod",
    },
    "buyleads": {
        "name": "Recent buyleads",
        "prefix": "BUYLEADS",
        "method": "GET",
        "path": "",
        "auth": "header",
    },
    "enquiries": {
        "name": "Recent enquiries",
        "prefix": "ENQUIRIES",
        "method": "GET",
        "path": "",
        "auth": "header",
    },
    "chat": {
        "name": "Seller messages",
        "prefix": "LMS",
        "method": "POST",
        "path": "/addressbook/conversationList/",
        "auth": "body",
        "base_url": "http://lms.imutils.com",
    },
    "pns": {
        "name": "Buyer call insights",
        "prefix": "PNS",
        "method": "POST",
        "path": "/pns-call-insights",
        "auth": "body",
        "base_url": "http://audio.imutils.com",
    },
    "company": {
        "name": "Company profile",
        "prefix": "COMPANY",
        "method": "GET",
        "base_url": "http://company.imutils.com",
        "path": "/wservce/company/detail/glusrid/{seller_id}/alias//cat_link//token/{token}/req_server/home_server/modid/tdw/is_mob_device/",
        "auth": "path",
    },
    "redash_hotlead": {
        "name": "Call summaries and meeting outcomes",
        "prefix": "REDASH",
        "method": "POST",
        "path": "/api/query_results",
        "auth": "redash",
        "base_url": "https://redash.intermesh.net",
        "datasource_id": 13,
    },
    "redash_recordings": {
        "name": "Seller recording metadata",
        "prefix": "REDASH",
        "method": "POST",
        "path": "/api/query_results",
        "auth": "redash",
        "base_url": "https://redash.intermesh.net",
        "datasource_id": 13,
    },
    "redash_executive": {
        "name": "Executive call count",
        "prefix": "REDASH",
        "method": "POST",
        "path": "/api/query_results",
        "auth": "redash",
        "base_url": "https://redash.intermesh.net",
        "datasource_id": 13,
    },
    "redash_pns": {
        "name": "Warehouse buyer-call metadata",
        "prefix": "REDASH",
        "method": "POST",
        "path": "/api/query_results",
        "auth": "redash",
        "base_url": "https://redash.intermesh.net",
        "datasource_id": 8,
    },
}
SOURCE_ALIASES = {
    "enquiry": "enquiries",
    "lms": "chat",
    "redash": "redash_hotlead",
}
ENV_ALIASES = {"enquiries": "ENQUIRY", "chat": "CHAT"}

# Identifiers and projections are reviewed source code. The only substituted
# values are exact manifest members containing digits and a bounded integer LIMIT.
REDASH_SQL = {
    "redash_hotlead": "SELECT data_hotlead_disposition_dtlid, fk_click_to_call_id, fk_glusr_usr_id, call_start_time, last_updated_on, lead_call_duration, lead_call_summary, lead_shl_trigger FROM redash.data_hotlead_disposition_dtl WHERE fk_glusr_usr_id = {glid} ORDER BY last_updated_on DESC LIMIT {limit}",
    "redash_recordings": "SELECT r.call_recordings_detail_id, r.fk_click_to_call_id, c.fk_glusr_usr_id, c.call_start_time, r.insertion_date, r.updated_on, r.model, r.status FROM redash.call_recordings_details r JOIN redash.click_to_call c ON r.fk_click_to_call_id = c.click_to_call_id WHERE c.fk_glusr_usr_id = {glid} ORDER BY c.call_start_time DESC LIMIT {limit}",
    "redash_executive": "SELECT {glid} AS fk_glusr_usr_id, COUNT(*) AS call_count FROM redash.click_to_call WHERE fk_glusr_usr_id = {glid}",
    "redash_pns": "SELECT pns_call_record_id, pns_call_receiver_glusr_id, pns_call_record_date, pns_call_status, pns_call_patch_duration, pns_call_ring_duration, pns_call_ref_prime_mcat FROM im_dwh_rpt.pns_call_all_records WHERE pns_call_receiver_glusr_id = {glid} ORDER BY pns_call_record_date DESC LIMIT {limit}",
}


class ConnectorError(RuntimeError):
    """Safe errors never include credential-bearing URLs, SQL or response bodies."""

    def __init__(self, source: str, code: str, message: str):
        super().__init__(message)
        self.source, self.code = source, code


class ConnectorService:
    """Fetch known read-only operations with an explicit acquisition mode."""

    # Set by the Genome service (5.10.1): Redash then goes through the IndiaMART gateway; paths and policy stay fixed.
    gateway_base = ""
    gateway_headers: dict = {}

    def __init__(self, config: dict, transport=None, checkpoint=None, before_request=None,
                 allow_any_glid: bool = False):
        self.config = config.get("connectors", config)
        # V5 upstream.glid_access = prod (D51): any numeric GLID; V4 callers keep the approved manifest.
        self.allow_any_glid = allow_any_glid
        self.transport = transport
        self.checkpoint = checkpoint
        self.before_request = before_request
        self.resume = None
        self.timeout = min(float(self.config.get("timeout_seconds", 10)), 30)
        self.max_pages = max(1, min(int(self.config.get("max_pages", 2)), 5))
        self.max_records = max(
            1, min(int(self.config.get("max_records", 25)), 100)
        )
        self.last_checked = {}
        self.last_receipt = {}
        self.raw_records = []
        self.exchanges = []

    def specification(self, source: str) -> dict:
        """Merge fixed templates with validated, nonsecret per-source settings."""
        source = SOURCE_ALIASES.get(source, source)
        if source not in SOURCES:
            raise ConnectorError(
                source, "unknown_source", "Unknown connector."
            )
        spec = {
            **SOURCES[source],
            **self.config.get("sources", {}).get(source, {}),
        }
        # Missing configuration never means permission to contact a source.
        spec.setdefault("mode", "disabled")
        prefix = spec["prefix"]
        alias = ENV_ALIASES.get(source, prefix)
        spec["base_url"] = os.getenv(
            prefix + "_BASE_URL", spec.get("base_url", "")
        ).rstrip("/")
        spec["path"] = os.getenv(prefix + "_PATH", spec["path"])
        full_url = os.getenv(alias + "_URL", "") or os.getenv(
            prefix + "_URL", ""
        )
        if full_url:
            parsed = urlsplit(full_url)
            spec["base_url"] = parsed.scheme + "://" + parsed.netloc
            if not source.startswith("redash"):
                spec["path"] = parsed.path or spec["path"]
                spec["url_params"] = dict(parse_qsl(parsed.query))
        # Fixed Redash datasource/template cannot be changed through JSON/YAML.
        if source in REDASH_SQL:
            spec["datasource_id"] = SOURCES[source]["datasource_id"]
            spec["path"] = "/api/query_results"
            if self.gateway_base:
                spec["base_url"] = self.gateway_base.rstrip("/")
        key_name = (
            "COMPANY_TOKEN" if source == "company" else prefix + "_API_KEY"
        )
        spec["secret"] = secret_value(key_name) or secret_value(
            alias + "_API_KEY"
        )
        spec["config_revision"] = int(self.config.get("config_revision", 0))
        return spec

    def ensure_target(self, source: str, seller_id: str) -> tuple[str, str]:
        """Recheck current mode and immutable approved identity before any work."""
        source = SOURCE_ALIASES.get(source, source)
        spec = self.specification(source)
        mode = spec["mode"]
        if mode == "disabled":
            raise ConnectorError(
                source,
                "source_disabled",
                "This source is disabled in administration.",
            )
        if mode == "sample":
            if seller_id != "sample-" + source:
                raise ConnectorError(
                    source,
                    "sample_target_required",
                    "Select this source's synthetic sample target; sample data cannot overwrite a private seller.",
                )
            return source, seller_id
        if mode != "live":
            raise ConnectorError(
                source, "invalid_mode", "Select Disabled, Sample or Live."
            )
        check = valid_glid if self.allow_any_glid else require_approved
        try:
            glid = check(seller_id)
            if source == "chat":
                contact = os.getenv(
                    "LMS_CONTACT_GLID", spec.get("contact_glid", "")
                )
                check(contact)
        except SourcePolicyError as error:
            raise ConnectorError(source, error.code, str(error)) from error
        return source, "real-" + glid

    @staticmethod
    def approved_targets() -> dict:
        """Return protected UI choices without granting permission to arbitrary IDs."""
        live = safe_target_list()
        return {
            "live": live,
            "sample": [
                {
                    "seller_id": "sample-" + source,
                    "label": "Synthetic · " + spec["name"],
                    "source": source,
                }
                for source, spec in SOURCES.items()
            ],
            "approved_count": len(live),
            "policy": "approved_test_only",
        }

    @staticmethod
    def seed_targets(store) -> dict:
        """Seed private approved aliases and isolated public synthetic accounts."""
        created = seed_approved_targets(store)
        samples = 0
        for source, spec in SOURCES.items():
            identifier = "sample-" + source
            if store.get("sellers", identifier):
                continue
            store.put(
                "sellers",
                identifier,
                {
                    "id": identifier,
                    "name": "Synthetic · " + spec["name"],
                    "company": "Illustrative test business",
                    "city": "Synthetic city",
                    "category": "Industrial packaging",
                    "language": "hi-IN",
                    "language_status": "unverified_default",
                    "segment": "Synthetic source sample",
                    "persona_tag": "balanced",
                    "source_mode": "sample",
                    "origin_mode": "sample",
                    "updated_at": source_timestamp(SAMPLE_DATE),
                    "metrics": {},
                    "evidence": [],
                },
            )
            samples += 1
        return {"approved_created": created, "samples_created": samples}

    def statuses(self) -> list[dict]:
        """Distinguish sample readiness, configuration, reachability and useful data."""
        result = []
        for source, base in SOURCES.items():
            spec = self.specification(source)
            configured = bool(
                spec["base_url"] and spec["secret"] and spec["path"]
            )
            if source == "chat":
                configured = configured and bool(
                    os.getenv("LMS_CONTACT_GLID", spec.get("contact_glid", ""))
                )
            mode = spec["mode"]
            status = (
                "disabled"
                if mode == "disabled"
                else "sample_ready"
                if mode == "sample"
                else "configured_unverified"
                if configured
                else "not_configured"
            )
            result.append(
                {
                    "id": source,
                    "name": base["name"],
                    "mode": mode,
                    "configured": configured,
                    "status": self.last_checked.get(source, {}).get(
                        "status", status
                    ),
                    "description": "Explicit synthetic replay or bounded test-only live source; no automatic fallback.",
                    "capabilities": [
                        "read_only",
                        "structured_observations",
                        "sample",
                        "approved_test_only",
                    ],
                    "last_checked": self.last_checked.get(source, {}).get(
                        "at"
                    ),
                    "config_revision": spec["config_revision"],
                    "sample_target": "sample-" + source,
                }
            )
        return result

    async def _request(
        self, client, source, method, url, *, seller_id, **kwargs
    ):
        """Policy is checked for every page/poll, not merely when work is queued."""
        self.ensure_target(source, seller_id)
        if self.before_request:
            self.before_request()
        try:
            if source == "chat":
                # Validate the outgoing snapshot too: admin/manifest changes
                # between pages must not authorize an old related contact.
                payload = kwargs.get("json", {})
                require_approved(payload.get("contact_glid", ""))
                if require_approved(
                    payload.get("user_glid", "")
                ) != require_approved(seller_id):
                    raise SourcePolicyError(
                        "seller_mismatch",
                        "Source request identity does not match its approved target.",
                    )
            approve_endpoint(
                source, url, isinstance(self.transport, httpx.MockTransport),
                gateway=self.gateway_base if source in REDASH_SQL else "",
            )
        except (SourcePolicyError, ValueError) as error:
            raise ConnectorError(
                source,
                getattr(error, "code", "invalid_endpoint"),
                "Source endpoint is outside the approved endpoint registry.",
            ) from error
        try:
            async with client.stream(method, url, **kwargs) as response:
                if response.status_code >= 300:
                    raise ConnectorError(
                        source,
                        f"upstream_{response.status_code}",
                        f"Source returned HTTP {response.status_code}.",
                    )
                chunks, length = [], 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > 2 * 1024 * 1024:
                        raise ConnectorError(
                            source,
                            "response_too_large",
                            "Source response exceeded 2 MiB.",
                        )
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks))
                if not isinstance(value, (list, dict)):
                    raise ValueError
                # Credentials are never copied; inspector uses registered templates.
                self.exchanges.append({"method": method, "path": re.sub(r"/token/[^/]+/", "/token/[REDACTED]/", urlsplit(url).path),
                                       "status": response.status_code, "response": value})
                return value
        except ConnectorError:
            raise
        except httpx.TimeoutException as error:
            raise ConnectorError(
                source, "timeout", "Source request timed out."
            ) from error
        except httpx.ConnectError as error:
            raise ConnectorError(
                source,
                "network_unreachable",
                "Source connection could not be established; verify its network or VPN route.",
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ConnectorError(
                source,
                "unavailable",
                "Source unavailable or returned invalid JSON.",
            ) from error

    async def fetch(self, source: str, seller_id: str) -> list[dict]:
        """Execute exactly the configured mode; failed live requests never replay samples."""
        source, target = self.ensure_target(source, seller_id)
        spec = self.specification(source)
        fetched = datetime.now(timezone.utc).isoformat()
        try:
            if spec["mode"] == "sample":
                records = sample_records(source)
            else:
                if (
                    not spec["base_url"]
                    or not spec["secret"]
                    or not spec["path"]
                ):
                    raise ConnectorError(
                        source,
                        "not_configured",
                        "Source endpoint or credentials are not configured.",
                    )
                if spec["method"] not in {"GET", "POST"}:
                    raise ConnectorError(
                        source,
                        "invalid_method",
                        "Only documented read-only GET/POST adapters are supported.",
                    )
                if not spec["path"].startswith("/") or spec["path"].startswith(
                    "//"
                ):
                    raise ConnectorError(
                        source,
                        "invalid_path",
                        "Source path must be relative to its configured host.",
                    )
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=False,
                    transport=self.transport,
                ) as client:
                    records = await self._fetch_records(
                        client, source, spec, target.removeprefix("real-")
                    )
                if any(
                    not isinstance(row, dict)
                    or not identity_matches(
                        source, row, target.removeprefix("real-")
                    )
                    for row in records
                ):
                    raise ConnectorError(
                        source,
                        "seller_mismatch",
                        "Source returned rows outside the requested test seller scope.",
                    )
            # Configuration changes during network work fence ingestion too.
            self.ensure_target(source, target)
            if (
                self.specification(source)["config_revision"]
                != spec["config_revision"]
            ):
                raise ConnectorError(
                    source,
                    "configuration_changed",
                    "Configuration changed during source fetch; retry with the current revision.",
                )
            self.raw_records = records
            events = [
                normalize(source, row, target, spec["mode"], spec, fetched)
                for row in records
                if isinstance(row, dict)
            ]
            status = (
                "sample_replayed"
                if spec["mode"] == "sample"
                else "connected_empty"
                if not events
                else "connected"
            )
            self.last_checked[source] = {"status": status, "at": fetched}
            self.last_receipt = {
                "source": source,
                "mode": spec["mode"],
                "config_revision": spec["config_revision"],
                "fetched_records": len(records),
                "useful_summaries": sum(
                    event["attributes"].get("summary_status") == "usable"
                    for event in events
                ),
                "source_status": status,
            }
            return events
        except ConnectorError as error:
            self.last_checked[source] = {"status": error.code, "at": fetched}
            raise

    async def _redash(self, client, source, spec, glid) -> list[dict]:
        """Run one fixed read-only query; persisted job IDs resume after restart."""
        headers = {**(self.gateway_headers if self.gateway_base else {}), "Authorization": "Key " + spec["secret"]}
        target = "real-" + glid
        reference = self.resume
        if (
            reference
            and reference.get("source") == source
            and reference.get("seller_id") == target
            and reference.get("config_revision") == spec["config_revision"]
        ):
            result = {"job": {"id": reference["job_id"], "status": 1}}
        else:
            result = await self._request(
                client,
                source,
                "POST",
                spec["base_url"] + "/api/query_results",
                seller_id=target,
                headers=headers,
                json={
                    "data_source_id": spec["datasource_id"],
                    "query": REDASH_SQL[source].format(
                        glid=glid, limit=self.max_records
                    ),
                    "max_age": max(
                        0,
                        min(int(self.config.get("redash_max_age", 300)), 3600),
                    ),
                },
            )
        maximum = max(1, min(int(self.config.get("redash_max_polls", 30)), 60))
        polls = 0
        result_fetched = False
        while True:
            if not isinstance(result, dict):
                raise ConnectorError(
                    source,
                    "schema_mismatch",
                    "Approved query returned an unexpected response shape.",
                )
            if "query_result" in result:
                rows = dotted(result, "query_result.data.rows")
                if not isinstance(rows, list):
                    raise ConnectorError(
                        source,
                        "schema_mismatch",
                        "Approved query returned an unexpected row shape.",
                    )
                return rows[: self.max_records]
            job = result.get("job", {})
            if not isinstance(job, dict) or result_fetched:
                raise ConnectorError(
                    source,
                    "schema_mismatch",
                    "Approved query returned an unexpected response shape.",
                )
            if job.get("status") in {4, 5}:
                raise ConnectorError(
                    source, "query_failed", "Approved query failed."
                )
            if job.get("status") == 3:
                result_id = str(job.get("query_result_id", ""))
                if not result_id.isdigit():
                    raise ConnectorError(
                        source,
                        "invalid_job",
                        "Source returned an invalid result reference.",
                    )
                result = await self._request(
                    client,
                    source,
                    "GET",
                    spec["base_url"]
                    + "/api/query_results/"
                    + result_id
                    + ".json",
                    seller_id=target,
                    headers=headers,
                )
                result_fetched = True
                continue
            job_id = str(job.get("id", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id):
                raise ConnectorError(
                    source,
                    "invalid_job",
                    "Source returned an invalid job reference.",
                )
            if self.checkpoint:
                self.checkpoint(
                    {
                        "source": source,
                        "seller_id": target,
                        "job_id": job_id,
                        "config_revision": spec["config_revision"],
                    }
                )
            if polls >= maximum:
                raise ConnectorError(
                    source,
                    "job_pending",
                    "Approved query is still running; its receipt can resume polling.",
                )
            await asyncio.sleep(
                max(
                    0.1,
                    min(float(self.config.get("redash_poll_seconds", 1)), 10),
                )
            )
            result = await self._request(
                client,
                source,
                "GET",
                spec["base_url"] + "/api/jobs/" + job_id,
                seller_id=target,
                headers=headers,
            )
            polls += 1

    async def _company(self, client, spec, glid):
        """Verified GLID-to-alias lookup; every hop rechecks approved identity."""
        from urllib.parse import quote
        suffix = "/cat_link//token/" + quote(spec["secret"], safe="") + "/req_server/home_server/modid/tdw/is_mob_device/"
        root = spec["base_url"] + "/wservce/company/detail/glusrid/"
        first = await self._request(client, "company", "GET", root + glid + "/alias/" + suffix, seller_id="real-" + glid)
        alias = dotted(first, "URL_DETAIL.FREESHOWROOM_ALIAS")
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", alias):
            raise ConnectorError("company", "invalid_alias", "Company lookup did not return a safe showroom alias.")
        # Reject a contradicting identity even when the first response has only alias metadata.
        first_id = dotted(first, "DATA.COMPANYDETAIL.GLUSRID") or dotted(first, "URL_DETAIL.GLUSRID")
        if first_id is not None and str(first_id) != glid:
            raise ConnectorError("company", "seller_mismatch", "Company alias identity differs from requested seller.")
        result = await self._request(client, "company", "GET", root + "/alias/" + alias + suffix, seller_id="real-" + glid)
        detail = dotted(result, "DATA.COMPANYDETAIL")
        if not isinstance(detail, dict) or str(detail.get("GLUSRID", "")) != glid:
            raise ConnectorError("company", "seller_mismatch", "Company detail identity differs from requested seller.")
        mapping = {"company_name": "DIR_SEARCH_COMPANY", "contact_name": "DIR_SEARCH_CONTACTPERSON",
                   "city": "DIR_SEARCH_CITY", "state": "DIR_SEARCH_STATE", "business_nature": "BIZ",
                   "phone_number": "MOBILE", "custtypeweight": "CUSTWT", "lead_locality_1": "GLUSR_USR_LOCALITY"}
        return [{"glid": glid, **{key: detail.get(value) for key, value in mapping.items()},
                 "showroom_alias": alias, "raw_response": result,
                 "source_paths": {key: "/raw_response/DATA/COMPANYDETAIL/" + value for key, value in mapping.items()}}]

    async def _fetch_records(self, client, source, spec, glid) -> list:
        """Build source-specific read requests from fixed templates and approved IDs."""
        if source == "company" and "/wservce/company/detail/" in spec["path"]:
            return await self._company(client, spec, glid)
        if source in REDASH_SQL:
            return await self._redash(client, source, spec, glid)
        params = {"glid": glid}
        params.update(
            {
                key: value.replace("{seller_id}", glid)
                for key, value in spec.get("url_params", {}).items()
                if key.lower()
                not in {"glid", "user_glid", "contact_glid", "usr_id", "ak"}
            }
        )
        headers, body = {}, {}
        if source == "sts":
            params.update(
                {
                    "api": 1,
                    "empid": os.getenv("STS_EMPLOYEE_ID", ""),
                    "fns_mod_id": 914,
                    "platform": "NSD",
                    "subduration": 1,
                    "tab": "history",
                    "modid": "weberp",
                    "duration": 1,
                    "native": "yes",
                }
            )
        if source == "context":
            params.update(
                {"source": "NSDHL", "keys": "kycdetails,activitydetails"}
            )
        if source == "chat":
            body = {
                "user_glid": glid,
                "contact_glid": os.getenv(
                    "LMS_CONTACT_GLID", spec.get("contact_glid", "")
                ),
                "modid": "SELLERMY",
                "from": 1,
                "to": 25,
                "app_version_no": "1.23.5",
            }
            params = {}
        if source == "pns":
            body = {
                "USR_ID": int(glid),
                "MODID": "PNS",
                "EMP_ID": os.getenv("PNS_EMPLOYEE_ID", ""),
                "PAGE_INDEX": 1,
            }
            params = {}
        if spec["auth"] == "query":
            params["AK"] = spec["secret"]
        elif spec["auth"] == "body":
            body["AK"] = spec["secret"]
        elif spec["auth"] == "header":
            headers["Authorization"] = "Bearer " + spec["secret"]
        from urllib.parse import quote

        path = (
            spec["path"]
            .replace("{seller_id}", glid)
            .replace("{token}", quote(spec["secret"], safe=""))
        )
        all_records = []
        for page in range(self.max_pages if source in {"chat", "pns"} else 1):
            if source == "chat":
                body.update({"from": page * 25 + 1, "to": (page + 1) * 25})
            if source == "pns":
                body["PAGE_INDEX"] = page + 1
            kwargs = {"headers": headers, "params": params}
            if spec["method"] == "POST":
                kwargs["json"] = body
            result = await self._request(
                client,
                source,
                spec["method"],
                spec["base_url"] + path,
                seller_id="real-" + glid,
                **kwargs,
            )
            configured = (
                dotted(result, spec["response_path"])
                if spec.get("response_path")
                else result
            )
            if isinstance(configured, list):
                records = configured
            elif isinstance(configured, dict):
                records = next(
                    (
                        configured[k]
                        for k in (
                            "data",
                            "results",
                            "messages",
                            "records",
                            "Data",
                            "Response",
                        )
                        if isinstance(configured.get(k), list)
                    ),
                    None,
                )
                if records is None:
                    nested = next(
                        (
                            configured[k]
                            for k in ("data", "result", "response")
                            if isinstance(configured.get(k), dict)
                        ),
                        configured,
                    )
                    records = [nested]
            else:
                raise ConnectorError(
                    source,
                    "schema_mismatch",
                    "Configure the documented source response path.",
                )
            all_records.extend(records)
            if len(records) < 25 or len(all_records) >= self.max_records:
                break
        return all_records[: self.max_records]

    def _normalize(self, source, spec, glid, records, now):
        """Compatibility entry point for adapter-focused fixtures and diagnostics."""
        source = SOURCE_ALIASES.get(source, source)
        return [
            normalize(source, row, "real-" + glid, "live", spec, now)
            for row in records
            if isinstance(row, dict)
        ]
