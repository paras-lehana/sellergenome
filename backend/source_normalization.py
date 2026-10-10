"""Typed V3 evidence projections shared by live responses and synthetic samples."""

import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def dotted(value, path: str):
    """Read JSON object paths without evaluating source content."""
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def timestamp(value) -> str | None:
    """Convert documented IST timestamps while keeping missing source time unknown."""
    if not isinstance(value, str):
        return None
    for pattern in (None, "%d-%m-%Y %H:%M:%S"):
        try:
            parsed = (
                datetime.fromisoformat(value.strip())
                if pattern is None
                else datetime.strptime(value.strip(), pattern)
            )
            if not parsed.tzinfo:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            return parsed.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            continue
    return None


def clean_text(value, maximum: int = 2000) -> str:
    """Remove contact/URL tokens; private names never become public projections."""
    if not isinstance(value, str):
        return ""
    text = re.sub(
        r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+|(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)",
        "[redacted]",
        value,
    )
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def tri_state(value) -> bool | None:
    """Documented binary flags are observations; unknown codes stay unknown."""
    if value in (True, 1, "1", "1.0"):
        return True
    if value in (False, 0, "0", "0.0"):
        return False
    return None


def meeting_interpretation(value, spec: dict) -> tuple[bool | None, str]:
    """Interpret source codes from bounded admin choices; unknown stays unknown."""
    positive = spec.get("meeting_true_codes", ["1", "1.0", "true"])
    negative = spec.get("meeting_false_codes", ["0", "0.0", "false"])
    code = str(value).strip().casefold() if value is not None else ""
    positive = {str(item).strip().casefold() for item in positive}
    negative = {str(item).strip().casefold() for item in negative}
    known_true = code in positive and code not in negative
    known_false = code in negative and code not in positive
    flag = True if known_true else False if known_false else None
    version = digest(json.dumps([sorted(positive), sorted(negative)]))
    return flag, version


def digest(value: str) -> str:
    """Stable IDs are independent of response order and acquisition time."""
    return hashlib.sha256(value.encode()).hexdigest()[:24]


IDENTITY_PATHS = {
    "redash_hotlead": ["fk_glusr_usr_id"],
    "redash_recordings": ["fk_glusr_usr_id"],
    "redash_executive": ["fk_glusr_usr_id"],
    "redash_pns": ["pns_call_receiver_glusr_id"],
    "pns": ["SELLER_GLID", "seller_glid", "USR_ID"],
    "context": ["glid", "GLID", "user_glid", "fk_glusr_usr_id"],
    "company": ["glid", "GLID", "fk_glusr_usr_id"],
    "sts": ["glid", "fk_glusr_usr_id"],
    "chat": ["user_glid"],
    "buyleads": ["seller_glid", "fk_glusr_usr_id"],
    "enquiries": ["seller_glid", "fk_glusr_usr_id"],
}


