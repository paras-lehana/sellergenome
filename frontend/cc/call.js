// The call workspace (/call/{run}): barge-in console → persona · call · architecture.
import { api, session, streamRun, BASE } from './api.js';
import { $, $$, busy, esc, fmtMs, genomeStrip, initials, mockTag, origin, pretty, reducedMotion, signalChip, titleCase, toast } from './ui.js';
import { agentBarHtml, agentBlocked, agentInfo, agentLine, chooseAgent, chosenApp, currentAgent } from './agent.js';
import { createGraph, SOURCES } from './graph.js';
import { closeInspector, openInspector } from './inspector.js';
import { basicsHtml, buyersHtml, engagementHtml, fullBriefHtml, openRecord, planHtml, qualityHtml } from './brief.js';
import { bindPicker, catalogue, effective, pickerHtml } from './engines.js';
import { playToEnd, prestartedOr, RealtimeListener, recordClip, speakLine as speakVia, speakOpts, speechMeta, trace, transcribe } from './voice.js';

const FACTOR_GROUPS = [
  ['Voice & language', ['language', 'hinglish_mix', 'voice', 'pace', 'sentence_length']],
  ['Tone', ['formality', 'warmth', 'empathy']],
  ['Conversation plan', ['opening', 'personalization', 'objection_playbook', 'escalation', 'next_action']],
  ['What we read', ['primary_objection', 'buying_stage', 'best_call_window']],
];
const LANGS = { 'hi-IN': 'Hindi', 'en-IN': 'English', 'te-IN': 'Telugu', 'ta-IN': 'Tamil', 'mr-IN': 'Marathi', 'gu-IN': 'Gujarati', 'bn-IN': 'Bengali', 'pa-IN': 'Punjabi', 'kn-IN': 'Kannada', 'ml-IN': 'Malayalam', 'od-IN': 'Odia' };
const STAGES = [['greeting', 'Greeting'], ['qualifying', 'Qualifying'], ['package', 'Package'], ['meeting', 'Meeting'], ['wrap_up', 'Wrap-up']];
// The business flow (docs/business/business-flow.md) — the side bar shows where this call is in it.
const FLOW = [
  { k: 'activity', l: 'Seller activity', nodes: ['trigger'] },
  { k: 'window', l: '~15 min window', nodes: ['trigger', 'orchestrator'] },
  { k: 'context', l: 'Context fetch', nodes: ['orchestrator', 'gateway', 'elastic', ...SOURCES] },
  { k: 'persona', l: 'Persona', nodes: ['contract', 'sarvam.aggregate', 'sarvam.locality', 'persona.index'] },
  { k: 'call', l: 'Bot call', nodes: ['teleprompter', 'voice.plugin'] },
  { k: 'meeting', l: 'Meeting fixed', nodes: ['teleprompter'] },
  { k: 'handoff', l: 'Human executive', nodes: [] },
];
const ARCH_KEY = 'sg-arch-bar';
const VOICE_KEY = 'sg-call-voice';
const voicePref = () => { try { return localStorage.getItem(VOICE_KEY) !== 'off'; } catch { return true; } };
// Engine kinds this place uses (Admin defaults, overridable here; decision D25).
const ENGINE_KINDS = ['bot', 'tts_model', 'tts_voice', 'tts_transport', 'listen', 'stt_realtime_model', 'stt_model', 'stt_mode', 'translate_model'];
const FEMALE_VOICE = /^(ritu|priya|kavya|ishita|simran|sanchita|shalini|roopa|aparna|shreya|suhani|aarti|neha|pooja|tanya|shruti)/;
const PLACEHOLDER = 'Take over as the seller — try “jaldi boliye, time nahi hai” or “Hindi mein boliye”';
const archOpen = () => { try { return localStorage.getItem(ARCH_KEY) !== 'closed'; } catch { return true; } };

export async function mount(root, { params, navigate }) {
  const runId = params[0];
  // Talk as Seller (5.11.0): the simple one-agent call page; Barge in and ?view=full keep this workspace.
  const view = new URLSearchParams(location.search).get('view');
  let talkFlag = false;
  try { talkFlag = sessionStorage.getItem(`sg-talk-${runId}`) === '1'; } catch { /* storage blocked */ }
  if (view === 'talk' || (talkFlag && view !== 'full')) return (await import('./talk.js')).mount(root, { params, navigate });
  const s = { runId, run: null, events: [], call: null, auto: false, autoTimer: 0, stop: null, navigate, busy: false, alive: true, pane: 'call', audio: null, recorder: null, abort: new AbortController(),
    voice: voicePref(), spoken: 0, speaking: false, voiceQueue: Promise.resolve(), cat: null, eng: null, listener: null, payal: null,
    payalLines: [], payalEvents: {}, payalState: 'idle', pendingText: '', prestarted: {}, replyTimes: {}, saidAt: 0,
    debugOpen: (() => { try { return localStorage.getItem('sg-debug') === '1'; } catch { return false; } })() };
  root.innerHTML = `
    <div class="call">
      <header class="call-head" id="call-head"><div class="ident"><div class="skeleton" style="width:240px;height:22px"></div></div></header>
      <div id="debug-panel" hidden></div>
      <div>
        <div class="pane-tabs tabs" role="tablist" aria-label="Workspace panes">
          <button class="tab" role="tab" data-pane="persona" aria-selected="false">Persona</button>
          <button class="tab" role="tab" data-pane="call" aria-selected="true">Call</button>
          <button class="tab" role="tab" data-pane="arch" aria-selected="false">Architecture</button>
        </div>
        <div class="panes" style="position:relative;height:calc(100dvh - var(--topbar) - 64px)">
          <section class="pane" id="p-persona" aria-label="Persona"><div class="skeleton" style="height:140px"></div><div class="skeleton" style="height:240px"></div></section>
          <section class="pane active" id="p-call" aria-label="Call"><div class="skeleton" style="height:160px"></div><div class="skeleton" style="height:280px"></div></section>
          <section class="pane archbar" id="p-arch" aria-label="Architecture">
            <div class="archbar-head"><h2 class="pane-title">Architecture · this run ${origin('internal', 'Actual events')}</h2>
              <button class="icon-btn arch-toggle" data-arch="hide" aria-label="Hide the architecture bar" aria-controls="p-arch" title="Hide (])"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button></div>
            <ol class="flow" id="flow" aria-label="Where this call is in the business flow"></ol>
            <p class="flow-now" id="flow-now" aria-live="polite"></p>
            <div id="graph"></div><div id="gw-line"></div><div id="run-stats"></div></section>
          <button class="arch-rail" data-arch="show" aria-controls="p-arch" aria-label="Show the architecture bar" title="Show architecture (])"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg><span>Architecture</span><i id="rail-stage"></i></button>
          <section class="pm-full" id="brief-view" hidden aria-label="Pre-call brief"></section>
          <div class="barge" id="barge" role="dialog" aria-modal="false" aria-labelledby="barge-title">
            <div class="barge-box">
              <div class="barge-head"><span class="onair">BARGING IN</span><h2 id="barge-title">Preparing context…</h2><span class="aside mono dim" id="barge-clock" style="margin-left:auto">0.00 s</span></div>
              <pre class="barge-log" id="barge-log" aria-live="polite"></pre>
              <div class="barge-foot"><div class="bar"><i id="barge-bar" style="width:4%"></i></div><span class="mono dim" id="barge-count">0 steps</span><button class="btn btn-sm btn-primary" id="enter" disabled>Enter the call →</button></div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
  const graph = createGraph($('#graph'), { onOpen: (node, st, el) => openNode(s, node, el) });
  s.graph = graph;
  root.addEventListener('click', e => onClick(s, e));
  root.addEventListener('change', e => { const a = e.target.closest('[data-agent-select]'); if (a) switchAgent(s, a.value); });
  root.addEventListener('submit', e => onSubmit(s, e));
  root.addEventListener('change', e => onChange(s, e));
  s.unbindEng = bindPicker(root, 'call', () => { s.eng = effective(s.cat, 'call'); if (s.call) renderCall(s); if (s.run) head(s); });
  agentInfo().then(d => { s.agentInfo = d; if (s.alive && s.run) head(s); }).catch(() => {});
  s.catReady = catalogue().then(cat => { s.cat = cat; s.eng = effective(cat, 'call'); return cat; }).catch(() => null);
  s.wide = window.matchMedia('(min-width: 1181px)');
  setArch(s, archOpen());
  s.onKey = e => { if (e.key === ']' && !e.target.closest('input, textarea, select, [contenteditable]')) setArch(s, !s.archOpen, true); };
  document.addEventListener('keydown', s.onKey);
  s.archReady = api('/api/v5/architecture', { signal: s.abort.signal }).then(arch => { s.arch = arch; return arch; }).catch(() => null);
  renderFlow(s);

  let run;
  try { run = await api(`/api/v5/runs/${encodeURIComponent(runId)}`); }
  catch (err) { root.innerHTML = `<div class="page"><h1>Call not available</h1><p class="muted">${esc(err.message)}</p><p style="margin-top:12px"><a class="btn" href="${BASE}/" data-nav>Back to the floor</a></p></div>`; return {}; }
  s.run = run;
  s.archReady.then(arch => { if (arch && s.alive) { graph.plan(arch, { synthetic: run.seller?.synthetic !== false }); gatewayLine(s); } });
  head(s);
  const started = performance.now();
  const clockTimer = setInterval(() => { if ($('#barge-clock')) $('#barge-clock').textContent = `${((performance.now() - started) / 1000).toFixed(2)} s`; }, 50);
  if (run.status === 'running') {
    s.stop = streamRun(runId, ev => onEvent(s, ev), () => finish(s, clockTimer));
  } else {
    (run.events || []).forEach(ev => onEvent(s, ev, true));
    finish(s, clockTimer, true);
  }
  return {
    destroy() { busy('run', false); busy('nav', false); s.alive = false; s.abort.abort(); s.stop?.(); clearInterval(clockTimer); clearTimeout(s.autoTimer); s.audio?.pause(); s.recorder?.stream?.getTracks().forEach(t => t.stop()); s.listener?.stop(); s.payal?.stop(); s.unbindEng?.(); document.removeEventListener('keydown', s.onKey); closeInspector(); },
  };
}

// ---------------------------------------------------------------- barge-in console
const LOG_ORIGIN = o => o ? `<span class="o-col">${origin(o === 'internal' ? 'internal' : o)}</span>` : '<span class="o-col"></span>';

function onEvent(s, ev, quiet = false) {
  s.events.push(ev);
  s.graph.update(ev);
  if (ev.node === 'identity' && ev.status === 'ok' && s.run) s.identityDetail = ev.detail;
  renderFlow(s);
  if (quiet) return;
  const log = $('#barge-log');
  if (!log) return;
  if (ev.kind === 'start' && !['trigger', 'orchestrator'].includes(ev.node) && !ev.node.startsWith('sarvam')) return; // keep the console readable: show results
  if (ev.node === 'elastic.persist') {
    if (ev.status === 'ok' && s.persistShown) return; // the graph's Elastic node keeps the full count
    if (ev.status === 'ok') s.persistShown = true;
  }
  const line = document.createElement('div');
  line.className = 'ln';
  const t = (ev.t_ms / 1000).toFixed(3).padStart(6, ' ');
  const mark = ev.status === 'error' || ev.status === 'fallback' ? '✕' : ev.kind === 'skip' ? '○' : ev.kind === 'cache' ? (ev.status === 'hit' ? '⚡' : '·') : '▸';
  line.innerHTML = `<span class="t">${t}</span>${LOG_ORIGIN(ev.kind === 'cache' ? (ev.status === 'hit' ? 'elastic_cache' : 'elastic') : ev.origin)}<span class="lab">${mark} ${esc(ev.label)}</span><span class="ms">${ev.ms != null ? esc(fmtMs(ev.ms)) : ''}</span><span class="det">${esc(ev.detail)}</span>`;
  log.append(line);
  log.scrollTop = log.scrollHeight;
  const done = s.events.filter(e => e.kind !== 'start').length;
  $('#barge-count').textContent = `${done} steps`;
  $('#barge-bar').style.width = `${Math.min(96, 4 + done * 2.6)}%`;
  if (ev.node === 'facts') $('#barge-title').textContent = 'Initial facts ready — building the persona…';
  if (ev.node === 'sarvam.aggregate' && ev.kind === 'start') $('#barge-title').textContent = 'Sarvam is reading the evidence…';
}

async function finish(s, clockTimer, immediate = false) {
  clearInterval(clockTimer);
  try { s.run = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}`); }
  catch (err) { toast(err.message, 'error'); return; }
  if (!s.alive) return;
  (s.run.events || []).filter(e => !s.events.some(x => x.seq === e.seq)).forEach(e => onEvent(s, e, true));
  const ok = s.run.status === 'completed' || s.run.status === 'degraded';
  $('#barge-title').textContent = ok ? `Context ready in ${fmtMs(s.run.duration_ms)} — you are on the call` : `Preparation ${s.run.status}: ${s.run.error || ''}`;
  $('#barge-bar').style.width = '100%';
  const enter = $('#enter');
  enter.disabled = !ok;
  head(s);
  renderStats(s);
  gatewayLine(s);
  renderFlow(s);
  s.graph.settle();
  if (!ok) return;
  try { s.brief = await api(`/api/v5/runs/${encodeURIComponent(s.runId)}/brief`, { signal: s.abort.signal }); }
  catch { s.brief = null; }  // the workspace still works without the PM layout
  renderPersona(s);
  await s.catReady;
  await startCall(s);
  const close = () => { const b = $('#barge'); if (b) b.remove(); };
  enter.onclick = close;
  if (immediate) close(); else setTimeout(close, reducedMotion() ? 0 : 1400);
}

