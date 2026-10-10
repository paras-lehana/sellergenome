#!/usr/bin/env python3
"""Import historical summaries once; private source text never enters stdout.

Run explicitly against a staging DB first. No network/model calls occur.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from backend.insights import EMPTY, InsightService, normalize_call
from backend.store import Store


def import_summaries(dataset, db_path, limit=0, batch_size=500):
    service = InsightService(None, Store(db_path))
    counts, pending = Counter(), []
    outcomes, cue_counts, cue_outcomes = Counter(), Counter(), Counter()
    source_dates = []
    for path in sorted(dataset.rglob("*bot_calls*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as file:
            for number, row in enumerate(csv.DictReader(file), 2):
                if limit and counts["source_rows"] >= limit:
                    break
                counts["source_rows"] += 1
                label = row.get("disposition_label") or "unknown"
                outcomes[label] += 1
                stamp = row.get("call_start_time")
                if stamp:
                    if not source_dates:
                        source_dates = [stamp, stamp]
                    else:
                        source_dates = [
                            min(source_dates[0], stamp),
                            max(source_dates[1], stamp),
                        ]
                try:
                    event = normalize_call(
                        row, source_file=path.name, row_number=number
                    )
                except ValueError:
                    counts["invalid_rows"] += 1
                    continue
                if (
                    str(row.get("lead_call_summary") or "").strip().casefold()
                    in EMPTY
                ):
                    counts["missing_summaries"] += 1
                if event is None:
                    continue
                if not event["summary_available"]:
                    counts["outcome_only_observations"] += 1
                for cue in event["insights"]:
                    cue_counts[cue["trait"]] += 1
                    cue_outcomes[(cue["trait"], label)] += 1
                pending.append(event)
                if len(pending) >= batch_size:
                    counts.update(service.ingest(pending))
                    pending.clear()
        if limit and counts["source_rows"] >= limit:
            break
    if pending:
        counts.update(service.ingest(pending))
    result = {
        "status": "complete",
        "schema_version": "call-insights-v3",
        "counts": dict(counts),
        "index_status": "pending_outbox_delivery",
        "method": "deterministic_private_summary_projection",
        "associations": {
            "source_calls": counts["source_rows"],
            "populated_summary_calls": counts["source_rows"]
            - counts["missing_summaries"]
            - counts["invalid_rows"],
            "outcome_counts": dict(outcomes),
            "lexical_cue_counts": dict(cue_counts),
            "cue_outcome_counts": [
                {
                    "cue": cue,
                    "source_outcome": label,
                    "calls": number,
                    "cue_denominator": cue_counts[cue],
                }
                for (cue, label), number in sorted(cue_outcomes.items())
            ],
            "source_time_window_IST": source_dates,
            "sampling": "answered bot calls Apr-Sep 2026; recording sample excluded",
            "causal_uplift": False,
            "human_validated": False,
        },
        "paid_calls": 0,
        "source_selection": "bot calls only; recording sample duplicates excluded",
        "limitations": [
            "Historical machine summaries are not current intent or reviewed seller quotations.",
            "Meeting Fixed/meeting_fixed disagreements are retained; no completed sales inferred.",
            "Keyword cues are weak, can be negated, and require context review.",
        ],
    }
    service.store.put("metadata", "call-summary-import", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    print(json.dumps(import_summaries(args.dataset, args.db, args.limit)))
