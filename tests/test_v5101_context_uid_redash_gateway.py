"""5.10.1: userCtx → generateContextUID, and Redash through the IndiaMART gateway.

Every fixture is synthetic; no network, no real seller data.
"""

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.connectors import ConnectorService
from backend.genome import contract, mock_upstream, persona_generator, registry, roster
from backend.genome.orchestrator import mapping_id
from backend.genome.registry import ApiExecutor, ApiRegistry
from backend.genome.service import Genome
from backend.genome.settings import GenomeSettings
from backend.genome.store import GenomeStore
from backend.source_policy import SourcePolicyError, approve_endpoint

GW = "https://test-gw.ngrok-free.app"


class NoElastic:
    enabled = False

    def alias(self, kind):
        return f"sellergenome-v5-{kind}"

    async def cache_get(self, key):
        return None, {}, 0.0

    def queue(self, *a, **k):
        pass


def jwt(exp: float) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'HS256'})}.{enc({'sub': '10001', 'iss': 'EMPLOYEE', 'exp': int(exp)})}.c2lnbmF0dXJl"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    ak = jwt(time.time() + 3600)
    monkeypatch.setenv("IM_EMPLOYEE_AK", ak)
    settings = GenomeSettings()
    monkeypatch.setattr(settings, "values", {**settings.values, "upstream": {**settings.values["upstream"], "gateway_enabled": True,
                                                                             "gateway_base_url": GW}})
    store = GenomeStore(tmp_path / "v5.sqlite3")
    reg = ApiRegistry(settings, tmp_path / "config")
    executor = ApiExecutor(settings, reg, NoElastic(), store, approved=lambda g: g == "12345")
    return settings, reg, executor, ak


def capture(monkeypatch, respond):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return respond(request)
    real = httpx.AsyncClient
    monkeypatch.setattr(registry.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


# --- generateContextUID -------------------------------------------------------------------------

def test_registry_rows_point_at_the_new_api_and_the_gateway(env):
    reg = env[1]
    ctx = reg.get("context.global")
    assert ctx.method == "POST" and ctx.url.endswith("/go/api/globalcontext/v1/generateContextUID") and ctx.via_gateway
    assert "userCtx" not in ctx.url and ctx.secret_name == "IM_EMPLOYEE_AK"
    assert reg.get("redash.hotlead").via_gateway and reg.get("redash.pns").via_gateway


def test_generate_context_uid_is_posted_through_the_gateway_with_the_owner_body(env, monkeypatch):
    _, _, executor, ak = env
    seen = capture(monkeypatch, lambda req: httpx.Response(200, json={"code": 200, "message": "ok", "data": {"context_uid": "cu-7f3a"}}))
    r = asyncio.run(executor.call("context.global", roster.profile("real-12345"), operator=True))
    assert r["status"] == "ok" and r["origin"] == "live_api"
    req = seen[0]
    assert req.method == "POST" and str(req.url) == GW + "/go/api/globalcontext/v1/generateContextUID"
    assert req.headers["ngrok-skip-browser-warning"] == "1"
    assert json.loads(req.content) == {"AK": ak, "empid": 84206, "mapping_type": 1, "mapping_value": "12345", "source_id": 1,
                                       "data_segments": "kycdetails"}
    assert r["request"]["body"]["AK"] == "[server-held]"  # what the browser sees
    assert mapping_id(r) == "cu-7f3a"


@pytest.mark.parametrize("response,expected", [
    ({"mapping_id": "m1"}, "m1"),
    ({"data": {"mapping_id": "m2"}}, "m2"),
    ({"code": 200, "data": {"contextUID": "m3"}}, "m3"),
    ({"code": 200, "data": {"context_uid": 77}}, "77"),
    ({"Data": {"uid": "m5"}}, "m5"),
    ({"code": 200, "data": {"generatedContextUid": "m6"}}, "m6"),
    ({"code": 200, "message": "Mapping Not Found", "data": {}}, ""),
    ({"code": 200, "data": {"mapping_id": ""}}, ""),
])
def test_mapping_id_finds_the_uid_under_any_likely_key(response, expected):
    assert mapping_id({"status": "ok", "response": response}) == expected


def test_mapping_id_ignores_failed_calls():
    assert mapping_id({"status": "error", "response": {"mapping_id": "m"}}) == ""
    assert mapping_id(None) == ""


# --- Global Context data: generateContextUID first, else getContext ---------------------------------

def _hero():
    glid = sorted(roster.heroes())[0]
    return roster.profile(f"syn-{glid}")


def _results(gc_response, getcontext_response):
    out = {"context.global": {"status": "ok", "origin": "live_api", "response": gc_response}}
    if getcontext_response is not None:
        out["context.getcontext"] = {"status": "ok", "origin": "live_api", "response": getcontext_response}
    return out


def test_fields_come_from_getcontext_when_generate_context_uid_holds_only_the_uid():
    seller = _hero()
    full = mock_upstream.global_context(seller)
    results = _results({"code": 200, "data": {"context_uid": "cu-1"}}, {"code": 200, "data": full["data"]})
    assert contract.global_context_source(results) == "context.getcontext"
    fields = contract.assemble(seller, results, "run-x")["fields"]
    assert fields["custtype"]["api"] == "context.getcontext" and fields["custtype"]["availability"] == "present"
    assert fields["pns_received_30d"]["api"] == "context.getcontext"


def test_generate_context_uid_wins_when_it_carries_the_segments():
    seller = _hero()
    full = mock_upstream.global_context(seller)
    results = _results(full, {"code": 200, "message": "Mapping Not Found", "data": {}})
    assert contract.global_context_source(results) == "context.global"
    assert contract.assemble(seller, results, "run-x")["fields"]["custtype"]["api"] == "context.global"


def test_persona_input_cites_the_api_that_held_the_context():
    seller = _hero()
    full = mock_upstream.global_context(seller)
    data, _ = persona_generator.build_input(seller, _results({"code": 200, "data": {"context_uid": "cu-1"}}, {"code": 200, "data": full["data"]}))
    assert data.get("global_context"), "the getContext segments reach the persona input"
    sources = {x.get("source") for key in ("records", "facts") for x in data.get(key) or [] if isinstance(x, dict)}
    assert "context.getcontext" in sources and "context.global" not in sources


# --- Redash through the gateway --------------------------------------------------------------------

@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    manifest = tmp_path / "approved.json"
    manifest.write_text(json.dumps({"version": 1, "glids": ["101"]}))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    monkeypatch.setenv("REDASH_API_KEY", "synthetic-fixture-key")
    for name in ("REDASH_BASE_URL", "REDASH_URL"):
        monkeypatch.delenv(name, raising=False)


def test_redash_query_goes_through_the_gateway_with_its_headers(isolated):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"query_result": {"data": {"rows": [{"fk_glusr_usr_id": 101, "lead_call_summary": "x"}]}}})
    service = ConnectorService({"sources": {"redash_hotlead": {"mode": "live"}}}, httpx.MockTransport(handler))
    service.gateway_base, service.gateway_headers = "https://gw-test.ngrok-free.app", {"ngrok-skip-browser-warning": "1"}
    rows = asyncio.run(service.fetch("redash_hotlead", "101"))
    assert rows and str(seen[0].url) == "https://gw-test.ngrok-free.app/api/query_results"
    assert seen[0].headers["authorization"] == "Key synthetic-fixture-key" and seen[0].headers["ngrok-skip-browser-warning"] == "1"


