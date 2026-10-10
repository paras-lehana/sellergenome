"""Offline PM study: verified source joins, safe aggregates and private review.

No API or provider call occurs here. Raw audio is not fetched. The private sidecar
is separate from the publishable JSON; both share a content-derived study ID.
"""

import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from backend.call_diagnostics import (
    VERSION,
    RULES,
    RULE_BY_ID,
    PERSONAS,
    RESPONSES,
    call_id,
    clean_turns,
    evaluate,
)
from backend.insights import PATTERNS, boolean
from backend.research import file_hash

SUMMARY_DEFINITIONS = {
    "callback_requested": (
        "Callback request",
        "The summary mentions busy/unavailable, calling again or rescheduling.",
        "The seller asks to call tomorrow.",
        "A bot offering to call again also matches; no time has necessarily been agreed.",
    ),
    "contact_suppression": (
        "Contact suppression",
        "The text contains a stop-calling or number-removal phrase.",
        "The seller says: please do not call.",
        "A bot quoting a policy can match. Confirm the actor, and honor actual stop requests immediately.",
    ),
    "executive_continuity": (
        "Executive continuity",
        "The text mentions an existing executive or previous contact/meeting.",
        "The seller says they already spoke with an executive.",
        "The phrase alone does not verify which executive, when, or whether a meeting happened.",
    ),
    "explanation_depth": (
        "Explanation depth",
        "The summary includes explain, clarify, confusion or did-not-understand wording.",
        "The seller asks the agent to explain the package.",
        "An agent saying they explained something also matches; this is not a learning-style score.",
    ),
    "language_preference": (
        "Language preference",
        "The narrow legacy rule looks for prefer/speak/switch/continue followed by Hindi, English or Tamil.",
        "The seller asks to continue in Hindi.",
        "Other languages and phrasings are missed. A bot's language proposal is not a seller preference.",
    ),
    "price_concern": (
        "Price concern",
        "The summary contains price/pricing, cost, budget or payment.",
        "The seller asks what the package costs.",
        "Even 'price was not an issue' matches the legacy chart. This is a price mention, not proven resistance.",
    ),
    "unresolved_service_issue": (
        "Service issue",
        "The text contains refund, complaint, unresolved or service issue.",
        "The seller mentions an unresolved complaint.",
        "A resolved complaint or agent reassurance can match; review the full context.",
    ),
}


def summary_help() -> list[dict]:
    """Explain the original seven categories without changing their counts."""
    return [
        {
            "trait": key,
            "label": value[0],
            "meaning": value[1],
            "synthetic_example": value[2],
            "caution": value[3],
            "pattern": PATTERNS[key],
        }
        for key, value in SUMMARY_DEFINITIONS.items()
    ]


