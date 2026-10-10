// Pre-call brief in the PM's "VANI Persona Studio" structure (docs/inputs/pm-design/README.md):
// 1 Basic details · 2 Engagement with IndiaMART · 3 Engagement with buyers · Conversation plan · Full record.
// Rendered from /api/v5/runs/{id}/brief. Every chip carries data-src so the call workspace opens the exact source.
import { $, esc, mockTag } from './ui.js';

const CONF_WORD = { H: 'high', M: 'medium', G: 'guess' };
export const conf = c => `<span class="conf ${c === 'G' ? 'g' : ''}" title="${esc(CONF_WORD[c] || 'guess')} confidence" aria-label="${esc(CONF_WORD[c] || 'guess')} confidence"><i class="on"></i><i class="${c !== 'G' ? 'on' : ''}"></i><i class="${c === 'H' ? 'on' : ''}"></i><b>${esc(c || 'G')}</b></span>`;
const src = s => s?.api ? `data-src='${esc(JSON.stringify({ api: s.api, pointer: s.pointer || '' }))}'` : '';
export const chip = s => `<button type="button" class="ev-chip" ${src(s)} title="${esc(s.text || s.api || '')}">${esc(s.label || s.api)}</button>`;
const num = n => `<span class="pm-num" aria-hidden="true">${n}</span>`;

export function legend() {
  return `<span class="pm-legend">${conf('H')} high ${conf('M')} medium ${conf('G')} guess</span>`;
}

export function basicsHtml(b, { compact = false } = {}) {
  // Owner, 2026-10-10: rows without data are left out (no "Not in file"), and no origin tag sits next to a value.
  const rows = b.basics.rows.filter(r => !r.missing).map(r => `<dt>${esc(r.label)}</dt><dd><span class="v">${esc(r.value)}</span>${r.note ? ` <span class="pm-note">(${esc(r.note)})</span>` : ''}${mockTag(r.origin) ? ` ${mockTag(r.origin)}` : ''}${r.api ? ` <button type="button" class="src-dot" ${src(r)} title="Source: ${esc(r.api)} ${esc(r.pointer || '')}" aria-label="Source of ${esc(r.label)}">↗</button>` : ''}</dd>`).join('');
  const products = b.basics.products.length ? `<dt>Products</dt><dd class="chips">${b.basics.products.map(p => `<button type="button" class="tagchip" ${src(p)}>${esc(p.name)}</button>`).join('')}</dd>` : '';
  return `<section class="pm-panel pm-basics" aria-labelledby="pm-b-${compact ? 'c' : 'f'}"><div class="pm-h">${num(1)}<h3 id="pm-b-${compact ? 'c' : 'f'}">Basic details</h3></div>
    <dl class="pm-kv">${rows}${products}</dl>${b.basics.flags.map(f => `<p class="pm-flag">⚑ ${esc(f)}</p>`).join('')}</section>`;
}

// Owner, 2026-10-10: "clicking it shows full insight" — each insight opens in place: confidence in words, the note, the
// facts it rests on and the full text of every record it cites (date · type · outcome).
function insightHtml(i) {
  const recs = i.records || [];
  const detail = `<div class="pm-ins-full">
      <p><b>${esc(i.text)}</b></p>
      <p class="dim">${esc(i.group || '')}${i.group ? ' · ' : ''}${esc(CONF_WORD[i.confidence] || 'guess')} confidence${i.note ? ` · ${esc(i.note)}` : ''}</p>
      ${(i.facts || []).length ? `<p class="dim">From: ${i.facts.map(esc).join(' · ')}</p>` : ''}
      ${recs.length ? `<ol class="pm-ins-recs">${recs.map(r => `<li><span class="mono">${esc(r.date || 'undated')}</span> · <b>${esc(r.type || '')}</b>${r.outcome ? ` · ${esc(r.outcome)}` : ''}<br>${esc(r.text || '')}</li>`).join('')}</ol>`
        : i.gap ? '' : '<p class="dim">No record cited — a statement about something absent, or a counted fact.</p>'}
    </div>`;
  return `<details class="pm-ins ${i.gap ? 'gap' : ''}"><summary><div class="pm-ins-t">${conf(i.confidence)}<span>${esc(i.text)}</span><i class="pm-ins-more" aria-hidden="true">›</i></div></summary>
      ${detail}
      <div class="pm-ins-ev">${i.evidence.length ? i.evidence.map(chip).join('') : i.gap ? '' : '<span class="dim">no citation</span>'}</div></details>`;
}

