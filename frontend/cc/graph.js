// Live architecture graph for a preparation run. Hand-laid SVG; nodes update from streamed events.
import { esc, fmtMs, ORIGIN_LABEL } from './ui.js';
import { highlight } from './json.js';

const W = 420, NODE_H = 40;
// IndiaMART seller APIs (left: identity + context, right: what the seller did and who he dealt with).
const LEFT = ['company.alias', 'company.detail', 'context.global', 'context.getcontext', 'context.insights', 'dataset.seller', 'dataset.calls', 'packages.catalog'];
const RIGHT = ['leads.purchased', 'leads.detail', 'enquiry.list', 'pns.insights', 'lms.messages', 'sts.history', 'redash.hotlead', 'redash.pns'];
export const SOURCES = [...LEFT, ...RIGHT];
// Nodes that fan out per item (one request per offer id / per chat contact).
const FAN = { 'leads.detail': 'leads', 'lms.messages': 'chats' };
const LABEL = {
  trigger: 'Seller activity', orchestrator: 'Orchestrator', elastic: 'Elasticsearch', gateway: 'IM gateway',
  'company.alias': 'Company · alias', 'company.detail': 'Company · details', 'context.global': 'Global ctx · context UID',
  'context.getcontext': 'Global ctx · getContext', 'context.insights': 'Insights API', 'dataset.seller': 'Dataset · seller row',
  'dataset.calls': 'Dataset · bot calls', 'packages.catalog': 'Package catalogue', 'leads.purchased': 'Buy leads · purchased',
  'leads.detail': 'Buy-lead details ×N', 'enquiry.list': 'Enquiries', 'pns.insights': 'PNS call insights', 'lms.messages': 'LMS chats ×N',
  'sts.history': 'STS history', 'redash.hotlead': 'Redash · hot leads', 'redash.pns': 'Redash · PNS', contract: 'Production contract',
  'sarvam.aggregate': 'Sarvam · persona', 'sarvam.locality': 'Sarvam · locality', 'persona.index': 'Persona index (ES)',
  teleprompter: 'Teleprompter', 'voice.plugin': 'Voice bot (plug-in)',
};
export const NODE_LABEL = LABEL;
const ROW0 = 168, ROW_GAP = 50, ROWS = Math.max(LEFT.length, RIGHT.length);

function layout() {
  const nodes = {
    trigger: { x: 110, y: 10, w: 200 }, gateway: { x: 6, y: 72, w: 128 }, orchestrator: { x: 144, y: 72, w: 132 }, elastic: { x: 286, y: 72, w: 128 },
  };
  LEFT.forEach((id, i) => { nodes[id] = { x: 6, y: ROW0 + i * ROW_GAP, w: 182 }; });
  RIGHT.forEach((id, i) => { nodes[id] = { x: 232, y: ROW0 + i * ROW_GAP, w: 182 }; });
  const after = ROW0 + ROWS * ROW_GAP + 14;
  nodes.contract = { x: 110, y: after, w: 200 };
  nodes['sarvam.aggregate'] = { x: 6, y: after + 70, w: 196 };
  nodes['sarvam.locality'] = { x: 218, y: after + 70, w: 196 };
  nodes['persona.index'] = { x: 110, y: after + 140, w: 200 };
  nodes.teleprompter = { x: 6, y: after + 210, w: 196 };
  nodes['voice.plugin'] = { x: 218, y: after + 210, w: 196 };
  return { nodes, height: after + 210 + NODE_H + 12, busTop: 124, busBottom: after };
}

// Badges stay short so they never collide with a node's label.
const BADGE = { live_api: 'LIVE', elastic_cache: 'ES HIT', elastic: 'ES', local_db: 'DB', mock: 'MOCK', llm: 'LLM', default: 'DEFAULT', internal: 'SYSTEM', off: 'OFF' };
const STRIPE = { live_api: '--o-live', elastic_cache: '--o-cache', elastic: '--o-cache', local_db: '--o-db', mock: '--o-mock', llm: '--o-llm', default: '--o-default', internal: '--o-default', off: '--o-blank' };