// ---------------------------------------------------------------- architecture side bar
function setArch(s, open, remember = false) {
  s.archOpen = open;
  const panes = $('.panes');
  panes?.classList.toggle('arch-collapsed', !open);
  $('#p-arch')?.toggleAttribute('data-collapsed', !open);
  $$('[data-arch]').forEach(b => b.setAttribute('aria-expanded', String(open)));
  const barge = $('#barge');
  if (barge) barge.style.inset = s.wide?.matches ? (open ? '0 var(--arch-w) 0 0' : '0 var(--arch-rail) 0 0') : '0';
  if (remember) { try { localStorage.setItem(ARCH_KEY, open ? 'open' : 'closed'); } catch { /* storage blocked: still works */ } }
  if (open && remember) $('#p-arch .arch-toggle')?.focus({ preventScroll: true }); else if (remember) $('.arch-rail')?.focus({ preventScroll: true });
}

// Which business-flow stage is this call in? Derived from real run events and the live call state.
function stageOf(s) {
  const ev = s.events;
  const has = (node, kinds) => ev.some(e => e.node === node && (!kinds || kinds.includes(e.kind)));
  if (s.meeting) return { i: 6, now: `Meeting booked for ${s.meeting.start_label} — handoff note ready for the executive` };
  if (s.call) {
    const st = STAGES.find(([k]) => k === s.call.stage)?.[1] || s.call.stage;
    if (s.call.stage === 'meeting') return { i: 5, now: `On the call · ${st} — the seller is choosing a slot` };
    return { i: 4, now: `On the call · ${st}${s.call.switches?.length ? ` · ${s.call.switches.length} persona switches` : ''}` };
  }
  if (has('run', ['done'])) return { i: 4, now: 'Context ready — the bot is about to speak' };
  if (has('sarvam.aggregate') || has('facts')) {
    const ag = ev.find(e => e.node === 'sarvam.aggregate' && e.kind === 'start');
    return { i: 3, now: ag ? `Sarvam is reading the evidence · ${ag.detail.split('·')[1]?.trim() || ''}` : 'Initial facts ready — assembling the persona' };
  }
  if (has('orchestrator')) {
    const done = ev.filter(e => ['response', 'error', 'skip'].includes(e.kind) && SOURCES.some(x => e.node === x || e.node?.startsWith(x + ':'))).length;
    return { i: 2, now: `Fetching context · ${done} sources answered${s.arch?.gateway?.enabled ? ' · IndiaMART APIs via the gateway' : ''}` };
  }
  return { i: 1, now: 'Inside the exploration window — barging in' };
}

function renderFlow(s) {
  const el = $('#flow');
  if (!el) return;
  const { i, now } = stageOf(s);
  const real = s.run?.seller && s.run.seller.synthetic === false;
  const sub = { activity: real ? 'operator search' : 'simulated', window: real ? 'skipped' : '~15 min', handoff: s.meeting ? 'note ready' : '' };
  el.innerHTML = FLOW.map((f, n) => `<li class="${n < i ? 'done' : n === i ? 'here' : ''}" ${n === i ? 'aria-current="step"' : ''}><button type="button" data-flow="${n}" title="Highlight this stage in the diagram"><span class="dot" aria-hidden="true"></span><span class="lbl">${esc(f.l)}</span>${sub[f.k] ? `<small>${esc(sub[f.k])}</small>` : ''}</button></li>`).join('');
  $('#flow-now').innerHTML = `<span class="chip solid">You are here</span> ${esc(now)}`;
  const rail = $('#rail-stage');
  if (rail) rail.textContent = FLOW[i].l;
  if (s.flowFocus == null) s.graph.focus(FLOW[i].nodes);
}

function gatewayLine(s) {
  const el = $('#gw-line');
  if (!el || !s.arch) return;
  const gw = s.arch.gateway || {};
  const routed = s.arch.items.filter(i => i.via_gateway).length;
  const synthetic = s.run?.seller?.synthetic !== false;
  el.innerHTML = `<p class="gw-line">${gw.enabled ? origin('live_api', 'Gateway on') : origin('off', 'Gateway off')}
    <span>${gw.enabled ? `${routed} IndiaMART APIs go through the pass-through gateway${session.operator && gw.base && gw.base !== 'configured' ? ` <span class="mono">${esc(gw.base)}</span>` : ''}.` : 'IndiaMART APIs are called directly.'}
    ${synthetic ? ' This is a synthetic seller, so nothing is sent upstream — mocks answer in the real shapes.' : gw.glid_access === 'prod' ? ' Live for any GLID (prod mode).' : ' Live only for approved test GLIDs (dev mode).'}</span></p>`;
}

// ---------------------------------------------------------------- agent switch
// Switch the agent during the call (owner, 2026-10-10: "switch seamlessly during talk to seller"): whatever was live stops
// and, when the person was talking, the new agent's voice starts straight away — the click is the user gesture.
async function switchAgent(s, id) {
  if (currentAgent(s.cat, s.agentInfo, 'call') === id) return;
  const wasLive = !!(s.listener || s.payal);
  if (s.payal) { const live = s.payal; s.payal = null; await live.stop().catch(() => {}); }
  if (s.listener) { s.listener.stop(); s.listener = null; }
  s.audio?.pause();
  chooseAgent('call', id);
  s.eng = effective(s.cat, 'call');
  if (s.call) renderCall(s);  // the call may not have started yet (WebKit QA, 5.10.1)
  head(s); paintDebug(s);
  toast(`Agent: ${agentLine(s.cat, s.agentInfo, 'call')}`);
  if (wasLive) (id !== 'builtin' ? $('[data-act="payal"]') : $('[data-act="mic"]'))?.click();
}

