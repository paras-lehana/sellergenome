// Agent picker (owner, 2026-10-10: "we should also be able to choose the agent from the screen itself … during talk to
// seller, or on the main screen, once selected you show there the agent id, name"; and "there are many other like
// manjula executive, i want all of them"). One choice per place ('call' = seller floor + call page, 'buyer' = buyer desk
// + buyer calls): the built-in pipeline, or ANY voice agent in the Sarvam workspace. The engine override `bot` says
// built-in vs Sarvam; which Sarvam app is kept next to it.
import { api } from './api.js';
import { esc } from './ui.js';
import { effective, overrides, setOverride } from './engines.js';

const KEY = 'sg-agent-app';
let info = null;

/** Agent names, ids and versions, the built-in pipeline's models and every workspace agent (/api/v5/debug/voice). */
export function agentInfo(refresh = false) {
  if (!info || refresh) info = api('/api/v5/debug/voice').catch(err => { info = null; throw err; });
  return info;
}

function storedApps() { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } }

/** Our own agent for a place (Payal for seller calls, VANI for buyer calls). */
function ownApp(d, place) { return (place === 'buyer' ? d?.vani : d?.payal)?.app_id || null; }

/** The chosen Sarvam app id for a place, or null when the built-in pipeline is chosen. */
export function chosenApp(cat, d, place) {
  // No choice in this browser yet: Admin's default agent (sarvam_voice.default_agent, e.g. Manjula Executive v2).
  const def = place === 'call' ? d?.default_agent : null;
  if (!overrides(place).bot && def && def !== 'builtin' && (d?.workspace_agents || []).some(a => a.app_id === def && a.callable !== false)) return def;
  if (cat && effective(cat, place).bot !== 'sarvam_agent') return null;
  const app = storedApps()[place];
  const all = new Set([ownApp(d, place), ...(d?.workspace_agents || []).map(a => a.app_id)]);
  return app && all.has(app) ? app : ownApp(d, place);
}

/** 'builtin' or a Sarvam app id. */
export function currentAgent(cat, d, place) { return chosenApp(cat, d, place) || 'builtin'; }

export function chooseAgent(place, id) {
  const apps = storedApps();
  if (id === 'builtin') { setOverride(place, 'bot', 'builtin'); delete apps[place]; }
  else { setOverride(place, 'bot', 'sarvam_agent'); apps[place] = id; }
  try { localStorage.setItem(KEY, JSON.stringify(apps)); } catch { /* storage blocked: the choice lasts for this page */ }
}

function options(d, place) {
  const b = d?.builtin || {};
  const who = place === 'buyer' ? 'VANI' : 'Payal';
  const own = place === 'buyer' ? d?.vani : d?.payal;
  const out = [{ id: 'builtin', name: `${who} · built-in (SellerGenome pipeline)`, ident: [b.llm_model, b.tts_model, b.stt_realtime_model].filter(Boolean).join(' · ') }];
  if (own?.app_id) out.push({ id: own.app_id, name: own.name || `${who} on Sarvam`, ident: `${own.app_id} · v${own.version ?? '?'}`, ours: true });
  for (const a of d?.workspace_agents || []) {
    if (out.some(o => o.id === a.app_id)) continue;
    out.push({ id: a.app_id, name: a.callable === false ? `${a.name || a.app_id} (draft — not callable yet)` : (a.name || a.app_id),
      ident: a.callable === false ? `${a.app_id} · draft only — commit it in Sarvam to call it` : `${a.app_id} · v${a.committed_version ?? a.version ?? '?'}`,
      blocked: a.callable === false ? `${a.name || a.app_id} is only a draft on Sarvam — commit a version in the Sarvam dashboard, then it can be called.` : '' });
  }
  return out;
}

/** A dropdown of every agent with the chosen agent's name and id beside it. */
export function agentBarHtml(cat, d, { place = 'call', compact = false } = {}) {
  const cur = currentAgent(cat, d, place);
  const opts = options(d, place);
  const chosen = opts.find(o => o.id === cur) || opts[0];
  const id = `agent-${place}`;
  return `<div class="agent-bar${compact ? ' compact' : ''}" data-agent-bar="${esc(place)}">
    <label class="agent-label" for="${id}">Agent</label>
    <select id="${id}" class="agent-select" data-agent-select data-place="${esc(place)}">${opts.map(o => `<option value="${esc(o.id)}" ${o.id === chosen.id ? 'selected' : ''}>${esc(o.name)}${o.ours ? ' ★' : ''}</option>`).join('')}</select>
    <span class="agent-id"><b>${esc(chosen.name)}</b> <span class="mono">${esc(chosen.ident)}</span></span>
  </div>`;
}

/** Why the chosen agent cannot be called right now ('' when it can). */
export function agentBlocked(cat, d, place) {
  return options(d, place).find(x => x.id === currentAgent(cat, d, place))?.blocked || '';
}

/** The chosen agent's display line (for toasts). */
export function agentLine(cat, d, place) {
  const opts = options(d, place);
  const o = opts.find(x => x.id === currentAgent(cat, d, place)) || opts[0];
  return `${o.name} · ${o.ident}`;
}
