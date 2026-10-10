// Persona page (/persona/{run}) — the PM's main dashboard (5.8): a standalone, clean persona with a Start call button.
// "Open persona" first shows how the persona is built — the APIs called (live, as they answer) and the attributes they
// filled — and then the persona page appears. "How this persona was built" brings that flow back for a finished run.
import { api, BASE, streamRun } from './api.js';
import { $, $$, busy, busyApis, esc, fmtMs, initials, mockTag, pretty, reducedMotion, titleCase } from './ui.js';
import { NODE_LABEL } from './graph.js';
import { closeInspector, openInspector } from './inspector.js';
import { basicsHtml, buyersHtml, engagementHtml, openRecord, planHtml } from './brief.js';

const API_NODES = /^(company|context|dataset|packages|leads|enquiry|pns|lms|sts|redash)\./;
const STEPS = [['apis', 'APIs called'], ['attrs', 'Attributes assembled'], ['persona', 'Persona generated'], ['page', 'Persona page']];

export async function mount(root, { params, navigate }) {
  const s = { runId: params[0], run: null, events: [], brief: null, fields: null, navigate, alive: true, auto: false,
    view: new URLSearchParams(location.search).get('view') === 'flow' ? 'flow' : 'persona', started: performance.now() };
  root.innerHTML = '<div class="page"><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:320px;margin-top:12px"></div></div>';
  try { s.run = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}`); }
  catch (err) {
    busy('nav', false);
    root.innerHTML = `<div class="page"><h1>Persona not available</h1><p class="muted">${esc(err.message)}</p><p style="margin-top:12px"><a class="btn" href="${BASE}/" data-nav>Back to the floor</a></p></div>`;
    return {};
  }
  root.addEventListener('click', e => onClick(root, s, e));
  busy('nav', false);
  if (s.run.status === 'running') {
    busy('run', true, 'Open persona · calling IndiaMART APIs and building the persona…');
    s.view = 'flow'; s.auto = true;
    renderFlow(root, s);
    s.clock = setInterval(() => { const c = $('#pf-clock'); if (c) c.textContent = `${((performance.now() - s.started) / 1000).toFixed(1)} s`; }, 100);
    s.stop = streamRun(s.runId, ev => { s.events.push(ev); paintApis(s); busyApis(apiChips(s.events)); }, () => finished(root, s));
  } else {
    s.events = s.run.events || [];
    if (s.view === 'flow') { renderFlow(root, s); await finished(root, s, true); }
    else await showPersona(root, s);
  }
  return { destroy() { busy('run', false); busyApis([]); s.alive = false; s.stop?.(); clearInterval(s.clock); clearTimeout(s.advance); closeInspector(); } };
}

// ---------------------------------------------------------------- the flow: APIs called → attributes → persona
function seller(s) {
  const b = s.brief?.seller || s.run.seller || {};
  return { name: b.contact_name || b.company_name || 'Seller', company: b.company_name || '', city: b.city || '', glid: b.glid || s.run.glid };
}

function renderFlow(root, s) {
  const who = seller(s);
  root.innerHTML = `
  <div class="page persona-flow">
    <div class="page-head"><div><p class="eyebrow">Open persona · how it is built</p><h1>${esc(who.company && !who.company.startsWith('Private seller') ? who.company : `GLID ${who.glid}`)}</h1>
      <p>Each attribute below comes from an API on the left — live, from the Elastic cache, the supplied dataset, or a labelled mock for demo sellers. Nothing is invented; a field with no data is left out.</p></div>
      <div class="aside"><span class="mono dim" id="pf-clock" aria-live="off">${s.run.duration_ms ? fmtMs(s.run.duration_ms) : '0.0 s'}</span></div></div>
    <ol class="pf-steps" id="pf-steps">${STEPS.map(([k, l], i) => `<li data-step="${k}"><span>${i + 1}</span>${esc(l)}</li>`).join('')}</ol>
    <div class="pf-grid">
      <section class="card" aria-labelledby="pf-api"><div class="card-head"><h2 id="pf-api">APIs called</h2><span class="aside chip" id="pf-api-n">0</span></div>
        <div class="card-body"><ul class="pf-apis" id="pf-apis"><li class="dim">Calling…</li></ul></div></section>
      <section class="card" aria-labelledby="pf-att"><div class="card-head"><h2 id="pf-att">Attributes we use</h2><span class="aside chip" id="pf-att-n">waiting</span></div>
        <div class="card-body" id="pf-attrs"><p class="dim">The attributes fill in once the sources have answered.</p></div></section>
    </div>
    <div class="pf-foot" id="pf-foot" aria-live="polite"></div>
  </div>`;
  paintApis(s);
}

// One short line per API: the HTTP size or the reason, not the repeated policy notes (PM: "not too verbose").
const short = d => String(d || '').split(' · ').filter(x => !/^Synthetic seller: never sent upstream/.test(x)).slice(0, 2).join(' · ').slice(0, 90);
const WITHHELD = new Set(['phone_number']);  // contact details are never shown, even masked

function apiRows(s) {
  const rows = new Map();
  for (const ev of s.events) {
    if (!ev.node || !API_NODES.test(ev.node)) continue;
    const prev = rows.get(ev.node);
    if (ev.kind === 'start' && prev) continue;
    if (ev.kind === 'cache' && ev.status !== 'hit' && prev) continue;  // a cache miss is followed by the real answer
    rows.set(ev.node, ev);
  }
  return [...rows.values()];
}

function paintApis(s) {
  const list = $('#pf-apis');
  if (!list) return;
  const rows = apiRows(s);
  const state = ev => ev.kind === 'start' ? 'run' : ev.kind === 'skip' ? 'skip' : ev.status === 'error' ? 'err' : 'ok';
  list.innerHTML = rows.map(ev => {
    const [base, key] = ev.node.split(':');
    const label = `${NODE_LABEL[base] || base}${key ? ` · ${key}` : ''}`;
    const o = ev.kind === 'cache' && ev.status === 'hit' ? 'elastic_cache' : ev.origin;
    return `<li class="st-${state(ev)}"><button type="button" class="pf-api" data-node="${esc(ev.node)}" ${s.run.status === 'running' ? 'disabled' : ''}>
      <span class="pf-mark" aria-hidden="true">${state(ev) === 'ok' ? '✓' : state(ev) === 'err' ? '✕' : state(ev) === 'skip' ? '○' : '…'}</span>
      <b>${esc(label)}</b>${ev.kind === 'start' ? '<span class="dim">calling…</span>' : mockTag(o)}<span class="mono dim">${ev.ms != null ? esc(fmtMs(ev.ms)) : ''}</span>
      <span class="pf-det" title="${esc(ev.detail || '')}">${esc(short(ev.detail))}</span></button></li>`;
  }).join('') || '<li class="dim">Calling…</li>';
  const n = $('#pf-api-n'); if (n) n.textContent = `${rows.filter(r => r.kind !== 'start').length} answered`;
  step(s.events.some(e => e.node === 'facts' || e.node === 'contract') ? 'attrs' : 'apis');
}

function step(current) {
  const order = STEPS.map(([k]) => k), at = order.indexOf(current);
  $$('#pf-steps li').forEach((li, i) => { li.classList.toggle('done', i < at); li.classList.toggle('now', i === at); });
}

async function finished(root, s, quiet = false) {
  clearInterval(s.clock);
  busy('run', false);
  busyApis([]);
  try {
    if (!quiet) s.run = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}`);
    [s.fields, s.brief] = await Promise.all([api(`/api/v5/runs/${encodeURIComponent(s.runId)}/fields`), api(`/api/v5/runs/${encodeURIComponent(s.runId)}/brief`).catch(() => null)]);
  } catch (err) { $('#pf-foot').innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  if (!s.alive) return;
  if (!s.events.length) s.events = s.run.events || [];
  paintApis(s);
  paintAttributes(s);
  const ok = ['completed', 'degraded'].includes(s.run.status);
  step(ok ? 'persona' : 'attrs');
  const engine = s.run.persona?.engine === 'persona_generator_v0.3' ? 'Persona Generator v0.3 on Sarvam' : s.run.persona?.origin === 'llm' ? 'Sarvam' : 'rules';
  const q = s.brief?.quality;
  $('#pf-foot').innerHTML = ok
    ? `<p><b>✓ Persona ready${s.run.duration_ms ? ` in ${esc(fmtMs(s.run.duration_ms))}` : ''}</b> · ${esc(engine)}${q?.score != null ? ` · quality ${esc(q.score)}/100 (${esc(q.passed)}/${esc(q.total)} checks)` : ''}</p>
       <button class="btn btn-primary" data-act="persona">Open the persona page →</button>`
    : `<p class="note">Preparation ${esc(s.run.status)}: ${esc(s.run.error || '')}</p>`;
  if (ok && s.auto) s.advance = setTimeout(() => s.alive && showPersona(root, s), reducedMotion() ? 600 : 1600);
}

