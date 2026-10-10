"""Versioned actual tool inventory, spend-free examples and bounded live harness."""
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from backend.admin_config import secret_value
from backend.context_contract import now
from backend.sarvam_v4 import ORGANIZATION

ROOT=Path(__file__).resolve().parents[1]
# No arbitrary file paths, URLs, schemas, job IDs or deletion targets from browser.
SAFE_INPUTS={
 'sarvam_code_api_reference':{'endpoint':'/text-to-speech'},
 'sarvam_code_languages':{'api':'tts'},
 'sarvam_code_speakers':{'model':'bulbul:v3'},
 'sarvam_code_pricing':{},
 'sarvam_code_snippet':{'api':'tts','language':'python'},
 'sarvam_code_recommend_model':{'task_description':'A bilingual seller qualification rehearsal with interruption support.'},
 'sarvam_code_validate_request':{'endpoint':'/text-to-speech','body':{'text':'This is a synthetic SellerGenome test.','target_language_code':'en-IN','model':'bulbul:v3','speaker':'shubh'}},
 'sarvam_tools_identify_language':{'input':'नमस्ते, मुझे पैकेज की जानकारी चाहिए।'},
 'sarvam_tools_translate':{'input':'May I explain the package briefly?','source_language_code':'en-IN','target_language_code':'hi-IN'},
 'sarvam_tools_transliterate':{'input':'नमस्ते','source_language_code':'hi-IN','target_language_code':'en-IN'},
 'sarvam_tools_tts_speak':{'text':'Hello. I am an AI assistant. Is this a good time for a short conversation?','target_language_code':'en-IN','speaker':'shubh','model':'bulbul:v3'},
 'sarvam_tools_tts_stream':{'text':'नमस्ते। मैं एक ए आई सहायक हूँ। क्या अभी बात कर सकते हैं?','target_language_code':'hi-IN','speaker':'shubh','model':'bulbul:v3'},
 'sarvam_tools_pronunciation_list':{},
 'sarvam_tools_text_analytics':{'text':'Synthetic seller: Please explain lead quality briefly in Hindi. I have not agreed to a meeting.', 'questions':[{'id':'q1','text':'Has the seller agreed to a meeting?','type':'boolean'},{'id':'q2','text':'Which language was explicitly requested?','type':'short answer'}]},
 'sarvam_tools_stt_transcribe':{'model':'saaras:v3','language_code':'unknown'},
 'sarvam_tools_stt_realtime':{'model':'saaras:v3-realtime','language_code':'unknown','stream_type':'fast'},
 'sarvam_tools_stt_batch_submit':{'model':'saaras:v3','language_code':'unknown','with_timestamps':True,'with_diarization':True,'num_speakers':1},
}
EXAMPLES={
 'stt':'Review a synthetic conversation transcript before assigning seller and buyer roles.',
 'tts':'Hear an AI-disclosed, concise opening in the selected language.',
 'translate':'Translate a package explanation while preserving uncertainty and product names.',
 'transliterate':'Render a name in another script without changing seller identity.',
 'identify':'Identify an explicit language sample, not language from geography.',
 'text_analytics':'Extract objections and requested next actions with source citations.',
 'llm':'Aggregate source snapshots into bounded, validated persona factors.',
 'vision':'Read a synthetic product specification sheet into structured requirements.',
 'document':'Translate a synthetic catalog while keeping its layout.',
 'dubbing':'Create a synthetic bilingual training clip, with no real person voice cloning.',
 'pronunciation':'Improve pronunciation of a disposable synthetic product name.',
 'code':'Inspect actual SDK request shapes and supported controls.',
 'voice':'Compose STT, gateway inference and speech in a test rehearsal.',
 'dub':'Translate and revoice a consented synthetic training clip.',
 'localize':'Localize a small synthetic product catalog into Hindi.',
 'recall':'Retrieve a cited fact from explicitly selected synthetic documents.',
 'set_api':'Replace a designated credential through write-only Admin storage.',
 'upgrade':'Review the pinned runtime upgrade plan without mutating the application.'}


