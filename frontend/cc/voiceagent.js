// Payal on Sarvam's Voice Agents platform (built through the Sarvam Voice MCP) — shown at the top of /sarvam.
// Live voice (5.3): a browser call to Payal through Sarvam's conversational SDK — Sarvam does listening, interruptions and
// turn-taking; our server only signs the WebSocket URL. Live text chat runs against the committed agent (send_chat);
// replies can be spoken in the browser with the voice chosen in this page's Engines picker.
import { api, session } from './api.js';
import { $, esc, fmtMs, origin, toast } from './ui.js';
import { catalogue, effective } from './engines.js';
import { playToEnd, speakLine } from './voice.js';

// Which Voice MCP tools SellerGenome uses, and why the rest are not used (policy: no outbound dialling).
const USE = {
  configure_kb: 'Creates the two knowledge bases', kb_file_upload_begin: 'Uploads every playbook and seller brief',
  kb_file_upload_commit: 'Commits each upload and waits for ingestion', knowledge_base: 'Lists KBs and checks retrieval',
  configure_agent: 'Variables, languages, voice, KB binding, prompt, goal, nudges', agents: 'Finds Payal, picks the voice, commits versions',
  create_api_tool: 'Adds load_seller_context, book_meeting, report_outcome', configure_voicemail: 'Sets the voicemail message',
  configure_secret: 'Stores the tool key in the workspace', secrets: 'Reads the secret list (names only)',
  configure_test_suite: 'Creates the behaviour suite', configure_scenario: 'Adds busy-seller, price and are-you-a-bot scenarios',
  evals: 'Reads the suite and run results', run_eval: 'Runs the suite on Sarvam (signed in)', send_chat: 'Powers “Talk to Payal” below',
  analytics: 'Reads interaction traces', upload_agent_code: 'Not needed — API tools cover the context, booking and outcome calls',
  configure_app_tool: 'Not needed — create_api_tool writes the same rows', create_validator_tool: 'Not needed yet — no caller-typed values to validate',
  create_verifier_tool: 'Not needed — Payal holds no secrets to verify against',
};
const POLICY = new Set(['telephony', 'configure_deployment', 'configure_connection', 'configure_endpoint', 'verify_phone_number', 'place_test_call',
  'campaigns', 'configure_campaign', 'stream_cohort_users', 'upload_cohort']);
const DESTRUCTIVE = new Set(['delete_kb', 'delete_kb_file']);

export async function mountVoiceAgent(box) {
  const s = { chat: [], interaction: null, seller: 'syn-91000101', busy: false, speak: false, audio: null, cat: null, eng: null,
    call: null, callLines: [], callEvents: {}, callState: 'idle' };
  box.innerHTML = '<div class="skeleton" style="height:220px"></div>';
  try { [s.data, s.live, s.cat, s.buyer] = await Promise.all([api('/api/v5/sarvam-voice'), api('/api/v5/sarvam-live').catch(() => ({ available: false })), catalogue().catch(() => null), api('/api/v5/sarvam-buyer').catch(() => null)]); }
  catch (err) { box.innerHTML = `<p class="note">${esc(err.message)}</p>`; return; }
  render(s, box);
  box.addEventListener('click', e => onClick(s, box, e));
  box.addEventListener('submit', e => onSubmit(s, box, e));
  box.addEventListener('change', e => { if (e.target.id === 'va-seller') { s.seller = e.target.value; reset(s, box); } });
  return { destroy() { s.call?.stop(); s.audio?.pause(); } };
}

// The second agent (5.7): VANI, the buyer-requirement agent — tried on the buyer desk (voice or text) and in Sarvam.
function buyerAgentHtml(s) {
  const b = s.buyer || {};
  const built = b.agent?.id;
  return `<section class="card" aria-label="VANI buyer agent on Sarvam">
    <div class="card-head"><h3>VANI · buyer-requirement agent on Sarvam</h3><span class="aside">${origin(built ? 'live_api' : 'off', built ? `Sarvam${b.version ? ` · v${b.version}` : ''}` : 'not built')}</span></div>
    <div class="card-body" style="display:grid;gap:8px;font-size:12.5px">
      <p class="dim">A second, separate agent built through the same MCP: when a buyer's call to a seller is not picked up, VANI notes the product, the number of pieces and the specifications, then closes with the sellers' link on WhatsApp. Synthetic buyer-desk calls only.</p>
      <div style="display:flex;flex-wrap:wrap;gap:8px"><a class="btn btn-talk btn-sm" href="${document.body.dataset.base}/buyer" data-nav>Try VANI on the buyer desk →</a>${session.operator && b.links?.agent ? `<a class="btn btn-sm btn-ghost" href="${esc(b.links.agent)}" target="_blank" rel="noopener">Open VANI in Sarvam ↗</a>` : ''}</div>
    </div></section>`;
}

