"""Meaningful regressions for freshness, live changes and evidence integrity."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone

import pytest

from backend.config import Settings
from backend.demo import demo_sellers
from backend.persona import (
    adapt,
    detect_signal,
    freshness,
    generate_persona,
    outcome,
    select_context,
)
from backend.providers import ProviderUnavailable, Providers


def persona():
    """Use an explicit synthetic seller; no private dataset dependencies."""
    seller = demo_sellers()[0]
    return generate_persona(seller, seller["evidence"], {"evidence_limit": 12})


@pytest.mark.parametrize("value", [None, "", "bad date", 123])
def test_unknown_timestamp_never_becomes_fresh(value):
    assert freshness({"observed_at": value})["state"] == "unknown"


def test_stale_and_future_evidence_excluded_and_context_bounded():
    now = datetime.now(timezone.utc)
    events = [
        {
            "id": "fresh",
            "text": "x" * 10000,
            "observed_at": now.isoformat(),
            "ttl_hours": 1,
            "kind": "call",
        },
        {
            "id": "old",
            "text": "Old intent",
            "observed_at": (now - timedelta(days=10)).isoformat(),
            "ttl_hours": 1,
            "kind": "call",
        },
        {
            "id": "future",
            "text": "Future",
            "observed_at": (now + timedelta(days=10)).isoformat(),
            "ttl_hours": 1,
            "kind": "call",
        },
    ]
    selected = select_context(events, budget=20)
    assert [item["id"] for item in selected["events"]] == ["fresh"]
    assert selected["characters"] == 20
    assert selected["excluded"] == 2


@pytest.mark.parametrize(
    "text,signal",
    [
        ("I am very frustrated", "frustration"),
        ("I do not understand this", "confusion"),
        ("That sounds useful", "interest"),
        ("I am busy, make it quick", "rush"),
        ("Please speak English", "language_switch"),
    ],
)
def test_five_live_signals_are_reactive_and_facts_unchanged(text, signal):
    original = persona()
    updated, switch, detected, reply = adapt(original, text, "session", 1)
    assert detected == signal
    assert switch and switch["before"] != switch["after"]
    assert updated["plan"]["guardrails"] == original["plan"]["guardrails"]
    assert original["revision"] == 1
    assert updated["revision"] == 2
    assert reply


def test_repeated_signal_does_not_duplicate_switch_and_stop_wins():
    updated, first, _, _ = adapt(persona(), "I am busy", "session", 1)
    _, second, _, _ = adapt(updated, "I am busy", "session", 2)
    assert first and second is None
    _, switch, signal, _ = adapt(
        updated, "Do not call, I am busy", "session", 3
    )
    assert signal == "stop"
    result = outcome(
        {
            "turns": [
                {"role": "seller", "signal": "interest"},
                {"role": "seller", "signal": "stop"},
            ],
            "switches": [switch],
        }
    )
    assert result["recommendation"] == "drop"
    assert result["external_actions_taken"] is False


def test_missing_language_and_historical_flags_have_low_confidence():
    seller = demo_sellers()[0]
    seller["language_status"] = "unverified_default"
    seller["persona_tag_status"] = "historical_cohort_not_current_state"
    result = generate_persona(seller, seller["evidence"], {})
    assert result["fields"]["language"]["confidence"] == 0.35
    assert result["fields"]["language"]["evidence_ids"] == []
    assert "Historical" in result["fields"]["tone"]["reason"]


def test_llm_cannot_introduce_unknown_citations(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    provider = Providers(Settings())

    async def bad_output(*args, **kwargs):
        return '{"fields":{"tone":{"value":"warm and focused","reason":"injected","evidence_ids":["invented"]},"pace":{"value":1,"reason":"x","evidence_ids":["invented"]},"empathy":{"value":"high","reason":"x","evidence_ids":["invented"]},"language":{"value":"hi-IN","reason":"x","evidence_ids":["invented"]}}}'

    provider.chat = bad_output
    previous = persona()
    baseline = copy.deepcopy(previous)
    with pytest.raises(ProviderUnavailable, match="validation"):
        asyncio.run(provider.enrich(previous, demo_sellers()[0]["evidence"]))
    assert previous == baseline


def test_negated_signals_and_rejected_languages_do_not_switch_wrongly():
    assert detect_signal(
        "I do not speak Hindi, please speak English", "ta-IN"
    ) == ("language_switch", "en-IN")
    assert detect_signal("I am not frustrated", "en-IN")[0] == "neutral"
    assert detect_signal("I am not in a hurry", "en-IN")[0] == "neutral"


def test_model_metadata_cannot_override_trusted_provenance(
    tmp_path, monkeypatch
):
    import json

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    provider = Providers(Settings())
    baseline = persona()
    fields = {
        key: {
            "value": baseline["fields"][key]["value"],
            "reason": "Supported by supplied evidence.",
            "evidence_ids": ["demo-asha-ev-1"],
            "expires_at": "2099-01-01T00:00:00Z",
            "prompt_version": "injected",
        }
        for key in ["tone", "pace", "empathy", "language"]
    }

    async def model_output(*args, **kwargs):
        return json.dumps({"fields": fields})

    provider.chat = model_output
    previous_expiry = baseline["fields"]["tone"]["expires_at"]
    result = asyncio.run(
        provider.enrich(baseline, demo_sellers()[0]["evidence"])
    )
    assert result["fields"]["tone"]["expires_at"] == previous_expiry
    assert result["fields"]["tone"]["prompt_version"] != "injected"