class SarvamLab:
    def __init__(self,config,repository):
        self.config,self.repo=config,repository
        self.catalog=json.loads((ROOT/'data/sarvam-mcp-inventory.json').read_text())
        self.lock=asyncio.Semaphore(1)

    def inventory(self):
        items=[]
        for tool in self.catalog['tools']:
            key=next((key for key in EXAMPLES if key in tool['name']), 'code')
            maintenance=tool['name'] in {'sarvam_tools_set_api_key','sarvam_tools_upgrade'}
            status='maintenance_only' if maintenance else 'ready_for_bounded_test' if tool['name'] in SAFE_INPUTS else 'requires_harness_resource'
            if tool['name']=='sarvam_tools_text_analytics':status='provider_retired'
            items.append({**tool,'group':'Builder' if tool['name'].startswith('sarvam_code') else 'Maintenance' if maintenance else key.title(),
                          'purpose':EXAMPLES[key],'live_status':status,
                          'sample_input':SAFE_INPUTS.get(tool['name'],self.example_input(tool)),
                          'demo_kind':'illustrative_target','last_receipts':[x for x in self.repo.list('tool_runs','mcp',100) if x.get('tool')==tool['name']][:1]})
        return {'items':items,'version':self.catalog['version'],'organization_id':ORGANIZATION,
                'voice_agents':{'status':'oauth_required','endpoint':'https://mcp.sarvam.ai/voice-agents','workspace':self.config.values['operations']['sarvam']['voice_workspace']},
                'scope_verification':'Designated credential pinned locally; Voice workspace identity still requires OAuth verification.'}

    @staticmethod
    def example_input(tool):
        schema=tool['input_schema']
        return {key: schema.get('properties',{}).get(key,{}).get('default',
                    '<disposable harness resource>' if any(x in key for x in ['path','job','dict','file']) else '<synthetic '+key+'>') for key in schema.get('required',[])}

    async def run(self,name,mode,language=None):
        if self.config.values['operations']['sarvam']['organization_id']!=ORGANIZATION:
            raise ValueError('Wrong organization; execution blocked.')
        tool=next((x for x in self.inventory()['items'] if x['name']==name),None)
        if not tool:
            raise ValueError('Tool is not in the pinned discovered inventory.')
        receipt={'id':'mcp-'+uuid4().hex,'subject_id':'mcp','tool':name,'mode':mode,
                 'created_at':now(),'organization_id':ORGANIZATION,'package_version':self.catalog['version']}
        if mode=='demo':
            receipt.update(status='illustrative_demo',input=tool['sample_input'],
                           result={'purpose':tool['purpose'],'expected_behavior':tool['description'][:900],
                                   'schema':tool['input_schema'],'provider_called':False,
                                   'limitation':'Illustrative tool contract; no claim that a provider action occurred.'})
            return self.repo.put('tool_runs',receipt)
        if tool['live_status']=='provider_retired':
            return self.repo.put('tool_runs',{**receipt,'status':'provider_retired','reason':'The pinned MCP implementation reports /text-analytics permanently removed. Structured persona extraction uses the separately verified Sarvam chat model. No request or spend is made by this retired tool.'})
        if name not in SAFE_INPUTS:
            receipt.update(status=tool['live_status'],reason='Maintenance uses deployment/Admin controls.' if tool['live_status']=='maintenance_only' else 'This operation requires an owned disposable job, document or dictionary from the integration harness; arbitrary browser resources are not accepted.')
            return self.repo.put('tool_runs',receipt)
        executable=Path(os.getenv('SARVAM_MCP_PYTHON','/opt/sellergenome/venv_sarvam_mcp/bin/python'))
        if not executable.exists():
            return self.repo.put('tool_runs',{**receipt,'status':'runtime_not_configured'})
        arguments = dict(SAFE_INPUTS[name])
        if language and name in {'sarvam_tools_tts_speak','sarvam_tools_tts_stream'}:
            samples = {'en-IN':'Hello. I am an AI assistant. May I explain the package briefly?', 'hi-IN':'नमस्ते। मैं एक ए आई सहायक हूँ। क्या मैं पैकेज की जानकारी संक्षेप में दे सकता हूँ?', 'te-IN':'నమస్కారం. నేను ఏ ఐ సహాయకుడిని. ప్యాకేజీ గురించి క్లుప్తంగా చెప్పవచ్చా?'}
            if language not in samples: raise ValueError('Select a supported test language.')
            arguments.update(text=samples[language], target_language_code=language)
        key=secret_value('SARVAM_V4_API_KEY')
        if not key:
            return self.repo.put('tool_runs',{**receipt,'status':'credential_not_configured'})
        artifact=self.config.data_dir/'sarvam-mcp'
        artifact.mkdir(exist_ok=True,mode=0o700)
        if name in {'sarvam_tools_stt_transcribe','sarvam_tools_stt_realtime','sarvam_tools_stt_batch_submit'}:
            prior=next((x for x in self.repo.list('tool_runs','mcp',100) if x.get('audio_file') and x['status']=='completed' and x.get('organization_id')==ORGANIZATION),None)
            if not prior:
                return self.repo.put('tool_runs',{**receipt,'status':'requires_harness_resource','reason':'First generate synthetic TTS in this Lab; STT accepts only its owned artifact.'})
            source=(artifact/prior['audio_file']).resolve()
            if source.parent!=artifact.resolve() or not source.is_file():
                return self.repo.put('tool_runs',{**receipt,'status':'artifact_unavailable'})
            normalized=artifact/('stt-input-'+uuid4().hex+'.wav')
            normalize=await asyncio.create_subprocess_exec('ffmpeg','-nostdin','-v','error','-i',str(source),'-t','25','-ac','1','-ar','16000','-c:a','pcm_s16le',str(normalized),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(normalize.wait(),timeout=20)
            if normalize.returncode:
                raise ValueError('Synthetic audio normalization failed before provider request.')
            arguments['audio_path']=str(normalized)
            receipt['input_artifact_receipt']=prior['id']
        env={'PATH':str(executable.parent)+':/usr/bin:/bin','SARVAM_API_KEY':key or '',
             'SARVAM_MCP_BASE_PATH':str(artifact),'SARVAM_AUDIO_OUTPUT_MODE':'files',
             'PYTHONUNBUFFERED':'1'}
        # Clean environment prevents older account credentials/config leaking in.
        if not name.startswith('sarvam_code'):
            day = now()[:10]
            receipt['budget_day'] = day
        if receipt.get('budget_day'):
            receipt=self.repo.reserve_tool(receipt,self.config.values['operations']['sarvam']['max_calls_per_day'])
        else:
            self.repo.put('tool_runs',{**receipt,'status':'started'})
        async with self.lock:
            process=await asyncio.create_subprocess_exec(str(executable),str(ROOT/'scripts/sarvam_mcp_runner.py'),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,cwd=artifact)
            try:
                out,_=await asyncio.wait_for(process.communicate(json.dumps({'name':name,'arguments':arguments}).encode()),timeout=90)
                if process.returncode or len(out)>200000:
                    raise ValueError('MCP tool failed.')
                result=json.loads(out)
                receipt.update(status='failed' if result.get('is_error') else 'completed',input=arguments,result=result)
                if 'audio_path' in receipt['input']:
                    receipt['input']['audio_path']='[owned synthetic artifact]'
                for block in result.get('content', []):
                    try:
                        payload=json.loads(block.get('text',''))
                    except ValueError:
                        continue
                    if isinstance(payload,dict) and payload.get('error'):
                        receipt['status']='failed'
                    filename=payload.get('file_path')
                    if filename:
                        audio=Path(filename).resolve()
                        if audio.parent==artifact.resolve() and audio.suffix in {'.wav','.mp3'} and audio.exists():
                            receipt['audio_file']=audio.name
                            receipt['audio_url']='/api/sarvam/runs/'+receipt['id']+'/audio'
                            payload['file_path']='[private artifact]'
                            block['text']=json.dumps(payload,ensure_ascii=False)
            except (asyncio.TimeoutError,ValueError):
                if process.returncode is None:
                    process.kill();await process.wait()
                receipt.update(status='failed',reason='Pinned MCP execution failed or exceeded its bounded timeout. No alternate account used.')
        return self.repo.put('tool_runs',receipt)
