// API inspector drawer (parse.lehana.in style): Overview · Request · Response · Elastic · Fields fed · Events.
import { api, session } from './api.js';
import { $, copyText, download, esc, fmtMs, origin, toast } from './ui.js';
import { highlight, treeBlock } from './json.js';

const drawer = () => $('#inspector');
let state = null;

const NODE_HELP = {
  trigger: 'The platform activity that made IndiaMART consider this seller a hot lead (e.g. viewing a paid package).',
  orchestrator: 'SellerGenome’s run: fans out to every source in parallel (company and context first), then aggregates.',
  facts: 'The first usable facts, published before slower enrichment finishes — the bot could start talking here.',
  contract: 'Every API response is normalised into the production persona contract: 28 vendor fields + custtypeweight, each with its origin.',
  'persona.index': 'Persona and factor documents written to Elasticsearch with typed value slots — LLM-invented factor names are values, not new mappings.',
  teleprompter: 'What to say next, built from the persona, doubts, insights and the call stage.',
  'voice.plugin': 'Where an external voice bot (LiveKit or Sarvam Voice Agents) plugs in: it pulls this context and pushes call events.',
  elastic: 'Elasticsearch: every API is looked up here first (cache) and every response is written back asynchronously.',
};

export function closeInspector() {
  const d = drawer();
  d.setAttribute('aria-hidden', 'true');
  d.inert = true;
  $('#scrim').hidden = true;
  state?.returnFocus?.focus?.();
  state = null;
}

export async function openInspector({ runId, node, focus = '', events = [], title = '', returnFocus = null, extra = null, siblings = null }) {
  const d = drawer();
  state = { runId, node, focus, events, tab: focus ? 'response' : 'overview', sub: 'json', data: null, fork: null, returnFocus: returnFocus || document.activeElement, extra, siblings };
  d.innerHTML = `<div class="ins-head"><span class="eyebrow">Inspector</span><h2>${esc(title || node)}</h2><button class="icon-btn x" data-close aria-label="Close inspector"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div><div class="ins-body"><div class="skeleton" style="height:120px"></div></div>`;
  d.setAttribute('aria-hidden', 'false');
  d.inert = false;
  $('#scrim').hidden = false;
  d.querySelector('[data-close]').focus();
  d.onclick = onClick;
  d.onkeydown = e => { if (e.key === 'Escape') closeInspector(); };
  $('#scrim').onclick = closeInspector;
  if (extra) { state.data = extra; render(); return; }
  await loadNode(node);
}

async function loadNode(node) {
  const mine = state;
  state.node = node; state.fork = null; state.editing = false;
  let data;
  try { data = await api(`/api/v5/runs/${encodeURIComponent(mine.runId)}/nodes/${encodeURIComponent(node)}`); }
  catch (err) { data = { node, result: null, missing: err.status === 404 ? null : err.message }; }
  // Closed (Esc) or opened on another node while this response was on its way: drop it (live QA, 5.8.0 console error).
  if (state !== mine || state.node !== node) return;
  state.data = data;
  render();
}

function tabsFor(data) {
  const r = data.result;
  const tabs = [['overview', 'Overview']];
  if (r?.request) tabs.push(['request', 'Request']);
  if (r && 'response' in r) tabs.push(['response', 'Response']);
  if (r?.cache || r?.persist) tabs.push(['elastic', 'Elastic']);
  if (data.fields_fed?.length) tabs.push(['fields', `Fields fed (${data.fields_fed.length})`]);
  tabs.push(['events', 'Events']);
  return tabs;
}

function render() {
  const d = drawer();
  const data = state.data;
  const r = data.result;
  const tabs = tabsFor(data);
  if (!tabs.some(([k]) => k === state.tab)) state.tab = tabs[0][0];
  d.innerHTML = `
    <div class="ins-head"><span class="eyebrow">Inspector</span><h2>${esc(r?.label || data.api?.label || state.node)}</h2><button class="icon-btn x" data-close aria-label="Close inspector"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    ${state.siblings?.length > 1 ? `<div class="ins-siblings" role="group" aria-label="One request per item">${state.siblings.map(k => `<button class="chip ${k === state.node ? 'solid' : ''}" data-sibling="${esc(k)}" aria-pressed="${k === state.node}">${esc(k.split(':')[1])}</button>`).join('')}</div>` : ''}
    <div class="tabs ins-tabs" role="tablist">${tabs.map(([k, l]) => `<button class="tab" role="tab" data-tab="${k}" aria-selected="${state.tab === k}">${esc(l)}</button>`).join('')}</div>
    <div class="ins-body" role="tabpanel">${body()}</div>
    <div class="ins-foot">${footer()}</div>`;
  d.querySelector(`[data-tab="${state.tab}"]`)?.focus();
  if (state.tab === 'response' && state.focus) d.querySelector('.jtree .hl')?.scrollIntoView({ block: 'center' });
}

