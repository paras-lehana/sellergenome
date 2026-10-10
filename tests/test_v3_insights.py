"""V3 source lineage, duplicate handling and bounded model inference regressions."""

import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.insights import InsightService, normalize_call
from backend.persona import adapt, generate_persona
from backend.demo import demo_sellers
from backend.provider_errors import ProviderUnavailable
from backend.store import Store
from backend.voice_profiles import tts_parameters


def row(**extra):
    return {
        "attempt_id": "synthetic-call-1",
        "fk_glusr_usr_id": "123456",
        "lead_call_summary": "The seller asked about pricing. The buyer wants a callback. The bot offered to explain.",
        "call_start_time": "2026-09-29 09:30:00",
        "disposition_label": "Meeting Fixed",
        "meeting_fixed": "0",
        **extra,
    }


@pytest.fixture
def service(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.put(
        "sellers",
        "real-123456",
        {"id": "real-123456", "source_mode": "private"},
    )
    config = SimpleNamespace(
        values={
            "insights": {
                "enabled": True,
                "max_events_per_request": 2,
                "max_characters": 500,
                "cache_ttl_hours": 24,
            }
        },
        llm_model="synthetic-model",
        llm_provider="fixture",
    )
    return InsightService(config, store)


def test_summary_conflict_actor_attribution_and_original_source_dates():
    event = normalize_call(row(), source_file="synthetic.csv", row_number=2)
    assert event["meeting"]["status"] == "disputed"
    assert event["observed_at"] == "2026-09-29T04:00:00+00:00"
    assert (
        event["strength"] == "weak" and event["actor_role"] == "mixed_summary"
    )
    actors = {cue["trait"]: cue["actor_role"] for cue in event["insights"]}
    assert actors["price_concern"] == "seller"
    assert actors["callback_requested"] == "buyer"
    assert actors["explanation_depth"] == "bot"
    assert all(
        event["text"][cue["span"]["start"] : cue["span"]["end"]]
        == cue["quote"]
        for cue in event["insights"]
    )


def test_redash_update_time_is_not_call_time_and_trigger_requires_mapping():
    data = {
        "fk_click_to_call_id": "synthetic-executive-call",
        "data_hotlead_disposition_dtlid": "synthetic-pk-1",
        "fk_glusr_usr_id": "123456",
        "lead_call_summary": "The seller was busy.",
        "last_updated_on": "2026-10-01 15:00:00",
        "lead_shl_trigger": "1",
    }
    unknown = normalize_call(data, source="redash")
    known = normalize_call(
        data, source="redash", trigger_map={"1": True, "0": False}
    )
    assert unknown["observed_at"] is None
    assert unknown["source_updated_at"] == "2026-10-01T09:30:00+00:00"
    assert unknown["meeting"]["reported"] is None
    assert known["meeting"]["reported"] is True
    assert known["meeting"]["completed_meeting"] is None


def test_idempotency_revision_conflicts_and_outbox_are_atomic(service):
    original = normalize_call(row(last_updated_on="2026-09-30 10:00:00"))
    assert service.ingest([original])["inserted"] == 1
    saved = service.store.get("evidence", original["id"])
    repeated = copy.deepcopy(original)
    repeated["ingested_at"] = "2099-01-01T00:00:00Z"
    assert service.ingest([repeated]) == {"unchanged": 1}
    assert (
        service.store.get("evidence", original["id"])["ingested_at"]
        == saved["ingested_at"]
    )
    newer = normalize_call(
        row(
            last_updated_on="2026-10-01 10:00:00",
            lead_call_summary="The seller requested a callback.",
        )
    )
    assert service.ingest([newer])["updated"] == 1
    assert service.ingest([original]) == {"older_revision_preserved": 1}
    unordered = normalize_call(
        row(lead_call_summary="The seller mentioned a refund.")
    )
    assert service.ingest([unordered]) == {"unsequenced_conflict_preserved": 1}
    assert (
        service.store.get("evidence", original["id"])["text"] == newer["text"]
    )
    with service.store.connect() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM insight_revisions").fetchone()[0]
            == 3
        )
        assert (
            db.execute(
                "SELECT generation FROM index_outbox WHERE kind='evidence'"
            ).fetchone()[0]
            == 2
        )