export function engagementHtml(b, { compact = false } = {}) {
  const e = b.engagement;
  const groups = e.groups.map(g => `<div class="pm-group"><p class="kicker">${esc(g.title)} <span>${g.items.length}</span></p>
    ${g.items.map(insightHtml).join('')}</div>`).join('');
  return `<section class="pm-panel pm-eng" aria-labelledby="pm-e-${compact ? 'c' : 'f'}"><div class="pm-h">${num(2)}<h3 id="pm-e-${compact ? 'c' : 'f'}">Engagement with IndiaMART</h3><span class="key">KEY INPUT</span>
      <span class="rt">${compact ? '' : legend()}<button type="button" class="linkish" data-record>Full record</button></span></div>
    <p class="pm-sub">Score ${esc(e.count)} insights · ${esc(e.span)} · ${e.origin === 'llm' ? 'grouped by Sarvam from cited evidence' : 'derived by rules from the record'}</p>
    ${groups || '<p class="dim">No insights yet.</p>'}
    ${(e.doubts || []).length ? `<div class="pm-doubts"><p class="kicker">Data doubts</p>${e.doubts.map(d => `<p class="dim">⚠ ${esc(d.text || d)}</p>`).join('')}</div>` : ''}</section>`;
}

export function buyersHtml(b) {
  const x = b.buyers;
  const stats = x.stats.map(s => `<button type="button" class="pm-stat" ${src(s)}><b>${esc(s.n)}${s.d != null ? `<small>/${esc(s.d)}</small>` : ''}</b><span>${esc(s.label)}</span>${s.f != null ? `<i style="--f:${Math.round(Math.max(0, Math.min(1, s.f)) * 100)}%"></i>` : ''}</button>`).join('');
  return `<section class="pm-panel pm-buyers" aria-labelledby="pm-y"><div class="pm-h">${num(3)}<h3 id="pm-y">Engagement with buyers</h3></div>
    ${stats}
    ${x.catalogue.some(c => String(c).length > 28)  /* generator sentences read as a list, short facts as chips */
      ? `<p class="kicker" style="margin-top:8px">Catalogue</p><ul class="pm-cat">${x.catalogue.map(c => `<li>${esc(c)}</li>`).join('')}</ul>`
      : x.catalogue.length ? `<p class="kicker" style="margin-top:8px">Catalogue ${x.catalogue.map(c => `<span class="tagchip">${esc(c)}</span>`).join('')}</p>` : ''}
    ${x.insights.map(i => `<div class="pm-ins"><div class="pm-ins-t">${conf(i.confidence)}<span>${esc(i.text)}</span></div><div class="pm-ins-ev"><span class="dim">${esc(i.note)}</span></div></div>`).join('')}</section>`;
}

// ---- conversation plan (dials move live with the call's delivery) -------------------------------
const LANG = { 'hi-IN': 'Hindi', 'en-IN': 'English', 'te-IN': 'Telugu', 'ta-IN': 'Tamil', 'mr-IN': 'Marathi', 'gu-IN': 'Gujarati', 'bn-IN': 'Bengali', 'kn-IN': 'Kannada', 'ml-IN': 'Malayalam', 'pa-IN': 'Punjabi', 'od-IN': 'Odia' };
const MIX = { none: 6, low: 28, medium: 55, high: 80 };
const FORM = { casual: [20, 'Casual, friendly'], respectful: [55, 'Respectful, friendly'], formal: [85, 'Formal, precise'] };
const WARM = { low: [20, 'Reserved, to the point'], medium: [55, 'Warm, steady'], high: [85, 'Warm, patient, no pressure'] };
const paceText = v => v < 0.9 ? 'Slow, with pauses' : v < 1.0 ? 'Measured' : v <= 1.08 ? 'Normal' : 'Brisk, short lines';

