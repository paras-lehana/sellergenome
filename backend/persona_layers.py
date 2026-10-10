"""Auditable established/incidental resolution, separate from conversation state."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.context_contract import digest, now
from backend.persona import generate_persona

FACTORS = {'language': ('category', 'en-IN'), 'pace': ('number', 1.0),
           'tone': ('category', 'warm and neutral'), 'warmth': ('category', 'neutral'),
           'formality': ('category', 'respectful and conversational'),
           'empathy': ('category', 'balanced'), 'personalization': ('category', 'confirm identity first'),
           'objection_strategy': ('category', 'ask for the principal concern'),
           'opening': ('category', 'AI disclosure and permission'),
           'next_action': ('category', 'confirm context')}


def validate_factor(name, value):
    """Controlled typed slots keep both the model and operator within bounds."""
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,63}', name):
        raise ValueError('Factor name must use lowercase letters, numbers and underscores.')
    if isinstance(value, (dict, list)) or value is None or len(str(value)) > 500:
        raise ValueError('Factor value must be a bounded scalar.')
    if name == 'pace' and (type(value) not in (int, float) or not math.isfinite(value) or not .5 <= value <= 2):
        raise ValueError('Pace must be a finite number between0.5 and2.')
    if name == 'language' and value not in {'en-IN','hi-IN','te-IN','ta-IN','bn-IN','mr-IN','gu-IN','kn-IN','ml-IN','pa-IN','od-IN'}:
        raise ValueError('Unsupported language.')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('A factor cannot contain a nonfinite number.')


class LayerService:
    """Never overwrite established history as a side effect of recent evidence."""

    def __init__(self, config, store, repository):
        self.config, self.store, self.repo = config, store, repository

    def baseline(self, subject, seller=None, scenario=False):
        """Import an existing accepted profile once, or retain explicit defaults."""
        current = self.repo.get('established', subject)
        if current:
            return current
        seller = seller or self.store.get('sellers', subject) or {'id': subject, 'source_mode': 'sample' if scenario else 'real'}
        old = None if scenario else self.store.get('personas', subject)
        evidence = [] if scenario else self.store.seller_evidence(subject, 12)
        persona = old or generate_persona(seller, evidence, self.config.values['persona'])
        fields = persona.get('fields', {})
        document = {'id': subject, 'subject_id': subject, 'layer': 'established',
                    'mode': 'sample' if scenario else 'historical', 'created_at': now(),
                    'review_status': 'existing_profile' if old else 'unreviewed_baseline',
                    'fields': {name: fields.get(name, {'value': default, 'confidence': 0,
                                'strength': 'default', 'reason': 'No accepted evidence; ask the seller.', 'evidence_ids': []})
                               for name, (_, default) in FACTORS.items()}, 'history': []}
        return self.repo.put('established', document, expected=0)

    def observe(self, subject, factor, value, *, actor='seller', source='operator',
                confidence=.7, citations=None, author='', business_id=None,
                transient=False, run_id=None, observed_at=None, review='unreviewed', evidence_keys=None, age_known=True):
        """Append an attributed correction with deterministic evidence identity."""
        validate_factor(factor, value)
        if actor not in {'seller', 'buyer', 'executive', 'bot', 'unknown'}:
            raise ValueError('Unknown actor.')
        if not 0 <= confidence <= 1 or not math.isfinite(confidence):
            raise ValueError('Confidence must be between zero and one.')
        settings = self.config.values['operations']['persona']
        business_id = business_id or str(uuid4())
        identifier = 'obs-' + digest([subject, factor, business_id])[:32]
        existing = self.repo.get('observations', identifier)
        if existing and existing['value'] == value:
            return existing
        at = observed_at or now()
        observed = datetime.fromisoformat(at.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('Observation timestamp requires a timezone.')
        seconds = settings['transient_seconds'] if transient else settings['expiry_days'] * 86400
        origin_run=self.repo.get('runs',run_id) if run_id else None
        mode='sample' if subject.startswith('scenario-') else origin_run['mode'] if origin_run and origin_run['subject_id']==subject else 'historical'
        observation = {'mode':mode,'id': identifier, 'subject_id': subject, 'factor': factor,
            'value': value, 'layer': 'incidental', 'confidence': confidence,
            'actor': actor, 'source': source, 'citations': citations or [],
            'author_label': author, 'business_id': business_id, 'at': at,
            'expires_at': (observed + timedelta(seconds=seconds)).isoformat(),
            'transient': transient, 'run_id': run_id, 'review_status': review,
            'method': 'operator_correction' if source == 'operator' else source,
            'evidence_keys': evidence_keys or [business_id], 'age_known': age_known,
            'config_revision': self.config.admin.revision}
        if factor not in FACTORS:
            self.repo.put('registry', {'id': factor, 'subject_id': 'factor-registry', 'name': factor,
                                     'value_type': type(value).__name__, 'status': 'proposed', 'source': source})
        return self.repo.put('observations', observation)

    def resolve(self, subject, run_id=None):
        """Deduplicate candidates and expose every weight/omission in the result."""
        baseline = self.baseline(subject, scenario=subject.startswith('scenario-'))
        run = self.repo.get('runs', run_id) if run_id else None
        execution_mode = run['mode'] if run and run['subject_id'] == subject else baseline['mode']
        observations = self.repo.list('observations', subject, 500)
        policy = self.config.values['operations']['persona']
        factors, omitted = [], []
        current = datetime.now(timezone.utc)
        grouped = {}
        seen = set()
        seen_evidence = set()
        for item in observations:
            identity = (item['factor'], item['business_id'])
            if identity in seen:
                omitted.append({'id': item['id'], 'reason': 'duplicate_business_observation'})
                continue
            related = {(item['factor'], key) for key in item.get('evidence_keys', [item['business_id']])}
            if related & seen_evidence:
                omitted.append({'id': item['id'], 'reason': 'related_business_evidence_already_counted'})
                continue
            observed = datetime.fromisoformat(item['at'])
            expiry = datetime.fromisoformat(item['expires_at'])
            age = (current-observed).total_seconds()/86400
            reason = 'unknown_evidence_age' if not item.get('age_known', True) else 'invalid_actor' if item['actor'] != 'seller' else 'expired' if expiry <= current else 'future' if age < -.002 else 'other_session' if item.get('transient') and item.get('run_id') != run_id else 'below_confidence' if item['confidence'] < policy['confidence_floor'] else None
            if reason:
                omitted.append({'id': item['id'], 'reason': reason})
                continue
            seen_evidence |= related
            seen.add(identity)
            item = {**item, 'decay': 2 ** (-max(0, age) / policy['half_life_days'])}
            grouped.setdefault(item['factor'], []).append(item)
        for factor in dict.fromkeys([*baseline['fields'], *grouped]):
            base = baseline['fields'].get(factor, {'value': None, 'confidence': 0, 'strength': 'default', 'evidence_ids': []})
            weight = policy['factor_weights'].get(factor, policy['established_weight']) if policy['factor_overrides_enabled'] else policy['established_weight']
            recent = grouped.get(factor, [])
            candidates = {}
            for item in recent:
                key = json.dumps(item['value'], sort_keys=True)
                candidate = candidates.setdefault(key, {'value': item['value'], 'score': 0, 'observations': []})
                candidate['score'] += item['confidence'] * item['decay']
                candidate['observations'].append(item['id'])
            total = sum(x['score'] for x in candidates.values())
            for candidate in candidates.values():
                candidate['normalized_score'] = candidate['score'] / max(total, 1)
                candidate['influence'] = (1-weight) * candidate['normalized_score']
            value, confidence = base['value'], float(base.get('confidence') or 0)
            supported_base = base.get('strength') != 'default' and bool(base.get('evidence_ids'))
            base_score = weight * confidence if supported_base else 0
            chosen = max(candidates.values(), key=lambda x: x['influence'], default=None)
            reason = 'Established evidence remains principal under the configured influence policy.' if supported_base else 'No supported established evidence; retain a stated default until evidence is available.'
            if chosen:
                if factor == 'pace' and isinstance(value, (int,float)) and isinstance(chosen['value'], (int,float)):
                    target = value * weight + chosen['value'] * (1-weight) if supported_base else chosen['value']
                    delta = max(-policy['numeric_max_change'], min(policy['numeric_max_change'], target-value))
                    value = round(max(.5, min(2., value+delta)), 3)
                    reason = 'Numeric delivery blended and clamped by the configured maximum change.'
                elif chosen['influence'] > base_score:
                    value = chosen['value']
                    reason = 'Incidental candidate has stronger weighted support; established history is unchanged.'
                confidence = max(confidence if supported_base else 0, chosen['normalized_score'])
            strength = 'strong' if supported_base and base.get('strength') == 'strong' else 'weak' if recent or supported_base else 'default'
            citations = [citation for x in recent for citation in x.get('citations', [])]
            factors.append({'id': 'factor-' + digest([subject, factor, run_id, baseline['revision'], [(x['id'], x['revision']) for x in recent], self.config.admin.revision])[:32],
                'subject_id': subject, 'run_id': run_id, 'factor': factor, 'value': value,
                'layer': 'effective', 'confidence': confidence, 'strength': strength,
                'established_value': base['value'], 'established_weight': weight,
                'incidental_weight': 1-weight, 'influence': 1-weight if recent else 0,
                'established_score': base_score, 'candidates': list(candidates.values()),
                'contradiction': any(x['value'] != base['value'] for x in recent) if supported_base else len(candidates)>1,
                'reason': reason, 'citations': citations, 'evidence_ids': base.get('evidence_ids', []),
                'at': now(), 'config_revision': self.config.admin.revision,
                'confidence_kind': 'heuristic_not_probability', 'mode': execution_mode,
                'established_mode': baseline['mode']})
        sections = [{'id': key, 'version': self.config.admin.revision,
                     'text': text} for key, text in self.config.values['operations']['prompts'].items()]
        return {'contract_version':'persona-layers/4.0','subject_id': subject, 'established': baseline, 'incidental': observations,
                'effective': factors, 'omitted': omitted, 'policy': policy,
                'prompt_inspector': {'sections': sections,
                    'rendered': '\n\n'.join(f"[{x['id']} revision {x['version']}]\n{x['text']}" for x in sections) + '\n\n[resolved factors]\n' + json.dumps([{k: x[k] for k in ('factor','value','established_weight','incidental_weight','reason')} for x in factors], ensure_ascii=False),
                    'selected_observation_ids': [x['id'] for values in grouped.values() for x in values],
                    'omitted': omitted, 'explanation': 'Recorded policy and evidence selection, not hidden model reasoning.'}}

    def promote(self, subject, observation_id, expected_revision, reviewer):
        """Promotion requires a separate explicit review and preserves history."""
        item = self.repo.get('observations', observation_id)
        if not item or item['subject_id'] != subject or item['actor'] != 'seller' or item.get('transient'):
            raise ValueError('Only non-transient seller observations can be reviewed for promotion.')
        base = self.baseline(subject)
        base['history'] = [*base.get('history', []), {'revision': base['revision'], 'fields': base['fields'], 'reviewer': reviewer, 'at': now()}][-100:]
        base['fields'] = {**base['fields'], item['factor']: {'value': item['value'], 'confidence': item['confidence'], 'strength': 'weak', 'reason': 'Operator reviewed promotion.', 'evidence_ids': [item['id']]}}
        base['review_status'] = 'reviewed'
        return self.repo.put('established', base, expected=expected_revision)
