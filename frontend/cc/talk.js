// Talk as Seller — the call workspace (5.13). Owner, 2026-10-10:
// - "get back to older screen, that three pane thing": left = the persona (all three PM cards, top items + Show more,
//   every value clickable → the side bar opens the API response with the value highlighted); middle = the call (agent,
//   transcript, auto-scrolled, persona / language / tone as it changes); right = a live log of everything being done
//   (APIs, Elasticsearch writes, the LLM, the agent, switches) — each new entry flashes green and scrolls into view,
//   and clicking one opens the side bar; bottom = the architecture, horizontal, across the middle and right panes.
// - Clicking outside the side bar closes it (the scrim). Everything fits on one screen.
// The call itself keeps 5.11's rule: exactly one agent per call (built-in Payal or one Sarvam workspace agent), the
// chosen one starts the call, and changing it restarts the call.
import { api, BASE, streamRun } from './api.js';
import { $, busy, busyApis, esc, fmtMs, initials, origin, toast } from './ui.js';
import { closeInspector, openInspector } from './inspector.js';
import { basicsHtml, buyersHtml, engagementHtml, openRecord } from './brief.js';
import { catalogue, effective } from './engines.js';
import { agentBarHtml, agentBlocked, agentInfo, agentLine, chooseAgent, chosenApp, currentAgent } from './agent.js';
import { playToEnd, prestartedOr, RealtimeListener, speakLine, speakOpts } from './voice.js';

const SIGNAL_TEXT = {
  rush: 'Seller is in a hurry → one short line and the direct ask',
  confusion: 'Seller is confused → one idea at a time, simple words',
  frustration: 'Seller is annoyed → acknowledge first, no pitch',
  interest: 'Seller is interested → straight to the next step',
  language_switch: 'Language switch → follow the seller’s language',
  bot_question: 'Asked if this is a bot → says it is IndiaMART’s virtual assistant',
  stop: 'Stop request → the call ends',
};
const STATE_LABEL = { preparing: 'Preparing', ready: 'Ready', connecting: 'Connecting…', listening: 'Listening', speaking: 'Speaking', thinking: 'Thinking…', ended: 'Call ended' };
const LANG = { 'hi-IN': 'Hindi', 'en-IN': 'English', 'te-IN': 'Telugu', 'ta-IN': 'Tamil', 'mr-IN': 'Marathi', 'gu-IN': 'Gujarati', 'bn-IN': 'Bengali', 'kn-IN': 'Kannada', 'ml-IN': 'Malayalam', 'pa-IN': 'Punjabi' };
const NOT_API = /^(trigger|orchestrator|facts|contract|teleprompter|done|persona\.index|elastic\.persist|call\.|sarvam\.)/;
const STAGES = [['trigger', 'Trigger'], ['apis', 'IndiaMART APIs · parallel'], ['elastic', 'Elasticsearch'], ['llm', 'Persona · Sarvam LLM'], ['agent', 'Voice agent'], ['outcome', 'Outcome']];
const MORE = { 'w-basics': 'basic details', 'w-eng': 'engagement with IndiaMART', 'w-buyers': 'engagement with buyers', 'w-tp': 'what the agent knows' };

