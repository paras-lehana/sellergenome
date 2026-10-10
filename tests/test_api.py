"""Browser ownership, private data and provider authorization boundaries."""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.config import Settings
from backend.providers import Providers
from backend.search import SearchIndex
from backend.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A temporary app state exercises routes without live provider spending."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_PATH", "")
    monkeypatch.setenv("ELASTICSEARCH_URL", "")
    monkeypatch.setenv("OPERATOR_API_KEY", "test-operator-long-secret")
    config = Settings()
    config.values["jobs"]["enabled"] = False
    monkeypatch.setattr(main, "config", config)
    monkeypatch.setattr(main, "store", Store(config.db_path))
    monkeypatch.setattr(main, "search", SearchIndex(config))
    monkeypatch.setattr(main, "providers", Providers(config))
    monkeypatch.setattr(main, "session_locks", {})
    with TestClient(main.app) as test_client:
        yield test_client


def test_public_demo_rehearsal_and_persistence(client):
    sellers = client.get("/api/sellers").json()
    assert sellers["total"] == 6
    seller = sellers["items"][0]
    persona = client.post(
        f"/api/sellers/{seller['id']}/persona", json={"use_llm": False}
    ).json()
    assert persona["method"] == "rules" and persona["indexed"] is False
    assert len(persona["derived_fields"]) >= 10
    created = client.post("/api/sessions", json={"seller_id": seller["id"]})
    assert created.status_code == 200
    identifier = created.json()["id"]
    response = client.post(
        f"/api/sessions/{identifier}/turns",
        json={"text": "I do not understand"},
    )
    assert response.status_code == 200
    assert response.json()["signal"] == "confusion"
    assert (
        len(client.get(f"/api/sessions/{identifier}").json()["switches"]) == 1
    )
    finished = client.post(f"/api/sessions/{identifier}/finish").json()
    assert finished["recommendation"] == "hold"
    assert client.post(f"/api/sessions/{identifier}/finish").json() == finished
    assert (
        client.post(
            f"/api/sessions/{identifier}/turns", json={"text": "hello"}
        ).status_code
        == 409
    )


def test_anonymous_cannot_spend_or_read_private_data(client):
    for path, body in [
        ("/api/sellers/demo-asha/persona", {"use_llm": True}),
        ("/api/voice/synthesize", {"text": "Hello"}),
        ("/api/sessions", {"seller_id": "demo-asha", "use_llm": True}),
        ("/api/integrations/company/sync", {"seller_id": "demo-asha"}),
    ]:
        assert client.post(path, json=body).status_code == 401
    for path in [
        "/api/sellers?dataset=private",
        "/api/sellers/real-any",
        "/api/recordings?dataset=private",
        "/api/evidence?dataset=private",
        "/api/personas/real-any/deconstruction",
    ]:
        assert client.get(path).status_code == 401
    assert client.patch("/api/settings", json={"pace": 1.1}).status_code == 401
    assert "test-operator-long-secret" not in client.get("/api/settings").text


def test_analysis_paths_export_and_contracts(client):
    """Exact public routes expose aggregates while unknown paths stay404."""
    assert client.get("/walkthrough").status_code == 200
    assert client.get("/analysis").status_code == 200
    report = client.get("/api/analysis/report").json()
    assert (
        report["total_rows"] == 908856
        and report["recording_exact_subset"] == 5000
    )
    assert "raw" not in report and "seller_id" not in report
    assert (
        client.get("/api/analysis/live?view=arbitrary_sql").status_code == 422
    )
    assert client.get("/api/analysis/live?period=2026-99").status_code == 422
    assert (
        client.get("/api/analysis/export")
        .headers["content-disposition"]
        .startswith("attachment;")
    )
    assert client.get("/analysis/not-a-project").status_code == 404
    assert (
        client.get("/api/personas/demo-asha/deconstruction").status_code == 503
    )


def test_automatic_persona_refresh_keeps_revision_monotonic(client):
    """Session refresh must not reset an indexed persona revision back to one."""
    persona = client.post(
        "/api/sellers/demo-asha/persona", json={"use_llm": False}
    ).json()
    persona["needs_refresh"] = True
    main.store.put("personas", "demo-asha", persona)
    session = client.post(
        "/api/sessions", json={"seller_id": "demo-asha"}
    ).json()
    assert session["persona"]["revision"] == persona["revision"] + 1
    assert (
        main.store.get("personas", "demo-asha")["revision"]
        == session["persona"]["revision"]
    )


