"""Additive, authenticated V4 operations contracts over shared services."""
import asyncio
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from backend.portal_api import StrictBody


class Preparation(StrictBody):
    seller_id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    mode: Literal['sample','live','local']='sample'
    flow: Literal['seller','buyer']='seller'
    sources: list[str]|None=Field(None,max_length=12)
    use_llm: bool=False

class Observation(StrictBody):
    factor: str=Field(min_length=2,max_length=64)
    value: str|float|bool
    confidence: float=Field(.7,ge=0,le=1)
    author: str=Field('',max_length=80)
    transient: bool=False
    run_id: str|None=None

class Promotion(StrictBody):
    observation_id: str
    expected_revision: int=Field(ge=1)
    reviewer: str=Field(min_length=1,max_length=80)

class BuyerStart(StrictBody):
    flow: Literal['new_requirement','enrichment','resume_identity','connection_retry']='new_requirement'
    language: Literal['en-IN','hi-IN','te-IN']='hi-IN'
    initial: dict=Field(default_factory=dict)
    run_id: str|None=None

class BuyerTurn(StrictBody):
    text: str=Field('',max_length=3000)
    values: dict=Field(default_factory=dict)
    product_change: bool=False
    interrupted: bool=False
    expected_revision: int|None=None

class BuyerTool(StrictBody):
    operation: Literal['product_context','seller_lookup','transfer','submit_lead']
    mode: Literal['sample','live']='sample'
    termination: Literal['tool_owned','explicit_after_success']='tool_owned'

class Meeting(StrictBody):
    subject: str=Field(max_length=100)
    run_id: str|None=None
    start: str=Field(max_length=50)
    online: bool=True
    address: str=Field('',max_length=800)
    mode: Literal['sample','live']='sample'

class Confirm(StrictBody):
    expected_revision: int=Field(ge=1)

class VoicePrepare(StrictBody):
    run_id: str
    adapter: Literal['sarvam_websocket','livekit','sarvam_voice_agents']

class VoiceEvent(StrictBody):
    type: str=Field(max_length=50)
    revision: int|None=None
    generation: int|None=None
    text: str|None=Field(None,max_length=4000)
    method: str|None=Field(None,max_length=100)
    confidence: float|None=Field(None,ge=0,le=1)

class ComparisonRun(StrictBody):
    flow: Literal["new_requirement","enrichment","resume_identity","connection_retry"]="new_requirement"
    scenario_id: str

class ToolRun(StrictBody):
    mode: Literal["demo","live"]="demo"
    language: Literal["en-IN","hi-IN","te-IN"]|None=None

class Fork(StrictBody):
    payload: dict

class Address(StrictBody):
    address: str=Field(min_length=5,max_length=800)
    confirmed: bool=False


