"""Test-only meeting proposals, confirmed addresses and idempotent bookings."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from backend.admin_config import secret_value
from backend.context_contract import now
from backend.source_policy import require_approved


class ActionService:
    """External action success requires a receipt; drafts never imply bookings."""

    def __init__(self,config,repository,transport=None):
        self.config,self.repo,self.transport=config,repository,transport

    def propose(self,subject,run_id,start,online,address='',mode='sample'):
        """Require explicit zoned time; seller location is never device location."""
        if mode not in {'sample','live'}:
            raise ValueError('Select Sample or Live test action mode.')
        if mode=='live':
            require_approved(subject)
        date=datetime.fromisoformat(start.replace('Z','+00:00'))
        if date.tzinfo is None:
            raise ValueError('Meeting time requires a timezone.')
        if date < datetime.now(date.tzinfo):
            raise ValueError('Choose a future meeting time.')
        if not online and not address.strip():
            raise ValueError('Offline meetings need a confirmed address.')
        document={'contract_version':'action/4.0','id':'meeting-'+uuid4().hex,'subject_id':subject,'run_id':run_id,
                  'kind':'meeting','mode':mode,'status':'proposed','test_only':True,
                  'created_at':now(),'start':date.isoformat(),'end':(date+timedelta(minutes=30)).isoformat(),
                  'display_time':date.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST'),
                  'online':online,'address':address,'seller_confirmed':False,
                  'conference_status':'not_requested','external_receipt':None}
        return self.repo.put('actions',document)

    def confirm(self,identifier,expected_revision):
        """Record simulated seller confirmation separately from external booking."""
        action=self.repo.get('actions',identifier)
        if not action:
            raise ValueError('Meeting proposal not found.')
        if action['status'] not in {'proposed','seller_confirmed'}:
            return action
        return self.repo.put('actions',{**action,'seller_confirmed':True,'status':'seller_confirmed','confirmed_at':now()},expected=expected_revision)

    async def token(self):
        """Refresh owner-authorized Calendar access without exposing OAuth secrets."""
        access=secret_value('GOOGLE_CALENDAR_ACCESS_TOKEN')
        refresh=secret_value('GOOGLE_CALENDAR_REFRESH_TOKEN')
        client_id,client_secret=secret_value('GOOGLE_CLIENT_ID'),secret_value('GOOGLE_CLIENT_SECRET')
        if refresh and client_id and client_secret:
            async with httpx.AsyncClient(timeout=15,transport=self.transport) as client:
                response=await client.post('https://oauth2.googleapis.com/token',data={'grant_type':'refresh_token','refresh_token':refresh,'client_id':client_id,'client_secret':client_secret})
                response.raise_for_status()
                return response.json()['access_token']
        if not access:
            raise ValueError('not_configured: Google Calendar OAuth permission is required.')
        return access

    async def book(self,identifier):
        """Reserve before sending; reconcile uncertain responses with a stable ID."""
        action=self.repo.get('actions',identifier)
        if not action or not action['seller_confirmed']:
            raise ValueError('Seller confirmation is required before test booking.')
        if action['status']=='calendar_booked' and (action['mode']=='sample' or action['conference_status'] in {'ready','not_requested'}):
            return action
        settings=self.config.values['operations']['actions']
        if not settings['test_only']:
            raise ValueError('Production actions are not enabled.')
        # Repeated proposals for the same seller/time share one booking resource.
        key='booking-'+hashlib.sha256(json.dumps([action['subject_id'],action['start'],action['mode']]).encode()).hexdigest()
        claimed=self.repo.claim(key,identifier)
        if claimed!=identifier:
            return self.repo.get('actions',claimed)
        if action['mode']=='sample':
            return self.repo.put('actions',{**action,'status':'calendar_booked','external_receipt':{'id':'sample-'+identifier,'provider':'synthetic_fixture','customer_invited':False},
                                'conference_status':'illustrative' if action['online'] else 'not_requested',
                                'meeting_link':None,'message':'Synthetic booking receipt. No event or Meet conference was created.'})
        require_approved(action['subject_id'])
        calendar=settings['calendar_id']
        if not calendar or calendar=='primary' or settings['calendar_owner']!='paras@lehana.in':
            raise ValueError('not_configured: a dedicated test calendar owned by paras@lehana.in is required.')
        token=await self.token()
        from urllib.parse import quote
        base='https://www.googleapis.com/calendar/v3/calendars/'+quote(calendar,safe='')
        event_id=hashlib.sha256(identifier.encode()).hexdigest()[:40]
        headers={'Authorization':'Bearer '+token}
        self.repo.put('actions',{**action,'status':'booking','provider_event_id':event_id})
        try:
            async with httpx.AsyncClient(timeout=20,transport=self.transport) as client:
                owner=await client.get('https://www.googleapis.com/calendar/v3/users/me/calendarList/primary',headers=headers)
                owner.raise_for_status()
                if owner.json().get('id','').casefold()!='paras@lehana.in':
                    raise ValueError('The authorized Calendar account must be paras@lehana.in.')
                # Verify write role and dedicated name before any mutation.
                info=await client.get('https://www.googleapis.com/calendar/v3/users/me/calendarList/'+quote(calendar,safe=''),headers=headers)
                info.raise_for_status()
                detail=info.json()
                if detail.get('accessRole')!='owner' or 'test' not in detail.get('summary','').lower():
                    raise ValueError('Calendar must be an owned, explicitly named test calendar.')
                prior=await client.get(base+'/events/'+event_id,headers=headers)
                if prior.status_code==200:
                    event=prior.json()
                else:
                    if prior.status_code!=404:
                        prior.raise_for_status()
                    free=await client.post('https://www.googleapis.com/calendar/v3/freeBusy',headers=headers,json={'timeMin':action['start'],'timeMax':action['end'],'timeZone':'Asia/Kolkata','items':[{'id':calendar}]})
                    free.raise_for_status()
                    availability=free.json()['calendars'][calendar]
                    if availability.get('errors') or availability.get('busy'):
                        raise ValueError('Calendar unavailable or the selected time conflicts with an event.')
                    payload={'id':event_id,'summary':'[SellerGenome TEST] Qualification handoff',
                             'description':'Synthetic integration test. No customer invitation or production booking.',
                             'start':{'dateTime':action['start'],'timeZone':'Asia/Kolkata'},
                             'end':{'dateTime':action['end'],'timeZone':'Asia/Kolkata'},
                             'location':action['address'],'extendedProperties':{'private':{'sellergenome_run':action.get('run_id') or '', 'test_only':'true'}}}
                    if action['online']:
                        payload['conferenceData']={'createRequest':{'requestId':event_id,'conferenceSolutionKey':{'type':'hangoutsMeet'}}}
                    result=await client.post(base+'/events',params={'conferenceDataVersion':1,'sendUpdates':'none'},headers=headers,json=payload)
                    if result.status_code==409:
                        result=await client.get(base+'/events/'+event_id,headers=headers)
                    result.raise_for_status()
                    event=result.json()
            conference=event.get('conferenceData',{})
            link=next((x.get('uri') for x in conference.get('entryPoints',[]) if x.get('entryPointType')=='video'),None)
            return self.repo.put('actions',{**action,'status':'calendar_booked','provider_event_id':event_id,
                'external_receipt':{'id':event['id'],'provider':'google_calendar','html_link':event.get('htmlLink'),'customer_invited':False},
                'conference_status':'ready' if link else conference.get('createRequest',{}).get('status',{}).get('statusCode','pending' if action['online'] else 'not_requested'),
                'meeting_link':link})
        except (httpx.HTTPError,ValueError,KeyError) as error:
            status='completion_unknown' if isinstance(error,httpx.TimeoutException) else 'failed'
            self.repo.put('actions',{**action,'status':status,'provider_event_id':event_id,'error':str(error)[:250] if isinstance(error,ValueError) else 'Calendar request failed; inspect/retry reconciles by stable event ID.'})
            raise ValueError('Test booking did not complete; no success is claimed.') from error

    async def geocode(self,address):
        """Geocode a seller-confirmed candidate, never the operator's position."""
        settings=self.config.values['operations']['actions']
        key=secret_value('GOOGLE_MAPS_API_KEY')
        if not settings['geocoding_enabled'] or not key:
            return {'status':'not_configured','candidates':[]}
        async with httpx.AsyncClient(timeout=12,transport=self.transport) as client:
            response=await client.get('https://maps.googleapis.com/maps/api/geocode/json',params={'address':address,'components':'country:IN','key':key})
            response.raise_for_status()
            result=response.json()
        return {'status':result['status'],'candidates':[{'formatted_address':x['formatted_address'],'location':x['geometry']['location'],'place_id':x['place_id'],'requires_confirmation':True} for x in result.get('results',[])[:5]]}