def _id_text(value) -> str:
    """Redash returns numeric ids as floats (23456789.0); compare the integer text so a seller's own rows match while
    any other seller's id still fails."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value)
    return text[:-2] if re.fullmatch(r"[0-9]+\.0", text) else text


def identity_matches(source: str, record: dict, glid: str) -> bool:
    """Redash rows require scoped IDs; source envelopes may omit echoed identity."""
    supplied = [
        _id_text(dotted(record, path))
        for path in IDENTITY_PATHS.get(source, [])
        if dotted(record, path) is not None
    ]
    return (
        bool(supplied) and all(x == glid for x in supplied)
        if source.startswith("redash")
        else all(x == glid for x in supplied)
    )


def normalize(
    source: str,
    record: dict,
    seller_id: str,
    mode: str,
    spec: dict,
    fetched_at: str,
) -> dict:
    """Build a role-aware observation without inventing current seller intent."""
    attrs = {}
    occurrence = None
    revision = None
    key = None
    call_id = None
    role = "seller"
    summary = ""
    if source == "redash_hotlead":
        role = "mixed_summary"
        key = record.get("data_hotlead_disposition_dtlid")
        call_id = record.get("fk_click_to_call_id")
        occurrence = record.get("call_start_time")
        revision = record.get("last_updated_on")
        raw_flag = record.get("lead_shl_trigger")
        flag, interpretation_version = meeting_interpretation(raw_flag, spec)
        attrs = {
            "duration": record.get("lead_call_duration"),
            "meeting_fixed": flag,
            "meeting_flag_mapping_known": flag is not None,
            "meeting_flag_raw": str(raw_flag)[:30]
            if raw_flag is not None
            else None,
            "meeting_meaning": "source_reported_meeting_fixed_not_held_or_sale",
        }
        summary = clean_text(record.get("lead_call_summary"))
        # The verified test records contain two-character placeholders. They
        # establish connectivity, not enough text for a persona inference.
        attrs["summary_status"] = (
            "usable" if len(summary) >= 20 else "insufficient_evidence"
        )
        if attrs["summary_status"] != "usable":
            summary = ""
    elif source == "redash_recordings":
        key = record.get("call_recordings_detail_id")
        call_id = record.get("fk_click_to_call_id")
        occurrence = record.get("call_start_time")
        revision = record.get("updated_on") or record.get("insertion_date")
        attrs = {
            "recording_status": clean_text(
                str(record.get("status", "unknown")), 100
            ),
            "transcript_status": "metadata_only_no_transcript_column",
        }
    elif source == "redash_executive":
        key = "executive-count"
        attrs = {
            "executive_call_count": record.get("call_count"),
            "snapshot_semantics": "read_time_aggregate",
        }
        occurrence = "2026-09-30 12:00:00" if mode == "sample" else fetched_at
        revision = occurrence
    elif source == "redash_pns":
        key = record.get("pns_call_record_id")
        call_id = "pns-" + str(key)
        occurrence = record.get("pns_call_record_date")
        attrs = {
            "call_status": clean_text(
                str(record.get("pns_call_status", "unknown")), 100
            ),
            "duration": record.get("pns_call_patch_duration"),
            "ring_duration": record.get("pns_call_ring_duration"),
            "category": record.get("pns_call_ref_prime_mcat"),
        }
    elif source == "pns":
        key = record.get("file_id")
        occurrence = record.get("call_timestamp")
        data = record.get("extracted_data", {})
        attrs = {
            "language": clean_text(
                dotted(data, "metadata.primary_language"), 100
            ),
            "call_outcome": clean_text(
                dotted(data, "metadata.call_outcome.category"), 100
            ),
            "buyer_deal_readiness": clean_text(
                dotted(data, "lead_tag.deal_readiness"), 100
            ),
            "buyer_intent": clean_text(
                dotted(data, "metadata.buyer_intent.intent_level"), 100
            ),
            "buyer_persona": clean_text(
                dotted(data, "metadata.buyer_persona.persona_category"), 100
            ),
            "callback": tri_state(dotted(data, "next_steps.callback")),
            "seller_next_steps": clean_text(
                dotted(data, "next_steps.seller_next_steps")
            ),
            "buyer_next_steps": clean_text(
                dotted(data, "next_steps.buyer_next_steps")
            ),
            "role_boundary": "buyer_* fields describe the buyer and are not seller traits",
        }
        role = "mixed_participants"
        summary = clean_text(
            dotted(data, "metadata.call_outcome.conclusion_notes")
        )
    elif source == "chat":
        key = record.get("message_id") or record.get("msg_query_id")
        occurrence = record.get("msg_date")
        attrs = {
            "message_type": record.get("msg_query_type"),
            "reference_type": record.get("msg_ref_type"),
            "call_status": record.get("msg_call_status"),
            "duration": record.get("msg_call_duration"),
            "speaker_role": "unverified",
        }
        role = "unverified"
        summary = clean_text(
            record.get("message_text") or record.get("msg_sub")
        )
    elif source == "context":
        key = "context-snapshot"
        occurrence = record.get("updated_at") or dotted(
            record, "activitydetails.last_activity"
        )
        attrs = {
            "activity_count": dotted(record, "activitydetails.activity_count"),
            "last_activity": dotted(record, "activitydetails.last_activity"),
            "custtype_weight": dotted(record, "kycdetails.custtype_weight"),
            "business_type": dotted(record, "kycdetails.business_type"),
            "turnover_band": dotted(record, "kycdetails.turnover_band"),
            "explicit_language": record.get("preferred_language"),
            "profile_semantics": "source_profile_not_personality",
        }
    elif source == "company":
        key = "company-profile"
        occurrence = record.get("updated_at")
        attrs = {
            "category": record.get("category"),
            "business_type": record.get("business_type"),
            "product_count": record.get("product_count"),
            "turnover_band": record.get("turnover_band"),
        }
    else:
        key = (
            record.get("id") or record.get("event_id") or record.get("lead_id")
        )
        occurrence = (
            record.get("observed_at")
            or record.get("updated_at")
            or record.get("created_at")
        )
        attrs = {
            name: record.get(name)
            for name in (
                "status",
                "category",
                "activity_count",
                "enquiries_received",
                "contact_suppressed",
            )
        }
        summary = clean_text(record.get("summary"))
    # Configured safe mappings remain compatible; they cannot add arbitrary PII
    # fields or replace the identity, timestamps or inferred provenance metadata.
    allowed = {
        "language",
        "outcome",
        "readiness",
        "callback",
        "message_type",
        "reference_type",
        "call_status",
        "duration",
        "status",
        "category",
        "activity_count",
        "enquiries_received",
        "product_count",
        "custtype_weight",
        "contact_suppressed",
    }
    for output, path in spec.get("field_map", {}).items():
        if output == "observed_at":
            occurrence = dotted(record, path)
        elif output in allowed:
            attrs[output] = dotted(record, path)
    attrs = {
        name: clean_text(value, 2000) if isinstance(value, str) else value
        for name, value in attrs.items()
        if value is not None
        and not isinstance(value, (dict, list))
        and value != ""
    }
    if summary:
        attrs["summary"] = summary
    observed = timestamp(occurrence)
    updated = timestamp(revision) or observed
    if not key:
        # Hash the stable semantic projection, never the row index or fetch time.
        key = "content-" + digest(
            json.dumps(
                {"attributes": attrs, "observed_at": observed}, sort_keys=True
            )
        )
    canonical = (
        f"seller-call:{seller_id}:{call_id}" if call_id is not None else None
    )
    event = {
        "id": "source-" + digest(f"{mode}:{source}:{seller_id}:{key}"),
        "seller_id": seller_id,
        "kind": source,
        "text": f"{spec['name']} ({'synthetic sample' if mode == 'sample' else 'live source'}). "
        + (summary + " " if summary else "")
        + "Validated fields: "
        + json.dumps(
            {k: v for k, v in attrs.items() if k != "summary"},
            ensure_ascii=False,
            sort_keys=True,
        ),
        "attributes": attrs,
        "source_ref": source,
        "source_mode": "sample" if mode == "sample" else "private",
        "origin_mode": mode,
        "actor_role": role,
        "source_record_id": str(key),
        "canonical_call_id": canonical,
        "observed_at": observed,
        "source_updated_at": updated,
        "ingested_at": fetched_at,
        "ttl_hours": float(spec.get("ttl_hours", 24)),
        "confidence": 0.5,
        "timestamp_inferred": False,
        "status": "observed" if observed else "source_time_unverified",
        "method": "typed_read_only_adapter_v3",
        "config_revision": spec.get("config_revision", 0),
    }
    if source == "redash_hotlead":
        event["interpretation_version"] = interpretation_version
        event["source_content_hash"] = digest(
            json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        )
    if summary:
        # Cues are weak, actor-attributed, exact spans of this evidence text.
        # They aid grounded plans but never turn a summary into a seller quote.
        from backend.insights import deterministic_insights

        event["insights"] = deterministic_insights(event["text"], event["id"])
        event["insight_traits"] = sorted(
            {item["trait"] for item in event["insights"]}
        )
    return event
