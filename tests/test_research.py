"""Raw indexing preserves source strings, identities and contradictory evidence."""

from pathlib import Path

from backend.research import number, raw_alias, raw_document, raw_mapping


def test_raw_source_is_lossless_and_null_is_unknown():
    """Numeric projections must not overwrite IDs, nulls or original fields."""
    row = {
        "fk_glusr_usr_id": "00001234567890123456",
        "eng_activity_30d": "",
        "eng_product_count": "0",
        "do_not_call_requested": "",
        "annual_turnover": "nan",
    }
    doc = raw_document(row, Path("ps07_seller_files_part1.csv"), 2, "a" * 64)
    assert doc["raw"] == row
    assert doc["seller_id"] == "00001234567890123456"
    assert doc["metrics"] == {"eng_product_count": 0.0}
    assert doc["flags"]["do_not_call_requested"] is None
    assert number("inf") is None


def test_raw_duplicates_have_distinct_lineage_but_replay_has_stable_ids():
    """A raw duplicate row is preserved; canonical evidence dedup is separate."""
    path, row = (
        Path("ps07_bot_calls_2026-09.csv"),
        {"attempt_id": "77", "fk_glusr_usr_id": "9"},
    )
    first = raw_document(row, path, 2, "b" * 64)
    assert first["id"] == raw_document(row, path, 2, "b" * 64)["id"]
    assert first["id"] != raw_document(row, path, 3, "b" * 64)["id"]
    assert first["id"] != raw_document(row, path, 2, "c" * 64)["id"]


def test_meeting_conflict_and_source_timezone_survive_projection():
    """Source disagreement is observable even without a useful summary."""
    row = {
        "attempt_id": "1",
        "fk_glusr_usr_id": "2",
        "disposition_label": "Meeting Fixed",
        "meeting_fixed": "0",
        "lead_call_summary": "",
        "call_start_time": "2026-09-01 09:00:00",
    }
    doc = raw_document(row, Path("ps07_bot_calls_2026-09.csv"), 2, "d" * 64)
    assert doc["meeting_conflict"] and not doc["summary_available"]
    assert doc["observed_at"] == "2026-09-01T03:30:00+00:00"
    assert doc["month"] == "2026-09"


def test_raw_schema_drift_stays_inside_disabled_source():
    """Changing evaluator JSON must not introduce uncontrolled mapping fields."""
    row = {
        "agent_output": '{"new_trait":{"value":true}}',
        "eval_agent": "topics",
    }
    doc = raw_document(
        row, Path("ps07_evaluator_outputs_2026-09.csv"), 2, "e" * 64
    )
    assert doc["evaluator_json_valid"] and doc["evaluator_keys"] == [
        "new_trait"
    ]
    mapping = raw_mapping(["agent_output", "eng_product_count"])
    assert mapping["mappings"]["dynamic"] == "strict"
    assert mapping["mappings"]["properties"]["raw"]["type"] == "flattened"
    assert mapping["mappings"]["properties"]["raw"]["ignore_above"] == 4096
    assert (
        raw_alias(Path("ps07_call_turns.csv"))
        == "sellergenome-raw-ps07-call-turns"
    )


def test_invalid_turns_are_quarantined_without_dropping_the_row():
    """Bad timing should prevent acoustic interpretation while retaining lineage."""
    row = {
        "start_sec": "7",
        "end_sec": "5",
        "speaker": "unknown",
        "text": "test",
    }
    doc = raw_document(row, Path("ps07_call_turns.csv"), 2, "f" * 64)
    assert doc["timing_invalid"] and doc["raw"] == row
