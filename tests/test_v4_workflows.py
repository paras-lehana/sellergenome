"""Buyer limits, action idempotence, identity and voice contract regressions."""
import asyncio
import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from test_v4_context import services
from backend.buyer_workflow import BuyerEngine
from backend.test_actions import ActionService
from backend.voice_adapters import VoiceAdapters,signed_token
from backend.connectors import ConnectorService,ConnectorError


def test_buyer_short_yes_requires_product_and_one_question(services):
    buyer=BuyerEngine(services.config,services.repo)
    state=buyer.create(language='en-IN')
    assert state['mode']=='sample' and state['simulation'] is True
    state=buyer.turn(state['id'],'yes')
    assert not state['product_confirmed'] and not state['fields'].get('product')
    state=buyer.turn(state['id'],'Packaging machine')
    assert state['last_question']=='confirm_product'
    state=buyer.turn(state['id'],'yes')
    assert state['product_confirmed'] and state['last_question']=='quantity'
    state=buyer.turn(state['id'],'10 to 20 boxes')
    assert buyer.compatibility(state)['quantity']==20
    assert state['fields']['quantity']=='10 to 20 boxes'


def test_buyer_attempts_survive_interruption_and_exhaust(services):
    buyer=BuyerEngine(services.config,services.repo)
    state=buyer.create()
    state=buyer.turn(state['id'],'background noise',interrupted=True)
    assert state['attempts']['product']==1
    for _ in range(4):
        state=buyer.turn(state['id'],'hello')
    assert state['status']=='ended' and state['attempts']['product']==4
    assert state['termination_count']==1
    assert buyer.turn(state['id'],'hello')['termination_count']==1


@pytest.mark.parametrize('flow',['new_requirement','enrichment','resume_identity','connection_retry'])
def test_all_buyer_modes_transfer_is_single_terminal_action(services,flow):
    buyer=BuyerEngine(services.config,services.repo)
    state=buyer.create(flow,'en-IN',{'product':'Packaging machine','product_confirmed':True,'quantity':'2','name':'Asha','city':'Pune','state':'Maharashtra'})
    state=buyer.tool(state['id'],'seller_lookup')
    with pytest.raises(ValueError,match='consent'):
        buyer.tool(state['id'],'transfer')
    state=buyer.turn(state['id'],'yes')
    state=buyer.tool(state['id'],'transfer')
    assert state['termination_count']==1 and state['transfer_status']=='succeeded'
    assert buyer.tool(state['id'],'transfer')['termination_count']==1
    assert state['submission_status']=='draft'


def test_location_and_submission_are_separate(services):
    buyer=BuyerEngine(services.config,services.repo)
    state=buyer.create('resume_identity','hi-IN',{'product':'Pump'})
    with pytest.raises(ValueError,match='city and state'):
        buyer.tool(state['id'],'seller_lookup')
    state=buyer.tool(state['id'],'submit_lead')
    assert state['submission_status']=='sample_accepted'
    assert state['transfer_status']=='not_requested'
    live=buyer.tool(state['id'],'submit_lead',mode='live')
    assert live['status']=='not_configured'


def test_sample_booking_idempotent_and_no_fake_meet(services):
    action=ActionService(services.config,services.repo)
    start=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    proposed=action.propose('scenario-seller',None,start,True)
    with pytest.raises(ValueError,match='confirmation'):
        asyncio.run(action.book(proposed['id']))
    confirmed=action.confirm(proposed['id'],proposed['revision'])
    booked=asyncio.run(action.book(confirmed['id']))
    again=asyncio.run(action.book(confirmed['id']))
    assert booked==again
    assert booked['meeting_link'] is None
    assert booked['external_receipt']['customer_invited'] is False
    duplicate=action.propose('scenario-seller',None,start,True)
    action.confirm(duplicate['id'],duplicate['revision'])
    assert asyncio.run(action.book(duplicate['id']))['id']==booked['id']


def test_voice_stale_revision_and_audio_after_interrupt(services):
    voice=VoiceAdapters(services.config,services.repo)
    session=asyncio.run(voice.prepare({'id':'run-1','subject_id':'scenario-a','revision':4},'livekit'))
    assert session['status']=='awaiting_connection'
    with pytest.raises(ValueError,match='Stale context'):
        voice.event(session['id'],{'type':'context_ack','revision':3})
    voice.event(session['id'],{'type':'interruption'})
    with pytest.raises(ValueError,match='Stale audio'):
        voice.event(session['id'],{'type':'speech_started','generation':0})