function banner(r) {
  if (!r) return '';
  const cls = r.status === 'ok' ? 'ok' : r.status === 'skipped' ? 'skip' : 'fail';
  const word = r.status === 'ok' ? (r.origin === 'elastic_cache' ? '✓ Served from Elasticsearch' : '✓ Success') : r.status === 'skipped' ? '○ Skipped by policy' : '✕ Failed';
  return `<div class="ins-banner ${cls}">${word} ${origin(r.origin)}${r.http_status ? `<span class="mono">HTTP ${esc(r.http_status)}</span>` : ''}<span class="ms">[${esc(fmtMs(r.ms) || '0 ms')}]</span></div>`;
}

function body() {
  const data = state.data, r = data.result, api_ = data.api;
  if (state.tab === 'overview') {
    const help = NODE_HELP[state.node] || NODE_HELP[state.node?.split(':')[0]] || '';
    return `${banner(r)}
      ${r?.note ? `<p class="note">${esc(r.note)}</p>` : ''}
      ${help ? `<p class="muted">${esc(help)}</p>` : ''}
      ${data.missing ? `<p class="note">${esc(data.missing)}</p>` : ''}
      ${!r && api_ ? `<p class="note">This run did not call this API (nothing to fetch for this seller). Its configuration is below — <b>Hit it now</b> or edit the URL and params and send.</p>` : ''}
      ${api_ ? `<dl class="kv">
        <dt>API</dt><dd>${esc(api_.label)} <span class="mono dim">${esc(api_.id)}</span></dd>
        <dt>Group</dt><dd>${esc(api_.group)}</dd>
        <dt>Mode</dt><dd>${esc(api_.mode)}${r?.mode && r.mode !== api_.mode ? ` → ran as <b>${esc(r.mode)}</b>` : ''}</dd>
        <dt>Endpoint</dt><dd class="mono" style="font-size:11.5px">${esc(api_.url || '(not supplied)')}</dd>
        <dt>Route</dt><dd>${api_.via_gateway ? (r?.request?.via ? `via gateway → <span class="mono" style="font-size:11.5px">${esc(r.request.via.split('?')[0])}</span>` : api_.sent_via ? 'via the IndiaMART gateway (URL shown to signed-in users)' : 'direct (gateway switched off)') : api_.kind === 'http' ? 'direct' : esc(api_.kind)}${api_.method === 'POST' ? ` · body ${esc(api_.body_format || 'json')}` : ''}</dd>
        <dt>Response shape</dt><dd>${esc({ sample: 'Copied from a real sample', guess: 'Documented guess — contract not supplied', fixed: 'Fixed by policy' }[api_.contract] || api_.contract)}</dd>
        <dt>Reachable here</dt><dd>${esc(api_.reachable)}</dd>
        <dt>Cache TTL</dt><dd>${esc(api_.ttl_hours)} h · read ${api_.cache_read ? 'on' : 'off'} · persist ${api_.persist ? 'on' : 'off'}</dd>
        <dt>Key</dt><dd>${[api_.secret, ...(api_.secrets || [])].filter(Boolean).map(keyLine).join('<br>') || 'none'}</dd>
        <dt>Notes</dt><dd>${esc(api_.notes || '—')}</dd></dl>` : ''}
      ${r?.usage ? `<dl class="kv"><dt>Tokens</dt><dd class="mono">${esc(JSON.stringify(r.usage))}</dd></dl>` : ''}
      ${state.editing && !r ? editor() : ''}
      ${state.fork ? `<div class="card"><div class="card-head"><h3>${esc(state.fork.label)}</h3></div><div class="card-body">${banner(state.fork.result)}${state.fork.result.note ? `<p class="note">${esc(state.fork.result.note)}</p>` : ''}${state.fork.result.request ? `<p class="mono dim" style="font-size:11.5px;overflow-wrap:anywhere">${esc(state.fork.result.request.method)} ${esc(state.fork.result.request.via || state.fork.result.request.url)}</p>` : ''}${treeBlock(state.fork.result.response)}</div></div>` : ''}`;
  }
  if (state.tab === 'request') {
    const req = r.request;
    const sub = state.sub;
    const text = sub === 'curl' ? curl(req) : sub === 'python' ? python(req) : JSON.stringify(req, null, 2);
    return `<div class="ins-strip"><span class="ins-label">Request</span><div class="seg">${['json', 'curl', 'python'].map(s => `<button data-sub="${s}" aria-pressed="${sub === s}">${s === 'json' ? 'JSON' : s === 'curl' ? 'cURL' : 'Python'}</button>`).join('')}</div>
      <div style="display:flex;gap:6px"><button class="btn btn-sm" data-copy="request">Copy</button><button class="btn btn-sm" data-download="request">Download</button></div></div>
      <pre class="code ${sub === 'json' ? '' : 'wrap'}">${sub === 'json' ? highlight(req) : esc(text)}</pre>
      <p class="dim" style="font-size:12px">Secrets show as <code>[server-held]</code>: they are injected on the server and never reach the browser.</p>
      ${state.editing ? editor() : ''}`;
  }
  if (state.tab === 'response') {
    return `<div class="ins-strip"><span class="ins-label">Response${state.focus ? ` · <span class="mono">${esc(state.focus)}</span> highlighted` : ''}</span><div style="display:flex;gap:6px"><button class="btn btn-sm" data-copy="response">Copy JSON</button><button class="btn btn-sm" data-download="response">Download JSON</button></div></div>
      ${banner(r)}${r.response == null ? '<p class="muted">No response body.</p>' : treeBlock(r.response, state.focus)}`;
  }
  if (state.tab === 'elastic') {
    const c = r.cache, p = r.persist;
    return `${c ? `<div class="ins-strip"><span class="ins-label">Cache lookup · ${c.hit ? 'HIT' : 'miss'} · ${esc(fmtMs(c.ms))}</span>${origin(c.hit ? 'elastic_cache' : 'default', c.hit ? 'Hit' : 'Miss')}</div>
      <p class="mono dim" style="font-size:12px">POST ${esc(c.index)}/_search</p><pre class="code">${highlight(c.query)}</pre>` : '<p class="muted">No cache lookup (cache read off, TTL 0, forced re-run or Elasticsearch unavailable).</p>'}
      ${p ? `<div class="ins-strip" style="margin-top:8px"><span class="ins-label">Async write</span></div><dl class="kv"><dt>Index</dt><dd class="mono">${esc(p.index)}</dd><dt>Document</dt><dd class="mono">${esc(p.doc_id)}</dd><dt>Status</dt><dd>${esc(p.status)} — flushed in the background by the outbox; see Events.</dd></dl>` : ''}
      ${r.cached_origin ? `<p class="note">Originally fetched as <b>${esc(r.cached_origin)}</b> at ${esc(r.cached_at)}.</p>` : ''}`;
  }
  if (state.tab === 'fields') {
    return `<table class="table"><thead><tr><th>Field</th><th>Value</th><th>Origin</th><th>Pointer</th></tr></thead><tbody>${state.data.fields_fed.map(f => `
      <tr><td class="mono">${esc(f.field)}</td><td>${esc(typeof f.value === 'object' ? JSON.stringify(f.value) : f.value)}</td><td>${origin(f.origin)}</td><td><button class="btn btn-sm btn-ghost mono" data-ptr="${esc(f.pointer || '')}">${esc(f.pointer || '—')}</button></td></tr>`).join('')}</tbody></table>`;
  }
  const events = (state.data.events?.length ? state.data.events : state.events).filter(e => !state.node || e.node === state.node || e.ref === state.node || state.node === 'run');
  return events.length ? events.map(e => `<details class="trace"><summary><span class="mono dim">#${e.seq}</span><b>${esc(e.label)}</b><span class="dim">${esc(e.kind)}</span>${origin(e.origin)}<span class="mono dim" style="margin-left:auto">${esc((e.t_ms / 1000).toFixed(2))} s ${e.ms != null ? `[${esc(fmtMs(e.ms))}]` : ''}</span></summary>
    <div style="padding:10px;font-size:12.5px;display:grid;gap:6px"><span>${esc(e.detail)}</span>${e.es_query ? `<pre class="code">${highlight(e.es_query)}</pre>` : ''}${e.sample_doc ? `<span class="ins-label">Sample factor document</span><pre class="code">${highlight(e.sample_doc)}</pre>` : ''}</div></details>`).join('') : '<p class="muted">No events recorded for this node.</p>';
}