function paintAttributes(s) {
  const box = $('#pf-attrs');
  if (!box || !s.fields?.fields) return;
  const f = s.fields.fields, groups = s.fields.groups || {};
  const shown = new Set();
  const row = name => {
    const x = f[name]; if (!x) return '';
    shown.add(name);
    // Owner, 2026-10-10: only fields that have data are listed, without origin tags (a demo seller's mock stays labelled).
    if (x.availability !== 'present') return '';
    const cell = WITHHELD.has(name) ? '<span class="dim">present · withheld</span>' : esc(String(pretty(x.value)).slice(0, 90));
    return `<tr><th scope="row">${esc(titleCase(name))}</th><td>${cell}</td>
      <td>${mockTag(x.origin)}${x.api ? ` <button type="button" class="linkish" data-node="${esc(x.api)}" data-pointer="${esc(x.pointer || '')}">source</button>` : ''}</td></tr>`;
  };
  const html = Object.entries(groups).map(([g, names]) => { const body = names.map(row).join(''); return body ? `<p class="kicker">${esc(g)}</p><table class="table pf-attr"><tbody>${body}</tbody></table>` : ''; }).join('');
  const rest = Object.keys(f).filter(k => !shown.has(k) && f[k]?.availability === 'present');
  const gen = s.events.find(e => e.node === 'sarvam.aggregate' && e.kind === 'start');
  const c = s.fields.completeness || {};
  box.innerHTML = `${gen ? `<p class="pf-gen"><b>Persona generator input:</b> ${esc(gen.detail)}</p>` : ''}${html}${rest.length ? `<p class="kicker">Other</p><table class="table pf-attr"><tbody>${rest.map(row).join('')}</tbody></table>` : ''}`;
  const n = $('#pf-att-n'); if (n) n.textContent = c.total ? `${c.present}/${c.total} present` : 'ready';
}

