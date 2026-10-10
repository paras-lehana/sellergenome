"""V5.1: IndiaMART APIs through the configurable gateway — placeholders, gateway routing, upstream errors,
parsers for the real response shapes, live-over-dataset precedence, key expiry and what the public may see.

Every fixture here is synthetic (shaped like the real responses seen on 2026-10-09); no real seller data.
"""

import asyncio
import base64
import json
import time
from urllib.parse import parse_qs, quote

import httpx
import pytest

from backend.genome import contract, mock_upstream, parsers, registry, roster
from backend.genome.orchestrator import Orchestrator, mapping_id
from backend.genome.registry import ApiExecutor, ApiRegistry, _fill, _fill_params, sanitize, upstream_error, via_gateway
from backend.genome.settings import GenomeSettings, jwt_claims
from backend.genome.store import GenomeStore


class NoElastic:
    enabled = False

    def alias(self, kind):
        return f"sellergenome-v5-{kind}"

    async def cache_get(self, key):
        return None, {}, 0.0

    def queue(self, *a, **k):
        pass


def jwt(exp: float, sub: str = "10001") -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc({'sub': sub, 'iss': 'EMPLOYEE', 'exp': int(exp)})}.c2lnbmF0dXJl"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("IM_EMPLOYEE_AK", jwt(time.time() + 3600))
    monkeypatch.setenv("COMPANY_TOKEN", "company-token-test")
    monkeypatch.setenv("LEADS_TOKEN", "leads-token-test")
    settings = GenomeSettings()
    monkeypatch.setattr(settings, "values", {**settings.values, "runs": {**settings.values["runs"], "mock_latency_ms": [0, 1]},
                                             "upstream": {**settings.values["upstream"], "gateway_enabled": True,
                                                          "gateway_base_url": "https://test-gw.ngrok-free.app"}})
    store = GenomeStore(tmp_path / "v5.sqlite3")
    reg = ApiRegistry(settings, tmp_path / "config")
    executor = ApiExecutor(settings, reg, NoElastic(), store, approved=lambda g: g in {"12345", "67890"})
    return settings, store, reg, executor