function keyLine(k) {
  if (!k) return '';
  const exp = k.expires_at ? (k.expired ? ` · <b style="color:var(--err)">expired ${esc(k.expires_at)}</b>` : ` · expires in ${esc(k.expires_in_hours)} h`) : '';
  return `<span class="mono">${esc(k.name)}</span> — ${k.configured ? `configured${k.source ? ` (${esc(k.source)}${k.fingerprint ? `, fp ${esc(k.fingerprint)}` : ''})` : ''}${exp}` : '<b>not configured</b>'}`;
}

// Edit the API TEMPLATE (placeholders stay placeholders; keys are injected by the server), then hit it.
function editor() {
  const a = state.data.api;
  const post = a.method === 'POST';
  const tpl = JSON.stringify(post ? a.body || {} : a.params || {}, null, 2);
  return `<div class="card edit-card"><div class="card-head"><h3>Edit &amp; hit this API</h3><span class="aside dim" style="font-size:12px">Send = a labelled fork; the original receipt never changes</span></div>
    <div class="card-body" style="display:grid;gap:8px">
      <label class="dim" for="edit-url" style="font-size:12px">URL template (${esc(a.method)})</label>
      <input id="edit-url" type="text" spellcheck="false" class="mono" value="${esc(a.url || '')}" ${a.kind === 'http' ? '' : 'disabled'}>
      <label class="dim" for="edit-json" style="font-size:12px">${post ? `Body (${esc(a.body_format || 'json')})` : 'Query params'} — JSON</label>
      <textarea id="edit-json" rows="9" spellcheck="false">${esc(tpl)}</textarea>
      <p class="dim" style="font-size:11.5px">Placeholders the server fills: <code>{glid}</code> <code>{alias}</code> <code>{offerid}</code> <code>{contact_glid}</code> <code>{mapping_id}</code> <code>{source_uid}</code> <code>{empid}</code> (<code>|int</code> sends a number). Keys: <code>{secret}</code>, <code>{secret:NAME}</code> — injected on the server, never shown. Hosts must be on the allow-list.</p>
      <p class="form-error" id="edit-error"></p>
      <div class="row-end" style="margin:0;flex-wrap:wrap;gap:6px"><button class="btn btn-ghost btn-sm" data-cancel-edit>Cancel</button>
        ${state.data.can_save ? '<button class="btn btn-sm" data-save-edit title="Make this the API’s configuration for every future run (revisioned in Admin)">Save as API default</button>' : ''}
        <button class="btn btn-primary btn-sm" data-send-edit>Send edited request</button></div>
      ${!session.operator ? '<p class="dim" style="font-size:11.5px">Synthetic sellers are answered by the production-shaped mock, so an edited URL changes the receipt, not the data. Sign in and open an approved GLID to hit the live endpoint.</p>' : ''}</div></div>`;
}

