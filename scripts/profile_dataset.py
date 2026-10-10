#!/usr/bin/env python3
"""Stream every supplied CSV into aggregate metadata without exporting PII.

Usage: python3 scripts/profile_dataset.py --dataset dataset --output data/catalog.json
Only bounded enumerated labels, schemas, counts and numeric summaries leave the
process. Seller/call identifiers are held in memory solely for join validation.
No network calls, raw text samples, recording URLs or credentials are emitted.
"""

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FAMILIES = (
    "seller_files",
    "bot_calls",
    "evaluator_outputs",
    "call_turns",
    "recording_sample",
    "executive_calls",
)
SAFE_ENUMS = {
    "disposition_label",
    "lead_bot_version",
    "eval_agent",
    "speaker",
    "status",
    "module",
    "has_turn_transcript",
    "has_evaluator_output",
}
FLAGS = (
    "do_not_call_requested",
    "showed_frustration",
    "asked_if_talking_to_bot",
    "already_in_touch_with_executive",
)
NUMERICS = {
    "num_categories",
    "bot_attempts",
    "bot_answered",
    "bot_answer_rate",
    "avg_answered_call_sec",
    "call_attempt_count",
    "lead_call_duration",
    "call_duration",
    "call_duration_customer",
    "ring_duration",
    "start_sec",
    "end_sec",
    "turn_no",
    "calls_meeting_fixed",
    "calls_not_interested",
    "calls_general",
    "calls_call_later",
}
EMPTY = {"", "null", "none", "nan", "info_na"}


def family_name(path: Path) -> str:
    """Classify split files without assuming their directory labels are correct."""
    return next(name for name in FAMILIES if name in path.name)


def numeric_summary(values: dict[str, float]) -> dict[str, float]:
    """Produce numeric summaries, excluding non-finite values from JSON."""
    return {
        "count": int(values["count"]),
        "min": values["min"],
        "max": values["max"],
        "mean": round(values["sum"] / values["count"], 4),
    }


def duration_bucket(value: str) -> str:
    """Use half-open intervals so every valid duration has exactly one cohort."""
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        return "unknown"
    if not math.isfinite(seconds) or seconds < 0:
        return "invalid"
    if seconds < 30:
        return "under_30s"
    if seconds < 90:
        return "30_to_89s"
    if seconds < 180:
        return "90_to_179s"
    return "180s_plus"


