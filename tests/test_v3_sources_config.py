"""V3 egress, sample isolation, provenance, config and key-rotation contracts."""

import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.admin_config import (
    ConfigurationConflict,
    ConfigurationError,
    ensure_secret_version,
    find_secret_version,
    secret_value,
)
from backend.config import Settings
from backend.connectors import ConnectorError, ConnectorService, SOURCES
from backend.source_normalization import normalize
from backend.source_policy import SourcePolicyError, approve_endpoint
from backend.store import Store
from backend.sync import SyncWorker


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No test reads production credentials, approved IDs or databases."""
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ELASTICSEARCH_URL", "")
    manifest = tmp_path / "approved.json"
    manifest.write_text(json.dumps({"version": 1, "glids": ["101", "102"]}))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    for name in (
        "PNS_API_KEY",
        "REDASH_API_KEY",
        "LMS_API_KEY",
        "CONTEXT_API_KEY",
    ):
        monkeypatch.setenv(name, "synthetic-fixture-key")
    return tmp_path, Settings()


def run(awaitable):
    """Run one async fixture without creating a long-lived event-loop dependency."""
    return asyncio.run(awaitable)


def test_live_denies_unapproved_and_related_ids_before_network(
    isolated, monkeypatch
):
    """Exact manifest scope also applies to LMS's secondary contact identity."""
    seen = []
    transport = httpx.MockTransport(
        lambda request: seen.append(request) or httpx.Response(200, json=[])
    )
    service = ConnectorService(
        {"sources": {"pns": {"mode": "live"}, "chat": {"mode": "live"}}},
        transport,
    )
    for identifier in ("real-103", "236", "101 OR 1=1", "", "sample-pns"):
        with pytest.raises(ConnectorError):
            run(service.fetch("pns", identifier))
    monkeypatch.setenv("LMS_CONTACT_GLID", "103")
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("chat", "real-101"))
    assert error.value.code == "unapproved_glid"
    assert seen == []


def test_missing_manifest_denies_even_valid_numeric_id(isolated, monkeypatch):
    """Deployment omissions cannot quietly widen the test-only network policy."""
    monkeypatch.delenv("APPROVED_GLIDS_PATH")
    with pytest.raises(ConnectorError, match="protected approved"):
        run(
            ConnectorService({"sources": {"pns": {"mode": "live"}}}).fetch(
                "pns", "101"
            )
        )


def test_lms_rechecks_outgoing_contact_snapshot_after_manifest_change(
    isolated,
):
    """Changing the current contact cannot reauthorize a removed old payload."""
    path, _ = isolated
    options = {
        "max_pages": 2,
        "max_records": 50,
        "sources": {"chat": {"mode": "live", "contact_glid": "102"}},
    }
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content)["contact_glid"] == "102"
        # The next page retains its initial body; both the live configuration
        # and the protected manifest change before that page is attempted.
        options["sources"]["chat"]["contact_glid"] = "101"
        (path / "approved.json").write_text(json.dumps({"glids": ["101"]}))
        return httpx.Response(
            200, json={"data": [{"message": "Fixture"}] * 25}
        )

    service = ConnectorService(options, httpx.MockTransport(handler))
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("chat", "101"))
    assert error.value.code == "unapproved_glid"
    assert len(seen) == 1


def test_all_samples_normalize_without_network_and_remain_isolated(isolated):
    """Every advertised sample uses its real normalizer and stable synthetic dates."""
    seen = []
    transport = httpx.MockTransport(
        lambda request: seen.append(request) or httpx.Response(500)
    )
    options = {"sources": {source: {"mode": "sample"} for source in SOURCES}}
    service = ConnectorService(options, transport)
    for source in SOURCES:
        result = run(service.fetch(source, "sample-" + source))
        assert result and all(
            event["source_mode"] == "sample" for event in result
        )
        assert all(event["origin_mode"] == "sample" for event in result)
        assert all(
            event["observed_at"].startswith("2026-09-30") for event in result
        )
        assert service.last_receipt["mode"] == "sample"
        with pytest.raises(ConnectorError) as error:
            run(service.fetch(source, "real-101"))
        assert error.value.code == "sample_target_required"
    assert seen == []