export async function mount(root, { params, navigate }) {
  const s = { runId: params[0], run: null, brief: null, cat: null, eng: null, info: null, alive: true, abort: new AbortController(), navigate,
    state: 'preparing', live: null, starting: false, lines: [], call: null, listener: null, audio: null, prestarted: {}, thinking: false,
    pending: '', lastBot: '', applied: 0, micStats: null, events: [], seenSeq: new Set(), logs: [], delivery: {}, outcome: null, autoStart: false };
  try { s.autoStart = sessionStorage.getItem(`sg-talk-${s.runId}`) === '1'; sessionStorage.removeItem(`sg-talk-${s.runId}`); } catch { /* storage blocked */ }
  if (new URLSearchParams(location.search).has('debug')) window.sgTalk = s;   // QA: inspect the call state from the console
  root.innerHTML = `<div class="ws">
    <aside class="ws-pane ws-left" aria-label="Persona"><div id="w-persona"><div class="skeleton" style="height:90px"></div><div class="skeleton" style="height:260px;margin-top:10px"></div></div></aside>
    <section class="ws-pane ws-mid" aria-label="Call">
      <header class="ws-head" id="w-head"><div class="skeleton" style="width:240px;height:22px"></div></header>
      <div class="ws-agent" id="w-agent"></div>
      <div class="ws-ctl">
        <button class="btn btn-talk ws-main" id="w-main" disabled>Preparing the seller…</button>
        <span class="chip" id="w-state" aria-live="polite">Preparing</span>
        <span class="ws-delivery" id="w-delivery" aria-label="Current delivery"></span>
      </div>
      <div id="w-switch"></div>
      <div class="ws-lines" id="w-lines" aria-live="polite"><p class="dim">Pulling the seller’s live IndiaMART data and building the persona…</p></div>
      <p class="ws-partial dim" id="w-partial" aria-live="polite"></p>
      <form class="composer" id="w-type" hidden><label class="sr" for="w-input">Type as the seller</label>
        <input id="w-input" maxlength="500" placeholder="Or type your reply as the seller" autocomplete="off"><button class="btn">Send</button></form>
    </section>
    <section class="ws-pane ws-right" aria-label="Live log">
      <div class="ws-pane-head"><h2>Live log</h2><span class="dim" id="w-logcount"></span></div>
      <ol class="ws-log" id="w-log" aria-live="polite"></ol>
    </section>
    <section class="ws-pane ws-arch" aria-label="Architecture"><div class="ws-pane-head"><h2>Architecture · this call</h2><span class="dim">click any box for its request and response</span></div>
      <div class="ws-flow" id="w-arch"></div></section>
  </div>`;
  root.addEventListener('click', e => onClick(s, e));
  root.addEventListener('change', e => { const a = e.target.closest('[data-agent-select]'); if (a) changeAgent(s, a.value); });
  root.addEventListener('submit', e => onSubmit(s, e));

  try { s.run = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}`); }
  catch (err) { busy('nav', false); root.innerHTML = `<div class="page"><h1>Call not available</h1><p class="muted">${esc(err.message)}</p><p style="margin-top:12px"><a class="btn" href="${BASE}/" data-nav>Back to the floor</a></p></div>`; return {}; }
  busy('nav', false);
  head(s);
  [s.cat, s.info] = await Promise.all([catalogue().catch(() => null), agentInfo().catch(() => null)]);
  s.eng = s.cat ? effective(s.cat, 'call') : null;
  paintAgent(s);
  if (s.run.status === 'running') {
    busy('run', true, 'Talk as Seller · pulling live IndiaMART data and building the persona…');
    s.stop = streamRun(s.runId, ev => onRunEvent(s, ev, true), () => ready(s));
  } else {
    (s.run.events || []).forEach(ev => onRunEvent(s, ev, false));
    await ready(s);
  }
  return {
    destroy() { s.alive = false; busy('run', false); busy('nav', false); busyApis([]); s.abort.abort(); s.stop?.(); endCall(s, true); closeInspector(); },
  };
}

// ---------------------------------------------------------------- run events → log + architecture + loading chips
function evState(ev) {
  if (ev.kind === 'start') return 'run';
  if (ev.status === 'error' || ev.kind === 'error') return 'err';
  if (ev.kind === 'skip') return 'skip';
  return 'ok';
}

function onRunEvent(s, ev, live) {
  if (ev.seq != null) { if (s.seenSeq.has(ev.seq)) return; s.seenSeq.add(ev.seq); }
  s.events.push(ev);
  if (ev.kind !== 'start' || !ev.node || NOT_API.test(ev.node)) addLog(s, logFromEvent(ev), live);
  paintArch(s);
  if (live) busyApis(apiStates(s).map(a => ({ label: a.label, short: a.short, state: a.state })));
}

function logFromEvent(ev) {
  const node = ev.ref || ev.node || '';
  const kind = ev.node === 'elastic.persist' || ev.kind === 'cache' || ev.node === 'persona.index' || ev.origin === 'elastic' || ev.origin === 'elastic_cache' ? 'es'
    : String(node).startsWith('sarvam.') || ev.origin === 'llm' ? 'llm' : String(node).startsWith('call.') ? 'call' : NOT_API.test(node) ? 'sys' : 'api';
  return { kind, title: ev.label || node, detail: ev.detail || '', node: kind === 'api' || kind === 'llm' ? node : ev.node === 'elastic.persist' ? (ev.ref || '') : '',
    origin: ev.origin, status: ev.status, ms: ev.ms, at: ev.at, ev };
}

function apiStates(s) {
  const map = new Map();
  for (const ev of s.events) {
    const node = ev.ref || ev.node;
    if (!node || NOT_API.test(node)) continue;
    const prev = map.get(node) || { node, label: ev.label || node, short: shortLabel(ev.label || node), state: 'run', origin: null, ms: null };
    const st = evState(ev);
    const cacheMiss = ev.kind === 'cache' && ev.status !== 'hit';
    if (!cacheMiss && !(prev.state !== 'run' && st === 'run')) prev.state = st;
    if (ev.origin && ev.kind !== 'start') prev.origin = ev.origin;
    if (ev.ms != null) prev.ms = ev.ms;
    map.set(node, prev);
  }
  return [...map.values()];
}

const shortLabel = l => String(l).split(' · ').slice(-1)[0].replace(/\s*\(.*\)$/, '').slice(0, 26);

// ---------------------------------------------------------------- preparation done
async function ready(s) {
  busy('run', false); busyApis([]);
  try { s.run = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}`); } catch (err) { toast(err.message, 'error'); return; }
  if (!s.alive) return;
  (s.run.events || []).forEach(ev => onRunEvent(s, ev, false));
  head(s);
  const ok = ['completed', 'degraded'].includes(s.run.status);
  if (!ok) { setState(s, 'ended'); $('#w-lines').innerHTML = `<p class="note">Preparation ${esc(s.run.status)}: ${esc(s.run.error || '')}</p>`; return; }
  try { s.brief = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}/brief`, { signal: s.abort.signal }); } catch { s.brief = null; }
  paintPersona(s);
  const f = s.run.persona?.factors || {};
  s.delivery = { language: f.language?.value, pace: f.pace?.value, warmth: f.warmth?.value, formality: f.formality?.value };
  paintDelivery(s);
  addLog(s, { kind: 'sys', title: `Persona ready${s.run.duration_ms ? ` in ${fmtMs(s.run.duration_ms)}` : ''}`, detail: s.brief?.plan?.headline || '' }, true);
  setState(s, 'ready');
  $('#w-lines').innerHTML = `<p class="dim">Ready. ${esc(agentLine(s.cat, s.info, 'call'))} will open the call; you answer as the seller.</p>`;
  if (s.autoStart) startCall(s);
}

// ---------------------------------------------------------------- left pane: the persona
function clamp(id, body) {
  return `<section class="ws-card clamped" id="${id}">${body}<button type="button" class="ws-more" data-more="${id}" aria-expanded="false">Show more · ${esc(MORE[id])}</button></section>`;
}

function paintPersona(s) {
  const el = $('#w-persona');
  if (!el) return;
  const b = s.brief, tp = b?.talking_points || {};
  const seller = s.run.seller || {};
  const point = (k, label) => tp[k] ? `<dt>${esc(label)}</dt><dd>${esc(tp[k])}</dd>` : '';
  const talk = Object.values(tp).some(Boolean) ? clamp('w-tp', `<h3 class="ws-card-h">What the agent knows</h3><dl class="ws-dl">
      ${point('call_reason', 'Reason to call')}${point('payment_status', 'Payment')}${point('market_snapshot', 'Market')}${point('competitors', 'Competitors')}${point('buyer_activity', 'Buyers')}${point('product_overview', 'Products')}</dl>`) : '';
  el.innerHTML = `<div class="ws-who"><div class="avatar" aria-hidden="true">${esc(initials(seller.company_name || 'S'))}</div>
      <div><b>${esc(seller.company_name || 'Seller')}</b><span class="dim">${[seller.contact_name, seller.city, `GLID ${seller.glid}`].filter(Boolean).map(esc).join(' · ')}</span>
      <span class="dim">${seller.synthetic === false ? 'real seller · live IndiaMART data' : 'synthetic seller'}${b?.plan?.headline ? ` · ${esc(b.plan.headline)}` : ''}</span></div></div>
    ${talk}
    ${b ? clamp('w-basics', basicsHtml(b, { compact: true })) + clamp('w-eng', engagementHtml(b, { compact: true })) + clamp('w-buyers', buyersHtml(b))
        : '<p class="note">The persona brief could not be loaded.</p>'}
    <details class="ws-card" id="w-meeting"><summary><h3 class="ws-card-h">Book a meeting</h3></summary>${meetingForm(s)}</details>
    <details class="ws-card" id="w-debug"><summary><h3 class="ws-card-h">Debug · agents, account, microphone</h3></summary><div id="w-debug-body"></div></details>
    <p class="ws-links dim"><a href="${BASE}/persona/${encodeURIComponent(s.runId)}" data-nav>Persona page</a> · <a href="${BASE}/persona/${encodeURIComponent(s.runId)}?view=flow" data-nav>How it was built</a> · <a href="${BASE}/call/${encodeURIComponent(s.runId)}?view=full" data-nav>Full workspace</a></p>`;
  paintDebug(s);
}

// ---------------------------------------------------------------- right pane: the live log
function addLog(s, entry, flash = true) {
  s.logs.push(entry);
  const list = $('#w-log');
  if (!list) return;
  const i = s.logs.length - 1;
  const ms = entry.ms != null ? `<span class="mono">${esc(fmtMs(entry.ms))}</span>` : '';
  const badge = { api: origin(entry.origin || 'live_api'), es: origin('elastic', 'Elastic'), llm: origin('llm', 'Sarvam'), call: '<span class="ws-tag ws-tag-call">Call</span>', sys: '<span class="ws-tag">SellerGenome</span>' }[entry.kind] || '';
  const time = entry.at ? new Date(entry.at) : new Date();
  // k-* class names: plain "call" collides with the full-height .call layout of the classic workspace (live QA, 5.13.2).
  list.insertAdjacentHTML('beforeend', `<li class="ws-log-item k-${entry.kind} ${entry.status === 'error' ? 'k-err' : ''} ${flash ? 'new' : ''}">
    <button type="button" data-log="${i}"><span class="ws-log-top">${badge}<b>${esc(entry.title)}</b>${ms}</span>
    ${entry.detail ? `<span class="ws-log-detail">${esc(entry.detail)}</span>` : ''}<span class="ws-log-time mono">${esc(time.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false }))}</span></button></li>`);
  const item = list.lastElementChild;
  if (flash) { list.scrollTo({ top: list.scrollHeight, behavior: 'smooth' }); setTimeout(() => item.classList.remove('new'), 2600); }
  else list.scrollTop = list.scrollHeight;
  const c = $('#w-logcount');
  if (c) c.textContent = `${s.logs.length} events`;
}

// What happens on the call is also recorded on the run (and queued to Elasticsearch's events index): logged twice —
// the event itself, then the write.
async function callEvent(s, kind, label, detail = '', org = 'internal') {
  addLog(s, { kind: 'call', title: label, detail, data: { kind, label, detail } }, true);
  try {
    const out = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}/call-events`, { method: 'POST', body: { kind, label, detail: String(detail).slice(0, 600), origin: org } });
    if (out.elastic?.queued) addLog(s, { kind: 'es', title: `Saved to Elasticsearch · ${out.elastic.index}`, detail: `run event #${out.seq}: ${label}`, data: out }, true);
  } catch { /* the call goes on without the record */ }
}

