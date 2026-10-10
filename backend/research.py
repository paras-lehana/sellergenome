"""Lossless private CSV indexing and reproducible aggregate research.

Raw rows never become API responses. Typed projections enable fixed aggregate
queries, while original strings, duplicates and source lineage remain in _source.
"""

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from backend.insights import (
    EMPTY,
    PATTERNS,
    actor_for_span,
    boolean,
    source_time,
)

FAMILIES = (
    "seller_files",
    "bot_calls",
    "evaluator_outputs",
    "call_turns",
    "recording_sample",
    "executive_calls",
)
RAW_PREFIX = "sellergenome-raw"
PARSER_VERSION = "raw-projection-2"
REPORT_VERSION = "atlas-1"
OUTCOMES = [
    "Meeting Fixed",
    "Not Interested",
    "Call Later / Busy",
    "General (talked)",
]
NUMERICS = {
    "num_categories",
    "bot_attempts",
    "bot_answered",
    "bot_answer_rate",
    "avg_answered_call_sec",
    "calls_meeting_fixed",
    "calls_not_interested",
    "calls_general",
    "calls_call_later",
    "lead_call_duration",
    "call_duration",
    "call_duration_customer",
    "ring_duration",
    "start_sec",
    "end_sec",
    "turn_no",
    "call_attempt_count",
    "annual_turnover",
    "gst_registration_year",
}
FLAGS = [
    "do_not_call_requested",
    "showed_frustration",
    "asked_if_talking_to_bot",
    "already_in_touch_with_executive",
]


def family_name(path: Path) -> str:
    """Reject unknown inputs instead of assigning them to an arbitrary index."""
    for family in FAMILIES:
        if family in path.name:
            return family
    raise ValueError("Unknown CSV family")


