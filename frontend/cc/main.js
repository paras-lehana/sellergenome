// Command Center shell: navigation, top bar, search, auth, theme and the client router.
import { api, BASE, VERSION, session, refreshStatus, login, logout } from './api.js';
import { $, esc, toast } from './ui.js';

const v = `?v=${VERSION}`;
const ROUTES = [
  { re: /^\/$/, view: 'floor', title: 'Seller floor', load: () => import(`./floor.js${v}`) },
  { re: /^\/buyer\/?$/, view: 'buyer-floor', title: 'Buyer desk', load: () => import(`./floor.js${v}`) },
  { re: /^\/call\/([\w-]+)$/, view: 'call', title: 'On call', load: () => import(`./call.js${v}`) },
  { re: /^\/persona\/([\w-]+)$/, view: 'persona', title: 'Persona', load: () => import(`./persona.js${v}`) },
  { re: /^\/buyer\/call\/([\w-]+)$/, view: 'buyer-call', title: 'Buyer call', load: () => import(`./buyer.js${v}`) },
  { re: /^\/genome\/?$/, view: 'genome', title: 'Persona index', load: () => import(`./genome.js${v}`) },
  { re: /^\/sarvam\/?$/, view: 'sarvam', title: 'Sarvam studio', load: () => import(`./sarvam.js${v}`) },
  { re: /^\/admin\/?$/, view: 'admin', title: 'Admin', load: () => import(`./admin.js${v}`) },
];

const NAV = [
  ['Live'],
  ['/', 'Seller floor', '◉'], ['/buyer', 'Buyer desk', '⇄'], ['/genome', 'Persona index', '⌬'], ['/sarvam', 'Sarvam studio', '✦'],
  ['Understand'],
  ['/analysis', 'Analysis & evidence', '▥'], ['https://kibana.lehana.in/s/sellergenome/app/dashboards', 'Kibana dashboards', '▤', 'ext'],
  ['/walkthrough', 'Guided workshop', '▷'], ['/studio#sellers', 'Seller explorer', '⌕'], ['/studio#evidence', 'Evidence search', '❝'],
  ['/studio#data', 'Data studio', '▦'], ['/studio#speech', 'Recording studio', '◍'],
  ['Practise'],
  ['/studio#lab', 'Conversation lab', '≋'],
  ['Control'],
  ['/admin', 'Admin', '⚙'], ['/studio#integrations', 'Sources & jobs', '⇅'], ['/studio#feedback', 'PM feedback', '☷'],
  ['/studio#settings', 'Access', '⚿'], ['/docs', 'API docs', '{}', 'ext'],
  ['Earlier versions'],
  ['/v4', 'V4 console', '◇'], ['/v4/buyer', 'V4 buyer assistance', '◇'], ['/v4/meetings', 'V4 meetings', '◇'],
  ['/v4/sarvam', 'V4 Sarvam lab', '◇'], ['/v4/admin', 'V4 admin', '◇'], ['/studio', 'Studio overview', '◇'],
  ['/studio#roadmap', 'Roadmap', '◇'], ['/slide', 'One slide', '◇'],
];

// Old studio hashes on "/" keep working.
const LEGACY = /^#(overview|sellers|lab|evidence|data|integrations|speech|settings|admin|roadmap|workshop|feedback|legal)(\/|$)/;
if (location.pathname.replace(/\/+$/, '') === (BASE || '') && LEGACY.test(location.hash)) location.replace(`${BASE}/studio${location.hash}`);

let current = null;

function path() {
  const p = location.pathname.slice(BASE.length).replace(/\/+$/, '');
  return p || '/';
}

function renderNav() {
  const here = path();
  $('#nav').innerHTML = NAV.map(([href, label, ico, kind]) => {
    if (!label) return `<div class="nav-group">${esc(href)}</div>`;
    const url = href.startsWith('http') ? href : BASE + href;
    const active = !href.includes('#') && (href === here || (href !== '/' && here.startsWith(href)) || (href === '/' && here.startsWith('/call/')) || (href === '/buyer' && here.startsWith('/buyer/')));
    return `<a href="${esc(url)}" class="${kind === 'ext' ? 'ext' : ''}" ${active ? 'aria-current="page"' : ''} ${href.startsWith('http') ? 'target="_blank" rel="noopener"' : ''}><span class="ico" aria-hidden="true">${ico}</span>${esc(label)}</a>`;
  }).join('');
}