// ---------------------------------------------------------------- bottom: the architecture, horizontal
function paintArch(s) {
  const el = $('#w-arch');
  if (!el) return;
  const apis = apiStates(s);
  const writes = s.events.filter(e => e.node === 'elastic.persist' && e.status === 'ok').length;
  const hits = s.events.filter(e => e.kind === 'cache' && e.status === 'hit').length;
  const indexed = s.events.some(e => e.node === 'persona.index' && e.kind === 'persist');
  const llm = ['sarvam.persona_generator', 'sarvam.aggregate', 'sarvam.locality'].map(n => {
    const evs = s.events.filter(e => e.node === n || e.ref === n);
    return evs.length ? { node: n, label: shortLabel(evs.at(-1).label || n), state: evState(evs.at(-1)), ms: evs.at(-1).ms } : null;
  }).filter(Boolean);
  // An API box opens its request and response; a stage box (trigger, Elasticsearch, agent) opens the events behind it.
  const box = (node, label, state, meta = '', stage = '') => `<button type="button" class="ws-node ${esc(state)}" ${node ? `data-node="${esc(node)}"` : stage ? `data-stage="${esc(stage)}"` : 'disabled'} title="${esc(label)}"><span>${esc(label)}</span>${meta ? `<small>${esc(meta)}</small>` : ''}</button>`;
  const trig = s.events.find(e => e.node === 'trigger');
  const agent = s.cat && s.info ? agentLine(s.cat, s.info, 'call').split(' · ')[0].replace(/\s*\(SellerGenome[^)]*\)/, '') : 'Agent';
  const col = {
    trigger: box('', trig ? shortLabel(trig.label || 'Seller activity') : 'Seller activity', trig ? 'ok' : 'run', '', 'trigger'),
    apis: apis.map(a => box(a.node, a.short, a.state, a.ms != null ? fmtMs(a.ms) : '')).join('') || box('', 'waiting', 'run'),
    elastic: `${box('', `Cache · ${hits} hit${hits === 1 ? '' : 's'}`, hits ? 'ok' : 'skip', '', 'cache')}${box('', `Responses written · ${writes}`, writes ? 'ok' : 'run', '', 'writes')}${box('', `Persona index${indexed ? '' : ' (queued)'}`, indexed ? 'ok' : 'run', '', 'index')}`,
    llm: llm.map(x => box(x.node, x.label, x.state, x.ms != null ? fmtMs(x.ms) : '')).join('') || box('', 'Persona Generator v0.3', 'run'),
    agent: box('', agent, s.live ? 'ok live' : s.state === 'ended' ? 'skip' : 'idle', STATE_LABEL[s.state] || s.state, 'agent'),
    outcome: box('', s.outcome || 'Meeting / callback', s.outcome ? 'ok' : 'idle', '', 'outcome'),
  };
  el.innerHTML = STAGES.map(([k, t], i) => `<div class="ws-stage ${k}"><h4>${esc(t)}</h4><div class="ws-stage-body">${col[k]}</div></div>${i < STAGES.length - 1 ? '<span class="ws-arrow" aria-hidden="true">→</span>' : ''}`).join('');
}