def rows(path: Path):
    """Attach exact CSV lineage privately; physical row 1 is the header."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), 2):
            yield {**row, "_file": path.name, "_row": line}


def proportion(count: int, denominator: int) -> dict:
    """Exact numerator/denominator and rounded display rate; zero is unknown."""
    return {
        "count": count,
        "denominator": denominator,
        "percent": round(100 * count / denominator, 2)
        if denominator
        else None,
    }


def interval(count: int, denominator: int) -> list | None:
    """Descriptive Wilson interval for call share, not seller-independent certainty."""
    if not denominator:
        return None
    z, p = 1.96, count / denominator
    scale = 1 + z * z / denominator
    center = (p + z * z / (2 * denominator)) / scale
    delta = (
        z
        * math.sqrt(
            p * (1 - p) / denominator + z * z / (4 * denominator * denominator)
        )
        / scale
    )
    return [round(100 * (center - delta), 1), round(100 * (center + delta), 1)]


class Cohort:
    """Keep distinct callers and overlapping cue denominators explicit."""

    def __init__(self):
        self.calls = 0
        self.sellers = set()
        self.coverage = Counter()
        self.reasons = Counter()
        self.primary = Counter()
        self.responses = defaultdict(Counter)
        self.responding_calls = set()
        self.reason_calls = set()
        self.outcomes = Counter()

    def add(self, row: dict, diagnosis: dict, sample: bool, turns: list):
        """Count each call once, with at most one first episode per need."""
        self.calls += 1
        self.sellers.add(row["fk_glusr_usr_id"])
        self.outcomes[row["disposition_label"]] += 1
        self.coverage["summary_present"] += diagnosis["summary_available"]
        self.coverage["recording_sample_member"] += sample
        self.coverage["has_usable_turns"] += bool(turns)
        self.coverage["seller_and_bot_turns"] += {"seller", "bot"} <= {
            t["speaker"] for t in turns
        }
        self.coverage["summary_only"] += (
            diagnosis["summary_available"] and not turns
        )
        self.coverage["neither_summary_nor_usable_turns"] += (
            not diagnosis["summary_available"] and not turns
        )
        self.reasons.update(diagnosis["reason_ids"])
        self.primary[diagnosis["primary_reason"]] += 1
        for ep in diagnosis["response_episodes"]:
            self.responses[ep["reason"]][ep["response"]] += 1
            self.reason_calls.add(row["attempt_id"])
            if ep["response"] != "not_observed":
                self.responding_calls.add(row["attempt_id"])

    def export(self) -> dict:
        """Only reviewed category names and counts can leave the private builder."""
        reasons = [
            {
                "id": rule["id"],
                "label": rule["label"],
                **proportion(self.reasons[rule["id"]], self.calls),
                "next_step": rule["action"],
            }
            for rule in RULES
        ]
        labels = dict((key, label) for key, label, _ in RESPONSES)
        labels.update(
            {
                "other_response": "Other bot response",
                "not_observed": "No eligible adjacent response",
            }
        )
        response_rows = []
        for reason, counts in sorted(self.responses.items()):
            denominator = sum(counts.values())
            response_rows.append(
                {
                    "reason": reason,
                    "label": RULE_BY_ID[reason]["label"],
                    "opportunities": denominator,
                    "observed": proportion(
                        denominator - counts["not_observed"], denominator
                    ),
                    "strategies": [
                        {
                            "id": key,
                            "label": label,
                            **proportion(counts[key], denominator),
                        }
                        for key, label in labels.items()
                    ],
                }
            )
        return {
            "calls": self.calls,
            "unique_sellers": len(self.sellers),
            "coverage": dict(self.coverage),
            "outcomes": dict(self.outcomes),
            "reasons": reasons,
            "primary_reasons": [
                {"id": key, **proportion(value, self.calls)}
                for key, value in sorted(self.primary.items())
            ],
            "no_detected_reason": proportion(
                self.primary["unknown"], self.calls
            ),
            "response_calls": proportion(
                len(self.responding_calls), len(self.reason_calls)
            ),
            "response_opportunity_calls": len(self.reason_calls),
            "responses": response_rows,
        }


def build_study(dataset: Path, catalog: dict, private_output: Path) -> dict:
    """Verify canonical bytes and publish aggregates with a read-only review DB."""
    files = {path.name: path for path in dataset.rglob("*.csv")}
    selected = [
        entry
        for entry in catalog["files"]
        if entry["family"] in {"bot_calls", "recording_sample", "call_turns"}
    ]
    for entry in selected:
        if (
            entry["name"] not in files
            or file_hash(files[entry["name"]]) != entry["sha256"]
        ):
            raise ValueError(
                "Study inputs differ from the verified source catalog"
            )
    manifest = [
        {key: entry[key] for key in ("name", "sha256", "rows", "family")}
        for entry in selected
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "sources": manifest,
                "rules": RULES,
                "responses": RESPONSES,
                "version": VERSION,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    sample = {}
    turns_by_call = defaultdict(list)
    for entry in selected:
        if entry["family"] == "recording_sample":
            for row in rows(files[entry["name"]]):
                if row["attempt_id"] in sample:
                    raise ValueError("Duplicate recording membership")
                sample[row["attempt_id"]] = row
        elif entry["family"] == "call_turns":
            for row in rows(files[entry["name"]]):
                turns_by_call[row["attempt_id"]].append(row)
    groups = {
        key: Cohort()
        for key in ("all_general", "sample_general", "sample_all")
    }
    personas = {
        key: {
            "calls": 0,
            "sellers": set(),
            "outcomes": Counter(),
            "seller_outcomes": defaultdict(set),
            "with_turns": 0,
            "summary_only": 0,
            "response_outcomes": defaultdict(Counter),
        }
        for key in PERSONAS
    }
    quality, samples_seen, general_seen, bot_seen = (
        Counter(),
        set(),
        set(),
        set(),
    )
    private_output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".study-", dir=private_output.parent
    )
    os.close(fd)
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE cases(id TEXT PRIMARY KEY,sample INTEGER,label TEXT,primary_reason TEXT,payload TEXT);
          CREATE TABLE memberships(case_id TEXT,persona TEXT,PRIMARY KEY(case_id,persona));
          CREATE INDEX case_filters ON cases(sample,label,primary_reason);
          CREATE INDEX persona_lookup ON memberships(persona,case_id);""")
        db.execute("INSERT INTO metadata VALUES('study_id',?)", (fingerprint,))
        for entry in selected:
            if entry["family"] != "bot_calls":
                continue
            count = 0
            for row in rows(files[entry["name"]]):
                count += 1
                attempt, seller = row["attempt_id"], row["fk_glusr_usr_id"]
                if attempt in bot_seen:
                    raise ValueError(
                        "Canonical attempt duplicate: reconcile before publishing study"
                    )
                bot_seen.add(attempt)
                is_sample = attempt in sample
                if (
                    not is_sample
                    and row["disposition_label"] != "General (talked)"
                ):
                    continue
                if is_sample:
                    if any(
                        sample[attempt].get(key) != value
                        for key, value in row.items()
                        if not key.startswith("_")
                    ):
                        raise ValueError(
                            "Recording sample differs from parent call"
                        )
                    samples_seen.add(attempt)
                clean, rejected = clean_turns(
                    turns_by_call.get(attempt, []), seller
                )
                quality.update(rejected)
                quality["calls_with_turn_conflicts"] += bool(
                    rejected.get("conflicting_slots")
                )
                diagnosis = evaluate(
                    row.get("lead_call_summary", ""),
                    clean,
                    label=row["disposition_label"],
                    flag=boolean(row.get("meeting_fixed")),
                )
                if row["disposition_label"] == "General (talked)":
                    general_seen.add(attempt)
                    groups["all_general"].add(row, diagnosis, is_sample, clean)
                    if is_sample:
                        groups["sample_general"].add(
                            row, diagnosis, True, clean
                        )
                if is_sample:
                    groups["sample_all"].add(row, diagnosis, True, clean)
                    for key in diagnosis["persona_ids"]:
                        group = personas[key]
                        group["calls"] += 1
                        group["sellers"].add(seller)
                        outcome = (
                            "disputed"
                            if diagnosis["meeting_conflict"]
                            else "meeting_claim"
                            if row["disposition_label"] == "Meeting Fixed"
                            else "no_meeting_claim"
                        )
                        group["outcomes"][outcome] += 1
                        group["seller_outcomes"][seller].add(outcome)
                        group["with_turns"] += bool(clean)
                        group["summary_only"] += not clean
                        for episode in diagnosis["response_episodes"]:
                            if episode["reason"] == key:
                                group["response_outcomes"][
                                    episode["response"]
                                ][outcome] += 1
                identifier = call_id(attempt)
                payload = {
                    "id": identifier,
                    "attempt_id": attempt,
                    "seller_glid": seller,
                    "sample_member": is_sample,
                    "summary": row.get("lead_call_summary", ""),
                    "source_file": row["_file"],
                    "source_row": row["_row"],
                    "source_sha256": entry["sha256"],
                    "observed_at": row.get("call_start_time"),
                    "duration_seconds": row.get("lead_call_duration"),
                    "turns": clean,
                    "turn_quality": rejected,
                    "evaluation": diagnosis,
                    "recording_id": "recording-"
                    + hashlib.sha256(attempt.encode()).hexdigest()[:24]
                    if is_sample
                    else None,
                    "audio_reviewed": False,
                    "study_id": fingerprint,
                }
                db.execute(
                    "INSERT INTO cases VALUES(?,?,?,?,?)",
                    (
                        identifier,
                        is_sample,
                        row["disposition_label"],
                        diagnosis["primary_reason"],
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                if is_sample:
                    db.executemany(
                        "INSERT INTO memberships VALUES(?,?)",
                        [
                            (identifier, key)
                            for key in diagnosis["persona_ids"]
                        ],
                    )
            if count != entry["rows"]:
                raise ValueError("Source row-count mismatch")
        if samples_seen != set(sample):
            raise ValueError(
                "Unjoined recording samples cannot enter the study"
            )
        db.commit()
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        db.close()
    os.chmod(temporary, 0o600)
    os.replace(temporary, private_output)
    persona_rows = []
    response_names = {key: label for key, label, _ in RESPONSES}
    response_names.update(
        {
            "other_response": "Other bot response",
            "not_observed": "No eligible adjacent response",
        }
    )
    for key, group in personas.items():
        meetings = group["outcomes"]["meeting_claim"]
        persona_rows.append(
            {
                "id": key,
                "label": PERSONAS[key],
                "calls": group["calls"],
                "unique_sellers": len(group["sellers"]),
                "outcomes": dict(group["outcomes"]),
                "meeting_share": proportion(meetings, group["calls"]),
                "descriptive_interval": interval(meetings, group["calls"]),
                "mixed_outcome_sellers": sum(
                    len(value) > 1
                    for value in group["seller_outcomes"].values()
                ),
                "with_usable_turns": group["with_turns"],
                "response_comparison": [
                    {
                        "response": response,
                        "label": label,
                        "meeting_claim": group["response_outcomes"][response][
                            "meeting_claim"
                        ],
                        "no_meeting_claim": group["response_outcomes"][
                            response
                        ]["no_meeting_claim"],
                        "disputed": group["response_outcomes"][response][
                            "disputed"
                        ],
                        "meeting_share": proportion(
                            group["response_outcomes"][response][
                                "meeting_claim"
                            ],
                            sum(group["response_outcomes"][response].values()),
                        ),
                    }
                    for response, label in response_names.items()
                    if sum(group["response_outcomes"][response].values())
                ],
                "strategy": RULE_BY_ID[key]["action"]
                if key in RULE_BY_ID
                else "Ask permission, confirm language and current need; avoid inventing a stable personality.",
                "assignment": "Current-call cue; overlapping, weak, unreviewed. Not a permanent seller label.",
            }
        )
    return {
        "version": VERSION,
        "study_id": fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Hash-verified complete scan of canonical General (talked) calls and the recording sample; deterministic summary/turn rules.",
        "source_manifest": manifest,
        "summary_definitions": summary_help(),
        "rules": RULES,
        "groups": {key: group.export() for key, group in groups.items()},
        "personas": persona_rows,
        "quality": dict(quality),
        "canonical_calls": len(bot_seen),
        "recording_scope": {
            "memberships": len(sample),
            "joined": len(samples_seen),
            "raw_turn_linked": len(set(sample) & set(turns_by_call)),
            "audio_listened": 0,
            "audio_note": "5,000 recording memberships; no audio was fetched or listened to in this study. Supplied role-labelled turns are machine transcripts, not newly validated recordings.",
        },
        "limitations": [
            "Association, not causal uplift or proof of which strategy works best.",
            "Calls and sellers are different denominators; persona cohorts overlap and repeat sellers.",
            "Broad lexical candidates can miss negation, actor nuance and unsupported languages.",
            "Turn conflicts are excluded, never arbitrarily resolved. Historical labels remain unchanged.",
            "Observed bot response does not establish a successful rebuttal; no validated resolution rate is available.",
        ],
    }
