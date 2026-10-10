// The live floor: seller calls (/) and the buyer desk (/buyer).
// The server sends each lane's script and elapsed time; we animate locally and resync every 15 s.
import { api, session } from './api.js';
import { $, $$, busy, esc, fmtClock, genomeStrip, initials, origin, reducedMotion, signalChip, toast, SIGNAL_LABEL } from './ui.js';
import { catalogue } from './engines.js';
import { agentBarHtml, agentInfo, chooseAgent } from './agent.js';

const STAGE = { greeting: 'Greeting', qualifying: 'Qualifying', package: 'Package', meeting: 'Meeting', wrap_up: 'Wrap-up', live: 'Live' };
const LANG = { 'hi-IN': 'Hindi', 'en-IN': 'English', 'te-IN': 'Telugu', 'ta-IN': 'Tamil', 'mr-IN': 'Marathi', 'gu-IN': 'Gujarati', 'bn-IN': 'Bengali', 'pa-IN': 'Punjabi', 'kn-IN': 'Kannada', 'ml-IN': 'Malayalam' };

export async function mount(root, { view, navigate }) {
  const buyer = view === 'buyer-floor';
  const state = { tiles: new Map(), found: [], raf: 0, sync: 0, tick: 0, data: null, navigate, buyer, alive: true };
  root.innerHTML = `
    <div class="page">
      <div class="page-head">
        <div>
          <p class="eyebrow">${buyer ? 'Buyer-side VANI · missed calls' : 'Seller-side VANI · hot leads'}</p>
          <h1>${buyer ? 'Buyer desk' : 'Seller floor'}</h1>
          <p>${buyer ? 'When a seller misses a buyer’s call, VANI answers, captures the requirement and connects a live seller or posts a buy lead.' : 'Sellers who just did something on IndiaMART get a call from VANI after a short exploration window. Pick any call and barge in — the context is prepared for real while you watch.'}</p>
        </div>
        <div class="aside">
          <nav class="seg" aria-label="Floor"><a href="${document.body.dataset.base}/" data-nav ${buyer ? '' : 'aria-current="page"'}>Seller floor</a><a href="${document.body.dataset.base}/buyer" data-nav ${buyer ? 'aria-current="page"' : ''}>Buyer desk</a></nav>
          <div id="floor-agent" class="floor-agent"></div>
        </div>
      </div>
      <p class="note sim" id="floor-note" style="margin-bottom:14px"></p>
      ${buyer ? '' : '<div class="kpis" id="kpis" aria-label="Floor numbers"></div>'}
      <div class="floor">
        <section aria-label="${buyer ? 'Buyer calls' : 'Ongoing calls'}"><div class="tiles" id="tiles"></div></section>
        <aside class="side" id="side"></aside>
      </div>
    </div>`;
  await sync(state);
  const found = sessionStorage.getItem('sg-found');
  if (found && !buyer) {
    sessionStorage.removeItem('sg-found');
    const f = JSON.parse(found);
    if (Date.now() - f.at < 60000) addFound(state, f.seller_id);
  }
  const onFound = e => addFound(state, e.detail.seller_id);
  window.addEventListener('sg:found', onFound);
  state.sync = setInterval(() => sync(state), 15000);
  state.tick = setInterval(() => tickText(state), 250);
  const frame = () => { if (!state.alive) return; if (!document.hidden) drawWaves(state); state.raf = requestAnimationFrame(frame); };
  if (!reducedMotion()) frame(); else drawWaves(state);
  paintAgent(state);
  root.addEventListener('change', e => {
    const a = e.target.closest('[data-agent-select]');
    if (!a) return;
    chooseAgent(a.dataset.place, a.value);
    paintAgent(state, true);
  });
  $('#tiles').addEventListener('click', e => {
    const b = e.target.closest('[data-barge]');
    if (b) barge(state, b.dataset.barge, b);
    const persona = e.target.closest('[data-persona]');
    if (persona) openPersona(state, persona.dataset.persona, persona);
    const talk = e.target.closest('[data-talk]');
    if (talk) (state.buyer ? talkAsBuyer : talkAsSeller)(state, talk.dataset.talk, talk);
  });
  $('#side').addEventListener('click', e => {
    const b = e.target.closest('[data-queue]');
    if (b) bargeSeller(state, b.dataset.queue, [], b, 'queue');
  });
  return {
    destroy() { state.alive = false; cancelAnimationFrame(state.raf); clearInterval(state.sync); clearInterval(state.tick); clearInterval(state.countdown); window.removeEventListener('sg:found', onFound); },
  };
}