// ---------------------------------------------------------------- debug panel
// Owner, 2026-10-10: "i also want details on the screen which agent id is being used, which account on sarvam, its
// name. debugging is getting difficult for me". Everything a voice call depends on, in one place.
async function paintDebug(s, refetch = false) {
  const el = $('#debug-panel');
  if (!el) return;
  el.hidden = !s.debugOpen;
  if (!s.debugOpen) return;
  if (refetch || !s.debug) {
    el.innerHTML = '<div class="skeleton" style="height:120px"></div>';
    try { s.debug = await api('/api/v5/debug/voice'); } catch (err) { el.innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  }
  const d = s.debug, b = d.builtin || {}, acct = d.sarvam?.account || {}, m = s.micStats;
  const row = (k, v) => v == null || v === '' ? '' : `<dt>${esc(k)}</dt><dd>${v}</dd>`;
  const code = v => v == null || v === '' ? null : `<span class="mono">${esc(String(v))}</span>`;
  const when = iso => iso ? esc(new Date(iso).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' })) + ' IST' : null;
  const agent = (title, a) => `<section><h4>${esc(title)}</h4><dl>${row('Agent', esc(a.name || '—'))}${row('Agent id', code(a.app_id))}${row('Version', code(a.version))}
    ${row('Voice', esc(a.voice || ''))}${row('Built', when(a.built_at))}${row('Live calls', a.live_available ? 'available' : esc(a.live_reason || 'not available'))}</dl></section>`;
  const synthetic = s.run?.seller?.synthetic !== false;
  const micLine = !m ? '<span class="dim">Press Talk live — numbers appear 3 s after listening starts.</span>'
    : `${m.frames ? `<b class="exp-ok">${esc(m.frames)} audio frames sent</b>` : '<b class="exp-bad">0 audio frames — the browser is not delivering microphone audio</b>'}`
      + ` · level peak ${esc(m.peak)}${m.peak < 3 && m.frames ? ' <b class="exp-bad">(silence)</b>' : ''} · audio engine ${esc(m.ctx || '?')} · ${esc(m.rate || '?')} Hz`;
  el.innerHTML = `<div class="card debug-card"><div class="card-head"><h3>Debug · voice and agents</h3><span class="aside"><span class="dim mono" style="font-size:11px">v${esc(d.version)} · run ${esc(s.runId)}</span> <button class="btn btn-sm btn-ghost" data-act="debug-refresh">Refresh</button></span></div>
    <div class="card-body debug-grid">
      <section><h4>This call · built-in ${esc(d.builtin?.bot === 'builtin' ? 'Payal (SellerGenome pipeline)' : b.bot || '')}</h4><dl>
        ${row('LLM', `${code(b.llm_model)} via ${esc(b.llm_route)} · reasoning ${esc(b.llm_reasoning || '—')}`)}
        ${row('Speech (TTS)', `${code(b.tts_model)} · voice ${esc(b.tts_voice)} · ${esc(b.tts_transport)}`)}
        ${row('Listening', `${esc(b.listen)} · ${code(m?.model || b.stt_realtime_model)} · mode ${esc(m?.mode || b.stt_mode)} · language ${esc(m?.language || '—')}`)}
        ${row('End of turn', `${esc(b.vad?.silence_duration_ms)} ms silence · threshold ${esc(b.vad?.threshold)} · cap ${esc(b.vad?.max_seconds)} s`)}
        ${row('Microphone', micLine)}
        ${m?.track ? row('Mic device', `${esc(m.track.label || '—')} · ${m.track.muted ? '<b class="exp-bad">muted</b>' : esc(m.track.state)}`) : ''}
      </dl></section>
      ${agent('Payal on Sarvam (seller calls)', d.payal || {})}
      ${agent('VANI on Sarvam (buyer calls)', d.vani || {})}
      <section><h4>Sarvam account</h4><dl>
        ${d.operator ? row('Signed in as', esc(acct.email || '—')) : row('Account', '<span class="dim">sign in to see</span>')}
        ${row('Organisation', code(acct.org_id || d.sarvam?.org_id))}${row('Workspace', code(acct.workspace_id || d.sarvam?.workspace_id))}
        ${d.operator ? row('MCP sign-in', `${d.sarvam?.mcp_connected ? 'connected' : '<b class="exp-bad">not connected</b>'} · expires ${when(d.sarvam?.mcp_expires_at) || '—'}`) : ''}
        ${Object.entries(d.keys || {}).map(([n, k]) => row(n, k.configured ? `${esc(k.source)} · <span class="mono">${esc(k.fingerprint)}</span>` : '<b class="exp-bad">not configured</b>')).join('')}
        ${row('This seller', synthetic ? 'synthetic — any agent can call it' : 'real seller — any agent can call it with this run’s real data')}
        ${row('Chosen agent', esc(agentLine(s.cat, d, 'call')))}
        ${(d.workspace_agents || []).length ? row(`Workspace agents (${d.workspace_agents.length})`, d.workspace_agents.map(a => `${esc(a.name)} <span class="mono">${esc(a.app_id)}</span>`).join('<br>')) : ''}
      </dl></section>
    </div></div>`;
}

// ---------------------------------------------------------------- header
function head(s) {
  const r = s.run, seller = r.seller, p = r.persona;
  const call = s.call;
  // Running bar at the top while the run prepares (owner, 2026-10-10); the floor's navigation job hands over to it here.
  busy('nav', false);
  busy('run', r.status === 'running', 'Preparing the call · calling IndiaMART APIs and building the persona…');
  $('#call-head').innerHTML = `
    <div class="avatar" aria-hidden="true">${esc(initials(seller.company_name))}</div>
    <div class="ident"><h1>${esc(seller.company_name)}</h1><span>${[seller.contact_name, seller.city, `GLID ${seller.glid}`].filter(Boolean).map(esc).join(' · ')} · ${seller.synthetic === false ? (seller.identity === 'confirmed' ? 'real seller · identity from live IndiaMART APIs' : 'real seller · identity pending') : 'synthetic seller'} · run <span class="mono">${esc(r.id)}</span></span></div>
    ${p ? genomeStrip([{ k: 'archetype', v: p.archetype }, { k: 'language', v: (call?.delivery || {}).language || p.factors.language?.value }, { k: 'pickup', v: 0.6 }]).replace('class="genome"', 'class="genome" style="width:120px"') : ''}
    <span class="onair">${r.status === 'running' ? 'PREPARING' : 'ON CALL'}</span>
    <div class="actions">
      <button class="btn btn-sm ${s.listener ? 'live' : ''}" data-act="mic" aria-pressed="${!!s.listener}" ${r.status === 'running' ? 'disabled' : ''}>${s.listener ? '■ Stop listening' : s.eng?.listen === 'realtime' ? '🎙 Talk live' : '🎙 Speak as seller'}</button>
      <button class="btn btn-sm" data-act="meeting">📅 Book meeting</button>
      <button class="btn btn-sm" data-act="trace">⌬ Full trace</button>
      <button class="btn btn-sm" data-act="debug" aria-pressed="${!!s.debugOpen}" aria-controls="debug-panel" title="Which agents, models, Sarvam account and microphone this call uses">🛠 Debug</button>
      <button class="btn btn-sm" data-act="brief" ${r.status === 'running' ? 'disabled' : ''}>📋 Pre-call brief</button>
      <a class="btn btn-sm" href="${BASE}/persona/${encodeURIComponent(s.runId)}" data-nav>👤 Persona page</a>
      <button class="btn btn-sm arch-head-toggle" data-act="arch" aria-controls="p-arch">▥ Architecture</button>
      <a class="btn btn-sm btn-ghost" href="${BASE}/" data-nav>Leave call</a>
    </div>
    ${s.cat && s.agentInfo ? agentBarHtml(s.cat, s.agentInfo, { place: 'call' }) : ''}`;
  if (s.debugOpen && !s.debugPainted) { s.debugPainted = true; paintDebug(s, true); }
}

// ---------------------------------------------------------------- persona pane
function renderPersona(s) {
  const r = s.run, p = r.persona, c = r.contract;
  const f = p.factors || {};
  const tag = t => `<button class="tag" data-sources='${esc(JSON.stringify(t.sources || []))}' data-title="${esc(t.tag)}" title="${esc(t.reason)}"><span>${esc(t.tag)}</span><span class="bar"><i style="width:${t.score}%"></i></span><span class="score">${t.score}</span></button>`;
  const factor = (name) => {
    const x = f[name]; if (!x) return '';
    return `<details class="factor" data-factor="${esc(name)}"><summary>
      <span class="fname">${esc(titleCase(name))}</span>
      <span class="fval">${esc(pretty(x.value))}</span>
      <span class="fside"><span class="strength ${esc(x.strength)}">${esc(x.strength)}</span>${origin(x.origin === 'operator' ? 'operator' : p.origin === 'llm' ? 'llm' : 'default', x.origin === 'operator' ? 'PM edit' : p.origin === 'llm' ? 'Sarvam' : 'Rules')}</span>
      <span class="fbar"><span class="bar"><i style="width:${x.score}%"></i></span><span class="score">${x.score}</span></span></summary>
      <div class="factor-body"><p>${esc(x.reason || 'No reason given.')}</p>${sourcesHtml(x.sources)}${session.operator ? editForm(name, x) : '<p class="dim" style="font-size:11.5px">Sign in to suggest or apply a change — edits are written to the Elasticsearch persona index with history.</p>'}</div></details>`;
  };
  $('#p-persona').innerHTML = `
    <h2 class="pane-title">Persona ${origin(p.origin === 'llm' ? 'llm' : 'default', p.origin === 'llm' ? `Sarvam · ${fmtMs(p.ms)}` : 'Rules fallback')}</h2>
    <div class="summary-card"><p class="eyebrow">${esc(titleCase(p.archetype))}</p><p>${esc(p.summary)}</p>
      ${p.teleprompter?.confirm_first?.length ? `<div class="note" style="font-size:12.5px"><b>Confirm first:</b> ${p.teleprompter.confirm_first.map(esc).join(' · ')}</div>` : ''}</div>
    ${s.brief ? `<div class="pm-stack">${basicsHtml(s.brief, { compact: true })}${engagementHtml(s.brief, { compact: true })}${buyersHtml(s.brief)}${qualityHtml(s.brief)}</div>
      <p class="group-title pm-engine">Persona engine · Sarvam factors, tags and every source</p>` : ''}
    <div><p class="group-title">Tags · score · click for sources</p><div class="tags">${(p.tags || []).map(tag).join('') || '<span class="dim">No tags.</span>'}</div></div>
    ${FACTOR_GROUPS.map(([title, names]) => `<div style="display:grid;gap:6px"><p class="group-title">${esc(title)}</p>${names.map(factor).join('')}</div>`).join('')}
    ${p.novel_factors?.length ? `<div style="display:grid;gap:6px"><p class="group-title">Discovered by Sarvam · indexed as values</p>${p.novel_factors.map(n => `
      <details class="factor"><summary><span class="fname mono">${esc(n.name)}</span><span class="fval">${esc(n.value)}</span><span class="fside">${origin('llm', 'Novel')}</span><span class="fbar"><span class="bar"><i style="width:${n.score}%"></i></span><span class="score">${n.score}</span></span></summary>
      <div class="factor-body"><p>${esc(n.reason)}</p>${sourcesHtml(n.sources)}<p class="dim" style="font-size:11.5px">Stored as <code>factor="${esc(n.name)}"</code>, <code>value_${esc(n.value_type)}</code> — no new Elasticsearch mapping.</p></div></details>`).join('')}</div>` : ''}
    <details class="card" ${c.doubts?.length ? 'open' : ''}><summary class="card-head"><h3>Insights &amp; doubts</h3><span class="aside">${mockTag(c.insights[0]?.origin)}</span></summary><div class="card-body" style="display:grid;gap:8px;font-size:12.5px">
      ${c.doubts.map(d => `<div class="note"><b>Doubt:</b> ${esc(d.doubt)} <button class="btn btn-sm btn-ghost" data-src='${esc(JSON.stringify({ api: d.api, pointer: d.pointer }))}'>source</button></div>`).join('')}
      ${c.insights.map(i => `<div><b>${esc(titleCase(i.category))}</b> — ${esc(i.headline)} <button class="btn btn-sm btn-ghost" data-src='${esc(JSON.stringify({ api: i.api, pointer: i.pointer }))}'>source</button></div>`).join('')}
    </div></details>
    <details class="card"><summary class="card-head"><h3>Data received · ${c.completeness.present}/${c.completeness.total} fields</h3><span class="aside chip">${esc(c.classification)} seller</span></summary>
      <div class="card-body" style="display:grid;gap:10px">
        <div class="scroll-x"><table class="table"><thead><tr><th>Field</th><th>Value</th><th>Source</th></tr></thead><tbody>${Object.values(c.fields).filter(x => x.availability === 'present').map(x => `
          <tr><td class="mono">${esc(x.field)}</td><td>${x.availability === 'present' ? esc(String(typeof x.value === 'object' ? JSON.stringify(x.value) : x.value).slice(0, 120)) : `<span class="dim">${esc(x.availability)}</span>`}</td>
          <td>${x.api ? `<button class="btn btn-sm btn-ghost" style="padding:0 4px" data-src='${esc(JSON.stringify({ api: x.api, pointer: x.pointer }))}' title="${esc(x.api)} ${esc(x.pointer || '')}">${mockTag(x.origin) || 'source'}</button>` : mockTag(x.origin)}</td></tr>`).join('')}</tbody></table></div>
      </div></details>
    ${evidenceCards(c)}`;
}

// ---------------------------------------------------------------- evidence (every row keeps its source)
const srcBtn = x => x?.api ? `<button class="btn btn-sm btn-ghost src-btn" data-src='${esc(JSON.stringify({ api: x.api, pointer: x.pointer }))}' title="${esc(x.api)} ${esc(x.pointer || '')}">source</button>` : '';
const when = at => at ? `<span class="mono dim ev-at">${esc(String(at).replace('T', ' ').slice(0, 16))}</span>` : '';
const evCard = (title, n, body, open = false, aside = '') => n ? `<details class="card ev-card" ${open ? 'open' : ''}><summary class="card-head"><h3>${title} · ${n}</h3>${aside ? `<span class="aside">${aside}</span>` : ''}</summary><div class="card-body ev-list">${body}</div></details>` : '';

function evidenceCards(c) {
  const channel = { enquiry: 'Enquiry', buylead: 'Buy lead', pns: 'PNS call' };
  const buyers = (c.buyers || []).map(b => `<div class="ev-row">${mockTag(b.origin)}<span class="chip">${esc(channel[b.channel] || b.channel)}</span><span class="ev-main"><b>${esc(b.product || '—')}</b>${b.quantity ? ` · ${esc(b.quantity)}` : ''}${b.city ? ` · ${esc(b.city)}` : ''}${b.intent ? ` · intent ${esc(b.intent)}` : ''}</span>${when(b.last_at)}${srcBtn(b)}</div>`).join('');
  const enq = (c.enquiries || []).map(e => `<div class="ev-row">${mockTag(e.origin)}<span class="ev-main"><b>${esc(e.product || '—')}</b>${e.specs?.length ? ` · ${esc(e.specs.join('; '))}` : ''} · ${esc([e.city, e.state].filter(Boolean).join(', ') || 'city not shared')}${e.verified?.mobile ? ' · <span class="dim">mobile verified</span>' : ''}</span>${when(e.at)}${srcBtn(e)}</div>`).join('');
  const leads = (c.leads || []).map(l => `<div class="ev-row">${mockTag(l.origin)}<span class="ev-main"><b>${esc(l.title || l.product || '—')}</b>${l.quantity ? ` · ${esc(l.quantity)}` : ''}${l.order_value ? ` · order value ₹${esc(l.order_value)}` : ''}${l.buyer_city ? ` · buyer in ${esc(l.buyer_city)}` : ''}${l.buyer_requirements ? ` · buyer posted ${esc(l.buyer_requirements)} requirements` : ''}</span>${when(l.at)}${srcBtn(l)}</div>`).join('');
  const pnsSeller = (c.pns_calls || []).filter(x => x.role === 'seller');
  const pnsBuyer = (c.pns_calls || []).filter(x => x.role === 'buyer');
  const pnsRow = x => `<div class="ev-row">${mockTag(x.origin)}<span class="ev-main"><b>${esc(x.product || '—')}</b>${x.quantity ? ` · ${esc(x.quantity)}` : ''}${x.intent ? ` · intent ${esc(x.intent)}` : ''}${x.readiness ? ` · ${esc(x.readiness)}` : ''}${x.outcome ? ` · ${esc(x.outcome)}` : ''}${x.narrative ? `<br><span class="dim">${esc(x.narrative)}</span>` : ''}</span>${when(x.at)}${srcBtn(x)}</div>`;
  const pns = pnsSeller.map(pnsRow).join('') + (pnsBuyer.length ? `<p class="dim ev-note">As a buyer (kept apart — never a seller preference):</p>${pnsBuyer.map(pnsRow).join('')}` : '');
  const kindChip = k => `<span class="chip ${/complaint|ticket/.test(k) ? 'warn' : ''}">${esc(k.replace('STS · ', ''))}</span>`;
  const hist = (c.history || []).map(h => `<div class="ev-row">${mockTag(h.origin)}${kindChip(h.kind)}<span class="ev-main">${h.outcome ? `<b>${esc(h.outcome)}</b> — ` : ''}${esc(h.summary || '')}${h.by ? ` <span class="dim">· ${esc(h.by)}${h.via ? ' via ' + esc(h.via) : ''}</span>` : ''}</span>${when(h.at)}${srcBtn(h)}</div>`).join('');
  const msgs = (c.messages || []).slice(0, 30).map(m => `<div class="ev-row">${mockTag(m.origin)}<span class="chip">${m.from === 'seller' ? 'Seller' : 'Contact'}</span><span class="ev-main">${esc(m.text || '(attachment or call log)')}</span>${when(m.at)}${srcBtn(m)}</div>`).join('');
  const gc = c.global_context || {};
  const ctx = gc.api ? `<div class="ev-row">${mockTag(gc.origin)}<span class="ev-main">${Object.keys(gc.segments || {}).length ? Object.entries(gc.segments).map(([k, v]) => `<b>${esc(titleCase(k))}</b>: ${esc(JSON.stringify(v).slice(0, 160))}`).join('<br>') : `<span class="dim">${esc(gc.message || 'No context returned')} — getContext needs the mapping_id that generateContextUID returns.</span>`}</span>${srcBtn({ api: gc.api, pointer: '/data' })}</div>` : '';
  const complaints = (c.history || []).filter(h => /complaint|ticket/.test(h.kind)).length;
  return evCard('Who he dealt with', c.buyers?.length, buyers, false, '<span class="dim" style="font-size:11.5px">enquiries · buy leads · PNS</span>')
    + evCard('History · STS, calls', c.history?.length, hist, complaints > 0, complaints ? `<span class="chip warn">${complaints} complaints/tickets</span>` : '')
    + evCard('Enquiries', c.enquiries?.length, enq)
    + evCard('Purchased buy leads', c.leads?.length, leads)
    + evCard('PNS calls', c.pns_calls?.length, pns)
    + evCard('Chats (LMS)', c.messages?.length, msgs + (c.messages?.length > 30 ? `<p class="dim ev-note">Showing 30 of ${c.messages.length}; open a source for all.</p>` : ''))
    + evCard('Global context (getContext)', gc.api ? 1 : 0, ctx);
}

function sourcesHtml(sources = []) {
  if (!sources.length) return '<p class="dim" style="font-size:11.5px">No cited evidence — treat as a guess.</p>';
  return `<div class="sources">${sources.map(x => `<button class="source" data-src='${esc(JSON.stringify({ api: x.api, pointer: x.pointer }))}'>${mockTag(x.origin)}<span>${esc(x.text)}</span><code>${esc(x.api)} ${esc(x.pointer)}</code></button>`).join('')}</div>`;
}

function editForm(name, x) {
  return `<form class="edit-factor" data-factor="${esc(name)}" style="display:grid;gap:6px;border-top:1px solid var(--line);padding-top:8px">
    <label class="dim" style="font-size:11.5px" for="ef-${esc(name)}">Change ${esc(titleCase(name))}</label>
    <input id="ef-${esc(name)}" name="value" type="text" value="${esc(typeof x.value === 'object' ? JSON.stringify(x.value) : x.value)}" maxlength="200">
    <input name="reason" type="text" placeholder="Why (what you know that the data doesn't)" maxlength="300" aria-label="Reason">
    <div style="display:flex;gap:6px;justify-content:flex-end"><button class="btn btn-sm" name="mode" value="suggest">Suggest</button><button class="btn btn-sm btn-primary" name="mode" value="apply">Apply now</button></div></form>`;
}

// ---------------------------------------------------------------- call pane
async function startCall(s) {
  let seed = [];
  try { seed = JSON.parse(sessionStorage.getItem(`sg-seed-${s.runId}`) || '[]'); } catch { seed = []; }
  try { s.talk = sessionStorage.getItem(`sg-talk-${s.runId}`) === '1' || s.run?.origin === 'talk'; } catch { s.talk = s.run?.origin === 'talk'; }
  if (s.talk) seed = [];  // Talk as Seller starts from scratch: the bot opens, you answer
  try { s.call = await api('/api/v5/calls', { method: 'POST', body: { run_id: s.runId, seed_turns: seed, opening: !!s.talk } }); }
  catch (err) { $('#p-call').innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  s.auto = !s.talk && seed.length > 0;
  const lines = s.call.transcript || [];
  const lastBot = lines.map(t => t.who).lastIndexOf('bot');
  s.spoken = lastBot >= 0 ? lastBot : lines.length;  // the opening is spoken; earlier seeded floor lines are not replayed
  renderCall(s);
  if (s.auto) scheduleAuto(s, 2500);
}

function renderCall(s) {
  const r = s.run, call = s.call, tele = r.teleprompter;
  const say = call.say_next?.text || tele.opening;
  const why = call.say_next?.why?.length ? call.say_next.why : tele.why || [];
  const d = call.delivery;
  const stageIdx = STAGES.findIndex(([k]) => k === call.stage);
  const engOpen = $('#p-call [data-eng]')?.open;
  $('#p-call').innerHTML = `
    <h2 class="pane-title">Call · ${s.auto ? '<span style="color:var(--accent)">auto (Sarvam plays the seller)</span>' : 'you are the seller'}</h2>
    ${s.talk ? `<p class="note talk-note" role="note"><b>Talk as Seller.</b> The bot is the IndiaMART executive and has opened the call. Answer as the seller — type below${s.cat ? ' or use the microphone' : ''}. Watch the persona switch when you sound rushed, confused, interested or frustrated.</p>` : ''}
    ${s.cat ? pickerHtml(s.cat, 'call', ENGINE_KINDS, { open: engOpen }) : ''}
    ${s.eng?.bot === 'sarvam_agent' ? payalPanelHtml(s) : ''}
    ${s.brief ? planHtml(s.brief, { call, compact: true }) : ''}
    <div class="stages" aria-label="Call stage">${STAGES.map(([k, l], i) => `<span class="${i === stageIdx ? 'on' : i < stageIdx ? 'done' : ''}">${l}</span>`).join('')}</div>
    ${switchBannerHtml(s)}
    <section class="prompter" aria-labelledby="say-title">
      <div style="display:flex;align-items:center;gap:8px"><p class="eyebrow" id="say-title">Teleprompter · say next</p><span class="chip">${esc(LANGS[d.language] || d.language)} · pace ${esc(d.pace)} · ${esc(d.sentence_length)} · warmth ${esc(d.warmth)}</span></div>
      <p class="say" id="say">${esc(say)}</p>
      ${why.length ? `<div class="why">${why.map(w => `<span class="chip">${esc(w)}</span>`).join('')}</div>` : ''}
      <div class="prompter-tools">
        <button class="btn btn-sm btn-primary" data-act="speak">▶ Speak (${esc(voiceName(s, d.voice))})</button>
        <label class="sr" for="tr-lang">Translate to</label><select id="tr-lang" style="width:auto;min-height:30px">${Object.entries(LANGS).map(([k, l]) => `<option value="${k}" ${k === (d.language === 'hi-IN' ? 'te-IN' : 'hi-IN') ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <button class="btn btn-sm" data-act="translate">🌐 Translate</button>
        <span class="dim" id="say-meta" style="font-size:11.5px"></span>
      </div>
      <details><summary class="dim" style="font-size:12.5px">Objection · handoff · avoid</summary><div style="display:grid;gap:6px;margin-top:8px;font-size:12.5px">
        <div><b>If they object (${esc(tele.objection.type)}):</b> ${esc(tele.objection.line)}</div>
        <div><b>Handoff:</b> ${esc(tele.handoff)}</div>
        <div><b>Never:</b> ${tele.avoid.map(esc).join(' · ')}</div></div></details>
    </section>
    <div class="meters" aria-label="Live signals (recency-weighted)">${['frustration', 'confusion', 'interest', 'rush'].map(k => `<div class="meter"><span>${signalChip(k)}</span><div class="bar"><i style="width:${Math.min(100, (call.levels[k] || 0) * 70)}%;background:var(--s-${k})"></i></div></div>`).join('')}</div>
    <section class="card" aria-label="Transcript"><div class="card-head"><h3>Transcript</h3><span class="aside" style="display:flex;gap:6px">
      <button class="btn btn-sm voice-btn" data-act="voice" aria-pressed="${s.voice}" title="Hear the call: bot in the persona's Sarvam voice, simulated seller in a second voice">${s.voice ? '🔊 Voice on' : '🔇 Voice off'}</button>
      <button class="btn btn-sm" data-act="auto" aria-pressed="${s.auto}">${s.auto ? '⏸ Pause auto' : '▶ Auto (Sarvam seller)'}</button></span></div>
      <div class="card-body"><div class="transcript" id="transcript">${transcriptHtml(call, s)}</div>
      <form class="composer" id="composer" style="margin-top:10px"><label class="sr" for="say-input">Say something as the seller</label>
        <input id="say-input" placeholder="${esc(PLACEHOLDER)}" maxlength="600" ${call.stopped ? 'disabled' : ''}>
        <button class="btn btn-primary" ${call.stopped ? 'disabled' : ''}>Send</button></form>
      <p class="dim" style="font-size:11.5px;margin-top:6px">Signals are detected from words (not acoustic emotion). Inferred switches respect an 8-second, per-factor cooldown; explicit requests apply immediately.</p></div></section>
    <section class="card" aria-label="Switch log"><div class="card-head"><h3>Switch log · ${call.switches.length}</h3><span class="aside dim" style="font-size:11.5px">signal → change → reason</span></div>
      <div class="card-body switchlog" style="display:grid;gap:6px">${call.switches.slice().reverse().map(w => `
        <div class="switch-row ${w.applied ? '' : 'held'}"><div class="switch-top"><span class="mono dim">${esc(w.t_s)}s</span>${signalChip(w.signal)}${w.applied ? origin('live_api', w.explicit ? 'Applied · explicit' : 'Applied') : origin('default', 'Held · cooldown')}</div>
        <div class="mono" style="font-size:11.5px">${Object.entries(w.changes || {}).filter(([k]) => !['simplify', 'acknowledge', 'advance'].includes(k)).map(([k, [a, b]]) => `${esc(k)} ${esc(a)} → ${esc(b)}`).join(' · ') || esc(Object.keys(w.changes || {}).join(', ')) || '—'}</div>
        <div style="font-size:12.5px">${esc(w.reason)}${w.cue ? ` <span class="dim">· heard “${esc(w.cue)}”</span>` : ''}${w.override ? ` <span class="dim">· ${esc(w.override)}</span>` : ''}</div></div>`).join('') || '<p class="dim">No switches yet. Say something that shows rush, confusion, frustration, interest or another language.</p>'}</div></section>
    ${packagesHtml(tele)}
    ${meetingHtml(s)}`;
  const tr = $('#transcript'); tr.scrollTop = tr.scrollHeight;
  paintPayal(s);
  renderFlow(s);
  voiceNewLines(s);
}

// ---------------------------------------------------------------- Payal on Sarvam Voice Agents (live voice, 5.3)
function payalPanelHtml(s) {
  const avail = s.cat?.payal || {};
  const live = !!s.payal;
  const app = chosenApp(s.cat, s.agentInfo, 'call');
  const ours = app === s.agentInfo?.payal?.app_id;
  // Real sellers too (owner, 2026-10-10, production): the agent gets the real seller's prepared data from this run.
  const blocked = ours && !avail.available ? (avail.reason || 'Payal is not available right now.') : agentBlocked(s.cat, s.agentInfo, 'call');
  return `<section class="card payal-live" aria-label="Live voice call with Payal on Sarvam">
    <div class="card-head"><h3>${esc((s.agentInfo?.workspace_agents || []).find(a => a.app_id === app)?.name || 'Payal')} · Sarvam Voice Agent</h3><span class="aside">${origin('live_api', 'Sarvam')} <span class="chip" id="pl-state">${esc(s.payalState)}</span></span></div>
    <div class="card-body" style="display:grid;gap:8px">
      <p class="dim" style="font-size:12.5px">Talk to Sarvam’s hosted agent as this seller. Sarvam listens, handles interruptions and takes turns; this seller’s variables, the persona’s language and pace, and the words to expect are sent when the call starts. Headphones avoid echo.</p>
      ${blocked ? `<p class="note">${esc(blocked)}</p>` : ''}
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><button class="btn ${live ? '' : 'btn-primary'}" data-act="payal" ${blocked && !live ? 'disabled' : ''}>${live ? '■ End call' : `📞 Call ${esc(((s.agentInfo?.workspace_agents || []).find(a => a.app_id === app)?.name || 'Payal').split(/\s+[·(]/)[0])} (live voice)`}</button><span class="dim" id="pl-meta" style="font-size:11.5px"></span></div>
      <div id="pl-switch"></div>
      <div class="transcript pl-lines" id="pl-lines" aria-live="polite"></div>
      <div class="pl-events" id="pl-events"></div>
      <p class="meta" id="pl-replies" style="font-size:12px"></p>
    </div></section>`;
}

// Owner's Payal note "persona switch should be visible": what SellerGenome read in the seller's words, and how Payal's
// prompt adapts to it (rushed → one short line + meeting ask; confused → one idea, simple words; annoyed → acknowledge).
const PAYAL_SWITCH = {
  rush: 'Seller is in a hurry → Payal keeps to one short line and asks for the meeting',
  confusion: 'Seller is confused → Payal explains one thing at a time, in simple words',
  frustration: 'Seller is annoyed → Payal acknowledges first, no pitch',
  interest: 'Seller is interested → Payal moves straight to the meeting',
  language_switch: 'Language switch → Payal follows the seller’s language',
  bot_question: 'Asked if this is a bot → Payal says she is IndiaMART’s virtual assistant',
  stop: 'Stop request → Payal ends the call',
};
function payalSwitch(s, x) {
  const text = PAYAL_SWITCH[x.signal];
  if (!text) return;
  s.payalSwitches = [...(s.payalSwitches || []), { ...x, text, at: Date.now() }].slice(-10);
  s.payalSwitchFlash = true;
  toast(`Persona switch: ${text}`, 'info', 3200);
  paintPayal(s);
}

function paintPayal(s) {
  const banner = $('#pl-switch');
  if (banner) {
    const list = s.payalSwitches || [], w = list.at(-1);
    banner.innerHTML = w ? `<div class="switch-banner ${s.payalSwitchFlash ? 'flash' : ''}" role="status" aria-live="polite"><span class="eyebrow">Persona switch · ${list.length}</span><span>${signalChip(w.signal)} ${esc(w.text)}</span>${w.cue ? `<span class="mono dim">heard “${esc(w.cue)}”</span>` : ''}</div>` : '';
    s.payalSwitchFlash = false;
  }
  const lines = $('#pl-lines');
  if (lines) {
    lines.innerHTML = s.payalLines.map(l => `<div class="bubble ${l.who === 'bot' ? 'bot' : 'seller'}"><span class="who ${l.who === 'bot' ? 'bot' : 'seller'}">${l.who === 'bot' ? 'Payal' : 'You'}</span> ${esc(l.text)}${l.opening ? '<div class="meta"><span>opening line — Sarvam speaks it in the call language</span></div>' : ''}</div>`).join('')
      || '<p class="dim" style="font-size:12px">The conversation appears here as Sarvam transcribes it.</p>';
    lines.scrollTop = lines.scrollHeight;
  }
  const ev = $('#pl-events');
  if (ev) ev.innerHTML = Object.entries(s.payalEvents).map(([k, n]) => `<span class="chip">${esc(k.replace(/_/g, ' '))} · ${n}</span>`).join(' ');
  const st = $('#pl-state'); if (st) st.textContent = s.payalState;
  const meta = $('#pl-meta');
  if (meta) meta.textContent = s.payal ? `session ${s.payal.session.session_id} · REAL CALL on the floor` : s.payalLines.length ? `${s.payalLines.length} lines · ended` : '';
  const rt = $('#pl-replies');
  if (rt) rt.innerHTML = (s.payalReplies || []).length ? `Payal answered in ${(s.payalReplies || []).map(ms => `<span class="reply-time">${(ms / 1000).toFixed(1)} s</span>`).join(' · ')} <span class="dim">(from your last sound to her first sound)</span>` : '';
}

async function payalCall(s, button) {
  if (s.payal) { button.disabled = true; await s.payal.stop(); return; }
  s.listener?.stop(); s.listener = null;
  s.auto = false; clearTimeout(s.autoTimer); s.audio?.pause();
  s.payalLines = []; s.payalEvents = {}; s.payalState = 'connecting'; s.payalReplies = []; s.payalSwitches = [];
  button.disabled = true; paintPayal(s);
  try {
    const { startPayalCall } = await import('./payal-live.js');
    s.payal = await startPayalCall({ sellerId: s.run.seller_id, runId: s.runId, appId: chosenApp(s.cat, s.agentInfo, 'call'),
      onLine: line => { s.payalLines.push(line); paintPayal(s); },
      onEvent: kind => { if (kind && !['ping', 'audio_chunk', 'text_chunk'].includes(kind)) { s.payalEvents[kind] = (s.payalEvents[kind] || 0) + 1; paintPayal(s); } },
      onState: state => { s.payalState = state; paintPayal(s); },
      onReply: ms => { s.payalReplies = [...(s.payalReplies || []), ms].slice(-8); paintPayal(s); },
      onSignal: x => payalSwitch(s, x),
      onEnd: () => { s.payal = null; s.payalState = 'ended'; if (s.alive) { renderCall(s); head(s); } } });
    toast('Payal is on the line — speak as the seller.');
  } catch (err) { s.payal = null; s.payalState = 'idle'; toast(err.message, 'error'); }
  finally { if (s.alive) { renderCall(s); head(s); } }
}

function voiceName(s, personaVoice) {
  const e = s.eng || {};
  if (e.tts_voice && e.tts_voice !== 'auto') return e.tts_voice;
  const map = s.cat?.persona_voice_map || {};
  return e.tts_model === 'bulbul:v3' ? `Bulbul v3 ${personaVoice}` : (map[personaVoice] || personaVoice);
}

function sellerVoice(s, botVoice) {
  const e = s.eng || {};
  const bot = e.tts_voice && e.tts_voice !== 'auto' ? e.tts_voice : botVoice;
  const female = FEMALE_VOICE.test(bot || '');
  if (e.tts_model === 'bulbul:v3') return female ? 'shubh' : 'priya';
  return female ? 'rahul_hi_conversational' : 'kavya_hi_conversational';
}

// ---------------------------------------------------------------- PM's full brief over the workspace
function showBrief(s, open) {
  const v = $('#brief-view');
  if (!v || !s.brief) { if (open) toast('The brief is ready once preparation finishes.', 'error'); return; }
  v.hidden = !open;
  $('[data-act="brief"]')?.setAttribute('aria-pressed', String(open));
  if (open) { v.innerHTML = fullBriefHtml(s.brief, s.call); v.scrollTop = 0; v.querySelector('[data-brief-close]')?.focus({ preventScroll: true }); }
}

// ---------------------------------------------------------------- hear the call (Sarvam Bulbul)
// Every new bot line is spoken in the persona's voice; in Auto mode the simulated seller speaks in a contrasting
// voice. Lines play one after another and Auto waits for the audio, so the call can be followed by ear.
function voiceNewLines(s) {
  const lines = s.call?.transcript || [];
  if (!s.voice) { s.spoken = lines.length; return; }
  const fresh = lines.slice(s.spoken).filter(t => t.who === 'bot' || (t.who === 'seller' && t.source === 'simulated_seller'));
  s.spoken = lines.length;
  fresh.forEach(t => { s.voiceQueue = s.voiceQueue.then(() => speakLine(s, t)).catch(() => {}); });
}

async function speakLine(s, turn) {
  if (!s.alive || !s.voice) return;
  const d = s.call.delivery || {};
  const isBot = turn.who === 'bot';
  const botVoice = d.voice || 'ritu';
  // The simulated seller always speaks in a contrasting voice, so the two sides can be told apart by ear.
  const eng = isBot ? s.eng : { ...(s.eng || {}), tts_voice: 'auto' };
  s.speaking = true; markSpeaking(s, turn, true);
  try {
    const pre = s.prestarted[turn.i];
    delete s.prestarted[turn.i];
    const { audio, out, firstSound } = await prestartedOr(pre, () => speakVia({ text: turn.text.slice(0, 600), language: d.language || 'hi-IN',
      speaker: isBot ? botVoice : sellerVoice(s, botVoice), pace: isBot ? Number(d.pace) || 1 : 1, eng, signal: s.abort.signal }));
    if (!s.alive || !s.voice) return;
    s.audio?.pause();
    s.audio = audio;
    if (isBot) {
      s.lastBotText = `${turn.text} ${out.spoken_text || ''}`;
      const said = s.saidAt;
      firstSound.then(ms => {
        if (!ms) return;
        s.lastFirstSound = ms;
        // Reply time as the seller hears it: from the end of their turn (the final transcript or Send) to VANI's first sound.
        if (said && turn.source === 'sarvam') { s.replyTimes[turn.i] = Math.round(performance.now() - said); s.saidAt = 0; showReplyTime(s, turn.i); }
      });
    }
    await playToEnd(audio).catch(() => toast('Tap 🔊 once — the browser blocked autoplay.', 'error'));
  } catch (err) {
    if (err.name !== 'AbortError' && s.alive) toast(`Voice: ${err.message}`, 'error');
  } finally { s.speaking = false; markSpeaking(s, turn, false); }
}

function showReplyTime(s, i) {
  const meta = $(`#transcript [data-turn="${i}"] .meta`);
  if (meta && s.replyTimes[i] && !meta.querySelector('.reply-time')) meta.insertAdjacentHTML('beforeend', replyChip(s.replyTimes[i]));
}

const replyChip = ms => `<span class="reply-time" title="From the seller's last sound (microphone) or Send to VANI's first sound">answered in ${(ms / 1000).toFixed(1)} s</span>`;

function markSpeaking(s, turn, on) {
  const el = $(`#transcript [data-turn="${turn.i}"]`);
  el?.classList.toggle('speaking', on);
  const b = $('[data-act="voice"]');
  if (b) b.classList.toggle('live', on);
}


// "pace 1.0 → 1.12 · sentence_length medium → short" (flags such as simplify/acknowledge are named in the reason).
function switchChanges(w) {
  return Object.entries(w.changes || {}).filter(([k, v]) => Array.isArray(v) && !['simplify', 'acknowledge', 'advance'].includes(k))
    .map(([k, [a, b]]) => `${k.replace(/_/g, ' ')} ${a} → ${b}`).join(' · ');
}

// Owner, 2026-10-10: "Persona switch should be visible" — a banner over the call pane names the latest switch, flashes when
// a new one applies (and a toast says it), so nobody has to open the switch log to notice the bot changed its delivery.
function switchBannerHtml(s) {
  const applied = (s.call?.switches || []).filter(w => w.applied);
  const w = applied.at(-1);
  if (!w) return '';
  const flash = s.switchFlash;
  s.switchFlash = false;  // flash once per new switch, not on every re-render
  return `<div class="switch-banner ${flash ? 'flash' : ''}" role="status" aria-live="polite"><span class="eyebrow">Persona switch ${applied.length}</span>
    <span>${signalChip(w.signal)} ${esc(w.reason)}</span>${switchChanges(w) ? `<span class="mono dim">${esc(switchChanges(w))}</span>` : ''}</div>`;
}

function noteSwitches(s, before) {
  const applied = (s.call?.switches || []).filter(w => w.applied);
  s.switchFlash = applied.length > before;
  if (s.switchFlash) toast(`Persona switch: ${applied.at(-1).reason}`, 'info', 3200);
}

function transcriptHtml(call, s = { replyTimes: {} }) {
  const out = [];
  const switchesByTurn = {};
  call.switches.forEach(w => { (switchesByTurn[w.turn] ||= []).push(w); });
  for (const t of call.transcript) {
    if (t.who === 'system') { out.push(`<div class="sysline">${esc(t.text)}</div>`); continue; }
    const sigs = (t.signals || []).map(x => signalChip(x.signal)).join('');
    out.push(`<div class="bubble ${esc(t.who)}" data-turn="${esc(t.i)}"><span class="who ${esc(t.who)}">${t.who === 'bot' ? 'VANI' : 'Seller'}</span> ${esc(t.text)}${t.gloss ? `<span class="gloss">${esc(t.gloss)}</span>` : ''}
      <div class="meta">${t.scripted ? '<span>scripted opening</span>' : ''}${t.source === 'simulated_seller' ? '<span>Sarvam-simulated seller</span>' : ''}${t.source === 'typed' ? '<span>you</span>' : ''}${t.ms ? `<span>${esc(fmtMs(t.ms))}</span>` : ''}${t.source && t.who === 'bot' ? `<span>${esc(t.source)}</span>` : ''}${s.replyTimes[t.i] ? replyChip(s.replyTimes[t.i]) : ''}${sigs}</div></div>`);
    (switchesByTurn[t.i] || []).forEach(w => out.push(w.applied
      ? `<div class="switchline" role="note"><b>⇄ Persona switch</b> ${signalChip(w.signal)} <span>${esc(w.reason)}</span>${switchChanges(w) ? `<span class="mono">${esc(switchChanges(w))}</span>` : ''}</div>`
      : `<div class="sysline held">⇄ ${esc(w.reason)} · held by cooldown</div>`));
  }
  if (call.stopped) out.push('<div class="sysline">Call ended — do-not-call recorded</div>');
  return out.join('');
}

function packagesHtml(tele) {
  if (!tele.packages?.length) return '';
  return `<section class="card"><div class="card-head"><h3>Packages that fit</h3><span class="aside dim" style="font-size:11.5px">No prices — none are verified</span></div><div class="card-body" style="display:grid;gap:8px;font-size:12.5px">${tele.packages.map(p => `<div><b>${esc(p.name)}</b> — ${esc(p.why.join('; '))}<br><span class="dim">${esc(p.features.join(' · '))}</span></div>`).join('')}</div></section>`;
}

function meetingHtml(s) {
  const r = s.run, call = s.call, addrs = r.contract.addresses || [], hint = call.meeting_hint;
  if (s.meeting) {
    const m = s.meeting;
    return `<section class="card" id="meeting"><div class="card-head"><h3>Meeting booked (test)</h3>${origin('live_api', 'Seller confirmed')}</div><div class="card-body meeting-done">
      <div><b>${esc(m.start_label)}</b> · ${esc(m.mode === 'online' ? 'Online' : 'In person')} · ${esc(m.location)}</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">${m.meeting_link ? `<a class="btn btn-sm btn-primary" href="${esc(m.meeting_link)}" target="_blank" rel="noopener">Join meeting ↗</a>` : ''}
        <a class="btn btn-sm" href="${BASE}${esc(m.ics_path)}">⬇ Calendar file (.ics)</a><a class="btn btn-sm" href="${esc(m.google_calendar_link)}" target="_blank" rel="noopener">Add to Google Calendar ↗</a>
        ${m.map_link ? `<a class="btn btn-sm" href="${esc(m.map_link)}" target="_blank" rel="noopener">Map ↗</a>` : ''}</div>
      <details><summary class="dim" style="font-size:12.5px">Handoff note for the executive</summary><pre class="code wrap" style="margin-top:6px">${esc(m.handoff.join('\n'))}</pre></details>
      <p class="dim" style="font-size:11.5px">${esc(m.not_done)}</p></div></section>`;
  }
  const tomorrow = new Date(Date.now() + 86400000);
  const local = `${tomorrow.toISOString().slice(0, 10)}T11:00`;
  const loc = r.contract.locality;
  return `<section class="card" id="meeting"><div class="card-head"><h3>Meeting desk</h3>${hint ? `<span class="aside chip solid">Seller said: ${esc(hint.when || '')} ${esc(hint.where || '')}</span>` : ''}</div>
    <form class="card-body" id="meeting-form" style="display:grid;gap:10px">
      <div class="seg" role="radiogroup" aria-label="Meeting mode"><button type="button" data-mode="online" aria-pressed="${(hint?.mode || 'online') === 'online'}">Online</button><button type="button" data-mode="in_person" aria-pressed="${hint?.mode === 'in_person'}">In person</button></div>
      <input type="hidden" name="mode" value="${esc(hint?.mode === 'in_person' ? 'in_person' : 'online')}">
      <fieldset style="border:0;padding:0;margin:0;display:grid;gap:6px" id="addr-set" ${hint?.mode === 'in_person' ? '' : 'hidden'}><legend class="dim" style="font-size:12px;margin-bottom:4px">Which address? (never the device location)</legend>
        ${addrs.map((a, i) => `<label class="addr"><input type="radio" name="address_kind" value="${esc(a.kind)}" ${i === 0 ? 'checked' : ''}><span>${esc(a.label)} ${mockTag(a.origin)}</span><small class="dim">${esc(a.text)}</small></label>`).join('')}
        ${loc?.locality ? `<p class="dim" style="font-size:11.5px">Sarvam locality: <b>${esc(loc.locality)}</b>, ${esc(loc.city || '')} ${loc.pincode ? esc(loc.pincode) : ''} — ${esc(loc.note || '')}</p>` : ''}
        <label class="addr"><input type="radio" name="address_kind" value=""><span>What the seller said / other</span><input type="text" name="address_text" placeholder="Type the address the seller confirmed" style="grid-column:2" value="${esc(hint?.where && hint.mode === 'in_person' ? hint.where : '')}"></label>
      </fieldset>
      <label class="dim" style="font-size:12px" for="m-start">Date and time (IST)</label><input id="m-start" type="datetime-local" name="start" value="${local}" required>
      <p class="form-error" id="m-error"></p>
      <button class="btn btn-primary">Confirm test meeting</button>
      <p class="dim" style="font-size:11.5px">Creates a real meeting link, a calendar file and a Google Calendar link. Nobody is invited; nothing is written to a real calendar.</p>
    </form></section>`;
}

function renderStats(s) {
  const ev = s.events;
  const hits = ev.filter(e => e.kind === 'cache');
  const responses = ev.filter(e => e.kind === 'response');
  const byOrigin = {};
  responses.forEach(e => { byOrigin[e.origin] = (byOrigin[e.origin] || 0) + 1; });
  const sarvam = ev.find(e => e.node === 'sarvam.aggregate' && (e.kind === 'llm' || e.kind === 'cache'));
  $('#run-stats').innerHTML = `<div class="card"><div class="card-body" style="padding-top:12px;display:grid;gap:8px;font-size:12.5px">
    <dl class="kv"><dt>Total</dt><dd class="mono">${esc(fmtMs(s.run.duration_ms))}</dd>
    <dt>Initial facts</dt><dd class="mono">${esc(fmtMs(ev.find(e => e.node === 'facts')?.t_ms))}</dd>
    <dt>Sarvam persona</dt><dd class="mono">${sarvam ? (sarvam.kind === 'cache' ? 'reused (no spend)' : esc(fmtMs(sarvam.ms))) : 'rules fallback'}</dd>
    <dt>Elastic cache</dt><dd class="mono">${hits.filter(h => h.status === 'hit').length}/${hits.length} hits</dd>
    <dt>Responses by origin</dt><dd>${Object.entries(byOrigin).map(([o, n]) => `${origin(o)} <span class="mono">${n}</span>`).join(' ')}</dd></dl>
    <p class="dim">Hover a box for its request and Elasticsearch query; click to open the inspector.</p></div></div>`;
}

// ---------------------------------------------------------------- interactions
function scheduleAuto(s, delay = 4500) {
  clearTimeout(s.autoTimer);
  if (!s.auto || !s.alive) return;
  s.autoTimer = setTimeout(async () => {
    if (!s.auto || s.busy || document.hidden) return scheduleAuto(s, 3000);
    if (s.speaking) return scheduleAuto(s, 700);  // let the listener hear the last line first
    s.busy = true;
    try {
      const out = await api(`/api/v5/calls/${s.call.id}/auto`, { method: 'POST', body: { speak: s.voice ? speakOpts(s.eng) : null }, signal: s.abort.signal });
      const before = s.call.switches.filter(w => w.applied).length;
      s.call = out.call;
      noteSwitches(s, before);
      (out.speech || []).forEach(x => { s.prestarted[x.turn] = x; });
      renderCall(s); head(s);
    }
    catch (err) { if (err.name === 'AbortError' || !s.alive) return; s.auto = false; toast(err.message, 'error'); renderCall(s); }
    finally { s.busy = false; }
    if (s.call?.stopped || ['wrap_up'].includes(s.call?.stage) && s.call.outcome) s.auto = false;
    scheduleAuto(s, 5200);
  }, delay);
}

async function sendTurn(s, text, via = 'you') {
  if (!text.trim()) return;
  if (s.busy) { s.pendingText = `${s.pendingText} ${text}`.trim(); return; }  // a voice turn that arrives while VANI is thinking
  s.auto = false; clearTimeout(s.autoTimer);
  s.busy = true;
  const tr = $('#transcript');
  tr?.insertAdjacentHTML('beforeend', `<div class="bubble seller"><span class="who seller">Seller</span> ${esc(text)}<div class="meta"><span>${esc(via)}</span></div></div><div class="sysline held">VANI is thinking…</div>`);
  if (tr) tr.scrollTop = tr.scrollHeight;
  if (!s.saidAt) s.saidAt = performance.now();
  try {
    trace('turn_sent');
    const out = await api(`/api/v5/calls/${s.call.id}/turn`, { method: 'POST', body: { text, speak: s.voice ? speakOpts(s.eng) : null }, signal: s.abort.signal });
    trace('turn_received', { llm_ms: out.call.transcript.at(-1)?.ms });
    const before = s.call.switches.filter(w => w.applied).length;
    s.call = out.call;
    noteSwitches(s, before);
    (out.speech || []).forEach(x => { s.prestarted[x.turn] = x; });
  }
  catch (err) { if (err.name === 'AbortError' || !s.alive) return; toast(err.message, 'error'); }
  finally {
    s.busy = false;
    if (s.alive) { renderCall(s); head(s); if (!s.listener) $('#say-input')?.focus(); }
    if (s.pendingText && s.alive) { const next = s.pendingText; s.pendingText = ''; setTimeout(() => sendTurn(s, next, 'voice · live'), 0); }
  }
}

async function onClick(s, e) {
  const t = e.target.closest('button, [data-pane]');
  if (!t) return;
  if (t.dataset.pane) {
    s.pane = t.dataset.pane;
    $$('.pane-tabs .tab').forEach(b => b.setAttribute('aria-selected', String(b.dataset.pane === s.pane)));
    $$('.panes > .pane').forEach(p => p.classList.toggle('active', p.id === `p-${s.pane}`));
    return;
  }
  if (t.dataset.sources) {
    const sources = JSON.parse(t.dataset.sources || '[]');
    if (sources[0]) openSource(s, sources[0], t);
    return;
  }
  if (t.dataset.src) { openSource(s, JSON.parse(t.dataset.src), t); return; }
  if (t.dataset.mode) {
    $$('#meeting-form [data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b === t)));
    $('#meeting-form [name=mode]').value = t.dataset.mode;
    $('#addr-set').hidden = t.dataset.mode !== 'in_person';
    return;
  }
  if (t.dataset.arch) { setArch(s, t.dataset.arch === 'show', true); return; }
  if (t.dataset.record !== undefined && s.brief) { openRecord(s.brief, t, (src, el) => openSource(s, src, el)); return; }
  if (t.dataset.briefClose !== undefined) { showBrief(s, false); return; }
  if (t.dataset.flow) {
    const n = Number(t.dataset.flow);
    s.flowFocus = s.flowFocus === n ? null : n;
    s.graph.focus(FLOW[s.flowFocus ?? stageOf(s).i].nodes);
    $$('#flow [data-flow]').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.flow) === s.flowFocus)));
    return;
  }
  const act = t.dataset.act;
  if (act === 'arch') {
    if (!s.wide.matches) { $('[data-pane="arch"]')?.click(); $('#p-arch')?.scrollIntoView({ block: 'start' }); return; }
    setArch(s, !s.archOpen, true); return;
  }
  if (act === 'brief') { showBrief(s, $('#brief-view').hidden); return; }
  if (act === 'voice') {
    s.voice = !s.voice;
    try { localStorage.setItem(VOICE_KEY, s.voice ? 'on' : 'off'); } catch { /* storage blocked */ }
    if (!s.voice) { s.audio?.pause(); s.speaking = false; }
    else { const last = [...(s.call?.transcript || [])].reverse().find(t => t.who === 'bot'); if (last) { s.voiceQueue = s.voiceQueue.then(() => speakLine(s, last)); } }
    s.spoken = s.call?.transcript?.length || 0;
    renderCall(s); return;
  }
  if (act === 'auto') { s.auto = !s.auto; renderCall(s); if (s.auto) scheduleAuto(s, 400); return; }
  if (act === 'meeting') { if (s.pane !== 'call') $('[data-pane="call"]')?.click(); $('#meeting')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  if (act === 'debug-refresh') { paintDebug(s, true); return; }
  if (act === 'debug') { s.debugOpen = !s.debugOpen; try { localStorage.setItem('sg-debug', s.debugOpen ? '1' : ''); } catch { /* private mode */ } head(s); paintDebug(s, true); return; }
  if (act === 'trace') { openInspector({ runId: s.runId, node: 'run', title: 'Full trace', events: s.events, extra: { node: 'run', result: null, events: s.events }, returnFocus: t }); return; }
  if (act === 'speak') return speak(s, t);
  if (act === 'translate') return translate(s, t);
  if (act === 'mic') return mic(s, t);
  if (act === 'payal') return payalCall(s, t);
}

async function speak(s, button) {
  const text = $('#say').textContent;
  const d = s.call.delivery;
  const label = button.textContent;
  button.disabled = true; button.textContent = '… synthesising';
  try {
    const { audio, out, firstSound } = await speakVia({ text, language: d.language, speaker: d.voice, pace: d.pace, eng: s.eng });
    s.audio?.pause();
    s.audio = audio;
    audio.play().catch(() => toast('Tap play again — the browser blocked autoplay.', 'error'));
    $('#say-meta').textContent = speechMeta(out, null);
    firstSound.then(ms => { if (ms && $('#say-meta')) $('#say-meta').textContent = speechMeta(out, ms); });
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; button.textContent = label; }
}

async function translate(s, button) {
  const target = $('#tr-lang').value;
  button.disabled = true;
  try {
    const out = await api('/api/v5/translate', { method: 'POST', body: { text: $('#say').textContent, target, model: s.eng?.translate_model || null } });
    $('#say').textContent = out.text;
    $('#say-meta').textContent = `${out.model || 'Sarvam translate'} → ${LANGS[target]} · ${fmtMs(out.ms)}`;
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; }
}

async function mic(s, button) {
  if (s.listener) { s.listener.stop(); s.listener = null; setPartial(''); head(s); return; }
  if (s.recorder && s.recorder.state === 'recording') { s.recorder.stop(); return; }
  if (s.payal) { toast('End the Payal call first — it is using the microphone.', 'error'); return; }
  const eng = s.eng || {};
  const language = s.call?.delivery?.language || 'hi-IN';
  if (eng.listen === 'realtime') {
    const l = new RealtimeListener({ language, eng,
      onState: st => { if (st === 'closed' && s.listener === l) { s.listener = null; setPartial(''); if (s.alive) head(s); } },
      onPartial: text => {
        setPartial(text);
        // Barge-in: speaking over VANI stops its voice — unless the words are VANI's own audio leaking into the mic.
        if (s.speaking && text.split(/\s+/).length >= 2 && !isEcho(text, s.lastBotText)) { trace('barge_in'); s.audio?.pause(); }
      },
      onFinal: (text, _lang, lastVoiceAt) => {
        setPartial('');
        if (isEcho(text, s.lastBotText)) return;
        s.audio?.pause();
        s.saidAt = lastVoiceAt || performance.now();  // the seller's real end of speech when the microphone saw it
        sendTurn(s, text, 'voice · live');
      },
      onError: msg => toast(msg, 'error'),
      onCheck: stats => { s.micStats = { ...stats, at: new Date() }; paintDebug(s); } });
    s.listener = l;
    head(s);
    try {
      const info = await l.start();
      toast(`Listening live (${info.model} · ${info.mode}) — speak; a short pause ends your turn, speaking over VANI stops its voice.`);
    } catch (err) { s.listener = null; l.cleanup(); toast(err.message, 'error'); }
    finally { if (s.alive) head(s); }
    return;
  }
  try {
    const { recorder, done } = await recordClip();
    s.recorder = recorder;
    button.textContent = '■ Stop (max 20 s)';
    const blob = await done;
    button.textContent = '… transcribing';
    const out = await transcribe(blob, eng);
    toast(`Heard (${out.model} · ${fmtMs(out.ms)}): ${out.text}`);
    await sendTurn(s, out.text, 'voice · clip');
  } catch (err) { toast(err.message, 'error'); }
  finally { s.recorder = null; if (s.alive) head(s); }
}

function setPartial(text) {
  const input = $('#say-input');
  if (input) input.placeholder = text ? `🎙 ${text}` : PLACEHOLDER;
}

// VANI's audio can leak from speakers into the microphone; a "turn" that mostly repeats VANI's last line is ignored.
const words = x => String(x || '').toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s]/gu, ' ').split(/\s+/).filter(w => w.length > 1);
function isEcho(text, bot) {
  if (!bot) return false;
  const said = new Set(words(bot)), heard = words(text);
  return heard.length > 0 && heard.filter(w => said.has(w)).length / heard.length >= 0.6;
}

