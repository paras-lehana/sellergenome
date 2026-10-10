"""Provider-neutral voice preparation and room-scoped LiveKit handoff."""
import base64
import hashlib
import hmac
import json
import time
import os
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from backend.admin_config import secret_value
from backend.context_contract import now


def signed_token(payload,key):
    """HS256 server token, scoped to one short-lived room and identity."""
    encode=lambda x:base64.urlsafe_b64encode(json.dumps(x,separators=(',',':')).encode()).rstrip(b'=')
    body=encode({'alg':'HS256','typ':'JWT'})+b'.'+encode(payload)
    signature=base64.urlsafe_b64encode(hmac.new(key.encode(),body,hashlib.sha256).digest()).rstrip(b'=')
    return (body+b'.'+signature).decode()


class VoiceAdapters:
    """Existing speech remains available while the independent worker connects."""
    def __init__(self,config,repository):
        self.config,self.repo=config,repository

    def inventory(self):
        ready=bool(secret_value('LIVEKIT_API_KEY') and secret_value('LIVEKIT_API_SECRET') and self.config.values['operations']['voice']['livekit_url'])
        return [
            {'id':'sarvam_websocket','name':'Sarvam WebSocket rehearsal','status':'available','url':'/studio#lab','capabilities':['transcript','interruption','language','pace','speaker'],'connection':'Existing authenticated live-voice pipeline'},
            {'id':'livekit','name':'LiveKit worker','status':'configured_unverified' if ready else 'awaiting_connection','capabilities':['context_revision','transcript','interruption','tool_events'],'contract':'sellergenome-voice/4.0','limitation':'Worker acknowledgement is required; configured credentials do not prove a connected agent.'},
            {'id':'sarvam_voice_agents','name':'Sarvam Voice Agents','status':'oauth_required','capabilities':['context_tools','outcome_hooks'],'limitation':'Voice workspace OAuth is independent of Model API credentials.'}]

    async def prepare(self,run,adapter):
        if adapter not in {x['id'] for x in self.inventory()}:
            raise ValueError('Unknown voice adapter.')
        session={'id':'voice-'+uuid4().hex,'subject_id':run['subject_id'],'run_id':run['id'],
                 'adapter':adapter,'created_at':now(),'status':'prepared','context_revision':run['revision'],
                 'acknowledged_revision':None,'generation':0,'events':[], 'contract_version':'sellergenome-voice/4.0'}
        if adapter=='sarvam_websocket':
            session['rehearsal_url']='/studio#lab'
        elif adapter=='sarvam_voice_agents':
            session['status']='oauth_required'
        else:
            setting=self.config.values['operations']['voice']
            key,secret=secret_value('LIVEKIT_API_KEY'),secret_value('LIVEKIT_API_SECRET')
            if not key or not secret or not setting['livekit_url']:
                session['status']='awaiting_connection'
            else:
                parsed=urlsplit(setting['livekit_url'])
                allowed=set(filter(None,os.getenv('LIVEKIT_APPROVED_ORIGINS','').split(',')))
                if parsed.scheme!='wss' or parsed.username or parsed.password or setting['livekit_url'].rstrip('/') not in allowed:
                    raise ValueError('LiveKit URL must match a server-approved wss origin before dispatch.')
                room='sg-'+run['id']
                claims={'iss':key,'sub':'pm-'+session['id'],'iat':int(time.time()),'nbf':int(time.time())-5,'exp':int(time.time())+setting['token_seconds'],
                        'video':{'roomJoin':True,'room':room,'canPublish':True,'canSubscribe':True},'metadata':json.dumps({'run_id':run['id']})}
                session.update(status='awaiting_worker',room=room,url=setting['livekit_url'])
                # Dispatch metadata contains no private context; the worker fetches it.
                admin={**claims,'sub':'sellergenome-dispatch','video':{'roomAdmin':True,'room':room,'roomCreate':True}}
                endpoint=setting['livekit_url'].replace('wss://','https://').replace('ws://','http://').rstrip('/')
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        response=await client.post(endpoint+'/twirp/livekit.AgentDispatchService/CreateDispatch',headers={'Authorization':'Bearer '+signed_token(admin,secret)},json={'agent_name':setting['livekit_agent_name'],'room':room,'metadata':json.dumps({'run_id':run['id'],'voice_session_id':session['id']})})
                        response.raise_for_status()
                        session['dispatch_id']=response.json().get('id')
                except httpx.HTTPError:
                    session['status']='dispatch_failed'
                saved=self.repo.put('voice_sessions',session)
                return {**saved,'browser_token':signed_token(claims,secret)}
        return self.repo.put('voice_sessions',session)

    def event(self,identifier,event):
        session=self.repo.get('voice_sessions',identifier)
        if not session:
            raise ValueError('Voice session not found.')
        kind=event.get('type')
        if kind not in {'context_ack','transcript','speech_started','speech_finished','signal','interruption','tool','outcome','terminated'}:
            raise ValueError('Unsupported normalized voice event.')
        if kind=='context_ack':
            if event.get('revision')!=session['context_revision']:
                raise ValueError('Stale context acknowledgement.')
            session['acknowledged_revision']=event['revision']
            session['status']='context_acknowledged'
            session['media_verified']=False
        if kind=='interruption':
            session['generation']+=1
        if kind.startswith('speech_') and event.get('generation')!=session['generation']:
            raise ValueError('Stale audio generation rejected after interruption.')
        if kind=='terminated':
            session['status']='ended'
        session['events']=[*session['events'],{**event,'at':now()}][-500:]
        return self.repo.put('voice_sessions',session,expected=session['revision'])