export function navigate(to, { replace = false } = {}) {
  const url = to.startsWith('http') ? to : BASE + to;
  if (replace) history.replaceState({}, '', url); else history.pushState({}, '', url);
  route();
}

async function route() {
  const p = path();
  const match = ROUTES.find(r => r.re.test(p));
  renderNav();
  closeRail();
  if (current?.destroy) { try { current.destroy(); } catch { /* view already gone */ } }
  current = null;
  // A fresh container per page (5.11.1): listeners a page adds to its root used to survive the next route — the floor's
  // agent-picker handler then answered on Talk as Seller first and the mid-call agent switch never restarted.
  const old = $('#view');
  const view = old.cloneNode(false);
  old.replaceWith(view);
  if (!match) { view.innerHTML = '<div class="page"><h1>Not found</h1><p class="muted">That page does not exist.</p></div>'; return; }
  const params = p.match(match.re).slice(1);
  $('#crumbs').innerHTML = `<span>SellerGenome</span><span aria-hidden="true">/</span><b>${esc(match.title)}</b>`;
  document.title = `${match.title} · SellerGenome`;
  try {
    const mod = await match.load();
    current = await mod.mount(view, { view: match.view, params, navigate });
  } catch (err) {
    view.innerHTML = `<div class="page"><h1>Something went wrong</h1><p class="muted">${esc(err.message)}</p><p style="margin-top:12px"><a class="btn" href="${esc(location.href)}">Reload</a></p></div>`;
  }
  view.focus({ preventScroll: true });
}

// ---- top bar ----------------------------------------------------------------------------------
function clock() {
  const t = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date());
  $('#clock').textContent = `${t} IST`;
}

function renderStatus(s) {
  const dot = (label, state, title) => `<span title="${esc(title)}"><i class="dot ${state}"></i>${label}</span>`;
  // Right after a restart Traefik briefly serves the V3 rollback, whose /api/v5/status is a 404 body without these
  // sections — show no dots rather than throw (5.5.2; seen by the live-QA matrix).
  const complete = s && s.elastic && s.sarvam && s.sarvam.budget && s.redash && s.voice_plugin;
  $('#status').innerHTML = complete ? [
    dot('ES', s.elastic.status === 'ready' ? 'ok' : s.elastic.status === 'not_configured' ? 'warn' : 'err', `Elasticsearch: ${s.elastic.status}; ${s.elastic.outbox} writes pending`),
    dot('Sarvam', s.sarvam.configured ? 'ok' : 'err', `Sarvam ${s.sarvam.model}: ${s.sarvam.budget.public.used}/${s.sarvam.budget.public.limit} public calls today`),
    dot('Redash', s.redash.mode === 'live' ? 'ok' : 'warn', `Redash mode: ${s.redash.mode} (approved GLIDs only)`),
    dot('Bot', s.voice_plugin.connected ? 'ok' : 'warn', s.voice_plugin.connected ? `${s.voice_plugin.connected} voice bot call(s) connected` : 'No external voice bot connected'),
  ].join('') : '';
  $('#auth').textContent = session.operator ? 'Sign out' : 'Sign in';
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('sg-theme', theme); } catch { /* private window */ }
  document.querySelector('meta[name=theme-color]').content = theme === 'light' ? '#f3f5f9' : '#0b0e14';
}

function closeRail() {
  $('#rail').classList.remove('open');
  $('#scrim').hidden = true;
  $('#menu').setAttribute('aria-expanded', 'false');
}