// Agent picker on the floor (owner, 2026-10-10): the agent Talk as Seller / Talk as Buyer will use, with its name and id.
async function paintAgent(state, announce = false) {
  const el = $('#floor-agent');
  if (!el) return;
  try {
    const [cat, d] = await Promise.all([catalogue(), agentInfo()]);
    state.agentInfo = d;
    el.innerHTML = agentBarHtml(cat, d, { place: state.buyer ? 'buyer' : 'call', compact: true });
    if (announce) toast(`Agent: ${el.querySelector('.agent-id')?.textContent.trim()}`);
  } catch { el.innerHTML = ''; }
}

async function sync(state) {
  let data;
  try { data = await api(`/api/v5/floor?flow=${state.buyer ? 'buyer' : 'seller'}`); }
  catch (err) { if (!state.data) $('#tiles').innerHTML = `<p class="muted">${esc(err.message)}</p>`; return; }
  state.data = data;
  $('#floor-note').textContent = data.note;
  const now = performance.now();
  const ids = new Set(data.tiles.map(t => t.id));
  for (const [id] of state.tiles) if (!ids.has(id) && !state.found.includes(id)) state.tiles.delete(id);
  for (const t of data.tiles) {
    const prev = state.tiles.get(t.id);
    state.tiles.set(t.id, { ...t, t0: now - t.elapsed_s * 1000, air0: t.on_air_s - t.elapsed_s, phase: prev?.phase ?? Math.random() * 10 });
  }
  renderTiles(state);
  if (!state.buyer) { renderKpis(data.kpis); renderSide(state, data); } else renderBuyerSide(state);
}

function order(state) {
  const found = state.found.map(id => state.tiles.get(id)).filter(Boolean);
  const pinned = [...state.tiles.values()].filter(t => t.real_seller && !t.found);
  const real = [...state.tiles.values()].filter(t => t.real && !t.found);
  const rest = [...state.tiles.values()].filter(t => !t.real && !t.real_seller && !t.found);
  return [...found, ...pinned, ...real, ...rest];
}

function renderTiles(state) {
  const box = $('#tiles');
  box.innerHTML = order(state).map(t => state.buyer ? buyerTile(t) : sellerTile(t)).join('');
  tickText(state);
}

// A real seller (pinned for operators, or found by search): no simulated call and no generated persona strip.
function realSellerTile(t) {
  const s = t.seller;
  return `
  <article class="tile real-seller ${t.found ? 'found' : ''}" data-tile="${esc(t.id)}" aria-label="Real seller ${esc(s.company_name)}">
    <div class="tile-top">
      <div class="avatar" aria-hidden="true">${esc(initials(s.company_name))}</div>
      <div class="tile-id"><b>${esc(s.company_name)}</b><span>${esc([s.contact_name, s.city, LANG[t.language] || t.language].filter(Boolean).join(' · '))}</span></div>
      <span class="chip">${s.identity === 'confirmed' ? 'Live identity' : 'Identity pending'}</span>
    </div>
    <div class="tile-meta"><span class="stage">Ready</span>${origin('live_api', 'REAL SELLER')}</div>
    <div class="tile-foot">
      <span class="mono dim" style="font-size:11px">GLID ${esc(s.glid)} · real seller</span>
      <span class="tile-actions"><button class="btn btn-sm btn-ghost" data-persona="${esc(t.id)}" title="See which live APIs and attributes build this seller's persona, then the persona page">Open persona</button>
        <button class="btn btn-talk btn-sm" data-talk="${esc(t.id)}" title="Pull all live data, then start the call from the beginning: the bot is the executive and you are the seller">Talk as Seller →</button></span>
    </div>
  </article>`;
}

