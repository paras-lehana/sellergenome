"""Meaningful quota, account pinning, review, privacy and interruption invariants."""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.config import Settings
from backend.live import LiveService
from backend.providers import Providers, ProviderUnavailable
from backend.sarvam import SarvamPool, quota_error
from backend.speech import SpeechService, normalize_segments, storage_url
from backend.store import Store
from backend.search import SearchIndex


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ELASTICSEARCH_URL", "")
    monkeypatch.setenv("PUBLIC_BASE_PATH", "")
    monkeypatch.setenv("SARVAM_API_KEY", "synthetic-primary")
    monkeypatch.setenv("SARVAM_BACKUP_API_KEY", "synthetic-backup")
    monkeypatch.setenv("OPERATOR_API_KEY", "synthetic-operator")
    config = Settings()
    config.values["jobs"]["enabled"] = False
    store = Store(config.db_path)
    providers = Providers(config)
    return config, store, providers


def test_quota_detection_does_not_switch_for_rate_limit_or_auth():
    assert quota_error(402, {})
    assert quota_error(429, {"error": {"code": "insufficient_quota_error"}})
    assert not quota_error(
        429, {"error": {"code": "rate_limit_exceeded_error"}}
    )
    assert not quota_error(401, {"error": {"code": "invalid_api_key_error"}})