function openSource(s, src, el) {
  if (!src?.api) return;
  const pointer = /^\/[^ ]*$/.test(src.pointer || '') ? src.pointer : '';  // free-text pointers ("entry 3") just open the response
  openInspector({ runId: s.runId, node: src.api, focus: pointer, events: s.events, returnFocus: el });
}

function openNode(s, node, el) {
  if (node === 'contract') {
    const fields = Object.values(s.run?.contract?.fields || {});
    return openInspector({ runId: s.runId, node, title: 'Production contract · 29 fields', events: s.events, returnFocus: el,
      extra: { node, result: { label: 'Production contract', status: 'ok', origin: 'internal', response: s.run?.contract?.fields, ms: 0 }, fields_fed: fields, events: s.events.filter(e => e.node === 'facts') } });
  }
  if (node === 'elastic') {
    const cache = s.events.filter(e => e.kind === 'cache' || e.node === 'elastic.persist');
    return openInspector({ runId: s.runId, node, title: 'Elasticsearch · cache and store', events: cache, returnFocus: el,
      extra: { node, result: { label: 'Elasticsearch', status: 'ok', origin: 'elastic_cache', response: { lookups: cache.filter(e => e.kind === 'cache').map(e => ({ node: e.node, status: e.status, ms: e.ms, query: e.es_query })), writes: cache.filter(e => e.node === 'elastic.persist').length } }, events: cache } });
  }
  if (node === 'leads.detail' || node === 'lms.messages') {
    const keys = Object.keys(s.run?.results || {}).filter(k => k.startsWith(node + ':'));
    if (keys.length) return openInspector({ runId: s.runId, node: keys[0], siblings: keys, events: s.events, returnFocus: el });
  }
  if (node === 'gateway') {
    const gw = s.arch?.gateway || {};
    const routed = (s.arch?.items || []).filter(i => i.via_gateway);
    const used = s.events.filter(e => routed.some(r => e.node === r.id || e.node?.startsWith(r.id + ':')) && ['response', 'error'].includes(e.kind));
    return openInspector({ runId: s.runId, node, title: 'IndiaMART gateway', events: used, returnFocus: el,
      extra: { node, result: { label: 'IndiaMART gateway', status: 'ok', origin: gw.enabled ? 'live_api' : 'off', ms: 0,
        response: { enabled: !!gw.enabled, base_url: gw.base || null, routed_apis: routed.map(r => ({ api: r.id, mode: r.mode, reachable: r.reachable })),
          this_run: used.map(e => ({ node: e.node, origin: e.origin, status: e.status, ms: e.ms, detail: e.detail })) } },
        events: used, note: 'IndiaMART-internal hosts do not resolve from this server; a pass-through gateway forwards the original path. Change its URL in Admin → APIs → Gateway.' } });
  }
  openInspector({ runId: s.runId, node, events: s.events, returnFocus: el });
}

