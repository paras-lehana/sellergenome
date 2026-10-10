"""Audit every private review citation without printing identifiers or source text."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def main():
    """Fail if paired artifacts, cohort counts or exact source spans disagree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=Path("data/pm-analysis.json")
    )
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    study = json.loads(args.report.read_text())
    counts = Counter()
    with sqlite3.connect(
        args.private_input.resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        assert (
            db.execute(
                "SELECT value FROM metadata WHERE key='study_id'"
            ).fetchone()[0]
            == study["study_id"]
        )
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        for (raw,) in db.execute("SELECT payload FROM cases"):
            case = json.loads(raw)
            counts["private_cases"] += 1
            assert case["study_id"] == study["study_id"]
            assert case["evaluation"]["version"] == study["version"]
            general = case["evaluation"]["source_label"] == "General (talked)"
            counts["all_general"] += general
            counts["sample_all"] += case["sample_member"]
            counts["sample_general"] += general and case["sample_member"]
            turns = {turn["turn_no"]: turn for turn in case["turns"]}
            for evidence in case["evaluation"]["evidence"]:
                text = (
                    case["summary"]
                    if evidence["source"] == "summary"
                    else turns[int(evidence["source"].split(":")[1])]["text"]
                )
                assert (
                    text[evidence["start"] : evidence["end"]]
                    == evidence["matched"]
                )
                counts["exact_spans_verified"] += 1
            for episode in case["evaluation"]["response_episodes"]:
                assert turns[episode["seller_turn"]]["speaker"] == "seller"
                if episode["response_turn"] is not None:
                    assert (
                        episode["response_turn"] == episode["seller_turn"] + 1
                    )
                    assert turns[episode["response_turn"]]["speaker"] == "bot"
                counts["episodes_checked"] += 1
    for key in ["all_general", "sample_all", "sample_general"]:
        assert counts[key] == study["groups"][key]["calls"]
    result = {
        "status": "passed",
        "study_id": study["study_id"],
        "version": study["version"],
        **counts,
    }
    if args.receipt:
        args.receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
