"""Refresh races, restart recovery, suppression and index outage regressions."""

import asyncio
import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from backend.config import Settings
from backend.connectors import ConnectorError
from backend.jobs import JobConflict, JobRepository
from backend.store import Store
from backend.sync import SyncWorker
from backend.search import SearchIndex


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Temporary local state makes no source/provider network calls."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ELASTICSEARCH_URL", "")
    config = Settings()
    store = Store(config.db_path)
    store.put(
        "sellers",
        "real-123",
        {
            "id": "real-123",
            "name": "Private sample",
            "source_mode": "private",
            "metrics": {},
        },
    )
    store.put(
        "personas",
        "real-123",
        {"seller_id": "real-123", "needs_refresh": False},
    )
    return config, store, JobRepository(store, config.values["jobs"])


def observation(identifier="source-test", suppressed=False):
    """Verified UTC source time and a structured projection without private text."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": identifier,
        "seller_id": "real-123",
        "kind": "company",
        "source_mode": "private",
        "observed_at": now,
        "ingested_at": now,
        "text": "Structured source observation.",
        "attributes": {"contact_suppressed": suppressed, "category": "Tools"},
        "ttl_hours": 6,
    }


class FakeSearch:
    """Simulate ES outage/recovery and record actual attempted projections."""

    def __init__(self, available=False):
        self.available, self.receipts = available, []

    async def put(self, kind, identifier, payload):
        self.receipts.append((kind, identifier, copy.deepcopy(payload)))
        return self.available


def test_concurrent_enqueue_deduplicates_request_keys_and_active_pairs(state):
    """Sixteen concurrent requests create one job and preserve all request keys."""
    _, _, repository = state
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(
            pool.map(
                lambda n: repository.enqueue(
                    "company", "real-123", f"request-{n}"
                ),
                range(16),
            )
        )
    assert len({job[0]["id"] for job in jobs}) == 1
    assert sum(created for _, created in jobs) == 1
    assert repository.enqueue("company", "real-123", "request-0")[1] is False
    with pytest.raises(JobConflict, match="another source"):
        repository.enqueue("chat", "real-123", "request-0")
    repository.cancel(jobs[0][0]["id"])
    assert (
        repository.enqueue("company", "real-123", "request-1")[0]["state"]
        == "cancelled"
    )


def test_recovered_claim_fences_the_old_worker_and_hides_lease_tokens(state):
    """A restarted worker can finish; its predecessor cannot commit late results."""
    _, store, repository = state
    queued, _ = repository.enqueue("company", "real-123")
    first = repository.claim()
    with store.connect() as db:
        db.execute(
            "UPDATE sync_jobs SET lease_until=0 WHERE id=?", (queued["id"],)
        )
    second = repository.claim()
    assert (
        second["attempts"] == 2
        and first["lease_token"] != second["lease_token"]
    )
    with pytest.raises(JobConflict, match="lease expired"):
        repository.apply_events("real-123", [observation()], first)
    repository.apply_events("real-123", [observation()], second)
    assert repository.get(queued["id"])["state"] == "succeeded"
    assert store.get("personas", "real-123")["needs_refresh"] is True
    assert "lease_token" not in repository.get(queued["id"])


def test_expired_final_attempt_has_terminal_failure(state):
    """Crash recovery cannot create an unlimited retry budget."""
    _, store, repository = state
    job, _ = repository.enqueue("company", "real-123")
    repository.claim()
    with store.connect() as db:
        db.execute(
            "UPDATE sync_jobs SET attempts=max_attempts,lease_until=0 WHERE id=?",
            (job["id"],),
        )
    assert repository.claim() is None
    assert repository.get(job["id"])["error_code"] == "lease_exhausted"


def test_cancel_during_fetch_discards_result(state):
    """Cancel after fetching starts; returned observations never reach storage."""
    config, store, repository = state

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        class WaitingSource:
            async def fetch(self, source, seller_id):
                started.set()
                await finish.wait()
                return [observation("cancelled-observation")]

        worker = SyncWorker(
            store, FakeSearch(), config, lambda: WaitingSource()
        )
        job, _ = repository.enqueue("company", "real-123")
        running = asyncio.create_task(worker.run_once())
        await started.wait()
        repository.cancel(job["id"])
        finish.set()
        await running
        assert repository.get(job["id"])["state"] == "cancelled"
        assert store.get("evidence", "cancelled-observation") is None
        assert store.get("personas", "real-123")["needs_refresh"] is False

    asyncio.run(scenario())


def test_safe_retry_codes_backoff_and_attempt_budget(state):
    """No credential-bearing exception text persists in repeated timeout receipts."""
    config, store, repository = state

    class FailingSource:
        async def fetch(self, source, seller_id):
            raise ConnectorError(
                source,
                "timeout",
                "https://private.invalid/?AK=sensitive-token",
            )

    worker = SyncWorker(store, FakeSearch(), config, lambda: FailingSource())
    job, _ = repository.enqueue("company", "real-123")
    for attempt in range(1, 4):
        assert asyncio.run(worker.run_once())
        receipt = repository.get(job["id"])
        assert (
            receipt["attempts"] == attempt
            and receipt["error_code"] == "timeout"
        )
        assert "sensitive-token" not in json.dumps(receipt)
        assert receipt["state"] == ("retry_wait" if attempt < 3 else "failed")
        if attempt < 3:
            assert not asyncio.run(worker.run_once())
            with store.connect() as db:
                db.execute(
                    "UPDATE sync_jobs SET available_at=0 WHERE id=?",
                    (job["id"],),
                )
    retry, created = repository.retry(job["id"], "new-attempt-budget")
    assert (
        created and retry["parent_id"] == job["id"] and retry["attempts"] == 0
    )


def test_mapping_failure_does_not_ingest_uninterpreted_data(state):
    """A timestamp alone cannot satisfy business-field mapping validation."""
    config, store, repository = state

    class UnmappedSource:
        async def fetch(self, source, seller_id):
            event = observation("unmapped")
            event["attributes"] = {"observed_at": event["observed_at"]}
            return [event]

    worker = SyncWorker(store, FakeSearch(), config, lambda: UnmappedSource())
    job, _ = repository.enqueue("company", "real-123")
    asyncio.run(worker.run_once())
    receipt = repository.get(job["id"])
    assert (
        receipt["state"] == "failed"
        and receipt["error_code"] == "mapping_required"
    )
    assert store.get("evidence", "unmapped") is None


def test_unchanged_and_late_observations_preserve_persona_and_source_time(
    state,
):
    """Repeated polling cannot falsely refresh old evidence or invalidate a persona."""
    _, store, repository = state
    event = observation()
    assert repository.apply_events("real-123", [event])["changed_records"] == 1
    store.put(
        "personas",
        "real-123",
        {"seller_id": "real-123", "needs_refresh": False},
    )
    repeated = {**event, "ingested_at": datetime.now(timezone.utc).isoformat()}
    assert (
        repository.apply_events("real-123", [repeated])["skipped_records"] == 1
    )
    late = {
        **event,
        "observed_at": (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat(),
        "text": "Older conflict",
    }
    assert repository.apply_events("real-123", [late])["skipped_records"] == 1
    assert store.get("evidence", event["id"]) == event
    assert store.get("personas", "real-123")["needs_refresh"] is False


def test_invalid_batch_rolls_back_every_document_and_outbox_write(state):
    """One wrong-seller event rolls back the preceding valid event in the batch."""
    _, store, repository = state
    before = repository.summary()["indexing"]["pending"]
    wrong = {**observation("wrong"), "seller_id": "real-456"}
    with pytest.raises(JobConflict):
        repository.apply_events("real-123", [observation("partial"), wrong])
    assert store.get("evidence", "partial") is None
    assert repository.summary()["indexing"]["pending"] == before


def test_suppression_invalidation_and_completion_are_one_transaction(state):
    """ES outage does not delay the authoritative local contact-suppression flag."""
    _, store, repository = state
    job, _ = repository.enqueue("company", "real-123")
    repository.apply_events(
        "real-123", [observation(suppressed=True)], repository.claim()
    )
    assert repository.get(job["id"])["state"] == "succeeded"
    assert store.get("sellers", "real-123")["metrics"]["do_not_call"] is True
    assert store.get("personas", "real-123")["needs_refresh"] is True
    assert repository.summary()["indexing"]["pending"] == 3


def test_outbox_survives_restart_outage_then_recovers(state):
    """Failed delivery persists across new worker/storage objects and succeeds later."""
    config, store, repository = state
    search = FakeSearch()
    worker = SyncWorker(store, search, config)
    assert asyncio.run(worker.deliver_once())
    assert repository.summary()["indexing"] == {"pending": 2, "retrying": 1}
    restarted = SyncWorker(Store(store.path), search, config)
    search.available = True
    restarted.repository.retry_index()
    while asyncio.run(restarted.deliver_once()):
        pass
    assert restarted.repository.summary()["indexing"]["pending"] == 0
    assert len(search.receipts) == 3


def test_old_delivery_receipt_does_not_acknowledge_new_generation(state):
    """An edit while ES is responding remains due for its own delivery."""
    _, store, repository = state
    claim = repository.claim_index()
    assert claim["kind"] == "sellers"
    seller = store.get("sellers", "real-123")
    seller["metrics"]["do_not_call"] = True
    store.put("sellers", "real-123", seller)
    repository.finish_index(claim, True)
    assert repository.summary()["indexing"]["pending"] == 2
    repository.retry_index()
    claims = [repository.claim_index(), repository.claim_index()]
    updated = next(c for c in claims if c["kind"] == "sellers")
    assert updated["payload"]["metrics"]["do_not_call"] is True
    repository.finish_index(claim, True)
    assert repository.summary()["indexing"]["pending"] == 2
    for item in claims:
        repository.finish_index(item, True)
    assert repository.summary()["indexing"]["pending"] == 0


def test_opt_in_schedules_coalesce_downtime_and_obey_pause(state):
    """A day of downtime becomes one job; pause suppresses future dispatch."""
    _, store, repository = state
    assert repository.schedules() == []
    paused = repository.save_schedule("company", "real-123", 300, False)
    assert repository.dispatch_schedules(time.time() + 86400) == 0
    enabled = repository.save_schedule("company", "real-123", 300, True)
    assert paused["id"] == enabled["id"]
    with store.connect() as db:
        db.execute("UPDATE sync_schedules SET next_run=0")
    assert repository.dispatch_schedules() == 1
    assert repository.dispatch_schedules() == 0
    with store.connect() as db:
        db.execute("UPDATE sync_schedules SET next_run=0")
    assert (
        repository.dispatch_schedules() == 0
    )  # An active job already exists.
    repository.save_schedule("company", "real-123", 900, False)
    assert repository.dispatch_schedules(time.time() + 86400) == 0
    assert len(repository.list_jobs()) == 1


def test_pause_invalidated_schedule_never_rechecks_live_policy(state):
    """An operator can stop an old schedule after the source is disabled."""
    _, _, repository = state
    schedule = repository.save_schedule("company", "real-123", 300, True)

    def deny_policy(source, seller_id):
        raise ConnectorError(source, "disabled", "Source is disabled.")

    repository.policy_check = deny_policy
    paused = repository.pause_schedule(schedule["id"])
    assert not paused["enabled"]
    assert paused["seller_id"] == schedule["seller_id"]
    assert paused["source"] == schedule["source"]
    assert paused["interval_seconds"] == schedule["interval_seconds"]
    assert paused["next_run"] == schedule["next_run"]
    assert repository.dispatch_schedules(time.time() + 86400) == 0
    edited = repository.pause_schedule(schedule["id"], 900)
    assert edited["interval_seconds"] == 900 and not edited["enabled"]
    with pytest.raises(ValueError, match="bounds"):
        repository.pause_schedule(schedule["id"], 1)
    assert repository.schedules()[0] == edited
    assert repository.pause_schedule("schedule-missing") is None
    with pytest.raises(ConnectorError):
        repository.save_schedule("company", "real-123", 900, True)
    assert repository.list_jobs() == []


def test_queue_bound_and_graceful_release_preserve_attempt_budget(state):
    """A planned stop returns a claim without consuming another failure attempt."""
    config, _, repository = state
    config.values["jobs"]["max_pending_jobs"] = 1
    job, _ = repository.enqueue("company", "real-123")
    with pytest.raises(JobConflict, match="full"):
        repository.enqueue("chat", "real-123")
    claim = repository.claim()
    repository.release(claim)
    assert repository.get(job["id"])["state"] == "queued"
    assert repository.get(job["id"])["attempts"] == 0


def test_bulk_delivery_acknowledges_success_and_retries_only_failed_items(
    state, monkeypatch
):
    """ES bulk can succeed overall while one item fails; preserve that item's intent."""
    config, store, repository = state
    config.es_url = "https://elastic.example"
    search = SearchIndex(config)

    def upstream(request):
        assert request.url.path == "/_bulk"
        assert len(request.content.decode().splitlines()) == 4
        return httpx.Response(
            200,
            json={
                "errors": True,
                "items": [
                    {"index": {"status": 201}},
                    {"index": {"status": 429}},
                ],
            },
        )

    monkeypatch.setattr(
        search,
        "client",
        lambda: httpx.AsyncClient(
            base_url=config.es_url, transport=httpx.MockTransport(upstream)
        ),
    )
    worker = SyncWorker(store, search, config)
    assert asyncio.run(worker.deliver_batch()) == 2
    assert repository.summary()["indexing"] == {"pending": 1, "retrying": 1}
