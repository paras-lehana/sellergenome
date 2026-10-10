// Admin (/admin): APIs first (endpoint, params, key, mode, cache, test, hit log), then every other setting.
import { api, session, BASE } from './api.js';
import { $, $$, esc, fmtMs, origin, titleCase, toast } from './ui.js';
import { openInspector, closeInspector } from './inspector.js';

const SECTIONS = [
  ['apis', 'APIs'], ['prompts', 'Prompts'], ['hits', 'API hit log'], ['elastic', 'Elasticsearch'], ['llm', 'LLM (Sarvam)'], ['persona', 'Persona policy'],
  ['personagen', 'Persona generator'], ['engines', 'Voice & engines'], ['app', 'Bot policy'], ['runs', 'Runs & concurrency'], ['floor', 'Call floor simulation'], ['meetings', 'Meetings'],
  ['buyer', 'Buyer VANI'], ['budgets', 'Sarvam budgets'], ['upstream', 'Upstream fan-out'], ['sarvamvoice', 'Sarvam voice agent'], ['voice', 'Voice plug-in'], ['legacy', 'Earlier versions'],
];
const HELP = {
  elastic: 'Cache reads, response persistence and persona indexing can each be switched off; work continues and nothing is deleted.',
  llm: 'All text inference goes through llm.lehana.in to Sarvam with the designated organisation key. Organisation and endpoint are locked.',
  persona: 'Policy weights, not probabilities. Cooldown applies per factor; explicit language/stop requests bypass it.',
  app: 'Decision D10: the bot is IndiaMART’s named virtual assistant and never claims to be human. "explicit_ai" opens with an AI disclosure.',
  floor: 'The floor is a deterministic simulation every viewer sees identically.',
  budgets: 'Daily Sarvam call budgets. Public = all anonymous visitors combined.',
  upstream: 'Gateway URL and on/off live in Admin → APIs → Gateway. Here: the employee id the IndiaMART key belongs to, how many purchased leads are expanded and how many chat threads are read per run, and approved test contacts tried for chats.',
};

export async function mount(root) {
  if (!session.operator) {
    root.innerHTML = `<div class="page"><p class="eyebrow">Admin</p><h1>Sign in to manage SellerGenome</h1><p class="muted" style="margin:8px 0 14px">Admin controls every API the pipeline hits — endpoint, parameters, key, mode and cache — plus Elasticsearch, Sarvam, persona and bot policy.</p><button class="btn btn-primary" id="admin-login">Sign in</button></div>`;
    $('#admin-login').addEventListener('click', () => $('#auth').click());
    return {};
  }
  const s = { section: (location.hash || '#apis').slice(1), apis: null, settings: null };
  root.innerHTML = `<div class="page"><div class="page-head"><div><p class="eyebrow">Control</p><h1>Admin</h1><p>Every change is revisioned and can be rolled back. Keys are write-only: you can replace them, never read them back.</p></div></div>
    <div id="admin-keys-banner" aria-live="polite"></div>
    <div class="admin-search" role="search"><label class="sr" for="admin-q">Search settings and APIs</label>
      <input id="admin-q" type="search" placeholder="Search settings and APIs — e.g. gateway, cooldown, voice, pinned" autocomplete="off" spellcheck="false">
      <span class="dim" id="admin-q-count" aria-live="polite"></span></div>
    <div class="admin"><nav class="admin-nav" aria-label="Admin sections">${SECTIONS.map(([k, l]) => `<button data-sec="${k}" aria-current="${k === s.section}">${esc(l)}</button>`).join('')}</nav><div id="admin-body"><div class="skeleton" style="height:300px"></div></div></div></div>`;
  root.addEventListener('click', e => onClick(s, e));
  root.addEventListener('submit', e => onSubmit(s, e));
  let typing = 0;
  $('#admin-q').addEventListener('input', e => { clearTimeout(typing); typing = setTimeout(() => { s.query = e.target.value.trim(); render(s); }, 120); });
  $('#admin-q').addEventListener('keydown', e => { if (e.key === 'Escape' && e.target.value) { e.target.value = ''; s.query = ''; render(s); } });
  await load(s);
  const tick = setInterval(() => paintCountdowns(), 30000);
  return { destroy() { clearInterval(tick); closeInspector(); } };
}

async function load(s) {
  try { [s.apis, s.settings, s.keys, s.prompts] = await Promise.all([api('/api/v5/admin/apis'), api('/api/v5/admin/settings'), api('/api/v5/admin/keys'), api('/api/v5/admin/prompts')]); }
  catch (err) { $('#admin-body').innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  render(s);
}

async function reloadPrompts(s, openId) {
  s.prompts = await api('/api/v5/admin/prompts');
  if (s.section !== 'prompts' || s.query) return render(s);
  render(s);
  const card = openId && document.querySelector(`[data-prompt-card="${CSS.escape(openId)}"]`);
  if (card) { card.open = true; card.scrollIntoView({ block: 'nearest' }); }
}

const AK_SLOTS = new Set(['IM_EMPLOYEE_AK', 'USERCTX_API_KEY']);  // slots that take the employee AK (JWT)
const OWN_KEYS = new Set(['SARVAM_V4_API_KEY', 'SARVAM_VOICE_AGENTS_API_KEY', 'VOICE_PLUGIN_KEY']);  // never overwritten in bulk

// "3 h 20 min left" / "expired 2 h 5 min ago" from an ISO time — refreshed every 30 s.
function timeLeft(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms)) return { text: '—', cls: '' };
  const m = Math.round(Math.abs(ms) / 60000), h = Math.floor(m / 60), span = h ? `${h} h ${m % 60} min` : `${m} min`;
  return ms < 0 ? { text: `expired ${span} ago`, cls: 'exp-bad' } : { text: `${span} left`, cls: ms < 2 * 3600000 ? 'exp-warn' : 'exp-ok' };
}
const expHtml = iso => { const t = timeLeft(iso); return `<b class="${t.cls}" data-exp="${esc(iso)}">${esc(t.text)}</b>`; };
function paintCountdowns() {
  $$('[data-exp]').forEach(el => { const t = timeLeft(el.dataset.exp); el.textContent = t.text; el.className = t.cls; });
}

function keysBanner(s) {
  const box = $('#admin-keys-banner'); if (!box) return;
  const due = (s.keys?.items || []).filter(k => k.expires_at && (k.expired || new Date(k.expires_at).getTime() - Date.now() < 2 * 3600000));
  box.innerHTML = due.length ? `<div class="note keys-banner" role="status"><b>${due.some(k => k.expired) ? 'API key expired' : 'API key expiring soon'}:</b>
    ${due.map(k => `<span class="mono">${esc(k.name)}</span> ${expHtml(k.expires_at)} <span class="dim">(${esc((k.used_by || []).length)} APIs)</span>`).join(' · ')}
    <button class="btn btn-sm btn-primary" type="button" data-fix-keys>Paste a new key →</button></div>` : '';
}

function render(s) {
  keysBanner(s);
  const body = $('#admin-body');
  if (s.query) return renderSearch(s, body);
  $('#admin-q-count').textContent = '';
  $$('.admin-nav button').forEach(b => { b.setAttribute('aria-current', String(b.dataset.sec === s.section)); b.classList.remove('no-hit'); b.querySelector('.hits')?.remove(); });
  if (s.section === 'apis') return renderApis(s, body);
  if (s.section === 'hits') return renderHits(s, body);
  if (s.section === 'prompts') return renderPrompts(s, body);
  if (s.section === 'voice') return renderVoice(s, body);
  if (s.section === 'sarvamvoice') return renderSarvamVoice(s, body);
  if (s.section === 'engines') return renderEngines(s, body);
  if (s.section === 'personagen') return renderPersonaGen(s, body);
  if (s.section === 'legacy') { body.innerHTML = `<div class="card"><div class="card-body" style="padding-top:14px;display:grid;gap:8px"><p>The V4 console and its configuration are kept intact.</p><p><a class="btn" href="${BASE}/v4/admin">V4 admin</a> <a class="btn" href="${BASE}/studio#admin">Studio admin (revision history)</a> <a class="btn" href="${BASE}/studio#integrations">Sources &amp; jobs</a></p></div></div>`; return; }
  renderSection(s, body, s.section);
}

function groups(items) {
  const out = {};
  items.forEach(a => (out[a.group] ||= []).push(a));
  return out;
}

function gatewayCard(s) {
  const up = s.settings.values.upstream || {};
  const routed = s.apis.items.filter(a => a.via_gateway);
  return `<form class="card gw-card" id="gateway-form"><div class="card-head"><h2>Gateway · IndiaMART internal APIs</h2>${origin(up.gateway_enabled && up.gateway_base_url ? 'live_api' : 'off', up.gateway_enabled ? 'On' : 'Off')}</div>
    <div class="card-body" style="display:grid;gap:10px">
      <p class="muted" style="font-size:12.5px">IndiaMART's internal hosts (company, leads, enquiry, merp, LMS, PNS) don't resolve from this server. A pass-through gateway forwards <code>&lt;base URL&gt; + original path</code> to the original host — ${routed.length} APIs use it. Change the URL here when the tunnel moves; switch it off to call the hosts directly. Allowed suffixes: ${(s.settings.allowed_hosts || []).filter(h => /ngrok/.test(h)).map(h => `<code>${esc(h)}</code>`).join(' ')}.</p>
      <label class="switch"><input type="checkbox" name="upstream.gateway_enabled" ${up.gateway_enabled ? 'checked' : ''}> Send IndiaMART APIs through the gateway</label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="gw-url">Gateway base URL<input id="gw-url" type="url" name="upstream.gateway_base_url" value="${esc(up.gateway_base_url || '')}" placeholder="https://xxxx.ngrok-free.app" spellcheck="false" class="mono"></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="gw-headers">Headers sent to the gateway (JSON)<input id="gw-headers" type="text" name="upstream.gateway_headers" value="${esc(JSON.stringify(up.gateway_headers || {}))}" class="mono"></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="gw-access">GLID access for live IndiaMART APIs (sign-in is always required)<select id="gw-access" name="upstream.glid_access"><option value="dev" ${up.glid_access !== 'prod' ? 'selected' : ''}>dev — approved test GLIDs only</option><option value="prod" ${up.glid_access === 'prod' ? 'selected' : ''}>prod — any GLID a signed-in user enters</option></select></label>
      <p class="form-error" id="gw-error"></p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end"><button class="btn btn-sm" type="button" data-gw-check>Check gateway</button><button class="btn btn-sm btn-primary">Save gateway</button></div>
      <div id="gw-result"></div></div></form>`;
}