async function onSubmit(s, e) {
  if (e.target.id === 'composer') { e.preventDefault(); const input = $('#say-input'); const text = input.value; input.value = ''; await sendTurn(s, text); return; }
  if (e.target.id === 'meeting-form') {
    e.preventDefault();
    const f = new FormData(e.target);
    $('#m-error').textContent = '';
    try {
      s.meeting = await api('/api/v5/meetings', { method: 'POST', body: { run_id: s.runId, call_id: s.call?.id, mode: f.get('mode'), address_kind: f.get('address_kind') || null, address_text: f.get('address_text') || null, start: f.get('start'), note: '' } });
      renderCall(s);
      $('#meeting')?.scrollIntoView({ block: 'center' });
      toast('Test meeting confirmed — link and calendar file ready');
    } catch (err) { $('#m-error').textContent = err.message; $('#m-start')?.focus(); }
    return;
  }
  if (e.target.classList.contains('edit-factor')) {
    e.preventDefault();
    const f = new FormData(e.target), factor = e.target.dataset.factor, mode = e.submitter?.value || 'suggest';
    let value = f.get('value');
    if (/^-?\d+(\.\d+)?$/.test(value)) value = Number(value);
    try {
      await api(`/api/v5/persona/${encodeURIComponent(s.run.seller_id)}/${mode}`, { method: 'POST', body: { factor, value, reason: f.get('reason') || '' } });
      if (mode === 'apply') { s.run.persona.factors[factor] = { ...s.run.persona.factors[factor], value, origin: 'operator', strength: 'strong', reason: f.get('reason') || 'Applied by PM' }; renderPersona(s); }
      toast(mode === 'apply' ? `${titleCase(factor)} updated — written to the persona index with history` : 'Suggestion saved to the persona index');
    } catch (err) { toast(err.message, 'error'); }
  }
}

function onChange() { /* reserved for future controls */ }