def test_redash_without_a_gateway_still_goes_direct(isolated):
    service = ConnectorService({"sources": {"redash_hotlead": {"mode": "live"}}})
    assert service.specification("redash_hotlead")["base_url"] == "https://redash.intermesh.net"


def test_gateway_origin_is_allowed_only_for_redash_over_https_on_the_fixed_paths():
    gw = "https://gw-test.ngrok-free.app"
    approve_endpoint("redash_hotlead", gw + "/api/query_results", gateway=gw)
    approve_endpoint("redash_pns", gw + "/api/jobs/abc-1", gateway=gw)
    approve_endpoint("redash_pns", gw + "/api/query_results/99.json", gateway=gw)
    for source, url, gateway in (
        ("redash_hotlead", gw + "/api/queries/1", gw),                         # not a fixed Redash path
        ("redash_hotlead", "https://other.ngrok-free.app/api/query_results", gw),  # not the configured gateway
        ("redash_hotlead", "http://gw-test.ngrok-free.app/api/query_results", "http://gw-test.ngrok-free.app"),  # no plain http
        ("pns", gw + "/pns-call-insights", gw),                                 # the gateway is only for Redash here
        ("redash_hotlead", gw + "/api/query_results", ""),                      # no gateway configured
    ):
        with pytest.raises(SourcePolicyError):
            approve_endpoint(source, url, gateway=gateway)


def test_genome_hands_the_gateway_to_redash_only_when_set(env, monkeypatch):
    settings, reg, _, _ = env
    seen = {}

    async def fake_fetch(self, source, seller_id):
        seen.update(base=self.gateway_base, headers=dict(self.gateway_headers), seller=seller_id)
        return [{"raw": {"lead_call_summary": "x"}}]
    monkeypatch.setattr(ConnectorService, "fetch", fake_fetch)
    owner = SimpleNamespace(v4_config=SimpleNamespace(values={}), settings=settings, registry=reg, glid_access=lambda: "dev")
    rows = asyncio.run(Genome._redash(owner, "redash_hotlead", "12345"))
    assert rows == [{"lead_call_summary": "x"}] and seen["seller"] == "real-12345"
    assert seen["base"] == GW and seen["headers"].get("ngrok-skip-browser-warning") == "1"
    settings.values["upstream"]["gateway_enabled"] = False
    asyncio.run(Genome._redash(owner, "redash_pns", "12345"))
    assert seen["base"] == ""
