"""Progressive context orchestration shared by feed, search and voice preparation."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.connectors import ConnectorService, ConnectorError, SOURCES, REDASH_SQL
from backend.context_contract import snapshot, scenario_payload, digest, now
from backend.operations_store import OperationsStore
from backend.operations_index import OperationsIndex
from backend.persona_layers import LayerService
from backend.sarvam_v4 import SarvamV4
from backend.source_policy import require_approved, SourcePolicyError

EXTRA_SOURCES = {'relationships', 'buyer_details', 'product_context', 'package_catalog'}


def redact(value):
    """Never persist credentials in graph exchanges or generated copy snippets."""
    if isinstance(value, dict):
        return {k: '[REDACTED]' if any(x in k.lower() for x in ('secret', 'password', 'token', 'api_key', 'authorization')) or k.lower() in {'ak','key'} else redact(v) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


class OperationsService:
    """Small durable run queue; provider attempts are never silently restarted."""

    def __init__(self, config, store, search):
        self.config, self.store, self.search = config, store, search
        self.repo = OperationsStore(store)
        self.index = OperationsIndex(config, search, self.repo)
        self.layers = LayerService(config, store, self.repo)
        self.sarvam = SarvamV4(config, self.repo)
        self.tasks, self.worker, self.index_worker = {}, None, None
        self.connector_factory = ConnectorService

    async def start(self):
        """Recover queued work; mark interrupted inference as uncertain explicitly."""
        for run in self.repo.list('runs', limit=500):
            if run['status'] == 'running':
                self.repo.put('runs', {**run, 'status': 'interrupted', 'error': 'Process restarted; completed snapshots retained. Create a new run to continue.'})
                self.repo.event(run['id'], 'run', 'interrupted', mode=run['mode'])
        self.worker = asyncio.create_task(self.loop())
        self.index_worker = asyncio.create_task(self.index_loop())

    async def stop(self):
        """Cancel only this service's tasks and leave durable receipts intact."""
        if self.worker:
            self.worker.cancel()
        tasks = [x for x in [self.worker, self.index_worker, *self.tasks.values()] if x]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def loop(self):
        """Drain bounded durable work without resuming copied production jobs."""
        while True:
            for run in self.repo.list('runs', limit=100):
                if self.config.values['operations']['enabled'] and run['status'] == 'queued' and run['id'] not in self.tasks and len(self.tasks) < self.config.values['operations']['max_active_runs']:
                    task = asyncio.create_task(self.execute(run['id']))
                    self.tasks[run['id']] = task
                    task.add_done_callback(lambda _, key=run['id']: self.tasks.pop(key, None))
            await asyncio.sleep(.25)

    async def index_loop(self):
        """Slow delivery never blocks initial preparation or the run queue."""
        while True:
            await self.index.deliver()
            await asyncio.sleep(2)

    def activities(self, query='', private=False, flow='seller'):
        """Simulation and search are read-only and make no upstream requests."""
        sellers = self.store.sellers(query.strip(), '', 1, 12, 'real' if query and private else 'demo')['items']
        if query and private and query.isdigit():
            seller = self.store.get('sellers', 'real-' + query)
            sellers = [seller] if seller else []
        items = []
        types = ['Product catalog updated', 'Exploring visibility packages', 'Qualification rehearsal', 'New buyer enquiry']
        for i, seller in enumerate(sellers):
            eligible=18+i*3 >= self.config.values['operations']['eligibility_delay_minutes']
            approved = False
            try:
                require_approved(seller['id'])
                approved = True
            except SourcePolicyError:
                pass
            items.append({'id': 'activity-' + digest([seller['id'], flow])[:12],
                          'seller_id': seller['id'], 'name': seller.get('name'),
                          'company': seller.get('company') or seller.get('name'),
                          'city': seller.get('city'), 'category': seller.get('category'),
                          'activity': types[i % len(types)], 'flow': flow,
                          'phase': 'Ready to prepare' if eligible else 'Exploration window',
                          'eligible_for_qualification':eligible,
                          'simulation': True, 'live_eligible': approved,
                          'simulated_elapsed_minutes': 18 + i * 3})
        return {'items': items, 'simulation': True, 'eligibility_delay_minutes':self.config.values['operations']['eligibility_delay_minutes'], 'clock': 'Reproducible scenario clock; no live calls are being dialed.', 'real_telemetry': False}

    def create(self, seller_id, mode='sample', flow='seller', sources=None, use_llm=False, force_sources=False):
        """Enforce identity at job creation as well as connector egress."""
        if not self.config.values['operations']['enabled']:
            raise ValueError('Operations preparation is paused in Admin.')
        if use_llm and not self.config.values['operations']['allow_enrichment']:
            raise ValueError('Paid enrichment is disabled in Admin.')
        if mode not in {'sample','live','local'} or flow not in {'seller','buyer'}:
            raise ValueError('Unsupported run mode or workflow.')
        if mode == 'live':
            require_approved(seller_id)
        self.check_contact(seller_id)
        seller = self.store.get('sellers', seller_id)
        if not seller and mode != 'sample':
            raise ValueError('Seller is not in local history or approved seeded targets.')
        if len([x for x in self.repo.list('runs') if x['status'] in {'queued','running'}]) >= self.config.values['operations']['max_active_runs']:
            raise ValueError('Preparation capacity reached; wait for a current run.')
        sources = sources or self.config.values['operations']['default_sources']
        if len(sources) > 12 or len(set(sources)) != len(sources) or any(x not in SOURCES and x not in EXTRA_SOURCES for x in sources):
            raise ValueError('Select unique registered sources only.')
        identifier = 'run-' + uuid4().hex
        subject = 'scenario-' + digest(seller_id)[:16] if mode == 'sample' else seller_id
        run = {'contract_version':'operations/4.0', 'id': identifier, 'subject_id': subject, 'seller_id': seller_id,
               'name': (seller or {}).get('name', 'Synthetic seller'), 'flow': flow,
               'mode': mode, 'status': 'queued', 'phase': 'preparation',
               'created_at': now(), 'sources': sources, 'snapshot_ids': [],
               'use_llm': bool(use_llm), 'config_revision': self.config.admin.revision,
               'provider_status': 'not_requested', 'simulation': mode == 'sample',
               'force_sources': force_sources, 'retrievals': {},
               'eligibility': {'policy_delay_minutes': self.config.values['operations'].get('eligibility_delay_minutes', 15), 'trigger': 'operator_rehearsal', 'outbound_dialing': False}}
        self.repo.put('runs', run)
        self.repo.event(identifier, 'run', 'queued', mode=mode, source='operations', message='Context preparation queued; no source request has been sent yet.')
        return self.repo.get('runs', identifier)

    def check_contact(self, seller_id):
        """Authoritative contact suppression is checked before each upstream hop."""
        seller = self.store.get('sellers', seller_id) or {}
        if seller.get('metrics', {}).get('do_not_call') or seller.get('contact_suppressed') or seller.get('persona_tag') == 'contact-suppressed':
            raise ValueError('Contact is suppressed; preparation stopped.')

    def save_run(self, identifier, **updates):
        """Always merge the latest run so parallel source completions coexist."""
        run = self.repo.get('runs', identifier)
        return self.repo.put('runs', {**run, **updates})

    def inspect(self, identifier):
        """Expose actual event-backed node receipts and current resolved layers."""
        run = self.repo.get('runs', identifier)
        if not run:
            raise ValueError('Preparation run not found.')
        events = self.repo.events(identifier, limit=1000)
        nodes = {}
        for event in events:
            nodes[event['node']] = {**nodes.get(event['node'], {}), **event}
        return {**run, 'events': events, 'nodes': list(nodes.values()), 'indexing': self.repo.backlog()}

    async def source(self, run, source, semaphore, force=False):
        """Reuse only matching, unexpired snapshots and preserve origin labels."""
        async with semaphore:
            start = time.monotonic()
            if run['mode'] == 'live':
                require_approved(run['seller_id'])
            config_revision = self.config.admin.revision
            if config_revision != run['config_revision']:
                self.repo.event(run['id'], source, 'configuration_changed', mode=run['mode'])
                return
            connector = self.connector_factory(self.config.values['connectors'], before_request=lambda: self.check_contact(run['seller_id']))
            spec = connector.specification(source) if source in SOURCES else self.config.values['operations']['sources'][source]
            mode = 'sample' if run['mode'] == 'sample' else 'local' if run['mode'] == 'local' else spec['mode']
            scope = digest([run['subject_id'], source, mode, config_revision, digest(spec.get('secret', ''))])
            cached = next((x for x in self.repo.list('snapshots', run['subject_id']) if x.get('scope') == scope and x['expires_at'] > now()), None)
            if cached and self.config.values['operations']['cache_reads'] and not force:
                indexed = await self.index.cached(cached['id'])
                retrieval = 'elasticsearch_cache' if indexed else 'sqlite_fallback'
                cached = copy.deepcopy(indexed or cached)
                for field in cached['fields'].values():
                    field['retrieval'] = retrieval
                self.attach(run['id'], cached['id'])
                self.record_retrieval(run['id'], cached['id'], retrieval)
                if source in {'company','context'}:
                    self.initial_context(run['id'],cached)
                self.repo.event(run['id'], source, 'cache_hit', source=source, mode=mode,
                    cache=retrieval, snapshot_id=cached['id'], duration_ms=round((time.monotonic()-start)*1000),
                    request={'method': 'GET', 'endpoint': self.index.alias('snapshots') + '/_doc/' + cached['id'], 'parameters': {'scope': scope}},
                    response={'snapshot_id': cached['id'], 'expires_at': cached['expires_at'], 'origin': source})
                return cached
            self.repo.event(run['id'], source, 'started', source=source, mode=mode, cache='miss',
                            message='Reading local evidence.' if mode=='local' else 'Replaying synthetic source.' if mode=='sample' else 'Executing configured source adapter.')
            try:
                exchanges, events = [], []
                if mode == 'disabled':
                    raise ValueError('source_disabled')
                if mode == 'local':
                    events = self.store.seller_evidence(run['seller_id'], 30)
                    seller = self.store.get('sellers', run['seller_id']) or {}
                    payload = {**seller, 'glid': run['seller_id'].removeprefix('real-'), 'records': events}
                    retrieval = 'sqlite'
                elif mode == 'sample':
                    payload = scenario_payload(run['seller_id'], source)
                    demo=self.store.get('sellers',run['seller_id']) if run['seller_id'].startswith('demo-') else None
                    if demo:
                        payload.update(company_name=demo.get('company') or demo.get('name'),city=demo.get('city') or payload['city'])
                    if source in SOURCES:
                        # Reuse the real adapter's normalization in an isolated namespace.
                        sample_config = copy.deepcopy(self.config.values['connectors'])
                        sample_config['sources'][source]['mode'] = 'sample'
                        sample_connector = self.connector_factory(sample_config)
                        events = await sample_connector.fetch(source, 'sample-' + source)
                        payload['records'] = sample_connector.raw_records
                    elif source == 'relationships':
                        payload = {'glid': run['seller_id'], 'buyers': [{'glid': 'scenario-buyer-1', 'relationship': 'synthetic_call'}]}
                    elif source == 'buyer_details':
                        payload = {'glid': run['seller_id'], 'name': 'Synthetic buyer', 'city': 'Hyderabad', 'state': 'Telangana'}
                    elif source == 'product_context':
                        payload = {'glid': run['seller_id'], 'product': 'Packaging machine', 'specifications': {'power': ['Single phase', 'Three phase']}}
                    elif source == 'package_catalog':
                        payload = self.config.values['operations']['packages']
                    retrieval = 'fixture'
                elif source in EXTRA_SOURCES:
                    from backend.context_adapters import ContextAdapter
                    fetched=await ContextAdapter(self.config).fetch(source,run['seller_id'])
                    payload=fetched['payload']
                    exchanges=[fetched['exchange']]
                    retrieval='live_api'
                else:
                    events = await connector.fetch(source, run['seller_id'])
                    raw = connector.raw_records
                    payload = {**(raw[0] if len(raw)==1 else {}), 'glid': run['seller_id'].removeprefix('real-'), 'records': raw}
                    exchanges = redact(connector.exchanges)
                    retrieval = 'live_api'
                self.check_contact(run['seller_id'])
                if self.config.admin.revision != config_revision:
                    raise ValueError('Configuration changed during source request; result not applied.')
                # A configured Sample in a connected run is inspectable but isolated.
                sample_subject = run['subject_id'] if run['mode'] == 'sample' or mode != 'sample' else 'scenario-' + digest(run['seller_id'])[:16]
                snap = snapshot(redact(payload), sample_subject, source, mode, config_revision,
                                spec.get('ttl_hours', 24), retrieval)
                snap.update({'scope': scope, 'run_id': run['id'], 'events': events, 'exchanges': exchanges,
                             'completeness': {'records': len(payload.get('records', [])),
                                              'limit': connector.max_records, 'possibly_truncated': len(payload.get('records', [])) >= connector.max_records}})
                snap = self.repo.put('snapshots', snap)
                self.attach(run['id'], snap['id'])
                self.record_retrieval(run['id'], snap['id'], retrieval)
                parameters = {'seller_id': run['seller_id']}
                if source in REDASH_SQL and mode == 'live':
                    parameters['query'] = REDASH_SQL[source].format(glid=require_approved(run['seller_id']), limit=connector.max_records)
                self.repo.event(run['id'], source, 'sample_replayed' if mode=='sample' else 'completed', source=source, mode=mode,
                                snapshot_id=snap['id'], duration_ms=round((time.monotonic()-start)*1000),
                                request={'method': spec.get('method','READ'), 'endpoint': spec.get('path','registered_adapter'), 'parameters': parameters, 'headers': {'authentication': '[server-held]'}},
                                response={'snapshot_id': snap['id'], 'records': snap['completeness']['records'], 'full_payload': '/api/context-snapshots/' + snap['id']},
                                indexing_status='pending')
                # Initial facts become usable before background requests complete.
                if source in {'company','context'}:
                    self.initial_context(run['id'],snap)
                return snap
            except (ConnectorError, ValueError, SourcePolicyError, httpx.HTTPError) as error:
                self.repo.event(run['id'], source, getattr(error,'code', 'unavailable'), source=source, mode=mode,
                                duration_ms=round((time.monotonic()-start)*1000), message='Source transport failed; no sample fallback.' if isinstance(error,httpx.HTTPError) else str(error)[:350])
                return None

    def attach(self, run_id, snapshot_id):
        """Single event-loop writes merge IDs after each independent completion."""
        run = self.repo.get('runs', run_id)
        self.save_run(run_id, snapshot_ids=list(dict.fromkeys([*run['snapshot_ids'], snapshot_id])))

    def record_retrieval(self, run_id, snapshot_id, retrieval):
        """Retrieval is run-specific; fetching a cache never rewrites source age."""
        run = self.repo.get('runs', run_id)
        self.save_run(run_id, retrievals={**run.get('retrievals', {}), snapshot_id: {'location': retrieval, 'at': now()}})

    def initial_context(self,run_id,snap):
        """Publish usable facts and wording without waiting for slower history/LLM."""
        run=self.repo.get('runs',run_id)
        if snap['subject_id']!=run['subject_id']:
            return
        facts={key:field for key,field in snap['fields'].items() if key in {'company_name','city','state','products','business_nature','language'} and field['availability']=='present'}
        layers=self.layers.resolve(run['subject_id'],run_id)
        self.save_run(run_id,initial_context={**run.get('initial_context',{}),**facts},initial_ready_at=run.get('initial_ready_at') or now(),phase='enriching',layers=layers,teleprompter=self.teleprompter(run,[snap],layers))

    async def related_context(self, run, snapshots):
        """Bound buyer fan-out; buyer claims never enter seller factor extraction."""
        from backend.context_adapters import ContextAdapter, related_buyers
        records=[record for snap in snapshots for record in snap['payload'].get('records',[]) if isinstance(record,dict)]
        records += [{'buyer_glid':buyer.get('glid')} for snap in snapshots if snap['source']=='relationships' for buyer in snap['payload'].get('buyers',[]) if buyer.get('glid')]
        buyers=related_buyers(records,self.config.values['operations']['max_related_buyers'])
        sem=asyncio.Semaphore(self.config.values['operations']['buyer_concurrency'])
        async def fetch(buyer):
            async with sem:
                node='buyer-'+digest(buyer)[:12]
                mode=self.config.values['operations']['sources']['buyer_details']['mode'] if run['mode']=='live' else run['mode']
                self.repo.event(run['id'],node,'started',source='buyer_details',mode=mode)
                try:
                    if run['mode']=='live':
                        require_approved(run['seller_id']);require_approved(buyer)
                    if mode=='sample':
                        payload={'glid':buyer,'name':'Synthetic related buyer','requirement':'Packaging machinery','actor':'buyer'}
                    elif mode=='live':
                        self.check_contact(run['seller_id'])
                        fetched=await ContextAdapter(self.config).fetch('buyer_details',run['seller_id'],buyer)
                        payload=fetched['payload']
                    else:
                        raise ValueError('Related buyer source is '+mode+'; no upstream lookup performed.')
                    subject=('scenario-buyer-' if mode=='sample' else 'buyer-')+digest(buyer)[:16]
                    snap=snapshot(redact(payload),subject,'buyer_details',mode,run['config_revision'],720,'fixture' if mode=='sample' else 'live_api')
                    snap.update(actor='buyer',run_id=run['id'],related_seller=run['subject_id'])
                    self.repo.put('snapshots',snap)
                    self.repo.event(run['id'],node,'sample_replayed' if mode=='sample' else 'completed',source='buyer_details',mode=mode,snapshot_id=snap['id'])
                    return snap['id']
                except (ValueError,httpx.HTTPError) as error:
                    self.repo.event(run['id'],node,'unavailable',source='buyer_details',mode=mode,message=str(error)[:250] if isinstance(error,ValueError) else 'Buyer detail request failed.')
                    return None
        if buyers:
            result=await asyncio.gather(*(fetch(x) for x in buyers))
            self.save_run(run['id'],related_buyer_snapshot_ids=[x for x in result if x])

    async def execute(self, run_id):
        """Produce conservative initial context; slower tasks remain visibly pending."""
        run = self.save_run(run_id, status='running', started_at=now())
        start = time.monotonic()
        try:
            seller = self.store.get('sellers', run['seller_id']) or {'id': run['subject_id'], 'source_mode': 'sample'}
            if seller.get('metrics', {}).get('do_not_call') or seller.get('persona_tag') == 'contact-suppressed' or seller.get('contact_suppressed'):
                raise ValueError('Contact is suppressed; preparation stopped.')
            if run['mode'] == 'live':
                require_approved(run['seller_id'])
            self.layers.baseline(run['subject_id'], seller, scenario=run['mode']=='sample')
            self.save_run(run_id, initial_ready_at=now(), teleprompter=self.teleprompter(run, []))
            sem = asyncio.Semaphore(self.config.values['operations']['max_concurrent_sources'])
            tasks = [self.source(run, source, sem, force=run.get('force_sources', False)) for source in sorted(run['sources'], key=lambda x: x not in {'company','context'})]
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=self.config.values['operations']['run_timeout_seconds'])
            snapshots = [x for x in results if x and x['subject_id'] == run['subject_id']]
            await self.related_context(run,snapshots)
            for snap in snapshots:
                language = snap['fields'].get('language', {})
                if language.get('availability') == 'present' and language['value'] in {'en-IN','hi-IN','te-IN'}:
                    self.layers.observe(run['subject_id'], 'language', language['value'],
                        source='explicit_source_field', confidence=.7,
                        citations=[{'snapshot_id':snap['id'],'pointer':language['pointer']}],
                        business_id=digest([snap['source'],snap['content_hash'],'language']),
                        observed_at=language.get('observed_at') or snap['retrieved_at'])
            layers = self.layers.resolve(run['subject_id'], run_id)
            if run['use_llm'] and snapshots:
                self.repo.event(run_id, 'sarvam', 'started', mode=run['mode'], source='sarvam', message='Structured extraction through the designated Sarvam account.')
                self.save_run(run_id, provider_status='started')
                try:
                    enriched = await self.sarvam.extract(snapshots, layers['prompt_inspector']['rendered'])
                    for factor in enriched['factors']:
                        cited = [x for x in snapshots if x['id'] in factor['snapshot_ids']]
                        event_dates = [event.get('observed_at') for snap in cited for event in snap.get('events', []) if event.get('observed_at')]
                        explicit_dates = [snap['payload'].get('observed_at') for snap in cited if snap['payload'].get('observed_at')]
                        dates = explicit_dates or event_dates
                        business_keys = sorted({event.get('source_ref') or event.get('id') for snap in cited for event in snap.get('events', []) if event.get('source_ref') or event.get('id')})
                        business_keys = business_keys or [x['content_hash'] for x in cited]
                        self.layers.observe(run['subject_id'], factor['factor'], factor['value'], source='sarvam_extraction', confidence=factor['confidence'],
                            citations=[{'snapshot_id': sid, 'pointer': '/payload', 'reason': factor.get('reason')} for sid in factor['snapshot_ids']],
                            business_id=digest([factor['factor'], business_keys]), evidence_keys=business_keys,
                            observed_at=max(dates) if dates else None, age_known=bool(dates), run_id=run_id)
                    self.repo.event(run_id, 'sarvam', 'completed', mode=run['mode'], source='sarvam', duration_ms=enriched['duration_ms'], response=enriched)
                    self.save_run(run_id, provider_status='completed', summary=enriched['summary'], inference_prompt=enriched.get('prompt_inspector'))
                except ValueError as error:
                    self.repo.event(run_id, 'sarvam', 'failed', source='sarvam', mode=run['mode'], message=str(error))
                    self.save_run(run_id, provider_status='failed')
            layers = self.layers.resolve(run['subject_id'], run_id)
            for factor in layers['effective']:
                self.repo.put('factors', factor)
            self.repo.event(run_id, 'persona', 'resolved', source='persona_resolver', mode=run['mode'],
                            response={'factor_count': len(layers['effective']), 'established_weight': layers['policy']['established_weight'], 'indexing_status': 'pending'})
            self.save_run(run_id, status='completed', phase='ready', finished_at=now(),
                          duration_ms=round((time.monotonic()-start)*1000), layers=layers,
                          teleprompter=self.teleprompter(run, snapshots, layers))
            self.repo.event(run_id, 'run', 'completed', mode=run['mode'], duration_ms=round((time.monotonic()-start)*1000), message='Preparation finished; inspect each source outcome. No outbound call was placed.')
        except asyncio.CancelledError:
            self.save_run(run_id, status='interrupted', error='Worker stopped; paid requests are not automatically repeated.')
            raise
        except Exception as error:
            self.save_run(run_id, status='failed', error=str(error)[:300] if isinstance(error,(ValueError,SourcePolicyError)) else 'Preparation failed; retained receipts are available.')
            self.repo.event(run_id, 'run', 'failed', mode=run['mode'], message=self.repo.get('runs',run_id)['error'])

    def teleprompter(self, run, snapshots, layers=None):
        """Ground business wording in known fields and preserve missing defaults."""
        fields = {key: value for snap in snapshots for key,value in snap['fields'].items() if value['availability']=='present'}
        company = fields.get('company_name', {}).get('value')
        values = {x['factor']: x['value'] for x in (layers or {}).get('effective', [])}
        language=values.get('language','en-IN')
        formal=values.get('formality') in {'formal','professional','high'}
        warm=values.get('warmth') in {'warm','high','empathetic','friendly'}
        subject=company or ('आपका व्यवसाय' if language=='hi-IN' else 'మీ వ్యాపారం' if language=='te-IN' else 'this business')
        if language=='hi-IN':
            opening=f"नमस्ते, मैं IndiaMART का AI सहायक हूँ। क्या आप {subject} संभालते हैं? "
            opening+=('आपसे बात करने का अवसर देने के लिए धन्यवाद। ' if warm else '')
            opening+= 'क्या संक्षिप्त बातचीत के लिए यह सुविधाजनक समय है?' if formal else 'क्या अभी थोड़ी बात कर सकते हैं?'
            question='अभी आपके लिए क्या ज़रूरी है: उपयोगी पूछताछ, उत्पाद की दृश्यता, या किसी समस्या का समाधान?'
            objection='आपकी बात समझता हूँ। एक उदाहरण बताइए ताकि हमारे मानव प्रतिनिधि आपकी समस्या ठीक से समझ सकें।' if warm else 'कृपया एक उदाहरण दीजिए; इसे मानव प्रतिनिधि के लिए दर्ज करूँगा।'
        elif language=='te-IN':
            opening=f"నమస్కారం, నేను IndiaMART AI సహాయకుడిని. మీరు {subject} నిర్వహిస్తున్నారా? "
            opening+=('మాట్లాడేందుకు సమయం ఇచ్చినందుకు ధన్యవాదాలు. ' if warm else '')
            opening+='కొద్దిసేపు మాట్లాడేందుకు ఇది అనువైన సమయమా?' if formal else 'ఇప్పుడు కాసేపు మాట్లాడవచ్చా?'
            question='ఇప్పుడు మీకు ఏది ముఖ్యం: సరైన విచారణలు, ఉత్పత్తుల ప్రచారం, లేదా సమస్య పరిష్కారం?'
            objection='మీ ఆందోళన అర్థమైంది. మా మానవ ప్రతినిధి సహాయం చేయడానికి ఒక ఉదాహరణ చెప్పగలరా?'
        else:
            opening=f"Hello, I am an AI assistant helping with IndiaMART enquiries. Am I speaking with the person who manages {subject}? "
            opening+=('Thank you for taking a moment with me. ' if warm else '')
            opening+='Would this be a convenient time for a brief conversation?' if formal else 'Is now a good time for a quick conversation?'
            question='Which matters most right now: relevant enquiries, product visibility, or an issue to resolve?'
            objection='I understand why that matters. Could you share one example so a human executive can address the specific concern?' if warm else 'Please share one example; I will record the concern for a human executive.'
        doubts=[d for snap in snapshots for d in snap.get('parsing',{}).get('doubts',[])]
        prior=fields.get('last_call_summary',{}).get('value')
        return {'opening':opening,'next_question':question,'objection_response':objection,
                'escalation':'Confirm identity, address and willingness before a test meeting. Route account, payment, wrong-number and unresolved service issues to a human.',
                'delivery':values,'prior_interaction':prior,'unresolved_doubts':doubts,
                'reasons':['Identity and permission come first.',f"Wording uses {language}, {values.get('formality','default')} formality and {values.get('warmth','default')} warmth.",'Historical summaries and upstream doubts are confirmation prompts, not established truth.','Only catalog-backed features/prices may be offered.'],
                'citations':[{'snapshot_id':x['id'],'pointer':'/payload'} for x in snapshots]}

    def replay(self, path):
        """Privately import the exact supplied example without any network access."""
        data = json.loads(Path(path).read_text())
        from backend.context_contract import parse_production
        parsed = parse_production(data)
        subject = 'replay-' + str(parsed['vendor']['glid'])
        existing=next((x for x in self.repo.list('snapshots',subject) if x.get('original_input')==data),None)
        if existing:
            return existing
        snap = snapshot(data, subject, 'supplied_example', 'local', self.config.admin.revision,
                        2160, 'supplied_example', supplied=True)
        return self.repo.put('snapshots', snap)