function liveDials(plan, call) {
  const d = structuredClone(plan.dials);
  if (!call?.delivery) return d;
  const now = call.delivery, base = call.base_delivery || {};
  const mark = (k, changed) => { if (changed) d[k].changed = true; };
  const pv = Number(now.pace) || 1;
  d.pace = { ...d.pace, pos: Math.max(2, Math.min(98, Math.round((pv - 0.8) / 0.45 * 100))), value: paceText(pv) }; mark('pace', now.pace !== base.pace);
  const lang = LANG[now.language] || now.language;
  if (now.language !== base.language || now.hinglish_mix !== base.hinglish_mix) {
    d.language = { ...d.language, left: lang, pos: now.language === 'hi-IN' ? (MIX[now.hinglish_mix] ?? 55) : now.language === 'en-IN' ? 92 : 8, value: now.language === 'hi-IN' ? 'Hinglish' : lang };
    mark('language', true);
  }
  if (FORM[now.formality] && now.formality !== base.formality) { d.formality = { ...d.formality, pos: FORM[now.formality][0], value: FORM[now.formality][1] }; mark('formality', true); }
  if (WARM[now.warmth] && now.warmth !== base.warmth) { d.tone = { ...d.tone, pos: WARM[now.warmth][0], value: WARM[now.warmth][1] }; mark('tone', true); }
  if (now.voice !== base.voice) { d.voice = { ...d.voice, value: `${String(now.voice).replace(/^./, c => c.toUpperCase())}, ${lang}` }; mark('voice', true); }
  return d;
}

const STAGE_STEP = { greeting: 'opening', qualifying: 'personalise', package: 'objection', meeting: 'next', wrap_up: 'next' };

// Compact plan for the call pane: the teleprompter stays the first thing on screen; the full plan is in the brief.
function planCompact(b, call) {
  const p = b.plan, d = liveDials(p, call);
  const current = STAGE_STEP[call?.stage];
  const mini = (k, title) => { const x = d[k];
    return `<div class="pm-mini ${x.changed ? 'changed' : ''}" title="WHY: ${esc(x.why)}"><div class="dt"><span class="kicker">${esc(title)}</span>${conf(x.c)}</div><b>${esc(x.value)}</b>${x.pos != null ? `<div class="bar" aria-hidden="true"><i style="left:${x.pos}%"></i></div>` : ''}</div>`; };
  const steps = ['opening', 'personalise', 'objection', 'escalation', 'next'].map((k, i) => { const st = p.steps[k];
    return `<li class="${current === k ? 'now' : ''}" title="${esc(st.text)} — ${esc(st.why)}" ${current === k ? 'aria-current="step"' : ''}><span class="kicker">0${i + 1} ${esc(st.label)}</span><span class="t">${esc(st.text)}</span></li>`; }).join('');
  return `<section class="pm-panel pm-plan compact" aria-label="Conversation plan"><div class="pm-h"><h3>Conversation plan</h3><span class="headline">${esc(p.headline)}</span>
      <span class="live-tag">LIVE</span><button type="button" class="linkish" data-act="brief">full brief</button></div>
    ${p.low_data ? `<p class="lowdata"><span>LOW DATA</span> ${esc(p.guesses)} of ${esc(p.settings)} settings are guesses — the live adapter corrects them.</p>` : ''}
    ${p.trigger ? `<p class="pm-trigger compact" title="${esc(p.trigger.detail || '')}"><span class="kicker">Why call</span> <b>${esc(String(p.trigger.type || '').replace(/_/g, ' '))}</b> — ${esc(p.trigger.detail || '')}</p>`
      : p.summary !== undefined ? '<p class="pm-trigger compact"><span class="kicker">Why call</span> <b>No call plan</b> — nothing in the data gives a reason to call now.</p>' : ''}
    <div class="pm-minis">${mini('voice', 'Voice')}${mini('pace', 'Pace')}${mini('language', 'Language')}${mini('formality', 'Formality')}${mini('tone', 'Tone')}</div>
    <ol class="pm-strip">${steps}</ol></section>`;
}