def test_gateway_extraction_caches_and_rejects_invented_quotes(service):
    event = normalize_call(row())
    service.ingest([event])
    calls = []

    async def good(system, payload):
        calls.append(payload)
        return json.dumps(
            {
                "items": [
                    {
                        "trait": "price_concern",
                        "value": "ask whether cost is still a concern",
                        "actor_role": "seller",
                        "evidence_id": event["id"],
                        "quote": "seller asked about pricing",
                        "reason": "Historical summary, not a verified current preference.",
                    }
                ]
            }
        )

    service.providers = SimpleNamespace(chat=good)
    first = asyncio.run(service.extract("real-123456", use_llm=True))
    second = asyncio.run(service.extract("real-123456", use_llm=True))
    assert len(calls) == 1 and second["cached"]
    assert first["items"][0]["strength"] == "weak"
    assert service.store.get("evidence", event["id"])["llm_insights"]
    # A separate prompt version bypasses cache and must still validate each span.
    service.config.values["insights"]["prompt_version"] = (
        "invalid-span-fixture"
    )

    async def bad(*args):
        data = json.loads(await good(*args))
        data["items"][0]["quote"] = "An invented seller quotation"
        return json.dumps(data)

    service.providers = SimpleNamespace(chat=bad)
    with pytest.raises(ProviderUnavailable, match="exact citation"):
        asyncio.run(service.extract("real-123456", use_llm=True))
    assert service.summary()["cached_extractions"] == 1


def test_refresh_during_model_request_fences_stale_result(service):
    event = normalize_call(row(last_updated_on="2026-09-30 10:00:00"))
    service.ingest([event])

    async def raced(*args):
        service.ingest(
            [
                normalize_call(
                    row(
                        lead_call_summary="The seller requested a refund.",
                        last_updated_on="2026-10-01 10:00:00",
                    )
                )
            ]
        )
        return json.dumps(
            {
                "items": [
                    {
                        "trait": "price_concern",
                        "value": "price",
                        "actor_role": "seller",
                        "evidence_id": event["id"],
                        "quote": "pricing",
                        "reason": "Historical cue.",
                    }
                ]
            }
        )

    service.providers = SimpleNamespace(chat=raced)
    with pytest.raises(ProviderUnavailable, match="changed during"):
        asyncio.run(service.extract("real-123456", use_llm=True))
    assert service.summary()["cached_extractions"] == 0
    assert not service.store.get("evidence", event["id"]).get("llm_insights")


def test_concurrent_selected_extractions_share_one_paid_request(service):
    """A manual request overlapping cohort work must reuse the first result."""
    event = normalize_call(row())
    service.ingest([event])
    calls = []

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            calls.append(True)
            started.set()
            await release.wait()
            return json.dumps(
                {
                    "items": [
                        {
                            "trait": "price_concern",
                            "value": "confirm whether price still matters",
                            "actor_role": "seller",
                            "evidence_id": event["id"],
                            "quote": "seller asked about pricing",
                            "reason": "Historical summary cue.",
                        }
                    ]
                }
            )

        service.providers = SimpleNamespace(chat=delayed)
        first = asyncio.create_task(
            service.extract("real-123456", use_llm=True)
        )
        await started.wait()
        second = asyncio.create_task(
            service.extract("real-123456", use_llm=True)
        )
        # Let the overlapping extraction reach the seller lock before releasing the model.
        await asyncio.sleep(0)
        assert calls == [True]
        assert not first.done() and not second.done()
        release.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(run())
    assert calls == [True]
    assert first["cached"] is False and second["cached"] is True
    assert first["items"] == second["items"]
    assert service.summary()["cached_extractions"] == 1


def test_context_and_cohort_model_budget_fail_closed(service):
    with pytest.raises(ValueError, match="cohort"):
        asyncio.run(
            service.extract_cohort(
                ["real-" + str(x) for x in range(11)], use_llm=True
            )
        )
    with pytest.raises(ValueError, match="private"):
        asyncio.run(service.extract_cohort(["demo-a"], use_llm=True))
    old = normalize_call(row())
    old["observed_at"] = None
    service.ingest([old])
    assert (
        asyncio.run(service.extract("real-123456", use_llm=True))["status"]
        == "no_eligible_evidence"
    )