// ---------------------------------------------------------------- middle: header, agent, state, delivery
function head(s) {
  const seller = s.run.seller || {};
  $('#w-head').innerHTML = `<div class="ident"><h1>${esc(seller.company_name || 'Seller')}</h1><span>${[seller.contact_name, seller.city, `GLID ${seller.glid}`].filter(Boolean).map(esc).join(' · ')}</span></div>
    <span class="onair">${s.run.status === 'running' ? 'PREPARING' : s.live ? 'ON CALL' : 'READY'}</span>
    <a class="btn btn-sm btn-ghost" href="${BASE}/" data-nav>Leave</a>`;
}

function paintAgent(s) {
  const el = $('#w-agent');
  if (el) el.innerHTML = s.cat && s.info ? agentBarHtml(s.cat, s.info, { place: 'call' }) : '<p class="dim">Agents could not be loaded — the built-in agent will be used.</p>';
}

function paintDelivery(s) {
  const d = s.delivery || {};
  const chip = (k, v) => v != null && v !== '' ? `<span class="ws-dchip" data-d="${k}"><i>${esc(k)}</i>${esc(v)}</span>` : '';
  const el = $('#w-delivery');
  if (el) el.innerHTML = chip('language', LANG[d.language] || d.language) + chip('pace', d.pace) + chip('warmth', d.warmth) + chip('formality', d.formality) + chip('tone', d.tone);
}