// ---------------------------------------------------------------- the persona page (PM sample UI: clean, not verbose)
async function showPersona(root, s) {
  clearTimeout(s.advance);
  s.view = 'persona';
  if (location.search) history.replaceState({}, '', location.pathname);
  if (!s.brief) {
    try { s.brief = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}/brief`); }
    catch (err) { root.innerHTML = `<div class="page"><h1>Persona not ready</h1><p class="muted">${esc(err.message)}</p></div>`; return; }
  }
  if (!s.alive) return;
  const b = s.brief, who = seller(s);
  const headline = b.plan?.headline || '';
  root.innerHTML = `
  <div class="page persona-page">
    <header class="persona-head">
      <div class="avatar" aria-hidden="true">${esc(initials(who.name))}</div>
      <div class="ident"><p class="eyebrow">Persona${headline ? ` · ${esc(headline)}` : ''}</p><h1 tabindex="-1">${esc(who.name)}</h1>
        <p class="dim">${esc([who.company !== who.name ? who.company : '', who.city, `GLID ${who.glid}`].filter(Boolean).join(' · '))}${b.data_mode === 'mock data' ? ` · ${mockTag('mock', 'demo data')}` : ''}${b.seller?.identity_note ? ` · <span title="${esc(b.seller.identity_note)}">${esc(b.seller.identity_note)}</span>` : ''}</p></div>
      <div class="persona-actions">
        <button class="btn btn-ghost" data-act="flow">How this persona was built</button>
        <button class="btn btn-talk btn-lg" data-act="start">📞 Start call</button>
      </div>
    </header>
    <div class="pm-grid">${basicsHtml(b)}${engagementHtml(b)}${buyersHtml(b)}</div>
    ${planHtml(b)}
    <button type="button" class="pm-recordlink" data-record>📄 View full record for ${esc(who.name)}: ${esc(b.record.total)} calls, meetings and notes <span class="dim">— every entry with its outcome, evidence rules and data gaps</span></button>
  </div>`;
  $('.persona-head h1')?.focus?.();
}

// ---------------------------------------------------------------- interactions
function onClick(root, s, e) {
  const t = e.target.closest('button, [data-act]');
  if (!t) return;
  if (t.dataset.act === 'start') {
    // Start call = Talk as Seller on this run: the bot opens as the executive and the operator answers as the seller.
    try { sessionStorage.setItem(`sg-talk-${s.runId}`, '1'); } catch { /* storage blocked: the call still opens */ }
    s.navigate(`/call/${s.runId}?view=talk`);
    return;
  }
  if (t.dataset.act === 'flow') { s.auto = false; history.replaceState({}, '', `${location.pathname}?view=flow`); renderFlow(root, s); finished(root, s, true); return; }
  if (t.dataset.act === 'persona') { showPersona(root, s); return; }
  if (t.hasAttribute('data-record') && s.brief) { openRecord(s.brief, t, (src, el) => source(s, src, el)); return; }
  if (t.dataset.src) { source(s, JSON.parse(t.dataset.src), t); return; }
  if (t.dataset.node && s.run.status !== 'running') source(s, { api: t.dataset.node, pointer: t.dataset.pointer || '' }, t);
}

function source(s, src, el) {
  if (!src?.api) return;
  const pointer = /^\/[^ ]*$/.test(src.pointer || '') ? src.pointer : '';
  openInspector({ runId: s.runId, node: src.api, focus: pointer, events: s.events, returnFocus: el });
}

// 5.13 (owner): the loading bar shows each API — loading in orange (breathing), loaded in green, failed in red.
const NOT_API = /^(trigger|orchestrator|facts|contract|teleprompter|done|persona\.index|elastic\.persist|call\.|sarvam\.)/;
function apiChips(events) {
  const map = new Map();
  for (const ev of events) {
    const node = ev.ref || ev.node;
    if (!node || NOT_API.test(node)) continue;
    const st = ev.kind === 'start' ? 'run' : ev.status === 'error' ? 'err' : ev.kind === 'skip' ? 'skip' : ev.kind === 'cache' && ev.status !== 'hit' ? null : 'ok';
    const prev = map.get(node) || { label: ev.label || node, short: String(ev.label || node).split(' · ').slice(-1)[0].slice(0, 26), state: 'run' };
    if (st && !(prev.state !== 'run' && st === 'run')) prev.state = st;
    map.set(node, prev);
  }
  return [...map.values()];
}