function readEdit() {
  const a = state.data.api;
  const raw = $('#edit-json').value;
  if (raw.includes('[server-held]')) throw new Error('Keys are never sent from the browser — use {secret} or {secret:NAME}.');
  let obj;
  try { obj = JSON.parse(raw || '{}'); } catch { throw new Error('That is not valid JSON.'); }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('Params and body must be a JSON object.');
  const url = $('#edit-url').value.trim();
  const out = a.method === 'POST' ? { body: obj } : { params: obj };
  if (url && url !== a.url) out.url = url;
  return out;
}

function footer() {
  const r = state.data.result;
  const api_ = state.data.api;
  const canRerun = !!api_ && state.runId;
  return `${canRerun ? `<button class="btn btn-sm" data-rerun>${r ? '↻ Re-run (new receipt)' : '▶ Hit it now'}</button>` : ''}
    ${canRerun && api_.kind === 'http' ? `<button class="btn btn-sm" data-edit>✎ Edit URL &amp; params, hit it</button>` : ''}
    ${r?.response != null ? `<button class="btn btn-sm btn-ghost" data-download="response">Download response</button>` : ''}
    <span class="dim" style="font-size:12px;margin-left:auto;align-self:center">${session.operator ? 'Signed in' : 'Public view — synthetic seller'}</span>`;
}

