/* Shared API, escaped templates, navigation metadata and accessible feedback. No provider secrets belong here. */
const { LOADING_TIPS } = await import('./loading-tips.js?v='+encodeURIComponent(document.querySelector('meta[name="app-version"]')?.content || 'dev'));

const baseMeta = document.querySelector('meta[name="app-base"]')?.content || '';
export const BASE = baseMeta.includes('__') ? location.pathname.replace(/\/static\/.*$/, '').replace(/\/$/, '') : baseMeta.replace(/\/$/, '');
export const state = { route: 'overview', sellers: [], seller: null, persona: null, session: null, result: null, sellerPage: 1, sellerQuery: '', sellerFilter: '', operator: false, auth: {authenticated:false, operator:false}, inspectorTab: 'persona' };
export const NAV = [
  ['workshop', 'PM Workshop', 'flag', 'PM'], ['feedback', 'Feedback inbox', 'link', '↳'],
  ['overview', 'Overview', 'grid', '01'], ['sellers', 'Seller explorer', 'users', '02'],
  ['lab', 'Conversation Lab', 'wave', '03'], ['evidence', 'Evidence search', 'search', '04'],
  ['data', 'Data Studio', 'database', '05'], ['integrations', 'Integrations', 'link', '06'],
  ['speech', 'Recording Studio', 'mic', '09'], ['settings', 'Access & password', 'shield', '07'], ['admin', 'Administration', 'settings', 'AD'], ['roadmap', 'Roadmap & demo', 'flag', '08'],
];
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6M17 14a5 5 0 0 1 4 5v2"/>',
  wave: '<path d="M3 10v4m4-9v14m5-17v20m5-16v12m4-8v4"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  link: '<path d="m10 13 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="m10 3-1 3-3 1-3-1-1 4 3 2v3l-2 2 3 3 3-2 3 1 1 3 4-1v-3l2-2 3-1-1-4-3-1-1-3V3z"/>',
  flag: '<path d="M5 22V3m0 0c4-4 10 4 14 0v11c-4 4-10-4-14 0"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  shield: '<path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6z"/><path d="m8 12 3 3 5-6"/>',
  mic: '<rect x="8" y="2" width="8" height="13" rx="4"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  play: '<path d="m8 4 13 8-13 8z"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
};

