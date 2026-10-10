"""Persistent four-flow buyer controller; collection limits outrank handlers.

Language models can suggest extracted values, but cannot choose tool execution,
reset allowances, invent seller availability or mark a lead as submitted.
"""
from __future__ import annotations

import copy
import re
from uuid import uuid4
from backend.context_contract import now

FLOWS = {'new_requirement', 'enrichment', 'resume_identity', 'connection_retry'}
TRANSLATIONS = {
    'product': {'en-IN':'Which product do you need?', 'hi-IN':'आपको कौन सा उत्पाद चाहिए?', 'te-IN':'మీకు ఏ ఉత్పత్తి కావాలి?'},
    'confirm_product': {'en-IN':'Have I understood the product correctly?', 'hi-IN':'क्या मैंने आपका उत्पाद सही समझा?', 'te-IN':'మీకు కావలసిన ఉత్పత్తిని సరిగ్గా అర్థం చేసుకున్నానా?'},
    'quantity': {'en-IN':'What quantity do you need?', 'hi-IN':'आपको कितनी मात्रा चाहिए?', 'te-IN':'మీకు ఎంత పరిమాణం కావాలి?'},
    'name': {'en-IN':'What name should I use?', 'hi-IN':'मैं आपको किस नाम से बुलाऊँ?', 'te-IN':'మిమ్మల్ని ఏ పేరుతో పిలవాలి?'},
    'city': {'en-IN':'Which city are you buying from?', 'hi-IN':'आप किस शहर से खरीद रहे हैं?', 'te-IN':'మీరు ఏ నగరం నుండి కొనుగోలు చేస్తున్నారు?'},
    'state': {'en-IN':'Which state is that city in?', 'hi-IN':'यह शहर किस राज्य में है?', 'te-IN':'ఆ నగరం ఏ రాష్ట్రంలో ఉంది?'},
    'transfer': {'en-IN':'May I connect you to the available seller?', 'hi-IN':'क्या मैं आपको उपलब्ध विक्रेता से जोड़ दूँ?', 'te-IN':'అందుబాటులో ఉన్న విక్రేతకు మిమ్మల్ని కనెక్ట్ చేయవచ్చా?'},
}