function setState(s, state) {
  s.state = state;
  const chip = $('#w-state');
  if (chip) { chip.textContent = STATE_LABEL[state] || state; chip.className = `chip ${['listening', 'speaking', 'thinking'].includes(state) ? 'solid' : ''}`; }
  const b = $('#w-main');
  if (b) {
    const on = !!s.live || s.starting;
    b.disabled = state === 'preparing' || (s.starting && !s.live);
    b.textContent = state === 'preparing' ? 'Preparing the seller…' : on ? '■ End call' : state === 'ended' ? '▶ Start the call again' : '▶ Start call';
    b.classList.toggle('btn-talk', !on);
  }
  const onair = $('#w-head .onair');
  if (onair && s.run) onair.textContent = s.run.status === 'running' ? 'PREPARING' : s.live ? 'ON CALL' : 'READY';
  const typing = $('#w-type');
  if (typing) typing.hidden = !(s.live && s.live.kind === 'builtin');
  paintArch(s);
}

// ---------------------------------------------------------------- the one call
async function startCall(s) {
  if (s.starting || s.live || s.state === 'preparing') return;   // one session at a time — a second click never opens another
  const why = s.cat && s.info ? agentBlocked(s.cat, s.info, 'call') : '';
  if (why) { toast(why, 'error'); return; }
  s.starting = true;
  s.lines = []; s.call = null; s.applied = 0; s.outcome = null; paintLines(s); $('#w-switch').innerHTML = '';
  setState(s, 'connecting');
  const app = s.cat && s.info ? chosenApp(s.cat, s.info, 'call') : null;
  try {
    if (app) await startSarvam(s, app); else await startBuiltin(s);
  } catch (err) {
    toast(err.message, 'error');
    addLog(s, { kind: 'call', status: 'error', title: 'The call could not start', detail: err.message }, true);
    await endCall(s, true);
  } finally { s.starting = false; if (s.alive) setState(s, s.live ? (s.state === 'connecting' ? 'listening' : s.state) : 'ended'); }
}

async function endCall(s, quiet = false) {
  const live = s.live;
  s.live = null;
  s.starting = false;
  s.listener?.stop(); s.listener = null;
  s.audio?.pause(); s.audio = null;
  if ($('#w-partial')) $('#w-partial').textContent = '';
  if (live?.stop) await live.stop().catch(() => {});
  if (live && s.alive) callEvent(s, 'end', 'Call ended', `${s.lines.length} lines on the transcript`);
  if (!quiet && s.alive) setState(s, 'ended');
}

async function changeAgent(s, id) {
  if (s.cat && s.info && currentAgent(s.cat, s.info, 'call') === id) return;
  const wasLive = !!s.live || s.starting;
  chooseAgent('call', id);
  s.eng = s.cat ? effective(s.cat, 'call') : s.eng;
  if (wasLive) await endCall(s, true);
  paintAgent(s); paintDebug(s); paintArch(s);
  toast(`Agent: ${agentLine(s.cat, s.info, 'call')}${wasLive ? ' — restarting the call' : ''}`);
  if (wasLive) startCall(s); else if (s.state !== 'preparing') setState(s, s.state === 'ended' ? 'ended' : 'ready');
}

// A Sarvam Voice Agent (Payal, Manjula Executive v2, …): Sarvam listens, speaks and takes turns.
async function startSarvam(s, app) {
  const { startPayalCall } = await import('./payal-live.js');
  const handle = await startPayalCall({ sellerId: s.run.seller_id, runId: s.runId, appId: app,
    onLine: line => { addLine(s, line.who === 'bot' ? 'agent' : 'you', line.text, line.opening ? 'opening line' : ''); },
    onState: st => { if (s.live?.kind === 'sarvam') setState(s, st === 'listening' || st === 'speaking' ? st : st === 'connected' ? 'listening' : s.state); },
    onSignal: x => {
      const t = SIGNAL_TEXT[x.signal];
      if (!t) return;
      switchNote(s, t, x.heard);
      callEvent(s, 'switch', `Persona switch · ${String(x.signal).replace('_', ' ')}`, `${t}${x.heard ? ` (heard “${x.heard}”)` : ''}`);
    },
    onEvent: kind => {
      if (kind === 'language_change') callEvent(s, 'language', 'Language switch', 'The agent follows the seller’s language');
      else if (kind === 'user_interrupt') addLog(s, { kind: 'call', title: 'Seller spoke over the agent', detail: 'barge-in: the agent stopped and listened' }, true);
      else if (kind === 'tool_call') addLog(s, { kind: 'call', title: 'Agent used a tool', detail: kind }, true);
    },
    onReply: ms => addLog(s, { kind: 'call', title: `Agent answered in ${(ms / 1000).toFixed(1)} s`, detail: 'from the seller’s last sound to the agent’s first sound' }, true),
    onEnd: () => { if (s.live?.kind === 'sarvam') { s.live = null; if (s.alive) { setState(s, 'ended'); callEvent(s, 'end', 'Call ended by the agent'); } } } });
  s.live = { kind: 'sarvam', stop: () => handle.stop() };
  const v = handle.session?.start?.agent_variables || {};
  const name = handle.session?.agent?.name || app;
  s.delivery = { ...s.delivery, language: { Hindi: 'hi-IN', English: 'en-IN' }[handle.session?.start?.initial_language_name] || s.delivery.language, tone: v.tone_directive || v.persona_headline };
  paintDelivery(s);
  s.agentStart = { app, name, version: handle.session?.agent?.version, variables: v, session: handle.session?.session_id };
  callEvent(s, 'agent', `Call started · ${name}`, `Sarvam Voice Agent ${app}${handle.session?.agent?.version ? ` v${handle.session.agent.version}` : ''} · ${Object.keys(v).length} seller variables sent: ${Object.keys(v).join(', ')}`, 'live_api');
  setState(s, 'listening');
}

