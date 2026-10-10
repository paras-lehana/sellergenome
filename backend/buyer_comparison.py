"""Preserved source prompt, documented rendering and deterministic policy probes."""
import hashlib
import os
import re
from pathlib import Path
from backend.context_contract import now

FLOW_RANGES={'new_requirement':(0,1243),'enrichment':(1243,1898),
             'resume_identity':(1898,2243),'connection_retry':(2243,None)}


def render_original(flow,variables=None):
    """Return original workflow text plus an explicit adaptation manifest."""
    if flow not in FLOW_RANGES:
        raise ValueError('Unknown original workflow.')
    path=Path(os.getenv('BUYER_ORIGINAL_PROMPT_PATH','/opt/sellergenome/private/inputs/production/Sarvam Prompt - Buyer Side VANI.docx.md'))
    if not path.exists():
        return {'status':'not_configured','reason':'Mount the preserved private buyer prompt to enable baseline inspection.'}
    raw=path.read_text();lines=raw.splitlines();start,end=FLOW_RANGES[flow]
    excerpt='\n'.join(lines[start:end])
    variables=variables or {}
    # Preserve body verbatim. Context is an appended manifest, not destructive replacement.
    return {'status':'available','flow':flow,'sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'original_text':excerpt,'line_start':start+1,'line_end':end or len(lines),
            'variables':variables,'adaptations':['Selected the corresponding original workflow by preserved line range.',
                'No original wording rewritten. Runtime variables are provided separately.',
                'External tools are mocked in comparison; this is not production-bot recording.'],
            'original_word_count':len(excerpt.split())}


def comparison_cases():
    """Explicit scenarios reveal policy differences without fabricating model runs."""
    return [
        {'id':'greeting','input':['Hello','yes'],'original_risk':'Short answers may be accepted without the appropriate preceding question.','corrected_rule':'Product confirmation requires an identified product and prior confirmation question.','metric':'unsupported_product_confirmation'},
        {'id':'attempts','input':['noise','interruption','hello','hello','hello'],'original_risk':'Handler-local questions can repeat after interruption.','corrected_rule':'Persistent top-level product allowance is four; interruption does not reset it.','metric':'question_repetition'},
        {'id':'location','input':['I am in Pune','connect me'],'original_risk':'Location and transfer branches can diverge.','corrected_rule':'Seller lookup requires city and state; transfer needs an availability receipt and consent.','metric':'invalid_tool_sequence'},
        {'id':'termination','input':['yes, connect me'],'original_risk':'Source workflows disagree whether assisted-buy owns termination.','corrected_rule':'Tool declares termination ownership; terminal event emitted exactly once.','metric':'duplicate_termination'},
        {'id':'promises','input':['Send me the price on WhatsApp'],'original_risk':'Some branches promise unsupported contact actions.','corrected_rule':'No channel, price or callback promise without configured successful action receipt.','metric':'unsupported_promise'},
    ]


