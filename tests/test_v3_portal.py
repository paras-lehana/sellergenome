"""PM access, exact feedback anchors, persistence and honest workshop receipts."""

import time

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.access import AccessService
from backend.config import Settings
from backend.providers import Providers
from backend.search import SearchIndex
from backend.store import Store
from backend.workshop import WorkshopService


@pytest.fixture
def portal(tmp_path, monkeypatch):
    """Use isolated storage and a test-only password, with no provider spending."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("PUBLIC_BASE_PATH", "")
    monkeypatch.setenv("ELASTICSEARCH_URL", "")
    monkeypatch.setenv("OPERATOR_API_KEY", "")
    config = Settings()
    config.values["jobs"]["enabled"] = False
    store = Store(config.db_path)
    access = AccessService(config, store)
    access.set_password("test-password-only")
    monkeypatch.setattr(main, "config", config)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "search", SearchIndex(config))
    monkeypatch.setattr(main, "providers", Providers(config))
    monkeypatch.setattr(main, "access", access)
    monkeypatch.setattr(main, "session_locks", {})
    with TestClient(main.app) as client:
        yield client


def login(client):
    """Obtain access through the same password endpoint as the PM."""
    response = client.post(
        "/api/auth/login", json={"password": "test-password-only"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["operator"]
    assert "HttpOnly" in response.headers["set-cookie"]
    return response


def test_password_full_access_and_logout_revocation(portal):
    for path in [
        "/api/admin/config",
        "/api/feedback",
        "/api/workshop/runs",
        "/api/speech-jobs",
        "/api/sources/targets",
    ]:
        assert portal.get(path).status_code == 401
    login(portal)
    assert portal.get("/api/auth/session").json()["authenticated"]
    assert portal.get("/api/admin/config").status_code == 200
    token = portal.cookies.get(main.access.cookie_name)
    assert "test-password-only" not in main.access.password_path.read_text()
    assert "hash" not in portal.get("/api/auth/session").text
    portal.post("/api/auth/logout")
    assert not main.access.session(token)["authenticated"]
    portal.cookies.set(main.access.cookie_name, token)
    assert portal.get("/api/feedback").status_code == 401


def test_login_throttle_expiry_and_password_change(portal):
    for _ in range(5):
        assert (
            portal.post(
                "/api/auth/login", json={"password": "wrong"}
            ).status_code
            == 401
        )
    assert (
        portal.post(
            "/api/auth/login", json={"password": "test-password-only"}
        ).status_code
        == 429
    )
    with main.store.connect() as db:
        db.execute("DELETE FROM access_attempts")
    login(portal)
    token = portal.cookies.get(main.access.cookie_name)
    with main.store.connect() as db:
        db.execute(
            "UPDATE access_sessions SET expires_at=?", (time.time() - 1,)
        )
    assert portal.get("/api/feedback").status_code == 401
    login(portal)
    token = portal.cookies.get(main.access.cookie_name)
    response = portal.post(
        "/api/auth/password",
        json={
            "current_password": "test-password-only",
            "new_password": "a-different-test-password",
        },
    )
    assert response.status_code == 200
    assert response.json()["sessions_revoked"]
    assert not main.access.session(token)["operator"]
    assert (
        portal.post(
            "/api/auth/login", json={"password": "test-password-only"}
        ).status_code
        == 401
    )


def test_feedback_exact_target_history_conflict_export_and_restart(portal):
    login(portal)
    manifest = portal.get("/api/workshop/manifest").json()
    evidence_before = main.store.count("evidence")
    target = next(
        item for item in manifest["targets"] if item["id"].startswith("field:")
    )
    body = {
        "target_id": target["id"],
        "breadcrumb": "tampered display",
        "mode": "demo",
        "category": "idea",
        "text": "Explain why this field is a default.",
        "author_label": "PM",
    }
    invalid = portal.post(
        "/api/feedback", json={**body, "target_id": "unknown"}
    )
    assert invalid.status_code == 422
    created = portal.post("/api/feedback", json=body)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["breadcrumb"] == target["breadcrumb"]
    assert item["author_verified"] is False
    changed = portal.patch(
        "/api/feedback/" + item["id"],
        json={
            "status": "planned",
            "comment": "Include in next copy pass.",
            "expected_revision": 1,
        },
    )
    assert changed.json()["revision"] == 2
    assert len(changed.json()["history"]) == 2
    assert (
        portal.patch(
            "/api/feedback/" + item["id"],
            json={"status": "done", "expected_revision": 1},
        ).status_code
        == 409
    )
    restored = WorkshopService(main.config, Store(main.store.path))
    assert restored.feedback(status="planned")[0]["id"] == item["id"]
    assert (
        "Explain why"
        in portal.get("/api/feedback/export?format=markdown").text
    )
    assert (
        portal.get("/api/feedback/export?format=json").json()["items"][0][
            "status"
        ]
        == "planned"
    )
    assert (
        main.store.count("evidence") == evidence_before
    )  # Feedback never becomes seller evidence.


def test_demo_no_provider_calls_and_live_receipts(portal, monkeypatch):
    login(portal)

    async def forbidden(*args, **kwargs):
        pytest.fail("A deterministic workshop flow called a paid provider")

    monkeypatch.setattr(main.providers, "enrich", forbidden)
    monkeypatch.setattr(main.providers, "synthesize", forbidden)
    manifest = portal.get("/api/workshop/manifest").json()
    for section in manifest["sections"]:
        for scenario in section["scenarios"]:
            response = portal.post(
                "/api/workshop/runs",
                json={"scenario_id": scenario["id"], "mode": "demo"},
            )
            assert response.status_code == 200
            assert response.json()["execution_origin"] == "bundled_synthetic"
    run = portal.post(
        "/api/workshop/runs",
        json={
            "scenario_id": "contrast-personas",
            "mode": "live",
            "seller_id": "demo-asha",
        },
    ).json()
    assert (
        run["status"] == "completed"
        and run["execution_origin"] == "synthetic_backend"
    )
    assert run["result"]["fields"]
    unavailable = portal.post(
        "/api/workshop/runs",
        json={"scenario_id": "source-context", "mode": "live"},
    ).json()
    assert unavailable["status"] == "unavailable"
    assert unavailable["mode"] == "live"
    assert unavailable["id"]
    progress = portal.patch(
        "/api/workshop/progress", json={"completed": ["product-story"]}
    )
    assert progress.status_code == 200
    assert portal.get("/api/workshop/progress").json()["completed"] == [
        "product-story"
    ]


def test_password_csrf_and_no_secret_error_reflection(portal):
    assert (
        portal.post(
            "/api/auth/login",
            json={"password": "test-password-only"},
            headers={"Origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    assert portal.get("/api/settings").status_code == 200
    assert "test-password-only" not in portal.get("/api/settings").text


def test_password_rotation_during_verification_never_issues_new_generation(
    portal, monkeypatch
):
    original = main.access.verify

    def racing_verify(password):
        verified = original(password)
        main.access.set_password("rotated-test-password")
        return verified

    monkeypatch.setattr(main.access, "verify", racing_verify)
    assert (
        portal.post(
            "/api/auth/login", json={"password": "test-password-only"}
        ).status_code
        == 409
    )
    assert not portal.get("/api/auth/session").json()["authenticated"]


def test_workshop_rejects_unbounded_or_unknown_inputs(portal):
    login(portal)
    for value in [
        {"query": "arbitrary SQL"},
        {"text": "x" * 19000},
        {"use_llm": "false"},
    ]:
        response = portal.post(
            "/api/workshop/runs",
            json={
                "scenario_id": "product-story",
                "mode": "live",
                "input": value,
            },
        )
        assert response.status_code == 422
        assert '"input":' not in response.text


def test_live_socket_is_revoked_by_password_logout(portal, monkeypatch):
    """A one-use voice ticket cannot outlive its password session."""
    login(portal)
    main.config.values["voice"]["live_origins"] = ["http://testserver"]
    session = portal.post(
        "/api/sessions", json={"seller_id": "demo-asha"}
    ).json()
    ticket = portal.post(
        "/api/sessions/" + session["id"] + "/live-ticket",
        json={"consent": True},
    ).json()

    async def no_provider_socket(websocket, *args, **kwargs):
        await websocket.send_json({"event": "ready"})
        while True:
            await websocket.receive_text()

    monkeypatch.setattr(main.app.state.live, "run", no_provider_socket)
    with portal.websocket_connect(
        "/api/sessions/" + session["id"] + "/live",
        headers={"Origin": "http://testserver"},
    ) as socket:
        socket.send_json({"ticket": ticket["ticket"]})
        assert socket.receive_json()["event"] == "ready"
        assert portal.post("/api/auth/logout").status_code == 200
        assert socket.receive()["code"] == 1008


def test_cookie_session_secure_behind_tls_proxy(portal):
    response = portal.post(
        "/api/auth/login",
        json={"password": "test-password-only"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