// The built-in pipeline: the executive (bot) opens, then the microphone stays open — speak, pause, it answers.
async function startBuiltin(s) {
  s.call = await api('/api/v5/calls', { method: 'POST', body: { run_id: s.runId, seed_turns: [], opening: true }, signal: s.abort.signal });
  s.live = { kind: 'builtin', stop: async () => {} };
  s.lines = (s.call.transcript || []).map(t => ({ who: t.who === 'bot' ? 'agent' : 'you', text: t.text, meta: t.scripted ? 'opening line' : '' }));
  paintLines(s);
  const d = s.call.delivery || {};
  s.delivery = { language: d.language, pace: d.pace, warmth: d.warmth, formality: d.formality };
  paintDelivery(s);
  s.agentStart = { app: 'builtin', name: 'Payal · built-in', call: s.call.id, delivery: d };
  callEvent(s, 'agent', 'Call started · Payal built-in', `Sarvam LLM + Bulbul + Saaras · call ${s.call.id} · ${LANG[d.language] || d.language}, pace ${d.pace}, warmth ${d.warmth}`, 'llm');
  setState(s, 'speaking');
  const opening = [...(s.call.transcript || [])].reverse().find(t => t.who === 'bot');
  await listen(s);
  if (opening) say(s, opening).finally(() => { if (s.live?.kind === 'builtin' && s.state === 'speaking') setState(s, 'listening'); });
  else setState(s, 'listening');
}

async function listen(s) {
  const l = new RealtimeListener({ language: s.call?.delivery?.language || 'hi-IN', eng: s.eng,
    onPartial: text => {
      $('#w-partial').textContent = text ? `🎙 ${text}` : '';
      if (s.audio && !s.audio.paused && text.split(/\s+/).length >= 2 && !isEcho(text, s.lastBot)) s.audio.pause();   // barge-in
    },
    onFinal: text => { $('#w-partial').textContent = ''; if (isEcho(text, s.lastBot)) return; s.audio?.pause(); turn(s, text); },
    onState: st => { if (st === 'closed' && s.listener === l) s.listener = null; },
    onError: msg => toast(msg, 'error'),
    onCheck: stats => { s.micStats = stats; paintDebug(s); } });
  s.listener = l;
  await l.start();
}

async function turn(s, text) {
  if (!s.live || s.live.kind !== 'builtin' || !text.trim()) return;
  const live = s.live;   // a reply that lands after End or an agent change belongs to a call that no longer exists
  if (s.thinking) { s.pending = `${s.pending} ${text}`.trim(); return; }
  s.thinking = true;
  addLine(s, 'you', text);
  setState(s, 'thinking');
  try {
    const before = { ...s.delivery };
    const out = await api(`/api/v5/calls/${s.call.id}/turn`, { method: 'POST', body: { text, speak: speakOpts(s.eng) }, signal: s.abort.signal });
    if (s.live !== live) return;
    const seen = (s.call.transcript || []).length;
    s.call = out.call;
    (out.speech || []).forEach(x => { s.prestarted[x.turn] = x; });
    const applied = (s.call.switches || []).filter(w => w.applied);
    for (const w of applied.slice(s.applied)) {
      switchNote(s, w.reason, '');
      const changes = Object.entries(w.changes || {}).filter(([, v]) => Array.isArray(v)).map(([k, [a, b]]) => `${k.replace(/_/g, ' ')} ${a} → ${b}`).join(' · ');
      callEvent(s, 'switch', `Persona switch · ${String(w.signal || '').replace('_', ' ')}`, `${w.reason}${changes ? ` · ${changes}` : ''}`);
    }
    s.applied = applied.length;
    const d = s.call.delivery || {};
    s.delivery = { language: d.language, pace: d.pace, warmth: d.warmth, formality: d.formality };
    if (before.language && d.language && before.language !== d.language) callEvent(s, 'language', `Language switch · ${LANG[before.language] || before.language} → ${LANG[d.language] || d.language}`, 'The agent follows the seller');
    if ((before.warmth && before.warmth !== d.warmth) || (before.formality && before.formality !== d.formality)) callEvent(s, 'tone', 'Tone change', `warmth ${before.warmth} → ${d.warmth} · formality ${before.formality} → ${d.formality}`);
    paintDelivery(s);
    const fresh = (s.call.transcript || []).slice(seen + 1).filter(t => t.who === 'bot');   // +1: the seller line we already showed
    for (const t of fresh) {
      addLine(s, 'agent', t.text, t.ms ? `${fmtMs(t.ms)}` : '');
      addLog(s, { kind: 'llm', title: `Agent reply · ${t.source || 'sarvam'}`, detail: t.text.slice(0, 120), ms: t.ms }, true);
    }
    if (s.call.outcome?.label) { s.outcome = s.call.outcome.label.replace(/_/g, ' '); paintArch(s); }
    s.thinking = false;
    for (const t of fresh) { if (s.live !== live) break; await say(s, t); }
  } catch (err) { if (err.name !== 'AbortError' && s.alive) toast(err.message, 'error'); }
  finally {
    s.thinking = false;
    if (s.live === live) setState(s, 'listening');
    if (s.pending && s.live === live) { const next = s.pending; s.pending = ''; turn(s, next); }
  }
}