def capture(monkeypatch, respond):
    """Route the executor's httpx client through a MockTransport and record requests."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)
    real = httpx.AsyncClient
    monkeypatch.setattr(registry.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


# --- placeholders and routing -------------------------------------------------------------------

def test_fill_secrets_named_secrets_and_int_coercion():
    lookup = {"LEADS_TOKEN": "tok"}.get
    tpl = {"AK": "{secret}", "token": "{secret:LEADS_TOKEN}", "USR_ID": "{glid|int}", "q": "x-{glid}"}
    assert _fill(tpl, {"glid": "12345"}, "ak", True, lookup) == {"AK": "ak", "token": "tok", "USR_ID": 12345, "q": "x-12345"}
    shown = _fill(tpl, {"glid": "12345"}, "ak", False, lookup)
    assert shown["AK"] == shown["token"] == "[server-held]" and shown["USR_ID"] == 12345
    # getContext takes mapping_id OR source_uid: the empty one is not sent.
    assert _fill_params({"mapping_id": "{mapping_id}", "source_uid": "{source_uid}", "x": "all"}, {"source_uid": "12345"}, "", True) == \
        {"source_uid": "12345", "x": "all"}


def test_gateway_swaps_host_and_keeps_path(env):
    settings = env[0]
    url, base = via_gateway(settings, "http://company.imutils.com/wservce/company/detail/glusrid/1/alias//cat_link//token/t/?a=1")
    assert base == "https://test-gw.ngrok-free.app"
    assert url == "https://test-gw.ngrok-free.app/wservce/company/detail/glusrid/1/alias//cat_link//token/t/?a=1"
    settings.values["upstream"]["gateway_enabled"] = False
    assert via_gateway(settings, "http://x.imutils.com/p") == ("http://x.imutils.com/p", None)


@pytest.mark.parametrize("payload,error", [
    ({"RESPONSE": {"CODE": "401", "DATA": "NULL", "MESSAGE": "Authentication failed", "STATUS": "0"}}, "upstream 401: Authentication failed"),
    ({"Code": 401, "Status": "Failure", "Reason": "expired token"}, "upstream 401: expired token"),
    ({"status": 401, "data": {}, "message": "Authentication failure"}, "upstream 401: Authentication failure"),
    ({"code": 400, "data": {}, "message": "Provide either params: MappingID or SourceUID"}, "upstream 400: Provide either params: MappingID or SourceUID"),
    ({"code": 200, "data": {}, "message": "Mapping Not Found"}, None),
    ({"code": 204, "status": "success", "response": "no data found"}, None),
    ([{"data": [], "enddate": "", "message": "New History", "status": "200"}], None),
    ({"status": "success", "code": 200, "error": "", "response": {"enquiries": []}}, None),
])
def test_upstream_errors_inside_http_200(payload, error):
    assert upstream_error(payload) == error


def test_sanitize_removes_recording_tokens():
    out = sanitize({"data": [{"call_recording_url": "https://openapi.example.in/recordingv2/download?token=SECRET123&x=1"}]})
    assert "SECRET123" not in json.dumps(out) and "token=[removed]" in out["data"][0]["call_recording_url"]


# --- parsers for the real shapes ----------------------------------------------------------------

def test_sts_blob_is_parsed_and_scrubbed():
    blob = ("08 Oct 2026 01:40:25 PM, Updated by Self Help&nbsp; through WA_9696<br/>&nbsp;&nbsp; Mail To: rm@example.com<br/>&nbsp;&nbsp; "
            "Comments: Self Help (1) Updated Customer Ticket(2) Mail To: rm@example.com Comments: Complaint. Seller wants to change their "
            "Relationship Manager Ravi Kumar Sharma. Call 9876543210.<br /><br/><br />"
            "05 OCT 2026 11:20:01 AM, Updated by WEBERP&nbsp; through Online Payment<br/>&nbsp;&nbsp; Comments: Payment of Rs 470 from APP<br /><br/><br />")
    entries = parsers.sts_entries([{"data": [quote(blob)], "status": "200"}])
    assert [e["kind"] for e in entries] == ["complaint", "payment"]
    assert entries[0]["at"].startswith("2026-10-08T13:40") and entries[1]["at"].startswith("2026-10-05T11:20")
    text = json.dumps(entries)
    assert "example.com" not in text and "9876543210" not in text and "Ravi" not in text
    assert entries[0]["text"].startswith("Complaint. Seller wants to change their Relationship Manager [name]")


def test_pns_rows_keep_buyer_side_calls_apart():
    payload = {"Code": 200, "data": [
        {"SELLER_GLID": 12345, "BUYER_GLID": 555, "call_timestamp": "2026-10-06 10:57:18", "mcat_name": "Cable",
         "extracted_data": {"metadata": {"buyer_intent": {"intent_level": "High", "narrative": "Hi Alok, needs 50 m"}}, "products": []}},
        {"SELLER_GLID": 999, "BUYER_GLID": 12345, "call_timestamp": "2026-09-01 10:00:00", "mcat_name": "Tea", "extracted_data": {}},
    ]}
    calls = parsers.pns_calls(payload, "12345")
    assert [(c["role"], c["counterpart_glid"]) for c in calls] == [("seller", "555"), ("buyer", "999")]
    assert "Alok" not in calls[0]["narrative"]


def test_lms_enquiry_and_lead_shapes_including_empty_answers():
    assert parsers.lms_messages({"code": 204, "status": "success", "response": "no data found"}, "1") == []
    msgs = parsers.lms_messages({"code": 200, "response": {"result": [
        {"msg_date": " 03-09-2026 17:52:21", "msg_sender_id": "1", "msg_text_json": json.dumps({"message_text": "Rate?"})},
        {"msg_date": " 03-09-2026 17:50:00", "msg_sender_id": "2", "msg_text_json": "not json"}]}}, "1", "2")
    assert [m["from"] for m in msgs] == ["seller", "contact"] and msgs[0]["at"].startswith("2026-09-03T17:52")
    enq = parsers.enquiries({"code": 200, "response": {"enquiries": [{"enquiryTitle": "Shoes", "postedDate": "2026-10-08T16:25:42.5",
                                                                   "buyerCity": "Pune", "mobileVerified": 1, "enquiryISQ": []}]}})
    assert enq[0]["product"] == "Shoes" and enq[0]["verified"]["mobile"]
    lead = parsers.lead({"RESPONSE": {"CODE": "200", "DATA": {"ETO_OFR_TITLE": "50 kg Sugar", "ETO_OFR_DATE": "09-OCT-26", "FK_GLUSR_USR_ID": "7"}}})
    assert lead["title"] == "50 kg Sugar" and lead["at"] == "2026-10-09" and lead["buyer_glid"] == "7"
    assert parsers.lead_ids({"RESPONSE": {"DATA": {"offerids": [None]}}}) == []


# --- contract precedence and mocks --------------------------------------------------------------

def _company(origin, city):
    p = roster.profile("syn-91000101")
    detail = mock_upstream.company_detail(p)
    detail["DATA"]["COMPANYDETAIL"]["DIR_SEARCH_CITY"] = city
    return {"status": "ok", "origin": origin, "response": detail, "retrieved_at": "t"}


def test_live_company_outranks_dataset_but_mock_never_does():
    p = roster.profile("syn-91000101")
    dataset = {"status": "ok", "origin": "local_db", "retrieved_at": "t", "response": {"row": {"seller_city": "Pali"}}}
    live = contract.assemble(p, {"company.detail": _company("live_api", "Noida"), "dataset.seller": dataset}, "r")
    assert live["fields"]["city"]["value"] == "Noida" and live["fields"]["city"]["origin"] == "live_api"
    mock = contract.assemble(p, {"company.detail": _company("mock", "Noida"), "dataset.seller": dataset}, "r")
    assert mock["fields"]["city"]["value"] == "Pali"
    cached = _company("elastic_cache", "Noida") | {"cached_origin": "live_api"}
    assert contract.assemble(p, {"company.detail": cached, "dataset.seller": dataset}, "r")["fields"]["city"]["value"] == "Noida"


def test_new_mocks_match_real_envelopes_and_feed_the_contract():
    p = roster.profile("syn-91000101")
    assert mock_upstream.pns_insights(p)["Code"] == 200 and isinstance(mock_upstream.pns_insights(p)["data"], list)
    assert mock_upstream.lms_messages(p)["response"]["result"]
    assert mock_upstream.enquiry_list(p)["response"]["enquiries"]
    offer = mock_upstream.leads_purchased(p)["RESPONSE"]["DATA"]["offerids"][0]
    assert mock_upstream.lead_detail(p, offer)["RESPONSE"]["DATA"]["ETO_OFR_TITLE"]
    assert parsers.sts_entries(mock_upstream.sts_history(p))
    results = {k: {"status": "ok", "origin": "mock", "retrieved_at": "t", "response": v} for k, v in {
        "enquiry.list": mock_upstream.enquiry_list(p), "pns.insights": mock_upstream.pns_insights(p),
        "sts.history": mock_upstream.sts_history(p), f"leads.detail:{offer}": mock_upstream.lead_detail(p, offer),
        "lms.messages:72000301": mock_upstream.lms_messages(p, "72000301")}.items()}
    out = contract.assemble(p, results, "r")
    assert {b["channel"] for b in out["buyers"]} == {"enquiry", "buylead", "pns"}
    assert out["messages"] and out["history"] and out["leads"] and out["enquiries"]
    assert all(not str(i["text"]).count("Rakesh") for i in contract.evidence_items(out))  # buyer names never reach the LLM


# --- executor policy ----------------------------------------------------------------------------

def test_real_seller_never_gets_seller_specific_mocks(env):
    _, _, _, executor = env
    real = roster.profile("real-12345")
    r = asyncio.run(executor.call("context.insights", real, operator=True))
    assert r["status"] == "skipped" and "never mixed" in r["note"]
    assert asyncio.run(executor.call("packages.catalog", real, operator=True))["origin"] == "mock"  # not about the seller


def test_live_call_goes_through_gateway_with_form_body_and_keys(env, monkeypatch):
    _, _, _, executor = env
    seen = capture(monkeypatch, lambda req: httpx.Response(200, json={"RESPONSE": {"CODE": "200", "DATA": {"ETO_OFR_TITLE": "x"}}}))
    real = roster.profile("real-12345")
    r = asyncio.run(executor.call("leads.detail", real, {"offerid": "1527"}, operator=True))
    assert r["status"] == "ok" and r["origin"] == "live_api"
    req = seen[0]
    assert str(req.url) == "https://test-gw.ngrok-free.app/wservce/buyleads/detail/"
    assert req.headers["ngrok-skip-browser-warning"] == "1" and req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    form = parse_qs(req.content.decode())
    assert form["token"] == ["company-token-test"] and form["offerid"] == ["1527"] and form["glusrid"] == ["12345"]
    assert r["request"]["body"]["AK"] == "[server-held]" and r["request"]["via"].startswith("https://test-gw")


def test_http_200_with_auth_error_stays_a_visible_failure(env, monkeypatch):
    _, _, _, executor = env
    capture(monkeypatch, lambda req: httpx.Response(200, json={"Code": 401, "Status": "Failure", "Reason": "expired token"}))
    r = asyncio.run(executor.call("pns.insights", roster.profile("real-12345"), operator=True))
    assert r["status"] == "error" and r["error"] == "UpstreamError" and "expired token" in r["note"]
    assert r["response"]["Reason"] == "expired token"  # the body is kept for the inspector, never replaced by a mock


def test_expired_key_sends_nothing(env, monkeypatch):
    _, _, _, executor = env
    monkeypatch.setenv("IM_EMPLOYEE_AK", jwt(time.time() - 60))
    seen = capture(monkeypatch, lambda req: httpx.Response(200, json={}))
    r = asyncio.run(executor.call("sts.history", roster.profile("real-12345"), operator=True))
    assert r["status"] == "error" and "expired" in r["note"] and not seen


def test_public_requests_never_show_the_gateway_url(env):
    _, _, _, executor = env
    r = asyncio.run(executor.call("company.alias", roster.profile("syn-91000101")))
    assert r["origin"] == "mock" and r["request"]["via"] is None and r["request"]["gateway"] == "configured"
    assert "ngrok" not in json.dumps(r)


def test_jwt_claims_expiry_without_the_value():
    claims = jwt_claims(jwt(time.time() + 7200, sub="84206"))
    assert claims["kind"] == "jwt" and claims["subject"] == "84206" and not claims["expired"] and 1.9 < claims["expires_in_hours"] <= 2.0
    assert jwt_claims("not-a-jwt") == {}


def test_gateway_url_must_be_allow_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path))
    s = GenomeSettings(tmp_path)
    with pytest.raises(ValueError):
        s.update({"upstream.gateway_base_url": "https://evil.example.com"}, 0)
    s.update({"upstream.gateway_base_url": "https://abcd-1-2-3-4.ngrok-free.app"}, 0)
    assert s.get("upstream.gateway_base_url").endswith("ngrok-free.app")


# --- orchestrator helpers -----------------------------------------------------------------------

def test_lms_contacts_need_both_glids_approved(env):
    settings, store, _, executor = env
    orch = Orchestrator.__new__(Orchestrator)
    orch.settings, orch.executor = settings, executor
    settings.values["upstream"]["lms_test_contacts"] = ["67890", "11111"]
    results = {"pns.insights": {"status": "ok", "response": {"data": [{"SELLER_GLID": 12345, "BUYER_GLID": 99999}]}}}
    contacts, _ = orch._lms_contacts(roster.profile("real-12345"), results)
    assert contacts == ["67890"]  # 99999 and 11111 are not approved test GLIDs
    assert mapping_id({"status": "ok", "response": {"data": {"mapping_id": "m-1"}}}) == "m-1"
    assert mapping_id({"status": "error", "response": None}) == ""


def test_private_identity_is_pending_until_confirmed():
    p = roster.profile("real-67890123")
    assert p["company_name"].startswith("Private seller") and p["contact_name"] == "" and p["trigger"] == {}
    assert roster.profile("real-67890123", {"city": "Unknown"})["city"] == ""
    assert roster.language_for_state("Uttar Pradesh") == "hi-IN" and roster.language_for_state("Karnataka") == "kn-IN"


# --- HTTP surface -------------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("COMPANY_TOKEN", "company-token-test")
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    with TestClient(main.app) as c:
        yield c, main


def test_public_architecture_and_inspector_hide_gateway_and_fingerprints(client, monkeypatch):
    c, main = client
    from backend.genome.sarvam import SarvamError

    async def fail(*a, **k):
        raise SarvamError("offline in tests")
    monkeypatch.setattr(main.app.state.genome.sarvam, "chat_json", fail)
    arch = c.get("/api/v5/architecture").json()
    assert arch["gateway"]["base"] in ("configured", None) and not arch["operator"]
    assert {"leads.purchased", "leads.detail", "enquiry.list", "context.getcontext"} <= {i["id"] for i in arch["items"]}
    assert c.get("/api/v5/admin/keys").status_code == 401 and c.post("/api/v5/admin/gateway/check").status_code == 401
    rid = c.post("/api/v5/runs", json={"seller_id": "syn-91000102"}).json()["run_id"]
    for _ in range(80):
        time.sleep(0.1)
        doc = c.get(f"/api/v5/runs/{rid}").json()
        if doc["status"] != "running":
            break
    assert doc["status"] == "completed"
    node = c.get(f"/api/v5/runs/{rid}/nodes/company.alias").json()
    text = json.dumps(node)
    assert "ngrok" not in text and "fingerprint" not in text
    assert any(k.startswith("leads.detail:") for k in doc["results"]) and doc["contract"]["buyers"]


def test_scrub_removes_bank_identifiers_but_keeps_business_text():
    out = parsers.scrub("Penny Drop Failed. Account Number : 989***998 IFSC Code : KKBK0001899 PAN ABCDE1234F, retry")
    assert "989" not in out and "KKBK0001899" not in out and "ABCDE1234F" not in out and out.startswith("Penny Drop Failed")


def test_gateway_url_comes_from_env_not_git_and_admin_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("UPSTREAM_GATEWAY_BASE_URL", "https://env-gw.ngrok-free.app")
    s = GenomeSettings(tmp_path)
    assert s.defaults["upstream"]["gateway_base_url"] == ""
    assert s.get("upstream.gateway_base_url") == "https://env-gw.ngrok-free.app"
    s.update({"upstream.gateway_base_url": "https://admin-gw.ngrok-free.app"}, 0)
    assert s.get("upstream.gateway_base_url") == "https://admin-gw.ngrok-free.app"


def test_redash_is_bounded_by_the_api_timeout(env):
    settings, store, reg, _ = env

    async def slow(source, glid):
        await asyncio.sleep(5)
        return []
    executor = ApiExecutor(settings, reg, NoElastic(), store, approved=lambda g: True, redash=slow)
    reg.update("redash.hotlead", {"timeout_seconds": 0.5}, reg.revision)
    started = time.monotonic()
    r = asyncio.run(executor.call("redash.hotlead", roster.profile("real-12345"), operator=True))
    assert r["status"] == "error" and "did not answer" in r["note"] and time.monotonic() - started < 2


def test_company_forbidden_inside_http_200_is_a_failure():
    assert upstream_error({"Status": "Forbidden", "URL_STATUS": "403", "alias": "", "glusrid": "1", "key": ""}) == "upstream 403: Forbidden"
