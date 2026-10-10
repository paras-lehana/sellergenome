"""Worker-side context client; no default seller and no private payload logging."""
import os
import httpx


class GenomeContext:
    """Fetch by dispatch run ID and apply revisions only at safe turn boundaries."""
    def __init__(self,run_id,session_id,transport=None):
        if not run_id.startswith('run-') or not session_id.startswith('voice-'):
            raise ValueError('Dispatch requires explicit run and voice-session IDs.')
        self.run_id,self.session_id=run_id,session_id
        self.client=httpx.AsyncClient(base_url=os.environ['SELLERGENOME_URL'],
            headers={'X-Operator-Key':os.environ['SELLERGENOME_SERVICE_KEY']},
            timeout=20,transport=transport)
        self.current=None
        self.generation=0

    async def fetch(self):
        response=await self.client.post('/api/voice-adapters/sessions/'+self.session_id+'/context',json={})
        response.raise_for_status()
        return response.json()['run']

    async def apply_at_turn_boundary(self,update_instructions):
        """Developer's agent callback updates its prompt before acknowledging."""
        context=await self.fetch()
        if self.current and context['revision']<=self.current['revision']:
            return False
        await update_instructions(context['layers']['prompt_inspector']['rendered'])
        await self.event('context_ack',revision=context['revision'])
        self.current=context
        return True

    async def event(self,kind,**payload):
        response=await self.client.post('/api/voice-adapters/sessions/'+self.session_id+'/events',json={'type':kind,**payload})
        response.raise_for_status()
        return response.json()

    async def interrupt(self,cancel_pending_audio):
        """Cancel old generated speech before accepting any new audio generation."""
        await cancel_pending_audio()
        result=await self.event('interruption')
        self.generation=result['generation']

    async def close(self):
        await self.client.aclose()