def operations_router(resolve,operator,is_operator):
    """Resolve service dependencies per lifespan, preserving isolated API tests."""
    router=APIRouter()
    def service(request):
        operator(request)
        return resolve()

    @router.get('/api/operations/activities')
    async def activities(request:Request,q:str='',flow:Literal['seller','buyer']='seller'):
        if len(q)>100:
            raise HTTPException(422,'Search is too long.')
        return resolve().activities(q,private=is_operator(request),flow=flow)

    @router.get('/api/operations/runs')
    async def runs(request:Request):
        return {'items':service(request).repo.list('runs',limit=50)}

    @router.post('/api/operations/example-replay')
    async def example(request:Request):
        import os
        from pathlib import Path
        ops=service(request)
        path=Path(os.getenv('SELLER_PRODUCTION_EXAMPLE_PATH','/opt/sellergenome/private/inputs/production/seller_persona_prod.json'))
        if not path.is_file():
            raise HTTPException(503,'The private production example is not mounted.')
        return ops.replay(path)

    @router.get('/api/buyer/leads')
    async def leads(request:Request):
        return {'items':service(request).repo.list('buyer_states',limit=100)}

    @router.post('/api/meetings/address-candidates')
    async def address(body:Address,request:Request):
        ops=service(request)
        if not body.confirmed:
            raise HTTPException(422,'Confirm the seller address before geocoding.')
        return await ops.actions.geocode(body.address)

    @router.post('/api/operations/runs',status_code=202)
    async def create(body:Preparation,request:Request):
        try:
            return service(request).create(**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/operations/runs/{identifier}')
    async def inspect(identifier:str,request:Request):
        try:
            return service(request).inspect(identifier)
        except ValueError as error:
            raise HTTPException(404,str(error)) from error

    @router.get('/api/operations/runs/{identifier}/events')
    async def events(identifier:str,request:Request,after:int=0):
        ops=service(request)
        if not ops.repo.get('runs',identifier):
            raise HTTPException(404,'Run not found.')
        try:
            cursor=max(after,int(request.headers.get('last-event-id','0')))
        except ValueError:
            raise HTTPException(422,'Invalid event cursor.')
        async def stream():
            nonlocal cursor
            for _ in range(120):
                if await request.is_disconnected() or not is_operator(request):
                    break
                for event in ops.repo.events(identifier,cursor):
                    cursor=event['seq']
                    yield f'id: {cursor}\ndata: {json.dumps(event,ensure_ascii=False)}\n\n'
                run=ops.repo.get('runs',identifier)
                if run['status'] not in {'queued','running'}:
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(.5)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @router.get('/api/context-snapshots/{identifier}')
    async def context(identifier:str,request:Request,run_id:str|None=None):
        ops=service(request)
        result=ops.repo.get('snapshots',identifier)
        if not result:
            raise HTTPException(404,'Context snapshot not found.')
        if run_id:
            run=ops.repo.get('runs',run_id)
            if not run or identifier not in run['snapshot_ids']:
                raise HTTPException(422,'Snapshot does not belong to this run.')
            retrieval=run.get('retrievals',{}).get(identifier)
            if retrieval:
                result['retrieval_receipt']=retrieval
                for field in result['fields'].values():
                    field['retrieval']=retrieval['location']
                    field['retrieved_at']=retrieval['at']
        return result

    @router.post('/api/context-snapshots/{identifier}/fork')
    async def fork(identifier:str,body:Fork,request:Request):
        ops=service(request)
        original=ops.repo.get('snapshots',identifier)
        if not original:
            raise HTTPException(404,'Snapshot not found.')
        if len(json.dumps(body.payload))>200000:
            raise HTTPException(422,'Scenario payload too large.')
        from backend.context_contract import snapshot
        forked=snapshot(body.payload,'scenario-fork-'+identifier,original['source'],'sample',ops.config.admin.revision,retrieval='operator_scenario')
        forked['fork_of']=identifier
        return ops.repo.put('snapshots',forked)

    @router.post('/api/operations/runs/{identifier}/nodes/{node}/rerun')
    async def rerun(identifier:str,node:str,request:Request):
        ops=service(request)
        run=ops.repo.get('runs',identifier)
        if not run or node not in run['sources']:
            raise HTTPException(422,'Select a recorded source node.')
        try:
            fresh=ops.create(run['seller_id'],run['mode'],run['flow'],[node],False,force_sources=True)
            return fresh
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/personas/{identifier}/layers')
    async def layers(identifier:str,request:Request,run_id:str|None=None):
        return service(request).layers.resolve(identifier,run_id)

    @router.post('/api/personas/{identifier}/observations')
    async def observation(identifier:str,body:Observation,request:Request):
        try:
            ops = service(request)
            result = ops.layers.observe(identifier,**body.model_dump())
            if body.run_id:
                run = ops.repo.get('runs', body.run_id)
                if run and run['subject_id'] == identifier:
                    layers = ops.layers.resolve(identifier, body.run_id)
                    snapshots=[ops.repo.get('snapshots',sid) for sid in run['snapshot_ids']]
                    ops.save_run(body.run_id, layers=layers,teleprompter=ops.teleprompter(run,[x for x in snapshots if x and x['subject_id']==identifier],layers))
                    for factor in layers['effective']:
                        ops.repo.put('factors', factor)
            return result
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.post('/api/personas/{identifier}/promotions')
    async def promote(identifier:str,body:Promotion,request:Request):
        try:
            return service(request).layers.promote(identifier,**body.model_dump())
        except ValueError as error:
            raise HTTPException(409,str(error)) from error

    @router.get('/api/personas/{identifier}/observations/{observation_id}/history')
    async def observation_history(identifier:str,observation_id:str,request:Request):
        ops=service(request)
        observation=ops.repo.get('observations',observation_id)
        if not observation or observation['subject_id']!=identifier:
            raise HTTPException(404,'Observation not found.')
        return {'items':ops.repo.history('observations',observation_id)}

    @router.get('/api/operations/indexing')
    async def indexing(request:Request):
        ops=service(request)
        return {'status':ops.index.status,'backlog':ops.repo.backlog()}

    @router.get('/api/personas/factors/search')
    async def factor_search(request:Request,factor:str='',value:str='',mode:Literal['sample','local','live','historical']|None=None):
        ops=service(request)
        if len(factor)>64 or len(value)>200:
            raise HTTPException(422,'Factor search exceeds its bound.')
        filters=[]
        if factor: filters.append({'term':{'factor':factor}})
        if mode: filters.append({'term':{'mode':mode}})
        query={'bool':{'filter':filters,'must':[{'match':{'value_text':value}}] if value else []}}
        if not ops.config.es_url:
            raise HTTPException(503,'Elasticsearch is not configured; no search success is claimed.')
        try:
            async with ops.search.client() as client:
                response=await client.post('/'+ops.index.alias('factors')+'/_search',json={'size':50,'track_total_hits':True,'query':query,'sort':[{'at':'desc'}]})
                response.raise_for_status()
                data=response.json()
            return {'items':[x['_source']['document'] for x in data['hits']['hits']],'total':data['hits']['total']['value'],'retrieval':'elasticsearch','query':query,'alias':ops.index.alias('factors')}
        except Exception as error:
            raise HTTPException(503,'Elasticsearch factor search failed; durable observations remain available.') from error

    @router.post('/api/operations/indexing/recover')
    async def recover(request:Request):
        ops=service(request)
        await ops.index.deliver()
        return {'status':ops.index.status,'backlog':ops.repo.backlog()}

    @router.post('/api/buyer/conversations')
    async def buyer_create(body:BuyerStart,request:Request):
        try:
            return service(request).buyer.create(**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/buyer/conversations/{identifier}')
    async def buyer_get(identifier:str,request:Request):
        ops=service(request)
        state=ops.repo.get('buyer_states',identifier)
        if not state:
            raise HTTPException(404,'Buyer state not found.')
        return {**state,'compatibility':ops.buyer.compatibility(state)}

    @router.post('/api/buyer/conversations/{identifier}/turns')
    async def buyer_turn(identifier:str,body:BuyerTurn,request:Request):
        try:
            return service(request).buyer.turn(identifier,**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.post('/api/buyer/conversations/{identifier}/tools')
    async def buyer_tool(identifier:str,body:BuyerTool,request:Request):
        try:
            return service(request).buyer.tool(identifier,**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/meetings')
    async def meetings(request:Request):
        return {'items':service(request).repo.list('actions')}

    @router.post('/api/meetings')
    async def propose(body:Meeting,request:Request):
        try:
            return service(request).actions.propose(**body.model_dump())
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.post('/api/meetings/{identifier}/confirm')
    async def confirm(identifier:str,body:Confirm,request:Request):
        try:
            return service(request).actions.confirm(identifier,body.expected_revision)
        except ValueError as error:
            raise HTTPException(409,str(error)) from error

    @router.post('/api/meetings/{identifier}/book')
    async def book(identifier:str,request:Request):
        try:
            return await service(request).actions.book(identifier)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/voice-adapters')
    async def voice_adapters(request:Request):
        return {'items':service(request).voice.inventory()}

    @router.post('/api/voice-adapters/sessions')
    async def voice_prepare(body:VoicePrepare,request:Request):
        ops=service(request)
        run=ops.repo.get('runs',body.run_id)
        if not run:
            raise HTTPException(404,'Run not found.')
        return await ops.voice.prepare(run,body.adapter)

    @router.post('/api/voice-adapters/sessions/{identifier}/context')
    async def voice_context(identifier: str, request: Request):
        ops = service(request)
        session = ops.repo.get('voice_sessions', identifier)
        if not session:
            raise HTTPException(404, 'Voice session not found.')
        run = ops.repo.get('runs', session['run_id'])
        if run['revision'] != session['context_revision']:
            session = ops.repo.put('voice_sessions', {**session, 'context_revision':run['revision'], 'acknowledged_revision':None, 'status':'context_pending'}, expected=session['revision'])
        return {'session':session, 'run':run}

    @router.post('/api/voice-adapters/sessions/{identifier}/events')
    async def voice_event(identifier:str,body:VoiceEvent,request:Request):
        try:
            return service(request).voice.event(identifier,body.model_dump(exclude_none=True))
        except ValueError as error:
            raise HTTPException(422,str(error)) from error

    @router.get('/api/operations/schema')
    async def schema(request:Request):
        service(request)
        return {model.__name__:model.model_json_schema() for model in [Preparation,Observation,BuyerStart,BuyerTurn,Meeting,VoiceEvent]}

    @router.get('/api/buyer/comparison')
    async def comparison(request: Request, flow: str = 'new_requirement'):
        service(request)
        from backend.buyer_comparison import render_original, comparison_cases
        try:
            return {'original': render_original(flow), 'scenarios': comparison_cases()}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post('/api/buyer/comparison')
    async def comparison_run(body: ComparisonRun, request: Request):
        ops = service(request)
        from backend.buyer_comparison import ComparisonService
        try:
            return await ComparisonService(ops.config, ops.repo).replay(body.flow, body.scenario_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/api/operations/packages')
    async def packages(request: Request):
        ops = service(request)
        catalog = ops.config.values['operations']['packages']
        if catalog['mode']!='sample':
            return {'mode':catalog['mode'],'status':'not_configured' if catalog['mode']=='live' else 'disabled','items':[],'limitation':'A verified live package-catalog contract is required; no sample prices are presented as live.'}
        return {'mode': catalog['mode'], 'items': [catalog['entry']],
                'limitation': 'Only verified catalog prices may be announced. Zero unverified price is not a free package.'}

    @router.get('/api/sarvam/runs/{identifier}/audio')
    async def tool_audio(identifier: str, request: Request):
        from fastapi.responses import FileResponse
        ops = service(request)
        receipt = ops.repo.get('tool_runs', identifier)
        name = (receipt or {}).get('audio_file', '')
        path = (ops.config.data_dir / 'sarvam-mcp' / name).resolve()
        if not name or path.parent != (ops.config.data_dir / 'sarvam-mcp').resolve() or not path.is_file():
            raise HTTPException(404, 'Audio artifact not found.')
        return FileResponse(path, media_type='audio/wav' if path.suffix=='.wav' else 'audio/mpeg', headers={'Cache-Control':'private, no-store'})

    @router.get('/api/sarvam/tools')
    async def sarvam_tools(request: Request):
        return service(request).sarvam_lab.inventory()

    @router.post('/api/sarvam/tools/{name}/run')
    async def sarvam_run(name: str, body: ToolRun, request: Request):
        try:
            return await service(request).sarvam_lab.run(name, body.mode, body.language)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