export function planHtml(b, { call = null, compact = false } = {}) {
  if (compact && call) return planCompact(b, call);
  const p = b.plan, d = liveDials(p, call);
  const lastSwitch = call?.switches?.filter(w => w.applied).slice(-1)[0];
  const dial = (k, title) => {
    const x = d[k];
    return `<div class="pm-dial ${x.changed ? 'changed' : ''}"><div class="dt"><span class="kicker">${esc(title)}</span>${conf(x.c)}</div><b>${esc(x.value)}</b>
      ${x.pos != null ? `<div class="track" aria-hidden="true"><span>${esc(x.left)}</span><div class="bar"><i style="left:${x.pos}%"></i></div><span>${esc(x.right)}</span></div>` : ''}
      <p class="why"><span>WHY</span> ${esc(x.changed && lastSwitch ? `Live: ${lastSwitch.reason}` : x.why)}</p>${x.sources?.length && !compact ? `<div class="chips">${x.sources.slice(0, 2).map(chip).join('')}</div>` : ''}</div>`;
  };
  const current = STAGE_STEP[call?.stage];
  const steps = ['opening', 'personalise', 'objection', 'escalation', 'next'].map((k, i) => {
    const s = p.steps[k];
    return `<div class="pm-step ${current === k ? 'now' : ''}" ${current === k ? 'aria-current="step"' : ''}><div class="dt"><span class="kicker">0${i + 1} ${esc(s.label)}</span>${conf(s.c)}</div><p class="t">${esc(s.text)}</p><p class="why">${esc(s.why)}</p>${s.sources?.length ? `<div class="chips">${s.sources.slice(0, 2).map(chip).join('')}</div>` : ''}</div>`;
  }).join('');
  return `<section class="pm-panel pm-plan" aria-labelledby="pm-p-${compact ? 'c' : 'f'}"><div class="pm-h"><h3 id="pm-p-${compact ? 'c' : 'f'}">Conversation plan</h3><span class="headline">${esc(p.headline)}</span>
      <span class="lock">🔒 Persona changes delivery only, never facts, pricing or compliance</span>${call ? '<span class="live-tag">LIVE · moves with the call</span>' : ''}</div>
    ${p.low_data ? `<p class="lowdata"><span>LOW DATA</span> <b>${esc(p.guesses)} of ${esc(p.settings)} settings are guesses.</b> Defaults used; the live adapter corrects them during the call.</p>` : ''}
    ${p.trigger ? `<p class="pm-trigger"><span class="kicker">Why call</span> <b>${esc(String(p.trigger.type || '').replace(/_/g, ' '))}</b> — ${esc(p.trigger.detail || '')}${p.trigger.other?.length ? ` <span class="dim">· also: ${esc(p.trigger.other.join(', '))}</span>` : ''}</p>`
      : p.summary !== undefined ? '<p class="pm-trigger"><span class="kicker">Why call</span> <b>No call plan</b> — nothing in the data gives a reason to call now.</p>' : ''}
    <div class="pm-dials">${dial('voice', 'Voice')}${dial('pace', 'Pace')}${dial('language', 'Language')}${dial('formality', 'Formality')}${dial('tone', 'Tone')}</div>
    <div class="pm-steps">${steps}</div></section>`;
}