def test_cookie_ownership_and_csrf(client):
    session = client.post(
        "/api/sessions", json={"seller_id": "demo-asha"}
    ).json()
    client.cookies.clear()
    assert client.get(f"/api/sessions/{session['id']}").status_code == 404
    assert (
        client.post(
            "/api/sessions",
            json={"seller_id": "demo-asha"},
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )


def test_contact_suppression_and_stop(client):
    main.store.put(
        "sellers",
        "real-suppressed",
        {
            "id": "real-suppressed",
            "source_mode": "private",
            "metrics": {"do_not_call": True},
        },
    )
    assert (
        client.post(
            "/api/sessions",
            json={"seller_id": "real-suppressed"},
            headers={"X-Operator-Key": "test-operator-long-secret"},
        ).status_code
        == 409
    )
    session = client.post(
        "/api/sessions", json={"seller_id": "demo-asha"}
    ).json()
    identifier = session["id"]
    result = client.post(
        f"/api/sessions/{identifier}/turns", json={"text": "Do not call me"}
    ).json()
    assert result["status"] == "stopped"
    assert (
        client.post(f"/api/sessions/{identifier}/finish").json()[
            "recommendation"
        ]
        == "drop"
    )


def test_validation_limits_and_settings(client):
    assert client.get("/api/sellers?page=0").status_code == 422
    assert (
        client.post("/api/sessions", json={"seller_id": ""}).status_code == 422
    )
    headers = {"X-Operator-Key": "test-operator-long-secret"}
    assert (
        client.patch(
            "/api/settings", headers=headers, json={"pace": 10}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/settings", headers=headers, json={"pace": 1.1}
        ).status_code
        == 200
    )
    assert main.store.get("metadata", "runtime_settings")["pace"] == 1.1
    assert client.get("/api/schema").json()["models"]["SpeechRequest"]


def test_proxy_stripped_static_path_matches_asgi_root_path(
    client, monkeypatch
):
    monkeypatch.setattr(main.config, "base_path", "/hackathon/seller-persona")
    assert client.get("/static/styles.css").status_code == 200
    assert (
        client.get("/hackathon/seller-persona/static/styles.css").status_code
        == 200
    )
    assert client.get("/health").status_code == 200


def test_new_evidence_invalidates_persona_and_stops_existing_session(client):
    from backend.persona import utcnow

    headers = {"X-Operator-Key": "test-operator-long-secret"}
    session = client.post(
        "/api/sessions", json={"seller_id": "demo-asha"}
    ).json()
    assert main.store.get("personas", "demo-asha") is not None
    event = {
        "id": "incoming-stop",
        "kind": "conversation",
        "text": "Seller requested no further contact.",
        "observed_at": utcnow(),
        "source_ref": "unit-test",
        "contact_suppressed": True,
    }
    assert (
        client.post("/api/sellers/demo-asha/events", json=event).status_code
        == 401
    )
    result = client.post(
        "/api/sellers/demo-asha/events", json=event, headers=headers
    )
    assert result.status_code == 200
    assert result.json()["needs_refresh"] is True
    assert main.store.get("personas", "demo-asha")["needs_refresh"] is True
    assert (
        client.post(
            "/api/sellers/demo-asha/events", json=event, headers=headers
        ).json()["status"]
        == "unchanged"
    )
    event["text"] = "Conflicting same-time revision."
    assert (
        client.post(
            "/api/sellers/demo-asha/events", json=event, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/sessions/{session['id']}/turns", json={"text": "Continue"}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/sessions/{session['id']}/finish").json()[
            "recommendation"
        ]
        == "drop"
    )


def test_recording_filters_change_aggregate_breakdowns(client):
    for identifier, cohort in [("r1", "curious"), ("r2", "rushed")]:
        main.store.put(
            "recordings",
            identifier,
            {
                "id": identifier,
                "cohort": cohort,
                "duration_bucket": "under_30s",
                "outcome": "Unknown",
                "transcript_status": "not_transcribed",
                "bot_version": "demo",
            },
            seller_id="demo-asha",
        )
    result = client.get("/api/recordings?cohort=rushed").json()
    assert result["total"] == 1 and result["all_recordings"] == 2
    assert result["breakdowns"]["duration"][0]["count"] == 1
    assert result["items"] == []


def test_voice_upload_auth_empty_type_and_size_bounds(client, monkeypatch):
    headers = {"X-Operator-Key": "test-operator-long-secret"}
    assert (
        client.post(
            "/api/voice/transcribe",
            files={"file": ("x.wav", b"audio", "audio/wav")},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/voice/transcribe",
            headers=headers,
            files={"file": ("x.txt", b"test", "text/plain")},
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/voice/transcribe",
            headers=headers,
            files={"file": ("x.wav", b"", "audio/wav")},
        ).status_code
        == 413
    )
    monkeypatch.setitem(main.config.values["voice"], "max_upload_mb", 1)
    assert (
        client.post(
            "/api/voice/transcribe",
            headers=headers,
            files={"file": ("x.wav", b"0" * 1048577, "audio/wav")},
        ).status_code
        == 413
    )


def test_source_jobs_and_schedules_are_private_typed_and_idempotent(
    client, tmp_path, monkeypatch
):
    """Queue metadata cannot expose real sellers or accept unknown source URLs."""
    headers = {"X-Operator-Key": "test-operator-long-secret"}
    manifest = tmp_path / "approved.json"
    manifest.write_text('["123"]')
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    main.config.values["connectors"]["sources"]["company"]["mode"] = "live"
    main.config.values["jobs"]["enabled"] = False
    main.store.put(
        "sellers",
        "real-123",
        {"id": "real-123", "source_mode": "private", "metrics": {}},
    )
    body = {"seller_id": "real-123", "request_key": "same-job"}
    for path in ["/api/sync-jobs", "/api/sync-schedules"]:
        assert client.get(path).status_code == 401
    assert (
        client.post("/api/integrations/company/jobs", json=body).status_code
        == 401
    )
    assert client.post("/api/index-deliveries/retry").status_code == 401
    assert (
        client.post(
            "/api/integrations/unknown/jobs", headers=headers, json=body
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/integrations/company/jobs",
            headers=headers,
            json={"seller_id": "demo-asha"},
        ).status_code
        == 422
    )
    queued = client.post(
        "/api/integrations/company/jobs", headers=headers, json=body
    )
    assert queued.status_code == 202
    job = queued.json()["job"]
    repeated = client.post(
        "/api/integrations/company/jobs", headers=headers, json=body
    ).json()
    assert repeated["job"]["id"] == job["id"] and not repeated["created"]
    assert client.get("/api/sync-jobs/" + job["id"]).status_code == 401
    assert (
        client.post("/api/sync-jobs/" + job["id"] + "/cancel").status_code
        == 401
    )
    assert (
        client.post(
            "/api/sync-jobs/" + job["id"] + "/cancel", headers=headers
        ).status_code
        == 200
    )
    retry = client.post(
        "/api/sync-jobs/" + job["id"] + "/retry", headers=headers
    ).json()
    assert retry["job"]["parent_id"] == job["id"]
    assert "real-123" not in client.get("/api/integrations").text
    schedule_body = {
        "source": "company",
        "seller_id": "real-123",
        "interval_seconds": 3600,
    }
    assert (
        client.post("/api/sync-schedules", json=schedule_body).status_code
        == 401
    )
    schedule = client.post(
        "/api/sync-schedules", headers=headers, json=schedule_body
    ).json()
    assert schedule["enabled"] is False
    assert (
        client.patch(
            "/api/sync-schedules/" + schedule["id"],
            headers=headers,
            json={"interval_seconds": 10},
        ).status_code
        == 422
    )
    enabled = client.patch(
        "/api/sync-schedules/" + schedule["id"],
        headers=headers,
        json={"enabled": True},
    ).json()
    assert enabled["enabled"] is True
    assert client.get("/api/schema").json()["models"]["JobSettings"]


def test_browser_identity_exists_before_polling_and_public_budget_is_separate(
    client, monkeypatch
):
    """First-visit refresh panels cannot consume a shared proxy/anonymous budget."""
    response = client.get("/")
    cookie = client.cookies.get("persona_owner")
    assert cookie and len(cookie) >= 32
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setitem(
        main.config.values["security"], "public_requests_per_minute", 2
    )
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/settings").status_code == 429
    assert (
        client.get(
            "/api/sync-jobs",
            headers={"X-Operator-Key": "test-operator-long-secret"},
        ).status_code
        == 200
    )
    # Reload preserves existing ownership; an invalid short cookie is replaced.
    client.get("/")
    assert client.cookies.get("persona_owner") == cookie
    client.cookies.clear()
    client.cookies.set("persona_owner", "short")
    client.get("/")
    replacement = client.cookies.get(
        "persona_owner", domain="testserver.local"
    )
    assert replacement and len(replacement) >= 32 and replacement != cookie
    assert client.get("/api/settings").status_code == 200