function sellerTile(t) {
  if (t.real_seller) return realSellerTile(t);
  const s = t.seller;
  return `
  <article class="tile ${t.found ? 'found' : ''} ${t.real ? 'real' : ''}" data-tile="${esc(t.id)}" aria-label="Call with ${esc(s.company_name)}">
    ${t.found ? '<p class="eyebrow" style="color:var(--accent)">Found in an ongoing call</p>' : ''}
    <div class="tile-top">
      <div class="avatar" aria-hidden="true">${esc(initials(s.company_name))}</div>
      <div class="tile-id"><b>${esc(s.company_name)}</b><span>${esc(s.contact_name || '')} · ${esc(s.city || '')} · ${esc(LANG[t.language] || t.language || '')}</span></div>
      <span class="onair" data-air>ON AIR</span>
    </div>
    <canvas class="wave" data-wave width="560" height="68" aria-hidden="true"></canvas>
    <div class="lines" data-lines aria-live="off"></div>
    <div class="tile-meta"><span class="stage" data-stage>${esc(STAGE[t.stage] || t.stage)}</span><span data-signal></span>${t.real ? origin('live_api', 'REAL CALL') : ''}</div>
    ${t.genome?.length ? genomeStrip(t.genome) : ''}
    <div class="tile-foot">
      <span class="mono dim" style="font-size:11px">GLID ${esc(s.glid)}${s.kind === 'private' ? ' · real seller' : s.synthetic === false ? '' : ' · synthetic'}</span>
      <span class="tile-actions"><button class="btn btn-sm btn-ghost" data-persona="${esc(t.id)}" title="See which APIs and attributes build this seller's persona, then the persona page">Open persona</button>
        ${t.real && !t.run_id ? '' : `<button class="btn btn-sm" data-barge="${esc(t.id)}" title="Join this call where it is now; Sarvam keeps playing the seller">Barge in</button>`}
        <button class="btn btn-talk btn-sm" data-talk="${esc(t.id)}" title="Pull all data, then start the call from the beginning: the bot is the executive and you are the seller">Talk as Seller →</button></span>
    </div>
  </article>`;
}

function buyerTile(t) {
  const c = t.call;
  return `
  <article class="tile" data-tile="${esc(t.id)}" aria-label="Buyer call from ${esc(c.buyer)}">
    <div class="tile-top">
      <div class="avatar" aria-hidden="true">${esc(initials(c.buyer))}</div>
      <div class="tile-id"><b>${esc(c.buyer)} · ${esc(c.product)}</b><span>${esc(c.city)} · missed ${esc(c.missed_seller)} · ${esc(c.flow.replace(/_/g, ' '))}</span></div>
      <span class="onair" data-air>ON AIR</span>
    </div>
    <canvas class="wave" data-wave width="560" height="68" aria-hidden="true"></canvas>
    <div class="lines" data-lines></div>
    <div class="tile-meta"><span class="stage">Lead</span><div class="progress-dots" data-progress aria-label="Collected fields"></div><span data-signal></span>${t.real ? origin('live_api', 'REAL CALL · Sarvam') : ''}</div>
    <div class="tile-foot"><span class="mono dim" style="font-size:11px">${esc(LANG[c.language] || c.language)} · buyer ${esc(c.buyer_glid)}</span>
      ${t.real ? '<span class="dim" style="font-size:11.5px">VANI buyer agent on Sarvam</span>' : `<span class="tile-actions"><button class="btn btn-sm" data-barge="${esc(t.id)}" title="Join this call where it is now; VANI keeps going from there">Barge in</button>
        <button class="btn btn-talk btn-sm" data-talk="${esc(t.id)}" title="Start the call from the beginning: VANI answers your missed call and you are the buyer">Talk as Buyer →</button></span>`}</div>
  </article>`;
}

function position(t) {
  const elapsed = (performance.now() - t.t0) / 1000;
  const idx = Math.floor(elapsed / t.turn_seconds);
  const frac = (elapsed % t.turn_seconds) / t.turn_seconds;
  return { elapsed, idx, frac };
}

