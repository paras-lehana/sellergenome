/* Protected source-refresh controls; public visitors receive aggregate readiness only. */
const { api, badge, busy, date, e, empty, human, invalid, state, toast } = await import('./core.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content || 'dev'));

let pollTimer;
let generation = 0;
let lastSnapshot = '';
let sourceTargets={live:[],sample:[]},sourceModes={};

/** End polling before route changes, access changes or page replacement. */
export function stopSyncPolling() {
  clearTimeout(pollTimer);
  generation += 1;
  lastSnapshot = '';
}

/** Source-specific jobs disclose attempts and safe results without raw context. */
function jobRows(items) {
  if (!items.length) return empty('No refresh jobs yet', 'Queue a refresh for a private seller to follow its progress here.');
  const rows = items.map(job => {
    const active = ['queued', 'running', 'retry_wait'].includes(job.state);
    const result = job.result;
    return `<article class="sync-job" data-job-id="${e(job.id)}">
      <div class="spread"><strong>${e(human(job.source))}</strong>${badge(job.state)}</div>
      <p class="small muted">${e(job.seller_id)} · Attempt ${e(job.attempts)} / ${e(job.max_attempts)} · ${date(job.updated_at)}</p>
      ${job.error_code ? `<p class="small sync-error">${e(human(job.error_code))}${job.state === 'retry_wait' ? ' · Retry due ' + date(job.available_at) : ' · Check source access or configuration before retrying.'}</p>` : ''}
      ${result ? `<p class="small">${e(result.changed_records)} changed · ${e(result.skipped_records)} unchanged or older · ${e(result.mapped_records)} mapped. Index delivery at commit: ${e(result.indexing)}.</p>` : ''}
      <div class="row">${active ? `<button class="button button-small" data-action="cancel-sync-job" data-id="${e(job.id)}">Cancel job</button>` : ['failed', 'cancelled'].includes(job.state) ? `<button class="button button-small" data-action="retry-sync-job" data-id="${e(job.id)}">Retry job</button>` : ''}
      <details class="native-details"><summary>Receipt</summary><pre class="code-block">${e(JSON.stringify(job, null, 2))}</pre></details></div>
    </article>`;
  });
  return `<div class="sync-jobs">${rows.slice(0, 5).join('')}${rows.length > 5 ? `<details class="native-details" data-older-jobs><summary>Show ${rows.length - 5} older jobs</summary><div class="sync-jobs">${rows.slice(5).join('')}</div></details>` : ''}</div>`;
}

/** Paused schedules retain settings and explain that current work is separate. */
function scheduleRows(items) {
  if (!items.length) return empty('No automatic refreshes configured', 'Save a paused schedule below. Enable it when source access is ready.');
  return `<div class="sync-jobs">${items.map(schedule => `<article class="sync-job" data-schedule-id="${e(schedule.id)}">
    <div class="spread"><strong>${e(human(schedule.source))}</strong>${badge(schedule.enabled ? 'Enabled' : 'Paused')}</div>
    <p class="small muted">${e(schedule.seller_id)} · Every ${e(schedule.interval_seconds / 60)} minutes${schedule.enabled ? ' · Next ' + date(schedule.next_run) : ''}</p>
    <div class="row"><button class="button button-small" data-action="toggle-sync-schedule" data-id="${e(schedule.id)}" data-enabled="${schedule.enabled ? 'false' : 'true'}">${schedule.enabled ? 'Pause schedule' : 'Enable schedule'}</button>
    <button class="button button-small" data-action="edit-sync-schedule" data-id="${e(schedule.id)}" data-source="${e(schedule.source)}" data-seller="${e(schedule.seller_id)}" data-interval="${e(schedule.interval_seconds)}" data-enabled="${schedule.enabled}">Edit interval</button></div>
  </article>`).join('')}</div>`;
}

/** Aggregate delivery counts remain honest while Elasticsearch is unavailable. */
function statusMarkup(data) {
  const counts = data.counts || {};
  const indexing = data.indexing || {};
  return `<div class="sync-summary">
    <span>${badge(data.worker?.status || 'Not checked')} worker</span>
    <span><strong>${e((counts.queued || 0) + (counts.retry_wait || 0))}</strong> waiting</span>
    <span><strong>${e(counts.running || 0)}</strong> running</span>
    <span><strong>${e(indexing.pending || 0)}</strong> index deliveries pending</span>
    <span><strong>${e(data.enabled_schedules || 0)}</strong> enabled schedules</span>
  </div>`;
}

/** Operator-only fetch errors stay inside this panel, preserving source readiness. */
export async function renderSyncPanel(integrations) {
  const heading = `<section class="panel page-section" id="source-refresh"><div class="panel-heading"><div><h2>Seller context refresh</h2><p>Queue recent context, inspect the result, and choose when it refreshes again.</p></div></div><div class="panel-body">`;
  if (!state.operator) return heading + statusMarkup(integrations.refresh || {}) + `<p class="small muted divider">Job history and refresh schedules require operator access. Public visitors can inspect aggregate readiness.</p><a class="button" href="#settings">Open access settings</a></div></section>`;
  try {
    const [jobs, schedules, registry] = await Promise.all([api('/sync-jobs'), api('/sync-schedules'),api('/sources/targets')]);
    sourceTargets={live:registry.live||registry.items||[],sample:registry.sample||[]};sourceModes=Object.fromEntries(integrations.items.map(item=>[item.id,item.mode]));
    const sources = integrations.items.filter(item => !['elasticsearch', 'sarvam', 'llm'].includes(item.id));
    const selectedSource=sources.find(item=>item.mode!=='disabled')?.id||sources[0]?.id;const validSeller=targetOptions(selectedSource).length>0;
    return heading + `<div id="sync-summary">${statusMarkup(jobs)}</div>
      <form id="source-refresh-form" class="stack divider">
        <h3>Queue or schedule a source refresh</h3>
        <p class="small muted" id="refresh-selected">${e(targetHint(selectedSource))}</p>
        <div class="grid-two"><div class="field"><label for="refresh-source">Source</label><select id="refresh-source" name="source">${sources.map(source => `<option value="${e(source.id)}" ${source.id===selectedSource?'selected':''}>${e(source.name)} · ${e(source.mode||'unknown')}</option>`).join('')}</select></div>
        <div class="field"><label for="refresh-interval">Refresh every (minutes)</label><input id="refresh-interval" name="interval_minutes" type="number" min="${e(schedules.limits.min_interval_seconds / 60)}" max="${e(schedules.limits.max_interval_seconds / 60)}" step="1" value="60"><small>Used for schedules. Queue now runs once.</small></div></div>
        <div class="field"><label for="refresh-target">Allowed target for this source</label><select id="refresh-target" name="seller_id">${targetChoices(selectedSource)}</select></div>
        <label class="checkbox-row"><input type="checkbox" name="enabled"> Enable scheduled refreshes</label>
        <p class="small muted">Schedules start paused. The first enabled run is one interval from saving. Pausing stops future runs; cancel an existing job separately.</p>
        <div class="row"><button class="button button-primary" type="submit" value="queue" ${validSeller ? '' : 'disabled'}>Queue refresh now</button><button class="button" type="submit" value="schedule" ${validSeller ? '' : 'disabled'}>Save refresh schedule</button><button class="button" type="button" data-action="retry-index-deliveries" ${jobs.indexing.pending ? '' : 'disabled'}>Retry index delivery</button></div>
        <p class="small muted">Refresh uses the configured source endpoint. Missing credentials or an unreachable source produce a visible failure receipt.</p>
      </form><div id="sync-feedback" class="small" role="status"></div>
      <div class="spread divider"><p class="small muted">Activity updates every five seconds while this page is visible.</p><button class="button button-small" data-action="refresh-sync-jobs">Refresh activity</button></div>
      <div class="grid-two"><section><h3>Recent jobs</h3><div id="sync-job-list">${jobRows(jobs.items)}</div></section><section><h3>Refresh schedules</h3><div id="sync-schedule-list">${scheduleRows(schedules.items)}</div></section></div>
      </div></section>`;
  } catch (error) {
    return heading + `<div class="error-state" role="alert"><h3>Refresh controls need operator access</h3><p>${e(error.message)}</p><a class="button" href="#settings">Review workspace access</a></div></div></section>`;
  }
}

/** Update activity only; preserve form values and focused controls during polling. */
async function refreshActivity(expectedGeneration = generation) {
  const key = state.operator;
  if (!key || state.route !== 'integrations' || !document.querySelector('#sync-job-list')) return;
  const [jobs, schedules] = await Promise.all([api('/sync-jobs'), api('/sync-schedules')]);
  if (generation !== expectedGeneration || key !== state.operator || state.route !== 'integrations') return;
  const snapshot = JSON.stringify({items: jobs.items, schedules: schedules.items, counts: jobs.counts, indexing: jobs.indexing});
  if (snapshot === lastSnapshot) return;
  let skippedFocusedRegion = false;
  for (const [id, markup] of [['sync-job-list', jobRows(jobs.items)], ['sync-schedule-list', scheduleRows(schedules.items)], ['sync-summary', statusMarkup(jobs)]]) {
    const node = document.getElementById(id);
    if (node?.contains(document.activeElement)) { skippedFocusedRegion = true; continue; }
    if (node) {
      const openReceipts = [...node.querySelectorAll('details[open]')].map(detail => detail.closest('[data-job-id]')?.dataset.jobId).filter(Boolean);
      const olderOpen = node.querySelector('[data-older-jobs]')?.open;
      node.innerHTML = markup;
      if (olderOpen) node.querySelector('[data-older-jobs]')?.setAttribute('open', '');
      for (const id of openReceipts) { const detail = node.querySelector('[data-job-id="' + id + '"] details'); if (detail) detail.open = true; }
    }
  }
  lastSnapshot = skippedFocusedRegion ? '' : snapshot;
  const deliveryButton = document.querySelector('[data-action="retry-index-deliveries"]');
  if (deliveryButton) deliveryButton.disabled = !jobs.indexing.pending;
}

/** Poll only the active authenticated view, with no overlapping requests. */
export function startSyncPolling() {
  const currentGeneration = generation;
  const next = async () => {
    if (currentGeneration !== generation || state.route !== 'integrations') return;
    if (!document.hidden) {
      try { await refreshActivity(currentGeneration); }
      catch (error) { const node = document.querySelector('#sync-feedback'); if (node) node.textContent = error.message; }
    }
    if (currentGeneration === generation) pollTimer = setTimeout(next, 5000);
  };
  if (state.operator && document.querySelector('#sync-job-list')) pollTimer = setTimeout(next, 5000);
}

/** Centralized protected actions show receipts and preserve safe retry semantics. */
export async function handleSyncAction(action, button) {
  if (!['refresh-sync-jobs', 'cancel-sync-job', 'retry-sync-job', 'toggle-sync-schedule', 'edit-sync-schedule', 'retry-index-deliveries', 'sync-integration'].includes(action)) return false;
  if (action === 'edit-sync-schedule') {
    const form = document.querySelector('#source-refresh-form');
    form.elements.source.value = button.dataset.source;
    updateTargetPicker();
    form.elements.seller_id.value = button.dataset.seller;
    if(!form.elements.seller_id.value)throw new Error('This schedule target is not allowed by the current source mode. Review its configuration before editing.');
    form.elements.interval_minutes.value = Number(button.dataset.interval) / 60;
    form.elements.enabled.checked = button.dataset.enabled === 'true';
    form.querySelector('[value="queue"]').disabled = false;
    form.querySelector('[value="schedule"]').disabled = false;
    document.querySelector('#refresh-selected').textContent = 'Editing ' + human(button.dataset.source) + ' schedule for ' + button.dataset.seller;
    form.elements.interval_minutes.focus();
    form.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    return true;
  }
  const message = await busy(button, 'Updating refresh…', async () => {
    switch (action) {
      case 'refresh-sync-jobs': return 'Activity refreshed.';
      case 'cancel-sync-job': await api('/sync-jobs/' + encodeURIComponent(button.dataset.id) + '/cancel', {method: 'POST'}); return 'Job cancelled. Any in-flight result will be discarded.';
      case 'retry-sync-job': { const result=await api('/sync-jobs/' + encodeURIComponent(button.dataset.id) + '/retry', {method: 'POST', body: JSON.stringify({request_key: crypto.randomUUID()})}); return result.created ? 'Retry queued with a new receipt.' : 'A refresh is already active. Follow its existing receipt.'; }
      case 'toggle-sync-schedule': await api('/sync-schedules/' + encodeURIComponent(button.dataset.id), {method: 'PATCH', body: JSON.stringify({enabled: button.dataset.enabled === 'true'})}); return button.dataset.enabled === 'true' ? 'Schedule enabled. First run follows the configured interval.' : 'Schedule paused. Existing jobs can be cancelled separately.';
      case 'retry-index-deliveries': await api('/index-deliveries/retry', {method: 'POST'}); return 'Pending index deliveries made due.';
      case 'sync-integration':
        {const form=document.querySelector('#source-refresh-form');if(!form)throw new Error('Sign in to choose a source target.');form.elements.source.value=button.dataset.id;updateTargetPicker();form.scrollIntoView({block:'start',behavior:'instant'});return 'Source selected. Review its allowed target, then queue or schedule a refresh.';}
    }
  });
  // Explicit actions refresh immediately; blur lets the changed action label update.
  if (button.isConnected && !['refresh-sync-jobs', 'retry-index-deliveries'].includes(action)) button.blur();
  await refreshActivity();
  const node = document.querySelector('#sync-feedback');
  if (node) node.textContent = message;
  toast(message);
  return true;
}

/** One seller/source form supports a one-off job or an explicitly enabled schedule. */
export async function handleSyncSubmit(form, values, submitter) {
  if (form.id !== 'source-refresh-form') return false;
  const sellerId = String(values.get('seller_id') || '');
  if (!sellerId) throw new Error('Select an allowed target for the current source mode.');
  const source = String(values.get('source'));
  const schedule = submitter?.value === 'schedule';
  const interval = Number(values.get('interval_minutes')) * 60;
  if (schedule && (!Number.isFinite(interval) || !form.elements.interval_minutes.checkValidity())) {
    invalid(form.elements.interval_minutes, 'Choose a refresh interval within the configured range.');
    return true;
  }
  const result=await busy(submitter, schedule ? 'Saving schedule…' : 'Queueing refresh…', () => schedule
    ? api('/sync-schedules', {method: 'POST', body: JSON.stringify({seller_id: sellerId, source, interval_seconds: interval, enabled: values.get('enabled') === 'on'})})
    : api('/integrations/' + encodeURIComponent(source) + '/jobs', {method: 'POST', body: JSON.stringify({seller_id: sellerId, request_key: crypto.randomUUID()})}));
  await refreshActivity();
  const message = schedule ? values.get('enabled') === 'on' ? 'Schedule enabled. The first run follows the configured interval.' : 'Paused schedule saved.' : result.created ? 'Refresh queued. Its receipt updates in Recent jobs.' : 'A refresh is already active. Follow its existing receipt.';
  const feedback = document.querySelector('#sync-feedback');
  if (feedback) feedback.textContent = message;
  toast(message);
  return true;
}

/** Sample sources have one isolated fixture target; live sources offer only the server's approved test registry. */
function targetOptions(source){return sourceModes[source]==='sample'?sourceTargets.sample.filter(t=>t.source===source):sourceModes[source]==='live'?sourceTargets.live:[];}
function targetHint(source){return sourceModes[source]==='sample'?'Sample mode uses its isolated fixture seller. It cannot write sample evidence onto a real seller.':sourceModes[source]==='live'?'Live mode permits only the supplied approved test GLIDs.':'This source is disabled. An administrator can choose Sample or Live mode.';}
function targetChoices(source){const choices=targetOptions(source);return choices.length?choices.map(t=>`<option value="${e(t.seller_id||t.id)}" ${(t.seller_id||t.id)===state.seller?.id?'selected':''}>${e(t.label||t.seller_id||t.id)}</option>`).join(''):'<option value="">No target available for this source mode</option>';}
function updateTargetPicker(){const form=document.querySelector('#source-refresh-form');if(!form)return;const source=form.elements.source.value;form.elements.seller_id.innerHTML=targetChoices(source);document.querySelector('#refresh-selected').textContent=targetHint(source);for(const button of form.querySelectorAll('[value="queue"],[value="schedule"]'))button.disabled=!targetOptions(source).length;}
export function handleSyncChange(event){if(event.target.id==='refresh-source')updateTargetPicker();}