class BuyerEngine:
    """Deterministic authority over questions, consent and terminal actions."""

    def __init__(self, config, repository):
        self.config, self.repo = config, repository

    def create(self, flow='new_requirement', language='hi-IN', initial=None, run_id=None):
        if flow not in FLOWS or language not in self.config.values['operations']['buyer']['languages']:
            raise ValueError('Unsupported buyer flow or language.')
        initial = initial or {}
        allowed = {'product','product_confirmed','quantity','name','city','state','specifications','lead_type','preferred_seller_city','preferred_seller_state'}
        if set(initial)-allowed:
            raise ValueError('Initial state contains unsupported fields.')
        state = {'contract_version':'buyer/4.0','id':'buyer-'+uuid4().hex, 'subject_id':run_id or 'scenario-buyer',
                 'flow':flow,'language':language,'created_at':now(),'run_id':run_id,
                 'mode':'sample','simulation':True,  # This engine rehearses test tools only.
                 'fields':copy.deepcopy(initial),'attempts':{},'closed_fields':{},
                 'last_question':None,'turns':[],'tools':[], 'status':'collecting',
                 'product_confirmed': bool(initial.get('product_confirmed')) or bool(initial.get('product') and flow!='new_requirement'),
                 'seller_available':False,'availability_receipt':None,
                 'transfer_consent':False,'transfer_status':'not_requested',
                 'submission_status':'draft','lead_quality':'incomplete',
                 'disposition':None,'call_outcome':None,'termination_count':0,
                 'interruption_count':0,'off_topic_count':0,'collection_since_available':0}
        state['fields'].pop('product_confirmed',None)  # One authoritative confirmation flag.
        self.next_question(state)
        return self.repo.put('buyer_states',state)

    def limit(self, field):
        rules=self.config.values['operations']['buyer']
        return rules['product_attempts'] if field in {'product','confirm_product'} else rules['quantity_attempts'] if field=='quantity' else rules['specification_attempts'] if field.startswith('spec:') else rules['transfer_attempts'] if field=='transfer' else rules['identity_attempts']

    def ask(self,state,field):
        """One ask per turn; product confirmation shares product allowance."""
        counter='product' if field=='confirm_product' else field
        if state['attempts'].get(counter,0)>=self.limit(field) or counter in state['closed_fields']:
            return False
        state['attempts'][counter]=state['attempts'].get(counter,0)+1
        state['last_question']=field
        state['question']=TRANSLATIONS.get(field,{}).get(state['language'], f'Which {field.removeprefix("spec:")} do you prefer?')
        if state['seller_available'] and field!='transfer':
            state['collection_since_available']+=1
        return True

    @staticmethod
    def terminate(state, disposition, outcome, reason):
        """Persist disposition/outcome before a single terminal signal."""
        if state['status']=='ended':
            return
        state.update(disposition=disposition,call_outcome=outcome,status='ended',
                     terminal_reason=reason,last_question=None,question=None,
                     termination_count=state['termination_count']+1)

    def next_question(self,state):
        """Skip closed slots, allow volunteered updates and honor enrichment shortcut."""
        if state['status']=='ended' or state['transfer_status']=='succeeded':
            return
        fields=state['fields']
        if not state['product_confirmed']:
            if not self.ask(state,'confirm_product' if fields.get('product') else 'product'):
                self.terminate(state,'BLDeleted','NoProductRequirement','Product not confirmed within allowance.')
            return
        shortcut=state['flow'] in {'enrichment','connection_retry'} and state['seller_available'] and (state['collection_since_available']>=1 or state['flow']=='connection_retry')
        if not shortcut:
            collect = ['name','city','state'] if state['flow']=='resume_identity' else ['quantity',*[f'spec:{key}' for key in state.get('spec_options',{})],'name','city','state']
            if state['flow']=='enrichment' and fields.get('lead_type') not in {None,'LIVE'}:
                collect=[]
            for field in collect:
                value=fields.get('specifications',{}).get(field[5:]) if field.startswith('spec:') else fields.get(field)
                if value is None or value=='':
                    if self.ask(state,field):
                        return
                    state['closed_fields'].setdefault(field,'attempts_exhausted')
        state['lead_quality']='complete' if all(fields.get(x) for x in ['product','quantity','name','city','state']) else 'partial'
        if state['seller_available']:
            if state['transfer_consent']:
                state.update(status='ready_to_transfer',question=None,last_question=None)
            elif not self.ask(state,'transfer'):
                self.terminate(state,'ENRICHMENTCALL' if state['flow']=='enrichment' else 'BLenriched','NoTransferConsent','Consent allowance exhausted; no transfer attempted.')
        else:
            state.update(status='draft_ready',question=None,last_question=None)

    def turn(self,identifier,text,values=None,product_change=False,interrupted=False,expected_revision=None):
        """Apply volunteered values before checking limits; never trust tool flags."""
        state=self.repo.get('buyer_states',identifier)
        if not state:
            raise ValueError('Buyer conversation not found.')
        if state['status']=='ended':
            return state
        if expected_revision is not None and state['revision']!=expected_revision:
            raise ValueError('Revision conflict; reload conversation.')
        normalized=text.strip().casefold()
        previous=state['last_question']
        values=values or {}
        allowed={'product','quantity','quantity_unit','name','city','state','specifications','preferred_seller_city','preferred_seller_state','language','slot_status'}
        if set(values)-allowed:
            raise ValueError('Values cannot set consent, availability, submission or dispositions.')
        if interrupted:
            state['interruption_count']+=1
            state['turns'].append({'text':text,'at':now(),'interrupted':True})
            return self.repo.put('buyer_states',state,expected=state['revision'])
        if values.get('language'):
            if values['language'] not in self.config.values['operations']['buyer']['languages']:
                raise ValueError('Choose Hindi, English or Telugu.')
            state['language']=values.pop('language')
        # Explicit stop always precedes data collection and tool eligibility.
        if re.search(r'\b(stop|do not call|don.t call|end the call)\b|कॉल बंद|बात नहीं|ఆపండి',normalized):
            self.terminate(state,'BLDeleted','NoProductRequirement','Explicit stop request.')
        elif re.search(r'\b(job|employment)\b|नौकरी',normalized) and not values.get('product'):
            self.terminate(state,'BLDeleted','Job Related___BL Not Approved','Job enquiry without buying requirement.')
        elif 'already bought' in normalized or 'खरीद लिया' in normalized:
            if state.get('already_bought_handled') or state['flow'] == 'enrichment':
                self.terminate(state,'ENRICHMENTCALL' if state['flow']=='enrichment' else 'BLDeleted','NoProductRequirement','Buyer has already bought the product.')
            else:
                state['already_bought_handled']=True
                state['last_question']='other_requirement'
                state['question']='Do you have another buying requirement?' if state['language']=='en-IN' else 'क्या आपको कोई दूसरा उत्पाद चाहिए?' if state['language']=='hi-IN' else 'మీకు మరో ఉత్పత్తి అవసరమా?'
                state['turns'].append({'text':text,'at':now(),'intent':'already_bought'})
                return self.repo.put('buyer_states',state,expected=state['revision'])
        elif normalized in {'i am selling','बेचना है'}:
            self.terminate(state,'BLDeleted','Selling Related','Seller enquiry, not a buying requirement.')
        if state['status']=='ended':
            state['turns'].append({'text':text,'at':now(),'disposition':state['disposition']})
            return self.repo.put('buyer_states',state,expected=state['revision'])
        if previous=='other_requirement':
            if normalized in {'no','no thanks','नहीं','వద్దు','కాదు'}:
                self.terminate(state,'BLDeleted','NoProductRequirement','No other buying requirement.')
                return self.repo.put('buyer_states',state,expected=state['revision'])
            if normalized not in {'yes','हाँ','हां','అవును'}:
                values['product']=text.strip();product_change=True
        if re.search(r'cannot hear|can.t hear|आवाज़ नहीं|వినిపించ', normalized):
            state['connectivity_count']=state.get('connectivity_count',0)+1
            if state['connectivity_count']>=2:
                self.terminate(state,'BLDeleted','ConnectivityIssue','Repeated connectivity problem; no unconfirmed callback promised.')
            else:
                state['question']='Can you hear me now?' if state['language']=='en-IN' else 'क्या अब मेरी आवाज़ आ रही है?' if state['language']=='hi-IN' else 'ఇప్పుడు నా మాట వినిపిస్తుందా?'
            return self.repo.put('buyer_states',state,expected=state['revision'])
        if re.search(r'complaint|fraud|refund|शिकायत|धोखा',normalized):
            self.terminate(state,'BLDeleted','SupportReferral','Account/payment complaint requires human support; no resolution or refund promised.')
            return self.repo.put('buyer_states',state,expected=state['revision'])
        if normalized in {'only the original seller','same seller only','उसी विक्रेता से बात करनी है'}:
            self.terminate(state,'BLDeleted','ParticularSeller','Buyer requests only the original seller; no alternate transfer or callback promised.')
            return self.repo.put('buyer_states',state,expected=state['revision'])
        if normalized in {'i will not speak to a bot','no ai','बॉट से बात नहीं'}:
            self.terminate(state,'BLDeleted','NoProductRequirement','Buyer declines AI assistance.')
            return self.repo.put('buyer_states',state,expected=state['revision'])
        product=values.get('product')
        if product and state['fields'].get('product') and product_change:
            if state['flow']=='enrichment':
                state['fields']['changed_product']=product
                self.terminate(state,'ENRICHMENTCALL','BLenriched','New requirement recorded for human review; existing lead not replaced.')
            else:
                for key in ['product','quantity','quantity_unit','specifications']:
                    state['fields'].pop(key,None)
                state['attempts']={k:v for k,v in state['attempts'].items() if k not in {'product','quantity'} and not k.startswith('spec:')}
                state['closed_fields']={k:v for k,v in state['closed_fields'].items() if k not in {'product','quantity'} and not k.startswith('spec:')}
                state['product_confirmed']=False
                state['spec_options']={}
        slot_status=values.pop('slot_status',{})
        if previous and (previous.startswith('spec:') or previous in {'quantity','name','city','state'}):
            if normalized in {'any','anything','कोई भी','ఏదైనా'}:
                slot_status[previous]='any'
            elif normalized in {'ask the seller','discuss with seller','seller will tell','विक्रेता से पूछें'}:
                slot_status[previous]='seller_deferred'
            elif normalized in {'i refuse','prefer not to say','नहीं बताना'}:
                slot_status[previous]='refused'
            elif previous.startswith('spec:') and text.strip() and not values:
                values['specifications']={previous[5:]:text.strip()}
        for field,status in slot_status.items():
            if field not in {'quantity','name','city','state','transfer'} and not field.startswith('spec:'):
                raise ValueError('Unknown collection slot.')
            if status not in {'any','refused','seller_deferred'}:
                raise ValueError('Unknown slot status.')
            state['closed_fields'][field]=status
        for key,value in values.items():
            if isinstance(value,str) and len(value)>500 or len(str(value))>4000:
                raise ValueError('Collected value exceeds its bound.')
            if key=='specifications':
                if not isinstance(value,dict) or len(value)>20:
                    raise ValueError('Specifications require a bounded object.')
                state['fields'].setdefault(key,{}).update(value)
            else:
                state['fields'][key]=value
        affirmative=normalized in {'yes','yes please','haan','ha','हाँ','हां','जी','అవును','సరే'}
        negative=normalized in {'no','no thanks','नहीं','వద్దు','కాదు'}
        # Product confirmation and consent are bound to the actual prior question.
        if previous=='confirm_product' and affirmative and state['fields'].get('product'):
            state['product_confirmed']=True
        elif previous=='confirm_product' and negative:
            state['fields'].pop('product',None)
            state['product_confirmed']=False
        if previous=='transfer' and affirmative and state['seller_available']:
            state['transfer_consent']=True
        if previous=='transfer' and negative:
            state['transfer_status']='refused'
            self.terminate(state,'ENRICHMENTCALL' if state['flow']=='enrichment' else 'BLenriched','BLenriched','Buyer refused transfer.')
        if normalized in {'connect me to another seller','connect me to the available seller'} and state['seller_available']:
            state['transfer_consent']=True
        # A plain answer is accepted only against its exact collection question.
        if previous in {'product','name','city','state','quantity'} and not values and previous not in slot_status and normalized not in {'hello','hi','नमस्ते','yes','no','हाँ','हां','जी','अवును','సరే'} and text.strip():
            state['fields'][previous]=text.strip()
        state['turns'].append({'text':text,'at':now(),'previous_question':previous,'captured':copy.deepcopy(values)})
        self.next_question(state)
        return self.repo.put('buyer_states',state,expected=state['revision'])

    def tool(self,identifier,operation,mode='sample',termination='tool_owned'):
        """Functional synthetic tools; unconfigured Live can never claim success."""
        state=self.repo.get('buyer_states',identifier)
        if not state:
            raise ValueError('Buyer conversation not found.')
        if operation not in {'product_context','seller_lookup','transfer','submit_lead'}:
            raise ValueError('Unknown buyer tool.')
        if mode!='sample':
            return {'status':'not_configured','operation':operation,'reason':'A verified company-specific test endpoint contract is required; no request was sent.'}
        if not state['product_confirmed']:
            raise ValueError('Confirm the product before executing tools.')
        receipt={'id':'tool-'+uuid4().hex,'operation':operation,'mode':'sample','at':now(),'status':'completed','simulation':True}
        if operation=='product_context':
            state['spec_options']={'power':['Single phase','Three phase']}
            receipt['response']={'product':state['fields']['product'],'specifications':state['spec_options']}
        elif operation=='seller_lookup':
            city,state_name=state['fields'].get('city'),state['fields'].get('state')
            if not city or not state_name:
                raise ValueError('Both buyer city and state must be collected before seller lookup.')
            state['seller_available']=True
            state['availability_receipt']=receipt['id']
            state['collection_since_available']=0
            receipt['response']={'seller':'Synthetic supplier','available':True,'city':city,'state':state_name}
        elif operation=='transfer':
            if not state['seller_available'] or not state['availability_receipt'] or not state['transfer_consent']:
                raise ValueError('A valid availability receipt and explicit consent are required.')
            if state['transfer_status']=='succeeded':
                return state
            if termination not in {'tool_owned','explicit_after_success'}:
                raise ValueError('Transfer must declare termination ownership.')
            state['transfer_status']='succeeded'
            receipt['termination_behavior']=termination
            self.terminate(state,'ENRICHMENTCALL' if state['flow']=='enrichment' else 'BLenriched','Connected','Synthetic transfer succeeded; no customer was called.')
        else:
            if state['submission_status']=='sample_accepted':
                return state
            state['submission_status']='sample_accepted'
            receipt['response']={'test_lead_id':'sample-'+identifier,'posted_to_production':False}
        state['tools'].append(receipt)
        if operation in {'product_context','seller_lookup'}:
            self.next_question(state)
        return self.repo.put('buyer_states',state,expected=state['revision'])

    @staticmethod
    def compatibility(state):
        """Preserve quantity range while exposing the original prompt's max slot."""
        fields=state['fields']
        numbers=re.findall(r'\d+(?:\.\d+)?',str(fields.get('quantity','')))
        return {'current_phase':state['status'],'product_name':fields.get('product'),
                'quantity':max(map(float,numbers)) if numbers else None,
                'quantity_original':fields.get('quantity'),'quantity_unit':fields.get('quantity_unit'),
                'specifications':fields.get('specifications',{}),'buyer_name':fields.get('name'),
                'buyer_city':fields.get('city'),'updated_state_name':fields.get('state'),
                'buyer_disposition':state['disposition'],'call_outcome':state['call_outcome'],
                'connect_ast_call':state['transfer_status']=='succeeded',
                'ast_call_remark':'Refused' if state['transfer_status']=='refused' else state['transfer_status'],
                'is_enrich':int(state['flow']=='enrichment'),'lead_type':fields.get('lead_type'),
                'quantity_asked_count':state['attempts'].get('quantity',0),
                'field_attempts':state['attempts'],'submission_status':state['submission_status'],
                'updated_buyer_name':fields.get('name'), 'updated_city_name':fields.get('city'),
                'triangulation_response_city':fields.get('preferred_seller_city'),
                'triangulation_response_value':fields.get('preferred_seller_state'),
                'ast_flow_live':state['seller_available'], 'bl_converted':state['submission_status']=='sample_accepted',
                'identity_revealed':True, 'identity_challenge_count':state.get('identity_challenge_count',0),
                'seller_ask_count':state.get('seller_ask_count',0), 'additional_details':fields.get('additional_details',''),
                'off_topic_counter':state['off_topic_count'], 'already_bought_handled':state.get('already_bought_handled',False)}