def test_real_cooldown_logs_suppressed_detection_but_language_and_stop_apply():
    seller = demo_sellers()[0]
    base = generate_persona(
        seller, seller["evidence"], {"affect_cooldown_seconds": 30}
    )
    first, change, _, _ = adapt(base, "I am frustrated", "session", 1)
    held, decision, signal, _ = adapt(
        first, "That sounds useful", "session", 2
    )
    assert change["applied"] and signal == "interest"
    assert (
        held["revision"] == first["revision"] and decision["applied"] is False
    )
    assert decision["status"] == "suppressed_cooldown"
    switched, explicit, signal, _ = adapt(
        held, "Please speak English", "session", 3
    )
    assert explicit["applied"] and signal == "language_switch"
    _, stopped, signal, _ = adapt(switched, "Do not call me", "session", 4)
    assert stopped["applied"] and signal == "stop"
    assert stopped["after"]["tone"] == "respectful closure"


def test_grounded_plan_excludes_buyer_preferences_and_formality_changes_reply():
    seller = demo_sellers()[0]
    seller["language"] = "en-IN"
    seller["source_mode"] = "private"
    event = normalize_call(row())
    event["observed_at"] = datetime.now(timezone.utc).isoformat()
    persona = generate_persona(seller, [event], {})
    topics = {x["topic"] for x in persona["plan"]["grounded_next_steps"]}
    assert "price_concern" in topics and "callback_requested" not in topics
    formal = copy.deepcopy(persona)
    formal["fields"]["formality"]["value"] = "formal"
    _, _, _, formal_reply = adapt(formal, "hello", "session", 1)
    _, _, _, warm_reply = adapt(persona, "hello", "session", 1)
    assert formal_reply != warm_reply and formal_reply.startswith("Certainly.")
    assert all("strength" in field for field in persona["fields"].values())


def test_model_profile_rejects_unsupported_controls_before_paid_request():
    config = SimpleNamespace(
        values={
            "voice": {
                "languages": ["en-IN"],
                "tts_profile": "v3",
                "sample_rate": 24000,
                "max_text_chars": 2500,
                "tts_profiles": {
                    "v2_pitch": {"enabled": True, "sample_rate": 22050}
                },
            }
        }
    )
    with pytest.raises(ProviderUnavailable, match="Pitch is not supported"):
        tts_parameters(config, "Hello", "en-IN", "shubh", 1, pitch=0.2)
    with pytest.raises(ProviderUnavailable, match="Speaker"):
        tts_parameters(
            config, "Hello", "en-IN", "shubh", 1, profile="v2_pitch"
        )
    params = tts_parameters(
        config,
        "Hello",
        "en-IN",
        "anushka",
        1,
        pitch=0.25,
        profile="v2_pitch",
        streaming=True,
    )
    assert params["model"] == "bulbul:v2" and params["pitch"] == 0.25
    with pytest.raises(ProviderUnavailable, match="limit"):
        tts_parameters(
            config, "x" * 1501, "en-IN", "anushka", 1, profile="v2_pitch"
        )


def test_pinned_batch_credential_survives_rotation_and_new_requests_use_new_key(
    tmp_path, monkeypatch
):
    import httpx
    from backend.config import Settings
    from backend.sarvam import SarvamPool

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SARVAM_API_KEY", "fixture-original-key")
    monkeypatch.setenv("SARVAM_BACKUP_API_KEY", "")
    config = Settings()
    pool = SarvamPool(config)
    seen = []

    async def request(self, method, url, **kwargs):
        seen.append(kwargs["headers"]["api-subscription-key"])
        return httpx.Response(200, json={"job_id": "fixture-job"})

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    _, pin = asyncio.run(
        pool.request("POST", "/speech-to-text/job/v1", return_pin=True)
    )
    config.admin.replace_secret(
        "SARVAM_API_KEY", "fixture-new-key", config.admin.revision
    )
    asyncio.run(
        pool.request(
            "GET",
            "/speech-to-text/job/v1/fixture-job/status",
            slot=pin["slot"],
            key_version=pin["version"],
        )
    )
    asyncio.run(pool.request("POST", "/text-to-speech"))
    assert seen == [
        "fixture-original-key",
        "fixture-original-key",
        "fixture-new-key",
    ]
    assert pool.fingerprint("primary", pin["version"]) == pin["fingerprint"]
    with pytest.raises(ProviderUnavailable, match="pinned credential"):
        asyncio.run(
            pool.request(
                "GET",
                "/job/status",
                slot="primary",
                key_version="missing-version",
            )
        )


