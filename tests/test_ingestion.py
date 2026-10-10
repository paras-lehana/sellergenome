"""Data-boundary regressions: unknown flags, private text and connector bounds."""

import asyncio
import csv
import json
import sqlite3

import httpx
import pytest

from backend.connectors import ConnectorError, ConnectorService
from scripts.import_dataset import flag, import_dataset, timestamp


@pytest.fixture(autouse=True)
def test_identity_scope(tmp_path, monkeypatch):
    """Legacy connector fixtures use an isolated approved manifest, never live IDs."""
    manifest = tmp_path / "approved.json"
    manifest.write_text(json.dumps(["101", "102"]))
    monkeypatch.setenv("APPROVED_GLIDS_PATH", str(manifest))
    monkeypatch.setenv("PRIVATE_CONFIG_DIR", str(tmp_path / "config"))


def write_csv(path, items):
    """Write tiny domain fixtures, never copies of real seller records."""
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=items[0].keys())
        writer.writeheader()
        writer.writerows(items)


def test_flags_and_source_timezone():
    """CSV booleans preserve unknown and source timestamps use IST."""
    assert flag("1.0") is True
    assert flag("0.0") is False
    assert flag("") is None
    assert flag("2") is None
    assert timestamp("2026-09-30 12:00:00") == "2026-09-30T06:30:00+00:00"


def test_import_is_idempotent_and_does_not_copy_raw_text(tmp_path):
    """Every family contributes counts while contact and transcript PII stay out."""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    write_csv(
        dataset / "ps07_seller_files_part1.csv",
        [
            {
                "fk_glusr_usr_id": "101",
                "seller_city": "Demo City",
                "top_category_1": "Fasteners",
                "do_not_call_requested": "1.0",
                "showed_frustration": "",
                "asked_if_talking_to_bot": "0.0",
                "already_in_touch_with_executive": "0.0",
                "past_objections": "",
            }
        ],
    )
    call = {
        "attempt_id": "test-attempt",
        "fk_glusr_usr_id": "101",
        "disposition_label": "Meeting Fixed",
        "call_start_time": "2026-09-29 12:00:00",
        "lead_call_duration": "40",
        "call_recording_url": "https://private.example/RAW_SECRET.mp3",
        "lead_call_summary": "Private Person RAW_SECRET 9999999999 person@example.test",
        "lead_bot_version": "main",
        "has_turn_transcript": "1",
        "has_evaluator_output": "1",
    }
    write_csv(dataset / "ps07_bot_calls_2026-09.csv", [call])
    write_csv(dataset / "ps07_recording_sample_5000.csv", [call])
    write_csv(
        dataset / "ps07_evaluator_outputs_2026-09.csv",
        [
            {
                "attempt_id": "orphan-evaluation",
                "fk_glusr_usr_id": "101",
                "eval_agent": "disposition",
                "agent_output": '{"reason":"RAW_SECRET"}',
            }
        ],
    )
    write_csv(
        dataset / "ps07_call_turns.csv",
        [
            {
                "attempt_id": "test-attempt",
                "fk_glusr_usr_id": "101",
                "turn_no": "1",
                "speaker": "seller",
                "start_sec": "0",
                "end_sec": "1",
                "text": "RAW_SECRET",
            }
        ],
    )
    write_csv(
        dataset / "ps07_executive_calls.csv",
        [
            {
                "click_to_call_id": "exec-1",
                "fk_glusr_usr_id": "101",
                "status": "Answered",
            }
        ],
    )
    db_path = tmp_path / "test.sqlite3"
    first = import_dataset(dataset, db_path)
    second = import_dataset(dataset, db_path)
    assert first["counts"] == second["counts"]
    assert first["counts"]["source_evaluator_outputs"] == 1
    with sqlite3.connect(db_path) as database:
        assert (
            database.execute("SELECT COUNT(*) FROM sellers").fetchone()[0] == 1
        )
        assert (
            database.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            == 4
        )
        assert (
            database.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
            == 1
        )
        seller = json.loads(
            database.execute("SELECT payload FROM sellers").fetchone()[0]
        )
        assert seller["source_mode"] == "private"
        assert seller["metrics"]["do_not_call"] is True
        assert seller["metrics"]["prior_frustration"] is None
        assert seller["language_status"] == "unverified_default"
        assert seller["metrics"]["evaluation_counts"]["disposition"] == 1
        for table in ("sellers", "evidence", "recordings", "metadata"):
            for (payload,) in database.execute(f"SELECT payload FROM {table}"):
                assert "RAW_SECRET" not in payload
                assert "person@example.test" not in payload
                assert "9999999999" not in payload


def test_connector_requires_configuration_and_numeric_seller(monkeypatch):
    """No request can supply a URL in place of an approved seller identifier."""
    monkeypatch.delenv("PNS_BASE_URL", raising=False)
    monkeypatch.delenv("PNS_API_KEY", raising=False)
    service = ConnectorService({"sources": {"pns": {"mode": "live"}}})
    with pytest.raises(ConnectorError) as invalid:
        asyncio.run(service.fetch("pns", "https://attacker.test"))
    assert invalid.value.code == "invalid_seller"
    with pytest.raises(ConnectorError) as missing:
        asyncio.run(service.fetch("pns", "101"))
    assert missing.value.code == "not_configured"