/** Escapes every untrusted API value before interpolation into a template. */
export function e(value) { return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char])); }
/** Stable presentation for unknown nested metadata without object coercion. */
export function display(value) { return value == null ? 'Not supplied' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
/** Small local vector icons keep the portal independent of third-party assets. */
export function icon(name, cls = 'nav-icon') { return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.grid}</svg>`; }
/** Humanizes identifiers while preserving the original data in exports. */
export function human(value) { return String(value ?? 'Unknown').replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()); }
/** Uses the user's specified India timezone for user-facing timestamps. */
export function date(value) { if (!value) return 'Not recorded'; const d = new Date(typeof value==='number' && value<1e12 ? value*1000 : value); return Number.isNaN(d.valueOf()) ? e(value) : new Intl.DateTimeFormat('en-IN', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Asia/Kolkata'}).format(d) + ' IST'; }
/** Browser authorization comes only from the verified HttpOnly session. Legacy keys are removed. */
try { sessionStorage.removeItem('persona-operator'); } catch {}
export function setAuth(value) { state.auth={authenticated:Boolean(value?.authenticated),operator:Boolean(value?.operator),expires_at:value?.expires_at};state.operator=state.auth.operator;document.dispatchEvent(new CustomEvent('persona:auth',{detail:state.auth})); }
export function setOperator() { setAuth({authenticated:false,operator:false}); }
/** Requests a bounded same-origin API call; server errors remain visible, with no fabricated fallback data. */
export async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 90000);
  const headers = { ...(options.body && !(options.body instanceof FormData) ? {'Content-Type':'application/json'} : {}), ...options.headers };
  try {
    const response = await fetch(BASE + '/api' + path, {...options, headers, signal:controller.signal, credentials:'same-origin', cache:'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const detail = data.detail || data.error || `Request failed (${response.status}).`; const error=new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));error.status=response.status;if(response.status===401 && !path.startsWith('/auth/'))setAuth({authenticated:false,operator:false});throw error; }
    return data;
  } catch (error) { if(error.name === 'AbortError') throw new Error('The request took too long. Check the connection and retry.'); throw error; }
  finally { clearTimeout(timeout); }
}
/** Typed badges never rely on colour alone; backend statuses stay verbatim. */
export function badge(value, kind = '') {
  const text = human(value);
  const lower = String(value).toLowerCase();
  if(!kind) kind = /ready|connected|implemented|healthy|indexed|available|complete|fresh|success|configured|verified/.test(lower) && !/unconfigured|not|unavailable/.test(lower) ? 'success' : /failed|error|offline|stale/.test(lower) ? 'danger' : /planned|partial|pending|degraded|unconfigured|configuration|exhausted|not_configured|blocked|roadmap|requires/.test(lower) ? 'warning' : 'neutral';
  return `<span class="badge badge-${e(kind)}">${e(text)}</span>`;
}
/** Page heading provides the primary next step once per screen. */
export function heading(eyebrow, title, subtitle, action = '') { return `<div class="page-heading"><div><div class="eyebrow">${e(eyebrow)}</div><h1>${e(title)}</h1><p>${e(subtitle)}</p></div>${action}</div>`; }
/** Empty states explain how to recover or begin without pretending data exists. */
export function empty(title, text, action='') { return `<div class="empty-state"><h3>${e(title)}</h3><p>${e(text)}</p>${action}</div>`; }
/** Top-level failure offers the same operation again. */
export function errorView(error) { return `<div class="error-state" role="alert"><h2>We couldn't open this view</h2><p>${e(error.message || error)}</p><button class="button" data-action="reload">Try again</button></div>`; }
/** Reusable provenance card separates source age, ingestion and confidence. */
export function evidenceCard(item) { return `<article class="evidence-card"><div class="spread"><span class="eyebrow" style="margin:0">${e(human(item.kind || item.source || 'Evidence'))}</span>${badge(item.freshness?.state || item.freshness || (item.stale ? 'stale' : 'Observation'))}</div><p>${e(item.text || item.summary || 'No text supplied.')}</p><div class="evidence-meta"><span>${e(item.id)}</span><span>Observed ${date(item.observed_at)}</span>${item.confidence != null ? `<span>${Math.round(Number(item.confidence)*100)}% confidence</span>` : ''}</div>${item.source_ref ? `<p class="small muted">Source: ${e(display(item.source_ref))}</p>` : ''}${item.ttl_hours ? `<span class="small muted">Freshness window: ${e(item.ttl_hours)} hours</span>` : ''}</article>`; }
/** Persona traits expose reasons instead of hiding inference behind a score. */
export function traits(persona, compact=false) {
  const fields = Object.entries(persona?.fields || {});
  if(!fields.length) return empty('No persona generated yet', 'Generate a persona to see delivery choices and their supporting evidence.');
  return `<div class="trait-grid">${fields.slice(0,compact ? 6 : fields.length).map(([key, raw]) => {const field = typeof raw === 'object' && raw !== null ? raw : {value:raw}; const confidence = Math.max(0, Math.min(100, Math.round(Number(field.confidence || 0)*100))); return `<div class="trait"><div class="trait-label">${e(human(key))}</div><div class="trait-value">${e(display(field.value))}</div>${field.confidence != null ? `<div class="confidence" aria-hidden="true"><i style="--confidence:${confidence}%"></i></div>` : ''}<details><summary>${field.confidence != null ? confidence + '% heuristic confidence · ' : ''}Why this choice</summary><p>${e(field.reason || 'Default delivery setting; no source reason supplied.')}</p><p>Evidence: ${e((field.evidence_ids || []).join(', ') || 'No direct evidence / default')}</p>${field.observed_at ? `<p>Observed: ${date(field.observed_at)}</p>` : ''}${field.computed_at?`<p>Computed: ${date(field.computed_at)}</p>`:''}${field.expires_at?`<p>Fresh until: ${date(field.expires_at)}</p>`:''}<p>Method: ${e(field.method || persona.method || 'Not supplied')}${field.model?' · '+e(field.model):''}</p></details></div>`; }).join('')}</div>`;
}
/** Plans preserve facts and give operators an inspectable conversation outline. */
export function planView(plan) { return Object.entries(plan || {}).map(([key,value]) => `<section class="plan-block"><h3>${e(human(key))}</h3>${Array.isArray(value) ? `<ul>${value.map(item => `<li>${e(display(item))}</li>`).join('')}</ul>` : `<p>${e(display(value))}</p>`}</section>`).join('') || empty('No plan yet', 'Generate a persona first.'); }
/** Safe compact dictionary renderer for configurable backend metadata. */
export function definitions(object) { return `<dl class="definition-list">${Object.entries(object || {}).map(([key,value]) => `<div><dt>${e(human(key))}</dt><dd>${e(display(value))}</dd></div>`).join('')}</dl>`; }
/** Downloads only the object explicitly selected by the user. */
export function download(name, value) { const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})); const a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); }
/** Dismissible transactional notifications; no recurring promotional nudges. */
export function toast(message, error=false) { const node=document.createElement('div'); node.className='toast'+(error?' error':''); node.innerHTML=`<span>${e(message)}</span><button aria-label="Dismiss notification">×</button>`; node.querySelector('button').onclick=()=>node.remove(); document.querySelector('#toasts').append(node); if(!error) setTimeout(()=>node.remove(),5000); }
/** Inline validation scrolls and focuses the actual field while preserving its input. */
export function invalid(field, message) { field.setAttribute('aria-invalid','true'); field.classList.add('field-glow'); let node=field.parentElement.querySelector('.field-error'); if(!node){node=document.createElement('div');node.className='field-error';node.id=field.id+'-error';field.after(node);} node.textContent=message;node.setAttribute('role','alert');field.setAttribute('aria-describedby',node.id); field.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});setTimeout(()=>field.focus({preventScroll:true}),200);setTimeout(()=>field.classList.remove('field-glow'),3000); }
/** Long actions show a real operation name and shuffled static tool tips, then restore control state. */
export async function busy(button, text, operation) {
  const original=button?.innerHTML; if(button){button.disabled=true;button.innerHTML=`<span class="spinner"></span>${e(text)}`;}
  let tipNode, interval;
  const delayed=setTimeout(()=>{tipNode=document.createElement('p');tipNode.className='loading-tip';tipNode.setAttribute('role','status');button?.parentElement?.after(tipNode);const next=()=>{if(!tipPool.length) tipPool=shuffle([...LOADING_TIPS]);tipNode.textContent='Studio note: '+tipPool.pop();};next();interval=setInterval(next,7000);},5000);
  try{return await operation();}finally{clearTimeout(delayed);clearInterval(interval);tipNode?.remove();if(button?.isConnected){button.disabled=false;button.innerHTML=original;}}
}
/** Fisher-Yates avoids repeating educational tips within a pool cycle. */
function shuffle(items){for(let i=items.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[items[i],items[j]]=[items[j],items[i]];}return items;}
let tipPool=[];