def test_index_aliases_use_v3_and_insights_remain_private_scoped():
    import httpx
    from backend.search import SearchIndex

    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "HEAD":
            return httpx.Response(404)
        if "/_alias/" in request.url.path:
            return httpx.Response(404)
        if request.url.path.endswith("/_search"):
            return httpx.Response(
                200, json={"hits": {"total": {"value": 0}, "hits": []}}
            )
        return httpx.Response(200, json={"acknowledged": True})

    config = SimpleNamespace(
        es_url="http://fixture.test",
        es_prefix="fixture-persona",
        es_username="",
        es_password="",
        values={"elasticsearch": {"timeout_seconds": 1}},
    )
    search = SearchIndex(config)
    search.client = lambda: httpx.AsyncClient(
        base_url=config.es_url, transport=httpx.MockTransport(handler)
    )
    asyncio.run(search.ensure())
    assert search.name("evidence") == "fixture-persona-evidence"
    mapping = next(
        json.loads(r.content)
        for r in requests
        if r.url.path == "/fixture-persona-evidence-v3/_mapping"
    )
    assert mapping["properties"]["insights"]["type"] == "nested"
    assert (
        mapping["properties"]["derived_fields"]["properties"]["strength"][
            "type"
        ]
        == "keyword"
    )
    asyncio.run(
        search.insights(
            "callback",
            "real-123456",
            "private",
            trait="callback_requested",
            actor="seller",
        )
    )
    body = json.loads(requests[-1].content)
    assert {"term": {"source_mode": "private"}} in body["query"]["bool"][
        "filter"
    ]
    assert {"term": {"seller_id": "real-123456"}} in body["query"]["bool"][
        "filter"
    ]
    assert "insights.actor_role" in json.dumps(body)


def test_missing_summary_meeting_conflict_preserved_without_fabricated_summary():
    event = normalize_call(row(lead_call_summary=""))
    assert (
        event["kind"] == "call_outcome" and event["summary_available"] is False
    )
    assert event["meeting"]["conflict"] and event["insights"] == []
    assert event["method"] == "structured_outcome_conflict"
    assert normalize_call(row(lead_call_summary="", meeting_fixed="1")) is None


def test_redash_primary_key_identifies_observation_call_link_is_not_identity():
    a = {
        "data_hotlead_disposition_dtlid": "pk-1",
        "fk_click_to_call_id": "same-call",
        "fk_glusr_usr_id": "123456",
        "lead_call_summary": "The seller was busy.",
    }
    b = {**a, "data_hotlead_disposition_dtlid": "pk-2"}
    first = normalize_call(a, source="redash")
    second = normalize_call(b, source="redash")
    assert first["id"] != second["id"]
    assert (
        first["provenance"]["call_key_hash"]
        == second["provenance"]["call_key_hash"]
    )
    with pytest.raises(ValueError, match="primary key"):
        normalize_call(
            {
                key: value
                for key, value in a.items()
                if key != "data_hotlead_disposition_dtlid"
            },
            source="redash",
        )


def test_index_migration_count_mismatch_never_publishes_alias():
    import httpx
    from backend.search import SearchIndex

    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/_count"):
            return httpx.Response(
                200, json={"count": 2 if "-v1/" in request.url.path else 1}
            )
        if request.url.path == "/_reindex":
            return httpx.Response(200, json={"failures": []})
        return httpx.Response(200, json={"acknowledged": True})

    config = SimpleNamespace(
        es_url="http://fixture.test",
        es_prefix="fixture-persona",
        es_username="",
        es_password="",
        values={"elasticsearch": {"timeout_seconds": 1}},
    )
    search = SearchIndex(config)
    search.client = lambda: httpx.AsyncClient(
        base_url=config.es_url, transport=httpx.MockTransport(handler)
    )

    async def already_ensured():
        pass

    search.ensure = already_ensured
    with pytest.raises(RuntimeError, match="count verification"):
        asyncio.run(search.migrate_v3())
    assert not any(r.url.path == "/_aliases" for r in requests)


def test_language_mention_is_not_preference_and_inferred_script_honors_cooldown():
    from backend.persona import detect_signal

    assert detect_signal(
        "Our products include English textbooks", "hi-IN"
    ) == ("neutral", None)
    seller = demo_sellers()[0]
    seller["language"] = "en-IN"
    base = generate_persona(
        seller, seller["evidence"], {"affect_cooldown_seconds": 30}
    )
    changed, _, _, _ = adapt(base, "I am frustrated", "session", 1)
    held, decision, signal, _ = adapt(
        changed, "नमस्ते, आज बात करते हैं", "session", 2
    )
    assert signal == "language_switch" and decision["applied"] is False
    assert held["fields"]["language"]["value"] == "en-IN"
    switched, decision, signal, _ = adapt(
        held, "Please speak Hindi", "session", 3
    )
    assert (
        decision["applied"]
        and switched["fields"]["language"]["value"] == "hi-IN"
    )


