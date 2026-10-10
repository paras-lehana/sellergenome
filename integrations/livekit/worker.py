"""Named dispatch reference. The other developer supplies the conversation agent.

This worker verifies the handoff and context contract. It deliberately does not
choose another provider/account or claim to be the completed LiveKit voice bot.
"""
import json
import os
from livekit import agents
from integrations.livekit.handoff import GenomeContext

server=agents.AgentServer()


@server.rtc_session(agent_name=os.getenv('LIVEKIT_AGENT_NAME','sellergenome-agent'))
async def entrypoint(ctx:agents.JobContext):
    metadata=json.loads(ctx.job.metadata or '{}')
    bridge=GenomeContext(metadata['run_id'],metadata['voice_session_id'])
    await ctx.connect()
    try:
        context=await bridge.fetch()
        # Replace this callback with the developer's Agent.update_instructions.
        # ACK is issued only after that agent accepts the rendered context.
        await ctx.room.local_participant.publish_data(json.dumps({
            'type':'context_available','run_id':context['id'],
            'revision':context['revision'],'status':'awaiting_voice_worker_implementation'}),topic='sellergenome.context')
    finally:
        await bridge.close()


if __name__=='__main__':
    agents.cli.run_app(server)