function tickText(state) {
  for (const t of state.tiles.values()) {
    if (t.real_seller) continue;  // nothing is simulated on a real seller's tile
    const el = document.querySelector(`[data-tile="${CSS.escape(t.id)}"]`);
    if (!el) continue;
    const { elapsed, idx, frac } = position(t);
    const turns = t.turns || [];
    const visible = turns.slice(0, Math.min(turns.length, idx + 1));
    const spoken = visible.filter(x => x.who !== 'system');
    const last = spoken.slice(-2);
    const html = last.map((x, i) => {
      const isCurrent = i === last.length - 1 && idx < turns.length && turns[Math.min(idx, turns.length - 1)] === x;
      const text = isCurrent && !reducedMotion() ? x.text.slice(0, Math.max(1, Math.round(x.text.length * Math.min(1, frac * 1.7)))) : x.text;
      return `<p><span class="who ${esc(x.who)}">${x.who === 'bot' ? 'VANI' : x.who}</span>${esc(text)}</p>`;
    }).join('') || '<p class="dim">Dialling…</p>';
    const lines = el.querySelector('[data-lines]');
    if (lines.innerHTML !== html) lines.innerHTML = html;
    const sig = [...visible].reverse().find(x => x.signal || x.switch);
    const sigHtml = sig ? (sig.switch ? `<span class="signal sig-language_switch" title="Persona switch">⇄ ${esc(sig.switch)}</span>` : signalChip(sig.signal)) : '';
    const sigEl = el.querySelector('[data-signal]');
    if (sigEl.innerHTML !== sigHtml) sigEl.innerHTML = sigHtml;
    const stage = [...visible].reverse().find(x => x.stage)?.stage;
    const stageEl = el.querySelector('[data-stage]');
    if (stageEl && stage) stageEl.textContent = STAGE[stage] || stage;
    el.querySelector('[data-air]').textContent = `ON AIR ${fmtClock(t.air0 + elapsed)}`;
    if (state.buyer) {
      const got = {};
      visible.forEach(x => Object.assign(got, x.capture || {}));
      const fields = [['product', 'Product'], ['quantity', 'Quantity'], ['spec', 'Specs'], ['name', 'Name'], ['city', 'City']];
      el.querySelector('[data-progress]').innerHTML = fields.map(([k, label]) => {
        const on = k === 'spec' ? Object.keys(got).some(g => g.startsWith('spec:')) : !!got[k];
        return `<i class="${on ? 'on' : ''}" title="${label}: ${on ? 'captured' : 'pending'}"></i>`;
      }).join('');
    }
    t.speaker = idx < turns.length ? (turns[idx].who === 'system' ? null : turns[idx].who) : (Math.floor(elapsed / 3) % 2 ? 'bot' : 'seller');
    t.speaking = idx < turns.length ? frac < 0.8 : (elapsed % 3) < 2.2;
  }
}

function drawWaves(state) {
  const css = getComputedStyle(document.documentElement);
  const botColor = css.getPropertyValue('--accent').trim();
  const sellerColor = css.getPropertyValue('--o-db').trim();
  const idle = css.getPropertyValue('--line-strong').trim();
  const now = performance.now() / 1000;
  for (const t of state.tiles.values()) {
    if (t.real_seller) continue;
    const canvas = document.querySelector(`[data-tile="${CSS.escape(t.id)}"] [data-wave]`);
    if (!canvas) continue;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, hgt = canvas.height, bars = 56, gap = 3, bw = (w - gap * (bars - 1)) / bars;
    ctx.clearRect(0, 0, w, hgt);
    const color = !t.speaking ? idle : t.speaker === 'bot' ? botColor : sellerColor;
    ctx.fillStyle = color;
    for (let i = 0; i < bars; i++) {
      const n = Math.sin(i * 0.55 + now * 7 + t.phase) * 0.5 + Math.sin(i * 1.7 - now * 11 + t.phase * 2) * 0.3 + Math.sin(i * 0.21 + now * 3) * 0.2;
      const amp = t.speaking && !reducedMotion() ? 0.18 + Math.abs(n) * 0.82 : 0.08;
      const bh = Math.max(3, amp * hgt * 0.9);
      ctx.globalAlpha = t.speaking ? 0.95 : 0.6;
      ctx.fillRect(i * (bw + gap), (hgt - bh) / 2, bw, bh);
    }
  }
}

function renderKpis(k) {
  const cell = (label, value, kind, unit = '', title = '') => `<div class="kpi" title="${esc(title)}"><div class="lbl">${esc(label)}<em class="${kind}">${kind === 'sim' ? 'SIM' : 'REAL'}</em></div><div class="val">${value ?? '—'}${unit ? ` <small>${unit}</small>` : ''}</div></div>`;
  $('#kpis').innerHTML = [
    cell('Calls on air', k.simulated.on_air, 'sim', '', 'Simulated floor'),
    cell('Calls today', k.simulated.calls_today.toLocaleString('en-IN'), 'sim'),
    cell('Meetings fixed today', k.simulated.meetings_today.toLocaleString('en-IN'), 'sim', '', '11.5% of answered calls, the dataset rate'),
  ].join('');
}