def test_live_failure_never_falls_back_to_sample(isolated):
    """Provider/VPN failure is an explicit failed live receipt."""
    service = ConnectorService(
        {"sources": {"pns": {"mode": "live"}}},
        httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("pns", "101"))
    assert error.value.code == "upstream_503"
    assert service.last_checked["pns"]["status"] == "upstream_503"
    assert not service.last_receipt


def test_returned_wrong_seller_rejected_and_placeholder_not_inferred(isolated):
    """Scoped SQL is insufficient: validate returned row scope independently."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                "query_result": {
                    "data": {
                        "rows": [
                            {
                                "fk_glusr_usr_id": "102",
                                "lead_call_summary": "--",
                                "lead_shl_trigger": 0,
                            }
                        ]
                    }
                }
            },
        )

    service = ConnectorService(
        {"sources": {"redash_hotlead": {"mode": "live"}}},
        httpx.MockTransport(handler),
    )
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("redash_hotlead", "101"))
    assert error.value.code == "seller_mismatch"
    row = {
        "data_hotlead_disposition_dtlid": 1,
        "fk_click_to_call_id": 2,
        "fk_glusr_usr_id": "101",
        "call_start_time": "2026-09-30 12:00:00",
        "last_updated_on": "2026-10-01 12:00:00",
        "lead_call_summary": "--",
        "lead_shl_trigger": 0,
    }
    event = normalize(
        "redash_hotlead",
        row,
        "real-101",
        "live",
        SOURCES["redash_hotlead"],
        "2026-10-08T00:00:00+00:00",
    )
    assert event["attributes"]["meeting_fixed"] is False
    assert event["attributes"]["summary_status"] == "insufficient_evidence"
    assert "summary" not in event["attributes"]
    assert event["observed_at"].startswith("2026-09-30")
    assert event["source_updated_at"].startswith("2026-10-01")
    changed = normalize(
        "redash_hotlead",
        {
            **row,
            "last_updated_on": "2026-10-02 12:00:00",
            "lead_shl_trigger": 7,
        },
        "real-101",
        "live",
        SOURCES["redash_hotlead"],
        "2026-10-08T01:00:00+00:00",
    )
    assert event["id"] == changed["id"]
    assert changed["attributes"].get("meeting_fixed") is None
    assert event["canonical_call_id"] == changed["canonical_call_id"]


def test_buyer_traits_never_become_seller_attributes(isolated):
    """PNS preserves actor roles and deliberately names buyer-only preferences."""
    service = ConnectorService({"sources": {"pns": {"mode": "sample"}}})
    event = run(service.fetch("pns", "sample-pns"))[0]
    assert event["actor_role"] == "mixed_participants"
    assert event["attributes"]["buyer_persona"] == "retailer"
    assert "persona" not in event["attributes"]
    assert "seller traits" in event["attributes"]["role_boundary"]


def test_disabled_mid_poll_prevents_next_request_and_checkpoint_resumes(
    isolated,
):
    """Each poll honors source policy; resume skips creating a second query."""
    options = {
        "redash_poll_seconds": 0.1,
        "redash_max_polls": 2,
        "sources": {"redash_hotlead": {"mode": "live"}},
    }
    checkpoints, seen = [], []

    def first(request):
        seen.append(request)
        options["sources"]["redash_hotlead"]["mode"] = "disabled"
        return httpx.Response(
            200, json={"job": {"id": "job-fixture", "status": 1}}
        )

    service = ConnectorService(
        options, httpx.MockTransport(first), checkpoint=checkpoints.append
    )
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("redash_hotlead", "101"))
    assert error.value.code == "source_disabled"
    assert len(seen) == 1 and checkpoints
    options["sources"]["redash_hotlead"]["mode"] = "live"

    def resumed(request):
        assert request.method == "GET"
        seen.append(request)
        return httpx.Response(
            200, json={"query_result": {"data": {"rows": []}}}
        )

    service = ConnectorService(options, httpx.MockTransport(resumed))
    service.resume = checkpoints[-1]
    assert run(service.fetch("redash_hotlead", "101")) == []
    assert seen[-1].url.path == "/api/jobs/job-fixture"


def test_endpoint_registry_has_fixed_origins_and_paths(isolated, monkeypatch):
    """Internal HTTP is explicit; unrelated paths and destinations stay forbidden."""
    approve_endpoint("pns", "http://audio.imutils.com/pns-call-insights")
    for url in (
        "http://audio.imutils.com/delete-account",
        "https://attacker.test/pns-call-insights",
        "http://127.0.0.1/pns-call-insights",
    ):
        with pytest.raises(SourcePolicyError):
            approve_endpoint("pns", url)
    path = isolated[0] / "endpoints.json"
    path.write_text(
        json.dumps(
            {
                "company": [
                    {
                        "origin": "https://company.fixture.invalid",
                        "paths": ["/profiles/*"],
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("SOURCE_ENDPOINTS_PATH", str(path))
    approve_endpoint("company", "https://company.fixture.invalid/profiles/101")
    with pytest.raises(SourcePolicyError):
        approve_endpoint("company", "https://company.fixture.invalid/admin")


@pytest.mark.parametrize("completed", [False, True])
def test_redash_poll_budget_accepts_completion_on_final_allowed_poll(
    isolated, completed
):
    """Poll budget counts status reads; a final completed result is not lost."""
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.method == "POST":
            body = {"job": {"id": "fixture-job", "status": 1}}
        elif request.url.path.endswith(".json"):
            body = {"query_result": {"data": {"rows": []}}}
        else:
            body = {"job": {"id": "fixture-job", "status": 1}}
            if completed:
                body["job"].update(status=3, query_result_id=99)
        return httpx.Response(200, json=body)

    service = ConnectorService(
        {
            "redash_max_polls": 1,
            "redash_poll_seconds": 0.1,
            "sources": {"redash_hotlead": {"mode": "live"}},
        },
        httpx.MockTransport(handler),
    )
    if completed:
        assert run(service.fetch("redash_hotlead", "101")) == []
        assert seen[-1] == "/api/query_results/99.json"
    else:
        with pytest.raises(ConnectorError) as error:
            run(service.fetch("redash_hotlead", "101"))
        assert error.value.code == "job_pending"
    assert seen.count("/api/jobs/fixture-job") == 1


class OfflineSearch:
    """No Elasticsearch or source calls are needed for transaction fixtures."""

    async def put(self, *args):
        return False


def test_queue_schedule_retry_and_worker_all_recheck_policy(isolated):
    """Existing durable work cannot bypass a later source disablement."""
    _, config = isolated
    store = Store(config.db_path)
    ConnectorService.seed_targets(store)
    worker = SyncWorker(store, OfflineSearch(), config)
    repository = worker.repository
    with pytest.raises(ConnectorError):
        repository.enqueue("pns", "real-101")
    job, _ = repository.enqueue("pns", "sample-pns")
    schedule = repository.save_schedule("pns", "sample-pns", 300, True)
    config.values["connectors"]["sources"]["pns"]["mode"] = "disabled"
    assert run(worker.run_once())
    assert repository.get(job["id"])["error_code"] == "source_disabled"
    with pytest.raises(ConnectorError):
        repository.retry(job["id"])
    repository.dispatch_schedules(time.time() + 301)
    assert (
        next(
            row
            for row in repository.schedules()
            if row["id"] == schedule["id"]
        )["enabled"]
        is False
    )


def test_changed_summary_revision_preserves_call_time_and_invalidates(
    isolated,
):
    """Corrected summary is a revision of one call, not a new call occurrence."""
    _, config = isolated
    store = Store(config.db_path)
    ConnectorService.seed_targets(store)
    worker = SyncWorker(store, OfflineSearch(), config)
    repository = worker.repository
    occurrence = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    revision = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    row = {
        "data_hotlead_disposition_dtlid": 1,
        "fk_click_to_call_id": 2,
        "call_start_time": occurrence,
        "last_updated_on": occurrence,
        "lead_shl_trigger": 0,
        "lead_call_summary": "The seller requested a concise explanation and a callback.",
    }
    event = normalize(
        "redash_hotlead",
        row,
        "real-101",
        "live",
        SOURCES["redash_hotlead"],
        revision,
    )
    assert repository.apply_events("real-101", [event])["changed_records"] == 1
    store.put("personas", "real-101", {"needs_refresh": False})
    revised = normalize(
        "redash_hotlead",
        {**row, "last_updated_on": revision, "lead_shl_trigger": 1},
        "real-101",
        "live",
        SOURCES["redash_hotlead"],
        revision,
    )
    assert (
        repository.apply_events("real-101", [revised])["changed_records"] == 1
    )
    assert store.get("personas", "real-101")["needs_refresh"] is True
    assert store.get("evidence", revised["id"])["observed_at"] == occurrence
    assert (
        repository.apply_events("real-101", [revised])["changed_records"] == 0
    )


def test_configuration_revision_restart_rollback_and_invalid_atomicity(
    isolated,
):
    """A failed edit cannot change file, effective values, or revision history."""
    path, config = isolated
    admin = config.admin
    result = admin.update(
        {
            "persona.evidence_limit": 14,
            "connectors.sources.context.mode": "disabled",
        },
        0,
    )
    assert result["revision"] == 1
    assert Settings().values["persona"]["evidence_limit"] == 14
    saved = admin.path.read_bytes()
    with pytest.raises(ConfigurationError):
        admin.update({"persona.evidence_limit": 500}, 1)
    with pytest.raises(ConfigurationConflict):
        admin.update({"persona.evidence_limit": 15}, 0)
    with pytest.raises(ConfigurationError):
        admin.update({"connectors.approved_glids": ["999"]}, 1)
    assert admin.path.read_bytes() == saved
    admin.rollback(0, 1)
    assert config.values["persona"]["evidence_limit"] == 12
    assert admin.revision == 2
    assert (admin.path.stat().st_mode & 0o777) == 0o600
    assert "secret" not in json.dumps(admin.export()).lower()


def test_config_environment_lock_and_public_private_projection(
    isolated, monkeypatch
):
    """Operator forms explain deployment locks; anonymous settings hide source IDs."""
    monkeypatch.setenv("LLM_MODEL", "fixture-model")
    config = Settings()
    fields = {
        field["path"]: field
        for group in config.admin.view()["groups"]
        for field in group["fields"]
    }
    assert fields["llm.model"]["environment_locked"]
    with pytest.raises(ConfigurationError):
        config.admin.update({"llm.model": "other"}, 0)
    config.values["connectors"]["sources"]["chat"]["contact_glid"] = "101"
    assert "contact_glid" not in json.dumps(config.public())
    assert "synthetic-fixture-key" not in json.dumps(config.admin.view())


def test_secret_rotation_retains_pinned_baseline_and_redacts_exports(
    isolated, monkeypatch
):
    """Immutable speech versions survive account/key rotation and process restart."""
    monkeypatch.setenv("SARVAM_API_KEY", "synthetic-old-key")
    config = Settings()
    old = ensure_secret_version("SARVAM_API_KEY")
    response = config.admin.replace_secret(
        "SARVAM_API_KEY", "synthetic-new-key", 0
    )
    assert secret_value("SARVAM_API_KEY", old) == "synthetic-old-key"
    assert secret_value("SARVAM_API_KEY") == "synthetic-new-key"
    assert Settings().sarvam_key == "synthetic-new-key"
    fingerprint = hashlib.sha256(b"synthetic-old-key").hexdigest()
    assert find_secret_version("SARVAM_API_KEY", fingerprint) == old
    for output in (response, config.admin.view(), config.admin.export()):
        assert "synthetic-old-key" not in json.dumps(output)
        assert "synthetic-new-key" not in json.dumps(output)
    with pytest.raises(ConfigurationConflict):
        config.admin.replace_secret(
            "SARVAM_API_KEY", "another-fixture-key", 0, 0
        )


def test_configuration_merged_bounds_and_rollback_restart_notice(isolated):
    """Partial updates use existing bounds; rollback retains restart visibility."""
    _, config = isolated
    config.admin.update({"jobs.retry_base_seconds": 1}, 0)
    config.admin.update({"jobs.retry_max_seconds": 5}, 1)
    assert config.values["jobs"]["retry_max_seconds"] == 5
    with pytest.raises(ConfigurationError):
        config.admin.update({"jobs.retry_base_seconds": 6}, 2)
    config.admin.update({"app.public_base_path": "/preview"}, 2)
    restarted = Settings()
    assert restarted.base_path == "/preview"
    assert not restarted.admin.restart_pending
    result = restarted.admin.rollback(2, 3)
    assert "app.public_base_path" in result["restart_pending"]
    assert result["revision"] == 4


def test_meeting_mapping_reinterprets_same_call_without_renewing_its_date(
    isolated,
):
    """An admin code mapping is applied on replay without fabricating a new call."""
    _, config = isolated
    store = Store(config.db_path)
    ConnectorService.seed_targets(store)
    repository = SyncWorker(store, OfflineSearch(), config).repository
    row = {
        "data_hotlead_disposition_dtlid": 1,
        "fk_click_to_call_id": 2,
        "call_start_time": "2026-09-30 12:00:00",
        "last_updated_on": "2026-10-01 12:00:00",
        "lead_shl_trigger": 7,
        "lead_call_summary": "The seller agreed to a meeting after the explanation.",
    }
    service = ConnectorService(config.values)

    def mapped_event():
        return normalize(
            "redash_hotlead",
            row,
            "real-101",
            "live",
            service.specification("redash_hotlead"),
            datetime.now(timezone.utc).isoformat(),
        )

    original = mapped_event()
    assert not original["attributes"]["meeting_flag_mapping_known"]
    repository.apply_events("real-101", [original])
    store.put("personas", "real-101", {"needs_refresh": False})
    path = "connectors.sources.redash_hotlead.meeting_true_codes"
    config.admin.update({path: ["1", "1.0", "true", "7"]}, 0)
    interpreted = mapped_event()
    assert interpreted["attributes"]["meeting_fixed"] is True
    assert interpreted["attributes"]["meeting_flag_raw"] == "7"
    assert (
        interpreted["source_content_hash"] == original["source_content_hash"]
    )
    assert (
        repository.apply_events("real-101", [interpreted])["changed_records"]
        == 1
    )
    saved = store.get("evidence", original["id"])
    assert saved["observed_at"] == original["observed_at"]
    assert store.get("personas", "real-101")["needs_refresh"]
    assert (
        repository.apply_events("real-101", [interpreted])["changed_records"]
        == 0
    )
    with pytest.raises(ConfigurationError, match="overlap"):
        config.admin.update({path: ["0"]}, 1)
    with pytest.raises(ConfigurationError, match="lowercase"):
        config.admin.update({path: ["UNCHECKED"]}, 1)


def test_credential_rotation_checks_persisted_configuration_revision(isolated):
    """A stale second process cannot rotate after a concurrent config commit."""
    _, first = isolated
    stale = Settings()
    first.admin.update({"persona.evidence_limit": 14}, 0)
    with pytest.raises(ConfigurationConflict):
        stale.admin.replace_secret("SARVAM_API_KEY", "fixture-secret-new", 0)
    assert not (stale.admin.directory / "secrets.json").exists()
    result = stale.admin.replace_secret(
        "SARVAM_API_KEY", "fixture-secret-new", 1
    )
    assert result["revision"] == 1
    assert stale.values["persona"]["evidence_limit"] == 14


def test_voice_model_identity_cannot_be_edited_as_an_ineffective_knob(
    isolated,
):
    """Profile selection is editable; fixed provider model identities are not."""
    _, config = isolated
    for path in ("voice.tts_model", "voice.tts_profiles.v3.model"):
        with pytest.raises(
            ConfigurationError, match="deployment configuration"
        ):
            config.admin.update({path: "arbitrary-provider-model"}, 0)
    fields = {
        field["path"]: field
        for group in config.admin.view()["groups"]
        for field in group["fields"]
    }
    assert fields["voice.tts_model"]["readonly"]
    assert fields["voice.tts_profiles.v3.model"]["readonly"]
    assert not fields["voice.tts_profile"]["readonly"]


def test_durable_poll_checkpoint_survives_repository_restart_and_is_fenced(
    isolated,
):
    """Remote references persist, but cancellation rejects subsequent checkpoints."""
    from backend.jobs import JobConflict, JobRepository

    _, config = isolated
    store = Store(config.db_path)
    repository = JobRepository(store, config.values["jobs"])
    queued, _ = repository.enqueue("redash_hotlead", "real-101")
    claimed = repository.claim()
    progress = {
        "source": "redash_hotlead",
        "seller_id": "real-101",
        "job_id": "remote-fixture",
        "config_revision": 0,
    }
    repository.checkpoint(claimed, progress)
    restarted = JobRepository(store, config.values["jobs"])
    with store.connect() as db:
        saved = db.execute(
            "SELECT remote_progress FROM sync_jobs WHERE id=?", (queued["id"],)
        ).fetchone()[0]
    assert json.loads(saved) == progress
    assert "remote_progress" not in restarted.get(queued["id"])
    restarted.cancel(queued["id"])
    with pytest.raises(JobConflict):
        repository.checkpoint(claimed, progress)


def test_future_revision_cannot_poison_observation_order(isolated):
    """A plausible call date does not authorize an impossible update timestamp."""
    from backend.jobs import JobRepository

    _, config = isolated
    store = Store(config.db_path)
    ConnectorService.seed_targets(store)
    repository = JobRepository(store, config.values["jobs"])
    row = {
        "data_hotlead_disposition_dtlid": 1,
        "fk_click_to_call_id": 2,
        "call_start_time": "2026-01-01 12:00:00",
        "last_updated_on": (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(),
        "lead_shl_trigger": 1,
        "lead_call_summary": "The seller asked for a callback after a concise explanation.",
    }
    event = normalize(
        "redash_hotlead",
        row,
        "real-101",
        "live",
        SOURCES["redash_hotlead"],
        datetime.now(timezone.utc).isoformat(),
    )
    result = repository.apply_events("real-101", [event])
    assert result["changed_records"] == 0 and result["skipped_records"] == 1
    assert store.get("evidence", event["id"]) is None


def test_configuration_changed_during_fetch_discards_response(isolated):
    """A response fetched under the old revision cannot silently use new rules."""
    options = {"config_revision": 1, "sources": {"pns": {"mode": "live"}}}

    def handler(request):
        options["config_revision"] = 2
        return httpx.Response(200, json={"data": []})

    service = ConnectorService(options, httpx.MockTransport(handler))
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("pns", "real-101"))
    assert error.value.code == "configuration_changed"


def test_network_failure_is_safe_and_not_mislabeled_expired_credentials(
    isolated,
):
    """Connection errors are differentiated without leaking their raw details."""

    def handler(request):
        raise httpx.ConnectError(
            "PRIVATE_TOKEN host unavailable", request=request
        )

    service = ConnectorService(
        {"sources": {"pns": {"mode": "live"}}}, httpx.MockTransport(handler)
    )
    with pytest.raises(ConnectorError) as error:
        run(service.fetch("pns", "real-101"))
    assert error.value.code == "network_unreachable"
    assert "PRIVATE_TOKEN" not in str(error.value)