def file_hash(path: Path) -> str:
    """Hash the original bytes, independent of CSV/newline interpretation."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def number(value):
    """Missing and nonfinite values remain unknown rather than becoming zero."""
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def raw_alias(path: Path) -> str:
    """Keep one stable, reviewed namespace per source file."""
    return (
        RAW_PREFIX
        + "-"
        + re.sub(r"[^a-z0-9-]", "-", path.stem.lower()).strip("-")
    )


def raw_document(row: dict, path: Path, row_number: int, sha256: str) -> dict:
    """Project explicit fields without changing raw strings or original dates."""
    family = family_name(path)
    identity = f"{sha256}:{row_number}"
    text_fields = [
        "lead_call_summary",
        "past_objections",
        "past_questions_asked",
        "text",
        "agent_output",
    ]
    metrics = {
        key: number(value)
        for key, value in row.items()
        if key in NUMERICS or key.startswith("eng_")
    }
    metrics = {
        key: value for key, value in metrics.items() if value is not None
    }
    text = "\n".join(str(row.get(key) or "") for key in text_fields).strip()
    summary = str(row.get("lead_call_summary") or "").strip()
    has_summary = summary.casefold() not in EMPTY
    start, end = metrics.get("start_sec"), metrics.get("end_sec")
    flag = boolean(row.get("meeting_fixed"))
    result = {
        "id": hashlib.sha256(identity.encode()).hexdigest(),
        "family": family,
        "source_file": path.name,
        "source_sha256": sha256,
        "source_row": row_number,
        "schema_version": PARSER_VERSION,
        "seller_id": str(row.get("fk_glusr_usr_id") or ""),
        "call_id": str(
            row.get("attempt_id") or row.get("click_to_call_id") or ""
        ),
        "source_key": str(
            row.get("attempt_id")
            or row.get("click_to_call_id")
            or row.get("fk_glusr_usr_id")
            or ""
        ),
        "observed_at": source_time(row.get("call_start_time")),
        "month": str(row.get("call_start_time") or "")[:7] or None,
        "bot_version": row.get("lead_bot_version"),
        "outcome": row.get("disposition_label"),
        "meeting_fixed": flag,
        "meeting_conflict": row.get("disposition_label") == "Meeting Fixed"
        and flag is False,
        "summary_available": has_summary
        if "lead_call_summary" in row
        else None,
        "summary_length": len(summary) if has_summary else None,
        "duration_seconds": metrics.get(
            "lead_call_duration", metrics.get("call_duration")
        ),
        "speaker": row.get("speaker"),
        "eval_agent": row.get("eval_agent"),
        "status": row.get("status"),
        "metrics": metrics,
        "flags": {key: boolean(row.get(key)) for key in FLAGS if key in row},
        "timing_invalid": family == "call_turns"
        and (start is None or end is None or start < 0 or end < start),
        "search_text": text,
        "raw": row,
    }
    if "agent_output" in row:
        try:
            parsed = json.loads(row["agent_output"])
            result["evaluator_json_valid"] = True
            result["evaluator_keys"] = (
                sorted(parsed) if isinstance(parsed, dict) else []
            )
        except (TypeError, ValueError):
            result["evaluator_json_valid"] = False
            result["evaluator_keys"] = []
    return result


def raw_mapping(columns: list[str]) -> dict:
    """Control all aggregate types; retain original columns without dynamic growth."""
    keywords = [
        "id",
        "family",
        "source_file",
        "source_sha256",
        "schema_version",
        "seller_id",
        "call_id",
        "source_key",
        "month",
        "bot_version",
        "outcome",
        "speaker",
        "eval_agent",
        "status",
        "evaluator_keys",
    ]
    booleans = [
        "meeting_fixed",
        "meeting_conflict",
        "summary_available",
        "timing_invalid",
        "evaluator_json_valid",
    ]
    properties = {key: {"type": "keyword"} for key in keywords}
    properties.update({key: {"type": "boolean"} for key in booleans})
    properties.update(
        {
            "source_row": {"type": "integer"},
            "summary_length": {"type": "integer"},
            "duration_seconds": {"type": "double"},
            "observed_at": {"type": "date"},
            "search_text": {"type": "text"},
            # Original strings remain lossless in _source and searchable as keywords.
            # Long summaries/evaluator JSON also have the separate full-text projection.
            "raw": {"type": "flattened", "ignore_above": 4096},
            "metrics": {
                "dynamic": False,
                "properties": {
                    key: {"type": "double"}
                    for key in columns
                    if key in NUMERICS or key.startswith("eng_")
                },
            },
            "flags": {
                "dynamic": False,
                "properties": {key: {"type": "boolean"} for key in FLAGS},
            },
        }
    )
    return {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"dynamic": "strict", "properties": properties},
    }


def counter_rows(counter: Counter, label="label") -> list[dict]:
    """Stable aggregate rows; labels must come from approved categorical fields."""
    return [
        {label: key, "count": value} for key, value in sorted(counter.items())
    ]


def quantile(sorted_values: list, fraction: float):
    """Nearest-rank quantile with its exact definition, never an ES approximation."""
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(len(sorted_values) * fraction) - 1)]


def build_report(dataset: Path, catalog: dict) -> dict:
    """Verify every source hash and calculate new summary/time/version analysis.

    Publication uses only safe aggregates. Identifier/hash maps support exact
    duplicate and membership checks in memory, then are discarded.
    """
    by_name = {path.name: path for path in dataset.rglob("*.csv")}
    files, counts = [], Counter()
    month_outcomes, version_outcomes = (
        defaultdict(Counter),
        defaultdict(Counter),
    )
    version_months, duration_buckets = defaultdict(Counter), Counter()
    summary_lengths, durations = [], []
    cue_outcomes, cue_actors = defaultdict(Counter), defaultdict(Counter)
    conflicts, missing = Counter(), Counter()
    bot_hashes, recording_checks = {}, []
    seller_totals = Counter()
    duplicate_details = {
        family: {
            "extra_rows": 0,
            "exact_extras": 0,
            "distinct_content_extras": 0,
            "conflicting_keys": 0,
        }
        for family in ("evaluator_outputs", "call_turns")
    }
    seen, conflicted = defaultdict(dict), defaultdict(set)
    for source in catalog["files"]:
        path = by_name.get(source["name"])
        if path is None or file_hash(path) != source["sha256"]:
            raise ValueError(
                "Dataset differs from the audited catalog; re-audit before publishing."
            )
        family, row_count = source["family"], 0
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row_count += 1
                counts[family] += 1
                if family == "seller_files":
                    seller_totals["attempts"] += (
                        number(row.get("bot_attempts")) or 0
                    )
                    seller_totals["answered"] += (
                        number(row.get("bot_answered")) or 0
                    )
                elif family == "bot_calls":
                    outcome = row.get("disposition_label") or "unknown"
                    version = row.get("lead_bot_version") or "unknown"
                    month = (
                        str(row.get("call_start_time") or "")[:7] or "unknown"
                    )
                    month_outcomes[month][outcome] += 1
                    version_outcomes[version][outcome] += 1
                    version_months[version][month] += 1
                    duration = number(row.get("lead_call_duration"))
                    if duration is not None and duration >= 0:
                        durations.append(duration)
                        bucket = (
                            "Under 30s"
                            if duration < 30
                            else "30–89s"
                            if duration < 90
                            else "90–179s"
                            if duration < 180
                            else "180s+"
                        )
                        duration_buckets[bucket] += 1
                    text = str(row.get("lead_call_summary") or "").strip()
                    if text.casefold() not in EMPTY:
                        summary_lengths.append(len(text))
                        for trait, pattern in PATTERNS.items():
                            match = re.search(pattern, text, re.I)
                            if match:
                                cue_outcomes[trait][outcome] += 1
                                cue_actors[trait][
                                    actor_for_span(text, *match.span())
                                ] += 1
                    else:
                        missing["summaries"] += 1
                    if (
                        row.get("disposition_label") == "Meeting Fixed"
                        and boolean(row.get("meeting_fixed")) is False
                    ):
                        conflicts[version] += 1
                        if text.casefold() in EMPTY:
                            missing["conflict_summaries"] += 1
                    bot_hashes[row["attempt_id"]] = hashlib.sha256(
                        json.dumps(row, sort_keys=True).encode()
                    ).digest()
                elif family == "recording_sample":
                    recording_checks.append(
                        (
                            row["attempt_id"],
                            hashlib.sha256(
                                json.dumps(row, sort_keys=True).encode()
                            ).digest(),
                        )
                    )
                elif family in duplicate_details:
                    key = (
                        row.get("attempt_id"),
                        row.get("eval_agent")
                        if family == "evaluator_outputs"
                        else row.get("turn_no"),
                    )
                    content = hashlib.sha256(
                        json.dumps(row, sort_keys=True).encode()
                    ).digest()
                    variants = seen[family].setdefault(key, set())
                    if variants:
                        duplicate_details[family]["extra_rows"] += 1
                        duplicate_details[family][
                            "exact_extras"
                            if content in variants
                            else "distinct_content_extras"
                        ] += 1
                        if content not in variants:
                            conflicted[family].add(key)
                    variants.add(content)
        if row_count != source["rows"]:
            raise ValueError("Source count differs from audited metadata")
        files.append(
            {
                key: source[key]
                for key in (
                    "name",
                    "family",
                    "rows",
                    "column_count",
                    "sha256",
                    "columns",
                    "null_counts",
                )
            }
        )
    summary_lengths.sort()
    durations.sort()
    for family in duplicate_details:
        duplicate_details[family]["conflicting_keys"] = len(conflicted[family])
    known_flags = {
        name: counts["seller_files"]
        - catalog["null_counts"]["seller_files"].get(name, 0)
        for name in FLAGS
    }
    report = {
        "version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Provided historical CSV snapshots; no live upstream requests or paid model extraction.",
        "privacy": "Aggregate-only. Source identifiers, free text and URLs are excluded.",
        "method": "Full scan, verified SHA256 inputs; nearest-rank quantiles; lexical mentions are weak, not negation-validated.",
        "file_count": len(files),
        "total_rows": sum(counts.values()),
        "source_bytes": catalog["total_bytes"],
        "families": counter_rows(counts, "family"),
        "sources": files,
        "call_window": catalog["time_ranges"]["bot_calls"],
        "outcomes": counter_rows(
            Counter(catalog["enum_counts"]["bot_calls.disposition_label"])
        ),
        "monthly": [
            {
                "month": key,
                "calls": sum(value.values()),
                "outcomes": dict(value),
            }
            for key, value in sorted(month_outcomes.items())
        ],
        "versions": [
            {
                "version": key,
                "calls": sum(value.values()),
                "outcomes": dict(value),
                "months": dict(version_months[key]),
            }
            for key, value in sorted(
                version_outcomes.items(),
                key=lambda item: -sum(item[1].values()),
            )
        ],
        "summary": {
            "populated": len(summary_lengths),
            "missing": missing["summaries"],
            "p50_characters": quantile(summary_lengths, 0.5),
            "p95_characters": quantile(summary_lengths, 0.95),
            "maximum_characters": max(summary_lengths, default=0),
        },
        "duration": {
            "valid_calls": len(durations),
            "median_seconds": quantile(durations, 0.5),
            "p95_seconds": quantile(durations, 0.95),
            "buckets": counter_rows(duration_buckets),
        },
        "meeting_conflicts": {
            "count": sum(conflicts.values()),
            "by_version": dict(conflicts),
            "without_summary": missing["conflict_summaries"],
        },
        "cues": [
            {
                "trait": trait,
                "mentions": sum(value.values()),
                "outcomes": dict(value),
                "actors": dict(cue_actors[trait]),
                "pattern": PATTERNS[trait],
            }
            for trait, value in sorted(cue_outcomes.items())
        ],
        "duplicates": duplicate_details,
        "recording_exact_subset": sum(
            bot_hashes.get(key) == content for key, content in recording_checks
        ),
        "relationships": catalog["relationships"],
        "unique_counts": catalog["unique_counts"],
        "null_counts": catalog["null_counts"],
        "seller_flags": [
            {
                "field": name,
                "positive": catalog["seller_flags"].get(name, 0),
                "known": known_flags[name],
                "unknown": counts["seller_files"] - known_flags[name],
            }
            for name in FLAGS
        ],
        "recordings": {
            "outcomes": catalog["enum_counts"][
                "recording_sample.disposition_label"
            ],
            "versions": catalog["enum_counts"][
                "recording_sample.lead_bot_version"
            ],
            "months": catalog["month_counts"]["recording_sample"],
            "durations": catalog["recording_duration_buckets"],
            "cohorts": catalog["recording_cohorts"],
        },
        "historical_cohorts": catalog["historical_cohort_outcomes"],
        "seller_cohorts": catalog["seller_cohorts"],
        "quality": {
            **catalog["quality"],
            "unknown_turn_speakers": catalog["enum_counts"][
                "call_turns.speaker"
            ].get("unknown", 0),
        },
        "numeric_summaries": catalog["numeric_summaries"],
        "seller_call_totals": {
            "attempts": int(seller_totals["attempts"]),
            "answered": int(seller_totals["answered"]),
            "weighted_answer_rate": seller_totals["answered"]
            / seller_totals["attempts"]
            if seller_totals["attempts"]
            else None,
            "denominator": "Sum of bot_attempts across seller snapshots; not a rate computed from answered-call CSV alone.",
        },
        "evaluator_counts": catalog["enum_counts"][
            "evaluator_outputs.eval_agent"
        ],
        "source_definitions": {
            "outcomes": {
                "files": [
                    f["name"] for f in files if f["family"] == "bot_calls"
                ],
                "columns": [
                    "disposition_label",
                    "call_start_time",
                    "lead_bot_version",
                ],
                "denominator": counts["bot_calls"],
                "limitation": "Answered-call selection; model/time/selection differences prevent causal ranking.",
            },
            "summaries": {
                "files": [
                    f["name"] for f in files if f["family"] == "bot_calls"
                ],
                "columns": [
                    "lead_call_summary",
                    "meeting_fixed",
                    "disposition_label",
                ],
                "denominator": counts["bot_calls"],
                "limitation": "Machine summaries, mixed actors and unverified lexical cues; not verbatim seller intent.",
            },
            "seller_fields": {
                "files": [
                    f["name"] for f in files if f["family"] == "seller_files"
                ],
                "columns": FLAGS
                + [
                    "eng_activity_30d",
                    "past_objections",
                    "past_questions_asked",
                ],
                "denominator": counts["seller_files"],
                "limitation": "April–September aggregates leak later behavior into an earlier-call evaluation. Null is unknown, not false.",
            },
            "recordings": {
                "files": [
                    f["name"]
                    for f in files
                    if f["family"] == "recording_sample"
                ],
                "columns": [
                    "attempt_id",
                    "lead_bot_version",
                    "disposition_label",
                ],
                "denominator": counts["recording_sample"],
                "limitation": "Exact subset of answered calls; selected sample, not an unbiased population estimate.",
            },
            "duplicates": {
                "files": [
                    f["name"]
                    for f in files
                    if f["family"] in ("call_turns", "evaluator_outputs")
                ],
                "columns": ["attempt_id", "turn_no", "eval_agent"],
                "denominator": counts["call_turns"]
                + counts["evaluator_outputs"],
                "limitation": "Same business key with different content is a conflict, not an independent observation.",
            },
        },
    }
    return report