function liveHtml(s) {
  const l = s.live || {};
  return `<section class="card va-live" aria-label="Call Payal — live voice on Sarvam">
    <div class="card-head"><h3>Call Payal · live voice on Sarvam</h3><span class="aside">${origin(l.available ? 'live_api' : 'off', l.available ? 'Sarvam runtime' : 'unavailable')} <span class="chip" id="va-live-state">${esc(s.callState)}</span></span></div>
    <div class="card-body" style="display:grid;gap:8px">
      <p class="dim" style="font-size:12.5px">Full-duplex voice in your browser through Sarvam’s conversational SDK: Sarvam listens, handles interruptions and turn-taking, and Payal looks up the seller picked below (synthetic demo sellers only). The Voice Agents key stays on our server, which only hands the page a short-lived signed connection. Headphones avoid echo.</p>
      ${l.available ? '' : `<p class="note">${esc(l.reason || 'Live calls are not available right now.')}</p>`}
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><button class="btn ${s.call ? '' : 'btn-primary'}" type="button" data-va="live" ${l.available || s.call ? '' : 'disabled'}>${s.call ? '■ End call' : '📞 Call Payal'}</button><span class="dim" id="va-live-meta" style="font-size:11.5px"></span></div>
      <div class="transcript va-transcript" id="va-live-lines" aria-live="polite"></div>
      <div class="chips" id="va-live-events"></div>
      <p class="meta" id="va-live-replies" style="font-size:12px"></p>
    </div></section>`;
}

function paintLive(s, box) {
  const lines = $('#va-live-lines', box);
  if (lines) {
    lines.innerHTML = s.callLines.map(l => `<div class="bubble ${l.who === 'bot' ? 'bot' : 'seller'}"><span class="who ${l.who === 'bot' ? 'bot' : 'seller'}">${l.who === 'bot' ? 'Payal' : 'You'}</span> ${esc(l.text)}${l.opening ? '<div class="meta"><span>opening line — Sarvam speaks it in the call language</span></div>' : ''}</div>`).join('')
      || '<p class="dim" style="font-size:12px">Press “Call Payal”, allow the microphone, and answer as the seller. The conversation appears here.</p>';
    lines.scrollTop = lines.scrollHeight;
  }
  const ev = $('#va-live-events', box);
  if (ev) ev.innerHTML = Object.entries(s.callEvents).map(([k, n]) => `<span class="tagchip">${esc(k.replace(/_/g, ' '))} · ${n}</span>`).join('');
  const st = $('#va-live-state', box); if (st) st.textContent = s.callState;
  const meta = $('#va-live-meta', box);
  if (meta) meta.textContent = s.call ? `session ${s.call.session.session_id} · shown on the floor as a REAL CALL` : '';
  const rt = $('#va-live-replies', box);
  if (rt) rt.innerHTML = (s.callReplies || []).length ? `Payal answered in ${(s.callReplies || []).map(ms => `<span class="reply-time">${(ms / 1000).toFixed(1)} s</span>`).join(' · ')} <span class="dim">(from your last sound to her first sound)</span>` : '';
}