def test_connector_projects_fields_and_bounds_pagination(monkeypatch):
    """Pagination is capped, private free text is dropped, source time retained."""
    monkeypatch.setenv("PNS_BASE_URL", "https://example.test")
    monkeypatch.setenv("PNS_API_KEY", "test-secret")
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "call_timestamp": "2026-10-07 10:00:00",
                        "buyer_mobile_number": "9999999999",
                        "extracted_data": {
                            "metadata": {"primary_language": "Hindi"},
                            "next_steps": {"callback": True},
                        },
                        "raw_quote": "RAW_SECRET",
                    }
                    for _ in range(25)
                ]
            },
        )

    service = ConnectorService(
        {
            "max_pages": 2,
            "max_records": 30,
            "sources": {"pns": {"mode": "live"}},
        },
        httpx.MockTransport(handler),
    )
    observations = asyncio.run(service.fetch("pns", "real-101"))
    assert len(requests) == 2
    assert len(observations) == 30
    assert requests[0]["PAGE_INDEX"] == 1
    assert requests[1]["PAGE_INDEX"] == 2
    assert observations[0]["observed_at"] == "2026-10-07T04:30:00+00:00"
    output = json.dumps(observations)
    assert "RAW_SECRET" not in output
    assert "test-secret" not in output
    assert "9999999999" not in output
    assert all(item["source_mode"] == "private" for item in observations)


def test_connector_does_not_follow_redirects_or_expose_keys(monkeypatch):
    """Credential-bearing upstream failures have a safe outward error contract."""
    monkeypatch.setenv("CONTEXT_BASE_URL", "https://example.test")
    monkeypatch.setenv("CONTEXT_API_KEY", "test-secret")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            302, headers={"Location": "https://attacker.test"}
        )

    service = ConnectorService(
        {"sources": {"context": {"mode": "live"}}},
        httpx.MockTransport(handler),
    )
    with pytest.raises(ConnectorError) as error:
        asyncio.run(service.fetch("context", "101"))
    assert error.value.code == "upstream_302"
    assert len(seen) == 1
    assert "test-secret" not in str(error.value)
    assert "http" not in str(error.value).lower().replace("http 302", "")


def test_redash_uses_only_fixed_query_and_bounded_result(monkeypatch):
    """A reviewed template uses approved scope and ignores legacy saved-query IDs."""
    monkeypatch.setenv("REDASH_BASE_URL", "https://warehouse.example.test")
    monkeypatch.setenv("REDASH_API_KEY", "test-secret")
    monkeypatch.setenv("REDASH_QUERY_ID", "42")
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["data_source_id"] == 13
            assert "WHERE fk_glusr_usr_id = 101" in body["query"]
            assert body["query"].endswith("LIMIT 25")
            return httpx.Response(
                200,
                json={
                    "job": {"id": "job-1", "status": 3, "query_result_id": 12}
                },
            )
        return httpx.Response(
            200,
            json={
                "query_result": {
                    "data": {
                        "rows": [
                            {
                                "fk_glusr_usr_id": "101",
                                "data_hotlead_disposition_dtlid": 1,
                                "lead_shl_trigger": 0,
                                "lead_call_summary": "--",
                                "private_quote": "RAW_SECRET",
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
    result = asyncio.run(service.fetch("redash", "101"))
    assert len(seen) == 2
    assert seen[0].url.path == "/api/query_results"
    assert result[0]["attributes"]["meeting_fixed"] is False
    assert result[0]["attributes"]["summary_status"] == "insufficient_evidence"
    assert "RAW_SECRET" not in json.dumps(result)


def test_canonical_endpoint_env_and_singular_enquiry_alias(monkeypatch):
    """The documented deployment names work with a complete URL template."""
    monkeypatch.setenv(
        "ENQUIRY_URL", "https://example.test/enquiries/{seller_id}?limit=20"
    )
    monkeypatch.setenv("ENQUIRY_API_KEY", "test-secret")
    monkeypatch.delenv("ENQUIRIES_BASE_URL", raising=False)
    monkeypatch.delenv("ENQUIRIES_API_KEY", raising=False)

    def handler(request):
        assert request.url.path == "/enquiries/101"
        assert request.url.params["limit"] == "20"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200, json={"data": []})

    service = ConnectorService(
        {"sources": {"enquiries": {"mode": "live"}}},
        httpx.MockTransport(handler),
    )
    assert asyncio.run(service.fetch("enquiry", "real-101")) == []


def test_connector_summary_contains_only_validated_mapped_values():
    """A persona prompt needs actual normalized values, while private fields stay out."""
    from backend.connectors import ConnectorService

    service = ConnectorService({})
    events = service._normalize(
        "company",
        {
            "name": "Company",
            "field_map": {
                "category": "category",
                "language": "language",
                "secret": "token",
                "status": "contact",
            },
        },
        "123",
        [
            {
                "category": "Tools",
                "language": "hi-IN",
                "token": "hidden-secret",
                "contact": "private@example.com",
            }
        ],
        "2026-10-07T12:00:00+00:00",
    )
    assert '"language": "hi-IN"' in events[0]["text"]
    assert "Tools" in events[0]["text"]
    assert "hidden-secret" not in events[0]["text"]
    assert "private@example.com" not in events[0]["text"]