def test_v4_pitch_contract_and_observed_provider_access_status(
    tmp_path, monkeypatch
):
    from backend.config import Settings
    from backend.sarvam import SarvamPool
    from backend.voice_profiles import public_profiles

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("SARVAM_API_KEY", "fixture-pitch-key")
    config = Settings()
    config.values["voice"]["tts_profiles"]["v4_pitch"] = {"enabled": True}
    pool = SarvamPool(config)
    params = tts_parameters(
        config,
        "Hello",
        "en-IN",
        "shubh_en_recovery",
        1,
        pitch=0.2,
        profile="v4_pitch",
        streaming=True,
    )
    assert (
        params["model"] == "bulbul:v4-flash"
        and params["enable_preprocessing"] is True
    )
    with pytest.raises(ProviderUnavailable, match="Pitch exceeds"):
        tts_parameters(
            config,
            "Hello",
            "en-IN",
            "shubh_en_recovery",
            1,
            pitch=0.6,
            profile="v4_pitch",
        )
    pool.capability_error(
        {"message": "This model is currently in beta and not available."},
        "bulbul:v4-flash",
        "primary",
        "fixture-pitch-key",
    )
    profiles = {x["id"]: x for x in public_profiles(config)}
    assert profiles["v4_pitch"]["availability"] == "beta_access_required"
    assert "fixture-pitch-key" not in json.dumps(
        profiles
    ) and "fingerprint" not in json.dumps(profiles)
    pool.capability_error(
        {"error": {"message": "Model has been deprecated."}},
        "bulbul:v2",
        "primary",
        "fixture-pitch-key",
    )
    assert (
        next(p for p in public_profiles(config) if p["id"] == "v2_pitch")[
            "enabled"
        ]
        is False
    )
    config.sarvam_key = "fixture-rotated-key"
    assert (
        next(p for p in public_profiles(config) if p["id"] == "v4_pitch")[
            "availability"
        ]
        == "documented_capability_account_access_unverified"
    )


@pytest.mark.parametrize("seconds,turns,skip_to", [(0, 2, 2), (30, 0, 20)])
def test_turn_and_time_cooldowns_each_work_independently(
    seconds, turns, skip_to
):
    seller = demo_sellers()[0]
    base = generate_persona(
        seller,
        seller["evidence"],
        {"affect_cooldown_seconds": seconds, "switch_cooldown_turns": turns},
    )
    first, _, _, _ = adapt(base, "I am frustrated", "session", 1)
    held, decision, _, _ = adapt(
        first, "That sounds useful", "session", skip_to
    )
    assert (
        decision["applied"] is False and held["revision"] == first["revision"]
    )
    if turns:
        assert (
            decision["cooldown_remaining_turns"] > 0
            and decision["cooldown_remaining_seconds"] == 0
        )
    else:
        assert (
            decision["cooldown_remaining_seconds"] > 0
            and decision["cooldown_remaining_turns"] == 0
        )
    _, explicit, _, _ = adapt(held, "Please explain", "session", skip_to + 1)
    assert explicit["applied"] is True


def test_conservative_automatic_english_and_supported_recognition_hint():
    from backend.persona import detect_signal

    assert detect_signal(
        "We would like to discuss our business requirements today", "hi-IN"
    ) == ("language_switch", "en-IN")
    assert detect_signal(
        "Mujhe business ki details please abhi batana", "hi-IN"
    ) == ("neutral", None)
    assert detect_signal("Mumbai Traders AC DC Wholesale Supply", "hi-IN") == (
        "neutral",
        None,
    )
    assert detect_signal("Tell us your requirements", "hi-IN", "en-IN") == (
        "neutral",
        None,
    )
    assert detect_signal("Today we have questions", "hi-IN", "en-IN") == (
        "language_switch",
        "en-IN",
    )
    assert detect_signal(
        "This is frustrating, you are not listening", "hi-IN"
    ) == ("frustration", None)
    assert detect_signal("I am not upset", "en-IN") == ("neutral", None)