async function liveCall(s, box, b) {
  if (s.call) { b.disabled = true; await s.call.stop(); return; }
  s.callLines = []; s.callEvents = {}; s.callState = 'connecting'; s.callReplies = []; s.audio?.pause();
  b.disabled = true; $('#va-seller', box)?.setAttribute('disabled', '');
  paintLive(s, box);
  const rerender = () => { const sec = $('.va-live', box); if (sec) { sec.outerHTML = liveHtml(s); paintLive(s, box); } };
  try {
    const { startPayalCall } = await import('./payal-live.js');
    s.call = await startPayalCall({ sellerId: s.seller,
      onLine: line => { s.callLines.push(line); paintLive(s, box); },
      onEvent: kind => { if (kind && !['ping', 'audio_chunk', 'text_chunk'].includes(kind)) { s.callEvents[kind] = (s.callEvents[kind] || 0) + 1; paintLive(s, box); } },
      onState: state => { s.callState = state; paintLive(s, box); },
      onReply: ms => { s.callReplies = [...(s.callReplies || []), ms].slice(-8); paintLive(s, box); },
      onEnd: () => { s.call = null; s.callState = 'ended'; $('#va-seller', box)?.removeAttribute('disabled'); rerender(); } });
  } catch (err) { s.call = null; s.callState = 'idle'; $('#va-seller', box)?.removeAttribute('disabled'); toast(err.message, 'error'); }
  rerender();
}

function reset(s, box) {
  s.chat = []; s.interaction = null;
  const seller = s.data.demo_sellers.find(x => x.seller_id === s.seller);
  // Sarvam speaks the configured opening line first on a voice call; over text it is shown here so the chat reads naturally.
  s.chat.push({ who: 'payal', text: s.data.intro.replace(/\{\{seller_name\}\}/g, seller?.first_name || 'ji'), opening: true });
  renderChat(s, box);
}

