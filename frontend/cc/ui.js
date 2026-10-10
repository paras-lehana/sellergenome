// Small UI helpers shared by every view. No framework: strings for markup, functions for behaviour.
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ENT = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ENT[c]);

export const ORIGIN_LABEL = {
  live_api: 'Live API', elastic_cache: 'ES cache', local_db: 'Dataset', supplied_export: 'Export', mock: 'Mock',
  llm: 'Sarvam', default: 'Default', blank: 'Blank', missing: 'Missing', off: 'Off', internal: 'System',
  elastic: 'Elastic', request: 'Request', operator: 'PM edit', rules: 'Rules',
};
export const origin = (o, label) => o ? `<span class="origin o-${esc(o)}" title="${esc(ORIGIN_TITLE[o] || '')}">${esc(label || ORIGIN_LABEL[o] || o)}</span>` : '';
// Owner, 2026-10-10: data views (persona page, brief, call pane) show no "Live API"-style tags — only a demo seller's mock
// data is still labelled (honesty rule). Operator views (inspector, graph, Admin, the API flow) keep origin().
export const mockTag = (o, label) => o === 'mock' ? origin(o, label) : '';
const ORIGIN_TITLE = {
  live_api: 'Returned by a real upstream call in this run', elastic_cache: 'An earlier API response read back from Elasticsearch',
  local_db: 'From the supplied seller dataset (real, private)', supplied_export: 'From the owner-supplied production export',
  mock: 'Production-shaped mock — the real API is unreachable from this server or was never supplied',
  llm: 'Derived by Sarvam from other fields', default: 'Documented default — nothing was available', blank: 'The source answered with an empty value',
  off: 'Turned off or skipped by policy', operator: 'Changed by a PM',
};
export const ORIGIN_VAR = { live_api: '--o-live', elastic_cache: '--o-cache', local_db: '--o-db', supplied_export: '--o-db', mock: '--o-mock', llm: '--o-llm', default: '--o-default', blank: '--o-blank', request: '--o-default', operator: '--o-default' };

export const signalChip = s => s ? `<span class="signal sig-${esc(s)}">${esc(SIGNAL_LABEL[s] || s)}</span>` : '';
export const SIGNAL_LABEL = { frustration: 'Frustration', confusion: 'Confusion', interest: 'Interest', rush: 'Rush', language_switch: 'Language switch', stop: 'Stop', bot_question: 'Asked if bot' };

export const fmtMs = ms => ms == null ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
export const fmtClock = s => { s = Math.max(0, Math.floor(s)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
export const initials = name => String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
export const pretty = v => typeof v === 'string' ? v : Array.isArray(v) ? v.map(x => typeof x === 'object' && x ? Object.values(x).join(' → ') : x).join(' · ') : typeof v === 'number' ? String(v) : v == null ? '—' : JSON.stringify(v);
export const titleCase = s => String(s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

export function toast(message, kind = 'info', ms = 4200) {
  const box = document.getElementById('toasts');
  if (!box) return;
  if ([...box.children].some(t => t.textContent === message)) return;
  const el = document.createElement('div');
  el.className = `toast ${kind === 'error' ? 'err' : ''}`;
  el.textContent = message;
  box.append(el);
  setTimeout(() => el.remove(), kind === 'error' ? ms * 2 : ms);
}

export function h(tag, attrs = {}, html = '') {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (v === false || v == null) continue; if (k === 'class') el.className = v; else el.setAttribute(k, v === true ? '' : v); }
  if (html) el.innerHTML = html;
  return el;
}

export const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// A seller's persona as a coloured barcode. Each segment is one trait; colour encodes the value.
const ARCH_COLOR = { price_sensitive: '--s-rush', curious: '--s-language', rushed: '--onair', confused: '--s-confusion', frustrated: '--s-frustration', interested: '--s-interest', uninterested: '--o-default', already_with_executive: '--o-db', language_switch: '--o-llm' };
export function genomeStrip(genes = []) {
  const color = g => {
    if (g.k === 'archetype') return `var(${ARCH_COLOR[g.v] || '--o-default'})`;
    if (g.k === 'language') return `var(${g.v === 'en-IN' ? '--o-db' : g.v === 'hi-IN' ? '--accent' : '--o-llm'})`;
    if (g.k === 'tier') return g.v === 'paid' ? 'var(--ok)' : 'var(--o-default)';
    const n = Number(g.v) || 0;
    return `color-mix(in srgb, var(--o-mock) ${Math.min(100, 20 + n * (g.k === 'pickup' ? 80 : 6))}%, var(--panel-3))`;
  };
  return `<div class="genome" title="Persona genome: ${esc(genes.map(g => `${g.k}=${g.v}`).join(', '))}">${genes.map(g => `<i style="background:${color(g)}"></i>`).join('')}</div>`;
}

export function emptyState(title, body, action = '') {
  return `<div class="empty"><h3>${esc(title)}</h3><p class="muted" style="margin:6px 0 12px">${esc(body)}</p>${action}</div>`;
}

export function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => toast('Copied'), () => toast('Copy failed — select and copy manually', 'error'));
}

export function download(name, text, type = 'application/json') {
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
}

// Owner, 2026-10-10: "make the part where talk to seller or open persona (same for buyer) is being loaded … look like
// its loading so some bar on the top, running bar". A thin running bar at the very top while any named job is busy
// (the navigation, the run preparing, a buyer call connecting); each job is switched off by name, with a safety timeout.
const busyJobs = new Map();
export function busy(job, on = true, label = '') {
  let bar = document.getElementById('top-progress');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'top-progress';
    bar.className = 'top-progress';
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-hidden', 'true');
    bar.innerHTML = '<span class="top-progress-label" aria-live="polite"></span>';
    document.body.appendChild(bar);
  }
  clearTimeout(busyJobs.get(job)?.timer);
  if (on) busyJobs.set(job, { label, timer: setTimeout(() => busy(job, false), 120000) });
  else busyJobs.delete(job);
  const active = [...busyJobs.values()];
  bar.classList.toggle('on', active.length > 0);
  bar.setAttribute('aria-hidden', String(!active.length));
  bar.querySelector('.top-progress-label').textContent = active.at(-1)?.label || '';
  document.documentElement.toggleAttribute('aria-busy', active.length > 0);
}

// Owner, 2026-10-10: "make that loading bar … a more width. the api which are being loaded are shown in orange breathing
// while the ones loaded in green hue." The APIs of the run being prepared, under the running bar.
export function busyApis(items = []) {
  busy('apis', items.length > 0 && items.some(i => i.state === 'run'), '');
  const bar = document.getElementById('top-progress');
  if (!bar) return;
  let row = bar.querySelector('.top-progress-apis');
  if (!row) { row = document.createElement('div'); row.className = 'top-progress-apis'; bar.appendChild(row); }
  row.innerHTML = items.map(i => `<span class="tp-api ${esc(i.state)}" title="${esc(i.label)}">${esc(i.short || i.label)}</span>`).join('');
  row.hidden = !items.length || !items.some(i => i.state === 'run');
}
