"""Independent offline contract check: no LiveKit account or paid models needed."""
import asyncio
import os
import httpx
from integrations.livekit.handoff import GenomeContext

async def main():
    os.environ.setdefault('SELLERGENOME_URL','https://portal.test')
    os.environ.setdefault('SELLERGENOME_SERVICE_KEY','offline-harness-only')
    events=[]
    def handler(request):
        if request.url.path.endswith('/context'):
            return httpx.Response(200,json={'run':{'id':'run-test','revision':3,'layers':{'prompt_inspector':{'rendered':'AI disclosure. Confirm language; honor stop requests.'}}}})
        import json
        value=json.loads(request.content);events.append(value)
        return httpx.Response(200,json={'generation':1})
    bridge=GenomeContext('run-test','voice-test',transport=httpx.MockTransport(handler))
    prompts=[]
    async def apply(prompt):prompts.append(prompt)
    async def cancel():events.append({'type':'cancel_pending_audio'})
    assert await bridge.apply_at_turn_boundary(apply)
    assert not await bridge.apply_at_turn_boundary(apply)
    await bridge.interrupt(cancel)
    assert events[-2]['type']=='cancel_pending_audio'
    assert events[-1]['type']=='interruption'
    assert bridge.generation==1
    await bridge.close()
    print('LiveKit handoff: context fetch, safe-boundary ACK, duplicate revision and interruption contract passed.')

if __name__=='__main__':asyncio.run(main())