function renderSide(state, data) {
  $('#side').innerHTML = `
    <section class="card" aria-labelledby="q-title">
      <div class="card-head"><h2 id="q-title">Exploration window</h2><span class="aside chip" title="15 simulated minutes pass per real minute">×${esc(data.queue[0]?.multiplier || 15)} clock</span></div>
      <div class="card-body queue"><p class="muted" style="font-size:12.5px;margin-bottom:6px">IndiaMART waits ~15 minutes after an activity so the seller can keep exploring, then VANI calls.</p>
        <ul>${data.queue.map(q => `<li><div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(q.seller.company_name)}</b><span class="countdown" data-count="${q.remaining_minutes}" data-mult="${q.multiplier}">${q.remaining_minutes.toFixed(1)} min</span></div><span class="dim">${esc(q.activity)}</span><button class="btn btn-sm btn-ghost" style="justify-self:start;margin-top:4px" data-queue="${esc(q.seller.seller_id)}">Call now →</button></li>`).join('')}</ul></div>
    </section>
    <section class="card ticker" aria-labelledby="t-title">
      <div class="card-head"><h2 id="t-title">Floor events</h2></div>
      <div class="card-body"><ul>${data.ticker.map(e => `<li><time>${esc(e.at)}</time><span>${esc(e.text)}</span></li>`).join('')}</ul></div>
    </section>
    <section class="card"><div class="card-body" style="padding-top:14px;display:grid;gap:8px;font-size:12.5px">
      <b>What happens when you barge in</b>
      <span class="muted">Real context preparation: Elasticsearch cache lookups, the IndiaMART APIs (production-shaped mocks where the network can’t reach them), the supplied dataset${session.operator ? ', live Redash for approved test GLIDs' : ''}, Sarvam persona aggregation and the persona index — streamed step by step.</span>
      <div class="legend">${['live_api', 'elastic_cache', 'local_db', 'mock', 'llm', 'default'].map(o => origin(o)).join('')}</div>
    </div></section>`;
  const started = performance.now();
  clearInterval(state.countdown);
  state.countdown = setInterval(() => {
    $$('.countdown').forEach(el => {
      const left = Number(el.dataset.count) - (performance.now() - started) / 1000 * Number(el.dataset.mult) / 60;
      el.textContent = left > 0 ? `${left.toFixed(1)} min` : 'Dialling…';
    });
  }, 1000);
}

function renderBuyerSide(state) {
  $('#side').innerHTML = `
    <section class="card"><div class="card-head"><h2>How Buyer VANI works</h2></div><div class="card-body" style="display:grid;gap:8px;font-size:12.5px">
      <span class="muted">The buyer called a seller through a PNS number; the seller didn’t pick up, so the call came to the IndiaMART Help Desk.</span>
      <b style="font-size:12.5px">Talk as Buyer · your VANI buyer-requirement prompt</b>
      <ol style="margin:0;padding-left:18px;display:grid;gap:4px"><li>Confirm the product (≤ 4 asks)</li><li>Number of pieces (≤ 3) — never the word “quantity”</li><li>Each specification (≤ 2), at most two options</li><li>Close: the sellers’ link on WhatsApp (BL Enriched)</li></ol>
      <span class="muted">The same flow runs as a separate agent on Sarvam — call or chat with it from the buyer call page.</span>
      <b style="font-size:12.5px">Barge in · the production flow</b>
      <span class="muted">Product, quantity, specifications, name and city; a live seller (consent first) or a buy lead. Signed in, your original production prompt runs, rendered. Limits and consent are enforced in code.</span>
    </div></section>`;
}

async function addFound(state, sellerId) {
  try {
    const t = await api(`/api/v5/floor/tile?seller_id=${encodeURIComponent(sellerId)}`);
    t.id = `found-${sellerId}`;
    state.found = [t.id, ...state.found.filter(id => id !== t.id)].slice(0, 3);
    state.tiles.set(t.id, { ...t, found: true, t0: performance.now(), air0: 4, phase: Math.random() * 10 });
    renderTiles(state);
    document.querySelector(`[data-tile="${CSS.escape(t.id)}"]`)?.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'center' });
    toast(t.real_seller ? `${t.seller.company_name} — real seller ready: Talk as Seller starts a live preparation` : `${t.seller.company_name} found in an ongoing call — barge in when ready`);
  } catch (err) { toast(err.message, 'error'); }
}