@pytest.mark.parametrize("status", [401, 429, 500])
def test_non_quota_errors_do_not_spend_backup(setup, monkeypatch, status):
    config, store, _ = setup
    requests = []

    async def request(self, method, url, **kwargs):
        requests.append(kwargs["headers"]["api-subscription-key"])
        return httpx.Response(
            status, json={"error": {"code": "rate_limit_exceeded_error"}}
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    with pytest.raises(ProviderUnavailable):
        asyncio.run(
            SarvamPool(config, store).request(
                "POST", "/text-to-speech", json={}
            )
        )
    assert requests == ["synthetic-primary"]


def test_quota_fallback_persists_and_rotation_resets(setup, monkeypatch):
    config, store, _ = setup
    calls = []

    async def request(self, method, url, **kwargs):
        key = kwargs["headers"]["api-subscription-key"]
        calls.append(key)
        return httpx.Response(
            402 if key == "synthetic-primary" else 200,
            json={"audios": ["fixture"]},
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    pool = SarvamPool(config, store)
    assert asyncio.run(pool.request("POST", "/text-to-speech"))[1] == "backup"
    reloaded = SarvamPool(config, store)
    assert reloaded.slots() == ["backup"]
    assert (
        asyncio.run(reloaded.request("POST", "/text-to-speech"))[1] == "backup"
    )
    assert calls == [
        "synthetic-primary",
        "synthetic-backup",
        "synthetic-backup",
    ]
    with pytest.raises(ProviderUnavailable):
        asyncio.run(pool.request("POST", "/job/status", slot="primary"))
    config.sarvam_key = "rotated-primary"
    assert SarvamPool(config, store).slots() == ["primary", "backup"]
    assert "fingerprint" not in json.dumps(pool.public())


def test_provider_storage_allowlist():
    assert storage_url(
        "https://sample.blob.core.windows.net/test?signature=private"
    )
    for url in [
        "http://sample.blob.core.windows.net/test",
        "https://127.0.0.1/x",
        "https://evil.test/x",
        "https://sample.blob.core.windows.net.evil.test/x",
        "https://user@api.sarvam.ai/test",
        "https://api.sarvam.ai:8443/test",
    ]:
        with pytest.raises(ProviderUnavailable):
            storage_url(url)


def test_segment_validation_preserves_clusters():
    actual = normalize_segments(
        {
            "diarized_transcript": {
                "entries": [
                    {
                        "speaker_id": "speaker_1",
                        "transcript": "I prefer Hindi",
                        "start_time_seconds": 1,
                        "end_time_seconds": 3,
                    }
                ]
            }
        }
    )
    assert actual == [
        {
            "speaker": "speaker_1",
            "text": "I prefer Hindi",
            "start": 1.0,
            "end": 3.0,
        }
    ]
    for entry in [
        {},
        {
            "speaker_id": 0,
            "transcript": "bad",
            "start_time_seconds": 4,
            "end_time_seconds": 3,
        },
        {
            "speaker_id": 0,
            "transcript": "bad",
            "start_time_seconds": float("nan"),
            "end_time_seconds": 3,
        },
    ]:
        with pytest.raises(ProviderUnavailable):
            normalize_segments({"diarized_transcript": {"entries": [entry]}})


def completed(service, seller="private-fixture", synthetic=False):
    service.store.put(
        "sellers",
        seller,
        {"id": seller, "source_mode": "private", "metrics": {}},
    )
    service.store.put(
        "personas",
        seller,
        {"id": seller, "generated_at": "2026-01-01T00:00:00+00:00"},
    )
    job = service.create(
        b"fixture",
        "wav",
        "owner-fixture",
        "en-IN",
        2,
        seller,
        "2026-02-01T00:00:00+00:00",
        synthetic,
    )
    job.update(
        status="completed",
        segments=[
            {"start": 0.0, "end": 1.0, "speaker": "0", "text": "Agent secret"},
            {
                "start": 1.0,
                "end": 2.0,
                "speaker": "1",
                "text": "I prefer Hindi",
            },
        ],
        speaker_count=2,
    )
    service.save(job)
    return job


def test_review_ingests_only_seller_and_invalidates_with_outbox(setup):
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    job = completed(service)
    result = service.approve(
        job, job["segments"], {"0": "agent", "1": "seller"}
    )
    evidence = store.get("evidence", result["evidence_id"])
    assert (
        evidence["text"] == "I prefer Hindi"
        and "Agent secret" not in evidence["text"]
    )
    assert evidence["observed_at"] == job["source_at"]
    assert evidence["provenance"]["human_reviewed"] is True
    assert store.get("personas", job["seller_id"])["invalidated_at"]
    with store.connect() as db:
        assert db.execute(
            "SELECT 1 FROM index_outbox WHERE document_id=?",
            (result["evidence_id"],),
        ).fetchone()
    assert (
        service.approve(job, job["segments"], {"0": "agent", "1": "seller"})[
            "evidence_id"
        ]
        == result["evidence_id"]
    )


@pytest.mark.parametrize(
    "case", ["synthetic", "unknown", "cancelled", "suppressed"]
)
def test_review_rejects_unsafe_evidence(setup, case):
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    job = completed(service, synthetic=case == "synthetic")
    roles = {"0": "agent", "1": "unknown" if case == "unknown" else "seller"}
    if case == "cancelled":
        service.cancel(job)
    if case == "suppressed":
        store.put(
            "sellers",
            job["seller_id"],
            {
                "id": job["seller_id"],
                "source_mode": "private",
                "metrics": {"do_not_call": True},
            },
        )
    with pytest.raises(HTTPException):
        service.approve(job, job["segments"], roles)
    assert not store.get("evidence", "speech-" + job["id"])


def test_cancellation_fences_late_receipt_and_owner_isolation(setup):
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    job = completed(service)
    stale = dict(job)
    service.cancel(job)
    service.save(stale)
    assert service.get(job["id"], job["owner"])["status"] == "cancelled"
    with pytest.raises(HTTPException):
        service.get(job["id"], "other-owner")


def test_live_ticket_single_use_owner_bound(setup):
    config, _, providers = setup
    live = LiveService(config, providers)
    grant = live.grant("session", "owner")
    assert not live.consume(grant["ticket"], "session", "other")
    assert not live.consume(grant["ticket"], "session", "owner")
    grant = live.grant("session", "owner")
    assert live.consume(grant["ticket"], "session", "owner")
    assert not live.consume(grant["ticket"], "session", "owner")


def test_speech_api_requires_consent_and_owns_upload(setup, monkeypatch):
    config, store, providers = setup
    for name, value in [
        ("config", config),
        ("store", store),
        ("providers", providers),
        ("search", SearchIndex(config)),
        ("session_locks", {}),
    ]:
        monkeypatch.setattr(main, name, value)
    headers = {"X-Operator-Key": "synthetic-operator"}
    with TestClient(main.app) as client:
        assert client.get("/api/speech-jobs").status_code == 401
        form = {
            "consent": "true",
            "synthetic": "true",
            "source_at": datetime.now(timezone.utc).isoformat(),
        }
        response = client.post(
            "/api/speech-jobs",
            files={"file": ("x.wav", b"fixture", "audio/wav")},
            data=form,
            headers=headers,
        )
        assert response.status_code == 202
        assert "owner" not in response.json()
        assert client.cookies.get("persona_owner")
        identifier = response.json()["id"]
        assert (
            client.get(
                "/api/speech-jobs/" + identifier, headers=headers
            ).status_code
            == 200
        )
        client.cookies.clear()
        assert (
            client.get(
                "/api/speech-jobs/" + identifier, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/speech-jobs",
                files={"file": ("x.wav", b"fixture", "audio/wav")},
                data={**form, "consent": "false"},
                headers=headers,
            ).status_code
            == 422
        )


def test_cancel_during_upload_prevents_paid_start(setup, monkeypatch):
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    job = service.create(
        b"fixture",
        "wav",
        "owner",
        "en-IN",
        2,
        "",
        "2026-01-01T00:00:00Z",
        True,
    )
    calls = []

    async def request(method, path, **kwargs):
        calls.append(path)
        if path.endswith("/upload-files"):
            return {
                "upload_urls": {
                    "audio.wav": {
                        "file_url": "https://fixture.blob.core.windows.net/input"
                    }
                }
            }, "primary"
        if path.endswith("/status"):
            return {"job_state": "Accepted"}, "primary"
        return {"job_id": "remote-fixture"}, "primary"

    async def transfer(*args, **kwargs):
        current = service.get(job["id"], job["owner"])
        service.cancel(current)

    async def normalize(job):
        return service.directory / (job["id"] + ".wav")

    monkeypatch.setattr(service, "normalize", normalize)
    monkeypatch.setattr(service.pool, "request", request)
    monkeypatch.setattr(service, "transfer", transfer)
    asyncio.run(service.step(job))
    assert not any(path.endswith("/start") for path in calls)
    assert service.get(job["id"], "owner")["status"] == "cancelled"


def test_restart_resumes_existing_account_job_without_new_charge(
    setup, monkeypatch
):
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    job = service.create(
        b"fixture",
        "wav",
        "owner",
        "en-IN",
        2,
        "",
        "2026-01-01T00:00:00Z",
        True,
    )
    job.update(
        remote_id="existing",
        credential_slot="backup",
        fingerprint=service.pool.fingerprint("backup"),
        status="uploading",
    )
    service.save(job)
    calls = []

    async def request(method, path, **kwargs):
        calls.append((path, kwargs.get("slot")))
        return {"job_state": "Running"}, "backup"

    monkeypatch.setattr(service.pool, "request", request)
    asyncio.run(service.step(service.get(job["id"], "owner")))
    assert calls == [("/speech-to-text/job/v1/existing/status", "backup")] * 2
    assert service.get(job["id"], "owner")["status"] == "running"


def test_original_recording_api_resolves_only_known_id(setup, monkeypatch):
    config, store, providers = setup
    store.put(
        "sellers",
        "private-fixture",
        {"id": "private-fixture", "source_mode": "private", "metrics": {}},
    )
    store.put(
        "recordings",
        "recording-fixture",
        {
            "id": "recording-fixture",
            "seller_id": "private-fixture",
            "observed_at": "2026-02-01T00:00:00Z",
        },
        seller_id="private-fixture",
    )
    for name, value in [
        ("config", config),
        ("store", store),
        ("providers", providers),
        ("search", SearchIndex(config)),
        ("session_locks", {}),
    ]:
        monkeypatch.setattr(main, name, value)
    called = []

    async def fetch(identifier):
        called.append(identifier)
        return b"fixture", "mp3"

    with TestClient(main.app) as client:
        monkeypatch.setattr(main.app.state.recording_source, "fetch", fetch)
        headers = {"X-Operator-Key": "synthetic-operator"}
        assert (
            client.post(
                "/api/recordings/recording-fixture/transcribe",
                json={"consent": True},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/recordings/recording-fixture/transcribe",
                json={"consent": False},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/recordings/arbitrary/transcribe",
                json={"consent": True},
                headers=headers,
            ).status_code
            == 404
        )
        response = client.post(
            "/api/recordings/recording-fixture/transcribe",
            json={"consent": True},
            headers=headers,
        )
        assert response.status_code == 202
        assert response.json()["source_at"] == "2026-02-01T00:00:00Z"
        assert called == ["recording-fixture"]


def test_socket_rejects_origin_and_forged_ticket_without_provider(
    setup, monkeypatch
):
    config, store, providers = setup
    for name, value in [
        ("config", config),
        ("store", store),
        ("providers", providers),
        ("search", SearchIndex(config)),
        ("session_locks", {}),
    ]:
        monkeypatch.setattr(main, name, value)
    from starlette.websockets import WebSocketDisconnect

    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/sessions/unknown/live",
                headers={"Origin": "https://evil.test"},
            ):
                pass
        with client.websocket_connect(
            "/api/sessions/unknown/live",
            headers={"Origin": "https://portfolio.aidhunik.com"},
        ) as socket:
            socket.send_json({"ticket": "forged"})
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        assert not main.app.state.live.active


def test_normalization_recovers_low_rate_audio_and_rejects_corruption(setup):
    import wave
    import shutil
    import subprocess
    from array import array
    from math import sin, pi

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is a documented runtime dependency")
    config, store, providers = setup
    service = SpeechService(config, store, providers)
    original = config.data_dir / "original.wav"
    with wave.open(str(original), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(
            array(
                "h",
                [
                    round(4000 * sin(2 * pi * 220 * i / 8000))
                    for i in range(8000)
                ],
            ).tobytes()
        )
    mp3 = config.data_dir / "source.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original), str(mp3)],
        check=True,
    )
    job = service.create(
        mp3.read_bytes(),
        "mp3",
        "owner",
        "en-IN",
        1,
        "",
        "2026-01-01T00:00:00Z",
        True,
    )
    normalized = asyncio.run(service.normalize(job))
    with wave.open(str(normalized)) as reader:
        assert reader.getframerate() == 16000 and reader.getnchannels() == 1
        assert 0.9 <= reader.getnframes() / reader.getframerate() <= 1.2
    assert job["normalized"] is True and not job.get("remote_id")
    bad = service.create(
        b"corrupt audio",
        "mp3",
        "owner",
        "en-IN",
        1,
        "",
        "2026-01-01T00:00:00Z",
        True,
    )
    with pytest.raises(ProviderUnavailable):
        asyncio.run(service.normalize(bad))
    assert not bad.get("remote_id")
