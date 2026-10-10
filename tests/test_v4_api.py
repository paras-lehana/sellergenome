"""Public/privacy boundaries and real run/inspector API contracts."""
import time
import pytest
from test_v3_portal import portal,login


def test_operations_auth_and_sample_run_receipts(portal):
    # V5 owns '/'; the V4 console is preserved under /v4 (and /operations).
    assert portal.get('/v4').status_code==200
    assert 'operations.js' in portal.get('/v4').text and 'operations.js' in portal.get('/operations').text
    assert portal.get('/api/operations/activities').json()['simulation'] is True
    for path in ['/api/operations/runs','/api/sarvam/tools','/api/meetings','/api/voice-adapters']:
        assert portal.get(path).status_code==401
    login(portal)
    result=portal.post('/api/operations/runs',json={'seller_id':'demo-asha','mode':'sample','sources':['company','context']})
    assert result.status_code==202,result.text
    identifier=result.json()['id']
    for _ in range(60):
        run=portal.get('/api/operations/runs/'+identifier).json()
        if run['status'] not in {'queued','running'}:break
        time.sleep(.05)
    assert run['status']=='completed',run
    assert len(run['snapshot_ids'])==2
    assert run['layers']['established']['subject_id'].startswith('scenario-')
    source=portal.get('/api/context-snapshots/'+run['snapshot_ids'][0]).json()
    assert source['fields']['local_buylead_count']['value']==0
    assert source['fields']['personalise']['value'] is True
    fork=portal.post('/api/context-snapshots/'+source['id']+'/fork',json={'payload':{'glid':'123','company_name':'Scenario fork'}})
    assert fork.status_code==200
    assert fork.json()['subject_id'].startswith('scenario-fork-')
    assert portal.get('/api/context-snapshots/'+source['id']).json()['payload']==source['payload']
    assert portal.post('/api/operations/runs/'+identifier+'/nodes/arbitrary_url/rerun').status_code==422
    assert portal.post('/api/operations/runs',json={'seller_id':'real-999','mode':'live'}).status_code==422


def test_all_discovered_mcp_demos_have_receipts_without_provider_calls(portal):
    login(portal)
    tools=portal.get('/api/sarvam/tools').json()['items']
    assert len(tools)==36
    for tool in tools:
        result=portal.post('/api/sarvam/tools/'+tool['name']+'/run',json={'mode':'demo'})
        assert result.status_code==200,result.text
        assert result.json()['result']['provider_called'] is False
        assert result.json()['status']=='illustrative_demo'
    assert portal.post('/api/sarvam/tools/not_a_tool/run',json={'mode':'live'}).status_code==422


def test_new_routes_legacy_routes_and_schema(portal):
    for route in ['/operations','/studio','/buyer','/meetings','/sarvam','/admin','/walkthrough','/analysis','/pm']:
        assert portal.get(route).status_code==200
    for route in ['/operations/','/studio/','/buyer/','/meetings/','/sarvam/','/admin/']:
        response=portal.get(route,follow_redirects=False)
        assert response.status_code==200
        assert 'location' not in response.headers
    login(portal)
    schema=portal.get('/api/operations/schema').json()
    assert 'Preparation' in schema and 'VoiceEvent' in schema
    assert portal.get('/api/operations/packages').json()['items'][0]['price_verified'] is False


def test_feedback_anchors_validate_real_operations_targets(portal):
    login(portal)
    body={'target_id':'v4.dashboard.feed','mode':'demo','category':'idea','text':'Improve source coverage labeling','breadcrumb':'Untrusted label'}
    result=portal.post('/api/feedback',json=body)
    assert result.status_code==201,result.text
    assert result.json()['breadcrumb']=='Operations / Activity feed'
    assert portal.post('/api/feedback',json={**body,'target_id':'v4.run.nonexistent.persona'}).status_code==422
    assert portal.post('/api/feedback',json={**body,'run_id':'made-up'}).status_code==422


def test_v4_rehearsal_uses_effective_context_and_isolates_sample_stops(portal):
    login(portal)
    run=portal.post('/api/operations/runs',json={'seller_id':'demo-asha','sources':['company'],'mode':'sample'}).json()
    for _ in range(60):
        result=portal.get('/api/operations/runs/'+run['id']).json()
        if result['status']=='completed':break
        time.sleep(.05)
    session=portal.post('/api/operations/runs/'+run['id']+'/rehearsal')
    assert session.status_code==200,session.text
    payload=session.json()
    assert payload['seller_id']==result['subject_id']
    assert payload['operations_run_id']==run['id']
    assert payload['turns'][0]['text']==result['teleprompter']['opening']
    assert portal.get('/api/sessions/'+payload['id']).status_code==200
    ended=portal.post('/api/sessions/'+payload['id']+'/turns',json={'text':'Please do not call me again.'})
    assert ended.status_code==200,ended.text
    assert not portal.get('/api/sellers/demo-asha').json().get('metrics',{}).get('do_not_call')