async function barge(state, tileId, button) {
  const t = state.tiles.get(tileId);
  if (!t) return;
  if (state.buyer) {
    button.disabled = true; button.textContent = 'Connecting…';
    const { idx } = position(t);
    try {
      const call = await api('/api/v5/buyer/calls', { method: 'POST', body: { tile_id: tileId, shown: Math.max(1, Math.min(t.turns.length, idx + 1)), policy: 'compact' } });
      state.navigate(`/buyer/call/${call.id}`);
    } catch (err) { toast(err.message, 'error'); button.disabled = false; button.textContent = 'Barge in'; }
    return;
  }
  if (t.real && t.run_id) { state.navigate(`/call/${t.run_id}`); return; }
  const { idx } = position(t);
  const seed = (t.turns || []).slice(0, Math.min(t.turns.length, idx + 1));
  bargeSeller(state, t.seller.seller_id, seed, button, t.found ? 'search' : 'floor');
}

// Talk as Seller (owner, 2026-10-10): a fresh run pulls all the data, then the call starts from scratch — the bot opens as
// the executive and the operator answers as the seller (no seeded floor lines, no simulated seller).
async function talkAsSeller(state, tileId, button) {
  const t = state.tiles.get(tileId);
  if (!t) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Pulling data…';
  busy('nav', true, 'Talk as Seller · pulling live IndiaMART data…');
  try {
    const run = await api('/api/v5/runs', { method: 'POST', body: { seller_id: t.seller.seller_id, origin: 'talk' } });
    try { sessionStorage.setItem(`sg-talk-${run.run_id}`, '1'); } catch { /* storage blocked: the call still opens */ }
    state.navigate(`/call/${run.run_id}?view=talk`);
  } catch (err) { busy('nav', false); toast(err.message, 'error'); button.disabled = false; button.textContent = label; }
}

// Open persona (PM, 2026-10-10): a fresh run, then /persona/{run} shows the APIs called and the attributes they filled
// while it prepares, and then the clean persona page (with Start call).
async function openPersona(state, tileId, button) {
  const t = state.tiles.get(tileId);
  if (!t) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Opening…';
  busy('nav', true, 'Open persona · calling IndiaMART APIs…');
  try {
    const run = await api('/api/v5/runs', { method: 'POST', body: { seller_id: t.seller.seller_id, origin: 'persona' } });
    state.navigate(`/persona/${run.run_id}`);
  } catch (err) { busy('nav', false); toast(err.message, 'error'); button.disabled = false; button.textContent = label; }
}

// Talk as Buyer (5.7): the same idea on the buyer desk — the call starts from scratch, VANI answers the missed call with the
// owner's buyer-requirement flow, and the operator is the buyer.
async function talkAsBuyer(state, tileId, button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Connecting…';
  busy('nav', true, 'Talk as Buyer · connecting VANI…');
  try {
    const call = await api('/api/v5/buyer/calls', { method: 'POST', body: { tile_id: tileId, talk: true, policy: 'requirement' } });
    state.navigate(`/buyer/call/${call.id}`);
  } catch (err) { busy('nav', false); toast(err.message, 'error'); button.disabled = false; button.textContent = label; }
}

async function bargeSeller(state, sellerId, seed, button, originKind) {
  button.disabled = true; button.textContent = 'Barging in…';
  busy('nav', true, 'Barge in · preparing the call…');
  try {
    const run = await api('/api/v5/runs', { method: 'POST', body: { seller_id: sellerId, origin: originKind } });
    try { sessionStorage.setItem(`sg-seed-${run.run_id}`, JSON.stringify(seed)); } catch { /* storage full or blocked */ }
    state.navigate(`/call/${run.run_id}`);
  } catch (err) { busy('nav', false); toast(err.message, 'error'); button.disabled = false; button.textContent = originKind === 'queue' ? 'Call now →' : 'Barge in'; }
}

export { SIGNAL_LABEL };
