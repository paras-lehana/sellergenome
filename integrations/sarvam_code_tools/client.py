"""Workspace Code Tools call the same typed portal services; secrets stay private."""
import httpx


async def call_portal(context,path,payload=None):
    """Resolve workspace secret references; never copy keys into agent variables."""
    if context.get_secret('SELLERGENOME_ORGANIZATION_ID')!='01a110b3-f038-7adb-b627-6863a3785c40':
        raise ValueError('Code Tools are restricted to the verified hackathon workspace.')
    base=context.get_secret('SELLERGENOME_URL')
    key=context.get_secret('SELLERGENOME_SERVICE_KEY')
    async with httpx.AsyncClient(base_url=base,headers={'X-Operator-Key':key},timeout=25) as client:
        response=await client.request('POST' if payload is not None else 'GET',path,json=payload)
        response.raise_for_status()
        return response.json()


async def load_context(context,run_id):
    return await call_portal(context,'/api/operations/runs/'+run_id)

async def additional_evidence(context,snapshot_id):
    run=await load_context(context,context.get_agent_variable('run_id'))
    if snapshot_id not in [*run.get('snapshot_ids',[]),*run.get('related_buyer_snapshot_ids',[])]:
        raise ValueError('Evidence must belong to this preparation run.')
    return await call_portal(context,'/api/context-snapshots/'+snapshot_id)

async def update_persona(context,subject,factor,value,run_id):
    run=await load_context(context,run_id)
    if subject!=run['subject_id']:
        raise ValueError('Persona updates must belong to this preparation run.')
    return await call_portal(context,'/api/personas/'+subject+'/observations',{'factor':factor,'value':value,'author':'Sarvam Code Tool','run_id':run_id,'transient':True})

async def package_lookup(context):
    return await call_portal(context,'/api/operations/packages')

async def propose_meeting(context,payload):
    # Test-only payload validation/identity enforcement happens server-side.
    return await call_portal(context,'/api/meetings',payload)

async def buyer_action(context,conversation_id,operation):
    return await call_portal(context,'/api/buyer/conversations/'+conversation_id+'/tools',{'operation':operation,'mode':'sample'})

async def persist_outcome(context,voice_session_id,text):
    return await call_portal(context,'/api/voice-adapters/sessions/'+voice_session_id+'/events',{'type':'outcome','text':text})
