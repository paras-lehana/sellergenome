"""Versioned, lossless production context and isolated deterministic scenarios.

Source payloads are evidence, never instructions. A supplied log quotation is an
upstream claim, not an independently verified transcript. No network calls here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

VERSION = 'seller-context/4.0'
VENDOR_FIELDS = (
    'is_dev', 'lead_id', 'glid', 'phone_number', 'contact_name',
    'business_nature', 'company_name', 'city', 'state', 'bot_flag_id',
    'last_call_summary', 'pns_received_30d', 'pns_picked_30d',
    'local_buylead_count', 'national_buylead_count', 'enquiries_received_30d',
    'products', 'competitor', 'data_type', 'custtype', 'retry_flag',
    'sub_data_type', 'last_call_start_time', 'last_meeting_date',
    'lead_locality_1', 'lead_locality_2', 'personalise', 'insights',
)
# The export may include an additional vendor field; preserve it rather than
# silently inventing a 29th key from a planning count. Tests record actual keys.
COUNTS = {'pns_received_30d', 'pns_picked_30d', 'local_buylead_count',
          'national_buylead_count', 'enquiries_received_30d'}
CATEGORIES = ('purchase_intent', 'objection_or_blocker', 'last_interaction',
              'market_opportunity', 'competitor_landscape',
              'engagement_history', 'catalog_gaps')
ALIASES = {'company_name': ('company_name', 'company', 'companyname'),
           'contact_name': ('contact_name', 'name', 'contact_person'),
           'city': ('city', 'city_name'), 'state': ('state', 'state_name'),
           'products': ('products', 'product_list', 'category'),
           'glid': ('glid', 'fk_glusr_usr_id', 'GLUSR_USR_ID')}


def now() -> str:
    """Return timezone-aware receipt time, never substitute it for occurrence."""
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    """Stable content identity prevents duplicate copies strengthening evidence."""
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def pointer_get(document: Any, pointer: str) -> Any:
    """Resolve RFC6901 pointers, including keys containing slash and tilde."""
    value = document
    for token in pointer.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def objects_in_log(text: str) -> list[tuple[dict, int, int]]:
    """Decode bounded embedded JSON without eval or greedy brace matching."""
    decoder, result, offset = json.JSONDecoder(), [], 0
    while offset < len(text):
        start = text.find('{', offset)
        if start < 0:
            break
        try:
            item, length = decoder.raw_decode(text[start:])
        except ValueError:
            offset = start + 1
            continue
        if isinstance(item, dict):
            result.append((item, start, start + length))
        offset = start + length
    return result


def parse_production(value: dict) -> dict:
    """Accept vendor JSON or an ES export with layered serialized insights."""
    if not isinstance(value, dict):
        raise ValueError('Context input must be an object.')
    if len(json.dumps(value)) > 2 * 1024 * 1024:
        raise ValueError('Context exceeds the 2 MiB input limit.')
    origin, span, candidates = '', None, []
    source = value.get('_source', value)
    logs = source.get('customlogs', '')
    if isinstance(logs, list):
        logs = '\n'.join(str(x) for x in logs)
    objects = objects_in_log(logs) if isinstance(logs, str) else []
    for obj, start, end in objects:
        if 'glid' in obj and ('phone_number' in obj or 'company_name' in obj):
            candidates.append((obj, start, end))
    if candidates:
        vendor, start, end = max(candidates, key=lambda x: len(x[0]))
        span = {'pointer': '/_source/customlogs', 'start': start, 'end': end}
        origin = 'embedded_vendor_payload'
    elif 'glid' in source:
        vendor = source
        origin = 'vendor_payload'
    else:
        raise ValueError('No recognizable vendor payload in this input.')
    vendor = copy.deepcopy(vendor)
    insights = vendor.get('insights')
    if isinstance(insights, str):
        try:
            insights = json.loads(insights)
        except ValueError:
            insights = {'parse_error': 'invalid_serialized_insights', 'raw': insights}
    claims = insights if isinstance(insights, dict) else {}
    copies = [obj for obj, _, _ in objects if isinstance(obj.get('insights'), list)]
    differences = [obj for obj in copies if digest(obj) != digest(claims)]
    # Identical repetitions in fields/_source do not create observations.
    return {'vendor': vendor, 'insights': claims,
            'doubts': claims.get('doubts', []), 'input_kind': origin,
            'log_span': span, 'content_hash': digest(vendor),
            'conflicting_insight_copies': list({digest(x): x for x in differences}.values()),
            'export_metadata': {k: source.get(k) for k in (
                'request_start_time', 'request_end_time', 'process_time',
                'custtypeweight', 'activityid') if k in source}}


def normalize_value(name: str, value: Any) -> Any:
    """Expose useful types while the field envelope retains the original value."""
    if name in COUNTS:
        try:
            return int(value) if str(value).strip() else value
        except (ValueError, TypeError):
            return value
    if name in {'personalise', 'is_dev', 'retry_flag'}:
        if str(value).lower() in {'true', 'false'}:
            return str(value).lower() == 'true'
    if name == 'insights' and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return copy.deepcopy(value)


def snapshot(payload: dict, subject: str, source: str, mode: str,
             revision: int, ttl_hours: float = 24, retrieval: str = 'live_api',
             supplied: bool = False) -> dict:
    """Normalize an immutable snapshot without losing blanks, dates or extras."""
    parsed = parse_production(payload) if supplied else {'vendor': payload}
    vendor = parsed['vendor']
    retrieved = now()
    expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    # Each actual retrieval has an immutable receipt. Evidence deduplication uses
    # content/business identities independently, so retries cannot add support.
    snap_id = 'ctx-' + digest([subject, source, mode, vendor, revision, retrieved])[:32]
    fields = {}
    for name in dict.fromkeys([*VENDOR_FIELDS, *vendor]):
        raw, key = None, None
        for candidate in ALIASES.get(name, (name,)):
            if candidate in vendor:
                key, raw = candidate, vendor[candidate]
                break
        availability = 'missing' if key is None else 'blank' if raw is None or raw == '' or raw == [] else 'present'
        pointer = '/payload/' + str(key or name).replace('~', '~0').replace('/', '~1')
        if name in vendor.get('source_paths', {}):
            pointer = '/payload' + vendor['source_paths'][name]
        fields[name] = {
            'value': normalize_value(name, raw), 'raw_value': raw,
            'availability': availability, 'origin': 'supplied_example' if supplied else 'fixture' if mode == 'sample' else source,
            'retrieval': retrieval, 'source': source, 'snapshot_id': snap_id,
            'pointer': pointer if key is not None else None,
            'log_span': ({**parsed['log_span'], 'pointer': '/original_input' + parsed['log_span']['pointer']} if parsed.get('log_span') else None), 'observed_at': vendor.get('observed_at') or None,
            'source_revision_at': vendor.get('last_updated_on') or None,
            'retrieved_at': retrieved, 'expires_at': expires,
            'method': 'lossless_normalization', 'confidence': None,
            'review_status': 'upstream_reported' if supplied else 'unreviewed',
            'config_revision': revision,
        }
    if supplied and 'custtypeweight' in parsed.get('export_metadata', {}):
        raw_weight = parsed['export_metadata']['custtypeweight']
        fields['custtypeweight'] = {**fields['custtype'], 'value': raw_weight,
            'raw_value': raw_weight, 'availability': 'present',
            'pointer': '/parsing/export_metadata/custtypeweight',
            'log_span': None, 'method': 'outer_export_metadata'}
    weight=fields.get('custtypeweight',{}).get('value')
    try:
        numeric_weight=float(weight)
        classification='paid' if numeric_weight<1000 else 'free' if numeric_weight>1000 else 'unknown'
    except (TypeError,ValueError):
        classification='unknown'
    return {'id': snap_id, 'contract_version': VERSION, 'subject_id': subject,
            'source': source, 'mode': mode, 'config_revision': revision,
            'payload': copy.deepcopy(vendor), 'fields': fields,
            **({'original_input': copy.deepcopy(payload)} if supplied else {}),
            'parsing': {k: v for k, v in parsed.items() if k != 'vendor'},
            'retrieved_at': retrieved, 'expires_at': expires,
            'content_hash': digest(vendor), 'indexing_status': 'pending',
            'customer_classification': {'value':classification,'weight':weight,'method':'supplied_weight_rule','reason':'Below1000 is paid; above1000 is free; exactly1000 or missing stays unknown. Custtype is a separate field.'}}


def scenario_payload(subject: str, source: str = 'company') -> dict:
    """A complete synthetic contract that never mutates a displayed real GLID."""
    seed = int(digest(subject)[:8], 16)
    names = ['Kaveri Packaging', 'Northline Tools', 'Deccan Components', 'Prakash Textiles']
    cities = [('Pune', 'Maharashtra'), ('Jaipur', 'Rajasthan'), ('Hyderabad', 'Telangana'), ('Surat', 'Gujarat')]
    city, state = cities[seed % 4]
    payload = {key: '' for key in VENDOR_FIELDS}
    payload.update({'is_dev': True, 'lead_id': 'scenario-' + digest(subject)[:8],
                    'glid': subject.removeprefix('real-'), 'contact_name': 'Demo owner',
                    'company_name': names[seed % 4], 'business_nature': 'Manufacturer',
                    'city': city, 'state': state, 'personalise': 'true',
                    'products': ['Packaging machinery', 'Industrial components'],
                    'competitor': [], 'pns_received_30d': 12, 'pns_picked_30d': 9,
                    'enquiries_received_30d': 6, 'local_buylead_count': 0,
                    'national_buylead_count': 3, 'retry_flag': False,
                    'custtype': 53, 'custtypeweight': 1199,
                    'language': 'te-IN' if seed % 4 == 2 else 'hi-IN',
                    'observed_at': '2026-10-08T10:00:00+00:00',
                    'insights': {'insights': [{'category': c, 'summary': text} for c, text in zip(CATEGORIES, (
                        'Exploring a paid package; ask permission before explaining.',
                        'Wants clear examples of relevant enquiries.',
                        'Asked for a concise explanation.', 'Local buyer relevance matters.',
                        'No verified competitor pricing available.', 'Recently explored product visibility.',
                        'Confirm product specifications before recommending catalog work.'))],
                        'doubts': ['Synthetic scenario, not verified business history.']}})
    if source == 'context':
        payload['last_call_summary'] = 'Seller asked in Hindi: please keep the explanation short and explain lead quality.'
    return payload