function keysCard(s) {
  const rows = (s.keys?.items || []).map(k => `<tr><td class="mono">${esc(k.name)}</td>
    <td>${k.configured ? `${esc(k.source)} · <span class="mono">${esc(k.fingerprint)}</span>` : '<b>not configured</b>'}</td>
    <td>${k.expires_at ? `${expHtml(k.expires_at)}<br><span class="mono dim" style="font-size:11px">${esc(new Date(k.expires_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }))} IST</span>` : '<span class="dim">no expiry claim</span>'}${k.issuer ? `<br><span class="dim" style="font-size:11px">${esc(k.issuer)} · ${esc(k.subject)}</span>` : ''}</td>
    <td style="font-size:11.5px">${esc((k.used_by || []).join(', '))}</td>
    <td><form class="key-form" data-key="${esc(k.name)}" style="display:flex;gap:4px"><label class="sr" for="k-${esc(k.name)}">New value for ${esc(k.name)}</label><input id="k-${esc(k.name)}" type="password" name="value" placeholder="Paste to replace" autocomplete="off" style="min-width:120px"><button class="btn btn-sm">Save</button>${k.source === 'admin' ? `<button class="btn btn-sm btn-ghost" type="button" data-key-reset="${esc(k.name)}" title="Remove the value saved here; the deployment's environment value applies again">Use environment value</button>` : ''}</form></td></tr>`).join('');
  const expired = (s.keys?.items || []).filter(k => k.expired).length;
  return `<details class="card" ${expired ? 'open' : ''}><summary class="card-head"><h2>Keys · ${esc(s.keys?.items?.length || 0)}</h2>${expired ? `<span class="aside chip warn">${expired} expired</span>` : '<span class="aside dim" style="font-size:12px">write-only</span>'}</summary>
    <div class="card-body scroll-x"><p class="muted" style="font-size:12.5px;margin-bottom:8px">Values are never shown — only where they come from, a fingerprint and, for JWT keys, when they expire. The IndiaMART employee key lasts about 24 hours: paste a fresh one here and every API that uses it picks it up on the next request.</p>
    ${bulkKeyForm(s)}
    <table class="table"><thead><tr><th>Key</th><th>Source</th><th>Expiry</th><th>Used by</th><th>Replace</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

// Owner, 2026-10-10: "give an option to copy the same api key to every api" — paste once, save into every ticked slot.
function bulkKeyForm(s) {
  const slots = (s.keys?.items || []).filter(k => !OWN_KEYS.has(k.name));
  if (!slots.length) return '';
  return `<form id="key-bulk" class="key-bulk"><b>One key for every API</b>
    <p class="dim" style="font-size:12px">Paste the key once and it is saved into every ticked slot; each API uses it from its next request. Values stay write-only.</p>
    <div style="display:flex;gap:6px;flex-wrap:wrap"><label class="sr" for="key-bulk-value">Key for every API</label>
      <input id="key-bulk-value" type="password" name="value" placeholder="Paste the key" autocomplete="off" style="flex:1 1 260px;min-width:0">
      <button class="btn btn-primary btn-sm">Save to all ticked</button></div>
    <div class="chips" style="margin-top:6px">${slots.map(k => `<label class="tagchip" style="cursor:pointer"><input type="checkbox" name="names" value="${esc(k.name)}" ${AK_SLOTS.has(k.name) ? 'checked' : ''}> <span class="mono">${esc(k.name)}</span> <span class="dim">· ${esc((k.used_by || []).length)} APIs</span></label>`).join('')}</div>
    <p class="dim" style="font-size:11.5px;margin-top:4px">Ticked: the slots that take the employee key (AK). COMPANY_TOKEN and LEADS_TOKEN are separate credentials — the company API answers “Forbidden” to the AK (live, 10 Oct) — tick them only for their own token.</p>
    <p class="form-error" id="key-bulk-error"></p></form>`;
}

function renderApis(s, body) {
  const g = groups(s.apis.items);
  body.innerHTML = `${gatewayCard(s)}${healthCard(s)}${keysCard(s)}
    <div class="ins-strip" style="margin:14px 0 10px"><span class="ins-label">Revision ${esc(s.apis.revision)} · ${s.apis.items.length} APIs</span>
      <label class="dim" style="font-size:12px;display:flex;gap:6px;align-items:center">Test as <input id="test-seller" type="text" value="${esc(s.testSeller || 'syn-91000101')}" style="width:150px" class="mono" aria-describedby="test-help"></label>
      <span class="dim" id="test-help" style="font-size:12px">syn-… = mock · real-&lt;GLID&gt; = live via the gateway (GLID access: dev = approved test GLIDs, prod = any GLID). Endpoints may only point at allow-listed hosts.</span></div>
    ${Object.entries(g).map(([group, items]) => `<h2 class="group-title" style="margin:14px 0 8px">${esc(group)}</h2><div style="display:grid;gap:8px">${items.map(apiRow).join('')}</div>`).join('')}
    ${s.apis.internal.length ? `<h2 class="group-title" style="margin:18px 0 8px">Sarvam (internal) · last 24 h</h2><p class="muted" style="font-size:12.5px">${s.apis.internal.map(i => `${esc(i.id)}: ${esc(i.hits_24h)}`).join(' · ')}</p>` : ''}`;
}

// Owner, 2026-10-10: "there should be some testing like that domain option, where we should get the health etc for each api
// once new ak key is put" — one button runs every live API once; it also runs by itself after a key is saved.
function healthCard(s) {
  const h = s.health;
  return `<section class="card" id="health-card" aria-labelledby="health-h"><div class="card-head"><h2 id="health-h">API health · every API with the current keys</h2>${h?.items ? `<span class="aside chip ${h.errors ? 'warn' : 'good'}">${esc(h.ok)} OK · ${esc(h.errors)} error${h.errors === 1 ? '' : 's'} · ${esc(h.skipped)} skipped</span>` : ''}</div>
    <div class="card-body" style="display:grid;gap:10px">
      <p class="muted" style="font-size:12.5px">Calls every live IndiaMART API once for one seller, with the keys saved now, and shows what each returned. It runs by itself after you save a key. Use a real GLID (real-…) for live data — any GLID when GLID access is prod, an approved test GLID in dev; syn-… uses mocks.</p>
      <form id="health-form" style="display:flex;flex-wrap:wrap;gap:8px;align-items:end"><label class="dim" style="display:grid;gap:4px;font-size:12px" for="health-seller">Seller<input id="health-seller" name="seller" type="text" class="mono" value="${esc(s.healthSeller || 'real-23456789')}" style="width:170px" spellcheck="false"></label>
        <button class="btn btn-primary btn-sm" ${s.healthBusy ? 'disabled' : ''}>${s.healthBusy ? 'Testing every API…' : 'Test every API'}</button>
        ${h?.at ? `<span class="dim" style="font-size:11.5px">Last run ${esc(new Date(h.at).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', timeStyle: 'short' }))} IST · ${esc(h.seller_id)}</span>` : ''}</form>
      <div id="health-result" aria-live="polite">${s.healthBusy ? '<div class="skeleton" style="height:120px"></div>' : h?.error ? `<p class="note">${esc(h.error)}</p>` : h?.items ? healthTable(h) : ''}</div></div></section>`;
}

const HEALTH_STATUS = { ok: ['live_api', 'OK'], error: ['missing', 'Error'], skipped: ['default', 'Skipped'], off: ['off', 'Off'] };
function healthTable(h) {
  const row = r => {
    const [kind, text] = HEALTH_STATUS[r.status] || ['default', r.status || '—'];
    const key = r.key ? `<span class="mono">${esc(r.key)}</span>${r.key_expired ? ' <b class="exp-bad">expired</b>' : r.key_expires_at ? ` · ${expHtml(r.key_expires_at)}` : ''}` : '<span class="dim">no key</span>';
    return `<tr><td><b>${esc(r.label)}</b><br><span class="mono dim" style="font-size:11px">${esc(r.api)}</span></td><td>${origin(kind, text)}${r.http_status ? ` <span class="mono dim">${esc(r.http_status)}</span>` : ''}</td>
      <td class="mono">${esc(fmtMs(r.ms))}</td><td style="font-size:11.5px">${key}</td><td style="font-size:11.5px">${esc(r.note || '')}</td></tr>`;
  };
  return `<div class="scroll-x"><table class="table health-table"><thead><tr><th>API</th><th>Result</th><th>Time</th><th>Key</th><th>Note</th></tr></thead><tbody>${h.items.map(row).join('')}</tbody></table></div>`;
}

async function runHealth(s) {
  s.healthBusy = true; s.health = null;
  const card = $('#health-card'); if (card) card.outerHTML = healthCard(s);
  try { s.health = await api('/api/v5/admin/apis/test-all', { method: 'POST', body: { seller_id: s.healthSeller || 'real-23456789' } }); }
  catch (err) { s.health = { error: err.message }; }
  s.healthBusy = false;
  const now = $('#health-card'); if (now) now.outerHTML = healthCard(s);
  if (s.health?.items) toast(`API health: ${s.health.ok} OK, ${s.health.errors} error(s), ${s.health.skipped} skipped`, s.health.errors ? 'error' : 'ok');
}

// Owner, 2026-10-10: "the prompts (all, described also where they are used) should be visible in admin in a prompt
// section. Should be also editable from there."
function renderPrompts(s, body) {
  const list = s.prompts?.items || [];
  const groupsOf = {};
  list.forEach(p => (groupsOf[p.group] ||= []).push(p));
  const edited = list.filter(p => p.edited).length;
  body.innerHTML = `<div class="card"><div class="card-head"><h2>Prompts · ${esc(list.length)}</h2>${edited ? `<span class="aside chip warn">${edited} edited</span>` : '<span class="aside dim" style="font-size:12px">all defaults</span>'}</div>
    <div class="card-body"><p class="muted" style="font-size:12.5px">Every prompt SellerGenome sends to a model, where it is used and when an edit takes effect. Edits are checked before saving (placeholders, Sarvam's rules, length limits); <i>Reset</i> brings back the version in code. Sarvam agent prompts reach Sarvam on the next <i>Build</i>.</p></div></div>
    ${Object.entries(groupsOf).map(([group, items]) => `<h2 class="group-title" style="margin:14px 0 8px">${esc(group)}</h2><div style="display:grid;gap:8px">${items.map(promptCard).join('')}</div>`).join('')}`;
}

function promptCard(p) {
  const id = `pr-${p.id.replace(/\W/g, '-')}`;
  const holders = (p.names || []).map(n => `<code>{${esc(n)}}</code>`).join(' ') || (p.token ? `<code>${esc(p.token)}</code>` : '');
  return `<details class="card prompt-card" data-prompt-card="${esc(p.id)}"><summary class="card-head"><h3 style="font-size:14px">${esc(p.title)}</h3>
      <span class="aside" style="display:flex;gap:6px;align-items:center">${p.edited ? '<span class="chip warn">edited</span>' : ''}<span class="dim mono" style="font-size:11px">${esc(p.chars.toLocaleString('en-IN'))} chars</span></span></summary>
    <form class="card-body prompt-form" data-prompt="${esc(p.id)}" style="display:grid;gap:8px">
      <dl class="prompt-meta"><dt>Used</dt><dd>${esc(p.used)}</dd><dt>Model call</dt><dd class="mono">${esc(p.label)}</dd><dt>Source</dt><dd class="mono">${esc(p.where)}</dd>
        <dt>Takes effect</dt><dd>${esc(p.applies)}</dd>${holders ? `<dt>Placeholders</dt><dd>${holders}</dd>` : ''}${p.limit ? `<dt>Sent</dt><dd>The first ${esc(p.limit.toLocaleString('en-IN'))} characters.</dd>` : ''}
        ${p.edited ? `<dt>Edited</dt><dd>${esc(new Date(p.edited_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }))} IST by ${esc(p.edited_by || 'admin')}</dd>` : ''}</dl>
      <label class="sr" for="${id}">Prompt text: ${esc(p.title)}</label>
      <textarea id="${id}" name="text" class="mono prompt-text" rows="${Math.min(22, Math.max(4, Math.ceil(p.chars / 110)))}" spellcheck="false">${esc(p.text)}</textarea>
      <p class="form-error" data-prompt-err></p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end">
        ${p.edited ? `<button class="btn btn-sm btn-ghost" type="button" data-prompt-reset="${esc(p.id)}">Reset to default</button>` : ''}
        ${p.build ? `<button class="btn btn-sm" type="button" data-prompt-build="${esc(p.build)}">Build on Sarvam</button>` : ''}
        <button class="btn btn-primary btn-sm">Save prompt</button></div>
      ${p.history?.length ? `<p class="dim" style="font-size:11.5px">Earlier edits: ${p.history.map(h => `${esc(h.at ? new Date(h.at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'short', timeStyle: 'short' }) : '—')} (${esc(h.chars)} chars)`).join(' · ')}</p>` : ''}
    </form></details>`;
}

function apiRow(a) {
  const last = a.last_hit;
  const json = v => esc(JSON.stringify(v || {}, null, 2));
  return `<details class="api-row" data-api="${esc(a.id)}"><summary>
    <span><b>${esc(a.label)}</b><br><span class="mono dim" style="font-size:11px">${esc(a.id)} · ${esc({ sample: 'shape from a real sample', guess: 'documented guess', fixed: 'fixed by policy' }[a.contract] || a.contract)}</span></span>
    <span>${origin(a.mode === 'live' ? 'live_api' : a.mode === 'mock' ? 'mock' : 'off', a.mode)}</span>
    <span class="url" title="${esc(a.url)}">${esc(a.url || '(endpoint not supplied)')}</span>
    <span class="hits mono dim" style="font-size:11.5px;text-align:right">${esc(a.hits_24h)} hits/24h${last ? `<br>${esc(last.status)} · ${esc(fmtMs(last.ms))}` : ''}</span></summary>
    <form class="api-form" data-api="${esc(a.id)}">
      <p class="muted" style="font-size:12.5px">${esc(a.notes || '')} Reachable from this server: <b>${esc(a.reachable)}</b>.${a.overridden.length ? ` Overridden: ${esc(a.overridden.join(', '))}.` : ''}</p>
      <div class="seg" role="radiogroup" aria-label="Mode">${['live', 'mock', 'off'].map(m => `<label style="padding:4px 10px;cursor:pointer"><input type="radio" name="mode" value="${m}" ${a.mode === m ? 'checked' : ''}> ${titleCase(m)}</label>`).join('')}</div>
      <div class="two">
        <label>Method<select name="method" ${a.editable ? '' : 'disabled'}><option ${a.method === 'GET' ? 'selected' : ''}>GET</option><option ${a.method === 'POST' ? 'selected' : ''}>POST</option></select></label>
        <label>Timeout (s)<input type="number" name="timeout_seconds" step="0.5" min="0.5" max="30" value="${esc(a.timeout_seconds)}"></label>
      </div>
      <label style="display:grid;gap:4px;font-size:12px" class="dim"><span>Endpoint template — placeholders <code>{glid}</code> <code>{alias}</code> <code>{offerid}</code> <code>{contact_glid}</code> <code>{empid}</code> <code>{secret}</code> <code>{secret:NAME}</code> <code>{x|int}</code></span><input type="text" name="url" value="${esc(a.url)}" ${a.editable ? '' : 'disabled'} class="mono"></label>
      ${a.kind === 'http' ? `<div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center"><label class="switch"><input type="checkbox" name="via_gateway" ${a.via_gateway ? 'checked' : ''} ${a.editable ? '' : 'disabled'}> Send through the gateway${a.sent_via ? ` <span class="mono dim" style="font-size:11px">→ ${esc(a.sent_via)}</span>` : ''}</label>
        <label class="dim" style="font-size:12px">Body format <select name="body_format" ${a.editable ? '' : 'disabled'} style="width:auto"><option value="json" ${a.body_format !== 'form' ? 'selected' : ''}>JSON</option><option value="form" ${a.body_format === 'form' ? 'selected' : ''}>Form (urlencoded)</option></select></label></div>` : ''}
      <div class="two">
        <label>Query params (JSON)<textarea name="params" rows="4" ${a.editable ? '' : 'disabled'}>${json(a.params)}</textarea></label>
        <label>Body (JSON)<textarea name="body" rows="4" ${a.editable ? '' : 'disabled'}>${json(a.body)}</textarea></label>
      </div>
      <div class="two">
        <label>Headers (JSON)<textarea name="headers" rows="2" ${a.editable ? '' : 'disabled'}>${json(a.headers)}</textarea></label>
        <label>Cache TTL (hours)<input type="number" name="ttl_hours" min="0" max="2160" value="${esc(a.ttl_hours)}"></label>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:16px"><label class="switch"><input type="checkbox" name="cache_read" ${a.cache_read ? 'checked' : ''}> Read Elastic cache first</label><label class="switch"><input type="checkbox" name="persist" ${a.persist ? 'checked' : ''}> Store responses in Elastic</label></div>
      ${a.secret ? `<div class="card" style="background:var(--panel-2)"><div class="card-body" style="padding-top:10px;display:grid;gap:6px">
        <span style="font-size:12.5px">Key <b class="mono">${esc(a.secret.name)}</b>: ${a.secret.configured ? `configured from ${esc(a.secret.source)} · fingerprint <span class="mono">${esc(a.secret.fingerprint)}</span>${a.secret.expires_at ? (a.secret.expired ? ' · <b style="color:var(--err)">expired</b>' : ` · expires in ${esc(a.secret.expires_in_hours)} h`) : ''}` : '<b>not configured</b>'}${(a.secrets || []).length ? ` · also uses ${a.secrets.map(x => `<span class="mono">${esc(x.name)}</span>${x.configured ? '' : ' (missing)'}`).join(', ')}` : ''}</span>
        <div style="display:flex;gap:6px"><label class="sr" for="key-${esc(a.id)}">New key</label><input id="key-${esc(a.id)}" type="password" name="secret" placeholder="Paste a new key (write-only)" autocomplete="off"><button class="btn btn-sm" type="button" data-save-key="${esc(a.id)}">Save key</button></div></div></div>` : ''}
      <p class="form-error" data-err></p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end">
        <button class="btn btn-sm btn-ghost" type="button" data-reset="${esc(a.id)}">Reset to default</button>
        <button class="btn btn-sm" type="button" data-hits="${esc(a.id)}">Recent hits</button>
        <button class="btn btn-sm" type="button" data-test="${esc(a.id)}">Test</button>
        <button class="btn btn-sm btn-primary">Save changes</button></div>
      <div data-hitlist></div>
    </form></details>`;
}

async function renderHits(s, body) {
  body.innerHTML = '<div class="skeleton" style="height:200px"></div>';
  const out = await api('/api/v5/admin/hits?count=200');
  body.innerHTML = `<div class="card"><div class="card-head"><h2>Every API hit · newest first</h2><span class="aside dim" style="font-size:12px">${out.items.length} shown</span></div><div class="card-body scroll-x"><table class="table"><thead><tr><th>When</th><th>API</th><th>Seller</th><th>Origin</th><th>Status</th><th>Time</th><th>Note</th></tr></thead><tbody>${out.items.map(h => `
    <tr><td class="mono" style="font-size:11px">${esc(new Date(h.at_epoch * 1000).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' }))}</td><td class="mono">${esc(h.api)}</td><td class="mono" style="font-size:11px">${esc(h.glid || h.scope || '')}</td><td>${origin(h.origin || 'default')}</td><td>${esc(h.status)}</td><td class="mono">${esc(fmtMs(h.ms))}</td><td style="font-size:12px">${esc(h.note || '')}</td></tr>`).join('')}</tbody></table></div></div>`;
}

// ---- Persona generator (5.5): the owner's prompt, the scenario suite and its results.
async function renderPersonaGen(s, body) {
  body.innerHTML = '<div class="skeleton" style="height:240px"></div>';
  const g = await api('/api/v5/admin/persona-generator');
  s.pg = g;
  body.innerHTML = `<form class="card" data-settings><div class="card-head"><h2>Persona generator</h2><span class="aside mono dim" style="font-size:11.5px">${esc(g.prompt_file)}</span></div>
    <div class="card-body" style="display:grid;gap:12px">
      <p class="muted" style="font-size:12.5px">Every barge-in builds the persona with the owner's <b>Persona Generator prompt v0.3</b> from the APIs (only the keys the prompt and the Consolidated API Guide allow, scrubbed), then checks it automatically — evidence ids, invented numbers, the never-mention list, the call-plan trigger, word limits, dial bounds — and repairs once when a critical rule is broken. The earlier Sarvam aggregation stays available.</p>
      <label class="dim" style="display:grid;gap:4px;font-size:12px;max-width:420px" for="pg-engine">Persona engine<select id="pg-engine" name="persona.engine" data-type="string">
        <option value="generator" ${g.engine === 'generator' ? 'selected' : ''}>Persona Generator v0.3 (owner's prompt + checks)</option>
        <option value="aggregator" ${g.engine === 'aggregator' ? 'selected' : ''}>Sarvam aggregation (5.x)</option></select></label>
      <p class="form-error"></p><div style="display:flex;justify-content:flex-end"><button class="btn btn-sm btn-primary">Save</button></div>
      <details><summary class="dim" style="font-size:12.5px">What SellerGenome changed in the prompt</summary><pre class="code wrap" style="max-height:260px">${esc(g.changes)}</pre></details>
      <details><summary class="dim" style="font-size:12.5px">The prompt (${esc(g.prompt.length.toLocaleString())} chars; system part ${esc(g.system_chars.toLocaleString())} of the gateway's ${esc(g.system_limit.toLocaleString())})</summary><pre class="code wrap" style="max-height:420px">${esc(g.prompt)}</pre></details>
    </div></form>
    <div class="card"><div class="card-head"><h2>Scenario suite · how good is it</h2><span class="aside"><button class="btn btn-sm btn-primary" type="button" data-pg-run>Run the suite on Sarvam</button></span></div>
      <div class="card-body" style="display:grid;gap:10px">
        <p class="dim" style="font-size:12.5px">14 synthetic sellers (every archetype) and 11 edge cases from the prompt's rules: new free seller, follow-up due, unknown hot-lead type, contact name = company, First Copy enquiries, counts that disagree, large business, unanswered calls, Tamil seller, meeting with pending work, renewal activity. About 25 Sarvam calls, paced for the gateway (≈4 minutes).</p>
        <div id="pg-out">${evalHtml(g.last_eval)}</div></div></div>`;
  if (g.last_eval?.status === 'running') pollEval(s, g.last_eval.id);
}

function evalHtml(e) {
  if (!e) return '<p class="dim">No run yet.</p>';
  const sm = e.summary || {};
  const rows = (e.rows || []).map((r, i) => `<tr><td style="font-size:12px">${esc(r.name)}</td>
    <td class="mono">${r.error ? `<b style="color:var(--err)">error</b>` : `<span class="disp ${r.critical ? 'neg' : r.score >= 85 ? 'pos' : 'neu'}">${esc(r.score)}</span>${r.raw_score != null && r.raw_score !== r.score ? ` <span class="dim" title="The model's own reply, before the code fixes">(${esc(r.raw_score)})</span>` : ''}`}</td>
    <td class="mono">${r.error ? '' : `${esc(r.critical)} / ${esc(r.warnings)}`}</td>
    <td style="font-size:11.5px">${esc((r.expected || [])[0] || 'none')} → ${esc(r.plan || 'none')}</td>
    <td class="mono">${r.scenario_checks?.length ? `${esc(r.scenario_ok)}/${esc(r.scenario_checks.length)}` : '—'}</td>
    <td class="mono">${esc(Math.round((r.ms || 0) / 100) / 10)} s${r.attempts > 1 ? ' · repaired' : ''}</td>
    <td>${r.error ? esc(r.error) : `<button class="btn btn-sm btn-ghost" type="button" data-pg-case="${i}" data-pg-eval="${esc(e.id)}">Open</button>`}</td></tr>`).join('');
  return `<p style="font-size:12.5px"><b>${esc(e.status === 'running' ? `Running… ${(e.rows || []).length}/${e.total}` : 'Last run')}</b> · ${esc(e.started_at || '')}
      ${sm.mean_score != null ? ` · mean quality <b>${esc(sm.mean_score)}</b>/100 delivered${sm.mean_raw_score != null ? ` (model's own reply ${esc(sm.mean_raw_score)})` : ''} · ${esc(sm.no_critical)}/${esc(sm.cases)} with no critical issue · call plan right ${esc(sm.plan_matches)}/${esc(sm.cases)} · scenario checks ${esc(sm.scenario_checks)} · median ${esc(Math.round((sm.median_ms || 0) / 100) / 10)} s` : ''}</p>
    ${sm.top_failures?.length ? `<p class="dim" style="font-size:11.5px">Most frequent issues: ${sm.top_failures.map(([c, n]) => `${esc(c)} (${n})`).join(' · ')}</p>` : ''}
    <div class="scroll-x"><table class="table"><thead><tr><th>Case</th><th>Quality (model)</th><th>Critical / warnings</th><th>Expected → plan</th><th>Scenario</th><th>Time</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function pollEval(s, id) {
  clearTimeout(s.pgTimer);
  try {
    const e = await api(`/api/v5/admin/persona-generator/eval/${id}`);
    const out = $('#pg-out');
    if (!out) return;
    out.innerHTML = evalHtml(e);
    if (e.status === 'running') s.pgTimer = setTimeout(() => pollEval(s, id), 4000);
  } catch { /* the next render retries */ }
}

// ---- Voice & engines (5.3, decision D25): defaults for every place that speaks, listens or hands a call to a bot.
const ENGINE_FIELDS = [['bot', 'Bot on calls'], ['tts_model', 'Voice model (Bulbul)'], ['tts_voice', 'Voice'], ['tts_transport', 'Speech delivery'],
  ['listen', 'Listening'], ['stt_realtime_model', 'Realtime listening model'], ['stt_model', 'Clip listening model'], ['stt_mode', 'Transcript style'],
  ['translate_model', 'Translation model']];

async function renderEngines(s, body) {
  body.innerHTML = '<div class="skeleton" style="height:240px"></div>';
  const cat = await api('/api/v5/engines');
  const v = s.settings.values.engines || {}, rt = v.realtime || {}, llm = s.settings.values.llm || {};
  const opts = (kind, current) => {
    const list = kind === 'tts_voice' ? [{ id: 'auto', label: 'Persona voice (auto)' }, ...(cat.voices[v.tts_model] || []).map(x => ({ id: x, label: x }))] : cat.options[kind] || [];
    return list.map(o => `<option value="${esc(o.id)}" ${o.id === current ? 'selected' : ''}>${esc(o.label)}</option>`).join('');
  };
  const sel = (path, kind, current, label, note = '') => `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-${esc(path)}">${esc(label)}<select id="e-${esc(path)}" name="${esc(path)}" data-type="string">${opts(kind, current)}</select>${note ? `<small>${esc(note)}</small>` : ''}</label>`;
  const num = (path, value, label) => `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-${esc(path)}">${esc(label)}<input id="e-${esc(path)}" type="number" step="any" name="${esc(path)}" data-type="number" value="${esc(value)}"></label>`;
  const noteOf = (kind, id) => (cat.options[kind] || []).find(o => o.id === id)?.note || '';
  body.innerHTML = `<form class="card" data-settings><div class="card-head"><h2>Voice &amp; engines</h2><span class="aside mono dim" style="font-size:11.5px">revision ${esc(s.settings.revision)}</span></div>
    <div class="card-body" style="display:grid;gap:12px">
      <p class="muted" style="font-size:12.5px">Defaults for every place SellerGenome speaks, listens or hands a call to a bot. Each place also has its own <b>⚙ Engines</b> picker (call workspace, buyer desk, Sarvam page) that overrides these for one browser. Earlier choices stay available — Bulbul v3, REST speech and clip listening were kept next to the new ones. Model names are checked against Sarvam's allow-list on the server.</p>
      <div class="two">${ENGINE_FIELDS.map(([k, l]) => sel(`engines.${k}`, k, v[k], l, noteOf(k, v[k]))).join('')}
        ${sel('llm.model', 'llm_model', llm.model, 'Persona & bot LLM (Sarvam)', noteOf('llm_model', llm.model))}
        ${sel('engines.transliterate', 'transliterate', v.transliterate, 'Romanised Hindi → Devanagari before speaking', noteOf('transliterate', v.transliterate))}
        <label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-llm-re">Reasoning effort<select id="e-llm-re" name="llm.reasoning_effort" data-type="string">${cat.options.reasoning_effort.map(x => `<option ${x === llm.reasoning_effort ? 'selected' : ''}>${esc(x)}</option>`).join('')}</select><small>low is fastest for live turns</small></label></div>
      <p class="kicker">Realtime listening (Sarvam streaming speech-to-text)</p>
      <div class="two">
        <label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-rt-st">Partial transcripts<select id="e-rt-st" name="engines.realtime.stream_type" data-type="string">${['fast', 'balanced'].map(x => `<option ${x === rt.stream_type ? 'selected' : ''}>${x}</option>`).join('')}</select><small>fast = words appear sooner; balanced = steadier partials</small></label>
        ${num('engines.realtime.silence_duration_ms', rt.silence_duration_ms, 'Pause that ends a turn (ms)')}${num('engines.realtime.threshold', rt.threshold, 'Speech-detection threshold (0–1)')}
        ${num('engines.realtime.min_speech_duration_ms', rt.min_speech_duration_ms, 'Shortest speech counted (ms)')}${num('engines.realtime.prefix_padding_ms', rt.prefix_padding_ms, 'Audio kept before speech (ms)')}
        ${num('engines.realtime.max_seconds', rt.max_seconds, 'Listening session cap (s)')}${num('engines.realtime.sessions_per_visitor_per_minute', rt.sessions_per_visitor_per_minute, 'Sessions per visitor per minute')}
        <label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-kt">Words to expect on seller calls (keyterms, JSON list · Saaras v4)<textarea id="e-kt" rows="2" name="engines.keyterms" data-type="json">${esc(JSON.stringify(v.keyterms || []))}</textarea></label>
        <label class="dim" style="display:grid;gap:4px;font-size:12px" for="e-bkt">Words to expect on the buyer desk (JSON list)<textarea id="e-bkt" rows="2" name="engines.buyer_keyterms" data-type="json">${esc(JSON.stringify(v.buyer_keyterms || []))}</textarea><small>empty by default — seller words can bias a clipped buyer phrase into a keyterm</small></label></div>
      <p class="form-error"></p>
      <div style="display:flex;gap:8px;justify-content:flex-end">${s.settings.history.length ? `<button class="btn btn-sm btn-ghost" type="button" data-rollback="${esc(s.settings.revision - 1)}">Roll back last change</button>` : ''}<button class="btn btn-primary btn-sm">Save defaults</button></div>
    </div></form>
    <div class="card"><div class="card-head"><h2>Where each engine is used</h2></div><div class="card-body"><div class="scroll-x"><table class="table"><thead><tr><th>Place</th><th>Choices it offers</th></tr></thead><tbody>
      ${Object.values(cat.locations).map(l => `<tr><td>${esc(l.label)}</td><td style="font-size:12px">${l.uses.map(k => esc((ENGINE_FIELDS.find(f => f[0] === k) || [k, k])[1])).join(' · ')}</td></tr>`).join('')}
      <tr><td>Payal on Sarvam (live voice)</td><td style="font-size:12px">Sarvam runs listening and speech for its own agent — tune it in Admin → Sarvam voice agent</td></tr></tbody></table></div></div></div>
    <div class="card"><div class="card-head"><h2>Compare voices · listen and choose</h2></div><div class="card-body" style="display:grid;gap:10px">
      <p class="dim" style="font-size:12.5px">The same line in four engines, or in every Hinglish voice of Bulbul v4: time to the first sound, speaking pace and a player. "Use as default" sets the voice every call uses (persona voices are then overridden). One operator-budget call per row.</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><label class="sr" for="cmp-text">Line</label><input id="cmp-text" style="flex:1;min-width:220px" value="नमस्ते जी, मैं IndiaMART से पायल बोल रही हूँ। क्या अभी दो मिनट बात हो सकती है?"><label class="sr" for="cmp-set">Compare</label><select id="cmp-set" style="width:auto"><option value="models">Models &amp; delivery (4)</option><option value="female">Female voices (Bulbul v4)</option><option value="male">Male voices (Bulbul v4)</option></select><button class="btn btn-sm btn-primary" type="button" data-eng-measure>Measure</button></div>
      <div id="cmp-out"></div></div></div>`;
}

// Listening board: the same line in several engines or voices — first-sound time, speaking rate, a player and "use as
// default". Voices that measure well can still sound wrong; the ear decides (5.4).
const VOICE_SETS = {
  models: null,
  female: ['ritu_hi_customer', 'ritu_hi_conversation', 'ritu_hi_customer_warm', 'ishita_enhi_customer', 'ishita_enhi_customer_expressive',
    'simran_enhi_customer', 'shalini_enhi_customer', 'kavya_hi_conversational', 'aarti_hi_customer', 'sanchita_hi_assistant', 'roopa_hi_conversational'],
  male: ['shubh_hi_customer', 'shubh_enhi_banking', 'sunny_enhi_customer', 'rahul_hi_conversational', 'aditya_hi_sales', 'tarun_hi_conversational', 'mohit_hi_conversational'],
};

async function streamOnce(body, t0) {
  const r = await api('/api/v5/speak/stream', { method: 'POST', body });
  const res = await fetch(BASE + r.url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const chunks = []; let first = null, part;
  while (!(part = await reader.read()).done) { if (first === null) first = Math.round(performance.now() - t0); chunks.push(part.value); }
  return { speaker: r.speaker, first, total: Math.round(performance.now() - t0), src: URL.createObjectURL(new Blob(chunks, { type: 'audio/mpeg' })) };
}

async function measureVoices(button, s) {
  const text = $('#cmp-text').value.trim();
  const set = $('#cmp-set')?.value || 'models';
  const out = $('#cmp-out');
  if (!text) return;
  button.disabled = true; out.innerHTML = '<div class="skeleton" style="height:90px"></div>';
  const rows = [];
  const jobs = VOICE_SETS[set] ? VOICE_SETS[set].map(v => ['bulbul:v4-flash', 'stream', v])
    : [['bulbul:v4-flash', 'stream', 'ritu'], ['bulbul:v4-flash', 'rest', 'ritu'], ['bulbul:v3', 'stream', 'ritu'], ['bulbul:v3', 'rest', 'ritu']];
  for (const [model, transport, voice] of jobs) {
    const body = { text, language: 'hi-IN', speaker: voice, pace: 1, transliterate: true, model };
    const t0 = performance.now();
    try {
      if (transport === 'rest') {
        const r = await api('/api/v5/speak', { method: 'POST', body });
        const ms = Math.round(performance.now() - t0);
        rows.push({ model, transport, speaker: r.speaker, first: ms, total: ms, src: `data:${r.content_type};base64,${r.audio_base64}` });
      } else rows.push({ model, transport, ...(await streamOnce(body, t0)) });
    } catch (err) { rows.push({ model, transport, speaker: voice, error: err.message }); }
  }
  out.innerHTML = `<div class="scroll-x"><table class="table"><thead><tr><th>Model</th><th>Delivery</th><th>Voice</th><th>First sound</th><th>Complete</th><th>Pace</th><th>Listen</th>${VOICE_SETS[set] ? '<th></th>' : ''}</tr></thead><tbody>${rows.map((r, i) => `<tr><td class="mono">${esc(r.model)}</td><td>${r.transport === 'stream' ? 'streamed' : 'REST'}</td><td class="mono" style="font-size:11px">${esc(r.speaker || '—')}</td>
    <td class="mono">${r.error ? `<b style="color:var(--err)">${esc(r.error)}</b>` : `${esc(r.first)} ms`}</td><td class="mono">${r.error ? '' : `${esc(r.total)} ms`}</td><td class="mono" data-rate="${i}">—</td>
    <td>${r.src ? `<audio controls preload="metadata" src="${esc(r.src)}" data-row="${i}" style="height:30px;max-width:180px"></audio>` : ''}</td>
    ${VOICE_SETS[set] && !r.error ? `<td><button class="btn btn-sm" type="button" data-use-voice="${esc(r.speaker)}">Use as default</button></td>` : VOICE_SETS[set] ? '<td></td>' : ''}</tr>`).join('')}</tbody></table></div>
    <p class="dim" style="font-size:11.5px">Measured from this browser through the server, including the network both ways. Pace = characters per second of speech (about 15–17 sounds natural on a call).</p>`;
  $$('#cmp-out audio[data-row]').forEach(a => a.addEventListener('loadedmetadata', () => {
    const cell = $(`#cmp-out [data-rate="${a.dataset.row}"]`);
    if (cell && Number.isFinite(a.duration) && a.duration > 0) cell.textContent = `${(text.length / a.duration).toFixed(1)} c/s`;
  }));
  button.disabled = false;
}

async function paintBuyerAgent() {
  const box = $('#sv-buyer-status'); if (!box) return;
  try {
    const b = await api('/api/v5/sarvam-buyer');
    box.innerHTML = `<dl class="kv"><dt>Agent</dt><dd>${b.agent?.id ? `<span class="mono">${esc(b.agent.id)}</span> · version ${esc(b.version || '—')}` : 'not built'}</dd>
      <dt>Built</dt><dd>${esc(b.built_at || '—')}</dd><dt>Voice</dt><dd>${esc(b.voice || '—')}</dd><dt>Languages</dt><dd>${esc((b.languages || []).join(', ') || '—')}</dd>
      <dt>Tools</dt><dd>${(b.tools || []).map(t => `${esc(t.name)} <span class="dim">(${esc(t.lifecycle)}, ${esc(t.status)})</span>`).join(' · ') || '—'}</dd>
      <dt>Live calls</dt><dd>${b.live?.available ? 'available' : esc(b.live?.reason || '—')}</dd></dl>
      ${b.links?.agent ? `<p><a href="${esc(b.links.agent)}" target="_blank" rel="noopener">Open VANI in Sarvam ↗</a>${b.links.suite ? ` · <a href="${esc(b.links.suite)}" target="_blank" rel="noopener">eval suite ↗</a>` : ''}</p>` : ''}
      <details><summary>Prompt (${esc(b.prompt_chars)} characters) and what changed from the owner's text</summary><ul style="margin:8px 0;padding-left:18px">${(b.changes || []).map(c => `<li>${esc(c)}</li>`).join('')}</ul><pre class="code wrap">${esc(b.prompt)}</pre><p class="dim">Intro: ${esc(b.intro)}</p></details>`;
  } catch (err) { box.textContent = err.message; }
}

async function renderSarvamVoice(s, body) {
  body.innerHTML = '<div class="skeleton" style="height:200px"></div>';
  const v = await api('/api/v5/sarvam-voice');
  s.voiceOptions = v.voice_options || [];
  const a = v.auth || {};
  body.innerHTML = `<div class="card"><div class="card-head"><h2>Payal on Sarvam Voice Agents</h2>${origin(a.connected ? 'live_api' : 'off', a.connected ? 'MCP connected' : 'Not connected')}</div>
    <div class="card-body" style="display:grid;gap:12px;font-size:13px">
      <p class="muted">SellerGenome builds Payal on Sarvam's Voice Agents platform through the Sarvam Voice MCP (https://mcp.sarvam.ai/voice-agents): two knowledge bases, the agent (variables, languages, voice, prompt, tools, voicemail, nudges), a committed version and an eval suite. Only synthetic demo sellers are sent to Sarvam; nothing dials real numbers.</p>
      <dl class="kv"><dt>Sign-in</dt><dd>${a.connected ? `connected · token valid ${esc(a.expires_in_hours)} h (refreshes itself${a.can_refresh ? '' : ' — no refresh token, reconnect daily'})` : 'not connected'}</dd>
        <dt>Agent</dt><dd>${v.agent?.id ? `<span class="mono">${esc(v.agent.id)}</span> · version ${esc(v.version || '—')}` : 'not built'}</dd>
        <dt>Built</dt><dd>${esc(v.built_at || '—')}</dd><dt>Voice</dt><dd>${esc(v.voice || '—')}</dd></dl>
      <div class="card" style="background:var(--panel-2)"><div class="card-body" style="padding-top:10px;display:grid;gap:8px">
        <b>1 · Connect (once a day if the refresh fails)</b>
        <p class="dim" style="font-size:12px">Sarvam's sign-in only returns to 127.0.0.1. Open the link, sign in with the hackathon org, then copy the address the browser lands on (it may not load) and paste it here.</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" type="button" data-sv="connect">Get sign-in link</button><span id="sv-link" style="font-size:12px;overflow-wrap:anywhere"></span></div>
        <form id="sv-complete" style="display:flex;gap:6px"><label class="sr" for="sv-cb">Callback address</label><input id="sv-cb" name="callback_url" placeholder="http://127.0.0.1:53682/callback?code=…" class="mono"><button class="btn btn-sm">Connect</button></form></div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-primary btn-sm" type="button" data-sv="build" ${a.connected ? '' : 'disabled'}>2 · Build / update Payal on Sarvam</button>
        <button class="btn btn-sm" type="button" data-sv="eval" ${v.suite ? '' : 'disabled'}>3 · Run the eval suite</button><a class="btn btn-sm btn-ghost" href="${BASE}/sarvam">Talk to Payal →</a></div>
      <div class="card" style="background:var(--panel-2)"><div class="card-body" style="padding-top:10px;display:grid;gap:8px">
        <b>Browser voice calls to Payal (optional)</b>
        <p class="dim" style="font-size:12px">Needs the Voice Agents API key (Admin → APIs → Keys → SARVAM_VOICE_AGENTS_API_KEY) and the workspace id from the Sarvam dashboard URL. Save the workspace id here, then test the key.</p>
        <form id="sv-ws" style="display:flex;gap:6px"><label class="sr" for="sv-wsid">Workspace id</label><input id="sv-wsid" name="workspace_id" class="mono" placeholder="workspace id" value="${esc(s.settings.values.sarvam_voice?.workspace_id || '')}"><button class="btn btn-sm">Save</button><button class="btn btn-sm" type="button" data-sv="testkey">Test the Voice API key</button></form>
        <p id="sv-keyout" class="dim" style="font-size:12px"></p></div></div>
      ${payalTuning(s)}
      <div class="card" style="background:var(--panel-2)" id="sv-buyer"><div class="card-body" style="padding-top:10px;display:grid;gap:8px">
        <b>VANI buyer agent on Sarvam (5.7)</b>
        <p class="dim" style="font-size:12px">A second, separate agent: the buyer desk's requirement flow (product → number of pieces → specifications → sellers' link on WhatsApp) from the owner's VANI prompt, restructured to Sarvam's playbook. Same sign-in, workspace and keys as Payal; synthetic buyer-desk calls only.</p>
        <div id="sv-buyer-status" class="dim" style="font-size:12.5px">Loading…</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-primary btn-sm" type="button" data-sv="buildbuyer" ${a.connected ? '' : 'disabled'}>Build / update VANI on Sarvam</button>
          <a class="btn btn-sm btn-ghost" href="${BASE}/buyer">Try it on the buyer desk →</a></div>
        <div id="sv-buyer-log"></div></div></div>
      <div class="card" style="background:var(--panel-2)"><div class="card-body" style="padding-top:10px;display:grid;gap:8px">
        <b>Smoke call · Python SDK (headless)</b>
        <p class="dim" style="font-size:12px">Opens a real voice call to Payal from this server with Sarvam's Python conversational SDK, speaks two seller lines (Bulbul v4) into it and times how fast Payal answers. Synthetic seller; costs one short call.</p>
        <div><button class="btn btn-sm" type="button" data-sv="smoke" ${v.agent?.id ? '' : 'disabled'}>Run a smoke call</button></div>
        <div id="sv-smoke"></div></div></div>
      <div id="sv-log">${(v.build_log || []).length ? buildLog(v.build_log) : ''}</div></div></div>`;
  paintBuyerAgent();
}

function payalTuning(s) {
  const v = s.settings.values.sarvam_voice || {};
  return `<form class="card" data-settings style="background:var(--panel-2)"><div class="card-body" style="padding-top:10px;display:grid;gap:8px">
    <b>Payal’s listening and live calls</b>
    <p class="dim" style="font-size:12px">Voice detection, the pause that ends the seller’s turn, interruptions and the words Sarvam should expect are applied to Payal on the next <b>Build / update</b>; hotwords are also sent at the start of every live call. Live browser calls can be switched off here.</p>
    <div class="two">
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-vad">Voice detection<select id="sv-vad" name="sarvam_voice.vad" data-type="string">${['hybrid-vad', 'basic-vad'].map(x => `<option ${x === v.vad ? 'selected' : ''}>${x}</option>`).join('')}</select><small>basic-vad was the platform default</small></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-sf">Silence frames that end a turn<input id="sv-sf" type="number" name="sarvam_voice.silence_frames" data-type="number" value="${esc(v.silence_frames)}"><small>platform default 6; measured: 6 → 1.42 s, 4 → 1.36 s, 3 → 1.33 s median answer, no talk-over</small></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-pace">Payal's pace<input id="sv-pace" type="number" step="0.05" name="sarvam_voice.pace" data-type="number" value="${esc(v.pace)}"><small>1.0 = the voice's natural pace</small></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-voice">Payal's voice (Sarvam catalogue)<input id="sv-voice" list="sv-voices" name="sarvam_voice.voice" data-type="string" value="${esc(v.voice || '')}" placeholder="auto — best Hinglish support voice"><datalist id="sv-voices">${(s.voiceOptions || []).map(x => `<option value="${esc(x)}">`).join('')}</datalist></label>
      <label class="switch"><input type="checkbox" name="sarvam_voice.allow_interrupt" data-type="bool" ${v.allow_interrupt ? 'checked' : ''}> Seller can interrupt Payal</label>
      <label class="switch"><input type="checkbox" name="sarvam_voice.live_calls_enabled" data-type="bool" ${v.live_calls_enabled ? 'checked' : ''}> Live browser calls to Payal</label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-lph">Live calls per visitor per hour<input id="sv-lph" type="number" name="sarvam_voice.live_calls_per_visitor_per_hour" data-type="number" value="${esc(v.live_calls_per_visitor_per_hour)}"></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-lms">Longest live call (s)<input id="sv-lms" type="number" name="sarvam_voice.live_call_max_seconds" data-type="number" value="${esc(v.live_call_max_seconds)}"></label>
      <label class="dim" style="display:grid;gap:4px;font-size:12px" for="sv-hw">Hotwords (JSON list)<textarea id="sv-hw" rows="2" name="sarvam_voice.hotwords" data-type="json">${esc(JSON.stringify(v.hotwords || []))}</textarea></label></div>
    <p class="form-error"></p><div style="display:flex;justify-content:flex-end"><button class="btn btn-sm btn-primary">Save</button></div></div></form>`;
}

function smokeHtml(r) {
  const turns = (r.turns || []).map(t => `<tr><td>${esc(t.seller)}</td><td class="mono">${t.payal_first_audio_after_ms == null ? '<b>no answer</b>' : `${esc(t.payal_first_audio_after_ms)} ms`}</td></tr>`).join('');
  return `<p style="font-size:12.5px">${r.connected ? '✓ connected' : '✕ not connected'} in ${esc(r.connect_ms)} ms · Payal’s opening started ${r.opening_first_audio_ms == null ? '—' : `${esc(r.opening_first_audio_ms)} ms`} after start · ${esc(r.bot_audio_chunks)} audio chunks · ${esc(Math.round((r.total_ms || 0) / 100) / 10)} s total</p>
    <table class="table"><thead><tr><th>Seller said (Bulbul)</th><th>Payal answered after</th></tr></thead><tbody>${turns}</tbody></table>
    ${(r.transcript || []).length ? `<details open><summary class="dim" style="font-size:12px">Transcript (Sarvam)</summary>${r.transcript.map(t => `<div style="font-size:12px"><span class="mono dim">${esc(t.at_ms)} ms</span> <b>${esc(t.who)}</b> ${esc(t.text)}</div>`).join('')}</details>` : ''}
    <p class="dim mono" style="font-size:11px">events: ${esc(Object.entries(r.events || {}).map(([k, n]) => `${k.split('.').pop()} ${n}`).join(' · '))} · interaction ${esc(r.interaction_id || '—')}</p>`;
}

function buildLog(log) {
  return `<details open><summary class="dim" style="font-size:12px">Last build · ${esc(log.length)} MCP calls</summary><table class="table" style="margin-top:6px">${log.slice(-40).map(e => `<tr><td class="mono" style="font-size:11px">${esc(e.tool)}</td><td>${e.ok ? '✓' : '<b style="color:var(--err)">✕</b>'}</td><td class="mono dim" style="font-size:11px">${esc(e.ms ?? '')} ms</td><td style="font-size:11.5px">${esc(String(e.summary || '').slice(0, 160))}</td></tr>`).join('')}</table></details>`;
}

function renderVoice(s, body) {
  const secret = s.settings.secrets.find(x => x.name === 'VOICE_PLUGIN_KEY');
  body.innerHTML = `<div class="card"><div class="card-head"><h2>Voice bot plug-in</h2>${origin(secret.configured ? 'live_api' : 'off', secret.configured ? 'Key set' : 'No key')}</div><div class="card-body" style="display:grid;gap:10px;font-size:13px">
    <p>The voice bot (LiveKit Agents or Sarvam Voice Agents) is built separately and plugs in over HTTP: it registers a call, pulls the prepared persona and pushes transcript, signal, switch, tool and outcome events. Its calls show on the floor as REAL CALL tiles.</p>
    <p>Contract: <span class="mono">docs/architecture/voice-plugin.md</span> · header <span class="mono">X-Voice-Plugin-Key</span>.</p>
    <form id="voice-key" style="display:flex;gap:6px"><label class="sr" for="vk">Plug-in key</label><input id="vk" type="password" name="value" placeholder="Set a new plug-in key (write-only)" autocomplete="off"><button class="btn btn-primary btn-sm">Save key</button></form>
    <p class="dim">Current: ${secret.configured ? `configured · fingerprint <span class="mono">${esc(secret.fingerprint)}</span>` : 'not configured — the plug-in API refuses every request.'}</p>
    <pre class="code wrap">curl -X POST ${esc(location.origin)}${BASE}/api/v5/voice/sessions \\
  -H 'X-Voice-Plugin-Key: …' -H 'Content-Type: application/json' \\
  -d '{"glid":"91000101","provider":"livekit","direction":"seller_outbound"}'</pre></div></div>`;
}

// Owner, 2026-10-10: "make the settings in admin searchable". Every setting (all sections, including those with a custom
// page) and every API match the words typed; matches are editable in place and saved through the same revisioned PATCH.
const VALUE_SECTION = { sarvam_voice: 'sarvamvoice', voice_plugin: 'voice' };

function searchIndex(s) {
  const label = key => SECTIONS.find(x => x[0] === key)?.[1] || titleCase(key);
  const rows = [];
  for (const [top, values] of Object.entries(s.settings.values || {})) {
    if (!values || typeof values !== 'object') continue;
    const sec = VALUE_SECTION[top] || top;
    for (const [k, v] of Object.entries(values)) {
      const path = `${top}.${k}`;
      rows.push({ kind: 'setting', sec, top, path, v, text: `${label(sec)} ${top} ${path} ${titleCase(k)} ${k.replace(/_/g, ' ')} ${HELP[sec] || ''} ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`.toLowerCase() });
    }
  }
  for (const p of s.prompts?.items || []) {
    rows.push({ kind: 'prompt', sec: 'prompts', p, text: `prompts prompt ${p.id} ${p.title} ${p.group} ${p.used} ${p.where} ${p.label} ${p.text}`.toLowerCase() });
  }
  for (const a of s.apis?.items || []) {
    rows.push({ kind: 'api', sec: 'apis', a, text: `apis api ${a.id} ${a.label} ${a.group} ${a.kind} ${a.mode} ${a.method || ''}`.toLowerCase() });
  }
  return rows;
}

function renderSearch(s, body) {
  const words = s.query.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = searchIndex(s).filter(r => words.every(w => r.text.includes(w)));
  const bySec = {};
  hits.forEach(h => (bySec[h.sec] ||= []).push(h));
  $('#admin-q-count').textContent = `${hits.length} match${hits.length === 1 ? '' : 'es'}`;
  $$('.admin-nav button').forEach(b => {
    const n = (bySec[b.dataset.sec] || []).length;
    b.setAttribute('aria-current', 'false');
    b.classList.toggle('no-hit', !n);
    b.querySelector('.hits')?.remove();
    if (n) b.insertAdjacentHTML('beforeend', ` <span class="hits">${n}</span>`);
  });
  const label = key => SECTIONS.find(x => x[0] === key)?.[1] || titleCase(key);
  const groupsHtml = Object.entries(bySec).map(([sec, list]) => `
    <section class="search-group" aria-label="${esc(label(sec))}"><div class="search-group-head"><h3>${esc(label(sec))}</h3><button class="btn btn-sm btn-ghost" type="button" data-sec="${esc(sec)}">Open section →</button></div>
      <div class="two">${list.map(h => h.kind === 'setting' ? fieldHtml(s, h.path, h.v)
        : h.kind === 'prompt' ? `<div class="search-api"><b>${esc(h.p.title)}</b><span class="dim">${esc(h.p.used)}</span><button class="btn btn-sm" type="button" data-open-prompt="${esc(h.p.id)}">Open prompt →</button></div>`
        : `<div class="search-api"><b>${esc(h.a.label)}</b><span class="mono dim">${esc(h.a.id)} · ${esc(h.a.mode)}</span><button class="btn btn-sm" type="button" data-open-api="${esc(h.a.id)}">Open API →</button></div>`).join('')}</div></section>`).join('');
  const editable = hits.some(h => h.kind === 'setting');
  body.innerHTML = `<form class="card" id="settings-form" data-section="search"><div class="card-head"><h2>Search · “${esc(s.query)}”</h2><span class="aside mono dim" style="font-size:11.5px">revision ${esc(s.settings.revision)}</span></div>
    <div class="card-body" style="display:grid;gap:14px">${hits.length ? groupsHtml : `<p class="muted">No setting or API matches “${esc(s.query)}”. Try one word, such as <i>gateway</i>, <i>voice</i> or <i>cache</i>.</p>`}
      <p class="form-error" id="settings-error"></p>
      ${editable ? '<div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn btn-primary btn-sm">Save changes</button></div>' : ''}</div></form>`;
}

function fieldHtml(s, path, v) {
  const id = `set-${path.replace(/\./g, '-')}`;
  const name = titleCase(path.split('.').pop());
  const locked = s.settings.locked.includes(path) ? 'disabled' : '';
  if (typeof v === 'boolean') return `<label class="switch"><input type="checkbox" name="${esc(path)}" data-type="bool" ${v ? 'checked' : ''} ${locked}> ${esc(name)}</label>`;
  if (typeof v === 'number') return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(name)}<input id="${id}" type="number" step="any" name="${esc(path)}" data-type="number" value="${esc(v)}" ${locked}></label>`;
  if (Array.isArray(v) || (v && typeof v === 'object')) return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(name)} (JSON)<textarea id="${id}" rows="2" name="${esc(path)}" data-type="json" ${locked}>${esc(JSON.stringify(v))}</textarea></label>`;
  return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(name)}<input id="${id}" type="text" name="${esc(path)}" data-type="string" value="${esc(v ?? '')}" ${locked}></label>`;
}

function renderSection(s, body, key) {
  const values = s.settings.values[key] || {};
  const field = (path, v) => {
    const id = `set-${path.replace(/\./g, '-')}`;
    if (typeof v === 'boolean') return `<label class="switch"><input type="checkbox" name="${esc(path)}" data-type="bool" ${v ? 'checked' : ''}> ${esc(titleCase(path.split('.').pop()))}</label>`;
    if (typeof v === 'number') return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(titleCase(path.split('.').pop()))}<input id="${id}" type="number" step="any" name="${esc(path)}" data-type="number" value="${esc(v)}" ${s.settings.locked.includes(path) ? 'disabled' : ''}></label>`;
    if (Array.isArray(v) || (v && typeof v === 'object')) return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(titleCase(path.split('.').pop()))} (JSON)<textarea id="${id}" rows="2" name="${esc(path)}" data-type="json">${esc(JSON.stringify(v))}</textarea></label>`;
    return `<label class="dim" style="display:grid;gap:4px;font-size:12px" for="${id}">${esc(titleCase(path.split('.').pop()))}<input id="${id}" type="text" name="${esc(path)}" data-type="string" value="${esc(v)}" ${s.settings.locked.includes(path) ? 'disabled' : ''}></label>`;
  };
  const extra = key === 'elastic' ? `<p class="dim" style="font-size:12.5px">Index counts: ${Object.entries(s.settings.elastic).map(([k, v]) => `${esc(k)} ${esc(v ?? '—')}`).join(' · ')} · outbox ${esc(s.settings.outbox)}</p>` : key === 'budgets' ? `<p class="dim" style="font-size:12.5px">Used today: public ${esc(s.settings.budget.public.used)}, operator ${esc(s.settings.budget.operator.used)}</p>` : '';
  body.innerHTML = `<form class="card" id="settings-form" data-section="${esc(key)}"><div class="card-head"><h2>${esc(SECTIONS.find(x => x[0] === key)?.[1] || key)}</h2><span class="aside mono dim" style="font-size:11.5px">revision ${esc(s.settings.revision)}</span></div>
    <div class="card-body" style="display:grid;gap:12px">${HELP[key] ? `<p class="muted" style="font-size:12.5px">${esc(HELP[key])}</p>` : ''}${extra}
      <div class="two">${Object.entries(values).map(([k, v]) => field(`${key}.${k}`, v)).join('')}</div>
      <p class="form-error" id="settings-error"></p>
      <div style="display:flex;gap:8px;justify-content:flex-end">${s.settings.history.length ? `<button class="btn btn-sm btn-ghost" type="button" data-rollback="${esc(s.settings.revision - 1)}">Roll back last change</button>` : ''}<button class="btn btn-primary btn-sm">Save</button></div>
      ${s.settings.history.length ? `<details><summary class="dim" style="font-size:12.5px">History</summary><table class="table" style="margin-top:6px">${s.settings.history.slice().reverse().map(h => `<tr><td class="mono">r${esc(h.revision)}</td><td class="mono" style="font-size:11px">${esc(h.at)}</td><td>${esc(h.changed.join(', '))}</td></tr>`).join('')}</table></details>` : ''}
    </div></form>`;
}

async function onClick(s, e) {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.openPrompt) {
    s.section = 'prompts'; s.query = ''; $('#admin-q').value = ''; history.replaceState({}, '', '#prompts'); render(s);
    const card = document.querySelector(`[data-prompt-card="${CSS.escape(b.dataset.openPrompt)}"]`);
    if (card) { card.open = true; card.scrollIntoView({ block: 'start' }); card.querySelector('textarea')?.focus(); }
    return;
  }
  if (b.dataset.promptReset) {
    try { await api(`/api/v5/admin/prompts/${encodeURIComponent(b.dataset.promptReset)}`, { method: 'DELETE' }); toast('Prompt reset to the version in code'); await reloadPrompts(s, b.dataset.promptReset); }
    catch (ex) { toast(ex.message, 'error'); }
    return;
  }
  if (b.dataset.promptBuild) {
    b.disabled = true; const label = b.textContent; b.textContent = 'Building on Sarvam…';
    try { const r = await api(b.dataset.promptBuild, { method: 'POST' }); toast(r.status === 'ok' ? 'Built and committed on Sarvam' : 'Build stopped — see the agent section for the log', r.status === 'ok' ? 'ok' : 'error'); }
    catch (ex) { toast(ex.message, 'error'); }
    finally { b.disabled = false; b.textContent = label; }
    return;
  }
  if (b.dataset.keyReset) {
    try { const r = await api(`/api/v5/admin/secrets/${encodeURIComponent(b.dataset.keyReset)}`, { method: 'PUT', body: { value: '' } }); toast(`${r.name}: ${r.configured ? 'environment value in use' : 'no value configured'}`); await load(s); }
    catch (ex) { toast(ex.message, 'error'); }
    return;
  }
  if (b.hasAttribute('data-fix-keys')) {
    s.section = 'apis'; s.query = ''; $('#admin-q').value = ''; history.replaceState({}, '', '#apis'); render(s);
    const card = $('#key-bulk')?.closest('details'); if (card) card.open = true;
    $('#key-bulk')?.scrollIntoView({ block: 'center' }); $('#key-bulk-value')?.focus();
    return;
  }
  if (b.dataset.sec) { s.section = b.dataset.sec; s.query = ''; $('#admin-q').value = ''; history.replaceState({}, '', `#${s.section}`); render(s); return; }
  if (b.dataset.openApi) {
    s.section = 'apis'; s.query = ''; $('#admin-q').value = ''; history.replaceState({}, '', '#apis'); render(s);
    const row = document.querySelector(`details.api-row[data-api="${CSS.escape(b.dataset.openApi)}"]`);
    if (row) { row.open = true; row.scrollIntoView({ block: 'center' }); row.querySelector('summary')?.focus(); }
    return;
  }
  if (b.dataset.test) {
    b.disabled = true; b.textContent = 'Testing…';
    s.testSeller = ($('#test-seller')?.value || 'syn-91000101').trim();
    try {
      const r = await api(`/api/v5/admin/apis/${b.dataset.test}/test`, { method: 'POST', body: { seller_id: s.testSeller } });
      const meta = s.apis.items.find(a => a.id === b.dataset.test);
      openInspector({ node: b.dataset.test, title: `Test · ${meta.label} · ${s.testSeller}`, returnFocus: b, extra: { node: b.dataset.test, result: r, api: meta, events: [] } });
    } catch (err) { toast(err.message, 'error'); }
    finally { b.disabled = false; b.textContent = 'Test'; }
    return;
  }
  if (b.dataset.sv) {
    const label = b.textContent;
    b.disabled = true; b.textContent = b.dataset.sv.startsWith('build') ? 'Building on Sarvam (1–2 min)…' : 'Working…';
    try {
      if (b.dataset.sv === 'connect') { const r = await api('/api/v5/admin/sarvam-voice/connect', { method: 'POST' }); $('#sv-link').innerHTML = `<a href="${esc(r.authorize_url)}" target="_blank" rel="noopener">Open the Sarvam sign-in ↗</a> <span class="dim">${esc(r.note)}</span>`; }
      if (b.dataset.sv === 'buildbuyer') { const r = await api('/api/v5/admin/sarvam-buyer/build', { method: 'POST' }); toast(r.status === 'ok' ? 'VANI buyer agent built and committed on Sarvam' : 'Build stopped — see the log', r.status === 'ok' ? 'ok' : 'error'); $('#sv-buyer-log').innerHTML = buildLog(r.log); paintBuyerAgent(); }
      if (b.dataset.sv === 'build') { const r = await api('/api/v5/admin/sarvam-voice/build', { method: 'POST' }); toast(r.status === 'ok' ? 'Payal built and committed on Sarvam' : 'Build stopped — see the log', r.status === 'ok' ? 'ok' : 'error'); $('#sv-log').innerHTML = buildLog(r.log); }
      if (b.dataset.sv === 'eval') { await api('/api/v5/admin/sarvam-voice/eval', { method: 'POST' }); toast('Eval run started on Sarvam'); }
      if (b.dataset.sv === 'testkey') { const r = await api('/api/v5/admin/sarvam-voice/test-key', { method: 'POST' }); $('#sv-keyout').textContent = `${r.ok ? '✓' : '✕'} ${r.note}`; }
      if (b.dataset.sv === 'smoke') { b.textContent = 'Calling Payal (≈30 s)…'; const r = await api('/api/v5/admin/sarvam-voice/smoke', { method: 'POST' }); $('#sv-smoke').innerHTML = smokeHtml(r); }
    } catch (err) { toast(err.message, 'error'); }
    finally { b.disabled = false; b.textContent = label; }
    return;
  }
  if (b.dataset.engMeasure !== undefined) { await measureVoices(b, s); return; }
  if (b.dataset.pgRun !== undefined) {
    b.disabled = true;
    try { const r = await api('/api/v5/admin/persona-generator/eval', { method: 'POST' }); toast(`Running ${r.cases} cases on Sarvam`); pollEval(s, r.id); }
    catch (err) { toast(err.message, 'error'); }
    finally { b.disabled = false; }
    return;
  }
  if (b.dataset.pgCase !== undefined) {
    try {
      const r = await api(`/api/v5/admin/persona-generator/eval/${b.dataset.pgEval}/case/${b.dataset.pgCase}`);
      openInspector({ node: 'persona', title: `Persona · ${r.name}`, returnFocus: b, extra: { node: 'persona', result: { label: r.name, status: r.critical ? 'error' : 'ok', origin: 'llm', ms: r.ms,
        note: `Quality ${r.score}/100 · ${r.critical} critical · ${r.warnings} warnings · failed: ${(r.failed || []).map(c => c.check).join('; ') || 'none'}`,
        response: { persona: r.persona, failed_checks: r.failed, scenario_checks: r.scenario_checks, corrected: r.corrected } }, events: [] } });
    } catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (b.dataset.useVoice) {
    try { await api('/api/v5/admin/settings', { method: 'PATCH', body: { changes: { 'engines.tts_model': 'bulbul:v4-flash', 'engines.tts_voice': b.dataset.useVoice }, expected_revision: s.settings.revision } }); s.settings = await api('/api/v5/admin/settings'); toast(`Default voice: ${b.dataset.useVoice}`); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (b.dataset.gwCheck !== undefined) {
    const out = $('#gw-result');
    b.disabled = true; b.textContent = 'Checking…'; out.innerHTML = '<div class="skeleton" style="height:80px"></div>';
    try {
      const r = await api('/api/v5/admin/gateway/check', { method: 'POST' });
      out.innerHTML = r.ok ? `<p style="font-size:12.5px">${origin('live_api', 'Reachable')} <span class="mono">${esc(r.base)}</span> answered in ${esc(fmtMs(r.ms))}. ${r.ours.filter(o => o.covered).length}/${r.ours.length} of our gateway APIs are covered.</p>
        <div class="scroll-x"><table class="table"><thead><tr><th>Our API</th><th>Path</th><th>Gateway</th><th>Host reachable</th></tr></thead><tbody>${r.ours.map(o => `<tr><td class="mono">${esc(o.api)}</td><td class="mono" style="font-size:11px">${esc(o.path)}</td><td>${o.covered ? origin('live_api', 'forwarded') : origin('off', 'not configured')}</td><td>${o.reachable == null ? '—' : o.reachable ? 'yes' : '<b>no</b>'}</td></tr>`).join('')}</tbody></table></div>
        <p class="dim" style="font-size:11.5px">${esc(r.note)}</p>` : `<p class="note">${esc(r.error)}</p>`;
    } catch (err) { out.innerHTML = `<p class="note">${esc(err.message)}</p>`; }
    finally { b.disabled = false; b.textContent = 'Check gateway'; }
    return;
  }
  if (b.dataset.hits) {
    const out = await api(`/api/v5/admin/hits?api=${encodeURIComponent(b.dataset.hits)}&count=20`);
    b.closest('form').querySelector('[data-hitlist]').innerHTML = out.items.length ? `<table class="table">${out.items.map(h => `<tr><td class="mono" style="font-size:11px">${esc(new Date(h.at_epoch * 1000).toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' }))}</td><td>${origin(h.origin || 'default')}</td><td>${esc(h.status)}</td><td class="mono">${esc(fmtMs(h.ms))}</td><td style="font-size:11.5px">${esc(h.note || h.url || '')}</td></tr>`).join('')}</table>` : '<p class="dim">No hits yet.</p>';
    return;
  }
  if (b.dataset.saveKey) {
    const input = b.closest('form').querySelector('[name=secret]');
    if (!input.value) return toast('Paste a key first', 'error');
    try { await api(`/api/v5/admin/apis/${b.dataset.saveKey}/secret`, { method: 'PUT', body: { value: input.value } }); input.value = ''; toast('Key saved (write-only)'); await load(s); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (b.dataset.reset) {
    try { await api(`/api/v5/admin/apis/${b.dataset.reset}/reset`, { method: 'POST', body: { revision: 0, expected_revision: s.apis.revision } }); toast('Reset to default'); await load(s); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (b.dataset.rollback) {
    try { await api('/api/v5/admin/settings/rollback', { method: 'POST', body: { revision: Number(b.dataset.rollback), expected_revision: s.settings.revision } }); toast('Rolled back'); await load(s); }
    catch (err) { toast(err.message, 'error'); }
  }
}

async function onSubmit(s, e) {
  e.preventDefault();
  const form = e.target;
  if (form.id === 'sv-ws') {
    try { await api('/api/v5/admin/settings', { method: 'PATCH', body: { changes: { 'sarvam_voice.workspace_id': String(new FormData(form).get('workspace_id') || '').trim() }, expected_revision: s.settings.revision } }); toast('Workspace id saved'); s.settings = await api('/api/v5/admin/settings'); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (form.id === 'sv-complete') {
    try { await api('/api/v5/admin/sarvam-voice/complete', { method: 'POST', body: { callback_url: new FormData(form).get('callback_url') } }); toast('Connected to Sarvam Voice Agents'); render(s); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (form.id === 'gateway-form') {
    const changes = {};
    const up = s.settings.values.upstream || {};
    const enabled = form.querySelector('[name="upstream.gateway_enabled"]').checked;
    const base = form.querySelector('[name="upstream.gateway_base_url"]').value.trim().replace(/\/+$/, '');
    let headers;
    try { headers = JSON.parse(form.querySelector('[name="upstream.gateway_headers"]').value || '{}'); }
    catch { $('#gw-error').textContent = 'Headers must be a JSON object.'; form.querySelector('[name="upstream.gateway_headers"]').focus(); return; }
    if (enabled !== up.gateway_enabled) changes['upstream.gateway_enabled'] = enabled;
    if (base !== up.gateway_base_url) changes['upstream.gateway_base_url'] = base;
    if (JSON.stringify(headers) !== JSON.stringify(up.gateway_headers)) changes['upstream.gateway_headers'] = headers;
    const access = form.querySelector('[name="upstream.glid_access"]').value;
    if (access !== (up.glid_access || 'dev')) changes['upstream.glid_access'] = access;
    if (!Object.keys(changes).length) return toast('Nothing changed');
    try { await api('/api/v5/admin/settings', { method: 'PATCH', body: { changes, expected_revision: s.settings.revision } }); toast('Gateway saved — the next request uses it'); await load(s); }
    catch (err) { $('#gw-error').textContent = err.message; $('#gw-url').focus(); }
    return;
  }
  if (form.id === 'health-form') {
    s.healthSeller = String(new FormData(form).get('seller') || '').trim() || 'real-23456789';
    return runHealth(s);
  }
  if (form.dataset.prompt) {
    const err = form.querySelector('[data-prompt-err]');
    const text = form.querySelector('textarea').value;
    const p = s.prompts.items.find(x => x.id === form.dataset.prompt);
    if (text === p.text) return toast('Nothing changed');
    err.textContent = '';
    try { await api(`/api/v5/admin/prompts/${encodeURIComponent(p.id)}`, { method: 'PUT', body: { text } }); toast(`${p.title}: saved — ${p.applies}`); await reloadPrompts(s, p.id); }
    catch (ex) { err.textContent = ex.message; const ta = form.querySelector('textarea'); ta.scrollIntoView({ block: 'center' }); ta.focus(); }
    return;
  }
  if (form.classList.contains('key-form')) {
    const value = new FormData(form).get('value');
    if (!value) return toast('Paste a value first', 'error');
    try { await api(`/api/v5/admin/secrets/${encodeURIComponent(form.dataset.key)}`, { method: 'PUT', body: { value } }); toast(`${form.dataset.key} saved (write-only)`); await load(s); runHealth(s); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (form.id === 'key-bulk') {
    const f = new FormData(form), value = String(f.get('value') || '').trim(), names = f.getAll('names').map(String);
    const err = $('#key-bulk-error');
    if (!value) { err.textContent = 'Paste a key first.'; $('#key-bulk-value').focus(); return; }
    if (!names.length) { err.textContent = 'Tick at least one key slot.'; return; }
    try { const r = await api('/api/v5/admin/secrets/bulk', { method: 'POST', body: { value, names } }); toast(`Saved into ${r.saved.length} key slot(s): ${names.join(', ')}`); await load(s); runHealth(s); }
    catch (ex) { err.textContent = ex.message; }
    return;
  }
  if (form.id === 'voice-key') {
    try { await api('/api/v5/admin/secrets/VOICE_PLUGIN_KEY', { method: 'PUT', body: { value: new FormData(form).get('value') } }); toast('Plug-in key saved'); await load(s); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (form.id === 'settings-form' || form.hasAttribute('data-settings')) {
    const changes = {};
    const error = form.querySelector('.form-error');
    try {
      $$('[name]', form).forEach(el => {
        if (el.disabled) return;
        const v = el.dataset.type === 'bool' ? el.checked : el.dataset.type === 'number' ? Number(el.value) : el.dataset.type === 'json' ? JSON.parse(el.value) : el.value;
        const current = el.name.split('.').reduce((o, k) => o?.[k], s.settings.values);
        if (JSON.stringify(current) !== JSON.stringify(v)) changes[el.name] = v;
      });
    } catch { error.textContent = 'One of the JSON fields is not valid JSON.'; return; }
    if (!Object.keys(changes).length) return toast('Nothing changed');
    try { await api('/api/v5/admin/settings', { method: 'PATCH', body: { changes, expected_revision: s.settings.revision } }); toast(`Saved ${Object.keys(changes).length} change(s)`); await load(s); }
    catch (err) { error.textContent = err.message; }
    return;
  }
  if (form.dataset.api) {
    const f = new FormData(form);
    const a = s.apis.items.find(x => x.id === form.dataset.api);
    const changes = {};
    const err = form.querySelector('[data-err]');
    try {
      const candidate = { mode: f.get('mode'), timeout_seconds: Number(f.get('timeout_seconds')), ttl_hours: Number(f.get('ttl_hours')), cache_read: f.get('cache_read') === 'on', persist: f.get('persist') === 'on' };
      if (a.editable) Object.assign(candidate, { method: f.get('method'), url: f.get('url'), params: JSON.parse(f.get('params') || '{}'), body: JSON.parse(f.get('body') || '{}'), headers: JSON.parse(f.get('headers') || '{}') });
      if (a.editable && a.kind === 'http') Object.assign(candidate, { via_gateway: f.get('via_gateway') === 'on', body_format: f.get('body_format') || 'json' });
      for (const [k, v] of Object.entries(candidate)) if (JSON.stringify(a[k]) !== JSON.stringify(v)) changes[k] = v;
    } catch { err.textContent = 'Params, body and headers must be valid JSON objects.'; return; }
    if (!Object.keys(changes).length) return toast('Nothing changed');
    try { await api(`/api/v5/admin/apis/${a.id}`, { method: 'PATCH', body: { changes, expected_revision: s.apis.revision } }); toast(`${a.label}: saved`); await load(s); }
    catch (ex) { err.textContent = ex.message; }
  }
}
