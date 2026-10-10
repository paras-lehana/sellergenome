"""Durable cohort jobs fence cancellation and do not repeat uncertain paid work."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.insight_jobs import InsightCohortService
from backend.insights import InsightService, normalize_call
from backend.persona import attach_pattern_evidence
from backend.store import Store


@pytest.fixture
def setup(tmp_path):
    config = SimpleNamespace(
        values={
            "jobs": {"enabled": False},
            "insights": {
                "enabled": True,
                "max_calls_per_cohort": 2,
                "max_cohort_jobs": 2,
            },
        },
        llm_model="fixture-model",
        llm_provider="fixture",
    )
    store = Store(tmp_path / "state.sqlite3")
    for identifier in ["real-123", "real-456"]:
        store.put(
            "sellers", identifier, {"id": identifier, "source_mode": "private"}
        )
    insights = InsightService(config, store)
    event = normalize_call(
        {
            "attempt_id": "fixture",
            "fk_glusr_usr_id": "123",
            "lead_call_summary": "The seller asked about pricing.",
            "call_start_time": datetime.now(timezone.utc).isoformat(),
        }
    )
    insights.ingest([event])
    return (
        config,
        store,
        insights,
        InsightCohortService(config, store, insights),
        event,
    )


def test_job_resumes_remaining_work_with_bounded_private_results(setup):
    _, store, _, jobs, _ = setup
    job = jobs.create("owner", ["real-123", "real-456"], False)
    assert job["status"] == "queued" and "owner" not in job
    asyncio.run(jobs.step(job["id"], "owner"))
    partial = jobs.get(job["id"], "owner")
    assert partial["cursor"] == 1 and partial["status"] == "queued"
    # A replacement service recovers durable state and continues only the remainder.
    restarted = InsightCohortService(jobs.config, store, jobs.insights)
    restarted.recover()
    asyncio.run(restarted.step(job["id"], "owner"))
    done = restarted.get(job["id"], "owner")
    assert done["status"] == "completed" and done["completed_sellers"] == 2
    assert done["new_model_results"] == 0
    assert "quote" not in json.dumps(restarted.public(done))
    with pytest.raises(HTTPException) as error:
        jobs.get(job["id"], "other-owner")
    assert error.value.status_code == 404


def test_cancel_during_model_wait_fences_evidence_cache_and_job_receipt(setup):
    _, store, insights, jobs, event = setup
    job = jobs.create("owner", ["real-123"], True)

    async def cancelled(*args):
        jobs.cancel(job["id"], "owner")
        return json.dumps(
            {
                "items": [
                    {
                        "trait": "price_concern",
                        "value": "pricing mentioned",
                        "actor_role": "seller",
                        "evidence_id": event["id"],
                        "quote": "pricing",
                        "reason": "Historical summary cue.",
                    }
                ]
            }
        )

    insights.providers = SimpleNamespace(chat=cancelled)
    asyncio.run(jobs.step(job["id"], "owner"))
    assert jobs.get(job["id"], "owner")["status"] == "cancelled"
    assert not store.get("evidence", event["id"]).get("llm_insights")
    assert insights.summary()["cached_extractions"] == 0
    assert jobs.get(job["id"], "owner")["results"] == []


def test_restart_does_not_retry_unknown_paid_attempt(setup):
    _, store, insights, jobs, _ = setup
    created = jobs.create("owner", ["real-123", "real-456"], True)
    job = jobs.get(created["id"], "owner")
    job.update(
        status="running",
        active_seller="real-123",
        active_token="fixture-token",
    )
    with store.connect() as db:
        jobs._write(db, job)
    jobs.recover()
    recovered = jobs.get(created["id"], "owner")
    assert recovered["cursor"] == 1 and recovered["failed_sellers"] == 1
    assert recovered["results"][0]["status"] == "completion_unknown"

    async def must_not_bill(*args):
        raise AssertionError(
            "No evidence for remaining seller; no gateway call allowed"
        )

    insights.providers = SimpleNamespace(chat=must_not_bill)
    asyncio.run(jobs.step(created["id"], "owner"))
    done = jobs.get(created["id"], "owner")
    assert (
        done["status"] == "completed_with_errors"
        and done["new_model_results"] == 0
    )


def test_selected_cohort_bounds_and_sample_isolation(setup):
    _, store, _, jobs, _ = setup
    for selected in [["real-123"] * 3, ["demo-test"], ["real-999"]]:
        with pytest.raises(HTTPException):
            jobs.create("owner", selected, True)
    store.put(
        "sellers", "real-789", {"id": "real-789", "source_mode": "sample"}
    )
    with pytest.raises(HTTPException):
        jobs.create("owner", ["real-789"], True)
    jobs.create("owner", ["real-123"])
    jobs.create("owner", ["real-456"])
    with pytest.raises(HTTPException) as error:
        jobs.create("owner", ["real-123"])
    assert error.value.status_code == 429


def test_corpus_patterns_have_explicit_denominators_and_no_individual_probability():
    result = attach_pattern_evidence(
        {},
        {
            "source_calls": 269455,
            "populated_summary_calls": 258736,
            "outcome_counts": {"Meeting Fixed": 31054},
            "source_time_window_IST": ["2026-04-01", "2026-09-30"],
        },
    )
    assert result["pattern_evidence"]["source_calls"] == 269455
    assert (
        result["pattern_evidence"]["individual_conversion_probability"] is None
    )
    assert result["pattern_evidence"]["causal_uplift"] is False
    assert (
        attach_pattern_evidence({}, None)["pattern_evidence"]["status"]
        == "unavailable"
    )
