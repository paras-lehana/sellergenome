"""SDK1.1.1 hooks: deploy only in the verified hackathon workspace."""
from sarvam_conv_ai_sdk import SarvamOnStartTool,SarvamOnEndTool
from integrations.sarvam_code_tools.client import load_context,persist_outcome

class OnStart(SarvamOnStartTool):
    async def run(self,context):
        run_id=context.get_agent_variable('run_id')
        if not run_id: raise ValueError('An explicit run_id is required.')
        run=await load_context(context,run_id)
        context.set_agent_variable('subject_id',run['subject_id'])
        context.set_agent_variable('seller_context_revision',run['revision'])
        context.set_agent_variable('seller_persona_sections',run.get('layers',{}).get('prompt_inspector',{}).get('rendered','Confirm identity and language; no inferred preference is available.'))
        return context

class OnEnd(SarvamOnEndTool):
    async def run(self,context):
        voice_id=context.get_agent_variable('voice_session_id')
        if voice_id:
            await persist_outcome(context,voice_id,str(context.get_agent_variable('call_outcome') or 'ended_without_confirmed_business_outcome')[:4000])
        return context