class ComparisonService:
    """Same synthetic transcript, two prompts, mocked tools and measured output."""
    def __init__(self,config,repository):
        self.config,self.repo=config,repository

    async def replay(self,flow,scenario_id):
        import json,time,httpx
        from backend.admin_config import secret_value
        from backend.sarvam_v4 import ORGANIZATION,MODEL
        from backend.context_contract import digest
        scenario=next((x for x in comparison_cases() if x['id']==scenario_id),None)
        if not scenario: raise ValueError('Unknown comparison scenario.')
        original=render_original(flow)
        if original['status']!='available': return original
        settings=self.config.values['operations']['sarvam']
        if settings['organization_id']!=ORGANIZATION: raise ValueError('Wrong organization.')
        key=secret_value('SARVAM_V4_API_KEY')
        if not key or not self.config.llm_internal_key: raise ValueError('Designated inference access not configured.')
        policies={'original':original['original_text'],
                  'corrected':'''You are an AI buyer-assistance bot, never a human. Use Hindi, English or Telugu. Product must be explicitly identified and confirmed before a new lead or product tool. Interpret yes only against the immediately preceding question; greeting is not consent. Ask one collection question per turn. Persist attempts: product4,quantity3,specification2,name/city/state3,transfer2, including interruptions. Preserve quantity ranges and buyer versus preferred-seller location. Require city and state for seller lookup, actual availability and explicit consent for transfer. A transfer declares ownership of termination; terminate once. No prices, callback promises, availability or messages without tool success. A draft is not a submitted lead. Stop immediately when asked. Continue existing enrichment/resume/retry workflows without replacing existing leads. Mock all tools; never call a customer.'''}
        output={'scenario':scenario,'flow':flow,'model':MODEL,'mode':'model_replay_with_mocked_tools','production_recording':False,'results':{}}
        for variant,policy in policies.items():
            identifier='compare-'+digest([scenario,flow,variant,policy,MODEL])[:32]
            prior=self.repo.get('tool_runs',identifier)
            if prior and prior['status']=='completed':
                output['results'][variant]=prior['result'];continue
            if prior and prior['status'] in {'started','completion_unknown'}:
                raise ValueError('A prior comparison completion is uncertain; automatic retry disabled.')
            day=now()[:10]
            receipt={'id':identifier,'subject_id':'buyer-comparison','status':'started','budget_day':day,'created_at':now()}
            receipt=self.repo.reserve_tool(receipt,settings['max_calls_per_day'])
            system='This is a sandbox policy replay, not a live call. Follow the supplied workflow policy on the identical synthetic buyer turns. Do not execute tools: report proposed tool names only. Return JSON {"replies":["one reply per supplied buyer turn"],"captured":{},"disposition":"...","tools":["tool names"],"unsupported_promises":["exact claimed promises with no mock receipt"]}. No external action occurs.'
            messages=[{'role':'user','content':'Workflow policy part:\n'+policy[i:i+28000]} for i in range(0,len(policy),28000)]
            messages.append({'role':'user','content':json.dumps({'flow':flow,'buyer_turns':scenario['input'],'mock_tool_state':{'seller_available':False,'posted_lead':False,'meeting_booked':False}},ensure_ascii=False)})
            start=time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=settings['timeout_seconds']) as client:
                    response=await client.post(self.config.llm_url+'/byok',headers={'X-Internal-Key':self.config.llm_internal_key},json={'provider':'custom','base_url':'https://api.sarvam.ai/v1','model':MODEL,'api_key':key,'ref':'sellergenome-buyer-comparison','system_prompt':system,'messages':messages,'max_tokens':1600,'temperature':.1,'json_mode':True,'extra_body':{'reasoning_effort':'low','response_format':{'type':'json_object'}}})
                    response.raise_for_status();data=response.json()
                if data.get('model')!=MODEL or data.get('provider') not in {None,'custom'}: raise ValueError('Unexpected provider/model.')
                result=json.loads(data['choices'][0]['message']['content'])
                if not isinstance(result.get('replies'),list) or len(result['replies'])>20: raise ValueError('Invalid replay schema.')
                replies=[str(x)[:1500] for x in result['replies']]
                result['metrics']={'question_repetition':len(replies)-len(set(replies)),'captured_fields':len(result.get('captured',{})),
                                   'proposed_tool_count':len(result.get('tools',[])), 'unsupported_promises_reported':len(result.get('unsupported_promises',[])),
                                   'metric_limit':'Model-reported promise audit; human review required.'}
                result['duration_ms']=round((time.monotonic()-start)*1000)
                self.repo.put('tool_runs',{**receipt,'status':'completed','result':result})
                output['results'][variant]=result
            except (httpx.HTTPError,ValueError,KeyError,TypeError) as error:
                self.repo.put('tool_runs',{**receipt,'status':'completion_unknown' if isinstance(error,httpx.TimeoutException) else 'failed'})
                output['results'][variant]={'status':'failed','reason':'Provider or structured-output validation failed; no fallback used.'}
        return output