// ---- search -----------------------------------------------------------------------------------
let searchTimer = null, searchAbort = null, searchItems = [];
async function runSearch(q) {
  searchAbort?.abort();
  searchAbort = new AbortController();
  try {
    const data = await api(`/api/v5/search?q=${encodeURIComponent(q)}`, { signal: searchAbort.signal });
    searchItems = data.items;
    const box = $('#search-results');
    box.innerHTML = searchItems.length ? searchItems.map((it, i) => `
      <button type="button" role="option" data-i="${i}">
        <b>${esc(it.company_name)}</b><span class="chip">${esc(it.kind === 'private' ? 'Real seller' : it.kind === 'locked' ? 'Real · sign in' : it.kind === 'generated' ? 'New mock' : 'Synthetic')}</span>
        <small>${esc([it.contact_name, it.city, `GLID ${it.glid}`].filter(Boolean).join(' · '))}${it.note ? ' — ' + esc(it.note) : ''}</small>
      </button>`).join('') + `<p class="dim" style="font-size:11.5px;padding:6px 10px">${session.operator ? 'Signed in: real sellers included.' : 'Sign in to search real sellers. Any other GLID shows a demo seller.'}</p>`
      : '<p class="dim" style="padding:10px">No seller found. Try a GLID — any number creates a production-shaped mock seller.</p>';
    box.hidden = false;
  } catch (err) { if (err.name !== 'AbortError') toast(err.message, 'error'); }
}

function chooseResult(it) {
  $('#search-results').hidden = true;
  if (it.kind === 'locked') { $('#auth')?.click(); return; }  // a real seller's GLID: sign in, never a made-up profile
  $('#search-input').value = '';
  // Results appear on the floor "as an ongoing call"; the floor view handles the insertion.
  sessionStorage.setItem('sg-found', JSON.stringify({ seller_id: it.seller_id, at: Date.now() }));
  if (path() === '/') window.dispatchEvent(new CustomEvent('sg:found', { detail: it }));
  else navigate('/');
}

// ---- boot -------------------------------------------------------------------------------------
function boot() {
  let theme = 'dark';
  try { theme = localStorage.getItem('sg-theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); } catch { /* default */ }
  setTheme(theme);
  $('#theme').addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));
  $('#menu').addEventListener('click', () => { $('#rail').classList.add('open'); $('#scrim').hidden = false; $('#menu').setAttribute('aria-expanded', 'true'); $('#rail a')?.focus(); });
  $('#scrim').addEventListener('click', closeRail);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeRail(); $('#search-results').hidden = true; }
    if ((e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); $('#search-input').focus(); }
  });
  const input = $('#search-input');
  input.addEventListener('input', () => { clearTimeout(searchTimer); const q = input.value.trim(); if (!q) { $('#search-results').hidden = true; return; } searchTimer = setTimeout(() => runSearch(q), 180); });
  input.addEventListener('focus', () => { if (input.value.trim()) $('#search-results').hidden = false; });
  $('#search').addEventListener('submit', e => { e.preventDefault(); if (searchItems[0]) chooseResult(searchItems[0]); else if (input.value.trim()) runSearch(input.value.trim()); });
  $('#search-results').addEventListener('click', e => { const b = e.target.closest('button[data-i]'); if (b) chooseResult(searchItems[+b.dataset.i]); });
  document.addEventListener('click', e => { if (!e.target.closest('#search')) $('#search-results').hidden = true; });

  const dialog = $('#login-dialog');
  $('#auth').addEventListener('click', async () => {
    if (session.operator) { await logout(); toast('Signed out'); route(); }
    else { $('#login-error').textContent = ''; dialog.showModal(); $('#login-password').focus(); }
  });
  $('#login-form').addEventListener('submit', async e => {
    if (e.submitter?.value === 'cancel') return;
    e.preventDefault();
    $('#login-submit').disabled = true;
    try { await login($('#login-password').value); dialog.close(); $('#login-password').value = ''; toast('Signed in — real sellers and admin unlocked'); route(); }
    catch (err) { $('#login-error').textContent = err.status === 401 || err.status === 403 ? 'That password did not work.' : err.message; }
    finally { $('#login-submit').disabled = false; }
  });
  window.addEventListener('sg:auth-required', () => { if (!dialog.open && !session.operator) toast('That needs the PM password — use Sign in.', 'error'); });
  window.addEventListener('sg:status', e => renderStatus(e.detail));
  window.addEventListener('popstate', route);
  document.addEventListener('click', e => {
    const a = e.target.closest('a[data-nav]');
    if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); navigate(a.getAttribute('href').slice(BASE.length)); }
  });
  clock(); setInterval(clock, 1000);
  refreshStatus().then(route);
  setInterval(refreshStatus, 30000);
}

boot();
