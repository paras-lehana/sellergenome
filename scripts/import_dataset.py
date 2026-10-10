#!/usr/bin/env python3
"""Import private, structured seller evidence without copying raw conversation PII.

All input families are scanned. Free-text transcripts, summaries, evaluator quotes,
contact information and recording URLs remain in the original private dataset.
The normalized database contains aliases and controlled factual summaries only.
"""

import argparse
import csv
import hashlib
import heapq
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

SNAPSHOT_AT = "2026-09-30T18:29:59+00:00"
KNOWN_OUTCOMES = {
    "Meeting Fixed",
    "Not Interested",
    "Call Later / Busy",
    "General (talked)",
}
KNOWN_EVALUATORS = {
    "disposition",
    "seller_questions",
    "incorrect_mf",
    "execdrop",
    "se_contact",
    "location_confirmation",
    "latency_repetition",
    "topics",
    "objections",
    "ivr",
}


def digest(text: str) -> str:
    """Stable hashes give idempotent document identities without exporting GLIDs."""
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def number(value: Any) -> float | None:
    """Preserve absent numeric values as unknown, never as fabricated zero."""
    try:
        result = float(value)
        return (
            result
            if result == result and abs(result) != float("inf")
            else None
        )
    except (TypeError, ValueError):
        return None


def flag(value: Any) -> bool | None:
    """The supplied CSV boolean encoding is 1.0 / 0.0 / empty."""
    parsed = number(value)
    return bool(parsed) if parsed in (0.0, 1.0) else None


