"""Evidence semantics and privacy tests for the PM diagnostic study."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.call_diagnostics import clean_turns, evaluate, demo, call_id
from backend.analysis_study import Cohort, proportion, summary_help
from backend.analysis_review_api import analysis_review_router


def test_absent_summary_is_unknown_not_rejection():
    for summary in ["", "INFO_NA", "null"]:
        result = evaluate(summary, [], label="General (talked)", flag=False)
        assert result["status"] == "insufficient_evidence"
        assert result["primary_reason"] == "unknown"
        assert result["causal_reason"] is None


def test_negated_busy_and_bot_or_buyer_claim_do_not_define_seller_persona():
    result = evaluate(
        "The seller was not busy. The bot explained the price. The buyer asked about budget.",
        [],
        label="General (talked)",
    )
    assert "timing" not in result["reason_ids"]
    assert result["persona_ids"] == ["unclassified"]
    assert all(item["actor"] != "seller" for item in result["evidence"])


def test_first_objection_episode_is_counted_once_with_resolving_spans():
    turns = [
        {"turn_no": 1, "speaker": "seller", "text": "I am busy."},
        {"turn_no": 2, "speaker": "bot", "text": "Can I call back tomorrow?"},
        {"turn_no": 3, "speaker": "seller", "text": "I am still busy."},
    ]
    result = evaluate("The seller was busy.", turns, label="General (talked)")
    assert len(result["response_episodes"]) == 1
    assert result["response_episodes"][0]["response"] == "callback"
    assert result["response_episodes"][0]["resolved"] == "not_established"
    for item in result["evidence"]:
        text = (
            "The seller was busy."
            if item["source"] == "summary"
            else next(
                t["text"]
                for t in turns
                if item["source"] == f"turn:{t['turn_no']}"
            )
        )
        assert text[item["start"] : item["end"]] == item["matched"]


def test_conflicting_turns_and_foreign_identity_cannot_supply_rebuttal():
    def row(no, speaker, text, seller="7"):
        return {
            "turn_no": str(no),
            "speaker": speaker,
            "text": text,
            "start_sec": "1",
            "end_sec": "2",
            "fk_glusr_usr_id": seller,
        }

    source = [
        row(1, "seller", "I am busy."),
        row(2, "bot", "Call back?"),
        row(2, "bot", "Goodbye."),
        row(3, "bot", "Call later?", "8"),
    ]
    clean, rejected = clean_turns(source, "7")
    assert rejected == {"identity_mismatch": 1, "conflicting_slots": 1}
    assert len(clean) == 1
    result = evaluate("", clean, label="General (talked)")
    assert result["response_episodes"][0]["response"] == "not_observed"


def test_exact_duplicates_do_not_amplify_turn_evidence():
    row = {
        "turn_no": "1",
        "speaker": "seller",
        "text": "No need.",
        "start_sec": "0",
        "end_sec": "2",
        "fk_glusr_usr_id": "7",
    }
    clean, rejected = clean_turns([row, row.copy()], "7")
    assert len(clean) == 1 and rejected["exact_duplicate"] == 1


def test_outcome_disagreement_is_preserved_and_meeting_not_inferred():
    result = evaluate(
        "The call ended.", [], label="General (talked)", flag=True
    )
    assert result["status"] == "disputed_outcome"
    assert result["source_label"] == "General (talked)"
    assert result["meeting_flag"] is True


def test_gapped_or_unknown_speaker_does_not_establish_response():
    for following in [
        {"turn_no": 3, "speaker": "bot", "text": "Call later?"},
        {"turn_no": 2, "speaker": "unknown", "text": "Call later?"},
    ]:
        result = evaluate(
            "",
            [{"turn_no": 1, "speaker": "seller", "text": "Busy."}, following],
            label="General (talked)",
        )
        assert result["response_episodes"][0]["response"] == "not_observed"


def test_overlap_keeps_call_and_opportunity_denominators_separate():
    cohort = Cohort()
    result = demo("timing")["evaluation"]
    cohort.add(
        {
            "attempt_id": "a",
            "fk_glusr_usr_id": "1",
            "disposition_label": "General (talked)",
        },
        result,
        True,
        demo("timing")["turns"],
    )
    cohort.add(
        {
            "attempt_id": "b",
            "fk_glusr_usr_id": "1",
            "disposition_label": "General (talked)",
        },
        evaluate("", [], label="General (talked)"),
        False,
        [],
    )
    data = cohort.export()
    assert data["calls"] == 2 and data["unique_sellers"] == 1
    assert data["response_calls"] == {
        "count": 1,
        "denominator": 1,
        "percent": 100.0,
    }
    assert (
        next(r for r in data["reasons"] if r["id"] == "timing")["percent"]
        == 50.0
    )
    assert proportion(0, 0)["percent"] is None
    assert len(summary_help()) == 7


@pytest.fixture
def review_client(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/pm-analysis.json").write_text(
        json.dumps({"study_id": "verified", "groups": {}})
    )
    payload = {
        "seller_glid": "7",
        "observed_at": "2026-07-01",
        "sample_member": True,
        "turns": [],
        "summary": "The seller was busy.",
        "evaluation": evaluate(
            "The seller was busy.", [], label="General (talked)", flag=False
        ),
    }
    with sqlite3.connect(tmp_path / "analysis-review.sqlite3") as db:
        db.executescript(
            "CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE cases(id TEXT,sample INTEGER,label TEXT,primary_reason TEXT,payload TEXT); CREATE TABLE memberships(case_id TEXT,persona TEXT);"
        )
        db.execute(
            "INSERT INTO metadata VALUES(?,?)", ("study_id", "verified")
        )
        db.execute(
            "INSERT INTO cases VALUES(?,?,?,?,?)",
            (
                call_id("a"),
                1,
                "General (talked)",
                "timing",
                json.dumps(payload),
            ),
        )

    def authorize(request):
        if request.headers.get("X-Test-Access") != "yes":
            raise HTTPException(401, "Sign in")

    app = FastAPI()
    app.include_router(
        analysis_review_router(
            tmp_path, lambda: SimpleNamespace(data_dir=tmp_path), authorize
        )
    )
    with TestClient(app) as client:
        yield client, tmp_path


def test_public_safe_report_and_synthetic_demo_private_cases_require_access(
    review_client,
):
    client, _ = review_client
    assert client.get("/api/analysis/pm-study").status_code == 200
    assert (
        client.get("/api/analysis/evaluator/demo").json()["mode"]
        == "synthetic_demo"
    )
    assert client.get("/api/analysis/review/calls").status_code == 401
    assert (
        client.get("/api/analysis/review/calls/" + call_id("a")).status_code
        == 401
    )
    assert (
        client.post(
            "/api/analysis/review/calls/" + call_id("a") + "/evaluate"
        ).status_code
        == 401
    )


def test_private_scope_rerun_and_stale_build_fail_closed(review_client):
    client, path = review_client
    headers = {"X-Test-Access": "yes"}
    response = client.get("/api/analysis/review/calls", headers=headers)
    assert (
        response.json()["total"] == 1
        and response.headers["cache-control"] == "no-store"
    )
    assert (
        client.get(
            "/api/analysis/review/calls?persona=invalid", headers=headers
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/analysis/review/calls?scope=sample_all&outcome=meeting",
            headers=headers,
        ).json()["total"]
        == 0
    )
    assert client.post(
        "/api/analysis/review/calls/" + call_id("a") + "/evaluate",
        headers=headers,
    ).json()["execution"]["recomputed"]
    (path / "data/pm-analysis.json").write_text('{"study_id":"different"}')
    assert (
        client.get("/api/analysis/review/calls", headers=headers).status_code
        == 503
    )


def test_apology_is_not_proof_of_a_stop_acknowledgement():
    result = evaluate(
        "",
        [
            {"turn_no": 1, "speaker": "seller", "text": "The price is high."},
            {
                "turn_no": 2,
                "speaker": "bot",
                "text": "Sorry, what cost did you expect?",
            },
        ],
        label="General (talked)",
    )
    assert result["response_episodes"][0]["response"] == "clarification"
    stopped = evaluate(
        "",
        [
            {"turn_no": 1, "speaker": "seller", "text": "Do not call me."},
            {
                "turn_no": 2,
                "speaker": "bot",
                "text": "We will not call again.",
            },
        ],
        label="General (talked)",
    )
    assert stopped["response_episodes"][0]["response"] == "respect_stop"


def test_published_study_denominators_and_no_private_payloads():
    from pathlib import Path

    study = json.loads(
        (
            Path(__file__).resolve().parents[1] / "data/pm-analysis.json"
        ).read_text()
    )
    assert study["canonical_calls"] == 269455
    assert study["groups"]["sample_all"]["calls"] == 5000
    assert study["groups"]["sample_all"]["unique_sellers"] == 4511
    assert study["groups"]["all_general"]["calls"] == 78365
    assert study["groups"]["sample_general"]["calls"] == 1540
    for group in study["groups"].values():
        assert (
            sum(r["count"] for r in group["primary_reasons"]) == group["calls"]
        )
        for response in group["responses"]:
            assert (
                sum(r["count"] for r in response["strategies"])
                == response["opportunities"]
            )

    def inspect(value):
        if isinstance(value, dict):
            assert (
                not {
                    "seller_glid",
                    "attempt_id",
                    "call_recording_url",
                    "text",
                    "summary",
                    "turns",
                    "payload",
                }
                & value.keys()
            )
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(study)


def test_summary_citations_preserve_original_whitespace_offsets():
    summary = " \n The seller was busy.  "
    result = evaluate(summary, [], label="General (talked)")
    span = next(
        item for item in result["evidence"] if item["reason"] == "timing"
    )
    assert summary[span["start"] : span["end"]] == "busy"


def test_persona_response_comparison_uses_its_own_call_denominator():
    from pathlib import Path

    study = json.loads(
        (
            Path(__file__).resolve().parents[1] / "data/pm-analysis.json"
        ).read_text()
    )
    for persona in study["personas"]:
        total = 0
        for row in persona["response_comparison"]:
            count = (
                row["meeting_claim"]
                + row["no_meeting_claim"]
                + row["disputed"]
            )
            assert row["meeting_share"]["denominator"] == count
            assert row["meeting_share"]["count"] == row["meeting_claim"]
            total += count
        assert total <= persona["with_usable_turns"] <= persona["calls"]


def test_callback_or_identity_question_is_not_an_explanation_preference():
    for text in [
        "The seller asked for a callback.",
        "The seller asked for the name.",
    ]:
        result = evaluate(text, [], label="General (talked)")
        assert "clarity" not in result["persona_ids"]
    result = evaluate(
        "The seller asked for more information about the package.",
        [],
        label="General (talked)",
    )
    assert "clarity" in result["persona_ids"]