export function createGraph(container, { onOpen }) {
  const { nodes, height, busTop, busBottom } = layout();
  const st = {};
  for (const id of Object.keys(nodes)) st[id] = { status: 'pending', origin: null, ms: null, detail: '', count: 0, done: 0, es: null, query: null, via: false };
  const meta = {}; // registry facts per API (mode, via_gateway) from /api/v5/architecture
  const busX = 210;
  const edge = (id, d, cls = '') => `<path class="edge ${cls}" data-edge="${id}" d="${d}"/>`;
  let edges = '';
  edges += edge('trigger', `M210 ${nodes.trigger.y + NODE_H} V${nodes.orchestrator.y}`);
  edges += edge('orchestrator', `M${busX} ${nodes.orchestrator.y + NODE_H} V${busBottom}`);
  edges += edge('elastic', `M350 ${nodes.elastic.y + NODE_H} V${busTop + 14} H${busX}`, 'bus');
  edges += edge('gateway', `M70 ${nodes.gateway.y + NODE_H} V${busTop + 14} H${busX}`, 'gw');
  for (const id of SOURCES) {
    const n = nodes[id], y = n.y + NODE_H / 2;
    edges += LEFT.includes(id) ? edge(id, `M${n.x + n.w} ${y} H${busX}`) : edge(id, `M${busX} ${y} H${n.x}`);
  }
  const c = nodes.contract, a = nodes['sarvam.aggregate'], l = nodes['sarvam.locality'], p = nodes['persona.index'], t = nodes.teleprompter, v = nodes['voice.plugin'];
  edges += edge('contract', `M${busX} ${busBottom} V${c.y}`);
  edges += edge('sarvam.aggregate', `M190 ${c.y + NODE_H} V${a.y - 12} H${a.x + a.w / 2} V${a.y}`);
  edges += edge('sarvam.locality', `M230 ${c.y + NODE_H} V${l.y - 12} H${l.x + l.w / 2} V${l.y}`);
  edges += edge('persona.index', `M${a.x + a.w / 2} ${a.y + NODE_H} V${p.y - 12} H190 V${p.y}`);
  edges += edge('teleprompter', `M190 ${p.y + NODE_H} V${t.y - 12} H${t.x + t.w / 2} V${t.y}`);
  edges += edge('voice.plugin', `M230 ${p.y + NODE_H} V${v.y - 12} H${v.x + v.w / 2} V${v.y}`);
  const nodeSvg = id => {
    const n = nodes[id];
    return `<g class="node" data-node="${id}" tabindex="0" role="button" aria-label="${esc(LABEL[id])}: pending" transform="translate(${n.x} ${n.y})">
      <rect class="box" width="${n.w}" height="${NODE_H}"/><rect class="stripe" width="4" height="${NODE_H - 10}" x="5" y="5" fill="var(--line-strong)"/>
      <text x="16" y="17">${esc(LABEL[id])}</text><text class="sub" x="16" y="31">waiting</text>
      <text class="badge" x="${n.w - 8}" y="17" text-anchor="end"></text><text class="sub es" x="${n.w - 8}" y="31" text-anchor="end"></text></g>`;
  };
  container.innerHTML = `<div class="graph-wrap"><svg class="graph" viewBox="0 0 ${W} ${height}" role="group" aria-label="Architecture of this preparation run">
    <rect class="region" x="2" y="${ROW0 - 22}" width="${W - 4}" height="${ROWS * ROW_GAP + 20}"/>
    <text class="region-label" x="12" y="${ROW0 - 9}">SOURCES · FETCHED IN PARALLEL</text>
    <text class="bus-label" x="${busX + 6}" y="${busTop - 4}">ES cache ⇄</text>
    ${edges}${Object.keys(nodes).map(nodeSvg).join('')}</svg><div class="tip" hidden></div></div>`;
  const svg = container.querySelector('svg');
  const tip = container.querySelector('.tip');

  function paint(id) {
    const g = svg.querySelector(`[data-node="${CSS.escape(id)}"]`);
    if (!g) return;
    const s = st[id];
    g.classList.toggle('running', s.status === 'running');
    g.classList.toggle('error', s.status === 'error' || s.status === 'fallback');
    g.classList.toggle('skipped', s.status === 'skipped' || s.status === 'waiting' || s.status === 'idle');
    const stripe = g.querySelector('.stripe');
    stripe.setAttribute('fill', s.origin ? `var(${STRIPE[s.origin] || '--line-strong'})` : 'var(--line-strong)');
    const badge = g.querySelector('.badge');
    const word = s.status === 'running' ? '…' : s.origin === 'elastic_cache' ? 'ES HIT' : s.status === 'skipped' ? 'SKIP' : s.status === 'error' ? 'FAIL'
      : s.status === 'fallback' ? 'RULES' : s.status === 'idle' ? (s.idle || '') : s.origin ? (BADGE[s.origin] || (ORIGIN_LABEL[s.origin] || s.origin).toUpperCase()) : (s.planned || '');
    badge.textContent = word;
    badge.setAttribute('fill', s.status === 'error' || s.status === 'fallback' ? 'var(--err)' : s.origin ? `var(${STRIPE[s.origin] || '--dim'})` : 'var(--dim)');
    const sub = g.querySelector('.sub:not(.es)');
    sub.textContent = FAN[id] && s.count ? `${s.done}/${s.count} ${FAN[id]}${s.ms ? ' · ' + fmtMs(s.ms) : ''}`
      : id === 'gateway' ? (s.status === 'pending' ? 'waiting' : s.idleSub || s.detail || '')
      : s.status === 'pending' ? (s.plannedSub || 'waiting') : s.status === 'running' ? 'running…' : s.status === 'skipped' ? 'skipped'
      : s.status === 'waiting' ? 'not connected' : s.status === 'idle' ? (s.idleSub || '') : (fmtMs(s.ms) || s.status);
    g.querySelector('.es').textContent = s.es || (s.via && s.origin === 'live_api' ? 'via GW' : '');
    g.setAttribute('aria-label', `${LABEL[id]}: ${s.status}${s.origin ? ', ' + s.origin : ''}${s.ms ? ', ' + fmtMs(s.ms) : ''}`);
    const e = svg.querySelector(`[data-edge="${CSS.escape(id)}"]`);
    if (e) e.classList.toggle('active', s.status === 'running');
  }

  function gatewayTouch(id, ev) {
    if (!meta[id]?.via_gateway) return;
    const gw = st.gateway;
    if (ev.kind === 'response' && ev.origin === 'live_api') { gw.live = (gw.live || 0) + 1; st[id].via = true; }
    if (ev.kind === 'error' && ev.origin === 'live_api') gw.failed = (gw.failed || 0) + 1;
    if (ev.kind === 'response' && ev.origin === 'mock') gw.mocked = (gw.mocked || 0) + 1;
    if (gw.live || gw.failed) {
      gw.status = gw.live ? 'ok' : 'error'; gw.origin = 'live_api';
      gw.idleSub = ''; gw.detail = `${gw.live || 0} live · ${gw.failed || 0} failed`;
    } else {
      gw.status = 'idle'; gw.origin = 'off'; gw.idle = 'UNUSED'; gw.idleSub = `${gw.mocked || 0} mocked`;
      gw.detail = 'Synthetic seller — nothing is sent upstream; mocks answer in the same shape.';
    }
    paint('gateway');
  }

  function update(ev) {
    let id = ev.node;
    for (const fan of Object.keys(FAN)) if (id?.startsWith(fan + ':')) id = fan;
    if (id === 'facts' || id === 'identity') id = 'contract';
    if (id === 'run' || !st[id]) {
      if (id === 'elastic.persist') { const s = st.elastic; s.writes = (s.writes || 0) + 1; s.status = 'ok'; s.origin = 'elastic'; s.ms = ev.ms; s.es = `${s.writes} writes`; paint('elastic'); }
      return;
    }
    const s = st[id];
    if (FAN[id]) {
      if (ev.kind === 'start') { s.count += 1; s.status = 'running'; }
      if (ev.kind === 'response' || ev.kind === 'error' || (ev.kind === 'cache' && ev.status === 'hit')) {
        s.done += 1; s.origin = ev.kind === 'cache' ? 'elastic_cache' : ev.origin; s.ms = Math.max(s.ms || 0, ev.ms || 0);
        if (ev.kind === 'error') s.failed = (s.failed || 0) + 1;
        if (s.done >= s.count) s.status = s.failed && s.failed === s.count ? 'error' : 'ok';
        s.detail = `${s.done - (s.failed || 0)} of ${s.count} answered${s.failed ? `, ${s.failed} failed` : ''}`;
      }
      if (ev.kind === 'skip') { s.status = 'skipped'; s.origin = 'off'; s.detail = ev.detail; }
    } else if (ev.kind === 'start') { s.status = ev.status === 'ok' ? 'ok' : 'running'; s.origin = ev.status === 'ok' ? 'internal' : s.origin; s.detail = ev.detail; }
    else if (ev.kind === 'cache') {
      s.query = ev.es_query || s.query; s.es = ev.status === 'hit' ? 'ES hit' : 'ES miss';
      if (ev.status === 'hit') { s.status = 'ok'; s.origin = 'elastic_cache'; s.ms = ev.ms; }
      const es = st.elastic; es.lookups = (es.lookups || 0) + 1; es.hits = (es.hits || 0) + (ev.status === 'hit' ? 1 : 0);
      es.status = 'ok'; es.origin = 'elastic'; es.es = `${es.hits}/${es.lookups} hits`; es.detail = ev.detail; es.query = ev.es_query; paint('elastic');
    } else if (ev.kind === 'skip') { s.status = ev.status === 'waiting' ? 'waiting' : 'skipped'; s.origin = 'off'; s.detail = ev.detail; }
    else if (ev.kind === 'error') { s.status = ev.status === 'fallback' ? 'fallback' : 'error'; s.detail = ev.detail; s.origin = ev.origin; }
    else { s.status = 'ok'; s.origin = ev.origin === 'internal' ? 'internal' : ev.origin; s.ms = ev.ms ?? s.ms; s.detail = ev.detail; }
    if (id === 'contract') { s.status = 'ok'; s.origin = 'internal'; s.detail = ev.detail; }
    if (SOURCES.includes(id)) gatewayTouch(id, ev);
    paint(id);
  }

  // Registry facts before (or without) a run: planned mode per source, and which go through the gateway.
  // A synthetic seller is never sent upstream, so its live APIs are planned as MOCK.
  function plan(arch, { synthetic = true } = {}) {
    for (const item of arch?.items || []) {
      meta[item.id] = item;
      const s = st[item.id];
      if (!s) continue;
      const mode = synthetic && item.mode === 'live' && item.kind !== 'dataset' ? 'mock' : item.mode;
      s.planned = mode === 'live' ? 'LIVE' : mode === 'mock' ? 'MOCK' : 'OFF';
      s.plannedSub = synthetic && item.kind === 'dataset' ? 'no row (synthetic)' : mode === 'mock' ? 'mock' : item.via_gateway ? 'via gateway'
        : item.kind === 'redash' ? 'Redash' : item.kind === 'dataset' ? 'Elasticsearch' : item.endpoint_supplied ? 'direct' : 'no endpoint';
      if (s.status === 'pending') paint(item.id);
    }
    const gw = st.gateway;
    if (gw.status === 'pending') {
      const on = !!arch?.gateway?.enabled;
      gw.status = 'idle'; gw.origin = on ? 'live_api' : 'off'; gw.idle = on ? 'ON' : 'OFF';
      gw.idleSub = on ? `${(arch.items || []).filter(i => i.via_gateway).length} APIs routed` : 'direct to hosts';
      gw.detail = on ? 'Pass-through gateway to IndiaMART-internal hosts (URL set in Admin → APIs).' : 'Gateway switched off — requests go to the hosts directly.';
      paint('gateway');
    }
  }

  // After the run: sources that never ran (nothing to fan out to) say so instead of looking planned.
  function settle() {
    for (const id of SOURCES) {
      const s = st[id];
      if (s.status !== 'pending') continue;
      s.status = 'idle'; s.idle = 'NOT RUN'; s.origin = null;
      s.idleSub = FAN[id] ? `no ${FAN[id]} to fetch` : 'not needed';
      s.detail = FAN[id] === 'leads' ? 'No purchased buy leads to expand for this seller.' : FAN[id] === 'chats' ? 'No chat contact for this seller.' : 'This run did not need this source.';
      paint(id);
    }
  }

  // "You are here": ring the nodes of the current business-flow stage.
  function focus(ids = []) {
    svg.querySelectorAll('.node.here').forEach(g => g.classList.remove('here'));
    ids.forEach(id => svg.querySelector(`[data-node="${CSS.escape(id)}"]`)?.classList.add('here'));
  }

  function showTip(g) {
    const id = g.dataset.node, s = st[id];
    const box = g.getBoundingClientRect(), wrap = container.querySelector('.graph-wrap').getBoundingClientRect();
    const m = meta[id];
    tip.innerHTML = `<b>${esc(LABEL[id])}</b><span class="dim">${esc(s.detail || (m ? `${m.label} · ${m.mode}${m.via_gateway ? ' · via gateway' : ''}` : 'Waiting for this step.'))}</span>${s.query ? `<span class="ins-label">Elastic query</span><pre>${highlight(s.query)}</pre>` : ''}<span class="dim" style="font-size:11px">Click to inspect, edit the request and hit it again.</span>`;
    tip.hidden = false;
    const left = Math.min(Math.max(0, box.left - wrap.left), wrap.width - 300);
    tip.style.left = `${left}px`;
    tip.style.top = `${box.bottom - wrap.top + 6}px`;
  }
  svg.addEventListener('mouseover', e => { const g = e.target.closest('.node'); if (g) showTip(g); });
  svg.addEventListener('mouseout', e => { if (!e.relatedTarget?.closest?.('.node')) tip.hidden = true; });
  svg.addEventListener('focusin', e => { const g = e.target.closest('.node'); if (g) showTip(g); });
  svg.addEventListener('focusout', () => { tip.hidden = true; });
  const open = g => { tip.hidden = true; onOpen(g.dataset.node, st[g.dataset.node], g); };
  svg.addEventListener('click', e => { const g = e.target.closest('.node'); if (g) open(g); });
  svg.addEventListener('keydown', e => { const g = e.target.closest('.node'); if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(g); } });

  return { update, plan, settle, focus, state: st, labels: LABEL, meta };
}