def timestamp(value: str) -> str | None:
    """Convert naive source IST timestamps to timezone-aware UTC."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def rows(dataset: Path, pattern: str) -> Iterator[dict[str, str]]:
    """Stream each CSV part in stable filename order."""
    for path in sorted(dataset.rglob(pattern)):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)


def cohort(row: dict[str, str]) -> str:
    """Use explicit observed signals, never demographics, to group sellers."""
    if flag(row.get("do_not_call_requested")):
        return "contact-suppressed"
    if flag(row.get("showed_frustration")):
        return "frustrated"
    objections = row.get("past_objections", "").lower()
    if any(
        term in objections for term in ("pric", "cost", "payment", "budget")
    ):
        return "price_sensitive"
    if flag(row.get("already_in_touch_with_executive")):
        return "balanced"
    if any(term in objections for term in ("schedul", "unavail", "busy")):
        return "rushed"
    return "balanced"


def open_database(path: Path) -> sqlite3.Connection:
    """Match the application store contract and leave other tables untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=60)
    database.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS sellers(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,seller_id TEXT,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_seller ON evidence(seller_id);
        CREATE TABLE IF NOT EXISTS recordings(id TEXT PRIMARY KEY,seller_id TEXT,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,payload TEXT);
    """)
    return database


def put(database: sqlite3.Connection, table: str, document: dict) -> None:
    """Store only known entity kinds with bound SQL parameters."""
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if table == "sellers":
        database.execute(
            "INSERT OR REPLACE INTO sellers VALUES (?,?)",
            (document["id"], payload),
        )
    elif table in {"evidence", "recordings"}:
        database.execute(
            f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)",
            (document["id"], document["seller_id"], payload),
        )
    else:
        raise ValueError("Unsupported import table")


def import_dataset(dataset: Path, db_path: Path, limit: int = 0) -> dict:
    """Aggregate every family, then import one bounded evidence bundle per seller."""
    ingested_at = datetime.now(timezone.utc).isoformat()
    counts: Counter = Counter()
    calls: dict[str, Counter] = defaultdict(Counter)
    recent: dict[str, list] = defaultdict(list)
    evaluations: dict[str, Counter] = defaultdict(Counter)
    turn_stats: dict[str, Counter] = defaultdict(Counter)
    executive: dict[str, Counter] = defaultdict(Counter)
    for row in rows(dataset, "*bot_calls*.csv"):
        counts["source_bot_calls"] += 1
        seller = row["fk_glusr_usr_id"]
        outcome = row["disposition_label"]
        if outcome not in KNOWN_OUTCOMES:
            outcome = "Unknown"
        calls[seller][outcome] += 1
        item = (
            row["call_start_time"],
            digest(row["attempt_id"]),
            outcome,
            number(row["lead_call_duration"]),
            bool(row["call_recording_url"]),
        )
        heapq.heappush(recent[seller], item)
        if len(recent[seller]) > 3:
            heapq.heappop(recent[seller])
    for row in rows(dataset, "*evaluator_outputs*.csv"):
        counts["source_evaluator_outputs"] += 1
        agent = row["eval_agent"]
        evaluations[row["fk_glusr_usr_id"]][
            agent if agent in KNOWN_EVALUATORS else "unknown"
        ] += 1
    for row in rows(dataset, "*call_turns*.csv"):
        counts["source_call_turns"] += 1
        speaker = row["speaker"]
        turn_stats[row["fk_glusr_usr_id"]][
            speaker if speaker in {"bot", "seller"} else "unknown"
        ] += 1
    for row in rows(dataset, "*executive_calls*.csv"):
        counts["source_executive_calls"] += 1
        executive[row["fk_glusr_usr_id"]][
            row["status"]
            if row["status"] in {"Answered", "NotAnswered"}
            else "Unknown"
        ] += 1

    database = open_database(db_path)
    imported: set[str] = set()
    persona_counts: Counter = Counter()
    seller_tags: dict[str, str] = {}
    try:
        for row in rows(dataset, "*seller_files*.csv"):
            if limit and counts["sellers"] >= limit:
                break
            glid = row["fk_glusr_usr_id"]
            seller_id = "real-" + glid
            imported.add(glid)
            alias = digest(glid)[:10].upper()
            tag = cohort(row)
            seller_tags[glid] = tag
            persona_counts[tag] += 1
            metrics = {
                name: number(row.get(name))
                for name in (
                    "bot_attempts",
                    "bot_answered",
                    "bot_answer_rate",
                    "avg_answered_call_sec",
                    "calls_meeting_fixed",
                    "calls_not_interested",
                    "calls_general",
                    "calls_call_later",
                    "eng_activity_30d",
                    "eng_pickup_ratio_90d",
                    "eng_cqs",
                    "eng_enq_received_90d",
                    "eng_enq_replies_90d",
                )
            }
            metrics.update(
                {
                    "call_outcomes": dict(calls[glid]),
                    "evaluation_counts": dict(evaluations[glid]),
                    "turn_counts": dict(turn_stats[glid]),
                    "executive_call_counts": dict(executive[glid]),
                    "contact_suppressed": flag(
                        row.get("do_not_call_requested")
                    ),
                    "do_not_call": flag(row.get("do_not_call_requested")),
                    "prior_frustration": flag(row.get("showed_frustration")),
                    "already_in_touch_with_executive": flag(
                        row.get("already_in_touch_with_executive")
                    ),
                    "asked_about_bot": flag(
                        row.get("asked_if_talking_to_bot")
                    ),
                }
            )
            evidence = []

            def add(
                kind: str,
                text: str,
                observed: str = SNAPSHOT_AT,
                source_ref: str = "seller_snapshot",
                suffix: str = "",
            ) -> None:
                """All evidence wording is controlled; source free text never passes through."""
                evidence.append(
                    {
                        "id": "import-" + digest(seller_id + kind + suffix),
                        "seller_id": seller_id,
                        "kind": kind,
                        "text": text,
                        "observed_at": observed,
                        "ingested_at": ingested_at,
                        "ttl_hours": 2160,
                        "confidence": 0.7,
                        "confidence_kind": "source_observation_not_calibrated",
                        "source_ref": source_ref,
                        "source_mode": "private",
                        "timestamp_inferred": observed == SNAPSHOT_AT,
                        "method": "csv_structured_projection_v1",
                    }
                )

            add(
                "history",
                f"Historical answered bot calls: {sum(calls[glid].values())}. "
                f"Meeting Fixed labels: {calls[glid]['Meeting Fixed']}; "
                f"Not Interested labels: {calls[glid]['Not Interested']}; "
                f"Call Later / Busy labels: {calls[glid]['Call Later / Busy']}. "
                "These are historical labels, not confirmed sales.",
            )
            positive = [
                label
                for name, label in (
                    ("contact_suppressed", "do-not-call requested"),
                    ("prior_frustration", "past frustration flag"),
                    (
                        "already_in_touch_with_executive",
                        "existing executive contact",
                    ),
                    ("asked_about_bot", "asked about bot identity"),
                )
                if metrics[name] is True
            ]
            add(
                "preference",
                "Recorded behavior flags: "
                + (
                    ", ".join(positive)
                    if positive
                    else "no positive flag observed"
                )
                + ". Missing flags remain unknown; these are historical observations.",
            )
            if evaluations[glid]:
                add(
                    "evaluation",
                    "Historical machine evaluations: "
                    + ", ".join(
                        f"{key}: {value}"
                        for key, value in sorted(evaluations[glid].items())
                    )
                    + ". These evaluator outputs are not human-validated labels.",
                )
            for call_time, call_hash, outcome, seconds, available in sorted(
                recent[glid], reverse=True
            ):
                add(
                    "call",
                    f"Past call outcome: {outcome}; duration: {seconds} seconds. "
                    f"Recording availability: {'present' if available else 'absent'}. "
                    "Transcript text stays in the private source dataset.",
                    timestamp(call_time) or SNAPSHOT_AT,
                    "bot_call:" + call_hash,
                    call_hash,
                )
            seller = {
                "id": seller_id,
                "name": "Seller " + alias,
                "company": "Business " + alias,
                "city": row.get("seller_city") or "Unknown",
                "category": row.get("top_category_1") or "Unspecified",
                "language": "hi-IN",
                "language_status": "unverified_default",
                "segment": "Unknown",
                "persona_tag": tag,
                "persona_tag_status": "historical_cohort_not_current_state",
                "source_mode": "private",
                "updated_at": SNAPSHOT_AT,
                "ingested_at": ingested_at,
                "snapshot_timestamp_inferred": True,
                "metrics": metrics,
                "evidence": evidence,
                "missing_fields": [
                    key for key, value in row.items() if not value
                ],
                "contact_policy": {
                    "do_not_call": metrics["contact_suppressed"]
                },
            }
            put(database, "sellers", seller)
            # Remove only this importer's old projection for this seller.
            database.execute(
                "DELETE FROM evidence WHERE seller_id=? AND id LIKE 'import-%'",
                (seller_id,),
            )
            for item in evidence:
                put(database, "evidence", item)
            counts["sellers"] += 1
            counts["evidence"] += len(evidence)
            if counts["sellers"] % 1000 == 0:
                database.commit()
        recording_cohorts: Counter = Counter()
        for row in rows(dataset, "*recording_sample*.csv"):
            counts["source_recordings"] += 1
            glid = row["fk_glusr_usr_id"]
            if glid not in imported:
                continue
            seconds = number(row["lead_call_duration"])
            bucket = (
                "unknown"
                if seconds is None
                else (
                    "under_30s"
                    if seconds < 30
                    else "30_to_89s"
                    if seconds < 90
                    else "90_to_179s"
                    if seconds < 180
                    else "180s_plus"
                )
            )
            recording_cohorts[seller_tags[glid]] += 1
            put(
                database,
                "recordings",
                {
                    "id": "recording-" + digest(row["attempt_id"]),
                    "seller_id": "real-" + glid,
                    "seller_alias": "Seller " + digest(glid)[:10].upper(),
                    "source_mode": "private",
                    "source": "provided_recording_sample",
                    "duration": seconds,
                    "duration_seconds": seconds,
                    "duration_bucket": bucket,
                    "language": "unknown",
                    "cohort": seller_tags[glid],
                    "persona": seller_tags[glid],
                    "outcome": row["disposition_label"]
                    if row["disposition_label"] in KNOWN_OUTCOMES
                    else "Unknown",
                    "bot_version": row["lead_bot_version"],
                    "observed_at": timestamp(row["call_start_time"]),
                    "has_transcript": flag(row["has_turn_transcript"]),
                    "transcript_status": "provided_machine_transcript"
                    if flag(row["has_turn_transcript"])
                    else "not_transcribed",
                    "has_evaluation": flag(row["has_evaluator_output"]),
                    "audio_status": "private_source_url_not_exported",
                    "diarization_status": "not_run",
                    "review_status": "not_reviewed",
                },
            )
            counts["recordings"] += 1
        result = {
            "status": "complete",
            "imported_at": ingested_at,
            "counts": dict(counts),
            "persona_counts": dict(persona_counts),
            "recording_cohorts": dict(recording_cohorts),
            "source_mode": "private",
            "schema_version": "1.0.0",
            "coverage": "all_sellers" if not limit else "limited_sellers",
            "limitations": [
                "No raw transcript, summary, evaluator quotes or recording URLs imported.",
                "Language defaults are unverified; source snapshot timestamps are inferred.",
                "Evaluator/turn duplicates preserved as aggregate row counts, not independent labels.",
                "Normalization performs no LLM inference; AI enrichment is a separate action.",
            ],
        }
        database.execute(
            "INSERT OR REPLACE INTO metadata VALUES (?,?)",
            ("dataset_import", json.dumps(result)),
        )
        database.commit()
        return result
    finally:
        database.close()


def main() -> None:
    """Expose reproducible import with optional small local fixture scope."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument(
        "--db", type=Path, default=Path("runtime/persona.sqlite3")
    )
    parser.add_argument("--limit-sellers", type=int, default=0)
    parser.add_argument(
        "--index",
        action="store_true",
        help="Reserved; run the dedicated Elasticsearch indexer after import.",
    )
    args = parser.parse_args()
    if args.limit_sellers < 0:
        parser.error("--limit-sellers must be nonnegative")
    if args.index:
        parser.error(
            "Use the dedicated indexer after import; --index is not implemented."
        )
    print(
        json.dumps(import_dataset(args.dataset, args.db, args.limit_sellers))
    )


if __name__ == "__main__":
    main()