export function recordHtml(b) {
  const r = b.record;
  return `<div class="ins-head"><span class="eyebrow">Full record</span><h2>${esc(b.seller.company_name || '')}</h2><button class="icon-btn x" data-close aria-label="Close full record"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    <div class="ins-body">
      <p class="kicker">Counts</p><div class="chips">${r.counts.map(([n, l]) => `<span class="tagchip"><b>${esc(n)}</b> ${esc(l)}</span>`).join('')}${r.last ? `<span class="tagchip">Last contact <b>${esc(r.last)}</b></span>` : ''}</div>
      <p class="kicker">Calls, meetings, payments, tickets and notes (${esc(r.total)})</p>
      <div class="scroll-x"><table class="table pm-log"><thead><tr><th>Date</th><th>Type</th><th>Outcome</th><th>What was said / noted</th><th></th></tr></thead><tbody>
      ${r.log.map(l => `<tr><td class="mono">${esc(l.date || '—')}</td><td>${esc(l.type)}</td><td><span class="disp ${esc(l.disp)}">${esc(l.outcome || '')}</span></td><td>${esc(l.text)}</td><td>${l.api ? `<button type="button" class="btn btn-sm btn-ghost" ${src(l)}>${mockTag(l.origin) || 'source'}</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="5" class="dim">Nothing on record.</td></tr>'}
      </tbody></table></div>
      ${(r.doubts || []).length ? `<p class="kicker">Data doubts</p>${r.doubts.map(d => `<p class="dim">⚠ ${esc(d.text || d)}</p>`).join('')}` : ''}
      <p class="kicker">Evidence rules behind the persona</p>
      ${r.rules.map(x => `<div class="pm-rule"><div><span class="kicker">Rule</span> <b>${esc(x.rule)}</b> <span class="str ${esc(x.strength)}">${esc(x.strength)}</span></div>
        <div class="dim">Basis: ${esc(x.basis)}${x.similar != null ? ` · ${esc(x.similar)} other indexed personas share this objection` : ''}</div><div class="chips">${(x.sources || []).slice(0, 4).map(s => chip({ ...s, label: s.label || s.id })).join('')}</div></div>`).join('') || '<p class="dim">No rule applied.</p>'}
    </div>`;
}

/** The persona generator's automatic checks for this seller (5.5). */
export function qualityHtml(b) {
  const q = b.quality;
  if (!q) return '';
  const tone = q.critical ? 'neg' : q.score >= 85 ? 'pos' : 'neu';
  return `<section class="pm-panel pm-quality" aria-label="Persona quality checks"><div class="pm-h"><h3>Quality · ${esc(q.engine)}</h3>
      <span class="disp ${tone}">${esc(q.score)}/100</span><span class="rt dim">${esc(q.passed)}/${esc(q.total)} checks · ${esc(q.attempts)} attempt(s) · ${esc(Math.round((q.ms || 0) / 100) / 10)} s</span></div>
    <p class="pm-sub">Segment <b>${esc(q.segment || 'Unknown')}</b> · tags ${q.tags.map(t => `<span class="tagchip">${esc(t)}</span>`).join(' ')}</p>
    ${q.failed.length ? `<ul class="pm-checks">${q.failed.map(c => `<li class="${c.critical ? 'crit' : ''}">${c.critical ? '✕' : '△'} ${esc(c.check)}${c.detail ? ` <span class="dim">— ${esc(c.detail)}</span>` : ''}</li>`).join('')}</ul>`
      : '<p class="dim">Every check passed.</p>'}
    ${q.corrected.length ? `<p class="dim" style="font-size:11.5px">Corrected by code${q.raw_score != null && q.raw_score !== q.score ? ` (model's own reply scored ${esc(q.raw_score)}/100)` : ''}: ${esc(q.corrected.join('; '))}</p>` : ''}
    ${q.flags.length ? `<p class="dim" style="font-size:11.5px">Data flags: ${esc(q.flags.join(' · '))}</p>` : ''}</section>`;
}

export function pipelineHtml(b) {
  return `<p class="pm-pipe"><b>One engine, two call types:</b> ${b.pipeline.map(x => `<span>${esc(x)}</span>`).join('<i>›</i>')}</p>`;
}

// Full-width brief (the PM's three-column layout) shown over the workspace.
export function fullBriefHtml(b, call) {
  return `<div class="pm-full-head"><div><p class="eyebrow">Pre-call brief · VANI Persona Studio</p><h2>${esc(b.seller.company_name || '')}</h2>
      <p class="dim">Call type <b>${esc(b.call_type)}</b>${b.data_mode === 'mock data' ? ' · demo data' : ''} · persona ${esc(b.engine === 'generator' ? 'by the Persona Generator v0.3 (Sarvam)' : b.persona_origin === 'llm' ? 'by Sarvam' : 'by rules')}</p></div>
    <button class="btn btn-sm" data-brief-close>Back to the call</button></div>
    <div class="pm-grid">${basicsHtml(b)}${engagementHtml(b)}${buyersHtml(b)}</div>
    ${planHtml(b, { call })}
    ${qualityHtml(b)}
    <button type="button" class="pm-recordlink" data-record>📄 View full record for ${esc(b.seller.company_name || 'this seller')}: ${esc(b.record.total)} calls, payments, tickets and notes <span class="dim">— every entry with its outcome, plus counts, evidence rules and data gaps</span></button>
    ${pipelineHtml(b)}`;
}

export function openRecord(b, returnFocus, onSource) {
  const d = $('#inspector');
  d.innerHTML = recordHtml(b);
  d.setAttribute('aria-hidden', 'false'); d.inert = false; $('#scrim').hidden = false;
  const close = () => { d.setAttribute('aria-hidden', 'true'); d.inert = true; $('#scrim').hidden = true; returnFocus?.focus?.(); };
  d.querySelector('[data-close]').focus();
  d.onclick = e => {
    if (e.target.closest('[data-close]')) return close();
    const t = e.target.closest('[data-src]');
    if (t && onSource) onSource(JSON.parse(t.dataset.src), t);  // opens the inspector on the exact response
  };
  d.onkeydown = e => { if (e.key === 'Escape') close(); };
  $('#scrim').onclick = close;
  return close;
}