def test_company_two_hops_mismatch_and_redacted_token(services,monkeypatch):
    monkeypatch.setenv('COMPANY_TOKEN','secret-test-value')
    monkeypatch.setenv('COMPANY_BASE_URL','https://company.test')
    config=services.config.values['connectors']; config['sources']['company']['mode']='live'
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'URL_DETAIL':{'FREESHOWROOM_ALIAS':'test-company'}})
        return httpx.Response(200,json={'DATA':{'COMPANYDETAIL':{'GLUSRID':'123','DIR_SEARCH_COMPANY':'Synthetic Company'}}})
    connector=ConnectorService(config,transport=httpx.MockTransport(handler))
    asyncio.run(connector.fetch('company','real-123'))
    assert len(calls)==2
    assert 'secret-test-value' not in json.dumps(connector.exchanges)
    calls.clear()
    def wrong(request):
        calls.append(request)
        return httpx.Response(200,json={'URL_DETAIL':{'FREESHOWROOM_ALIAS':'test-company','GLUSRID':'999'}})
    with pytest.raises(ConnectorError,match='identity'):
        asyncio.run(ConnectorService(config,transport=httpx.MockTransport(wrong)).fetch('company','real-123'))
    assert len(calls)==1


def test_refusal_and_any_do_not_become_location_or_quantity(services):
    engine=BuyerEngine(services.config,services.repo)
    state=engine.create('new_requirement','en-IN',{'product':'Tools','product_confirmed':True})
    state=engine.turn(state['id'],'ask the seller')
    assert state['closed_fields']['quantity']=='seller_deferred'
    assert 'quantity' not in state['fields']
    state=engine.turn(state['id'],'prefer not to say')
    assert state['closed_fields']['name']=='refused'
    assert 'name' not in state['fields']


def test_teleprompter_language_warmth_formality_change_wording(services):
    run={'id':'r'}
    def layers(language,warmth,formality):return {'effective':[{'factor':k,'value':v} for k,v in [('language',language),('warmth',warmth),('formality',formality)]]}
    english=services.teleprompter(run,[],layers('en-IN','neutral','formal'))
    warm=services.teleprompter(run,[],layers('en-IN','warm','conversational'))
    hindi=services.teleprompter(run,[],layers('hi-IN','warm','formal'))
    telugu=services.teleprompter(run,[],layers('te-IN','warm','formal'))
    assert english['opening']!=warm['opening']
    assert 'AI' in hindi['opening'] and 'నమస్కారం' in telugu['opening']


def test_calendar_owned_test_scope_and_pending_conference_recovery(services,monkeypatch):
    monkeypatch.setattr('backend.test_actions.secret_value',lambda name:'test-token' if name=='GOOGLE_CALENDAR_ACCESS_TOKEN' else '')
    settings=services.config.values['operations']['actions'];settings['calendar_id']='dedicated-test@group.calendar.google.com'
    events={};posts=[];conference_ready=False
    def handler(request):
        if request.url.path.endswith('/calendarList/primary'):return httpx.Response(200,json={'id':'paras@lehana.in'})
        if '/calendarList/' in request.url.path:return httpx.Response(200,json={'accessRole':'owner','summary':'SellerGenome Test'})
        if request.url.path.endswith('/freeBusy'):return httpx.Response(200,json={'calendars':{settings['calendar_id']:{'busy':[]}}})
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body);events[body['id']]={**body,'conferenceData':{'createRequest':{'status':{'statusCode':'pending'}}}}
            return httpx.Response(200,json=events[body['id']])
        identifier=request.url.path.rsplit('/',1)[-1]
        if identifier not in events:return httpx.Response(404)
        result=events[identifier]
        if conference_ready:result={**result,'conferenceData':{'entryPoints':[{'entryPointType':'video','uri':'https://meet.google.com/test-fixture'}]}}
        return httpx.Response(200,json=result)
    action=ActionService(services.config,services.repo,httpx.MockTransport(handler))
    proposal=action.propose('real-123',None,(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),True,mode='live')
    action.confirm(proposal['id'],proposal['revision'])
    booked=asyncio.run(action.book(proposal['id']))
    assert booked['conference_status']=='pending' and len(posts)==1
    conference_ready=True
    ready=asyncio.run(action.book(proposal['id']))
    assert ready['conference_status']=='ready' and len(posts)==1
    assert 'attendees' not in posts[0]


def test_designated_speech_does_not_fallback_to_old_accounts(services,monkeypatch):
    from backend.sarvam import SarvamPool
    monkeypatch.setenv('SARVAM_SPEECH_SCOPE','designated_v4')
    monkeypatch.setattr('backend.sarvam.secret_value',lambda name,version=None:'old-pinned' if version else 'new-org-key' if name=='SARVAM_V4_API_KEY' else 'old-account')
    pool=SarvamPool(services.config,services.store)
    assert pool.credential('primary')=='new-org-key'
    assert pool.credential('backup')==''
    assert pool.credential('primary','immutable-old-version')=='old-pinned'