async function say(s, t) {
  if (!s.live || !s.call) return;
  const live = s.live;
  const d = s.call.delivery || {};
  try {
    setState(s, 'speaking');
    const pre = s.prestarted[t.i]; delete s.prestarted[t.i];
    const { audio, out } = await prestartedOr(pre, () => speakLine({ text: t.text.slice(0, 600), language: d.language || 'hi-IN', speaker: d.voice || 'ritu',
      pace: Number(d.pace) || 1, eng: s.eng, signal: s.abort.signal }));
    if (s.live !== live) return;
    s.audio?.pause(); s.audio = audio;
    s.lastBot = `${t.text} ${out?.spoken_text || ''}`;
    await playToEnd(audio).catch(() => toast('Tap once on the page — the browser blocked the audio.', 'error'));
  } catch (err) { if (err.name !== 'AbortError' && s.alive) toast(`Voice: ${err.message}`, 'error'); }
}

// Speakers can leak the agent's voice into the microphone; a "turn" that mostly repeats the agent's last line is ignored.
const words = x => String(x || '').toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s]/gu, ' ').split(/\s+/).filter(w => w.length > 1);
function isEcho(text, bot) {
  if (!bot) return false;
  const said = new Set(words(bot)), heard = words(text);
  return heard.length > 0 && heard.filter(w => said.has(w)).length / heard.length >= 0.6;
}

// ---------------------------------------------------------------- transcript and switch note
function addLine(s, who, text, meta = '') {
  if (!text) return;
  s.lines.push({ who, text, meta });
  paintLines(s);
}

