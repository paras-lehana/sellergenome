// Sarvam studio (/sarvam): every MCP tool, where SellerGenome uses it, and a way to run it.
import { api, session } from './api.js';
import { $, esc, fmtMs, origin, toast } from './ui.js';
import { highlight } from './json.js';
import { mountVoiceAgent } from './voiceagent.js';
import { bindPicker, catalogue, effective, pickerHtml } from './engines.js';
import { speechMeta } from './voice.js';

const KINDS = ['tts_model', 'tts_voice', 'tts_transport', 'translate_model'];

const WHERE_ORDER = ['In the main flow', 'Conversation lab', 'Recording studio', 'Composite', 'Build time', 'Planned', 'Maintenance', 'Retired by Sarvam', 'Available'];

export async function mount(root) {
  root.innerHTML = '<div class="page"><div class="skeleton" style="height:300px"></div></div>';
  let data, cat = null;
  try { [data, cat] = await Promise.all([api('/api/v5/sarvam/tools'), catalogue().catch(() => null)]); }
  catch (err) { root.innerHTML = `<div class="page"><p class="note">${esc(err.message)}</p></div>`; return {}; }
  const groups = {};
  data.items.forEach(t => (groups[t.where] ||= []).push(t));
  root.innerHTML = `
    <div class="page">
      <div class="page-head"><div><p class="eyebrow">Hackathon sponsor · designated organisation</p><h1>Sarvam studio</h1>
        <p>All ${data.items.length} tools of the pinned Sarvam MCP (v${esc(data.version)}), mapped to where SellerGenome uses them. Organisation <span class="mono">${esc(data.organization_id)}</span>; text model <span class="mono">${esc(data.model)}</span>.</p></div>
        <div class="aside"><span class="chip">Public budget ${esc(data.budget.public.used)}/${esc(data.budget.public.limit)} today</span></div></div>
      <div id="eng-box" style="margin-bottom:12px">${cat ? pickerHtml(cat, 'sarvam', KINDS) : ''}</div>
      <div id="va-box" style="margin-bottom:14px"></div>
      <h2 class="group-title" style="margin:18px 0 8px">Sarvam model APIs (sarvam-mcp) · speech, translation and the persona LLM</h2>
      <section class="card" style="margin-bottom:14px"><div class="card-head"><h2>Try the tools SellerGenome uses on every call</h2></div>
        <form class="card-body try-form" id="try-form">
          <label class="dim" style="display:grid;gap:4px;font-size:12px">Tool<select name="tool">${data.items.filter(t => t.try).map(t => `<option value="${esc(t.name)}">${esc(t.name.replace('sarvam_tools_', ''))}</option>`).join('')}</select></label>
          <label class="dim" style="display:grid;gap:4px;font-size:12px">Text<input type="text" name="text" value="Namaste, main IndiaMART ki virtual assistant Payal bol rahi hoon. Kya do minute baat ho sakti hai?" maxlength="600"></label>
          <label class="dim" style="display:grid;gap:4px;font-size:12px">Language<select name="language"><option value="hi-IN">Hindi</option><option value="te-IN">Telugu</option><option value="ta-IN">Tamil</option><option value="bn-IN">Bengali</option><option value="en-IN">English</option></select></label>
          <button class="btn btn-primary">Run live</button></form>
        <div class="card-body" id="try-out"></div></section>
      ${WHERE_ORDER.filter(w => groups[w]).map(w => `<h2 class="group-title" style="margin:18px 0 8px">${esc(w)} · ${groups[w].length}</h2>
        <div class="grid-3">${groups[w].map(t => `<article class="tool"><h3>${esc(t.name)}</h3><p style="font-size:12.5px">${esc(t.how)}</p>
          <p class="dim" style="font-size:11.5px">${esc(t.description.split('\n')[0].slice(0, 160))}</p>
          <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center">${origin(t.where === 'In the main flow' ? 'llm' : t.where === 'Planned' ? 'default' : 'internal', t.live_status?.replace(/_/g, ' ') || 'listed')}
            <button class="btn btn-sm" data-run="${esc(t.name)}" data-mode="demo">Demo</button>${session.operator && t.live_status === 'ready_for_bounded_test' ? `<button class="btn btn-sm" data-run="${esc(t.name)}" data-mode="live">Live</button>` : ''}</div>
          ${t.last_receipt ? `<p class="dim mono" style="font-size:10.5px">last: ${esc(t.last_receipt.status)} · ${esc((t.last_receipt.created_at || '').slice(0, 16))}</p>` : ''}
          <div data-out="${esc(t.name)}"></div></article>`).join('')}</div>`).join('')}
    </div>`;
  const agent = mountVoiceAgent($('#va-box'));
  if (cat) bindPicker(root, 'sarvam', () => { $('#eng-box').innerHTML = pickerHtml(cat, 'sarvam', KINDS, { open: true }); });
  $('#try-form').addEventListener('submit', async e => {
    e.preventDefault();
    const f = new FormData(e.target);
    const out = $('#try-out');
    out.innerHTML = '<div class="skeleton" style="height:60px"></div>';
    try {
      const eng = cat ? effective(cat, 'sarvam') : {};
      const tool = f.get('tool');
      const model = tool === 'sarvam_tools_tts_speak' ? eng.tts_model : tool === 'sarvam_tools_translate' ? eng.translate_model : null;
      const speaker = eng.tts_voice && eng.tts_voice !== 'auto' ? eng.tts_voice : 'priya';
      const r = await api('/api/v5/sarvam/try', { method: 'POST', body: { tool, text: f.get('text'), language: f.get('language'), model: model || null, speaker } });
      out.innerHTML = `${r.audio_base64 ? `<audio controls src="data:${esc(r.content_type)};base64,${r.audio_base64}"></audio><p class="dim" style="font-size:12px">${esc(speechMeta(r, null))} · spoken text: ${esc(r.spoken_text)}</p>` : ''}
        <pre class="code wrap">${highlight({ ...r, audio_base64: r.audio_base64 ? `[${r.audio_base64.length.toLocaleString()} base64 chars]` : undefined })}</pre><p class="dim" style="font-size:12px">${esc(fmtMs(r.ms))}</p>`;
    } catch (err) { out.innerHTML = `<p class="note">${esc(err.message)}</p>`; }
  });
  root.addEventListener('click', async e => {
    const b = e.target.closest('[data-run]'); if (!b) return;
    b.disabled = true;
    const box = root.querySelector(`[data-out="${CSS.escape(b.dataset.run)}"]`);
    try {
      const r = await api(`/api/v5/sarvam/tools/${encodeURIComponent(b.dataset.run)}/run?mode=${b.dataset.mode}`, { method: 'POST' });
      box.innerHTML = `<details open><summary class="dim" style="font-size:12px">${esc(r.status)}</summary><pre class="code wrap" style="max-height:220px">${highlight(r.result || r.reason || r)}</pre></details>`;
    } catch (err) { toast(err.message, 'error'); }
    finally { b.disabled = false; }
  });
  return { destroy() { agent.then(a => a?.destroy?.()); } };
}
