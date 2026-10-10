"""Lossless input, actor isolation, durable sequencing and layer policy proofs."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.context_contract import snapshot, parse_production, scenario_payload, pointer_get, VENDOR_FIELDS
from backend.config import Settings
from backend.store import Store
from backend.operations_store import OperationsStore
from backend.operations import OperationsService
from backend.persona_layers import LayerService
from backend.search import SearchIndex


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVATE_CONFIG_DIR', str(tmp_path/'config'))
    monkeypatch.setenv('DATA_DIR', str(tmp_path/'data'))
    monkeypatch.setenv('APPROVED_GLIDS_PATH', str(tmp_path/'approved.json'))
    monkeypatch.setenv('ELASTICSEARCH_URL', '')
    (tmp_path/'approved.json').write_text(json.dumps(['123']))
    config = Settings()
    store = Store(config.db_path)
    return OperationsService(config, store, SearchIndex(config))


def test_lossless_nested_export_zero_false_blank_and_duplicates():
    vendor = scenario_payload('123')
    vendor['insights'] = json.dumps(vendor['insights'])
    vendor['extra_vendor_fact'] = 0
    export = {'_index':'invalid-day-40', '_source': {'customlogs': 'prefix '+vendor['insights']+' payload '+json.dumps(vendor), 'custtypeweight':1199}}
    parsed = parse_production(export)
    assert parsed['vendor'] == vendor
    assert parsed['conflicting_insight_copies'] == []
    snap = snapshot(export, 'replay-123', 'supplied_example','local', 3, supplied=True)
    for name in vendor:
        field = snap['fields'][name]
        assert pointer_get(snap, field['pointer']) == vendor[name]
    assert snap['fields']['personalise']['raw_value'] == 'true'
    assert snap['fields']['personalise']['value'] is True
    assert snap['fields']['local_buylead_count']['value'] == 0
    assert snap['fields']['retry_flag']['value'] is False
    assert snap['fields']['phone_number']['availability'] == 'blank'
    assert len(snap['parsing']['insights']['insights']) == 7


def test_actor_expiry_dedup_and_promotion(services):
    layers = services.layers
    subject = 'scenario-test'
    base = layers.baseline(subject)
    first = layers.observe(subject,'language','hi-IN',business_id='same',citations=[{'snapshot_id':'x'}])
    second = layers.observe(subject,'language','hi-IN',business_id='same')
    assert first['id'] == second['id']
    assert first['mode']=='sample'
    layers.observe(subject,'language','te-IN',actor='buyer')
    layers.observe(subject,'pace',1.2,observed_at=(datetime.now(timezone.utc)-timedelta(days=45)).isoformat())
    result = layers.resolve(subject)
    assert result['established'] == base
    language = next(x for x in result['effective'] if x['factor']=='language')
    assert language['value']=='hi-IN'
    assert len(language['candidates'][0]['observations']) == 1
    assert {'invalid_actor','expired'} <= {x['reason'] for x in result['omitted']}
    updated = layers.promote(subject, first['id'],base['revision'],'PM')
    assert updated['history'][0]['fields']['language']['value']=='en-IN'
    with pytest.raises(ValueError, match='Revision conflict'):
        layers.promote(subject, first['id'],base['revision'],'PM')


def test_outbox_generation_and_event_cursors(services):
    repo = services.repo
    one = repo.put('snapshots',{'id':'s','subject_id':'x','payload':{}})
    row = repo.pending()[0]
    two = repo.put('snapshots',{'id':'s','subject_id':'x','payload':{'changed':True}})
    repo.delivered(row)
    assert repo.pending()[0]['generation'] == two['revision']
    for _ in range(4):
        repo.event('run','node','started')
    assert [x['seq'] for x in repo.events('run',2)] == [3,4]


def test_config_weights_and_scope_locked(services):
    config = services.config.admin
    with pytest.raises(ValueError,match='sum to one'):
        config.update({'operations.persona.established_weight':.7},0)


def test_unauthorized_run_zero_requests(services):
    with pytest.raises(ValueError):
        services.create('real-999','live')
    assert services.repo.list('runs') == []


def test_scenario_preparation_isolated_and_cache(services):
    import asyncio
    run = services.create('demo-any','sample',sources=['company','context'])
    asyncio.run(services.execute(run['id']))
    result = services.inspect(run['id'])
    assert result['status']=='completed', result
    assert len(result['snapshot_ids']) == 2
    assert all(x['subject_id'].startswith('scenario-') for x in services.repo.list('snapshots'))
    assert services.store.get('sellers','demo-any') is None
    again = services.create('demo-any','sample',sources=['company','context'])
    asyncio.run(services.execute(again['id']))
    assert len([x for x in services.repo.events(again['id']) if x['status']=='cache_hit'])==2


def test_disabled_projections_cannot_starve_traces(services):
    for i in range(25):
        services.repo.put('snapshots',{'id':str(i),'subject_id':'s'})
    services.repo.event('r','run','completed')
    rows=services.repo.pending(excluded=['snapshots'])
    assert len(rows)==1 and rows[0]['kind']=='traces'


def test_budget_failed_attempts_and_uncertain_repeat(services):
    receipt={'id':'paid','budget_day':'2026-10-09'}
    first=services.repo.reserve_tool(receipt,2)
    with pytest.raises(ValueError,match='uncertain'):
        services.repo.reserve_tool(receipt,2)
    services.repo.put('tool_runs',{**first,'status':'failed'})
    second=services.repo.reserve_tool(receipt,2)
    services.repo.put('tool_runs',{**second,'status':'failed'})
    with pytest.raises(ValueError,match='budget'):
        services.repo.reserve_tool(receipt,2)


def test_invalid_related_observation_does_not_occlude_seller(services):
    services.layers.observe('s','language','hi-IN',business_id='valid',evidence_keys=['call-1'])
    services.layers.observe('s','language','te-IN',actor='buyer',business_id='invalid',evidence_keys=['call-1'])
    result=services.layers.resolve('s')
    assert next(x for x in result['effective'] if x['factor']=='language')['value']=='hi-IN'


def test_reviewed_buyer_contract_requires_both_approved_identities(services):
    import asyncio,httpx
    from backend.context_adapters import ContextAdapter
    calls=[]
    contract={'buyer_details':{'endpoint':'https://reviewed.example/details','seller_parameter':'seller','buyer_parameter':'buyer','response_identity_pointer':'/id'}}
    adapter=ContextAdapter(services.config,transport=httpx.MockTransport(lambda r:calls.append(r) or httpx.Response(200,json={'id':'123'})),contracts=contract)
    with pytest.raises(ValueError):asyncio.run(adapter.fetch('buyer_details','123','999'))
    assert calls==[]
    assert asyncio.run(adapter.fetch('buyer_details','123','123'))['payload']['id']=='123'
    assert len(calls)==1


def test_force_rerun_bypasses_snapshot_cache(services):
    import asyncio
    first=services.create('demo-a',sources=['company']);asyncio.run(services.execute(first['id']))
    second=services.create('demo-a',sources=['company'],force_sources=True);asyncio.run(services.execute(second['id']))
    assert not any(x['status']=='cache_hit' for x in services.repo.events(second['id']))


def test_revised_observations_and_classification_remain_reviewable(services):
    first=services.layers.observe('s','warmth','warm',business_id='call-1')
    second=services.layers.observe('s','warmth','neutral',business_id='call-1')
    versions=services.repo.history('observations',first['id'])
    assert [x['value'] for x in versions]==['neutral','warm']
    assert second['revision']==2
    value=snapshot({'glid':'123','custtype':53,'custtypeweight':1000},'s','company','live',1)
    assert value['customer_classification']['value']=='unknown'
    assert value['fields']['custtype']['value']==53


def test_progressive_initial_panel_before_slow_source(services):
    import asyncio
    async def exercise():
        original=services.source
        async def slow(*args,**kwargs):
            await asyncio.sleep(.4)
            return await original(*args,**kwargs)
        services.source=slow
        run=services.create('demo-slow',sources=['company'])
        task=asyncio.create_task(services.execute(run['id']))
        await asyncio.sleep(.05)
        pending=services.repo.get('runs',run['id'])
        assert pending['status']=='running' and pending['initial_ready_at'] and pending['teleprompter']['opening']
        await task
    asyncio.run(exercise())


def test_company_facts_publish_while_history_is_pending(services):
    import asyncio
    async def exercise():
        original=services.source
        async def delayed(run,source,sem,**kwargs):
            if source=='redash_hotlead':await asyncio.sleep(.3)
            return await original(run,source,sem,**kwargs)
        services.source=delayed
        run=services.create('demo-progressive',sources=['company','redash_hotlead'])
        task=asyncio.create_task(services.execute(run['id']))
        await asyncio.sleep(.1)
        pending=services.repo.get('runs',run['id'])
        assert pending['status']=='running'
        assert pending['initial_context']['company_name']['availability']=='present'
        assert pending['teleprompter']['opening']
        await task
    asyncio.run(exercise())


def test_cache_expiry_config_revision_and_outage_provenance(services):
    import asyncio
    def prepare():
        run=services.create('demo-cache-policy',sources=['company'])
        asyncio.run(services.execute(run['id']))
        return services.inspect(run['id'])
    first=prepare()
    original=services.repo.get('snapshots',first['snapshot_ids'][0])
    second=prepare()
    assert second['snapshot_ids']==first['snapshot_ids']
    assert second['retrievals'][original['id']]['location']=='sqlite_fallback'
    assert services.repo.get('snapshots',original['id'])['retrieved_at']==original['retrieved_at']
    services.repo.put('snapshots',{**original,'expires_at':'2000-01-01T00:00:00+00:00'})
    expired=prepare()
    assert expired['snapshot_ids']!=first['snapshot_ids']
    admin=services.config.admin
    admin.update({'operations.cache_reads':False},admin.revision)
    uncached=prepare()
    assert not any(x['status']=='cache_hit' for x in uncached['events'])
    admin.update({'operations.cache_reads':True},admin.revision)
    changed=prepare()
    assert not any(x['status']=='cache_hit' for x in changed['events'])
    warm=prepare()
    assert warm['snapshot_ids']==changed['snapshot_ids']


def test_effective_factors_keep_execution_and_evidence_modes_separate(services):
    services.repo.put('runs',{'id':'live-run','subject_id':'123','mode':'live'})
    resolved=services.layers.resolve('123','live-run')
    assert resolved['established']['mode']=='historical'
    assert all(x['mode']=='live' and x['established_mode']=='historical' for x in resolved['effective'])
