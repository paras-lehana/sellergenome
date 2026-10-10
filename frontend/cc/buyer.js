// Buyer call workspace (/buyer/call/{id}): Buyer-side VANI answering a missed call.
import { api, session, BASE } from './api.js';
import { $, $$, esc, fmtMs, initials, origin, titleCase, toast, busy } from './ui.js';
import { closeInspector, openInspector } from './inspector.js';
import { highlight } from './json.js';
import { bindPicker, catalogue, effective, pickerHtml } from './engines.js';
import { agentBarHtml, agentBlocked, agentInfo, agentLine, chooseAgent, chosenApp, currentAgent } from './agent.js';
import { playToEnd, prestartedOr, RealtimeListener, recordClip, speakLine, speakOpts, transcribe } from './voice.js';

// Speech on the buyer desk (5.3): VANI's replies can be heard and the buyer can talk — engines per Admin, overridable here.
const ENGINE_KINDS = ['tts_model', 'tts_voice', 'tts_transport', 'listen', 'stt_realtime_model', 'stt_model', 'stt_mode'];
const VOICE_KEY = 'sg-buyer-voice';
const voicePref = () => { try { return localStorage.getItem(VOICE_KEY) !== 'off'; } catch { return true; } };

const FIELDS = [['product', 'Product'], ['quantity', 'Quantity'], ['specification', 'Specifications'], ['name', 'Name'], ['city', 'City'], ['state', 'State'], ['transfer', 'Transfer consent']];
const TOOLS = [
  ['pns', 'Missed PNS call', 'The buyer called a seller’s masked number; nobody picked up, so the call came to the Help Desk.'],
  ['vani', 'VANI · Sarvam', 'Each turn: the call’s policy (your buyer-requirement prompt, your original production prompt or the compact policy) runs on sarvam-105b-conversations; code enforces the ask limits.'],
  ['product_context', 'Tool · product context', 'requested_another_product_tool — units and specification options for the confirmed product.'],
  ['seller_by_city', 'Tool · seller by city', 'update_ast_buy_variables_by_city_tool — a live alternate seller near the buyer.'],
  ['assisted_buy', 'Tool · assisted buy', 'ast_buy_confirmed_tool — transfer after explicit consent (test only).'],
  ['post_lead', 'Buy lead · post', 'Test submission when there is no transfer — never written to production.'],
  ['whatsapp', 'Sellers’ link · WhatsApp', 'Requirement policy: the close promises suitable sellers’ links on WhatsApp — test only, no message is sent.'],
];
const POLICY_LABEL = { original: 'Your original VANI prompt', compact: 'Compact VANI policy', requirement: 'Your VANI buyer-requirement prompt' };

