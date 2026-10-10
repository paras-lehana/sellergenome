"""Strict typed V4 projections; dynamic factor names are values, not mappings."""
import json
import re
from backend.context_contract import now
from backend.operations_store import PROJECTIONS


class OperationsIndex:
    """Maintain project-only versioned aliases with verified document readback."""

    def __init__(self, config, search, repository):
        self.config, self.search, self.repository = config, search, repository
        self.ready = False
        self.status = 'not_configured' if not config.es_url else 'not_checked'

    def alias(self, kind):
        if kind not in PROJECTIONS:
            raise ValueError('Unknown projection.')
        prefix = self.config.values['operations']['index_prefix']
        if not re.fullmatch(r'sellergenome-v4[a-z0-9-]*', prefix):
            raise ValueError('V4 index prefix must stay within its namespace.')
        return f'{prefix}-{kind}'

    async def ensure(self):
        """Verify physical index health before publishing each fresh alias."""
        if not self.config.es_url or self.ready:
            return
        mapping = {'dynamic': 'strict', 'properties': {
            **{key: {'type': 'keyword'} for key in ('id', 'subject_id', 'source', 'mode', 'layer', 'factor', 'status', 'run_id', 'value_keyword', 'strength', 'actor', 'cache', 'node')},
            'revision': {'type': 'long'}, 'confidence': {'type': 'float'},
            'influence': {'type': 'float'}, 'duration_ms': {'type': 'float'},
            'value_number': {'type': 'double'}, 'value_boolean': {'type': 'boolean'},
            'contradiction': {'type': 'boolean'}, 'coverage_present': {'type': 'integer'}, 'coverage_total': {'type': 'integer'},
            'at': {'type': 'date'}, 'expires_at': {'type': 'date'},
            'value_text': {'type': 'text'}, 'metadata': {'type': 'flattened'},
            'document': {'type': 'object', 'enabled': False}}}
        async with self.search.client() as client:
            for kind in sorted(PROJECTIONS):
                alias = self.alias(kind)
                physical = alias + '-v1'
                exists = await client.head('/' + physical)
                if exists.status_code == 404:
                    result = await client.put('/' + physical, json={'settings': {'number_of_shards': 1, 'number_of_replicas': 0}, 'mappings': mapping})
                    result.raise_for_status()
                else:
                    exists.raise_for_status()
                additions = await client.put('/' + physical + '/_mapping', json={'properties': mapping['properties']})
                additions.raise_for_status()
                checked = await client.get('/' + physical + '/_mapping')
                checked.raise_for_status()
                if checked.json()[physical]['mappings']['properties']['document'].get('enabled') is not False:
                    raise ValueError('Projection mapping differs from V4 contract.')
                has_alias = await client.head('/_alias/' + alias)
                if has_alias.status_code == 404:
                    result = await client.post('/_aliases', json={'actions': [{'add': {'index': physical, 'alias': alias, 'is_write_index': True}}]})
                    result.raise_for_status()
        self.ready, self.status = True, 'ready'

    def projection(self, kind, doc):
        """Typed slots enable comparisons without model-generated field keys."""
        out = {key: doc[key] for key in ('id', 'subject_id', 'source', 'mode', 'layer', 'factor', 'status', 'run_id', 'revision', 'confidence', 'influence', 'duration_ms', 'strength', 'actor', 'cache', 'node', 'expires_at', 'contradiction') if doc.get(key) is not None}
        out.update({'at': doc.get('at') or doc.get('retrieved_at') or doc.get('created_at') or now(), 'document': doc})
        if kind == 'snapshots':
            fields = doc.get('fields', {})
            out['metadata'] = {'missing_fields': [key for key,value in fields.items() if value['availability'] in {'missing','blank'}]}
            out['coverage_present'] = sum(value['availability'] == 'present' for value in fields.values())
            out['coverage_total'] = len(fields)
        value = doc.get('value')
        if isinstance(value, bool):
            out['value_boolean'] = value
        elif isinstance(value, (int, float)):
            out['value_number'] = value
        elif value is not None:
            out['value_keyword'] = str(value)[:200]
            out['value_text'] = str(value)[:4000]
        return out

    async def deliver(self):
        """Pause only configured projections and retain their durable intents."""
        if not self.config.es_url:
            return
        try:
            await self.ensure()
            async with self.search.client() as client:
                settings = self.config.values['operations']
                excluded = ([] if settings['index_api_responses'] else ['snapshots']) + ([] if settings['index_personas'] else ['established', 'observations', 'factors'])
                for row in self.repository.pending(excluded=excluded):
                    try:
                        doc = json.loads(row['payload'])
                        path = '/' + self.alias(row['kind']) + '/_doc/' + row['id']
                        result = await client.put(path, params={'version': row['generation'], 'version_type': 'external_gte', 'refresh': 'false'}, json=self.projection(row['kind'], doc))
                        result.raise_for_status()
                        proof = await client.get(path)
                        proof.raise_for_status()
                        if proof.json()['_version'] < row['generation']:
                            raise ValueError('Readback generation mismatch.')
                        self.repository.delivered(row)
                        if row['kind'] == 'snapshots' and doc.get('run_id'):
                            self.repository.event(doc['run_id'], 'index-' + doc['source'], 'indexed', source=doc['source'], mode=doc['mode'], snapshot_id=doc['id'], response={'alias': self.alias('snapshots'), 'verified_generation': row['generation']})
                        elif row['kind']=='factors' and doc.get('run_id'):
                            self.repository.event(doc['run_id'],'persona-index','indexed',source='elasticsearch_factors',mode=doc.get('mode','historical'),request={'method':'PUT','endpoint':path,'parameters':{'factor':doc['factor'],'value_slots':'keyword / number / boolean; fixed mapping'}},response={'alias':self.alias('factors'),'factor':doc['factor'],'verified_generation':row['generation'],'query':{'term':{'subject_id':doc['subject_id']}}})
                    except Exception:
                        self.repository.delivered(row, 'projection_delivery_failed')
            self.status = 'connected'
        except Exception:
            self.status = 'unavailable'

    async def cached(self, identifier):
        """A point lookup retrieves the exact scope/config/mode snapshot only."""
        if not self.config.es_url:
            return None
        try:
            async with self.search.client() as client:
                result = await client.get('/' + self.alias('snapshots') + '/_doc/' + identifier)
                if result.status_code == 404:
                    return None
                result.raise_for_status()
                return result.json()['_source']['document']
        except Exception:
            self.status = 'unavailable'
            return None