def profile(dataset: Path) -> dict[str, Any]:
    """Read each row once, retaining identifiers only for exact join checks."""
    files = []
    ids: dict[str, set[str]] = defaultdict(set)
    primary_keys: dict[str, set[Any]] = defaultdict(set)
    totals: Counter = Counter()
    nulls: dict[str, Counter] = defaultdict(Counter)
    enums: dict[str, Counter] = defaultdict(Counter)
    numeric: dict[str, dict[str, float]] = {}
    ranges: dict[str, list[str]] = {}
    months: dict[str, Counter] = defaultdict(Counter)
    duplicates: Counter = Counter()
    quality: Counter = Counter()
    flags: Counter = Counter()
    eval_keys: dict[str, Counter] = defaultdict(Counter)
    eval_nested: dict[str, Counter] = defaultdict(Counter)
    seller_cohort: dict[str, str] = {}
    recording_cohorts: Counter = Counter()
    recording_durations: Counter = Counter()
    cohort_outcomes: dict[str, Counter] = defaultdict(Counter)
    schemas: dict[str, list[str]] = {}
    # Sellers first allows cohort joins without retaining transcript text.
    paths = sorted(
        dataset.rglob("*.csv"),
        key=lambda p: (FAMILIES.index(family_name(p)), str(p)),
    )
    for path in paths:
        family = family_name(path)
        digest = hashlib.sha256()
        with path.open("rb") as raw:
            for block in iter(lambda: raw.read(1024 * 1024), b""):
                digest.update(block)
        rows = 0
        file_nulls: Counter = Counter()
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            if family in schemas and schemas[family] != columns:
                quality["split_schema_mismatch"] += 1
            schemas[family] = columns
            for row in reader:
                rows += 1
                seller = row.get("fk_glusr_usr_id", "")
                attempt = row.get("attempt_id", "")
                ids[family + ":sellers"].add(seller)
                if attempt:
                    ids[family + ":calls"].add(attempt)
                key: Any = attempt
                if family == "seller_files":
                    key = seller
                elif family == "executive_calls":
                    key = row["click_to_call_id"]
                elif family == "evaluator_outputs":
                    key = (attempt, row["eval_agent"])
                elif family == "call_turns":
                    key = (attempt, row["turn_no"])
                if key in primary_keys[family]:
                    duplicates[family] += 1
                primary_keys[family].add(key)
                for column, value in row.items():
                    if column is None:
                        quality["rows_with_extra_columns"] += 1
                        continue
                    if value is None or value.strip().lower() in EMPTY:
                        file_nulls[column] += 1
                        nulls[family][column] += 1
                        continue
                    if column in SAFE_ENUMS:
                        enums[family + "." + column][value] += 1
                    if column in NUMERICS or column.startswith("eng_"):
                        try:
                            number = float(value)
                            if not math.isfinite(number):
                                raise ValueError("non-finite")
                        except ValueError:
                            quality[family + ":invalid_numeric:" + column] += 1
                            continue
                        num_key = family + "." + column
                        stat = numeric.setdefault(
                            num_key,
                            {
                                "count": 0,
                                "sum": 0,
                                "min": number,
                                "max": number,
                            },
                        )
                        stat["count"] += 1
                        stat["sum"] += number
                        stat["min"] = min(stat["min"], number)
                        stat["max"] = max(stat["max"], number)
                timestamp = row.get("call_start_time")
                if timestamp:
                    previous = ranges.setdefault(
                        family, [timestamp, timestamp]
                    )
                    previous[0] = min(previous[0], timestamp)
                    previous[1] = max(previous[1], timestamp)
                    months[family][timestamp[:7]] += 1
                if family == "seller_files":
                    for flag in FLAGS:
                        if row[flag] in {"1", "1.0", "true", "True"}:
                            flags[flag] += 1
                    if row["do_not_call_requested"] in {"1", "1.0"}:
                        cohort = "contact_suppressed"
                    elif row["showed_frustration"] in {"1", "1.0"}:
                        cohort = "prior_frustration"
                    elif "pric" in row["past_objections"].lower():
                        cohort = "price_objection"
                    elif row["already_in_touch_with_executive"] in {
                        "1",
                        "1.0",
                    }:
                        cohort = "existing_executive"
                    elif "schedul" in row["past_objections"].lower():
                        cohort = "scheduling_objection"
                    else:
                        cohort = "no_specific_signal"
                    seller_cohort[seller] = cohort
                elif family == "evaluator_outputs":
                    try:
                        value = json.loads(row["agent_output"])
                        quality["evaluator_valid_json"] += 1
                        if not isinstance(value, dict):
                            quality["evaluator_non_object_json"] += 1
                        else:
                            agent = row["eval_agent"]
                            for field, child in value.items():
                                eval_keys[agent][field] += 1
                                if isinstance(child, dict):
                                    for child_key in child:
                                        eval_nested[agent][
                                            field + "." + child_key
                                        ] += 1
                    except (ValueError, TypeError):
                        quality["evaluator_invalid_json"] += 1
                elif family == "call_turns":
                    try:
                        start, end = (
                            float(row["start_sec"]),
                            float(row["end_sec"]),
                        )
                        if start < 0 or end < start:
                            quality["invalid_turn_time_order"] += 1
                    except ValueError:
                        quality["invalid_turn_timestamp"] += 1
                if family in {"bot_calls", "recording_sample"}:
                    cohort = seller_cohort.get(seller, "unknown_seller")
                    if family == "recording_sample":
                        recording_cohorts[cohort] += 1
                        recording_durations[
                            duration_bucket(row["lead_call_duration"])
                        ] += 1
                    else:
                        cohort_outcomes[cohort][row["disposition_label"]] += 1
        totals[family] += rows
        files.append(
            {
                "name": path.name,
                "path": str(path.relative_to(dataset)),
                "family": family,
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": digest.hexdigest(),
                "columns": columns,
                "column_count": len(columns),
                "null_counts": dict(file_nulls),
            }
        )
    relationships = {
        "seller_orphans": {
            family: len(ids[family + ":sellers"] - ids["seller_files:sellers"])
            for family in FAMILIES
        },
        "attempt_ids_absent_from_answered_bot_calls": {
            family: len(ids[family + ":calls"] - ids["bot_calls:calls"])
            for family in (
                "evaluator_outputs",
                "call_turns",
                "recording_sample",
            )
        },
        "recording_calls_with_actual_turns": len(
            ids["recording_sample:calls"] & ids["call_turns:calls"]
        ),
        "recording_calls_with_actual_evaluations": len(
            ids["recording_sample:calls"] & ids["evaluator_outputs:calls"]
        ),
    }
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "privacy": "Aggregate-only; no IDs, free text, URLs or credentials.",
        "method": "Full streaming scan; no sampling; numeric null != zero.",
        "source_timezone": "Asia/Kolkata (per supplied dictionary)",
        "file_count": len(files),
        "total_rows": sum(totals.values()),
        "total_bytes": sum(item["bytes"] for item in files),
        "rows_by_family": dict(totals),
        "files": files,
        "unique_counts": {key: len(value) for key, value in ids.items()},
        "duplicate_primary_keys": {f: duplicates[f] for f in FAMILIES},
        "relationships": relationships,
        "null_counts": dict(nulls),
        "enum_counts": dict(enums),
        "numeric_summaries": {
            k: numeric_summary(v) for k, v in numeric.items()
        },
        "time_ranges": ranges,
        "month_counts": dict(months),
        "quality": dict(quality),
        "seller_flags": dict(flags),
        "evaluator_keys": dict(eval_keys),
        "evaluator_nested_keys": dict(eval_nested),
        "recording_cohorts": dict(recording_cohorts),
        "recording_duration_buckets": dict(recording_durations),
        "seller_cohorts": dict(Counter(seller_cohort.values())),
        "historical_cohort_outcomes": dict(cohort_outcomes),
        "caveats": [
            "Recordings are balanced samples, not population estimates.",
            "Evaluator labels and transcripts are machine-generated, not gold truth.",
            "Whole-window seller flags leak future information in retrospective tests.",
            "Dictionary executive date coverage differs from observed file coverage.",
            "Public URLs do not grant permission to publish recordings or identities.",
            "Confidence scores are source/self-report scores, not calibrated probabilities.",
        ],
    }


def main() -> None:
    """Write aggregate catalog atomically so the portal never reads partial JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument(
        "--output", type=Path, default=Path("data/catalog.json")
    )
    args = parser.parse_args()
    result = profile(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "files": result["file_count"],
                "rows": result["total_rows"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