export async function mount(root, { params }) {
  const s = { id: params[0], call: null, busy: false, voice: voicePref(), audio: null, listener: null, recorder: null, cat: null, eng: null, pending: '', alive: true,
    agent: null, va: null, vaLines: [], vaState: 'idle', vaChat: null, vaReplies: [], seenAdapt: 0 };
  root.innerHTML = '<div class="page"><div class="skeleton" style="height:300px"></div></div>';
  busy('nav', true, 'Talk as Buyer · connecting VANI…');
  try { [s.call, s.cat, s.agent] = await Promise.all([api(`/api/v5/buyer/calls/${encodeURIComponent(s.id)}`), catalogue().catch(() => null), api('/api/v5/sarvam-buyer').catch(() => null)]); }
  catch (err) { busy('nav', false); root.innerHTML = `<div class="page"><h1>Buyer call not found</h1><p class="muted">${esc(err.message)}</p><a class="btn" href="${BASE}/buyer" data-nav>Back to the buyer desk</a></div>`; return {}; }
  busy('nav', false);
  if (s.cat) s.eng = effective(s.cat, 'buyer');
  agentInfo().then(d => { s.agentInfo = d; if (s.alive) render(root, s); }).catch(() => {});
  s.seenAdapt = (s.call.adaptations || []).length;
  render(root, s);
  speakLast(s);  // the opening line, when voice is on
  const unbind = bindPicker(root, 'buyer', () => { s.eng = effective(s.cat, 'buyer'); render(root, s); });

  async function send(text, via = 'typed') {
    if (!text.trim()) return;
    if (s.busy) { s.pending = `${s.pending} ${text}`.trim(); return; }
    s.busy = true;
    $('#b-transcript')?.insertAdjacentHTML('beforeend', `<div class="bubble buyer"><span class="who buyer">Buyer</span> ${esc(text)}${via !== 'typed' ? `<div class="meta"><span>${esc(via)}</span></div>` : ''}</div><div class="sysline held">VANI is thinking…</div>`);
    if (!s.saidAt) s.saidAt = performance.now();
    try {
      const out = await api(`/api/v5/buyer/calls/${s.id}/turn`, { method: 'POST', body: { text, speak: s.voice ? speakOpts(s.eng) : null } });
      const before = (s.call.adaptations || []).length;
      s.call = out.call; s.lastGuard = out.guard; s.prestarted = (out.speech || [])[0] || null;
      const after = s.call.adaptations || [];
      if (after.length > before) toast(after.at(-1).text, 'info', 3200);
    }
    catch (err) { toast(err.message, 'error'); }
    finally {
      s.busy = false;
      if (!s.alive) return;
      render(root, s);
      if (!s.listener) $('#b-input')?.focus();
      speakLast(s);
      if (s.pending) { const next = s.pending; s.pending = ''; setTimeout(() => send(next, 'voice · live'), 0); }
    }
  }

  root.addEventListener('submit', async e => {
    if (e.target.id === 'va-chat') { e.preventDefault(); await agentChat(root, s); return; }
    if (e.target.id !== 'b-composer') return;
    e.preventDefault();
    const input = $('#b-input'); const text = input.value.trim(); if (!text) return;
    input.value = '';
    await send(text);
  });
  // Agent switch (owner, 2026-10-10): built-in VANI or ANY agent of the Sarvam workspace, seamlessly mid-call.
  root.addEventListener('change', async e => {
    const a = e.target.closest('[data-agent-select]');
    if (!a || currentAgent(s.cat, s.agentInfo, 'buyer') === a.value) return;
    const wasLive = !!(s.listener || s.va);
    if (s.va) await agentCall(root, s, $('[data-act="va-call"]'));   // ends the current Sarvam call
    if (s.listener) { s.listener.stop(); s.listener = null; }
    chooseAgent('buyer', a.value);
    s.eng = effective(s.cat, 'buyer');
    render(root, s);
    toast(`Agent: ${agentLine(s.cat, s.agentInfo, 'buyer')}`);
    if (a.value !== 'builtin') {
      const card = $('[data-act="va-call"]');
      card?.closest('section, .card')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      if (wasLive && card && !s.va) await agentCall(root, s, card);
    } else if (wasLive) $('[data-act="b-mic"]')?.click();
  });
  root.addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.pane) {
      $$('.pane-tabs .tab').forEach(x => x.setAttribute('aria-selected', String(x === b)));
      $$('.panes > .pane').forEach(p => p.classList.toggle('active', p.id === `b-${b.dataset.pane}`));
    }
    if (b.dataset.act === 'b-voice') {
      s.voice = !s.voice;
      try { localStorage.setItem(VOICE_KEY, s.voice ? 'on' : 'off'); } catch { /* storage blocked */ }
      if (!s.voice) s.audio?.pause(); else speakLast(s, true);
      render(root, s);
    }
    if (b.dataset.act === 'b-mic') { if (s.va) { toast('End the Sarvam call first — it is using the microphone.', 'error'); return; } await listen(root, s, b, send); }
    if (b.dataset.act === 'va-call') await agentCall(root, s, b);
    if (b.dataset.tool) {
      const used = s.call.tools.filter(t => t.tool === b.dataset.tool);
      const meta = TOOLS.find(t => t[0] === b.dataset.tool);
      openInspector({ node: b.dataset.tool, title: meta[1], returnFocus: b, extra: { node: b.dataset.tool, result: used.length ? { label: meta[1], status: 'ok', origin: used[0].origin, ms: used[0].ms, response: used.map(u => u.result), note: meta[2] } : { label: meta[1], status: 'skipped', origin: 'off', note: `${meta[2]} Not called yet in this conversation.`, response: null }, events: [] } });
    }
    if (b.dataset.act === 'prompt') {
      try {
        const out = await api(`/api/v5/buyer/prompt?flow=${encodeURIComponent(s.call.flow)}`);
        openInspector({ node: 'prompt', title: `Original VANI prompt · ${titleCase(out.flow)}`, returnFocus: b, extra: { node: 'prompt', result: { label: 'Original prompt (rendered)', status: 'ok', origin: 'supplied_export', note: `Lines ${out.lines.join('–')}: ${out.source_chars.toLocaleString()} characters → ${out.rendered_chars.toLocaleString()} after rendering its Jinja conditions (~${out.approx_tokens.toLocaleString()} tokens). Preview below.`, response: { rendered_preview: out.rendered_preview } }, events: [] } });
      } catch (err) { toast(err.message, 'error'); }
    }
    if (b.dataset.act === 'original') {
      try {
        const call = await api('/api/v5/buyer/calls', { method: 'POST', body: { tile_id: s.call.tile_id, shown: s.call.shown || 3, policy: 'original' } });
        location.assign(`${BASE}/buyer/call/${call.id}`);
      } catch (err) { toast(err.message, 'error'); }
    }
  });
  return { destroy() { s.alive = false; unbind(); s.audio?.pause(); s.listener?.stop(); s.va?.stop(); s.recorder?.state === 'recording' && s.recorder.stop(); closeInspector(); } };
}

