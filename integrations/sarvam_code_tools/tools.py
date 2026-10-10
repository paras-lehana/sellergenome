"""Typed code-tool classes. Upload each class with client.py in its code bundle."""
import json
from pydantic import Field
from sarvam_conv_ai_sdk import SarvamTool,SarvamToolOutput
from integrations.sarvam_code_tools import client


def output(context,value):
    return SarvamToolOutput(context=context,message_to_llm=json.dumps(value,ensure_ascii=False)[:20000])

class LoadContext(SarvamTool):
    async def run(self,context):
        return output(context,await client.load_context(context,context.get_agent_variable('run_id')))

class AdditionalEvidence(SarvamTool):
    snapshot_id:str=Field(description='Snapshot identifier cited by the current run.')
    async def run(self,context):
        return output(context,await client.additional_evidence(context,self.snapshot_id))

class UpdatePersona(SarvamTool):
    factor:str
    value:str|float|bool
    async def run(self,context):
        return output(context,await client.update_persona(context,context.get_agent_variable('subject_id'),self.factor,self.value,context.get_agent_variable('run_id')))

class PackageLookup(SarvamTool):
    async def run(self,context):
        return output(context,await client.package_lookup(context))

class MeetingProposal(SarvamTool):
    start:str=Field(description='Explicit seller-confirmed time with timezone.')
    online:bool=True
    address:str=''
    async def run(self,context):
        return output(context,await client.propose_meeting(context,{'subject':context.get_agent_variable('subject_id'),'run_id':context.get_agent_variable('run_id'),'start':self.start,'online':self.online,'address':self.address,'mode':'sample'}))

class BuyerRequirementAction(SarvamTool):
    operation:str=Field(description='product_context, seller_lookup, transfer or submit_lead; the server enforces consent and product state.')
    async def run(self,context):
        return output(context,await client.buyer_action(context,context.get_agent_variable('buyer_conversation_id'),self.operation))

class PersistOutcome(SarvamTool):
    outcome:str=Field(max_length=4000)
    async def run(self,context):
        return output(context,await client.persist_outcome(context,context.get_agent_variable('voice_session_id'),self.outcome))