function curl(req) {
  const lines = [`curl -X ${req.method} '${req.via || req.url}'`];
  Object.entries(req.headers || {}).forEach(([k, v]) => lines.push(`  -H '${k}: ${v}'`));
  if (req.body && req.body_format === 'form') Object.entries(req.body).forEach(([k, v]) => lines.push(`  --data-urlencode '${k}=${v}'`));
  else if (req.body) lines.push(`  -H 'Content-Type: application/json'`, `  -d '${JSON.stringify(req.body)}'`);
  return lines.join(' \\\n');
}

function python(req) {
  return `import httpx\n\nresponse = httpx.${req.method.toLowerCase()}(\n    ${JSON.stringify(req.via || req.url)},\n${req.body ? `    ${req.body_format === 'form' ? 'data' : 'json'}=${JSON.stringify(req.body, null, 4).replace(/\n/g, '\n    ')},\n` : ''}    headers=${JSON.stringify(req.headers || {})},\n    timeout=10,\n)\nprint(response.status_code, response.json())`;
}

async function onClick(e) {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.close !== undefined) return closeInspector();
  if (t.dataset.sibling) return loadNode(t.dataset.sibling);
  if (t.dataset.tab) { state.tab = t.dataset.tab; return render(); }
  if (t.dataset.sub) { state.sub = t.dataset.sub; return render(); }
  const r = state.data.result;
  if (t.dataset.copy) return copyText(t.dataset.copy === 'request' ? (state.sub === 'curl' ? curl(r.request) : state.sub === 'python' ? python(r.request) : JSON.stringify(r.request, null, 2)) : JSON.stringify(r.response, null, 2));
  if (t.dataset.download) return download(`${state.node}-${t.dataset.download}.json`, JSON.stringify(t.dataset.download === 'request' ? r.request : r.response, null, 2));
  if (t.dataset.ptr !== undefined) { state.focus = t.dataset.ptr; state.tab = 'response'; return render(); }
  if (t.dataset.edit !== undefined) { state.editing = true; state.tab = state.data.result?.request ? 'request' : 'overview'; render(); $('#edit-url')?.focus(); return; }
  if (t.dataset.cancelEdit !== undefined) { state.editing = false; return render(); }
  if (t.dataset.saveEdit !== undefined) {
    let changes;
    try { changes = readEdit(); } catch (err) { $('#edit-error').textContent = err.message; $('#edit-json').focus(); return; }
    t.disabled = true; t.textContent = 'Saving…';
    try {
      const saved = await api(`/api/v5/admin/apis/${encodeURIComponent(state.data.api.id)}`, { method: 'PATCH', body: { changes, expected_revision: state.data.registry_revision } });
      state.data.api = { ...state.data.api, ...saved }; state.data.registry_revision += 1;
      toast(`${saved.label}: saved as the default for every future run (Admin → APIs keeps the history)`);
      t.textContent = 'Saved ✓';
    } catch (err) { $('#edit-error').textContent = err.message; t.disabled = false; t.textContent = 'Save as API default'; }
    return;
  }
  if (t.dataset.rerun !== undefined || t.dataset.sendEdit !== undefined) {
    let payload = {};
    if (t.dataset.sendEdit !== undefined) {
      try { payload = readEdit(); }
      catch (err) { $('#edit-error').textContent = err.message; $('#edit-json').focus(); return; }
    }
    t.disabled = true; t.textContent = 'Sending…';
    try {
      const out = await api(`/api/v5/runs/${encodeURIComponent(state.runId)}/nodes/${encodeURIComponent(state.node)}/rerun`, { method: 'POST', body: payload });
      state.fork = out; state.editing = false; state.tab = 'overview';
      toast(out.label);
      render();
    } catch (err) { toast(err.message, 'error'); t.disabled = false; t.textContent = 'Retry'; }
  }
}