async function speakLast(s, force = false) {
  if (!s.voice || !s.alive) return;
  const last = [...s.call.transcript].reverse().find(t => t.who === 'bot');
  if (!last || (!force && last === s.spokenLast) || last.text === s.spokenText) return;
  s.spokenLast = last; s.spokenText = last.text;
  try {
    const pre = s.prestarted; s.prestarted = null;
    const { audio, out, firstSound } = await prestartedOr(pre, () => speakLine({ text: last.text.slice(0, 600), language: s.call.language || 'hi-IN', speaker: 'priya', pace: 1, eng: s.eng }));
    if (!s.alive || !s.voice) return;
    s.audio?.pause(); s.audio = audio; s.lastBot = `${last.text} ${out.spoken_text || ''}`;
    const said = s.saidAt;
    firstSound.then(ms => {
      if (!ms || !said) return;
      s.saidAt = 0;
      const t = ((performance.now() - said) / 1000).toFixed(1);
      $$('#b-transcript .bubble.bot').pop()?.insertAdjacentHTML('beforeend', `<div class="meta"><span class="reply-time" title="From the end of the buyer's turn to VANI's first sound">answered in ${t} s</span></div>`);
    });
    s.speaking = true;
    await playToEnd(audio).catch(() => toast('Tap 🔊 once — the browser blocked autoplay.', 'error'));
  } catch (err) { if (s.alive) toast(`Voice: ${err.message}`, 'error'); }
  finally { s.speaking = false; }
}

const words = x => String(x || '').toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s]/gu, ' ').split(/\s+/).filter(w => w.length > 1);
const isEcho = (text, bot) => { if (!bot) return false; const said = new Set(words(bot)), heard = words(text); return heard.length > 0 && heard.filter(w => said.has(w)).length / heard.length >= 0.6; };

async function listen(root, s, button, send) {
  if (s.listener) { s.listener.stop(); s.listener = null; render(root, s); return; }
  if (s.recorder && s.recorder.state === 'recording') { s.recorder.stop(); return; }
  const eng = s.eng || {};
  if (eng.listen === 'realtime') {
    const l = new RealtimeListener({ language: s.call.language || 'hi-IN', eng, place: 'buyer',
      onState: st => { if (st === 'closed' && s.listener === l) { s.listener = null; if (s.alive) render(root, s); } },
      onPartial: text => { const i = $('#b-input'); if (i) i.placeholder = `🎙 ${text}`; if (s.speaking && text.split(/\s+/).length >= 2 && !isEcho(text, s.lastBot)) s.audio?.pause(); },
      onFinal: (text, _lang, lastVoiceAt) => { if (isEcho(text, s.lastBot)) return; s.audio?.pause(); s.saidAt = lastVoiceAt || performance.now(); send(text, 'voice · live'); },
      onError: msg => toast(msg, 'error') });
    s.listener = l; render(root, s);
    try { const info = await l.start(); toast(`Listening live (${info.model} · ${info.mode}) — a short pause ends your turn.`); }
    catch (err) { s.listener = null; l.cleanup(); toast(err.message, 'error'); render(root, s); }
    return;
  }
  try {
    const { recorder, done } = await recordClip();
    s.recorder = recorder; button.textContent = '■ Stop (max 20 s)';
    const blob = await done; button.textContent = '… transcribing';
    const out = await transcribe(blob, eng, s.call.language || 'unknown');
    toast(`Heard (${out.model} · ${fmtMs(out.ms)}): ${out.text}`);
    await send(out.text, 'voice · clip');
  } catch (err) { toast(err.message, 'error'); }
  finally { s.recorder = null; if (s.alive) render(root, s); }
}

