// Engine choices (5.3, decision D25). Admin → Voice & engines sets the defaults; every place that speaks, listens or
// hands a call to a bot has a picker that overrides them for this browser only. "Default" always means Admin's value.
import { api } from './api.js';
import { esc } from './ui.js';

const KEY = 'sg-engines';
const LABEL = {
  bot: 'Bot', tts_model: 'Voice model', tts_voice: 'Voice', tts_transport: 'Speech delivery', listen: 'Listening',
  stt_model: 'Clip model', stt_realtime_model: 'Realtime model', stt_mode: 'Transcript style', translate_model: 'Translation',
};
let cached = null;

export function catalogue() {
  if (!cached) cached = api('/api/v5/engines').catch(err => { cached = null; throw err; });
  return cached;
}

function stored() { try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { return {}; } }
function save(all) { try { localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* storage blocked: choices last for this page */ } }
const memory = {};

export function overrides(place) { return { ...(stored()[place] || {}), ...(memory[place] || {}) }; }

export function setOverride(place, kind, value) {
  const all = stored();
  const mine = { ...(all[place] || {}) };
  if (!value || value === 'default') delete mine[kind]; else mine[kind] = value;
  if (kind === 'tts_model') delete mine.tts_voice;  // voices differ per model
  all[place] = mine; memory[place] = mine;
  save(all);
}

export function resetPlace(place) { const all = stored(); delete all[place]; delete memory[place]; save(all); }

function options(cat, kind, model) {
  if (kind === 'tts_voice') return [{ id: 'auto', label: 'Persona voice (auto)', note: 'The persona’s voice in this model’s catalogue' },
    ...(cat.voices[model] || []).map(v => ({ id: v, label: v.replace(/_/g, ' '), note: '' }))];
  return cat.options[kind] || [];
}

/** Admin defaults overlaid with this place's valid choices. */
export function effective(cat, place) {
  const out = { ...cat.defaults };
  const o = overrides(place);
  for (const [k, v] of Object.entries(o)) {
    if (k === 'tts_voice') continue;
    if (options(cat, k, out.tts_model).some(x => x.id === v)) out[k] = v;
  }
  if (o.tts_voice && options(cat, 'tts_voice', out.tts_model).some(x => x.id === o.tts_voice)) out.tts_voice = o.tts_voice;
  else if (!options(cat, 'tts_voice', out.tts_model).some(x => x.id === out.tts_voice)) out.tts_voice = 'auto';
  return out;
}

const short = (cat, kind, id) => (options(cat, kind, cat.defaults.tts_model).find(x => x.id === id)?.label || id || '').replace(/ \(.*\)$/, '');

export function summary(cat, eff, kinds) {
  const bits = [];
  if (kinds.includes('bot') && eff.bot === 'sarvam_agent') bits.push('Payal on Sarvam');
  if (kinds.includes('tts_model')) bits.push(`${short(cat, 'tts_model', eff.tts_model)}${kinds.includes('tts_transport') ? ` · ${eff.tts_transport === 'stream' ? 'streamed' : 'REST'}` : ''}`);
  if (kinds.includes('listen')) bits.push(eff.listen === 'realtime' ? `${short(cat, 'stt_realtime_model', eff.stt_realtime_model)} live` : `${short(cat, 'stt_model', eff.stt_model)} clip`);
  else if (kinds.includes('stt_model')) bits.push(short(cat, 'stt_model', eff.stt_model));
  if (kinds.includes('translate_model')) bits.push(short(cat, 'translate_model', eff.translate_model));
  return bits.join(' · ');
}

/** A collapsible picker: one select per kind this place uses. */
export function pickerHtml(cat, place, kinds, { open = false } = {}) {
  const eff = effective(cat, place);
  const custom = Object.keys(overrides(place)).length > 0;
  const rows = kinds.map(kind => {
    const opts = options(cat, kind, eff.tts_model);
    const chosen = overrides(place)[kind];
    const def = kind === 'tts_voice' ? cat.defaults.tts_voice : cat.defaults[kind];
    const defLabel = opts.find(x => x.id === def)?.label || def;
    const note = opts.find(x => x.id === eff[kind])?.note || '';
    const id = `eng-${place}-${kind}`;
    return `<div class="eng-row"><label for="${id}">${esc(LABEL[kind] || kind)}</label>
      <select id="${id}" data-eng-kind="${esc(kind)}"><option value="default">Default · ${esc(defLabel)}</option>${opts.map(x => `<option value="${esc(x.id)}" ${chosen === x.id ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</select>
      ${note ? `<small class="dim">${esc(note)}</small>` : ''}</div>`;
  }).join('');
  return `<details class="eng" data-eng="${esc(place)}" ${open ? 'open' : ''}><summary><span class="eng-title">⚙ Engines</span> <span class="eng-sum">${esc(summary(cat, eff, kinds))}</span>${custom ? ' <span class="chip">this browser</span>' : ''}</summary>
    <div class="eng-grid">${rows}</div>
    <p class="dim eng-foot">Defaults come from Admin → Voice &amp; engines. Choices here apply in this browser only. ${custom ? '<button type="button" class="btn btn-sm" data-eng-reset>Use defaults</button>' : ''}</p></details>`;
}

/** Re-render-safe binding; ``onChange`` gets the new effective choices. Returns an unbind function. */
export function bindPicker(root, place, onChange) {
  const change = e => {
    const sel = e.target.closest('[data-eng-kind]');
    if (!sel || !sel.closest(`[data-eng="${place}"]`)) return;
    setOverride(place, sel.dataset.engKind, sel.value);
    onChange();
  };
  const click = e => {
    const b = e.target.closest('[data-eng-reset]');
    if (!b || !b.closest(`[data-eng="${place}"]`)) return;
    resetPlace(place); onChange();
  };
  root.addEventListener('change', change);
  root.addEventListener('click', click);
  return () => { root.removeEventListener('change', change); root.removeEventListener('click', click); };
}
