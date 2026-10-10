"""5.9.6: upstream.glid_access (D51) — dev keeps the approved test manifest; prod lets any numeric GLID that a signed-in
user enters reach the live IndiaMART APIs. Sign-in stays required in both modes. All GLIDs and payloads are synthetic."""

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from backend.connectors import ConnectorError, ConnectorService
from backend.genome import registry, roster
from backend.genome.orchestrator import Orchestrator
from backend.genome.registry import ApiExecutor, ApiRegistry
from backend.genome.service import Genome
from backend.genome.settings import GenomeSettings
from backend.genome.store import GenomeStore
from backend.source_policy import SourcePolicyError, valid_glid

LISTED, UNLISTED = "12345", "55501234"


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


def genome_like(settings):
    """Genome's two approval methods bound to a stand-in that only has settings (no stores, no Elastic)."""
    g = SimpleNamespace(settings=settings)
    g.glid_access = lambda: Genome.glid_access(g)
    g.approved = lambda glid: Genome._approved(g, glid)
    return g


@pytest.fixture()
def env(tmp_path, monkeypatch):
    manifest = tmp_path / "approved.json"
    manifest.write_text(json.dumps({"approved_glids": [LISTED]}))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("IM_EMPLOYEE_AK", jwt(time.time() + 3600))
    monkeypatch.setenv("LEADS_TOKEN", "leads-token-test")
    settings = GenomeSettings()
    # Gateway off: requests go to the documented hosts, which the mock transport answers (no tunnel URL in Git).
    monkeypatch.setattr(settings, "values", {**settings.values, "upstream": {**settings.values["upstream"], "gateway_enabled": False}})
    g = genome_like(settings)
    executor = ApiExecutor(settings, ApiRegistry(settings, tmp_path / "config"), NoElastic(), GenomeStore(tmp_path / "v5.sqlite3"),
                           approved=g.approved)
    return settings, g, executor


def capture(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"RESPONSE": {"CODE": "200", "DATA": {"offerids": ["1527"]}}})
    real = httpx.AsyncClient
    monkeypatch.setattr(registry.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def test_default_is_dev_and_only_dev_or_prod_can_be_saved(env):
    settings, g, _ = env
    assert settings.get("upstream.glid_access") == "dev" and g.glid_access() == "dev"
    with pytest.raises(ValueError, match="dev .* or prod"):
        settings.update({"upstream.glid_access": "production"}, settings.revision)
    settings.update({"upstream.glid_access": "prod"}, settings.revision)
    assert settings.get("upstream.glid_access") == "prod"


def test_dev_uses_the_manifest_and_prod_any_numeric_glid(env):
    settings, g, _ = env
    assert g.approved(LISTED) and g.approved("real-" + LISTED) and not g.approved(UNLISTED)
    settings.values["upstream"]["glid_access"] = "prod"
    assert g.glid_access() == "prod"
    assert g.approved(UNLISTED) and g.approved("real-" + UNLISTED)
    assert not g.approved("abc") and not g.approved("") and not g.approved("real-") and not g.approved("syn-x")
    settings.values["upstream"]["glid_access"] = "Prod"     # only the exact value opens it; anything else is dev
    assert g.glid_access() == "dev" and not g.approved(UNLISTED)
    assert valid_glid("real-1") == "1"
    with pytest.raises(SourcePolicyError):
        valid_glid("12a")


def test_prod_sends_an_unlisted_glid_upstream_but_never_without_sign_in(env, monkeypatch):
    settings, _, executor = env
    seen = capture(monkeypatch)
    seller = roster.profile("real-" + UNLISTED)
    dev = asyncio.run(executor.call("leads.purchased", seller, operator=True))
    assert dev["status"] == "skipped" and "Not an approved test GLID" in dev["note"] and not seen
    settings.values["upstream"]["glid_access"] = "prod"
    anonymous = asyncio.run(executor.call("leads.purchased", seller, operator=False))
    assert anonymous["status"] == "skipped" and "PM session" in anonymous["note"] and not seen
    live = asyncio.run(executor.call("leads.purchased", seller, operator=True))
    assert live["status"] == "ok" and live["origin"] == "live_api" and len(seen) == 1
    assert f"glusrid={UNLISTED}" in str(seen[0].url)


def test_prod_reads_chats_with_real_buyers_and_skips_the_test_contacts(env):
    settings, _, executor = env
    orch = Orchestrator.__new__(Orchestrator)
    orch.settings, orch.executor = settings, executor
    settings.values["upstream"]["lms_test_contacts"] = [LISTED]
    results = {"pns.insights": {"status": "ok", "response": {"data": [{"SELLER_GLID": int(UNLISTED), "BUYER_GLID": 99999}]}}}
    seller = roster.profile("real-" + UNLISTED)
    assert orch._lms_contacts(seller, results)[0] == [LISTED]          # dev: only approved contacts
    settings.values["upstream"]["glid_access"] = "prod"
    assert orch._lms_contacts(seller, results)[0] == ["99999"]          # prod: the seller's own buyer, no test contact


def test_redash_connector_follows_the_switch(env):
    live = {"sources": {"redash_hotlead": {"mode": "live"}}}
    with pytest.raises(ConnectorError):
        ConnectorService(live).ensure_target("redash_hotlead", "real-" + UNLISTED)
    assert ConnectorService(live).ensure_target("redash_hotlead", "real-" + LISTED) == ("redash_hotlead", "real-" + LISTED)
    assert ConnectorService(live, allow_any_glid=True).ensure_target("redash_hotlead", "real-" + UNLISTED) == \
        ("redash_hotlead", "real-" + UNLISTED)
    with pytest.raises(ConnectorError):
        ConnectorService(live, allow_any_glid=True).ensure_target("redash_hotlead", "real-abc")


# --- HTTP surface: Admin saves the switch; search and architecture follow it ----------------------------------------

@pytest.fixture()
def op_client(tmp_path, monkeypatch):
    manifest = tmp_path / "approved.json"
    manifest.write_text(json.dumps({"approved_glids": [LISTED]}))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPERATOR_API_KEY", "synthetic-operator-key-for-tests")
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main.config, "operator_key", "synthetic-operator-key-for-tests")
    with TestClient(main.app) as c:
        yield c, {"x-operator-key": "synthetic-operator-key-for-tests", "Origin": "http://testserver"}


def test_admin_switch_changes_search_and_architecture(op_client):
    c, h = op_client
    first = lambda q, headers=None: c.get("/api/v5/search", params={"q": q}, headers=headers or {}).json()["items"][0]  # noqa: E731
    assert c.get("/api/v5/architecture").json()["gateway"]["glid_access"] == "dev"
    assert first(UNLISTED, h)["kind"] == "generated"                       # dev: unknown GLID → labelled demo seller
    settings = c.get("/api/v5/admin/settings", headers=h).json()
    body = {"changes": {"upstream.glid_access": "prod"}, "expected_revision": settings["revision"]}
    assert c.patch("/api/v5/admin/settings", json=body).status_code == 401  # anonymous can't flip it
    assert c.patch("/api/v5/admin/settings", json={**body, "changes": {"upstream.glid_access": "yes"}}, headers=h).status_code == 422
    assert c.patch("/api/v5/admin/settings", json=body, headers=h).status_code == 200
    assert c.get("/api/v5/architecture").json()["gateway"]["glid_access"] == "prod"
    private = first(UNLISTED, h)
    assert private["kind"] == "private" and private["seller_id"] == "real-" + UNLISTED and "prod" in private["note"]
    assert first(UNLISTED)["kind"] == "locked" and first(UNLISTED)["seller_id"] is None   # signed out: nothing invented, no data