// ---------------------------------------------------------------- VANI on Sarvam (separate voice agent, 5.7)
function agentPanelHtml(s) {
  const a = s.agent || {}, live = a.live || {};
  const app = chosenApp(s.cat, s.agentInfo, 'buyer');
  const ext = !!app && app !== a.agent?.id;   // another agent of the workspace (Manjula, Payal 6.0 …)
  const built = ext || !!a.agent?.id;
  const name = ext ? ((s.agentInfo?.workspace_agents || []).find(x => x.app_id === app)?.name || app) : 'VANI';
  const short = name.split(/\s+[·(]/)[0];
  return `<section class="card agent-live" aria-label="${esc(name)} on Sarvam Voice Agents">
    <div class="card-head"><h3>${esc(name)} · Sarvam Voice Agent</h3><span class="aside">${origin(built ? 'live_api' : 'off', built ? `Sarvam${a.version ? ` · v${a.version}` : ''}` : 'not built')} <span class="chip" id="va-state">${esc(s.vaState)}</span></span></div>
    <div class="card-body" style="display:grid;gap:8px">
      <p class="dim" style="font-size:12.5px">The same buyer-requirement flow also runs as its own agent on Sarvam’s platform, built through the Sarvam Voice MCP. Call it by voice (Sarvam listens, handles interruptions and turn-taking) or type to it. It loads this call’s product and seller itself.${session.operator && a.links?.agent ? ` <a href="${esc(a.links.agent)}" target="_blank" rel="noopener">Open the agent in Sarvam ↗</a>` : ''}</p>
      ${built ? '' : '<p class="note">Not built yet — Admin → Sarvam voice agent → Build VANI on Sarvam.</p>'}
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <button class="btn ${s.va ? '' : 'btn-talk'}" data-act="va-call" ${(live.available || ext) && !agentBlocked(s.cat, s.agentInfo, 'buyer') || s.va ? '' : 'disabled'}>${s.va ? '■ End call' : `📞 Call ${esc(short)} on Sarvam (voice)`}</button>
        <span class="dim" id="va-meta" style="font-size:11.5px">${esc(agentBlocked(s.cat, s.agentInfo, 'buyer') || (built && !live.available && !ext ? live.reason || '' : ''))}</span></div>
      <form class="composer" id="va-chat"><label class="sr" for="va-input">Type to VANI on Sarvam</label>
        <input id="va-input" maxlength="500" placeholder="Or type as the buyer to VANI on Sarvam" ${built ? '' : 'disabled'}><button class="btn" ${built ? '' : 'disabled'}>Send</button></form>
      <div class="transcript" id="va-lines" aria-live="polite">${agentLinesHtml(s)}</div>
      <p class="meta" id="va-replies" style="font-size:12px">${agentRepliesHtml(s)}</p>
    </div></section>`;
}

const agentLinesHtml = s => s.vaLines.map(l => `<div class="bubble ${l.who === 'bot' ? 'bot' : 'buyer'}"><span class="who ${l.who === 'bot' ? 'bot' : 'buyer'}">${l.who === 'bot' ? 'VANI · Sarvam' : 'You'}</span> ${esc(l.text)}${l.meta ? `<div class="meta"><span>${esc(l.meta)}</span></div>` : ''}</div>`).join('')
  || '<p class="dim" style="font-size:12px">Press “Call VANI on Sarvam”, allow the microphone and answer as the buyer — or type a first line.</p>';
const agentRepliesHtml = s => (s.vaReplies.length ? `VANI answered in ${s.vaReplies.map(ms => `<span class="reply-time">${(ms / 1000).toFixed(1)} s</span>`).join(' · ')} <span class="dim">(from your last sound to its first sound)</span>` : '');

function paintAgent(s) {
  const lines = $('#va-lines'); if (lines) { lines.innerHTML = agentLinesHtml(s); lines.scrollTop = lines.scrollHeight; }
  const st = $('#va-state'); if (st) st.textContent = s.vaState;
  const rt = $('#va-replies'); if (rt) rt.innerHTML = agentRepliesHtml(s);
}

async function agentCall(root, s, button) {
  if (s.va) { button.disabled = true; await s.va.stop(); return; }
  if (s.listener) { s.listener.stop(); s.listener = null; }
  s.audio?.pause();
  s.vaLines = []; s.vaReplies = []; s.vaState = 'connecting'; button.disabled = true; paintAgent(s);
  try {
    const { startPayalCall } = await import('./payal-live.js');
    s.va = await startPayalCall({ agent: 'buyer', buyerCallId: s.call.id, appId: chosenApp(s.cat, s.agentInfo, 'buyer'),
      onLine: line => { s.vaLines.push({ ...line, meta: line.opening ? 'opening line — Sarvam speaks it in the call language' : '' }); paintAgent(s); },
      onState: state => { s.vaState = state; paintAgent(s); },
      onReply: ms => { s.vaReplies = [...s.vaReplies, ms].slice(-8); paintAgent(s); },
      onEnd: () => { s.va = null; s.vaState = 'ended'; if (s.alive) render(root, s); } });
    toast('VANI on Sarvam is on the line — speak as the buyer.');
  } catch (err) { s.va = null; s.vaState = 'idle'; toast(err.message, 'error'); }
  if (s.alive) render(root, s);
}

async function agentChat(root, s) {
  const input = $('#va-input'); const text = input?.value.trim(); if (!text || s.vaBusy) return;
  input.value = ''; s.vaBusy = true;
  s.vaLines.push({ who: 'buyer', text, meta: 'typed' }); s.vaState = 'chat'; paintAgent(s);
  try {
    const out = await api('/api/v5/sarvam-buyer/chat', { method: 'POST', body: { buyer_call_id: s.call.id, text, interaction_id: s.vaChat } });
    s.vaChat = out.interaction_id;
    s.vaLines.push({ who: 'bot', text: out.reply || '…', meta: `Sarvam send_chat · ${fmtMs(out.ms)}` });
  } catch (err) { toast(err.message, 'error'); }
  finally { s.vaBusy = false; paintAgent(s); $('#va-input')?.focus(); }
}

// Owner, 2026-10-10: changes in how the bot handles the person should be visible — VANI's adaptations (language
// switch, ask limit reached, product changed) show as a banner that flashes once, like the seller side's persona switch.
function adaptBannerHtml(s) {
  const list = s.call.adaptations || [];
  const a = list.at(-1);
  if (!a) return '';
  const flash = list.length > s.seenAdapt;
  s.seenAdapt = list.length;
  return `<div class="switch-banner ${flash ? 'flash' : ''}" role="status" aria-live="polite"><span class="eyebrow">VANI adapted · ${list.length}</span><span>${esc(a.text)}</span></div>`;
}

function requirementRows(c) {
  const lead = c.lead, specs = lead.specs || {};
  const names = [...new Set([...(c.spec_names || []), ...Object.keys(specs)])];
  return [['Product', lead.product ? `${lead.product}${c.product_confirmed ? '' : ' · not confirmed yet'}` : '', 'product', 4],
    ['Number of pieces', [lead.quantity, lead.quantity_unit].filter(Boolean).join(' '), 'quantity', 3],
    ...names.map(n => [n, specs[n] || '', `spec:${n}`, 2]),
    ['Additional details', lead.additional_details || '', null, null]];
}

function render(root, s) {
  s.engOpen = root.querySelector('[data-eng="buyer"]')?.open || false;
  const c = s.call, lead = c.lead;
  const last = [...c.transcript].reverse().find(t => t.who === 'bot');
  const value = k => k === 'specification' ? (Object.entries(lead.specs || {}).map(([a, b]) => `${a}: ${b}`).join(', ') || '') : k === 'transfer' ? (c.consent ? 'Given' : c.available_seller ? 'Asked' : '') : (lead[k] || '');
  root.innerHTML = `
  <div class="call">
    <header class="call-head">
      <div class="avatar" aria-hidden="true">${esc(initials(lead.name || 'Buyer'))}</div>
      <div class="ident"><h1>${esc(lead.product || 'New requirement')}</h1><span>${esc(lead.name || 'Buyer')} · missed ${esc(c.missed_seller || 'seller')} · ${esc(titleCase(c.flow))} · ${esc(c.language)}</span></div>
      <span class="chip ${c.policy !== 'compact' ? 'solid' : ''}">${esc(POLICY_LABEL[c.policy] || c.policy)}</span>
      <span class="onair">${c.status === 'transferred' ? 'TRANSFERRED' : c.status === 'ended' ? 'ENDED' : 'ON CALL'}</span>
      <div class="actions">${session.operator ? `<button class="btn btn-sm" data-act="prompt">View original prompt</button>${c.policy !== 'original' && c.tile_id && !c.talk ? '<button class="btn btn-sm" data-act="original">Re-run with original prompt</button>' : ''}` : '<span class="dim" style="font-size:12px">Sign in to run your original production prompt</span>'}
        <a class="btn btn-sm btn-ghost" href="${BASE}/buyer" data-nav>Leave call</a></div>
      ${s.cat && s.agentInfo ? agentBarHtml(s.cat, s.agentInfo, { place: 'buyer' }) : ''}
    </header>
    <div>
      <div class="pane-tabs tabs" role="tablist"><button class="tab" role="tab" data-pane="lead" aria-selected="false">Lead</button><button class="tab" role="tab" data-pane="talk" aria-selected="true">Call</button><button class="tab" role="tab" data-pane="flow" aria-selected="false">Architecture</button></div>
      <div class="panes" style="height:calc(100dvh - var(--topbar) - 64px)">
        <section class="pane" id="b-lead" aria-label="Lead card">
          <h2 class="pane-title">Lead card · ask limits enforced in code</h2>
          <div class="card"><div class="card-body" style="padding-top:12px"><table class="table"><thead><tr><th>Field</th><th>Value</th><th>Asks</th></tr></thead><tbody>${c.policy === 'requirement'
            ? requirementRows(c).map(([label, val, key, lim]) => { const used = key ? (c.attempts[key] ?? 0) : null, closed = key && c.closed.includes(key);
              return `<tr><td>${esc(label)}</td><td>${val ? esc(val) : `<span class="dim">${closed ? 'left to the seller' : 'pending'}</span>`}</td><td class="mono">${key ? `${used}/${lim}${closed ? ' ✕' : ''}` : ''}</td></tr>`; }).join('')
            : FIELDS.map(([k, l]) => {
            const lim = c.limits[k] ?? 3, used = c.attempts[k] ?? 0, closed = c.closed.includes(k);
            return `<tr><td>${esc(l)}</td><td>${value(k) ? esc(value(k)) : `<span class="dim">${closed ? 'deferred to seller' : 'pending'}</span>`}</td><td class="mono">${used}/${lim}${closed ? ' ✕' : ''}</td></tr>`;
          }).join('')}</tbody></table></div></div>
          ${c.available_seller ? `<div class="card"><div class="card-head"><h3>Live alternate seller</h3>${origin('mock')}</div><div class="card-body" style="font-size:13px">${esc(c.available_seller.name)} · ${esc(c.available_seller.city)} · rating ${esc(c.available_seller.rating)} · replies ${esc(c.available_seller.response_rate)}</div></div>` : ''}
          <div class="card"><div class="card-head"><h3>Disposition</h3></div><div class="card-body" style="font-size:13px">${esc(c.disposition || 'Not decided yet')}${c.outcome ? ` · outcome ${esc(c.outcome)}` : ''} · status ${esc(c.status)}</div></div>
          ${c.sarvam?.requirement ? `<div class="card"><div class="card-head"><h3>Requirement from VANI on Sarvam</h3>${origin('live_api', 'Sarvam')}</div><div class="card-body" style="font-size:12.5px">${Object.entries(c.sarvam.requirement).filter(([k, v]) => v && !['at', 'buyer_call_id'].includes(k)).map(([k, v]) => `<div><b>${esc(titleCase(k))}:</b> ${esc(v)}</div>`).join('')}</div></div>` : ''}
          ${s.lastGuard?.notes?.length ? `<div class="note" style="font-size:12.5px"><b>Guard rails this turn:</b><br>${s.lastGuard.notes.map(esc).join('<br>')}</div>` : ''}
        </section>
        <section class="pane active" id="b-talk" aria-label="Conversation">
          <h2 class="pane-title">${c.talk ? 'Talk as Buyer · VANI answers your missed call' : 'You are the buyer · VANI answers'}</h2>
          ${c.talk ? `<p class="note talk-note" role="note"><b>Talk as Buyer.</b> You called ${esc(c.missed_seller || 'a seller')}, nobody picked up, and VANI answered. Reply as the buyer — type or use the microphone. VANI notes the product, the number of pieces and the specifications, then closes with the sellers’ link on WhatsApp.</p>` : ''}
          ${adaptBannerHtml(s)}
          ${s.cat ? pickerHtml(s.cat, 'buyer', ENGINE_KINDS, { open: s.engOpen }) : ''}
          <section class="prompter"><p class="eyebrow">VANI said</p><p class="say">${esc(last?.text || '')}</p>
            ${last?.intents?.length ? `<div class="why">${last.intents.map(i => `<span class="chip">${esc(i)}</span>`).join('')}</div>` : ''}
            ${last?.ms ? `<span class="dim" style="font-size:11.5px">Sarvam ${esc(fmtMs(last.ms))} · ${esc(last.policy || '')}</span>` : ''}</section>
          <section class="card"><div class="card-head"><h3>Transcript</h3><span class="aside" style="display:flex;gap:6px">
            <button class="btn btn-sm voice-btn" data-act="b-voice" aria-pressed="${s.voice}">${s.voice ? '🔊 Voice on' : '🔇 Voice off'}</button>
            <button class="btn btn-sm ${s.listener ? 'live' : ''}" data-act="b-mic" aria-pressed="${!!s.listener}" ${['transferred', 'ended'].includes(c.status) ? 'disabled' : ''}>${s.listener ? '■ Stop listening' : s.eng?.listen === 'realtime' ? '🎙 Talk live' : '🎙 Speak as buyer'}</button></span></div><div class="card-body">
            <div class="transcript" id="b-transcript">${c.transcript.map(t => t.who === 'system' ? `<div class="sysline">${esc(t.text)}</div>` : `<div class="bubble ${t.who === 'bot' ? 'bot' : 'buyer'}"><span class="who ${t.who === 'bot' ? 'bot' : 'buyer'}">${t.who === 'bot' ? 'VANI' : 'Buyer'}</span> ${esc(t.text)}${t.gloss ? `<span class="gloss">${esc(t.gloss)}</span>` : ''}${t.guard?.length ? `<div class="meta">${t.guard.map(g => `<span>⚑ ${esc(g)}</span>`).join('')}</div>` : ''}${t.scripted ? '<div class="meta"><span>scripted opening</span></div>' : ''}</div>`).join('')}</div>
            <form class="composer" id="b-composer" style="margin-top:10px"><label class="sr" for="b-input">Say something as the buyer</label>
              <input id="b-input" maxlength="600" placeholder="${c.policy === 'requirement' ? 'Reply as the buyer — e.g. “Haan ji”, “Pachaas piece chahiye”, “10 HP”' : 'Reply as the buyer — e.g. “Do chahiye”, “Pune, Maharashtra”, “haan connect kar do”'}" ${['transferred', 'ended'].includes(c.status) ? 'disabled' : ''}>
              <button class="btn btn-primary" ${['transferred', 'ended'].includes(c.status) ? 'disabled' : ''}>Send</button></form>
          </div></section>
          ${agentPanelHtml(s)}
        </section>
        <section class="pane" id="b-flow" aria-label="Architecture">
          <h2 class="pane-title">Architecture · this call</h2>
          ${TOOLS.map(([k, label, help]) => {
            const used = k === 'pns' || k === 'vani' || c.tools.some(t => t.tool === k);
            return `<button class="source" data-tool="${k}" style="grid-template-columns:auto 1fr auto;border:1px solid ${used ? 'var(--accent)' : 'var(--line)'}">${origin(k === 'vani' ? 'llm' : used ? 'mock' : 'off', k === 'vani' ? 'Sarvam' : used ? 'Called' : 'Idle')}<span><b>${esc(label)}</b><br><span class="dim">${esc(help)}</span></span><span class="mono dim">${esc(c.tools.filter(t => t.tool === k).length || '')}</span></button>`;
          }).join('')}
          <div class="card"><div class="card-head"><h3>Last tool result</h3></div><div class="card-body">${c.tools.length ? `<pre class="code wrap">${highlight(c.tools[c.tools.length - 1])}</pre>` : '<p class="dim">No tool called yet.</p>'}</div></div>
        </section>
      </div>
    </div>
  </div>`;
  const tr = $('#b-transcript'); if (tr) tr.scrollTop = tr.scrollHeight;
}