function paintLines(s) {
  const el = $('#w-lines');
  if (!el) return;
  const name = (agentLine(s.cat, s.info, 'call') || 'Agent').split(/\s+[·(]/)[0];
  el.innerHTML = s.lines.length ? s.lines.map(l => `<div class="bubble ${l.who === 'agent' ? 'bot' : 'seller'}"><span class="who ${l.who === 'agent' ? 'bot' : 'seller'}">${esc(l.who === 'agent' ? name : 'You')}</span> ${esc(l.text)}${l.meta ? `<div class="meta"><span>${esc(l.meta)}</span></div>` : ''}</div>`).join('')
    : `<p class="dim">${s.state === 'connecting' ? 'Connecting… allow the microphone if the browser asks.' : 'No lines yet.'}</p>`;
  el.scrollTop = el.scrollHeight;
}

function switchNote(s, text, heard) {
  if (!text) return;
  $('#w-switch').innerHTML = `<div class="switch-banner flash" role="status"><span class="eyebrow">Persona switch</span><span>${esc(text)}</span>${heard ? `<span class="mono dim">heard “${esc(heard)}”</span>` : ''}</div>`;
  toast(`Persona switch: ${text}`, 'info', 3000);
}

// ---------------------------------------------------------------- meeting and debug (left pane, collapsed)
function meetingForm(s) {
  if (s.meeting) {
    const m = s.meeting;
    return `<div class="ws-card-body"><p><b>${esc(m.start_label)}</b> · ${esc(m.mode === 'online' ? 'Online' : 'In person')} · ${esc(m.location || '')}</p>
      <p style="display:flex;flex-wrap:wrap;gap:8px">${m.meeting_link ? `<a class="btn btn-sm btn-primary" href="${esc(m.meeting_link)}" target="_blank" rel="noopener">Join meeting ↗</a>` : ''}
      <a class="btn btn-sm" href="${BASE}${esc(m.ics_path)}">⬇ Calendar file</a><a class="btn btn-sm" href="${esc(m.google_calendar_link)}" target="_blank" rel="noopener">Google Calendar ↗</a></p></div>`;
  }
  const t = new Date(Date.now() + 86400000);
  return `<form id="w-meet-form" class="ws-card-body" style="display:grid;gap:8px">
    <label class="dim" style="font-size:12px">Mode <select name="mode"><option value="online">Online</option><option value="in_person">In person</option></select></label>
    <label class="dim" style="font-size:12px">Address (in person) <input name="address_text" maxlength="300" placeholder="The address the seller confirmed"></label>
    <label class="dim" style="font-size:12px">Date and time (IST) <input type="datetime-local" name="start" value="${t.toISOString().slice(0, 10)}T11:00" required></label>
    <button class="btn btn-primary btn-sm">Confirm test meeting</button>
    <p class="dim" style="font-size:11.5px">Creates a meeting link, a calendar file and a Google Calendar link. Nobody is invited.</p></form>`;
}

function paintDebug(s) {
  const el = $('#w-debug-body');
  if (!el || !s.info) return;
  const d = s.info, b = d.builtin || {}, acct = d.sarvam?.account || {}, m = s.micStats;
  const row = (k, v) => v ? `<dt>${esc(k)}</dt><dd>${v}</dd>` : '';
  el.innerHTML = `<dl class="ws-dl">
    ${row('Agent', esc(agentLine(s.cat, d, 'call')))}
    ${row('Default agent', esc(d.default_agent || 'builtin'))}
    ${row('Built-in models', esc([b.llm_model, b.tts_model, b.stt_realtime_model, b.stt_mode].filter(Boolean).join(' · ')))}
    ${row('Microphone', m ? `${m.frames ? `<b class="exp-ok">${esc(m.frames)} frames</b>` : '<b class="exp-bad">no audio frames</b>'} · peak ${esc(m.peak)} · engine ${esc(m.ctx)} · ${esc(m.track?.label || '')}` : '<span class="dim">appears 3 s after the built-in agent starts listening</span>')}
    ${d.operator ? row('Sarvam account', esc([acct.email, acct.org_id, acct.workspace_id].filter(Boolean).join(' · '))) : ''}
    ${row('Workspace agents', (d.workspace_agents || []).map(a => `${esc(a.name)} <span class="mono">${esc(a.app_id)}</span>${a.callable === false ? ' <span class="exp-bad">draft</span>' : ''}`).join('<br>'))}
  </dl>`;
}

// ---------------------------------------------------------------- events
function inspect(s, node, el, focus = '') {
  if (!node) return;
  openInspector({ runId: s.runId, node, focus: /^\/[^ ]*$/.test(focus || '') ? focus : '', events: s.events, returnFocus: el });
}

async function onClick(s, e) {
  const t = e.target.closest('button, [data-src]');
  if (!t) return;
  if (t.id === 'w-main') { if (s.live || s.starting) await endCall(s); else await startCall(s); return; }
  if (t.dataset.more) {
    const card = document.getElementById(t.dataset.more);
    const open = !card.classList.toggle('clamped');
    t.setAttribute('aria-expanded', String(open));
    t.textContent = open ? 'Show less' : `Show more · ${MORE[t.dataset.more] || ''}`;
    return;
  }
  if (t.dataset.src) { try { const src = JSON.parse(t.dataset.src); inspect(s, src.api, t, src.pointer); } catch { /* bad chip */ } return; }
  if (t.dataset.node) { inspect(s, t.dataset.node, t); return; }
  if (t.dataset.stage) {
    const pick = { trigger: e => e.node === 'trigger', cache: e => e.kind === 'cache', writes: e => e.node === 'elastic.persist', index: e => e.node === 'persona.index',
      agent: e => String(e.node || '').startsWith('call.'), outcome: e => e.node === 'call.meeting' || e.node === 'call.end' }[t.dataset.stage] || (() => false);
    const evs = s.events.filter(pick);
    const callLogs = ['agent', 'outcome'].includes(t.dataset.stage) ? s.logs.filter(l => l.kind === 'call').map(l => ({ title: l.title, detail: l.detail })) : [];
    openInspector({ runId: s.runId, node: t.title, title: t.title, returnFocus: t, extra: { node: t.title, result: { label: t.title, status: 'ok',
      origin: ['cache', 'writes', 'index'].includes(t.dataset.stage) ? 'elastic' : 'internal', note: `${evs.length + callLogs.length} events`,
      response: t.dataset.stage === 'agent' && s.agentStart ? { agent: s.agentStart, events: callLogs } : evs.length ? evs : callLogs }, events: [] } });
    return;
  }
  if (t.dataset.log != null) {
    const entry = s.logs[Number(t.dataset.log)];
    if (!entry) return;
    if (entry.node) { inspect(s, entry.node, t); return; }
    // Not an API: show what the entry holds — the agent's start variables, the Elasticsearch write, the call event.
    const data = entry.title.startsWith('Call started') && s.agentStart ? s.agentStart : entry.data || entry.ev || { title: entry.title, detail: entry.detail };
    openInspector({ runId: s.runId, node: entry.title, title: entry.title, returnFocus: t,
      extra: { node: entry.title, result: { label: entry.title, status: entry.status || 'ok', origin: entry.kind === 'es' ? 'elastic' : entry.origin || 'internal', note: entry.detail, response: data }, events: [] } });
    return;
  }
  if (t.hasAttribute('data-record') && s.brief) openRecord(s.brief, t, (src, el) => inspect(s, src.api, el, src.pointer));
}

async function onSubmit(s, e) {
  e.preventDefault();
  if (e.target.id === 'w-type') {
    const input = $('#w-input');
    const text = input.value.trim();
    input.value = '';
    if (text) { s.audio?.pause(); turn(s, text); }
    return;
  }
  if (e.target.id === 'w-meet-form') {
    const f = new FormData(e.target);
    try {
      s.meeting = await api('/api/v5/meetings', { method: 'POST', body: { run_id: s.runId, call_id: s.call?.id || null, mode: f.get('mode'),
        address_kind: f.get('mode') === 'in_person' && f.get('address_text') ? '' : null, address_text: f.get('address_text') || null,
        start: String(f.get('start')), note: '' } });
      $('#w-meeting').innerHTML = `<summary><h3 class="ws-card-h">Book a meeting</h3></summary>${meetingForm(s)}`;
      $('#w-meeting').open = true;
      s.outcome = `Meeting ${s.meeting.start_label}`;
      callEvent(s, 'meeting', 'Meeting booked', `${s.meeting.start_label} · ${s.meeting.mode === 'online' ? 'online' : 'in person'}`);
      paintArch(s);
      toast('Test meeting booked');
    } catch (err) { toast(err.message, 'error'); }
  }
}