function render(s, box) {
  const d = s.data;
  const built = d.agent?.id && d.version;
  box.innerHTML = `
    <section class="va card">
      <div class="card-head va-head"><div><p class="eyebrow">Sarvam Voice Agents platform · built through the Sarvam Voice MCP</p>
        <h2>Payal — IndiaMART's seller-outreach voice agent, running on Sarvam</h2></div>
        <div class="chips">${origin(d.connected ? 'live_api' : 'off', d.connected ? 'MCP connected' : 'MCP not connected')}
          ${built ? `<span class="tagchip">version <b>${esc(d.version)}</b> committed</span>` : '<span class="tagchip">not built yet</span>'}
          ${d.voice ? `<span class="tagchip">voice <b>${esc(d.voice)}</b></span>` : ''}${d.languages ? `<span class="tagchip">${esc(d.languages.length)} languages</span>` : ''}</div></div>
      <div class="card-body va-body">
        <ol class="va-flow" aria-label="How Payal works">
          <li><b>SellerGenome</b><span>persona engine picks the seller and what to say</span></li>
          <li><b>Sarvam knowledge bases</b><span>${esc(d.kb_docs?.length || 0)} docs: IndiaMART playbook + a brief per demo seller</span></li>
          <li><b>load_seller_context</b><span>on start, Payal calls our API and fills its variables</span></li>
          <li><b>Payal on Sarvam</b><span>Hinglish voice, 8 languages, retrieves from the KB before advising</span></li>
          <li><b>book_meeting</b><span>books a test meeting with a working link</span></li>
          <li><b>report_outcome</b><span>on end, the result comes back here</span></li>
          <li><b>Live voice</b><span>your browser ↔ Sarvam through the conversational SDK; the key stays on our server</span></li>
        </ol>
        ${d.links ? `<p class="va-links">Open on Sarvam (sign-in to the hackathon org): <a href="${esc(d.links.agent)}" target="_blank" rel="noopener">agent ↗</a>
          ${(d.links.kb || []).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">knowledge base ${i + 1} ↗</a>`).join(' ')}
          ${d.links.suite ? `<a href="${esc(d.links.suite)}" target="_blank" rel="noopener">eval suite ↗</a>` : ''}</p>` : ''}
        ${liveHtml(s)}
        ${buyerAgentHtml(s)}
        <div class="va-grid">
          <section class="va-chat card" aria-label="Talk to Payal">
            <div class="card-head"><h3>Talk to Payal · live on Sarvam (text)</h3>
              <label class="dim" style="font-size:12px;display:flex;gap:6px;align-items:center">Seller <select id="va-seller" style="width:auto">${(d.demo_sellers || []).map(x => `<option value="${esc(x.seller_id)}" ${x.seller_id === s.seller ? 'selected' : ''}>${esc(x.company)} · ${esc(x.city)}</option>`).join('')}</select></label></div>
            <div class="card-body"><div class="transcript va-transcript" id="va-transcript" aria-live="polite"></div>
              <form class="composer" id="va-form" style="margin-top:10px"><label class="sr" for="va-input">Reply as the seller</label>
                <input id="va-input" maxlength="500" placeholder="Reply as the seller — try “Haan boliye”, “kitne ka package hai?”, “kal 11 baje online”" ${built ? '' : 'disabled'}>
                <button class="btn btn-primary" ${built ? '' : 'disabled'}>Send</button></form>
              <div class="va-tools"><button class="btn btn-sm" type="button" data-va="speak" aria-pressed="${s.speak}">${s.speak ? '🔊 Speaking replies' : '🔇 Speak replies'}</button>
                <button class="btn btn-sm btn-ghost" type="button" data-va="reset">New conversation</button>
                <span class="dim" style="font-size:11.5px">Every reply is Payal on Sarvam's platform (send_chat). Replies are spoken in your browser with the voice chosen in this page’s Engines.</span></div>

            </div></section>
          <section class="card" aria-label="What Payal knows"><div class="card-head"><h3>What Payal knows and can do</h3></div><div class="card-body va-know">
            <p class="kicker">Knowledge bases (instead of Elasticsearch — Sarvam agents retrieve from these)</p>
            <div class="chips">${(d.kb_docs || []).map(k => `<span class="tagchip" title="${esc(k.kb)} · ${esc(k.chars)} chars">${esc(k.name.replace('.md', ''))}</span>`).join('')}</div>
            <p class="kicker">Tools on the agent</p>
            ${(d.tools || []).map(t => `<div class="va-row"><span class="mono">${esc(t.name)}</span><span class="tagchip">${esc(t.lifecycle)}</span></div>`).join('') || '<p class="dim">Built with the agent.</p>'}
            <p class="kicker">Variables in → out</p><p style="font-size:12px"><span class="mono">${esc((d.variables?.inputs || []).join(', '))}</span><br>→ <span class="mono">${esc((d.variables?.outputs || []).join(', '))}</span> <span class="dim">(filled from the transcript after the call)</span></p>
            ${d.suite ? `<p class="kicker">Eval suite on Sarvam · ${esc(d.suite.scenarios.length)} scenarios</p><ul class="va-list">${d.suite.scenarios.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
            ${session.operator && d.suite ? '<button class="btn btn-sm" type="button" data-va="eval">Run the suite on Sarvam</button>' : ''}
            ${(d.evals || []).length ? `<details><summary class="dim" style="font-size:12px">Eval runs (${esc(d.evals.length)})</summary><pre class="code wrap" style="max-height:200px">${esc(JSON.stringify(d.evals.slice(-2), null, 1))}</pre></details>` : ''}
            <details><summary class="dim" style="font-size:12px">The prompt sent to Sarvam (${esc(d.prompt_chars)} chars, English source; Sarvam translates)</summary><pre class="code wrap" style="max-height:320px">${esc(d.prompt || '')}</pre></details>
          </div></section>
        </div>
        <section class="card"><div class="card-head"><h3>What Payal's tools sent back here</h3><span class="aside dim" style="font-size:11.5px">our API, called by Sarvam</span></div>
          <div class="card-body" id="va-calls">${callsHtml(d)}</div></section>
        <details class="card"><summary class="card-head"><h3>Sarvam Voice MCP · ${esc((d.mcp_tools || []).length)} tools and what SellerGenome does with each</h3></summary>
          <div class="card-body"><div class="scroll-x"><table class="table"><thead><tr><th>Tool</th><th>Use in SellerGenome</th></tr></thead><tbody>
          ${(d.mcp_tools || []).map(t => `<tr><td class="mono">${esc(t.name)}</td><td>${USE[t.name] ? esc(USE[t.name]) : POLICY.has(t.name) ? '<span class="dim">Not used by policy — no outbound dialling, campaigns or deployments to real numbers</span>' : DESTRUCTIVE.has(t.name) ? '<span class="dim">Not used — deleting needs the owner’s confirmation</span>' : t.name.includes('board') ? '<span class="dim">Available — dashboards stay in SellerGenome and Kibana for now</span>' : '<span class="dim">Available</span>'}</td></tr>`).join('')}
          </tbody></table></div></div></details>
      </div></section>`;
  if (!s.chat.length) reset(s, box); else renderChat(s, box);
  paintLive(s, box);
}

function callsHtml(d) {
  const calls = (d.calls || []).slice(-8).reverse();
  if (!calls.length) return '<p class="dim">Nothing yet — start a conversation.</p>';
  const word = { start: 'Context loaded', meeting: 'Meeting booked', end: 'Call ended' };
  return calls.map(c => `<div class="ev-row">${origin('live_api', 'Sarvam → us')}<span class="tagchip">${esc(word[c.kind] || c.kind)}</span><span class="ev-main">${esc(c.company || c.seller_id || '')}${c.slot ? ` · ${esc(c.slot)} · ${esc(c.mode)}` : ''}${c.link ? ` · <a href="${esc(c.link)}" target="_blank" rel="noopener">meeting link ↗</a>` : ''}</span><span class="mono dim ev-at">${esc(String(c.at || '').slice(11, 19))} UTC</span></div>`).join('');
}

function renderChat(s, box) {
  const t = $('#va-transcript', box);
  if (!t) return;
  t.innerHTML = s.chat.map(m => `<div class="bubble ${m.who === 'payal' ? 'bot' : 'seller'}"><span class="who ${m.who === 'payal' ? 'bot' : 'seller'}">${m.who === 'payal' ? 'Payal' : 'Seller'}</span> ${esc(m.text)}
    <div class="meta">${m.opening ? '<span>opening line configured on Sarvam (spoken first on a call)</span>' : ''}${m.ms ? `<span>Sarvam ${esc(fmtMs(m.ms))}</span>` : ''}${m.pending ? '<span>Payal is replying on Sarvam…</span>' : ''}</div></div>`).join('');
  t.scrollTop = t.scrollHeight;
}

async function speak(s, text) {
  if (!s.speak || !text) return;
  try {
    // "priya" is Payal's persona voice; with Bulbul v4 it maps to the Hinglish customer voice closest to Sarvam's Ishita.
    const eng = s.cat ? effective(s.cat, 'sarvam') : null;  // read at speak time: the page's Engines picker may have changed
    const { audio } = await speakLine({ text: text.slice(0, 600), language: 'hi-IN', speaker: 'priya', pace: 1, eng });
    s.audio?.pause();
    s.audio = audio;
    await playToEnd(audio).catch(() => {});
  } catch (err) { toast(`Voice: ${err.message}`, 'error'); }
}

async function onSubmit(s, box, e) {
  if (e.target.id !== 'va-form') return;
  e.preventDefault();
  const input = $('#va-input', box);
  const text = input.value.trim();
  if (!text || s.busy) return;
  input.value = '';
  s.busy = true;
  s.chat.push({ who: 'seller', text }, { who: 'payal', text: '…', pending: true });
  renderChat(s, box);
  try {
    const out = await api('/api/v5/sarvam-voice/chat', { method: 'POST', body: { seller_id: s.seller, text, interaction_id: s.interaction } });
    s.interaction = out.interaction_id;
    s.chat[s.chat.length - 1] = { who: 'payal', text: out.reply || '(no reply)', ms: out.ms };
    renderChat(s, box);
    speak(s, out.reply);
    try { s.data = await api('/api/v5/sarvam-voice'); $('#va-calls', box).innerHTML = callsHtml(s.data); } catch { /* status refresh is best effort */ }
  } catch (err) {
    s.chat.pop(); renderChat(s, box); toast(err.message, 'error');
  } finally { s.busy = false; input.focus(); }
}

async function onClick(s, box, e) {
  const b = e.target.closest('[data-va]');
  if (!b) return;
  if (b.dataset.va === 'reset') return reset(s, box);
  if (b.dataset.va === 'live') return liveCall(s, box, b);
  if (b.dataset.va === 'speak') { s.speak = !s.speak; b.setAttribute('aria-pressed', String(s.speak)); b.textContent = s.speak ? '🔊 Speaking replies' : '🔇 Speak replies'; if (s.speak) speak(s, s.chat.filter(m => m.who === 'payal').slice(-1)[0]?.text); return; }
  if (b.dataset.va === 'eval') {
    b.disabled = true; b.textContent = 'Starting…';
    try { const r = await api('/api/v5/admin/sarvam-voice/eval', { method: 'POST' }); toast('Eval run started on Sarvam'); b.textContent = `Run started · ${String(r.id || r.run_id || '').slice(0, 12)}`; }
    catch (err) { toast(err.message, 'error'); b.disabled = false; b.textContent = 'Run the suite on Sarvam'; }
  }
}
